"""Fake-clock official request-path replay, with zero supplier network traffic.

Host fetch orchestration and SDK encoding/decoding run unchanged. Quote's final
enriched sink is replaced by a counting sink; minute/depth files use private paths.
"""

from collections import Counter, defaultdict, deque
from contextlib import ExitStack
from datetime import datetime, timedelta
import json
from pathlib import Path
from threading import Lock
from time import perf_counter
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import polars as pl
from tickflow import TickFlow

from addons.lan_ma55.backend.market_grid import BEIJING


def windows(trace):
    by = defaultdict(list)
    for r in trace:
        by[r["capability"]].append(r)
        by["SHARED_ACCOUNT_CONSERVATIVE"].append(r)
    result = {}
    for cap, rows in by.items():
        queue = deque()
        peak = 0
        for row in sorted(rows, key=lambda r: r["at"]):
            while queue and row["at"] - queue[0] >= 60:
                queue.popleft()
            queue.append(row["at"])
            peak = max(peak, len(queue))
        histogram = Counter(int(r["at"] // 60) for r in rows)
        result[cap] = dict(
            requests=len(rows),
            peak_60s=peak,
            mean_batch=sum(r["symbols"] for r in rows) / len(rows),
            peak_batch=max(r["symbols"] for r in rows),
            per_minute=dict(sorted(histogram.items())),
            http_429=sum(r["status"] == 429 for r in rows),
            retries=sum(r["retries"] for r in rows),
        )
    return result


def run(folder, symbols, plugin_trace, on_tick):
    from app.services import (
        preferences,
        kline_sync,
        minute_refresh,
        quote_service,
        depth_service,
    )
    from app.services.minute_refresh import MinuteRefreshService
    from app.services.quote_service import QuoteService
    from app.services.depth_service import DepthService
    from app.tickflow.capabilities import Cap, CapabilityLimits, CapabilitySet
    from app.data_providers.tickflow_provider import TickFlowProvider
    from app.config import settings

    folder = Path(folder)
    host = folder / "isolated-host"
    host.mkdir()
    prefs = host / "preferences.json"
    prefs.write_text(
        json.dumps(
            dict(
                realtime_quotes_enabled=True,
                realtime_quote_interval=1,
                minute_refresh_enabled=True,
                minute_refresh_interval=3,
                minute_intraday_refresh_interval=3,
                realtime_data_provider="tickflow",
                full_minute_data_provider="tickflow",
                depth5_data_provider="tickflow",
                realtime_pull_stock=True,
                realtime_pull_etf=False,
                limit_ladder_monitor_enabled=True,
                depth_polling_interval=10,
                depth_finalize_time={"hour": 15, "minute": 2},
                sse_refresh_pages={
                    p: True
                    for p in ("dashboard", "watchlist", "limit-ladder", "industry")
                },
            )
        ),
        encoding="utf-8",
    )
    runtime_cache = json.loads(
        (Path(settings.data_dir) / "capabilities.json").read_text("utf-8")
    )
    caps = CapabilitySet(
        {
            Cap(k): CapabilityLimits(**v)
            for k, v in json.loads(
                (Path(settings.data_dir) / "capabilities.json").read_text("utf-8")
            )["capabilities"].items()
        }
    )
    trace = []
    clock = [0.0]
    guard = Lock()
    base = datetime(2026, 1, 26, 13, 46, 15, tzinfo=BEIJING)

    def now():
        return base + timedelta(seconds=clock[0])

    def reply(request):
        began = perf_counter()
        body = json.loads(request.content) if request.content else {}
        codes = body.get("symbols") or request.url.params.get("symbols", "").split(",")
        universe = bool(body.get("universes") or request.url.params.get("universes"))
        if universe:
            codes = symbols
        path = request.url.path
        cap = (
            ("quote.pool" if universe else "quote.batch")
            if path.endswith("/quotes")
            else (
                "intraday.universe"
                if path.endswith("/intraday/universe")
                else "intraday.batch"
                if path.endswith("/intraday/batch")
                else "depth5.batch"
            )
        )
        at = int(now().timestamp() * 1000)
        if cap.startswith("quote"):
            data = [
                dict(
                    symbol=s,
                    last_price=100.0,
                    prev_close=99.0,
                    open=99.0,
                    high=101.0,
                    low=98.0,
                    volume=100.0,
                    amount=1e6,
                    timestamp=at,
                    ext={},
                )
                for s in codes
            ]
        elif cap == "depth5.batch":
            data = {
                s: dict(ask_volumes=[0] * 5, bid_volumes=[100] * 5, timestamp=at)
                for s in codes
            }
        else:
            end = now().replace(second=0, microsecond=0)
            stamps = [
                int((end - timedelta(minutes=i)).timestamp() * 1000) for i in (2, 1, 0)
            ]
            data = {
                s: dict(
                    timestamp=stamps,
                    open=[100.0] * 3,
                    high=[101.0] * 3,
                    low=[99.0] * 3,
                    close=[100.0] * 3,
                    volume=[10.0] * 3,
                    amount=[100000.0] * 3,
                )
                for s in codes
            }
        response = httpx.Response(200, json={"data": data})
        with guard:
            trace.append(
                dict(
                    at=clock[0],
                    owner="official",
                    endpoint=path,
                    capability=cap,
                    symbols=len(codes),
                    status=200,
                    retries=0,
                    latency_ms=(perf_counter() - began) * 1000,
                )
            )
        return response

    tf = TickFlow(
        api_key="synthetic", base_url="https://synthetic.invalid", max_retries=0
    )
    tf._client._client.close()
    tf._client._client = httpx.Client(
        base_url="https://synthetic.invalid", transport=httpx.MockTransport(reply)
    )

    class Repo:
        def __init__(self):
            self.store = SimpleNamespace(data_dir=host)
            self._write_lock = Lock()

        def get_instruments(self):
            return pl.DataFrame({"symbol": symbols})

        def get_index_symbol_set(self):
            return {"000001.SH", "399001.SZ", "399006.SZ", "000680.SH"}

        def get_etf_instruments(self):
            return pl.DataFrame()

        def get_enriched_latest(self):
            return pl.DataFrame(
                {"symbol": symbols[:180], "signal_limit_up": [True] * 180}
            ), now().date()

    repo = Repo()
    state = SimpleNamespace(capabilities=caps)
    ui = Counter()
    round_ms = defaultdict(list)
    writes = []
    sink_rows = []
    try:
        with ExitStack() as stack:

            def replace(obj, name, value):
                stack.enter_context(patch.object(obj, name, value))

            replace(preferences, "_path", lambda: prefs)
            preferences._invalidate_cache()
            from app.tickflow import policy

            replace(policy, "base_tier_name", lambda: runtime_cache["label"].lower())
            replace(policy, "tier_label", lambda: runtime_cache["label"])
            replace(settings, "data_dir", host)
            replace(kline_sync, "get_client", lambda: tf)
            replace(
                __import__(
                    "app.tickflow.client", fromlist=["get_paid_realtime_client"]
                ),
                "get_paid_realtime_client",
                lambda: tf,
            )
            replace(
                __import__(
                    "app.data_providers.tickflow_provider", fromlist=["get_client"]
                ),
                "get_client",
                lambda: tf,
            )
            provider = TickFlowProvider()
            replace(
                __import__("app.data_providers.registry", fromlist=["get_provider"]),
                "get_provider",
                lambda name: provider,
            )
            for module in (minute_refresh, quote_service, depth_service):
                replace(module, "cn_now", now)
                replace(module, "cn_today", lambda: now().date())
            quote = QuoteService()
            quote._repo = repo
            quote._app_state = state
            state.quote_service = quote
            replace(
                quote,
                "_process_full_market_records",
                lambda records, **kw: sink_rows.append(len(records)),
            )
            minute = MinuteRefreshService(repo)
            minute.set_app_state(state)
            depth = DepthService()
            depth.set_repo(repo)
            depth.set_app_state(state)
            # Run the production mode selector, batching paths and SDK transport.
            for second in range(1800):
                clock[0] = float(second)
                on_tick(second)
                if second == 420:
                    minute._empty_rounds = 2
                began = perf_counter()
                quote._fetch_full_market_quotes()
                round_ms["quote"].append((perf_counter() - began) * 1000)
                ui["quote_1s"] += 1
                if second % 3 == 0:
                    began = perf_counter()
                    minute._run_round()
                    round_ms["minute"].append((perf_counter() - began) * 1000)
                    writes.append(
                        dict(
                            at=second,
                            mode=minute._state.last_mode,
                            requests=minute._state.last_requests,
                            rows=minute._state.last_rows,
                            error=minute._state.last_error,
                        )
                    )
                    ui["minute_3s"] += 1
                    ui["intraday_view_3s"] += 1
                if second % 10 == 0:
                    before = len(trace)
                    depth._poll_once()
                    ui["ladder_10s"] += 1
                    assert len(trace) > before
                if second % 300 == 0:
                    print(
                        json.dumps(
                            dict(stage="official_request_replay", second=second)
                        ),
                        flush=True,
                    )
            # Real official finalization path at the user's configured 15:02 boundary.
            clock[0] = (
                datetime(2026, 1, 26, 15, 2, tzinfo=BEIJING) - base
            ).total_seconds()
            depth.finalize()
            ui["finalize_15_02"] += 1
            assert (host / "depth5/date=2026-01-26/part.parquet").is_file()
            effective = dict(
                quote=preferences.get_realtime_quote_interval(),
                minute=minute._effective_interval(),
                intraday_view=preferences.get_minute_intraday_refresh_interval(),
                depth=depth._compute_interval(180)[0],
                finalize=preferences.get_depth_finalize_time(),
            )
    finally:
        tf.close()
        preferences._invalidate_cache()
    all_trace = trace + plugin_trace
    report = dict(
        synthetic=True,
        commercial_http=0,
        actual_path_replayed_seconds=1800,
        repeated_steady_model_seconds=0,
        effective_intervals=effective,
        ui_rounds=dict(ui),
        quote_sink="COUNT_ONLY_ENRICHED_CPU_NOT_MEASURED",
        supplier_limits_source="RUNTIME_CAPABILITY_CACHE_NOT_ACCOUNT_GUARANTEE",
        runtime_capabilities=caps.to_dict(),
        requests=windows(all_trace),
        official_round_ms={
            k: __import__(
                "addons.lan_ma55.tools.stress_db", fromlist=["quantiles"]
            ).quantiles(v)
            for k, v in round_ms.items()
        },
        minute_rounds=writes,
        quote_symbols_per_round=sorted(set(sink_rows)),
        shared_account_pool="NOT_VERIFIED",
        sse_starts_supplier_producer=False,
    )
    (folder / "request-metrics.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    (folder / "combined-request-trace.json").write_text(
        json.dumps(all_trace), encoding="utf-8"
    )
    return report
