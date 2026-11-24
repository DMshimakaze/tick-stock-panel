"""Admission and bounded refresh work for the existing MA55 owner."""
from datetime import date
from time import time
from uuid import uuid4

from .collector import SourceError
from .dc_store import DCStore
from .dc_taxonomy import (
    DCStructureError, HIERARCHY, KINDS, catalog_request, decode_catalog,
    decode_members, member_request,
)
from .dc_transport import DCUnavailable
from .market_grid import as_ms, now_cn
from .request_budget import RequestDeferred

BATCH_SIZE = {"industry": 16, "concept": 10, "theme": 20}
RECOVERABLE_BATCH = {"DC_MEMBER_PAGE_LIMIT", "DC_MISSING_BATCH_GROUP"}


def source_day(sessions, now):
    today = str(now.date())
    choices = [session for session in sessions if session < today or (session == today and now.hour >= 21)]
    return choices[-1].replace("-", "") if choices else None


def iso_day(day):
    return f"{day[:4]}-{day[4:6]}-{day[6:]}" if day else None


class DCMaintenance:
    def __init__(self, owner, source):
        self.owner, self.source = owner, source
        self.store = DCStore(owner.conn)
        self.state = owner.meta("dc_maintenance_v1", {})
        self.daily = owner.meta("dc_daily_focus_v1", {})
        self.active = self.store.generation("ACTIVE")
        self.boards = self.store.board_data(self.active["run"]) if self.active else {}
        self.staging = self.store.generation("STAGING")
        self.target_day = None

    def record(self, **values):
        self.state.update(values)
        with self.owner.conn:
            self.owner.set_meta("dc_maintenance_v1", self.state)

    def record_daily(self, daily):
        self.daily = daily
        with self.owner.conn:
            self.owner.set_meta("dc_daily_focus_v1", daily)

    def request_refresh(self):
        self.record(queued=True, retry_at=0, blocked_day=None, failed_day=None, restarts=0)

    def request(self, run, day, call):
        try:
            return self.source.request(*call)
        finally:
            if self.source.last_receipt is not None:
                self.store.receipt(run, day, self.source.last_receipt)

    def begin(self, day, master):
        run = uuid4().hex
        self.store.begin(run, day, master, as_ms(now_cn()))
        self.staging = self.store.generation("STAGING")
        self.record(queued=False, widths=BATCH_SIZE.copy())

    def build(self, stockmaster):
        run, day = self.staging["run"], self.staging["day"]
        present = self.store.catalogs(run)
        for kind in KINDS:
            if kind not in present:
                data = self.request(run, day, catalog_request(kind, day))
                self.store.accept_catalog(run, decode_catalog(kind, day, data))
                return
        widths = self.state.get("widths", BATCH_SIZE)
        for kind in KINDS:
            keys = self.store.pending(run, kind, widths[kind])
            if not keys:
                continue
            call = member_request(kind, keys, day)
            data = self.request(run, day, call)
            try:
                members = decode_members(kind, keys, day, data, limit=call[1].get("limit"))
            except DCStructureError as exc:
                if len(keys) == 1 or str(exc) not in RECOVERABLE_BATCH:
                    raise
                self.record(widths={**widths, kind: len(keys) // 2}, batch_issue=str(exc))
                return
            self.store.accept_members(self.staging, members, self.source.last_receipt["observed_ms"])
            return
        if self.store.outside(self.staging):
            if stockmaster is None:
                raise DCUnavailable("DC_UNIVERSE_REQUIRED", time() + 60)
            verified = stockmaster()
            if set(verified["stocks"]) != set(self.staging["scope"]):
                raise DCStructureError("DC_UNIVERSE_CHANGED")
            self.record(source_only=len(self.store.outside(self.staging)))
        self.store.publish(self.staging)
        self.active = self.store.generation("ACTIVE")
        self.boards = self.store.board_data(run)
        self.staging = None
        self.record(queued=False, error=None, retry_at=0, blocked_day=None, restarts=0, batch_issue=None)

    def latest_catalog(self, day):
        if self.daily.get("day") != day:
            self.record_daily(dict(day=day, parts={}, checked={}, scope_checked=False))
        parts = self.daily["parts"]
        for kind in KINDS:
            if kind not in parts:
                data = self.request(self.active["run"], day, catalog_request(kind, day))
                nodes = decode_catalog(kind, day, data)
                self.record_daily({**self.daily, "parts": {**parts, kind: nodes}})
                return None
        return {key: node for group in parts.values() for key, node in group.items()}

    def review_day(self, day, stockmaster, selected):
        catalog = self.latest_catalog(day)
        if catalog is None:
            return
        own = {key: (node["name"], node["level"]) for key, node in self.boards.items()}
        incoming = {key: (node["name"], node["level"]) for key, node in catalog.items()}
        if own != incoming:
            self.record(queued=True)
            return
        if not self.daily["scope_checked"] and stockmaster is not None:
            master = stockmaster()
            if set(master["stocks"]) != set(self.active["scope"]):
                self.record(queued=True)
                return
            self.record_daily({**self.daily, "scope_checked": True})
        scope = self.active_scope()
        known = scope | set(self.active["excluded"])
        checks = self.daily["checked"]
        for key in selected:
            if key not in self.boards or key in checks:
                continue
            board = self.boards[key]
            call = member_request(board["kind"], [key], day)
            data = self.request(self.active["run"], day, call)
            members = decode_members(board["kind"], [key], day, data)[key]
            incoming = set(members)
            outside = incoming - known
            issue = None
            if board["kind"] == "industry" or outside:
                previous = self.store.raw_symbols(self.active["run"], key)
                if outside - previous:
                    issue = "DC_UNIVERSE_CHANGED"
                elif board["kind"] == "industry" and incoming != previous:
                    issue = "DC_INDUSTRY_AWAITING_TREE"
            if issue:
                self.record_daily({**self.daily, "checked": {**checks, key: {"error": issue}}})
                self.record(queued=True)
            else:
                today_members = {symbol: val for symbol, val in members.items() if symbol in scope}
                self.record_daily({**self.daily, "checked": {**checks, key: {
                    "observed_ms": self.source.last_receipt["observed_ms"], "day": day,
                    "run": self.active["run"], "rows": today_members,
                }}})
            return

    def active_scope(self):
        return set(self.active["scope"]) if self.active else set()

    def board_check(self, key):
        item = self.daily.get("checked", {}).get(key, {})
        return item if self.active and item.get("run") == self.active["run"] else {}

    def step(self, sessions, stockmaster, selected):
        day = self.target_day = source_day(sessions, now_cn())
        if not day or self.source.auth_error or time() < self.state.get("retry_at", 0):
            return
        if self.source.budget.delay() > 0 or self.state.get("blocked_day") == day:
            return
        try:
            if self.staging:
                self.build(stockmaster)
            elif stockmaster and (not self.active or self.state.get("queued") or (
                date.fromisoformat(iso_day(day)) - date.fromisoformat(iso_day(self.active["day"]))
            ).days >= 7):
                self.begin(day, stockmaster())
            elif self.active and self.active["day"] < day:
                self.review_day(day, stockmaster, selected)
            else:
                return
            if self.state.get("error"):
                self.record(error=None, retry_at=0)
        except DCUnavailable as exc:
            self.record(error=str(exc), retry_at=exc.retry_at or time() + 60)
        except (SourceError, RequestDeferred):
            self.record(error="DC_UNIVERSE_DEFERRED", retry_at=time() + 60)
        except DCStructureError as exc:
            failed_day = self.staging["day"] if self.staging else day
            if self.staging:
                self.store.abandon(self.staging["run"])
                self.staging = None
            restarts = self.state.get("restarts", 0) + 1 if self.state.get("failed_day") == failed_day else 1
            self.record(error=str(exc), retry_at=time() + 300, queued=restarts == 1,
                        failed_day=failed_day, restarts=restarts, blocked_day=day if restarts > 1 else None)

    def status(self):
        active = self.active or {}
        day = active.get("day")
        previous = self.store.generation("PREVIOUS")
        return dict(
            run=active.get("run"), previous_run=previous["run"] if previous else None,
            staging_run=self.staging["run"] if self.staging else None, complete=bool(active),
            stock_total=len(active.get("scope", [])), market_day=iso_day(day),
            observed_ms=active.get("observed_ms"),
            progress=self.store.progress(self.staging["run"]) if self.staging else {},
            queued=bool(self.state.get("queued")), error=self.source.auth_error or self.state.get("error"),
            retry_at=self.state.get("retry_at") or None, batch_issue=self.state.get("batch_issue"),
            stale=bool(day and self.target_day and day < self.target_day),
            unclassified_count=len(active.get("unclassified", [])),
            hierarchy_status=HIERARCHY if active else "UNVERIFIED",
        )
