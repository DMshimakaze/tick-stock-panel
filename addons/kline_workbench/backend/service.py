"""Read-only shared-data bridge. No market DB, SDK, source quota or collector."""

import atexit
import json
from pathlib import Path
from threading import Event, Lock, Thread
import httpx

from .publisher import Publisher, VIEW_FIELDS
from .market_client import Client
from .bridge_lease import ProcessLease


class Worker:
    def __init__(self, context, settings):
        self.folder = Path(context.data_dir) / "user_data" / "lan_kline"
        self.folder.mkdir(parents=True, exist_ok=True)
        self.settings = settings
        self.stop, self.lock = Event(), Lock()
        self.state = dict(
            version="3.3.0",
            phase="STARTING",
            source="lan.ma55 HTTP v1",
            minute_request_limit=0,
            requests={"minute_requests": 0, "period_1m": 0},
            error=None,
        )
        self.publisher = Publisher(
            settings["api_base"],
            settings.get("api_token"),
            session=settings.get("api_session"),
        )
        self.source = Client(
            settings["api_base"],
            session=settings.get("api_session"),
            token=settings.get("market_api_token"),
        )
        self.thread = Thread(target=self.run, name="lan-kline-view-bridge", daemon=True)
        self.published = {}

    def start(self):
        self.lease = ProcessLease(self.folder / "worker.lock")
        self.thread.start()
        atexit.register(self.close)

    def status(self):
        with self.lock:
            return dict(self.state, worker_alive=self.thread.is_alive())

    def report(self, **values):
        with self.lock:
            self.state.update(values)

    def view(self, demand, data):
        symbol = demand["requested_symbol"]
        rows = [r for r in data["rows"] if r["symbol"] == symbol]
        primary = next(r for r in rows if r["timeframe"] == demand["period"])
        status = dict(
            phase=primary["state"],
            source="lan.ma55",
            row_count=primary["row_count"],
            warm_count=primary.get("warm_count", 0),
            warm_target=250,
            capacity=primary.get("capacity", 250),
            source_id=primary.get("source_id"),
            transition=primary.get("transition"),
            ma_history=primary.get("ma_history", {}),
            valid_ma_lengths={
                r["timeframe"]: {
                    str(n): sum(b.get(f"ma{n}") is not None for b in r["bars"])
                    for n in (55, 233)
                }
                for r in rows
            },
            gap_count=primary.get("gap_count", 0),
            error=primary.get("error_code") or "",
            requests={"minute_requests": 0, "period_1m": 0},
        )
        public = data.get("service_status", {})
        status.update(
            source_phase=public.get("phase"),
            source_error=public.get("error"),
            wait_seconds=public.get("wait_seconds", 0),
            source_requests=public.get("requests", {}),
        )
        payload = dict(
            symbol=symbol,
            name=primary.get("name", symbol),
            asset_type=primary.get("asset_type"),
            raw_tick=primary.get("raw_tick"),
            ma_history={r["timeframe"]:r.get("ma_history", {}) for r in rows},
            series={r["timeframe"]: r["bars"] for r in rows},
            events=data.get("events", {}).get(symbol, dict(rows=[],total=0,truncated=False)),
            states={r["timeframe"]: r["state"] for r in rows},
            calendar=data["sessions"],
            basis=primary.get("price_basis", "RAW"),
            basis_id=primary.get("basis_id"),
            asof=primary.get("latest_completed_at"),
            source_asof=primary.get("source_asof"),
            source="lan.ma55",
            source_id=primary.get("source_id"),
            transition=primary.get("transition"),
            correction=primary.get("correction", False),
            factor_revision=primary.get("factor_revision"),
            basis_revision=primary.get("basis_revision"),
            basis_note=(
                "QuantDash迁移历史，只读展示，等待TickFlow重建"
                if primary.get("source_id") == "QUANTDASH_LEGACY"
                else "TickFlow原生15m · 人工复核候选 / 事件链前复权"
                if primary.get("source_id") == "TICKFLOW_NATIVE_15M"
                else "历史冻结BUY · 真实5m聚合 / 事件链前复权"
            )
            if primary.get("price_basis") == "QFQ"
            else "因子待补，显示原始价格",
        )
        revision = json.dumps(
            [
                demand["period"],
                payload["events"],
                [
                    (r["timeframe"], r.get("revision"), r["state"], r.get("basis_id"))
                    for r in rows
                ],
            ],
            separators=(",", ":"),
        )
        return dict(
            scope=demand["scope"],
            client_id=demand["client_id"],
            requested_symbol=symbol,
            request_id=demand["request_id"],
            revision=revision,
            asof=primary.get("latest_completed_at") or "",
            phase=primary["state"],
            error="",
            status_json=json.dumps(status, ensure_ascii=False),
            payload_json=json.dumps(
                payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")
            ),
        )

    def publish(self, demands):
        if not demands:
            stale = [
                {k: s if k == "scope" else "" for k, _ in VIEW_FIELDS}
                for s in self.published
            ]
            self.publisher.publish(stale)
            self.published = {}
            return
        # 16 session slots x 3 periods x 250 bars is split below the v2 limit.
        datasets = {}
        for source in ("native", "archive"):
            symbols = sorted({d["requested_symbol"] for d in demands if d.get("source", "native") == source})
            for offset in range(0, len(symbols), 10):
                data = self.source.bars(symbols[offset:offset+10], timeframes="15m,30m,60m", source=source)
                data["events"] = {}
                for code in symbols[offset:offset+10]:
                    frame = next(r for r in data["rows"] if r["symbol"] == code and r["timeframe"] == "15m")
                    bars = frame["bars"]
                    data["events"][code] = self.source.events(code, bars[0]["timestamp"], bars[-1]["timestamp"], native=frame.get("source_id") == "TICKFLOW_NATIVE_15M") if bars else dict(rows=[],total=0,truncated=False)
                datasets.update({(s, source): data for s in symbols[offset:offset+10]})
        rows, receipts = [], {}
        for demand in demands:
            row = self.view(demand, datasets[(demand["requested_symbol"], demand.get("source", "native"))])
            receipt = (row["client_id"], row["request_id"], row["revision"])
            receipts[row["scope"]] = receipt
            if self.published.get(row["scope"]) != receipt:
                rows.append(row)
        for scope in self.published.keys() - receipts.keys():
            rows.append({k: scope if k == "scope" else "" for k, _ in VIEW_FIELDS})
        self.publisher.publish(rows)
        self.published = receipts

    def publish_error(self, demands, message):
        rows = []
        for d in demands:
            rows.append(
                dict(
                    scope=d["scope"],
                    client_id=d["client_id"],
                    requested_symbol=d["requested_symbol"],
                    request_id=d["request_id"],
                    revision="ERROR:" + message,
                    asof="",
                    phase="DATA_SOURCE_UNAVAILABLE",
                    error=message,
                    status_json=json.dumps(
                        dict(phase="DATA_SOURCE_UNAVAILABLE", error=message)
                    ),
                    payload_json=json.dumps(
                        dict(
                            symbol=d["requested_symbol"],
                            source="lan.ma55",
                            unavailable=True,
                        )
                    ),
                )
            )
        self.publisher.publish(rows)
        for demand in demands:
            self.published.pop(demand["scope"], None)

    def run(self):
        failures = 0
        try:
            while not self.stop.is_set():
                demands = []
                try:
                    demands = self.publisher.demands()
                    self.publish(demands)
                    self.report(phase="READY", error=None, sessions=len(demands))
                    failures = 0
                except (httpx.HTTPError, ValueError, KeyError) as exc:
                    failures += 1
                    status = (
                        exc.response.status_code
                        if isinstance(exc, httpx.HTTPStatusError)
                        else None
                    )
                    message = f"公共行情不可用 HTTP {status}" if status else str(exc)
                    if status == 503:
                        message += " · " + str(exc.response.json()["detail"])
                    self.report(phase="DATA_SOURCE_UNAVAILABLE", error=message)
                    try:
                        self.publish_error(demands, message)
                    except httpx.HTTPError:
                        self.report(publisher_error="Official view bridge unavailable")
                self.stop.wait(3 if not failures else min(60, 2 ** min(failures, 6)))
        finally:
            self.publisher.close()
            self.source.close()
            self.lease.close()

    def close(self):
        self.stop.set()
        if self.thread.is_alive():
            self.thread.join(35)
