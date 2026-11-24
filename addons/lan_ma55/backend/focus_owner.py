"""Authorized THS reads and focus edits within the existing SQLite owner."""

from datetime import datetime
from time import monotonic, time
from uuid import UUID

import httpx

from .focus_pool import KEY, SYMBOL, FocusPool, ScopeConflict, _union
from .market_grid import BEIJING, as_ms, now_cn, wall
from .request_budget import CollectionStopped, RollingBudget, retry_seconds
from .taxonomy_owner import TaxonomyMaintenance, due
from .ths_focus import CATALOG, MEMBERS, THSUnavailable
from .ths_taxonomy import REVERSE

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
        now = time()
        for row in owner.conn.execute(
            "SELECT at FROM native15_request_receipt WHERE capability LIKE 'ths.%' AND at>=? ORDER BY id",
            (str(datetime.fromtimestamp(now - 60, BEIJING).replace(tzinfo=None)),),
        ):
            elapsed = now - wall(row["at"]).replace(tzinfo=BEIJING).timestamp()
            self.budget.sent.append(monotonic() - max(0, elapsed))
        self.source_min_ms = 0
        self.last_receipt = None

    def defer(self, seconds):
        self.until = time() + seconds
        with self.owner.conn:
            self.owner.set_meta("ths_retry_until", self.until)

    def request(self, path, params):
        if self.stop.is_set():
            raise CollectionStopped()
        if not self.key:
            raise THSUnavailable("THS_KEY_REQUIRED")
        if self.auth_error:
            raise THSUnavailable(self.auth_error)
        if time() < self.until:
            raise THSUnavailable(self.owner.meta("ths_retry_reason", "THS_RATE_LIMITED"))
        if self.budget.delay() > 0:
            raise THSUnavailable("THS_LOCAL_BUDGET")
        self.budget.acquire(self.stop)
        began = monotonic()
        response = None
        code = source_ms = None
        try:
            response = self.http.get(path, params=params)
            status = response.status_code
            if status in (401, 403):
                self.auth_error = f"THS_HTTP_{status}"
                raise THSUnavailable(self.auth_error)
            if status == 429:
                self.defer(max(60, retry_seconds(header=response.headers.get("Retry-After")) + 1))
                with self.owner.conn:
                    self.owner.set_meta("ths_retry_reason", "THS_RATE_LIMITED")
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
            if code == 3002:
                self.defer(MANUAL_REFRESH_INTERVAL)
                with self.owner.conn:
                    self.owner.set_meta("ths_retry_reason", "THS_CODE_3002")
            if type(code) is not int or code != 0:
                raise THSUnavailable(f"THS_CODE_{code}" if type(code) is int else "THS_INVALID_RESPONSE")
            data = payload.get("data")
            source_ms = data.get("timestamp") if isinstance(data, dict) else None
            if not isinstance(data, dict) or (
                (path != REVERSE or source_ms is not None) and
                (type(source_ms) is not int or not max(1, self.source_min_ms) <= source_ms <= (time() + 300) * 1000)
            ):
                raise THSUnavailable("THS_SOURCE_TIME_UNVERIFIED")
            return payload
        except httpx.HTTPError:
            self.defer(30)
            with self.owner.conn:
                self.owner.set_meta("ths_retry_reason", "THS_UNREACHABLE")
            raise THSUnavailable("THS_UNREACHABLE") from None
        finally:
            observed = now_cn()
            receipt = dict(
                at=str(observed), endpoint=path,
                capability={CATALOG: "ths.catalog", REVERSE: "ths.reverse"}.get(path, "ths.members"),
                latency_ms=(monotonic() - began) * 1000,
                status=response.status_code if response is not None else None,
                symbol_count=0 if path == CATALOG else len(params["thscode"].split(",")), retries=0,
                quota_headers={key: value for key, value in response.headers.items()
                               if key.startswith(("x-ratelimit-", "ratelimit-")) or key == "retry-after"}
                if response is not None else {},
            )
            self.last_receipt = dict(receipt, code=code, source_ms=source_ms, observed_ms=as_ms(observed),
                                     target=params.get("tag") if path == CATALOG else params["thscode"])
            with self.owner.conn:
                self.owner.save_requests([receipt], "FOCUS")

    def close(self):
        self.http.close()


