"""One bounded TickFlow SDK owner; actual HTTP hooks account for all SDK calls."""

from collections import Counter, deque
from datetime import datetime
import json
from math import isfinite
from threading import Event, Lock
from time import monotonic

import httpx
from tickflow import TickFlow, TickFlowError

from .market_grid import BEIJING, as_ms, normalize_five
from .request_budget import (
    CollectionStopped,
    RequestDeferred,
    RollingBudget,
    retry_seconds,
)


class SourceError(Exception):
    def __init__(self, status=None):
        self.status_code = status
        super().__init__(
            f"TickFlow HTTP {status}" if status else "TickFlow request failed"
        )


class Collector:
    def __init__(
        self,
        key,
        cache_dir,
        *,
        stop=None,
        report=None,
        client=None,
        limit=8,
        host_busy=None,
    ):
        if not 1 <= limit <= 12:
            raise ValueError("Plugin minute budget must be 1..12 requests/minute")
        self.stop = stop or Event()
        self.report = report or (lambda **values: None)
        self.host_busy = host_busy or (lambda: False)
        self.tf = client or TickFlow(
            api_key=key, max_retries=0, timeout=20, cache_dir=str(cache_dir)
        )
        self.budgets = {"minute": RollingBudget(limit), "metadata": RollingBudget(30)}
        self.metrics = Counter(http_requests=0, minute_requests=0, http_429_count=0)
        self.observations = deque(maxlen=2048)
        self.lock = Lock()
        self.responses = 0
        self.observed = None
        self.retry_header = None
        self.retry_body = None
        # Installed SDK 0.1.25 uses httpx: observe requests, never replace SDK methods.
        hooks = self.tf._client._client.event_hooks
        hooks["request"].append(self.before_request)
        hooks["response"].append(self.observe)

    def before_request(self, request):
        if self.host_busy():
            self.report(phase="HOST_BUSY", wait_seconds=10)
            raise RequestDeferred(monotonic() + 10)
        minute = (
            request.url.path.endswith("/klines/batch")
            and request.url.params.get("period") == "5m"
        )
        budget = self.budgets["minute" if minute else "metadata"]
        began = monotonic()
        budget.acquire(
            self.stop,
            lambda delay: self.report(phase="QUOTA_WAIT", wait_seconds=round(delay, 2)),
        )
        try:
            body = json.loads(request.content) if request.content else {}
        except ValueError:
            body = {}
        symbols = body.get("symbols") or [
            s for s in request.url.params.get("symbols", "").split(",") if s
        ]
        request.extensions["lan_measure"] = dict(
            started=monotonic(),
            endpoint=request.url.path,
            capability="kline.minute.batch"
            if minute
            else "adj_factor"
            if "ex-factors" in request.url.path
            else "metadata",
            symbol_count=len(symbols),
            at=str(datetime.now(BEIJING).replace(tzinfo=None, microsecond=0)),
        )
        with self.lock:
            self.metrics["http_requests"] += 1
            self.metrics["minute_requests"] += int(minute)
            self.metrics[
                "period_" + (request.url.params.get("period") or "metadata")
            ] += 1
            self.metrics["budget_wait_seconds"] += monotonic() - began
        self.report(
            last_request_at=str(
                datetime.now(BEIJING).replace(tzinfo=None, microsecond=0)
            )
        )

    def observe(self, response):
        measurement = response.request.extensions.get("lan_measure")
        if measurement:
            self.observations.append(
                dict(
                    **{k: v for k, v in measurement.items() if k != "started"},
                    latency_ms=(monotonic() - measurement["started"]) * 1000,
                    status=response.status_code,
                    retries=0,
                    quota_headers={
                        k: v
                        for k, v in response.headers.items()
                        if k.lower().startswith(("x-ratelimit-", "ratelimit-"))
                        or k.lower() == "retry-after"
                    },
                )
            )
        self.responses += 1
        if response.status_code >= 400:
            self.observed = response.status_code
            self.retry_header = response.headers.get("Retry-After")
            if response.status_code == 429:
                self.metrics["http_429_count"] += 1
                response.read()
                try:
                    self.retry_body = response.json()
                except ValueError:
                    self.retry_body = None

    def request(self, action):
        self.observed = self.retry_header = self.retry_body = None
        if self.stop.is_set():
            raise CollectionStopped()
        before, responses = self.metrics["http_requests"], self.responses
        try:
            result = action()
            if self.observed:
                raise SourceError(self.observed)
            if self.metrics["http_requests"] - before != self.responses - responses:
                raise SourceError()
            return result
        except (CollectionStopped, RequestDeferred):
            raise
        except (TickFlowError, httpx.HTTPError, SourceError) as exc:
            status = self.observed or getattr(exc, "status_code", None)
            if status == 429:
                delay = max(60, retry_seconds(self.retry_body, self.retry_header) + 1)
                for budget in self.budgets.values():
                    budget.defer(delay)
                self.report(phase="RATE_LIMITED", wait_seconds=delay, http_status=429)
                raise RequestDeferred(monotonic() + delay, status=429) from None
            raise SourceError(status) from None

    def metadata(self, symbols):
        return self.request(lambda: self.tf.instruments.batch(symbols))

    def master(self):
        rows = []
        for exchange in ("SH", "SZ", "BJ"):
            rows.extend(
                self.request(
                    lambda ex=exchange: self.tf.exchanges.get_instruments(
                        ex, instrument_type="stock"
                    )
                )
            )
        for exchange in ("SH", "SZ"):
            rows.extend(self.request(lambda ex=exchange: self.tf.exchanges.get_instruments(ex, instrument_type="etf")))
        return rows

    def calendar(self):
        data = self.request(
            lambda: self.tf.klines.get(
                "000001.SH", period="1d", count=300, adjust="none"
            )
        )
        return sorted(
            {
                str(datetime.fromtimestamp(t / 1000, BEIJING).date())
                for t in data["timestamp"]
            }
        )

    def factors(self, symbols):
        if not symbols:
            return {}
        data = self.request(
            lambda: self.tf.klines.ex_factors(
                symbols, as_dataframe=False, batch_size=100, max_workers=1
            )
        )
        result = {}
        for symbol in symbols:
            entries = data.get(symbol)
            if entries is None:
                result[symbol] = None
                continue
            if any(
                not isinstance(e["ex_factor"], (int, float))
                or not isfinite(e["ex_factor"])
                or e["ex_factor"] <= 0
                for e in entries
            ):
                result[symbol] = None
                self.metrics["invalid_factor_symbols"] += 1
                continue
            result[symbol] = [
                dict(
                    day=str(
                        datetime.fromtimestamp(e["timestamp"] / 1000, BEIJING).date()
                    ),
                    ex_factor=e["ex_factor"],
                )
                for e in entries
            ]
        return result

    def fetch(self, symbols, start, end, *, cold=False):
        maximum = 8 if cold else 100
        if not 1 <= len(symbols) <= maximum:
            raise ValueError(
                f"{'Cold' if cold else 'Increment'} batch limited to {maximum} symbols"
            )
        data = self.request(
            lambda: self.tf.klines.batch(
                symbols,
                period="5m",
                start_time=as_ms(start),
                end_time=as_ms(end),
                count=1200 if cold else 1000,
                adjust="none",
                as_dataframe=False,
                batch_size=100,
                max_workers=1,
                show_progress=False,
            )
        )
        if set(data) - set(symbols):
            raise ValueError("UNREQUESTED_SYMBOL")
        valid, errors = {}, {}
        for symbol in symbols:
            if symbol not in data:
                errors[symbol] = dict(code="SOURCE_UNAVAILABLE", timestamps=None)
                continue
            try:
                valid[symbol], invalid = normalize_five(data[symbol], start, end)
                if invalid:
                    errors[symbol] = dict(code="INVALID_5M_OHLCV", timestamps=invalid)
                    self.metrics["invalid_5m_rows"] += len(invalid)
            except ValueError as exc:
                errors[symbol] = dict(code=str(exc), timestamps=None)
        return valid, errors

    def close(self):
        self.tf.close()
