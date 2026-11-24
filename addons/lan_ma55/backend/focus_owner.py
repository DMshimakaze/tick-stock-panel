"""Authorized THS reads and focus edits within the existing SQLite owner."""

from datetime import datetime
from time import monotonic, time

import httpx

from .focus_pool import FocusPool, KEY, ScopeConflict
from .market_grid import BEIJING, as_ms, now_cn, wall
from .request_budget import CollectionStopped, RollingBudget, retry_seconds
from .ths_focus import CATALOG, THSUnavailable, load_catalog, refresh_selection

THS_ORIGIN = "https://fuyao.aicubes.cn"
MANUAL_REFRESH_INTERVAL = 300


class THSTransport:
    def __init__(self, owner, key, stop, limit, *, transport=None):
        self.owner, self.stop, self.key = owner, stop, key
        if not 1 <= limit <= 60:
            raise ValueError("ths_requests_per_minute must be in 1..60")
        self.budget = RollingBudget(limit)
        self.http = httpx.Client(base_url=THS_ORIGIN, headers={"X-api-key": key} if key else {},
                                 timeout=12, transport=transport)
        self.auth_error = None
        self.until = owner.meta("ths_retry_until", 0)
        self.network_until = 0
        now = time()
        for row in owner.conn.execute(
            "SELECT at FROM native15_request_receipt WHERE capability LIKE 'ths.%' AND at>=? ORDER BY id",
            (str(datetime.fromtimestamp(now - 60, BEIJING).replace(tzinfo=None)),),
        ):
            elapsed = now - wall(row["at"]).replace(tzinfo=BEIJING).timestamp()
            self.budget.sent.append(monotonic() - max(0, elapsed))
        self.source_min_ms = 0

    def request(self, path, params):
        if self.stop.is_set():
            raise CollectionStopped()
        if not self.key:
            raise THSUnavailable("THS_KEY_REQUIRED")
        if self.auth_error:
            raise THSUnavailable(self.auth_error)
        if time() < self.until:
            raise THSUnavailable("THS_RATE_LIMITED")
        if time() < self.network_until:
            raise THSUnavailable("THS_UNREACHABLE")
        # End the current operation when the local account allowance is used.
        # A scope edit remains a draft until all newly selected boards verify.
        if self.budget.delay() > 0:
            raise THSUnavailable("THS_LOCAL_BUDGET")
        self.budget.acquire(self.stop)
        began = monotonic()
        response = None
        try:
            response = self.http.get(path, params=params)
            status = response.status_code
            if status in (401, 403):
                self.auth_error = f"THS_HTTP_{status}"
                raise THSUnavailable(self.auth_error)
            if status == 429:
                self.until = time() + max(60, retry_seconds(header=response.headers.get("Retry-After")) + 1)
                with self.owner.conn:
                    self.owner.set_meta("ths_retry_until", self.until)
                raise THSUnavailable("THS_RATE_LIMITED")
            if status != 200:
                raise THSUnavailable(f"THS_HTTP_{status}")
            try:
                payload = response.json()
            except ValueError:
                raise THSUnavailable("THS_INVALID_RESPONSE") from None
            code = payload.get("code") if isinstance(payload, dict) else None
            if code == 2001:
                self.auth_error = "THS_KEY_REJECTED"
                raise THSUnavailable(self.auth_error)
            if type(code) is not int or code != 0:
                raise THSUnavailable(f"THS_CODE_{code}" if type(code) is int else "THS_INVALID_RESPONSE")
            data = payload.get("data")
            source_ms = data.get("timestamp") if isinstance(data, dict) else None
            if type(source_ms) is not int or not self.source_min_ms <= source_ms <= (time() + 300) * 1000:
                raise THSUnavailable("THS_SOURCE_TIME_UNVERIFIED")
            return payload
        except httpx.HTTPError:
            self.network_until = time() + 30
            raise THSUnavailable("THS_UNREACHABLE") from None
        finally:
            with self.owner.conn:
                self.owner.save_requests([dict(
                    at=str(now_cn()), endpoint=path,
                    capability="ths.catalog" if path == CATALOG else "ths.members",
                    latency_ms=(monotonic() - began) * 1000,
                    status=response.status_code if response is not None else None,
                    symbol_count=0 if path == CATALOG else 1, retries=0,
                    quota_headers={key: value for key, value in response.headers.items()
                                   if key.startswith(("x-ratelimit-", "ratelimit-")) or key == "retry-after"}
                    if response is not None else {},
                )], "FOCUS")

    def close(self):
        self.http.close()


