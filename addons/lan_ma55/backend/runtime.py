"""Single-owner scheduler for verified THS focus and completed TickFlow bars."""

import atexit
import os
from datetime import timedelta
from pathlib import Path
from threading import Event, Lock, Thread
from time import monotonic, time

import httpx

from .collector import Collector, SourceError
from .daily_store import DailyStore
from .exchange_sessions import frame_schedules, planned_session
from .dc_focus_owner import DCFocusOwner
from .market_grid import (
    CAPACITY,
    now_cn,
    wall,
)
from .multiframe_core.multiframe import VERSION
from .native15_owner import NativeOwner
from .native15_scheduler import task as native_task
from .native15_shadow import SOURCE as NATIVE_SOURCE
from .observations import Observations
from .request_budget import CollectionStopped, RequestDeferred, RollingBudget
from .result_publisher import Publisher, workbench_configs
from .runtime_status import observation_summary
from .workbench_bridge import WorkbenchBridge, focus_view
from .worker_lease import ProcessLease


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
            version="0.12.0", api_version=1, phase="STARTING", active_operation="IDLE",
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
                self.publisher.prepare(workbench_configs())
            except (httpx.HTTPError, ValueError):
                self.report(publisher_error="工作台扩展数据尚未就绪")
            else:
                self.bridge_prepared = True
                self.report(publisher_error=None)

    def publish(self, sessions, schedules, *, force=False):
        if not force and monotonic() - self.last_publication < 5:
            return
        scope = self.active_scope()
        master = self.store.universe(scope)
        snapshot = self.focus.snapshot
        current = {**snapshot, "members": {symbol: boards for symbol, boards in snapshot["members"].items()
                                           if symbol in scope}}
        rows = self.observations.rows(current, master, schedules)
        target = schedules["15m"][-1] if schedules["15m"] else None
        market_day = sessions[-1] if sessions else None
        summary = observation_summary(rows, target, market_day)
        view = dict(focus=focus_view(self.focus, market_day), rows=rows, summary=summary,
                    state={k: self.state.get(k) for k in ("phase", "error", "waiting_for",
                           "calendar", "active_operation", "publisher_error")}, rule_version=VERSION)
        with self.lock:
            self.view = view
        if self.bridge_prepared:
            try:
                self.bridge.publish(view)
                self.report(publisher_error=None)
            except (httpx.HTTPError, ValueError):
                self.report(publisher_error="工作台发布失败，保留上次结果")
        self.last_publication = monotonic()

    def update_metadata(self, scope, now, market_day):
        saved = self.store.meta("focus_metadata_checks", {})
        missing = [symbol for symbol in scope if saved.get(symbol) != market_day][:100]
        if missing:
            rows, _ = classify(self.collector.metadata(missing), now[:10])
            with self.owner.conn:
                self.store.put_metadata([row for row in rows if row["symbol"] in missing])
                self.store.set_meta("focus_metadata_checks", dict(saved, **dict.fromkeys(missing, market_day)))
        return self.store.universe(scope)

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
            last_input = None
            while not self.stop.is_set():
                now = str(now_cn())
                sessions = self.sessions(now)
                schedules, weeks = frame_schedules(sessions, now)
                work = None
                phase, stage = "WAITING_NEXT_15M", None
                try:
                    self.prepare_bridge()
                    market_day = sessions[-1] if sessions else None
                    if self.bridge_prepared and monotonic() - self.last_commands >= 2:
                        self.last_commands = monotonic()
                        try:
                            self.bridge.consume(market_day, sessions, schedules, weeks)
                        except (httpx.HTTPError, ValueError):
                            self.report(publisher_error="工作台请求或回执暂不可用")
                    scope = self.active_scope()
                    if not scope:
                        phase = "SCOPE_PAUSED"
                    elif not self.collector or self.market_rejected:
                        phase = "CREDENTIAL_REQUIRED"
                    elif self.host_busy():
                        phase = "HOST_BUSY"
                    else:
                        sessions = self.refresh_calendar(now)
                        schedules, weeks = frame_schedules(sessions, now)
                        target = schedules["15m"][-1] if schedules["15m"] else None
                        master = self.update_metadata(scope, now, sessions[-1]) if sessions else {}
                        input_key = (tuple(scope), tuple((values[-1] if values else None) for values in schedules.values()),
                                     self.store.meta("calendar_revision", 0))
                        if last_input != input_key:
                            self.observations.refresh(master, sessions, schedules, weeks, now, self.state["started_at"])
                            last_input = input_key
                        if target:
                            work = native_task(self.store, master, sessions, target, now)
                        daily = self.daily.task(master, schedules["daily"]) if not work or work[2] == "C" else None
                        if daily and (not daily[-1] or self.cold_budget.delay() == 0):
                            start, end, count, symbols, cold = daily
                            stage = "W" if cold else "D"
                            self.report(phase=stage, active_operation="COLLECTING_DAILY", active_symbols=len(symbols))
                            received, errors = self.collector.fetch_daily(symbols, start, end, count=count, cold=cold)
                            self.daily.accept(symbols, received, errors, schedules["daily"], master)
                            self.observations.refresh(symbols, sessions, schedules, weeks, str(now_cn()),
                                                      self.state["started_at"], live=not cold)
                        elif work and (work[2] != "C" or self.cold_budget.delay() == 0):
                            stage = work[2]
                            self.collect_work(work, sessions, schedules, weeks, now, target)
                        elif not target:
                            phase = "CALENDAR_PENDING"
                        elif work or daily:
                            phase = "QUOTA_WAIT"
                    self.report(phase=phase, active_operation="IDLE", backend_scanning=bool(scope and self.collector and not self.market_rejected),
                                work_stage=stage, error=None, waiting_for=None)
                    if not stage or not "09:30:00" <= now[11:19] < "15:00:00":
                        self.focus.schedule(sessions[-1] if sessions else None, sessions,
                                            self.collector.taxonomy_universe
                                            if self.collector and not self.market_rejected else None)
                except RequestDeferred as exc:
                    if exc.status_code == 429 and not (exc.reason or "").startswith("HOST_"):
                        with self.owner.conn:
                            self.owner.set_meta("quota_until", time()+max(0, exc.until-monotonic()))
                    phase = "HOST_STATUS_UNAVAILABLE" if (exc.reason or "").startswith("HOST_") else (
                        "RATE_LIMITED" if exc.status_code == 429 else "QUOTA_WAIT")
                    self.report(phase=phase, active_operation="IDLE", waiting_for=exc.reason)
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
                                active_operation="BLOCKED", error=str(exc))
                self.flush_requests(stage or "METADATA")
                self.publish(sessions, schedules, force=bool(stage))
                self.stop.wait(2)
        except CollectionStopped:
            pass
        except Exception as exc:
            self.report(phase="ERROR", active_operation="BLOCKED", error=f"{type(exc).__name__}: {exc}")
        finally:
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

    def collect_work(self, work, sessions, schedules, weeks, now, target):
        start, end, stage, symbols = work
        live_after = self.state["started_at"]
        with self.owner.conn:
            self.owner.set_meta("target", target)
        if stage == "F":
            self.report(phase=stage, active_operation="CHECKING_FACTORS", active_symbols=len(symbols))
            self.store.factor_attempt(symbols, now)
            factors = self.collector.factors(symbols)
            self.owner.accept_factors(symbols, factors, sessions=sessions, target=target,
                                      now=str(now_cn()), live_after=live_after, analyze_candidates=False)
        else:
            self.report(phase=stage, active_operation="COLLECTING_NATIVE15", active_symbols=len(symbols))
            self.store.attempt(symbols, end, now, stage)
            received, errors = self.collector.fetch(symbols, start, end, cold=stage == "C")
            self.owner.accept_batch(symbols, received, errors, sessions=sessions, start=start, end=end,
                                    now=str(now_cn()), factors={}, stage=stage, live_after=live_after,
                                    analyze_candidates=False)
        self.observations.refresh(symbols, sessions, schedules, weeks, str(now_cn()), live_after, live=stage == "A")

    def flush_requests(self, stage):
        if self.collector:
            rows = list(self.collector.observations)
            with self.owner.conn:
                self.owner.save_requests(rows, stage)
            for _ in rows:
                self.collector.observations.popleft()

    def restore_budgets(self, now):
        boundary = str(wall(now)-timedelta(seconds=60))
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
