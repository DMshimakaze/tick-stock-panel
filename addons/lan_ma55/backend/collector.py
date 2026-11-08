"""QuantDash 0.1.0 compact batches; adapted from the pinned MA55 collector.

No TSP repository imports or vendor fallback. Actual HTTP hooks own the budget.
"""

from collections import Counter
from datetime import datetime
from math import isfinite
from threading import Event, Lock
from time import monotonic, time

import httpx
from quantdash import QuantDash
from quantdash._exceptions import ConnectionError as SDKConnectionError
from quantdash._exceptions import TimeoutError as SDKTimeoutError

from .market import as_ms, normalize_five
from .pipeline import BEIJING
from .request_budget import CollectionStopped, RequestDeferred, RollingBudget, rate_limit_delay, retry_seconds


class SourceError(Exception):
    def __init__(self, status=None):
        self.status_code = status
        super().__init__(f"QuantDash HTTP {status}" if status else "QuantDash request failed")


class Collector:
    def __init__(self, key, cache_dir, *, stop=None, report=None, qd=None, limit=24):
        if not 1 <= limit <= 24:
            raise ValueError("MA55 budget must be 1..24; six batch requests/min remain for TSP")
        self.stop = stop or Event()
        self.report = report or (lambda **values: None)
        self.qd = qd or QuantDash(api_key=key, max_retries=0, timeout=30, cache_dir=str(cache_dir))
        self.budgets = {kind: RollingBudget(limit if kind == "minute" else 24)
                        for kind in ("minute", "daily", "metadata", "factors", "quotes")}
        self.metrics = Counter(http_requests=0, http_429_count=0, minute_requests=0)
        self.lock = Lock()
        self.observed = None
        self.retry_header = None
        self.retry_details = None
        self.responses = 0
        # Standard httpx hook seam verified in quantdash==0.1.0 and the source
        # collector. SDK methods are never replaced; hidden internal calls count.
        hooks = self.qd._client._client.event_hooks
        hooks["request"].append(self.before_request)
        hooks["response"].append(self.observe)

    def before_request(self, request):
        path = request.url.path
        kind = ("minute" if path.endswith("/klines/batch") and request.url.params.get("period") == "5m"
                else "factors" if path.endswith("/ex-factors") else "metadata" if path.endswith("/instruments")
                else "quotes" if path.endswith("/quotes") else "daily")
        began = monotonic()
        self.budgets[kind].acquire(self.stop, lambda delay: self.report(
            phase="QUOTA_WAIT", wait_seconds=round(delay, 2), endpoint=kind))
        with self.lock:
            self.metrics["http_requests"] += 1
            self.metrics[kind + "_requests"] += 1
            self.metrics["budget_wait_seconds"] += monotonic() - began
        self.report(last_request_at=str(datetime.now(BEIJING).replace(tzinfo=None, microsecond=0)))

    def observe(self, response):
        with self.lock:
            self.responses += 1
        if response.status_code >= 400:
            self.observed = response.status_code
            self.retry_header = response.headers.get("Retry-After")
            if response.status_code == 429:
                with self.lock:
                    self.metrics["http_429_count"] += 1
                response.read()
                try:
                    self.retry_details = response.json()
                except ValueError:
                    self.retry_details = None

    def request(self, action):
        self.observed = self.retry_header = self.retry_details = None
        if self.stop.is_set():
            raise CollectionStopped()
        try:
            before_requests, before_responses = self.metrics["http_requests"], self.responses
            result = action()
            if self.stop.is_set():
                raise CollectionStopped()
            if self.observed:
                raise SourceError(self.observed)
            # SDK batch helpers can swallow a transport error and return {}.
            # An actual request without its response hook is not an empty market.
            if self.metrics["http_requests"] - before_requests != self.responses - before_responses:
                raise httpx.TransportError("QuantDash batch completed without an HTTP response")
            return result
        except CollectionStopped:
            raise
        except Exception as exc:
            status = self.observed or getattr(exc, "status_code", None)
            if status == 429:
                seconds = max(rate_limit_delay(exc, self.retry_header, 1),
                              retry_seconds(self.retry_details) + 1,
                              retry_seconds(self.retry_details.get("error")) + 1
                              if isinstance(self.retry_details, dict) else 1)
                for budget in self.budgets.values():
                    budget.defer(seconds)
                self.report(phase="RATE_LIMITED", wait_seconds=seconds, http_status=429)
                raise RequestDeferred(monotonic() + seconds, status=429) from None
            if status and status >= 500 or isinstance(exc, (SDKConnectionError, SDKTimeoutError, httpx.TransportError)):
                self.report(phase="NETWORK_WAIT", wait_seconds=5, http_status=status)
                raise RequestDeferred(monotonic() + 5, status=status) from None
            raise SourceError(status) from None

    def pool(self):
        return self.request(lambda: self.qd.quotes.get(universes="CN_Stock"))

    def metadata(self, symbols):
        return self.request(lambda: self.qd.instruments.batch(symbols, batch_size=100, max_workers=1))

    def calendar(self):
        data = self.request(lambda: self.qd.klines.get("000001.SH", period="1d", count=250, adjust="none"))
        return sorted({str(datetime.fromtimestamp(t / 1000, BEIJING).date()) for t in data["timestamp"]})

    def factors(self, symbols):
        # Complete response establishes symbol coverage; a filtered empty map
        # cannot distinguish omitted coverage from no events in the window.
        data = self.request(lambda: self.qd.klines.ex_factors(symbols, to_dataframe=False,
                                                          batch_size=100, max_workers=1))
        result = {}
        for symbol in symbols:
            entries = data.get(symbol)
            if entries is None:
                result[symbol] = None
                continue
            events = []
            for entry in entries:
                value = entry["ex_factor"]
                if not isinstance(value, (int, float)) or not isfinite(value) or value <= 0:
                    # A bad vendor factor invalidates this symbol's coverage,
                    # not the other members of a real 100-stock response.
                    # None reaches the engine as DATA_PENDING, never identity.
                    events = None
                    self.metrics["invalid_factor_symbols"] += 1
                    break
                events.append(dict(day=str(datetime.fromtimestamp(entry["timestamp"] / 1000, BEIJING).date()), ex_factor=value))
            result[symbol] = events
        return result

    def fetch(self, symbols, start, end):
        if not 1 <= len(symbols) <= 100:
            raise ValueError("QuantDash server batches support 1..100 symbols")
        data = self.request(lambda: self.qd.klines.batch(
            symbols, period="5m", start_time=as_ms(start), end_time=as_ms(end), count=5000,
            adjust="none", to_dataframe=False, batch_size=100, max_workers=1, show_progress=False))
        if set(data) - set(symbols):
            raise ValueError("UNREQUESTED_SYMBOL")
        return {s: normalize_five(rows, start, end) for s, rows in data.items()}

    def close(self):
        self.qd.close()

    def checkpoint(self):
        epoch, clock = time(), monotonic()
        budgets = {}
        for key, budget in self.budgets.items():
            with budget.lock:
                budgets[key] = dict(sent=[epoch - (clock - t) for t in budget.sent if clock - t < 60],
                                    cooldown=epoch + max(0, budget.cooldown - clock))
        return dict(metrics=dict(self.metrics), budgets=budgets)

    def restore(self, checkpoint):
        epoch, clock = time(), monotonic()
        self.metrics.update(checkpoint["metrics"])
        for key, saved in checkpoint["budgets"].items():
            budget = self.budgets[key]
            with budget.lock:
                budget.sent.extend(clock - (epoch - t) for t in saved["sent"] if epoch - t < 60)
                budget.cooldown = clock + max(0, saved["cooldown"] - epoch)
