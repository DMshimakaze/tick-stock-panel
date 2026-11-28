"""Single-owner scheduler for verified EastMoney focus and completed TickFlow bars."""

import atexit
import os
from bisect import bisect_left
from collections import OrderedDict, defaultdict
from datetime import timedelta
from itertools import islice
from pathlib import Path
from threading import Event, Lock, Thread
from time import monotonic, time

import httpx

from .activation_results import display_targets
from .collector import Collector, SourceError
from .daily_store import DailyStore
from .dc_focus_owner import DCFocusOwner
from .exchange_sessions import INTRADAY, frame_schedules, next_native15_request, planned_session
from .market_grid import (
    CAPACITY,
    now_cn,
    wall,
)
from .market_store import encoded
from .multiframe_core.multiframe import TIMEFRAMES, VERSION
from .native15_owner import NativeOwner
from .native15_scheduler import task as native_task
from .native15_shadow import SOURCE as NATIVE_SOURCE
from .native15_store import in_verified_halt
from .observations import Observations
from .request_budget import WINDOW_SECONDS, CollectionStopped, RequestDeferred, RollingBudget
from .result_publisher import RUNTIME_ID, Publisher
from .runtime_status import completed_targets, observation_summary, runtime_projection
from .workbench_bridge import WorkbenchBridge, focus_view
from .worker_lease import ProcessLease

ANALYSIS_BATCH = 12
WRITE_BATCH = 16


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


