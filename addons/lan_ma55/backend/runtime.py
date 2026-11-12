"""One TickFlow 15m producer: completed rounds first, a bounded cold queue second."""

import atexit
from datetime import timedelta
import os
from pathlib import Path
import sqlite3
from threading import Event, Lock, Thread
from time import monotonic, time

import httpx

from .collector import Collector, SourceError
from .market_grid import (
    CAPACITY,
    LEGACY,
    SOURCE,
    bucket_end,
    latest_point,
    now_cn,
    points,
    wall,
)
from .request_budget import CollectionStopped, RequestDeferred, RollingBudget
from .unified_store import UnifiedStore
from .result_publisher import Publisher
from .worker_lease import ProcessLease
from .engine import RULE_VERSION


def classify(items, day):
    rows = []
    for item in items:
        ext = item.get("ext") or {}
        stock = item.get("type") == "stock" and item.get("region") == "CN"
        listing = ext.get("listing_date")
        rows.append(
            dict(
                symbol=item["symbol"],
                name=item.get("name") or item["symbol"],
                eligible=stock and (not listing or listing <= day),
                source_type=item.get("type"),
                raw_tick=ext.get("tick_size"),
                listing_date=listing,
                listing_status=ext.get("status")
                or ext.get("listing_status")
                or "NOT_PROVIDED",
                metadata_source="TICKFLOW_INSTRUMENTS",
            )
        )
    return rows, dict(
        candidates=len(rows),
        expected=sum(r["eligible"] for r in rows),
        unpriced=sum(r["eligible"] and not r["raw_tick"] for r in rows),
        excluded=sum(not r["eligible"] for r in rows),
        unclassified=0,
        listing_status_unconfirmed=sum(
            r["listing_status"] == "NOT_PROVIDED" for r in rows
        ),
    )


def task(master, sessions, target, now, demanded=()):
    active = {s: r for s, r in master.items() if r["eligible"] or s in demanded}
    days = [d for d in sessions if d <= target[:10]][-18:]
    if not days:
        return None
    first = days[0] + " 09:35:00"
    groups = {}
    for symbol, info in active.items():
        if info["next_retry"] > time():
            continue
        if info["source_id"] != SOURCE or not info["latest_completed15"]:
            continue
        if info["attempted"] == target:
            continue
        if info["latest_completed15"] >= target and not info["rebuild_from"]:
            continue
        start = (
            str(wall(info["rebuild_from"]) - timedelta(minutes=10))
            if info["rebuild_from"]
            else next(
                iter(points(sessions, info["latest_completed15"], target)[1:]), None
            )
        )
        if start:
            cold = start < first
            groups.setdefault(
                (first if cold else start, target, "REBUILD" if cold else "LIVE"), []
            ).append(symbol)
    if groups:
        live = [key for key in groups if key[2] == "LIVE"]
        key = max(live or list(groups), key=lambda key: key[0])
        return (*key, sorted(groups[key])[: 8 if key[2] == "REBUILD" else 100])
    cold = [
        s
        for s, r in active.items()
        if r["next_retry"] <= time()
        and r["attempted"] != target
        and (r["source_id"] == LEGACY or not r["latest_completed15"])
    ]
    if cold:
        return (
            first,
            target,
            "COLD",
            sorted(cold, key=lambda s: (s not in demanded, s))[:8],
        )
    return None