class FocusOwner:
    def __init__(self, owner, key, stop, limit=12, *, transport=None):
        self.owner = owner
        self.pool = FocusPool(owner.market.path)
        self.snapshot = self.pool.read()
        self.catalog = owner.meta("focus_catalog_v1", {})
        self.source = THSTransport(owner, key, stop, limit, transport=transport)
        self.taxonomy = TaxonomyMaintenance(owner, self.source)
        self.daily = owner.meta("ths_daily_focus_v1", {})
        self.sector_errors = owner.meta("focus_sector_errors", {})

    @property
    def run(self):
        return self.taxonomy.active["run"] if self.taxonomy.active else None

    def schedule(self, market_day, sessions, stockmaster):
        before = self.run
        self.taxonomy.step(market_day, sessions, stockmaster, self.refresh_board)
        self.catalog = self.taxonomy.catalog(market_day) or self.catalog
        if before != self.run:
            selected = {s["key"]: s for s in self.snapshot["sectors"]}
            fresh = self.taxonomy.store.snapshots(self.run, selected)
            snapshots = [s for key, s in fresh.items() if self.newer(s, selected[key])]
            if snapshots:
                self.apply(list(selected), snapshots)

    @staticmethod
    def newer(after, before):
        return (after["observed_ms"] >= before["observed_ms"] and
                (before["source_ms"] is None or after["source_ms"] is None or
                 after["source_ms"] >= before["source_ms"]))

    def refresh_board(self, catalog):
        if not self.run or not self.snapshot["sectors"]:
            return False
        day = catalog["checked_day"]
        checks = self.daily.get("checks", {}) if self.daily.get("day") == day else {}
        active = self.taxonomy.active
        board = next((s for s in self.snapshot["sectors"] if s["key"] in catalog["entries"] and
                      s["key"] in active["catalog"] and s["key"] not in checks), None)
        if not board:
            return False
        key = board["key"]
        stocks, excluded = self.taxonomy.store.universe(self.run)
        try:
            payload = self.source.request(MEMBERS, {"thscode": key.split(":")[1]})
            snapshot, detail = self.taxonomy.store.forward_snapshot(
                key, catalog["entries"][key], payload, self.source.last_receipt, stocks, excluded)
            snapshot["run"] = self.run
            if not self.newer(snapshot, board):
                raise ValueError("Older source membership")
        except THSUnavailable:
            raise
        except ValueError:
            checks[key] = None
            self.sector_errors[key] = "THS_MEMBER_UNVERIFIED"
        else:
            checks[key] = dict(snapshot=snapshot, receipt=detail)
            self.sector_errors.pop(key, None)
            self.apply([s["key"] for s in self.snapshot["sectors"]], [snapshot])
        self.daily = dict(day=day, checks=checks)
        with self.owner.conn:
            self.owner.set_meta("ths_daily_focus_v1", self.daily)
            self.owner.set_meta("focus_sector_errors", self.sector_errors)
        return True

    def apply(self, keys, snapshots, preferences=None):
        result = self.pool.apply(keys, snapshots, expected_revision=self.snapshot["revision"],
                                 preferences=preferences)
        self.snapshot = {k: result[k] for k in ("revision", "sectors", "members", "groups", "aliases")}
        return result

    def edit(self, action, expected, run, market_day, *, selection=None, preferences=None):
        revision = self.snapshot["revision"]
        if type(expected) is not int or expected != revision or run != self.run:
            return dict(ok=False, error="SCOPE_CONFLICT", revision=revision)
        if action == "refresh":
            self.taxonomy.request_refresh()
            return dict(ok=True, queued=True, revision=revision)
        if not isinstance(selection, list) or any(not isinstance(k, str) or not KEY.fullmatch(k) for k in selection):
            return dict(ok=False, error="INVALID_SELECTION", revision=revision)
        keys = list(dict.fromkeys(selection))
        selected = {s["key"]: s for s in self.snapshot["sectors"]}
        new = set(keys) - selected.keys()
        if new and not self.run:
            return dict(ok=False, error="THS_MAPPING_PENDING", revision=revision)
        for key in new:
            if not self.is_available(key, market_day):
                return dict(ok=False, error="THS_BOARD_UNAVAILABLE", board=key, revision=revision)
        fresh = self.mapping_snapshots(new, market_day) if new else {}
        revised = _union([fresh.get(key) or selected[key] for key in keys])
        wanted, before = set(revised), set(self.snapshot["members"])
        if new and len(wanted) == self.taxonomy.active["stock_total"]:
            return dict(ok=False, error="MARKET_WIDE_SELECTION", revision=revision)
        delta = dict(added=len(wanted-before), removed=len(before-wanted), retained=len(before & wanted))
        if action == "preview":
            return dict(ok=True, revision=revision, **delta)
        if action != "save":
            return dict(ok=False, error="INVALID_COMMAND", revision=revision)
        try:
            validated = self.preferences(preferences) if preferences is not None else None
            result = self.apply(keys, list(fresh.values()), validated)
        except ScopeConflict:
            self.snapshot = self.pool.read()
            return dict(ok=False, error="SCOPE_CONFLICT", revision=self.snapshot["revision"])
        except (AttributeError, KeyError, TypeError, ValueError):
            return dict(ok=False, error="INVALID_PREFERENCES", revision=revision)
        return dict(ok=True, revision=result["revision"], membership_changed=result["membership_changed"], **delta)

    def is_available(self, key, day):
        active = self.taxonomy.active
        if not active or not day or key not in active["boards"] or due(active, day):
            return False
        if self.catalog.get("checked_day") == day:
            return self.catalog["entries"].get(key) == active["catalog"][key]
        return active["market_day"] == day

    def mapping_snapshots(self, keys, market_day):
        snapshots = self.taxonomy.store.snapshots(self.run, keys)
        if self.daily.get("day") == market_day:
            for key in snapshots.keys() & self.daily["checks"].keys():
                check = self.daily["checks"][key]
                if check and check["snapshot"]["run"] == self.run:
                    snapshots[key] = check["snapshot"]
        return snapshots

    def preferences(self, values):
        active = self.taxonomy.active or {}
        known = set(active.get("catalog", {}))
        groups, aliases, ids, names = [], {}, set(), set()
        old_groups = {g["id"]: g for g in self.snapshot["groups"]}
        for group in values["groups"]:
            key = str(UUID(group["id"]))
            name = group["name"].strip()
            boards = list(dict.fromkeys(group["boards"]))
            existing = set(old_groups.get(key, {}).get("boards", []))
            if not name or len(name) > 80 or key in ids or name.casefold() in names or not boards:
                raise ValueError("Invalid custom group")
            if not set(boards) <= known | existing:
                raise ValueError("Unknown custom group board")
            groups.append(dict(id=key, name=name, boards=boards))
            ids.add(key)
            names.add(name.casefold())
        for key, texts in values["aliases"].items():
            if (key not in known and key not in self.snapshot["aliases"]) or not isinstance(texts, list):
                raise ValueError("Invalid alias board")
            entries = list(dict.fromkeys(s.strip() for s in texts))
            if any(not s or len(s) > 80 for s in entries):
                raise ValueError("Invalid alias")
            if entries:
                aliases[key] = entries
        return dict(groups=groups, aliases=aliases)

    def lookup(self, args, market_day):
        offset = args.get("offset", 0)
        if not self.run or self.run != args.get("run"):
            return dict(ok=False, error="SCOPE_CONFLICT")
        if type(offset) is not int or offset < 0:
            raise ValueError("Invalid membership offset")
        board, symbol = args.get("board"), args.get("symbol")
        if isinstance(board, str) and board in self.taxonomy.active["boards"]:
            snap = self.mapping_snapshots([board], market_day)[board]
            symbols = snap["symbols"]
            return dict(ok=True, board={k: v for k, v in snap.items() if k != "symbols"},
                        rows=symbols[offset:offset + 50], total=len(symbols), offset=offset)
        if isinstance(symbol, str) and SYMBOL.fullmatch(symbol):
            stocks, _ = self.taxonomy.store.universe(self.run)
            if symbol not in stocks:
                return dict(ok=False, error="THS_STOCK_OUTSIDE_UNIVERSE")
            boards = set(self.taxonomy.store.stock_boards(self.run, symbol))
            if self.daily.get("day") == market_day:
                for key, checked in self.daily["checks"].items():
                    if checked and checked["snapshot"]["run"] == self.run:
                        if symbol in checked["snapshot"]["symbols"]:
                            boards.add(key)
                        else:
                            boards.discard(key)
            boards = sorted(boards)
            return dict(ok=True, symbol=symbol, total=len(boards), offset=offset, rows=boards[offset:offset + 50])
        raise ValueError("Invalid membership lookup")
