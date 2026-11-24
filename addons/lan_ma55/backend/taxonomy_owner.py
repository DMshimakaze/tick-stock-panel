"""One bounded classification step per cycle of the existing MA55 owner."""

from datetime import date
from time import time
from uuid import uuid4

from .collector import SourceError
from .focus_pool import KINDS, decode_catalog
from .market_grid import as_ms
from .request_budget import RequestDeferred
from .ths_focus import CATALOG, MEMBERS, THSUnavailable
from .ths_taxonomy import REVERSE, TaxonomyStore

REFRESH_DAYS = 7
RETRY_SECONDS = 300


def due(active, day):
    checked = active and active.get("market_day")
    return not checked or (date.fromisoformat(day) - date.fromisoformat(checked)).days >= REFRESH_DAYS


class TaxonomyMaintenance:
    def __init__(self, owner, source):
        self.owner = owner
        self.source = source
        self.store = TaxonomyStore(owner.market.path)
        self.active = self.store.active(include_members=False)
        self.run = self.store.staging()
        self.progress = self.store.progress(self.run) if self.run else {}
        self.samples = self.store.capture_plan(self.run) if self.run else None
        self.daily = owner.meta("ths_daily_check_v1", {})
        self.state = owner.meta("ths_maintenance_v1", {})

    def record(self, values):
        with self.owner.conn:
            self.state = dict(self.state, **values)
            self.owner.set_meta("ths_maintenance_v1", self.state)

    def request_refresh(self):
        self.record(dict(queued=self.run is None, retry_at=0, error=None))

    def catalog(self, day):
        parts = self.daily.get("parts", {})
        if self.daily.get("day") != day or len(parts) != len(KINDS):
            return None
        return dict(entries={key: name for part in parts.values() for key, name in part["entries"].items()},
                    source_ms={k: p["source_ms"] for k, p in parts.items()},
                    observed_ms=max(p["receipt"]["observed_ms"] for p in parts.values()),
                    receipts={k: p["receipt"] for k, p in parts.items()}, checked_day=day)

    def check_catalog(self, day):
        parts = dict(self.daily.get("parts", {})) if self.daily.get("day") == day else {}
        kind = next(k for k in KINDS if k not in parts)
        payload = self.source.request(CATALOG, {"tag": kind})
        part = decode_catalog(kind, payload)
        if self.active and part["source_ms"] < self.active["source_ms"][kind]:
            raise THSUnavailable("THS_CATALOG_OLDER")
        parts[kind] = dict(part, receipt=self.source.last_receipt)
        self.daily = dict(day=day, parts=parts)
        with self.owner.conn:
            self.owner.set_meta("ths_daily_check_v1", self.daily)

    def begin(self, market_day, catalog, stockmaster):
        master = stockmaster()
        run = uuid4().hex
        self.store.begin(run, catalog["entries"], master.pop("stocks"), source_ms=catalog["source_ms"],
                         observed_ms=catalog["observed_ms"], catalog_receipts=catalog["receipts"],
                         market_day=market_day, **master)
        self.run = run
        self.progress = self.store.progress(run)
        self.samples = self.store.capture_plan(run)
        self.record(dict(queued=False))

    def build(self):
        progress = self.progress
        samples = self.samples
        if samples is not None and progress["pending_stocks"]:
            requested = progress["pending_stocks"][:100]
            payload = self.source.request(REVERSE, {"thscode": ",".join(requested)})
            try:
                self.store.accept_reverse(self.run, requested, payload, receipt=self.source.last_receipt)
            except ValueError as exc:
                # The raw group is incomplete or invalid. Use exact-board
                # responses for the whole inventory instead of inferring absences.
                self.store.direct_only(self.run, str(exc), receipt=self.source.last_receipt)
                self.samples = None
        else:
            pending = set(progress["pending_boards"])
            wanted = sorted(pending.intersection(samples)) if samples is not None else sorted(pending)
            if wanted:
                board = wanted[0]
                payload = self.source.request(MEMBERS, {"thscode": board.split(":")[1]})
                self.store.accept_forward(self.run, board, payload, receipt=self.source.last_receipt)
            elif samples is not None:
                self.store.reconcile(self.run)
                self.samples = None
            else:
                self.store.publish(self.run)
                self.active = self.store.active(include_members=False)
                self.run = None
                self.progress = {}
                return
        self.progress = self.store.progress(self.run)

    def step(self, market_day, sessions, stockmaster, refresh_focus):
        if not market_day or not self.source.key or self.source.auth_error:
            return
        if time() < max(self.state.get("retry_at", 0), self.source.until) or self.source.budget.delay() > 0:
            return
        self.source.source_min_ms = as_ms(next((d for d in reversed(sessions) if d < market_day), market_day))
        try:
            catalog = self.catalog(market_day)
            if catalog is None:
                self.check_catalog(market_day)
            elif refresh_focus(catalog):
                pass
            elif self.run:
                self.build()
            elif stockmaster and (self.state.get("queued") or due(self.active, market_day)):
                self.begin(market_day, catalog, stockmaster)
            else:
                return
            if self.state.get("error"):
                self.record(dict(error=None, detail=None, retry_at=0))
        except (THSUnavailable, SourceError, RequestDeferred, ValueError, KeyError, TypeError) as exc:
            if isinstance(exc, THSUnavailable):
                reason = exc.reason
            elif isinstance(exc, (SourceError, RequestDeferred)):
                reason = "THS_UNIVERSE_DEFERRED"
            else:
                reason = f"THS_MAPPING_UNVERIFIED_{type(exc).__name__}"
            detail = str(exc) if isinstance(exc, ValueError) and not isinstance(exc, THSUnavailable) else None
            retry_at = self.source.until if self.source.until > time() else time() + RETRY_SECONDS
            self.record(dict(error=reason, detail=detail, retry_at=retry_at))

    def status(self):
        active = self.active or {}
        return dict(run=active.get("run"), previous_run=active.get("previous_run"), staging_run=self.run,
                    complete=bool(active), stock_total=active.get("stock_total"), market_day=active.get("market_day"),
                    observed_ms=active.get("observed_ms"), catalog_delta=active.get("catalog_delta", {}),
                    progress={k: v for k, v in self.progress.items() if not k.startswith("pending_")},
                    queued=bool(self.state.get("queued")),
                    error=("THS_KEY_REQUIRED" if not self.source.key else self.source.auth_error or self.state.get("error")),
                    retry_at=max(self.state.get("retry_at", 0), self.source.until) or None,
                    hierarchy_status="UNVERIFIED")