class Worker:
    def __init__(self, context, settings):
        self.settings = settings
        self.folder = Path(context.data_dir) / "user_data" / "lan_ma55"
        self.folder.mkdir(parents=True, exist_ok=True)
        self.stop, self.lock = Event(), Lock()
        self.store = self.owner = self.collector = None
        self.demands = {}
        self.minute_limit = min(int(settings.get("minute_requests_per_minute", 8)), 12)
        self.cold_budget = RollingBudget(
            int(settings.get("cold_requests_per_minute", 2))
        )
        self.state = dict(
            version="0.5.2",
            api_version=1,
            phase="STARTING",
            backend_scanning=True,
            rule_version=RULE_VERSION,
            state_revision=4,
            source_id=SOURCE,
            source="TickFlow 5m → unified MA55 SQLite",
            database="user_data/lan_ma55/state.sqlite3",
            database_connected=False,
            capacities=dict(CAPACITY, tail5=2),
            error=None,
            started_at=str(now_cn()),
            minute_request_limit=self.minute_limit,
            cold_request_limit=self.cold_budget.limit,
            collection_scope=settings.get(
                "collection_symbols", "FULL_MARKET_BOUNDED_QUEUE"
            ),
            official_reuse="NOT_AVAILABLE_RAW_PROVENANCE_OR_BATCH_READ_CONTRACT",
            quota_coordination="PLUGIN_BUDGET_ONLY_ACCOUNT_POOL_NOT_VERIFIED",
        )
        self.publisher = Publisher(
            settings["api_base"],
            settings.get("api_token"),
            session=settings.get("api_session"),
        )
        self.http = httpx.Client(
            base_url=settings["api_base"],
            timeout=5,
            trust_env=False,
            cookies={"tf_session": settings["api_session"]}
            if settings.get("api_session")
            else {},
        )
        self.thread = Thread(
            target=self.run, name="lan-ma55-unified-writer", daemon=True
        )
        self.last_publication = self.last_history = 0

    def start(self):
        self.lease = ProcessLease(self.folder / "worker.lock")
        self.thread.start()
        atexit.register(self.close)

    def demand(self, symbols):
        with self.lock:
            self.demands = {
                s: t for s, t in self.demands.items() if monotonic() - t < 600
            }
            for symbol in symbols:
                if symbol in self.demands or len(self.demands) < 64:
                    self.demands[symbol] = monotonic()

    def report(self, **values):
        with self.lock:
            self.state.update(values)

    def status(self):
        with self.lock:
            result = dict(
                self.state,
                writer_alive=self.thread.is_alive(),
                worker_alive=self.thread.is_alive(),
                requests=dict(self.collector.metrics) if self.collector else {},
            )
        if self.store:
            try:
                result.update(self.store.snapshot())
                result["source_asof"] = result.get("latest_15m")
            except sqlite3.Error:
                result.update(
                    database_connected=False, database_error="SQLITE_READ_ERROR"
                )
        return result

    def host_busy(self):
        try:
            response = self.http.get("/api/pipeline/jobs", params={"limit": 1})
            response.raise_for_status()
            return bool(response.json().get("active_id"))
        except (httpx.HTTPError, ValueError):
            return True

    def publish(self, target, *, force=False):
        now = monotonic()
        if not force and now - self.last_publication < 5:
            return
        pending = self.owner.meta("pending_publish")
        try:
            if pending:
                self.publisher.day(
                    pending["day"],
                    self.owner.day_rows(pending["day"], pending["symbols"]),
                )
                with self.owner.conn:
                    self.owner.set_meta("pending_publish", None)
            if now - self.last_history >= 60:
                self.publisher.history(self.owner.history())
                self.last_history = now
                self.owner.checkpoint()
            self.report(**self.owner.summary(target), publisher_error=None)
            self.publisher.summary(self.status())
            self.last_publication = now
        except (httpx.HTTPError, ValueError):
            self.report(
                publisher_error="Official Ext Data unavailable; committed SQLite retained"
            )

    def analyze_pending(self, target, now):
        symbols = self.owner.catch_up(target, now, self.state["started_at"])
        for offset in range(0, len(symbols), 24):
            if self.stop.is_set():
                break
            with self.owner.conn:
                self.owner.conn.execute("BEGIN IMMEDIATE")
                self.owner.analyze(
                    symbols[offset : offset + 24],
                    self.store.meta("sessions", []),
                    target,
                    now,
                    self.state["started_at"],
                )
            self.publish(target)
        self.report(
            pending_analysis=max(0, len(symbols) - offset - 24) if symbols else 0
        )

    def run(self):
        try:
            self.owner = UnifiedStore(self.folder / "state.sqlite3")
            self.store = self.owner.market
            self.report(database_connected=True)
            key = self.settings.get("tickflow_api_key") or os.environ.get(
                "TICKFLOW_API_KEY"
            )
            if not key:
                self.report(
                    phase="CREDENTIAL_REQUIRED",
                    error="Configure TickFlow key for the MA55 owner",
                )
                return
            self.collector = Collector(
                key,
                self.folder / "tickflow_sdk_cache",
                stop=self.stop,
                report=self.report,
                limit=self.minute_limit,
                host_busy=self.host_busy,
            )
            last_master_day = self.store.meta("metadata_day")
            last_target = None
            while not self.stop.is_set():
                work = None
                try:
                    now = str(now_cn())
                    sessions = self.store.meta("sessions", [])
                    target = latest_point(sessions, now)
                    # Restart catch-up consumes only persisted input; no vendor request.
                    if target and target != last_target:
                        self.analyze_pending(target, now)
                        self.publish(target, force=True)
                        last_target = target
                    if self.host_busy():
                        self.report(phase="HOST_BUSY", wait_seconds=10)
                        self.stop.wait(10)
                        continue
                    if last_master_day != now[:10]:
                        sessions = self.collector.calendar()
                        rows, overview = classify(self.collector.master(), now[:10])
                        self.owner.refresh_master(rows, overview, sessions)
                        with self.owner.conn:
                            self.store.set_meta("metadata_day", now[:10])
                        last_master_day = now[:10]
                        target = latest_point(sessions, now)
                    if not target:
                        self.report(phase="CALENDAR_PENDING")
                        self.stop.wait(10)
                        continue
                    self.report(target_15m=target, source_asof=target)
                    master = self.store.universe()
                    with self.lock:
                        demanded = {
                            s for s, t in self.demands.items() if monotonic() - t < 600
                        }
                    unknown = sorted(demanded - master.keys())[:8]
                    if unknown:
                        rows, _ = classify(self.collector.metadata(unknown), now[:10])
                        self.store.put_metadata(rows)
                        master = self.store.universe()
                    scope = self.settings.get("collection_symbols")
                    if scope is not None:
                        master = {
                            s: r
                            for s, r in master.items()
                            if s in scope or s in demanded
                        }
                    work = task(master, sessions, target, now, demanded)
                    self.report(
                        queue_size=sum(
                            r["eligible"]
                            and r["attempted"] != target
                            and (
                                r["source_id"] != SOURCE
                                or (r["latest_completed15"] or "") < target
                            )
                            for r in master.values()
                        ),
                        queue_bound=len(master),
                    )
                    if not work:
                        self.report(phase="WAITING_NEXT_15M", error=None)
                        self.publish(target)
                        self.stop.wait(5)
                        continue
                    start, end, stage, symbols = work
                    cold = stage in ("COLD", "REBUILD")
                    if cold and self.cold_budget.delay() > 0:
                        self.publish(target)
                        self.stop.wait(min(5, self.cold_budget.delay()))
                        continue
                    if cold:
                        self.cold_budget.acquire(self.stop)
                    self.report(
                        phase=stage,
                        error=None,
                        active_symbols=len(symbols),
                        range_start=start,
                        range_end=end,
                    )
                    needed = [
                        s
                        for s in symbols
                        if master[s]["factors_day"] != now[:10]
                        or master[s]["source_id"] != SOURCE
                    ]
                    factors = self.collector.factors(needed)
                    received, errors = self.collector.fetch(
                        symbols, start, end, cold=cold
                    )
                    for symbol in symbols:
                        error = errors.get(symbol)
                        code = (
                            error["code"]
                            if error
                            and (
                                error["timestamps"] is None
                                or any(
                                    bucket_end(t, 15) == end
                                    for t in error["timestamps"]
                                )
                            )
                            else None
                        )
                        self.owner.accept_market(
                            symbol,
                            received.get(symbol, {}),
                            sessions=sessions,
                            start=start,
                            end=end,
                            now=now,
                            events=factors.get(symbol),
                            factor_response=symbol in factors,
                            rebuild=cold,
                            error=code,
                            live_after=self.state["started_at"],
                            request_counts=dict(self.collector.metrics),
                        )
                    self.flush_requests(stage)
                    self.report(phase="RUNNING", error=None, last_commit=now)
                    self.publish(target)
                except RequestDeferred as exc:
                    self.flush_requests("DEFERRED")
                    if exc.status_code == 429 and work:
                        for symbol in work[3]:
                            self.owner.accept_market(
                                symbol,
                                {},
                                sessions=sessions,
                                start=work[0],
                                end=work[1],
                                now=str(now_cn()),
                                error="SOURCE_UNAVAILABLE",
                                live_after=self.state["started_at"],
                            )
                    self.report(
                        phase="RATE_LIMITED" if exc.status_code == 429 else "QUOTA_WAIT"
                    )
                    self.publish(target)
                    self.stop.wait(min(30, max(1, exc.until - monotonic())))
                except (SourceError, ValueError) as exc:
                    self.flush_requests("FAILED")
                    self.report(phase="SOURCE_UNAVAILABLE", error=str(exc))
                    if work:
                        for symbol in work[3]:
                            self.owner.accept_market(
                                symbol,
                                {},
                                sessions=sessions,
                                start=work[0],
                                end=work[1],
                                now=str(now_cn()),
                                error="SOURCE_UNAVAILABLE",
                                live_after=self.state["started_at"],
                            )
                    self.publish(target)
                    self.stop.wait(10)
        except CollectionStopped:
            pass
        except Exception as exc:
            self.report(phase="ERROR", error=f"{type(exc).__name__}: {exc}")
        finally:
            if self.collector:
                self.collector.close()
            if self.owner:
                self.owner.checkpoint()
                self.owner.close()
            self.publisher.close()
            self.http.close()
            self.lease.close()
            self.report(database_connected=False, backend_scanning=False)

    def flush_requests(self, stage):
        if self.collector:
            rows = list(self.collector.observations)
            with self.owner.conn:
                self.owner.save_requests(rows, stage)
            for _ in rows:
                self.collector.observations.popleft()

    def close(self):
        self.stop.set()
        if self.thread.is_alive():
            self.thread.join(35)
