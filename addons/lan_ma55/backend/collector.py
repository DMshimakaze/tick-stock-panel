"""One bounded TickFlow SDK owner; actual HTTP hooks account for all SDK calls."""

import json
from collections import Counter, deque
from datetime import datetime, timedelta
from math import isfinite
from threading import Event, Lock
from time import monotonic

import httpx
from tickflow import TickFlow, TickFlowError

from .market_grid import BEIJING, COMPLETE_BAR_SECONDS, as_ms, latest_point, normalize_five, now_cn, wall
from .multiframe_input import DAILY_HISTORY, MAX_BATCH_SYMBOLS, MAX_DAILY_BATCH_POINTS, MAX_NATIVE_BARS, normalize_daily
from .native15_shadow import normalize_native
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


def factor_events(entries):
    if not isinstance(entries, list):
        return None, "INVALID_FACTOR_SCHEMA"
    days = {}
    for entry in entries:
        if not isinstance(entry, dict):
            return None, "INVALID_FACTOR_EVENT"
        timestamp, factor = entry.get("timestamp"), entry.get("ex_factor")
        if (type(timestamp) not in (int, float) or not isfinite(timestamp) or timestamp <= 0
                or type(factor) not in (int, float) or not isfinite(factor) or factor <= 0):
            return None, "INVALID_FACTOR_EVENT"
        try:
            day = datetime.fromtimestamp(timestamp / 1000, BEIJING).date().isoformat()
        except (ValueError, OverflowError, OSError):
            return None, "INVALID_FACTOR_EVENT"
        if day in days and days[day] != factor:
            return None, "CONFLICTING_FACTOR_EVENTS"
        days[day] = factor
    return [dict(day=day, ex_factor=days[day]) for day in sorted(days)], None


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
        cold_budget=None,
        scope=None,
    ):
        if not 1 <= limit <= 12:
            raise ValueError("Plugin minute budget must be 1..12 requests/minute")
        self.stop = stop or Event()
        self.report = report or (lambda **values: None)
        self.host_busy = host_busy or (lambda: False)
        self.cold_budget = cold_budget
        self.scope = scope
        self.request_cold = False
        self.calendar_request = False
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
        period = request.url.params.get("period")
        try:
            body = json.loads(request.content) if request.content else {}
        except ValueError:
            body = {}
        symbols = body.get("symbols") or [
            s for s in request.url.params.get("symbols", "").split(",") if s
        ]
        if self.host_busy():
            self.report(phase="HOST_BUSY", wait_seconds=10)
            raise RequestDeferred(monotonic() + 10)
        minute = (
            request.url.path.endswith("/klines/batch")
            and period in ("1m", "5m", "10m", "15m", "30m", "60m")
        )
        factor = request.url.path.endswith("/klines/ex-factors")
        daily = request.url.path.endswith("/klines/batch") and period == "1d" and not self.calendar_request
        collection = minute or factor or daily
        if self.scope is not None and collection:
            if not symbols or not set(symbols) <= self.scope() or (minute and period != "15m"):
                raise ValueError("OUTSIDE_VERIFIED_FOCUS_SCOPE")
        budgets = [self.budgets["minute" if collection else "metadata"]]
        if collection and self.request_cold and self.cold_budget is not None:
            budgets.append(self.cold_budget)
        delay = max(b.delay() for b in budgets)
        if delay > 0:
            self.metrics["budget_deferrals"] += 1
            self.report(phase="QUOTA_WAIT", wait_seconds=round(delay, 2))
            raise RequestDeferred(monotonic() + delay, reason="QUOTA_WAIT")
        began = monotonic()
        for budget in budgets:
            budget.acquire(self.stop, lambda delay: self.report(phase="QUOTA_WAIT", wait_seconds=round(delay, 2)))
        request.extensions["lan_measure"] = dict(
            started=monotonic(),
            endpoint=request.url.path,
            capability="kline.minute.batch"
            if minute
            else "kline.daily.batch"
            if daily
            else "adj_factor"
            if "ex-factors" in request.url.path
            else "metadata",
            symbol_count=len(symbols),
            period=request.url.params.get("period"),
            at=str(datetime.now(BEIJING).replace(tzinfo=None, microsecond=0)),
        )
        with self.lock:
            self.metrics["http_requests"] += 1
            self.metrics["minute_requests"] += int(minute)
            self.metrics["collection_requests"] += int(collection)
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

    def request(self, action, *, cold=False):
        # The single SDK owner passes request context to its actual HTTP hook.
        self.request_cold = cold
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
        finally:
            self.request_cold = False

    def metadata(self, symbols):
        return self.request(lambda: self.tf.instruments.batch(symbols))

    def stock_master(self):
        rows = []
        for exchange in ("SH", "SZ", "BJ"):
            rows.extend(
                self.request(
                    lambda ex=exchange: self.tf.exchanges.get_instruments(
                        ex, instrument_type="stock"
                    )
                )
            )
        return rows

    def master(self):
        rows = self.stock_master()
        for exchange in ("SH", "SZ"):
            rows.extend(self.request(lambda ex=exchange: self.tf.exchanges.get_instruments(ex, instrument_type="etf")))
        return rows

    def taxonomy_universe(self):
        """TickFlow's listed A-share universe plus the historical code master."""
        from .focus_pool import SYMBOL

        selected = self.request(lambda: self.tf.universes.get("CN_Equity_A"))
        stocks = selected["symbols"]
        if (selected["id"] != "CN_Equity_A" or len(stocks) != selected["symbol_count"] or
            len(set(stocks)) != len(stocks) or not all(SYMBOL.fullmatch(s) for s in stocks) or
            {s[-2:] for s in stocks} != {"SH", "SZ", "BJ"}):
            raise ValueError("THS_UNIVERSE_UNVERIFIED")
        master = {r["symbol"] for r in self.stock_master() if r.get("region") == "CN"
                  and r.get("type") == "stock" and SYMBOL.fullmatch(r["symbol"])}
        if not set(stocks) <= master:
            raise ValueError("THS_UNIVERSE_NOT_IN_MASTER")
        return dict(stocks=stocks, excluded=sorted(master-set(stocks)), universe_receipt=dict(
            source="TickFlow CN_Equity_A", at=str(now_cn()), exchange_counts=dict(Counter(s[-2:] for s in stocks)),
            master_count=len(master), listed_count=len(stocks)))

    def calendar(self):
        self.calendar_request = True
        try:
            source = self.request(lambda: self.tf.klines.batch(
                ["000001.SH"], period="1d", count=DAILY_HISTORY, adjust="none", max_workers=1,
            ))
        finally:
            self.calendar_request = False
        data = source.get("000001.SH")
        if not data or not data.get("timestamp"):
            raise ValueError("EXCHANGE_CALENDAR_MISSING")
        return sorted(
            {
                str(datetime.fromtimestamp(t / 1000, BEIJING).date())
                for t in data["timestamp"]
            }
        )

    def fetch_daily(self, symbols, start, end, *, count, cold=False):
        if (not 1 <= len(symbols) <= MAX_BATCH_SYMBOLS or not 1 <= count <= DAILY_HISTORY
                or len(symbols) * count > MAX_DAILY_BATCH_POINTS):
            raise ValueError("Daily batch exceeds the scheduled request window")
        began = monotonic()
        data = self.request(lambda: self.tf.klines.batch(
            symbols, period="1d", adjust="none", start_time=as_ms(start[:10]),
            end_time=as_ms(end), count=count, as_dataframe=False, batch_size=100, max_workers=1,
        ), cold=cold)
        decoded = monotonic()
        if set(data) - set(symbols):
            raise ValueError("UNREQUESTED_SYMBOL")
        bars, errors = {}, {}
        for symbol in symbols:
            if data.get(symbol) is None:
                errors[symbol] = "SOURCE_SYMBOL_NULL" if symbol in data else "SOURCE_SYMBOL_OMITTED"
                continue
            try:
                bars[symbol] = normalize_daily(data[symbol], start, end)
                if not bars[symbol]:
                    errors[symbol] = "DAILY_SOURCE_PENDING"
            except (KeyError, TypeError, ValueError, OverflowError):
                errors[symbol] = "INVALID_DAILY_SCHEMA"
        self.record_bars("1d", count, bars, errors, began, decoded)
        return bars, errors

    def record_bars(self, period, count, bars, errors, began, decoded):
        valid = sum(bool(rows) for rows in bars.values())
        total = sum(len(rows) for rows in bars.values())
        self.metrics[f"processed_symbols_{period}"] += valid
        self.metrics[f"valid_rows_{period}"] += total
        if self.observations and self.observations[-1]["period"] == period:
            self.observations[-1]["batch_metrics"] = dict(
                valid_symbols=valid, valid_rows=total, errors=len(errors), requested_count=count,
                sdk_ms=round((decoded - began) * 1000, 1), normalize_ms=round((monotonic() - decoded) * 1000, 1),
            )

    def session_evidence(self, now):
        """Confirm today with completed native15; no 5m session probe."""
        day = now[:10]
        end = latest_point([day], now, step=15)
        if wall(now).weekday() >= 5 or not end:
            return dict(confirmed_day=None, source_asof=None)
        self.report(active_operation="COLLECTING_NATIVE15")
        rows, errors = self.fetch(["000001.SH"], day + " 09:45:00", end)
        if errors and not rows.get("000001.SH"):
            raise SourceError()
        actual = {t: r for t, r in rows["000001.SH"].items() if r["volume"] > 0}
        return dict(confirmed_day=day if actual else None,
                    source_asof=max(actual, default=None))

    def factors(self, symbols):
        return self.factor_responses(symbols)[0]

    def factor_responses(self, symbols):
        if not symbols:
            return {}, {}
        try:
            data = self.request(
                lambda: self.tf.klines.ex_factors(
                    symbols, as_dataframe=False, batch_size=100, max_workers=1
                )
            )
        except (KeyError, TypeError, json.JSONDecodeError):
            data = None
        result, errors = {}, {}
        for symbol in symbols:
            if not isinstance(data, dict):
                events, error = None, "INVALID_FACTOR_ENVELOPE"
            elif symbol not in data:
                events, error = None, "FACTOR_KEY_MISSING"
            elif data[symbol] is None:
                events, error = None, "FACTOR_NULL_RESPONSE"
            else:
                events, error = factor_events(data[symbol])
                self.metrics["invalid_factor_symbols"] += int(error is not None)
            result[symbol] = events
            if error:
                errors[symbol] = error
        if self.observations and self.observations[-1]["capability"] == "adj_factor":
            self.observations[-1]["batch_metrics"] = dict(
                verified_events=sum(bool(events) for events in result.values()),
                verified_empty=sum(events == [] for events in result.values()),
                failures=dict(Counter(errors.values())),
            )
        return result, errors

    def fetch_five(self, symbols, start, end, *, cold=False):
        """Explicit offline archive comparison only; never used by the worker."""
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

    def fetch(self, symbols, start, end, *, cold=False, count=MAX_NATIVE_BARS, previous=None):
        """One production native15 HTTP, isolated validation per security."""
        if not 1 <= len(symbols) <= MAX_BATCH_SYMBOLS or not 1 <= count <= MAX_NATIVE_BARS:
            raise ValueError("Native15 batch limited to 100 symbols / 1280 bars")
        cutoff = min(end, str(now_cn() - timedelta(seconds=COMPLETE_BAR_SECONDS)))
        began = monotonic()
        # The compact feed can return invalid OHLC for an isolated slot.
        # Request the preceding completed slot as context and admit only the planned window.
        request_count = 2 if previous and start == end and count == 1 else count
        request_start = previous if request_count != count else start
        data = self.request(lambda: self.tf.klines.batch(
            symbols, period="15m", adjust="none", start_time=as_ms(request_start),
            end_time=as_ms(end), count=request_count, as_dataframe=False,
            batch_size=100, max_workers=1, show_progress=False,
        ), cold=cold)
        decoded = monotonic()
        if set(data) - set(symbols):
            raise ValueError("UNREQUESTED_SYMBOL")
        valid, errors = {}, {}
        for symbol in symbols:
            if data.get(symbol) is None:
                errors[symbol] = dict(code="SOURCE_SYMBOL_NULL" if symbol in data else "SOURCE_SYMBOL_OMITTED",
                                      timestamps=None)
                continue
            try:
                rows, diagnostic = normalize_native(data[symbol], start, cutoff)
                valid[symbol] = {b["timestamp"]: b for b in rows}
                self.metrics["unfinished_native15_rows"] += diagnostic["unfinished"]
                if diagnostic["invalid"]:
                    errors[symbol] = dict(code="INVALID_NATIVE15_OHLCV", timestamps=diagnostic["invalid"])
                    self.metrics["invalid_native15_rows"] += len(diagnostic["invalid"])
            except (KeyError, TypeError, ValueError, OverflowError) as exc:
                errors[symbol] = dict(code=str(exc) if isinstance(exc, ValueError) else "INVALID_NATIVE15_SCHEMA", timestamps=None)
        self.record_bars("15m", request_count, valid, errors, began, decoded)
        return valid, errors

    def close(self):
        self.tf.close()

    def native15_shadow(self, symbols, start, end, *, count=1280):
        """Explicit bounded experiment; never called by the production scheduler."""
        if not 1 <= len(symbols) <= 8 or not 1 <= count <= 1280:
            raise ValueError("Native shadow limited to 8 symbols and 1280 bars")
        data = self.request(lambda: self.tf.klines.batch(
            symbols, period="15m", adjust="none", start_time=as_ms(start),
            end_time=as_ms(end), count=count, as_dataframe=False,
            batch_size=100, max_workers=1, show_progress=False,
        ))
        if set(data) - set(symbols):
            raise ValueError("UNREQUESTED_SYMBOL")
        return {s: normalize_native(data[s], start, end) for s in symbols if s in data}
