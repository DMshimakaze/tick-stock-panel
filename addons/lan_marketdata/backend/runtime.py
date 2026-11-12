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
from .grid import (
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
from .store import Store


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
        if info["attempted"] == target and not info["rebuild_from"]:
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
        key = min(groups)
        return (*key, sorted(groups[key])[: 8 if key[2] == "REBUILD" else 100])
    cold = [
        s
        for s, r in active.items()
        if r["next_retry"] <= time()
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
        self.context, self.settings = context, settings
        self.folder = Path(context.data_dir) / "user_data" / "lan_marketdata"
        self.folder.mkdir(parents=True, exist_ok=True)
        self.stop, self.lock = Event(), Lock()
        self.store, self.collector = None, None
        self.demands = {}
        self.minute_limit = min(int(settings.get("minute_requests_per_minute", 8)), 12)
        self.cold_budget = RollingBudget(
            int(settings.get("cold_requests_per_minute", 2))
        )
        self.state = dict(
            version="2.0.0",
            api_version=2,
            phase="STARTING",
            source_id=SOURCE,
            source="TickFlow Expert 5m → 15m",
            error=None,
            capacities=dict(CAPACITY, tail5=2),
            writer_alive=False,
            database="user_data/lan_marketdata/rolling15.sqlite3",
            database_connected=False,
            minute_request_limit=self.minute_limit,
            cold_request_limit=self.cold_budget.limit,
            collection_scope=settings.get(
                "collection_symbols", "FULL_MARKET_BOUNDED_QUEUE"
            ),
            request_counter_scope="SINCE_WORKER_START",
            quota_coordination="PLUGIN_OWNERSHIP_AND_PUBLIC_HOST_JOB_GATE",
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
            target=self.run, name="lan-marketdata-sqlite-writer", daemon=True
        )

    def start(self):
        self.thread.start()
        atexit.register(self.close)

    def demand(self, symbols):
        with self.lock:
            self.demands = {
                s: t for s, t in self.demands.items() if monotonic() - t < 600
            }
            for s in symbols:
                if s in self.demands or len(self.demands) < 64:
                    self.demands[s] = monotonic()

    def report(self, **values):
        with self.lock:
            self.state.update(values)

    def status(self):
        with self.lock:
            result = dict(
                self.state,
                writer_alive=self.thread.is_alive(),
                requests=dict(self.collector.metrics) if self.collector else {},
            )
        if self.store:
            try:
                result.update(self.store.snapshot())
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

    def run(self):
        try:
            self.store = Store(self.folder / "rolling15.sqlite3")
            self.report(database_connected=True)
            key = self.settings.get("tickflow_api_key") or os.environ.get(
                "TICKFLOW_API_KEY"
            )
            if not key:
                self.report(
                    phase="CREDENTIAL_REQUIRED",
                    error="Set plugin tickflow_api_key or TICKFLOW_API_KEY",
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
            # Source SDK cache owns only name metadata; market candles stay in rolling15.sqlite3.
            last_master_day = None
            failures = 0
            while not self.stop.is_set():
                work = None
                try:
                    now = str(now_cn())
                    if self.host_busy():
                        self.report(phase="HOST_BUSY", error=None, wait_seconds=10)
                        self.stop.wait(10)
                        continue
                    if last_master_day != now[:10]:
                        sessions = self.collector.calendar()
                        rows, overview = classify(self.collector.master(), now[:10])
                        self.store.save_master(rows, overview, sessions)
                        last_master_day = now[:10]
                    sessions = self.store.meta("sessions", [])
                    target = latest_point(sessions, now)
                    if not target:
                        self.report(phase="CALENDAR_PENDING")
                        self.stop.wait(10)
                        continue
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
                    if not work:
                        self.report(
                            phase="WAITING_NEXT_15M", error=None, target_15m=target
                        )
                        self.stop.wait(5)
                        continue
                    start, end, stage, symbols = work
                    cold = stage in ("COLD", "REBUILD")
                    if cold and self.cold_budget.delay() > 0:
                        self.stop.wait(min(5, self.cold_budget.delay()))
                        continue
                    if cold:
                        self.cold_budget.acquire(self.stop)
                    self.report(
                        phase=stage,
                        error=None,
                        target_15m=target,
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
                        if symbol not in received:
                            self.store.failed(
                                [symbol], end, now, errors[symbol]["code"]
                            )
                            continue
                        self.store.accept(
                            symbol,
                            received[symbol],
                            sessions=sessions,
                            start=start,
                            end=end,
                            now=now,
                            events=factors.get(symbol),
                            factor_response=symbol in factors,
                            rebuild=cold,
                        )
                        error = errors.get(symbol)
                        if error and (
                            error["timestamps"] is None
                            or any(
                                bucket_end(t, 15) == end for t in error["timestamps"]
                            )
                        ):
                            self.store.failed([symbol], end, now, error["code"])
                    with self.store.conn:
                        self.store.set_meta(
                            "request_counts", dict(self.collector.metrics)
                        )
                    self.report(error=None, phase="RUNNING", last_commit=now)
                    failures = 0
                except RequestDeferred as exc:
                    self.stop.wait(min(30, max(1, exc.until - monotonic())))
                except (SourceError, ValueError) as exc:
                    failures += 1
                    self.report(phase="SOURCE_UNAVAILABLE", error=str(exc))
                    if work:
                        self.store.failed(
                            work[3], work[1], str(now_cn()), "SOURCE_UNAVAILABLE"
                        )
                    self.stop.wait(min(60, 2 ** min(failures, 6)))
        except CollectionStopped:
            pass
        except Exception as exc:
            self.report(
                phase="ERROR",
                error=f"Writer stopped: {type(exc).__name__}; saved bars retained",
            )
        finally:
            if self.collector:
                self.collector.close()
            if self.store:
                self.store.close()
            self.report(database_connected=False)

    def close(self):
        self.stop.set()
        if self.thread.is_alive():
            self.thread.join(30)
        self.http.close()
