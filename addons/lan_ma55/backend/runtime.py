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
    latest_point,
    now_cn,
    points,
    wall,
)
from .request_budget import CollectionStopped, RequestDeferred, RollingBudget
from .native15_owner import NativeOwner
from .native15_shadow import SOURCE as NATIVE_SOURCE
from .native15_scheduler import task as native_task
from .result_publisher import Publisher
from .worker_lease import ProcessLease
from .native15_signal_adapter import FAMILY, RULE_REFERENCE
from .runtime_status import business_status


def classify(items, day):
    rows = []
    for item in items:
        ext = item.get("ext") or {}
        asset = item.get("type")
        exchange = item.get("exchange") or item["symbol"].rsplit(".", 1)[-1]
        admitted = item.get("region") == "CN" and (
            asset == "stock" and exchange in ("SH", "SZ", "BJ")
            or asset == "etf" and exchange in ("SH", "SZ")
        )
        listing = ext.get("listing_date")
        rows.append(
            dict(
                symbol=item["symbol"],
                name=item.get("name") or item["symbol"],
                eligible=admitted and (not listing or listing <= day),
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
        stock=sum(r["eligible"] and r["source_type"] == "stock" for r in rows),
        etf=sum(r["eligible"] and r["source_type"] == "etf" for r in rows),
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
        self.cache_demands = {}
        self.minute_limit = min(int(settings.get("minute_requests_per_minute", 8)), 12)
        self.cold_budget = RollingBudget(
            int(settings.get("cold_requests_per_minute", 2))
        )
        self.state = dict(
            version="0.9.3",
            api_version=1,
            phase="STARTING",
            active_operation="IDLE",
            backend_scanning=True,
            rule_version=FAMILY,
            rule_reference=RULE_REFERENCE,
            state_revision=4,
            source_id=NATIVE_SOURCE,
            source="TickFlow native15 → NATIVE15_REVIEW_V1",
            database="user_data/lan_ma55/state.sqlite3",
            database_connected=False,
            capacities=dict(CAPACITY),
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
            native=True,
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
        self.host_retry_at = 0
        self.host_failures = 0
        self.host_failure_reason = None

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

    def demand_cache(self, symbols):
        with self.lock:
            for symbol in symbols:
                if len(self.cache_demands) < 64:
                    self.cache_demands[symbol] = monotonic()

    def report(self, **values):
        with self.lock:
            if values.get("error") or values.get("publisher_error"):
                values["last_error_at"] = str(now_cn())
            if values.get("phase") and values["phase"] not in ("QUOTA_WAIT", "RATE_LIMITED", "HOST_BUSY"):
                values.setdefault("wait_seconds", 0)
                values.setdefault("waiting_for", None)
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
                snapshot = self.owner.status_snapshot(scope=self.settings.get("collection_symbols"))
                result.update(snapshot)
                result.update(business_status(result, snapshot, str(now_cn())))
                result["source_asof"] = result["latest_saved_15m"]
            except sqlite3.Error:
                result.update(
                    database_connected=False, database_error="SQLITE_READ_ERROR"
                )
        return result

    def host_busy(self):
        if monotonic() < self.host_retry_at:
            raise RequestDeferred(self.host_retry_at, reason=self.host_failure_reason)
        reason, status = None, None
        try:
            response = self.http.get("/api/pipeline/jobs", params={"limit": 1})
            status = response.status_code
            if status != 200:
                reason = f"HOST_HTTP_{status}"
            else:
                data = response.json()
                if not isinstance(data, dict) or "active_id" not in data:
                    reason = "HOST_INVALID_JSON"
                else:
                    self.host_failures = 0
                    self.host_failure_reason = None
                    self.report(host_job_status=dict(state="BUSY" if data["active_id"] else "IDLE",
                                active_id=data["active_id"], checked_at=str(now_cn()), http_status=200))
                    return bool(data["active_id"])
        except httpx.TimeoutException:
            reason = "HOST_TIMEOUT"
        except httpx.ConnectError:
            reason = "HOST_CONNECTION_ERROR"
        except httpx.HTTPError:
            reason = "HOST_NETWORK_ERROR"
        except ValueError:
            reason = "HOST_INVALID_JSON"
        self.host_failures += 1
        # No supplier requests while priority cannot be checked. Authentication
        # failures need configuration; transient checks back off after 3 attempts.
        delay = 300 if status in (401, 403, 404) or self.host_failures >= 3 else 30
        self.host_retry_at = monotonic() + delay
        self.host_failure_reason = reason
        self.report(host_job_status=dict(state="UNAVAILABLE", active_id=None,
                    reason=reason, http_status=status, checked_at=str(now_cn()),
                    retry_seconds=delay, consecutive_failures=self.host_failures))
        raise RequestDeferred(self.host_retry_at, status=status, reason=reason)

    def refresh_calendar(self, now):
        """Daily bars seed history; completed native15 independently admits today."""
        day = now[:10]
        sessions = self.store.meta("sessions", [])
        check = self.store.meta("calendar_check", {})
        if day in sessions:
            check = dict(check, confirmed_day=day, pending_day=None,
                         state="CONFIRMED", next_retry_at=None)
            if self.store.meta("calendar_check", {}) != check:
                with self.owner.conn:
                    self.store.set_meta("calendar_check", check)
            self.report(calendar=check)
            return sessions
        if check.get("next_retry_at") and now < check["next_retry_at"]:
            self.report(calendar=check)
            return sessions
        weekend = wall(now).weekday() >= 5
        check.update(checked_at=now, pending_day=None if weekend else day,
                     state="CLOSED" if weekend else "UNCONFIRMED",
                     next_retry_at=str(wall(now) + timedelta(seconds=300)))
        if not weekend and wall(now) < wall(day + " 09:46:15"):
            check.update(state="WAITING_FIRST_COMPLETED_15M", next_retry_at=day+" 09:46:15")
        # Persist the retry boundary before I/O, including failed source requests.
        with self.owner.conn:
            self.store.set_meta("calendar_check", check)
        self.report(calendar=check, phase="VERIFYING_CALENDAR", active_operation="IDLE")
        if check.get("daily_checked_day") != day:
            daily = self.collector.calendar()
            sessions = sorted(set(sessions) | {d for d in daily if d <= day})
            check.update(daily_last=max(daily, default=None), daily_checked_day=day)
            with self.owner.conn:
                self.store.set_meta("calendar_check", check)
        if day not in sessions and not weekend:
            evidence = self.collector.session_evidence(now)
            check["source_native15_asof"] = evidence["source_asof"]
            if evidence["confirmed_day"]:
                sessions = sorted(set(sessions) | {evidence["confirmed_day"]})
                check["verification_source"] = "TICKFLOW_COMPLETED_INDEX_NATIVE15"
        if day in sessions:
            check.update(confirmed_day=day, pending_day=None, state="CONFIRMED",
                         last_verified_at=now, next_retry_at=None)
            check.setdefault("verification_source", "TICKFLOW_DAILY_BAR")
        with self.owner.conn:
            self.store.set_sessions(sessions)
            self.owner.set_meta("sessions", self.store.meta("sessions", []))
            self.store.set_meta("calendar_check", check)
        self.report(calendar=check, active_operation="IDLE")
        return sessions

    def publish(self, target, *, force=False):
        now = monotonic()
        if not force and now - self.last_publication < 5:
            return
        pending = self.owner.meta("pending_publish")
        prior_operation = self.state.get("active_operation", "IDLE")
        self.report(active_operation="PUBLISHING")
        self.report(**self.owner.summary(target))
        try:
            if pending:
                self.publisher.day(
                    pending["day"],
                    self.owner.day_rows(pending["day"], pending["symbols"]),
                )
                with self.owner.conn:
                    self.owner.set_meta("pending_publish", None)
                    self.owner.set_meta("published_market_date", pending["day"])
                    self.owner.set_meta("day_published_revision", self.owner.meta("revision", 0))
                self.report(published_market_date=pending["day"],
                            day_published_revision=self.owner.meta("revision", 0),
                            day_published_at=str(now_cn()))
            if now - self.last_history >= 60:
                self.publisher.history(self.owner.history())
                self.last_history = now
                self.owner.checkpoint()
                self.report(history_published_at=str(now_cn()))
            self.report(publisher_error=None)
            if target:
                self.publisher.prices(target[:10], self.store.prices(target[:10]))
        except (httpx.HTTPError, ValueError):
            self.report(
                publisher_error="官方结果发布失败；SQLite已提交，保留上次有效投影"
            )
        try:
            payload = self.status()
            # Publishing itself is visible on /status. A completed projection
            # resumes the actual operation instead of sticking at PUBLISHING.
            if payload["active_operation"] != "BLOCKED":
                payload["active_operation"] = prior_operation
            payload["pending_publish"] = self.owner.meta("pending_publish") is not None
            self.publisher.summary(payload)
            self.report(last_publish_at=str(now_cn()))
            self.last_publication = now
        except (httpx.HTTPError, ValueError):
            self.report(publisher_error="官方状态投影发布失败；保留上次有效投影")
        finally:
            self.report(active_operation=prior_operation)

    def analyze_pending(self, target, now):
        symbols = self.owner.catch_up(target, now, self.state["started_at"])
        master = self.store.universe()
        symbols = [s for s in symbols if master[s]["source_id"] == NATIVE_SOURCE
                   and (master[s]["latest_completed15"] or "") >= target]
        scope = self.settings.get("collection_symbols")
        if scope is not None:
            symbols = [s for s in symbols if s in scope]
        for offset in range(0, len(symbols), 24):
            if self.stop.is_set():
                break
            with self.owner.conn:
                self.owner.conn.execute("BEGIN IMMEDIATE")
                self.report(active_operation="CALCULATING_NATIVE15_CANDIDATES", pending_analysis=len(symbols)-offset)
                self.owner.analyze(
                    symbols[offset : offset + 24],
                    self.store.meta("sessions", []),
                    target,
                    now,
                    self.state["started_at"],
                )
            self.report(active_operation="IDLE")
            self.publish(target)
        self.report(
            pending_analysis=max(0, len(symbols) - offset - 24) if symbols else 0
        )

    def run(self):
        try:
            self.owner = NativeOwner(self.folder / "state.sqlite3")
            self.store = self.owner.market
            self.report(database_connected=True, calendar=self.store.meta("calendar_check", {}),
                        published_market_date=self.owner.meta("published_market_date") or self.owner.summary(None)["market_date"],
                        day_published_revision=self.owner.meta("day_published_revision", 0))
            key = self.settings.get("tickflow_api_key") or os.environ.get(
                "TICKFLOW_API_KEY"
            )
            if not key:
                self.report(
                    phase="CREDENTIAL_REQUIRED",
                    error="Configure TickFlow key for the MA55 owner",
                )
                self.publish(latest_point(self.store.meta("sessions", []), str(now_cn())), force=True)
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
                    if self.host_busy():
                        self.report(phase="HOST_BUSY", active_operation="IDLE", wait_seconds=10)
                        self.publish(target)
                        self.stop.wait(10)
                        continue
                    sessions = self.refresh_calendar(now)
                    target = latest_point(sessions, now)
                    if last_master_day != now[:10]:
                        rows, overview = classify(self.collector.master(), now[:10])
                        self.owner.refresh_master(rows, overview, sessions)
                        with self.owner.conn:
                            self.store.set_meta("metadata_day", now[:10])
                        last_master_day = now[:10]
                        target = latest_point(sessions, now)
                    # Verify the session first. Only actually arrived target
                    # bars enter restart analysis; stale input is not a new scan.
                    if target and (target != last_target or self.owner.catch_up(target, now, self.state["started_at"])):
                        self.analyze_pending(target, now)
                        self.publish(target, force=True)
                        last_target = target
                    if not target:
                        self.report(phase="CALENDAR_PENDING")
                        self.stop.wait(10)
                        continue
                    self.report(target_15m=target)
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
                    with self.owner.conn:
                        self.owner.set_meta("target", target)
                    work = native_task(self.store, master, sessions, target, now, demanded)
                    # Saved chart rebuilds never consume commercial quota.
                    with self.lock:
                        cache_symbols = list(self.cache_demands)[:8]
                        for symbol in cache_symbols:
                            self.cache_demands.pop(symbol, None)
                    if not work or work[2] != "A":
                        self.store.warm_cache(cache_symbols)
                    self.report(queue_size=sum(r["eligible"] and (r["latest_completed15"] or "") < target for r in master.values()),
                                queue_bound=len(master))
                    if not work:
                        self.report(phase="WAITING_NEXT_15M", active_operation="IDLE", active_symbols=0, work_stage=None, error=None)
                        self.publish(target)
                        self.stop.wait(5)
                        continue
                    start, end, stage, symbols = work
                    self.report(work_stage=stage)
                    if stage == "C" and self.cold_budget.delay() > 0:
                        self.report(phase="QUOTA_WAIT", active_operation="IDLE", wait_seconds=round(self.cold_budget.delay(), 2), waiting_for="HISTORY_BUDGET")
                        self.publish(target)
                        self.stop.wait(min(5, self.cold_budget.delay()))
                        continue
                    # B is essential strategy input and uses the main 8 RPM pool;
                    # only optional C deep history is restricted to the cold pool.
                    if stage == "C":
                        self.cold_budget.acquire(self.stop)
                    self.report(phase=stage, error=None, active_symbols=len(symbols), range_start=start, range_end=end,
                                active_operation="COLLECTING_NATIVE15")
                    self.store.attempt(symbols, end, now, stage)
                    needed = [s for s in symbols if stage != "C" and master[s]["factors_day"] != now[:10]]
                    factors = self.collector.factors(needed)
                    self.publish(target, force=True)
                    received, errors = self.collector.fetch(symbols, start, end)
                    self.report(active_operation="CALCULATING_NATIVE15_CANDIDATES")
                    self.owner.accept_batch(symbols, received, errors, sessions=sessions, start=start, end=end, now=str(now_cn()),
                                            factors=factors, stage=stage, live_after=self.state["started_at"])
                    self.flush_requests(stage)
                    self.report(phase="RUNNING", active_operation="IDLE", error=None, last_commit=str(now_cn()))
                    if any(received.get(s) for s in symbols):
                        self.report(last_success_at=str(now_cn()), last_success_stage=stage)
                    self.publish(target, force=True)
                except RequestDeferred as exc:
                    self.flush_requests("DEFERRED")
                    if exc.status_code == 429 and not (exc.reason or "").startswith("HOST_") and work:
                        self.store.failed(work[3], work[1], str(now_cn()), "RATE_LIMITED", stage=work[2],
                                          delay=max(1, exc.until-monotonic()))
                    self.report(phase="HOST_STATUS_UNAVAILABLE" if (exc.reason or "").startswith("HOST_") else "RATE_LIMITED" if exc.status_code == 429 else "QUOTA_WAIT",
                                active_operation="IDLE", wait_seconds=round(max(0, exc.until-monotonic()), 2), waiting_for=exc.reason)
                    self.publish(target)
                    self.stop.wait(min(5, max(1, exc.until-monotonic())))
                except (SourceError, ValueError) as exc:
                    self.flush_requests("FAILED")
                    credential = getattr(exc, "status_code", None) in (401, 403)
                    self.report(phase="CREDENTIAL_REQUIRED" if credential else "SOURCE_UNAVAILABLE",
                                active_operation="BLOCKED", error=str(exc))
                    if work:
                        self.store.failed(work[3], work[1], str(now_cn()), "CREDENTIAL_REQUIRED" if credential else "SOURCE_UNAVAILABLE", stage=work[2])
                    self.publish(target, force=True)
                    if credential:
                        # Explicit account refusal requires a settings/restart action.
                        # Keep the RO service alive without further supplier calls.
                        while not self.stop.wait(5):
                            self.publish(target)
                        break
                    self.stop.wait(5)
        except CollectionStopped:
            pass
        except Exception as exc:
            self.report(phase="ERROR", active_operation="BLOCKED", error=f"{type(exc).__name__}: {exc}")
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
