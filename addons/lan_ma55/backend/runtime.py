"""Full-market consumer. Only lan.marketdata owns source requests and K history."""

import atexit
from pathlib import Path
from threading import Event, Lock, Thread
from time import monotonic
import httpx

from .market import now_cn
from .market_client import Client
from .result_publisher import Publisher
from .shared_store import SharedStore
from .engine import RULE_VERSION
from .worker_lease import ProcessLease


class Worker:
    def __init__(self, context, settings):
        self.folder = Path(context.data_dir) / "user_data" / "lan_ma55"
        self.folder.mkdir(parents=True, exist_ok=True)
        self.settings = settings
        self.stop, self.lock = Event(), Lock()
        self.state = dict(
            phase="STARTING",
            backend_scanning=True,
            rule_version=RULE_VERSION,
            state_revision=4,
            version="0.3.3",
            source="lan.marketdata HTTP v1",
            error=None,
            started_at=str(now_cn()),
            minute_request_limit=0,
            requests={"http_requests": 0, "minute_requests": 0, "period_1m": 0},
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
        self.thread = Thread(
            target=self.run, name="lan-ma55-shared-consumer", daemon=True
        )

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

    def publish_status(self):
        try:
            self.publisher.summary(self.status())
            self.report(publisher_error=None)
        except (httpx.HTTPError, ValueError):
            self.report(
                publisher_error="Official Ext Data publication unavailable; saved SQLite retained"
            )

    def run(self):
        failures = 0
        last_history = 0
        last_status = 0
        historical_dates = []
        try:
            with SharedStore(self.folder / "state.sqlite3") as store:
                while not self.stop.is_set():
                    try:
                        first = self.source.universe()
                        if first["total"] == 0 or not first.get("latest_completed_at"):
                            raise ValueError(
                                "lan.marketdata is waiting for verified market universe/calendar"
                            )
                        master = first["rows"]
                        for offset in range(100, first["total"], 100):
                            page = self.source.universe(offset)
                            if page["generation"] != first["generation"]:
                                raise ValueError(
                                    "Universe changed during pagination; restart this read"
                                )
                            master.extend(page["rows"])
                        store.source_master(
                            master, first["universe"], first["generation"]
                        )
                        with store.conn:
                            store.set_meta("sessions", first["sessions"])
                        pending = store.meta("pending_publish")
                        if pending:
                            self.publisher.day(
                                pending["day"],
                                store.day_rows(pending["day"], pending["symbols"]),
                            )
                            with store.conn:
                                store.set_meta("pending_publish", None)
                        target = first["latest_completed_at"]
                        eligible = [r for r in master if r.get("eligible")]
                        # Full market is determined by metadata, never UI demand slots.
                        self.report(
                            **store.summary(target),
                            phase="ANALYZING",
                            metadata_processed=len(master),
                            metadata_expected=first["total"],
                            source_asof=target,
                            expected_symbols=len(eligible),
                            error=None,
                        )
                        self.publish_status()
                        last_status = monotonic()
                        receipts = store.receipts()
                        changed = [
                            r["symbol"]
                            for r in eligible
                            if receipts.get(r["symbol"])
                            != [
                                r.get("revision", 0),
                                r.get("basis_revision", 0),
                                target,
                                RULE_VERSION,
                                4,
                            ]
                        ]
                        for offset in range(0, len(changed), 24):
                            if self.stop.is_set():
                                break
                            payload = self.source.bars(
                                changed[offset : offset + 24], asof=target
                            )
                            symbols = store.consume(
                                payload["rows"],
                                sessions=payload["sessions"],
                                target=target,
                                now=str(now_cn()),
                                live_after=self.state["started_at"],
                            )
                            if symbols:
                                self.publisher.day(
                                    target[:10], store.day_rows(target[:10], symbols)
                                )
                                with store.conn:
                                    store.set_meta("pending_publish", None)
                            if monotonic() - last_status >= 5:
                                self.report(**store.summary(target), phase="ANALYZING")
                                self.publish_status()
                                last_status = monotonic()
                        if monotonic() - last_history >= 60 or changed:
                            history = store.history()
                            self.publisher.history(history)
                            historical_dates = list(history)
                            last_history = monotonic()
                            self.report(history_published_at=str(now_cn()))
                        summary = store.summary(target)
                        summary["available_dates"] = sorted(
                            set(summary["available_dates"]) | set(historical_dates)
                        )
                        self.report(**summary, phase="WAITING", source_asof=target)
                        self.publish_status()
                        failures = 0
                        self.stop.wait(10)
                    except (httpx.HTTPError, ValueError) as exc:
                        failures += 1
                        status = (
                            exc.response.status_code
                            if isinstance(exc, httpx.HTTPStatusError)
                            else None
                        )
                        self.report(
                            phase="DATA_SOURCE_UNAVAILABLE",
                            error=f"Shared source HTTP {status}"
                            if status
                            else str(exc),
                        )
                        self.publish_status()
                        self.stop.wait(min(60, 2 ** min(failures, 6)))
        except Exception as exc:
            self.report(
                phase="ERROR",
                backend_scanning=False,
                error=f"{type(exc).__name__}: {exc}",
            )
            self.publish_status()
        finally:
            self.publisher.close()
            self.source.close()
            self.lease.close()
            self.report(backend_scanning=False)

    def close(self):
        self.stop.set()
        if self.thread.is_alive():
            self.thread.join(35)