class Worker:
    def __init__(self, context, settings):
        self.settings = settings
        self.folder = Path(context.data_dir) / "user_data" / "lan_ma55"
        self.folder.mkdir(parents=True, exist_ok=True)
        self.stop, self.lock = Event(), Lock()
        self.store = self.owner = self.collector = self.focus = None
        self.market_rejected = False
        self.view = None
        self.minute_limit = min(int(settings.get("minute_requests_per_minute", 8)), 12)
        self.cold_budget = RollingBudget(int(settings.get("cold_requests_per_minute", 2)))
        self.state = dict(
            version="0.13.14", api_version=1, phase="STARTING", active_operation="IDLE",
            backend_scanning=False, rule_version=VERSION, source_id=NATIVE_SOURCE,
            source="TickFlow 15m / 1d", database="user_data/lan_ma55/state.sqlite3",
            database_connected=False, capacities=dict(CAPACITY), error=None,
            started_at=str(now_cn()), collection_scope="VERIFIED_DC_FOCUS",
            minute_request_limit=self.minute_limit, cold_request_limit=self.cold_budget.limit,
        )
        credentials = dict(session=settings.get("api_session"))
        self.publisher = Publisher(settings["api_base"], settings.get("api_token"), **credentials)
        self.http = httpx.Client(
            base_url=settings["api_base"], timeout=5, trust_env=False,
            cookies={"tf_session": settings["api_session"]} if settings.get("api_session") else {},
        )
        self.thread = Thread(target=self.run, name="lan-ma55-unified-writer", daemon=True)
        self.last_commands = self.last_publication = 0
        self.host_retry_at = self.host_failures = 0
        self.host_failure_reason = None
        self.bridge_prepared = False
        self.bridge_retry = 0
        self.targets, self.market_day = {}, None
        self.view_input, self.published_view = None, None
        self.view_dirty, self.view_sequence = True, 0
        self.last_runtime, self.last_heartbeat, self.runtime_retry = None, 0, 0
        self.analyze_queue = OrderedDict()
        self.analysis_scope, self.analysis_targets = set(), {}
        self.analysis_calendar = None
        self.repair_turn = 0

    def start(self):
        self.lease = ProcessLease(self.folder / "worker.lock")
        self.thread.start()
        atexit.register(self.close)

    def active_scope(self):
        if not self.focus:
            return set()
        universe = self.focus.taxonomy.active_scope()
        scope = self.focus.snapshot["members"].keys() & universe
        return scope if scope < universe else set()

    def report(self, **values):
        with self.lock:
            if values.get("error") or values.get("publisher_error"):
                values["last_error_at"] = str(now_cn())
            self.state.update(values)

    def status(self):
        with self.lock:
            return dict(
                self.state, **(self.view["summary"] if self.view else {}),
                writer_alive=self.thread.is_alive(), worker_alive=self.thread.is_alive(),
                requests=dict(self.collector.metrics) if self.collector else {},
            )

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

    def sessions(self, now):
        observed = self.store.meta("sessions", [])
        day = now[:10]
        return sorted(set(observed) | ({day} if planned_session(day) else set()))

    def refresh_calendar(self, now):
        """Fill the calendar once per market session, before any focus K-lines."""
        day = now[:10]
        observed = self.store.meta("sessions", [])
        sessions = self.sessions(now)
        market_day = sessions[-1] if sessions else None
        key = market_day if planned_session(day) is not None else day
        state = self.store.meta("focus_calendar_check", {})
        if key and state.get("key") != key and time() >= state.get("retry_at", 0):
            with self.owner.conn:
                self.store.set_meta("focus_calendar_check", dict(state, retry_at=time() + 300))
            history = self.collector.calendar()
            verified = [d for d in history if d <= day and planned_session(d) is not False]
            if not verified:
                raise ValueError("EXCHANGE_CALENDAR_MISSING")
            with self.owner.conn:
                self.store.set_sessions(verified)
                self.owner.set_meta("sessions", self.store.meta("sessions", []))
                self.store.set_meta("focus_calendar_check", dict(
                    key=key if planned_session(day) is not None or day in verified else None,
                    retry_at=time() + 300, source="TICKFLOW_INDEX_1D",
                ))
            observed = self.store.meta("sessions", [])
            sessions = self.sessions(now)
        verified_today = day in observed or planned_session(day) is True
        self.report(calendar=dict(
            state="CONFIRMED" if verified_today else "CLOSED" if planned_session(day) is False else "UNCONFIRMED",
            confirmed_day=day if verified_today else None, last_data_day=max(observed, default=None),
            verification_source="EXCHANGE_2026_NOTICE" if planned_session(day) is not None else "TICKFLOW_INDEX_1D",
        ))
        return sessions

    def prepare_bridge(self):
        if not self.bridge_prepared and monotonic() >= self.bridge_retry:
            self.bridge_retry = monotonic() + 15
            try:
                self.publisher.prepare()
            except (httpx.HTTPError, ValueError):
                self.report(publisher_error="工作台扩展数据尚未就绪")
            else:
                self.bridge_prepared = True
                self.report(publisher_error=None)

    def publish(self, schedules):
        if monotonic() - self.last_publication < 5:
            return
        self.last_publication = monotonic()
        scope = self.active_scope()
        snapshot = self.focus.snapshot
        view_input = (snapshot["revision"], self.targets, self.market_day, self.store.meta("data_revision", 0),
                      self.focus.taxonomy.status(), self.focus.taxonomy.daily)
        if self.view_dirty or self.view_input != view_input:
            current = {**snapshot, "members": {symbol: snapshot["members"][symbol]
                                               for symbol in snapshot["members"] if symbol in scope}}
            master = self.store.universe(scope)
            rows = self.observations.rows(current, master, schedules, self.market_day)
            latest_saved = max((info["latest_completed15"] for info in master.values()
                                if info.get("latest_completed15")), default=None)
            prior_analysis = None
            if self.view and (snapshot["revision"], self.focus.run) == (
                self.view["focus"]["revision"], self.view["focus"]["taxonomy"]["run"],
            ):
                prior_analysis = self.view["summary"]["latest_analyzed_15m"]
            self.view_sequence += 1
            view = dict(
                result_id=f"{self.state['started_at']}/{self.view_sequence}",
                focus=focus_view(self.focus, self.market_day), rows=rows,
                industry_paths=self.focus.industry_paths(scope),
                activation_targets=display_targets(self.targets, self.market_day),
                summary=observation_summary(rows, self.targets, self.market_day, latest_saved=latest_saved,
                                            latest_analyzed=prior_analysis),
                state={key: self.state.get(key) for key in ("phase", "error", "waiting_for",
                       "calendar", "active_operation", "publisher_error")}, rule_version=VERSION,
            )
            with self.lock:
                self.view = view
            self.view_input, self.view_dirty = view_input, False
        if not self.bridge_prepared or (self.published_view and self.published_view["id"] == self.view["result_id"]):
            return
        try:
            self.bridge.publish(self.view)
        except (httpx.HTTPError, ValueError):
            self.report(publisher_error="工作台发布失败，保留上次结果")
        else:
            self.published_view = dict(
                id=self.view["result_id"], published_ms=int(time() * 1000),
                scope=(snapshot["revision"], self.focus.run, len(scope)), targets=self.targets,
                coverage=self.view["summary"],
            )
            self.report(publisher_error=None)

    def publish_runtime(self, *, alive=True):
        now = monotonic()
        if not self.bridge_prepared or now < self.runtime_retry:
            return
        status = runtime_projection(
            self.state, focus=self.focus, scope_count=len(self.active_scope()) if self.focus else None,
            targets=self.targets, market_day=self.market_day, published=self.published_view, worker_alive=alive,
        )
        host = status.get("host_job_status") or {}
        comparison = dict(status, host_job_status={k: v for k, v in host.items() if k != "checked_at"})
        # Stage changes can alternate twice per scheduler turn. Every ingest
        # clears host caches, so coalesce transient analysis/wait updates. Source
        # operations, errors, new results and shutdown remain immediate.
        immediate = self.last_runtime is None or not alive or (
            comparison["result_id"] != self.last_runtime["result_id"]
        ) or (
            comparison["active_operation"] != "IDLE" and comparison["phase"] != "ANALYZING"
        ) or any(
            comparison[field] != self.last_runtime[field]
            for field in ("error", "publisher_error", "command_error")
        )
        interval = 10 if comparison == self.last_runtime else 5
        if not immediate and now - self.last_heartbeat < interval:
            return
        row = dict(scope="lan.ma55", payload_json=encoded(dict(status, status_at_ms=int(time() * 1000))))
        try:
            self.publisher.ingest(RUNTIME_ID, [row], timeout=2)
        except (httpx.HTTPError, ValueError):
            self.runtime_retry = now + 5
        else:
            self.last_runtime, self.last_heartbeat = comparison, now

    def mark_task(self, phase, operation, symbols):
        self.report(phase=phase, work_stage=None if phase == "ANALYZING" else phase,
                    active_operation=operation, active_symbols=len(symbols) if symbols is not None else None,
                    error=None, waiting_for=None)
        self.publish_runtime()

    def service_commands(self, sessions, schedules, weeks):
        if not self.bridge_prepared or monotonic() - self.last_commands < 0.8:
            return
        self.last_commands = monotonic()
        try:
            self.bridge.consume(self.market_day, sessions, schedules, weeks)
        except (httpx.HTTPError, ValueError):
            self.report(command_error="工作台请求或回执暂不可用")
        else:
            self.report(command_error=None)

    def enqueue_analysis(self, symbols, frames=TIMEFRAMES, *, live=False, priority=False):
        for symbol in symbols:
            queued = self.analyze_queue.setdefault(symbol, {})
            queued.update({frame: live or queued.get(frame, False) for frame in frames})
            if priority:
                self.analyze_queue.move_to_end(symbol, last=False)

    def sync_analysis(self, scope):
        calendar = self.store.meta("calendar_revision", 0)
        removed = self.analysis_scope - scope
        for symbol in removed:
            self.analyze_queue.pop(symbol, None)
            for frame in TIMEFRAMES:
                self.observations.needs.pop((symbol, frame), None)
        changed = tuple(frame for frame in TIMEFRAMES if self.targets[frame] != self.analysis_targets.get(frame))
        if changed:
            # LIVE applies to the completed target whose input actually arrived.
            for queued in self.analyze_queue.values():
                for frame in changed:
                    if frame in queued:
                        queued[frame] = False
        if calendar != self.analysis_calendar:
            changed = TIMEFRAMES
        if changed:
            self.enqueue_analysis(scope, changed)
        self.enqueue_analysis(scope - self.analysis_scope, priority=True)
        self.analysis_scope, self.analysis_targets, self.analysis_calendar = scope, self.targets, calendar

    def analyze_next(self, sessions, schedules, weeks, now, *, stage="ANALYZING"):
        if not self.analyze_queue:
            return
        # At the close the daily/weekly replays can take much longer than the
        # new 15m result. Commit and publish the minutes before those replays.
        target = self.targets.get("15m")
        minute = []
        if target and target == self.targets.get("daily"):
            minute = list(islice((s for s, frames in self.analyze_queue.items() if "15m" in frames), ANALYSIS_BATCH))
        symbols = minute or list(islice(self.analyze_queue, ANALYSIS_BATCH))
        grouped = defaultdict(list)
        for symbol in symbols:
            if minute:
                queued = self.analyze_queue[symbol]
                wanted = {"15m": queued.pop("15m")}
                if not queued:
                    del self.analyze_queue[symbol]
            else:
                wanted = self.analyze_queue.pop(symbol)
            for live in (True, False):
                frames = tuple(frame for frame in TIMEFRAMES if wanted.get(frame) is live)
                if frames:
                    grouped[frames, live].append(symbol)
        began = monotonic()
        self.mark_task(stage, "ANALYZING_MA55", symbols)
        for (frames, live), batch in grouped.items():
            self.view_dirty |= self.observations.refresh(
                batch, sessions, schedules, weeks, now, self.state["started_at"], live=live, frames=frames,
            )
        with self.owner.conn:
            self.owner.conn.execute("INSERT INTO native15_timing(at,stage,symbols,scan_ms) VALUES(?,?,?,?)",
                                    (now, "ANALYSIS", len(symbols), (monotonic() - began) * 1000))
        self.service_commands(sessions, schedules, weeks)

    def update_metadata(self, scope, now, market_day):
        saved = self.store.meta("focus_metadata_checks", {})
        missing = [symbol for symbol in scope if saved.get(symbol) != market_day][:100]
        if missing:
            rows, _ = classify(self.collector.metadata(missing), now[:10])
            with self.owner.conn:
                self.store.put_metadata([row for row in rows if row["symbol"] in missing])
                self.store.set_meta("focus_metadata_checks", dict(saved, **dict.fromkeys(missing, market_day)))
            self.view_dirty = True
            self.enqueue_analysis((row["symbol"] for row in rows if row["symbol"] in missing), priority=True)
        return bool(missing)

    def run(self):
        try:
            self.owner = NativeOwner(self.folder / "state.sqlite3")
            self.store = self.owner.market
            self.daily = DailyStore(self.owner.conn)
            self.observations = Observations(self.store, self.daily)
            self.focus = DCFocusOwner(
                self.owner, os.environ.get("TUSHARE_RELAY_TOKEN"), os.environ.get("TUSHARE_RELAY_URL"),
                self.stop, int(self.settings.get("dc_requests_per_minute", 18)),
            )
            self.bridge = WorkbenchBridge(self.owner, self.focus, self.observations, self.publisher)
            self.report(database_connected=True)
            key = self.settings.get("tickflow_api_key") or os.environ.get("TICKFLOW_API_KEY")
            if key:
                self.collector = Collector(
                    key, self.folder / "tickflow_sdk_cache", stop=self.stop, report=self.report,
                    limit=self.minute_limit, host_busy=self.host_busy, cold_budget=self.cold_budget,
                    scope=self.active_scope,
                )
                self.restore_budgets(str(now_cn()))
            while not self.stop.is_set():
                now = str(now_cn())
                sessions = self.sessions(now)
                schedules, weeks = frame_schedules(sessions, now)
                self.targets, self.market_day = completed_targets(schedules), sessions[-1] if sessions else None
                work = None
                phase, stage, waiting_stage, wait_reason = "WAITING_NEXT_15M", None, None, None
                try:
                    self.prepare_bridge()
                    if self.last_runtime is None:
                        self.publish_runtime()
                    self.service_commands(sessions, schedules, weeks)
                    self.sync_analysis(self.active_scope())
                    self.analyze_next(sessions, schedules, weeks, now)
                    scope = self.active_scope()
                    self.report(deferred_bj_factors=self.store.deferred_bj_factors(scope))
                    if not scope:
                        phase = "SCOPE_PAUSED"
                    elif not self.collector or self.market_rejected:
                        phase = "CREDENTIAL_REQUIRED"
                    elif self.host_busy():
                        phase = "HOST_BUSY"
                    else:
                        sessions = self.refresh_calendar(now)
                        schedules, weeks = frame_schedules(sessions, now)
                        self.targets, self.market_day = completed_targets(schedules), sessions[-1] if sessions else None
                        target = self.targets["15m"]
                        self.service_commands(sessions, schedules, weeks)
                        scope = self.active_scope()
                        self.sync_analysis(scope)
                        master = self.store.universe(scope)
                        needs = self.observations.needs
                        cold_allowed = self.cold_budget.delay() == 0
                        halts = self.store.meta("verified_trading_halts", {})
                        if target:
                            minute_master = {s: info for s, info in master.items() if not in_verified_halt(halts.get(s), target)}
                            day = now[:10]
                            factor_day = day if day in sessions and target[:10] < day and now[11:] >= "09:30:00" else None
                            work = native_task(self.store, minute_master, sessions, target, now,
                                               needs=needs, grid=schedules["15m"], cold_allowed=cold_allowed,
                                               factor_day=factor_day)
                        metadata = bool(sessions and (not work or work[2] != "A")
                                        and self.update_metadata(scope, now, sessions[-1]))
                        if metadata:
                            work = None
                        daily_master = {s: info for s, info in master.items()
                                        if not in_verified_halt(halts.get(s), self.targets["daily"])}
                        daily = (self.daily.task(daily_master, schedules["daily"], needs, cold_allowed=cold_allowed)
                                 if not metadata and (not work or work[2] == "C") else None)
                        use_daily = daily and (not work or self.repair_turn % 2 == 0)
                        budget = self.collector.budgets["minute"]
                        next_request = next_native15_request(sessions, now)
                        reserve = next_request and next_request <= now_cn() + timedelta(seconds=WINDOW_SECONDS)
                        if (daily or work) and budget.delay() > 0:
                            phase = "RATE_LIMITED" if budget.cooldown > monotonic() else "QUOTA_WAIT"
                            waiting_stage = ("W" if daily[-1] else "D") if use_daily else work[2]
                        elif (daily or work) and reserve:
                            wait_reason = "NATIVE15_BUDGET_RESERVED"
                        elif use_daily:
                            start, end, count, symbols, cold = daily
                            stage = "W" if cold else "D"
                            self.mark_task(stage, "COLLECTING_DAILY", symbols)
                            received, errors = self.collector.fetch_daily(symbols, start, end, count=count, cold=cold)
                            self.flush_requests(stage)
                            for offset in range(0, len(symbols), WRITE_BATCH):
                                batch = symbols[offset:offset + WRITE_BATCH]
                                self.daily.accept(batch, received, errors, schedules["daily"], master)
                                self.service_commands(sessions, schedules, weeks)
                            scope = self.active_scope()
                            active = [symbol for symbol in symbols if symbol in scope]
                            self.queue_received(active, ("daily", "weekly"), live=start == end == self.targets["daily"])
                            self.repair_turn += 1
                        elif work:
                            stage = work[2]
                            self.collect_work(work, sessions, schedules, weeks, now, target)
                            if stage == "C":
                                self.repair_turn += 1
                        elif not target:
                            phase = "CALENDAR_PENDING"
                        elif self.analyze_queue:
                            phase = "ANALYZING"
                    if stage is not None:
                        self.sync_analysis(self.active_scope())
                        self.analyze_next(sessions, schedules, weeks, str(now_cn()), stage=stage)
                    self.report(phase=phase, active_operation="IDLE", active_symbols=None,
                                backend_scanning=bool(scope and self.collector and not self.market_rejected),
                                work_stage=waiting_stage, error=None, waiting_for=wait_reason)
                except RequestDeferred as exc:
                    if exc.status_code == 429 and not (exc.reason or "").startswith("HOST_"):
                        with self.owner.conn:
                            self.owner.set_meta("quota_until", time()+max(0, exc.until-monotonic()))
                    phase = "HOST_STATUS_UNAVAILABLE" if (exc.reason or "").startswith("HOST_") else (
                        "RATE_LIMITED" if exc.status_code == 429 else "QUOTA_WAIT")
                    self.report(phase=phase, active_operation="IDLE", active_symbols=None,
                                work_stage=stage, waiting_for=exc.reason, error=None)
                except (SourceError, ValueError) as exc:
                    credential = getattr(exc, "status_code", None) in (401, 403)
                    self.market_rejected = self.market_rejected or credential
                    if stage in ("D", "W"):
                        self.daily.accept(symbols, {}, dict.fromkeys(symbols, "SOURCE_UNAVAILABLE"),
                                          schedules["daily"], master)
                    if stage in ("A", "B", "C") and work:
                        self.store.failed(work[3], work[1], now, "SOURCE_UNAVAILABLE", stage=stage)
                    if stage == "F" and work:
                        self.store.factor_failed(work[3], now, "SOURCE_UNAVAILABLE")
                    self.report(phase="CREDENTIAL_REQUIRED" if credential else "SOURCE_UNAVAILABLE",
                                active_operation="BLOCKED", active_symbols=None, work_stage=stage,
                                waiting_for=None, error=str(exc))
                self.flush_requests(stage or "METADATA")
                self.service_commands(sessions, schedules, weeks)
                # Classification uses a separate provider and can progress
                # during a TickFlow cooldown. Service edits before a new call.
                if not stage or self.state["phase"] in ("QUOTA_WAIT", "RATE_LIMITED"):
                    self.focus.schedule(self.market_day, sessions,
                                        self.collector.taxonomy_universe
                                        if self.collector and not self.market_rejected else None)
                self.publish_runtime()
                self.publish(schedules)
                self.publish_runtime()
                self.stop.wait(0.4 if self.analyze_queue else 0.8)
        except CollectionStopped:
            self.report(phase="STOPPED", active_operation="IDLE", active_symbols=None)
        except Exception as exc:
            self.report(phase="ERROR", active_operation="BLOCKED", active_symbols=None,
                        error=f"{type(exc).__name__}: {exc}")
        finally:
            if self.stop.is_set() and self.state["phase"] != "ERROR":
                self.report(phase="STOPPED", active_operation="IDLE", active_symbols=None)
            self.publish_runtime(alive=False)
            if self.collector:
                self.collector.close()
            if self.focus:
                self.focus.source.close()
            if self.owner:
                self.owner.checkpoint()
                self.owner.close()
            self.publisher.close()
            self.http.close()
            self.lease.close()
            self.report(database_connected=False, backend_scanning=False)

    def queue_received(self, symbols, frames, *, live=False):
        for symbol in symbols:
            for frame in frames:
                self.observations.needs.pop((symbol, frame), None)
        self.enqueue_analysis(symbols, frames, live=live, priority=True)

    def collect_work(self, work, sessions, schedules, weeks, now, target):
        start, end, stage, symbols, count = work
        live_after = self.state["started_at"]
        with self.owner.conn:
            self.owner.set_meta("target", target)
        if stage == "F":
            self.mark_task(stage, "CHECKING_FACTORS", symbols)
            factors, errors = self.collector.factor_responses(symbols)
            self.store.factor_attempt(symbols, now)
            self.flush_requests(stage)
            for offset in range(0, len(symbols), WRITE_BATCH):
                self.owner.accept_factors(symbols[offset:offset + WRITE_BATCH], factors, sessions=sessions,
                                          target=target, now=str(now_cn()), live_after=live_after,
                                          analyze_candidates=False, errors=errors)
                self.service_commands(sessions, schedules, weeks)
        else:
            self.mark_task(stage, "COLLECTING_NATIVE15", symbols)
            grid = schedules["15m"]
            index = bisect_left(grid, start) if count == 1 and start == end else 0
            previous = grid[index - 1] if index else None
            received, errors = self.collector.fetch(symbols, start, end, cold=stage == "C", count=count, previous=previous)
            self.store.attempt(symbols, end, now, stage)
            self.flush_requests(stage)
            for offset in range(0, len(symbols), WRITE_BATCH):
                self.owner.accept_batch(symbols[offset:offset + WRITE_BATCH], received, errors,
                                        sessions=sessions, start=start, end=end, now=str(now_cn()), factors={},
                                        stage=stage, live_after=live_after, analyze_candidates=False)
                self.service_commands(sessions, schedules, weeks)
        scope = self.active_scope()
        self.queue_received([symbol for symbol in symbols if symbol in scope],
                            TIMEFRAMES if stage == "F" else INTRADAY,
                            live=stage == "A" and start == end == target)

    def flush_requests(self, stage):
        if self.collector:
            rows = list(self.collector.observations)
            with self.owner.conn:
                self.owner.save_requests(rows, stage)
            for _ in rows:
                self.collector.observations.popleft()

    def restore_budgets(self, now):
        boundary = str(wall(now)-timedelta(seconds=WINDOW_SECONDS))
        clock = monotonic()
        for row in self.owner.conn.execute("SELECT at,capability,period,stage FROM native15_request_receipt WHERE at>? ORDER BY id", (boundary,)):
            age = max(0, (wall(now)-wall(row['at'])).total_seconds())
            if row['capability'] in ('adj_factor', 'kline.minute.batch', 'kline.daily.batch'):
                self.collector.budgets['minute'].sent.append(clock-age)
            if row['stage'] in ('C', 'W'):
                self.cold_budget.sent.append(clock-age)
        delay = self.owner.meta('quota_until',0)-time()
        if delay > 0:
            for budget in self.collector.budgets.values():
                budget.defer(delay)

    def close(self):
        self.stop.set()
        if self.thread.is_alive():
            self.thread.join(35)