class FocusOwner:
    def __init__(self, owner, key, stop, limit=12, *, transport=None):
        self.owner = owner
        self.pool = FocusPool(owner.market.path)
        self.snapshot = self.pool.read()
        self.catalog = owner.meta("focus_catalog_v1", {})
        self.source = THSTransport(owner, key, stop, limit, transport=transport)
        self.error = None if key else "THS_KEY_REQUIRED"
        self.sector_errors = owner.meta("focus_sector_errors", {})

    def schedule(self, market_day, now, sessions, *, trading_today):
        # A first visit can show the directory before the user has saved a pool.
        if not market_day:
            return
        self.source.source_min_ms = as_ms(next(
            (d for d in reversed(sessions) if d < market_day), market_day
        ))
        if self.catalog.get("checked_day") != market_day and time() >= self.owner.meta("focus_catalog_retry", 0):
            with self.owner.conn:
                self.owner.set_meta("focus_catalog_retry", time() + MANUAL_REFRESH_INTERVAL)
            try:
                catalog = load_catalog(self.source.request)
                if any(catalog["source_ms"][k] < v for k, v in self.catalog.get("source_ms", {}).items()):
                    raise THSUnavailable("THS_CATALOG_OLDER")
                self.catalog = dict(catalog, checked_day=market_day, observed_ms=as_ms(now))
                with self.owner.conn:
                    self.owner.set_meta("focus_catalog_v1", self.catalog)
                self.error = None
            except ValueError as exc:
                self.error = exc.reason if isinstance(exc, THSUnavailable) else "THS_CATALOG_UNVERIFIED"
        if (trading_today and self.snapshot["sectors"] and self.catalog.get("checked_day") == market_day
                and time() >= self.owner.meta("focus_members_retry", 0)):
            entries = self.snapshot["sectors"]
            if any(wall(datetime.fromtimestamp(s["observed_ms"]/1000, BEIJING)).date().isoformat() != market_day
                   for s in entries):
                self.edit("refresh", [], self.snapshot["revision"], market_day)

    def edit(self, action, selection, expected, market_day):
        if type(expected) is not int or expected != self.snapshot["revision"]:
            return dict(ok=False, error="SCOPE_CONFLICT", revision=self.snapshot["revision"])
        selected = [s["key"] for s in self.snapshot["sectors"]]
        if action == "refresh":
            if time() < self.owner.meta("focus_members_retry", 0):
                return dict(ok=False, error="THS_REFRESH_COOLDOWN", revision=expected)
            with self.owner.conn:
                self.owner.set_meta("focus_members_retry", time() + MANUAL_REFRESH_INTERVAL)
            selection = selected
        elif not isinstance(selection, list) or any(not isinstance(k, str) or not KEY.fullmatch(k) for k in selection):
            return dict(ok=False, error="INVALID_SELECTION", revision=expected)
        if (action == "refresh" or set(selection) - set(selected)) and selection:
            if self.catalog.get("checked_day") != market_day:
                return dict(ok=False, error=self.error or "THS_CATALOG_PENDING", revision=expected)
        try:
            result = refresh_selection(
                self.pool, selection, self.catalog or {"entries": {}}, self.source.request,
                expected_revision=expected, observed_ms=int(time() * 1000),
                refresh_existing=action == "refresh",
            )
        except THSUnavailable as exc:
            return dict(ok=False, error=exc.reason, board=exc.board, revision=expected)
        except ScopeConflict:
            self.snapshot = self.pool.read()
            return dict(ok=False, error="SCOPE_CONFLICT", revision=self.snapshot["revision"])
        self.snapshot = {key: result[key] for key in ("revision", "sectors", "members")}
        self.sector_errors = result["sector_errors"]
        with self.owner.conn:
            self.owner.set_meta("focus_sector_errors", self.sector_errors)
        return dict(ok=True, **{key: result[key] for key in (
            "revision", "added", "removed", "retained", "membership_changed", "sector_errors")})
