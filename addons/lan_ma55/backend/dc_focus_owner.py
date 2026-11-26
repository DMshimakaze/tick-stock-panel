"""EastMoney focus edits within the single MA55 owner."""
from .dc_maintenance import DCMaintenance, iso_day
from .dc_taxonomy import DC_KEY
from .dc_transport import DCTransport
from .focus_pool import FocusPool, ScopeConflict, SYMBOL, _union, validate_preferences


class DCFocusOwner:
    def __init__(self, owner, token, endpoint, stop, limit=18, *, transport=None):
        self.source = DCTransport(token, endpoint, stop, limit, transport=transport)
        self.taxonomy = DCMaintenance(owner, self.source)
        self.pool = FocusPool(owner.market.path, dc=True)
        self.snapshot = self.pool.read()
        self.legacy = FocusPool.legacy(owner.market.path)

    @property
    def run(self):
        return self.taxonomy.active["run"] if self.taxonomy.active else None

    @property
    def migration_pending(self):
        return self.snapshot["revision"] == 0 and bool(
            self.legacy["sectors"] or self.legacy["groups"] or self.legacy["aliases"]
        )

    def schedule(self, market_day, sessions, stockmaster):
        selected = {row["key"]: row for row in self.snapshot["sectors"]}
        self.taxonomy.step(sessions, stockmaster, selected)
        fresh = []
        for key, before in selected.items():
            if not self.is_available(key, market_day):
                continue
            check = self.taxonomy.board_check(key)
            observed = check.get("observed_ms", self.taxonomy.boards[key]["observed_ms"])
            if before.get("run") == self.run and observed == before["observed_ms"]:
                continue
            after = self.board_snapshot(key)
            if after != before and after["observed_ms"] >= before["observed_ms"]:
                fresh.append(after)
        if fresh:
            self.apply(list(selected), fresh)

    def is_available(self, key, _day=None):
        return key in self.taxonomy.boards

    def board_snapshot(self, key):
        board = self.taxonomy.boards[key]
        checked = self.taxonomy.board_check(key)
        symbols = sorted(checked["rows"]) if checked else [
            row["symbol"] for row in self.taxonomy.store.members(self.run, key)
        ]
        return dict(
            key=key, name=board["name"], run=self.run, source_ms=None,
            data_day=iso_day(checked.get("day") or self.taxonomy.active["day"]),
            observed_ms=checked.get("observed_ms", board["observed_ms"]),
            symbols=symbols, proof="DC_MEMBER" if symbols else "DC_VERIFIED_EMPTY",
        )

    def apply(self, selection, snapshots, preferences=None):
        result = self.pool.apply(selection, snapshots, expected_revision=self.snapshot["revision"],
                                 preferences=preferences)
        self.snapshot = {key: result[key] for key in ("revision", "sectors", "members", "groups", "aliases")}
        return result

    def edit(self, action, expected, run, market_day, *, selection=None, preferences=None,
             migration=None, confirm_migration=False):
        revision = self.snapshot["revision"]
        if type(expected) is not int or expected != revision or run != self.run:
            return dict(ok=False, error="SCOPE_CONFLICT", revision=revision)
        if action == "refresh":
            self.taxonomy.request_refresh()
            return dict(ok=True, queued=True, revision=revision)
        if not isinstance(selection, list) or any(
            not isinstance(key, str) or not DC_KEY.fullmatch(key) for key in selection
        ):
            return dict(ok=False, error="INVALID_SELECTION", revision=revision)
        if not self.run and (selection or self.migration_pending):
            return dict(ok=False, error="DC_MAPPING_PENDING", revision=revision)
        keys = list(dict.fromkeys(selection))
        saved = {sector["key"]: sector for sector in self.snapshot["sectors"]}
        if set(keys) - self.taxonomy.boards.keys() - saved.keys():
            return dict(ok=False, error="DC_BOARD_UNAVAILABLE", revision=revision)
        fresh = {key: self.board_snapshot(key) for key in keys if key in self.taxonomy.boards}
        chosen = [fresh[key] if key in fresh else saved[key] for key in keys]
        wanted = set(_union(chosen))
        if self.run and set(keys) - saved.keys() and wanted >= self.taxonomy.active_scope():
            return dict(ok=False, error="MARKET_WIDE_SELECTION", revision=revision)
        before = set(self.legacy["members"] if self.migration_pending else self.snapshot["members"])
        delta = dict(added=len(wanted - before), removed=len(before - wanted),
                     retained=len(before & wanted), total=len(wanted))
        try:
            comparison = self.migration_comparison(migration or {}, fresh) if self.migration_pending else []
            validated = (validate_preferences(preferences, self.taxonomy.boards, self.snapshot)
                         if preferences is not None else None)
        except (AttributeError, KeyError, TypeError, ValueError):
            return dict(ok=False, error="INVALID_PREFERENCES", revision=revision)
        if action == "preview":
            return dict(ok=True, revision=revision, migration=comparison, **delta)
        if action != "save":
            return dict(ok=False, error="INVALID_COMMAND", revision=revision)
        if self.migration_pending and confirm_migration is not True:
            return dict(ok=False, error="DC_MIGRATION_CONFIRM", revision=revision)
        try:
            result = self.apply(keys, list(fresh.values()), validated)
        except ScopeConflict:
            self.snapshot = self.pool.read()
            return dict(ok=False, error="SCOPE_CONFLICT", revision=self.snapshot["revision"])
        return dict(ok=True, revision=result["revision"],
                    membership_changed=result["membership_changed"], **delta)

    def migration_comparison(self, choices, selected):
        old = {sector["key"]: sector for sector in self.legacy["sectors"]}
        if not isinstance(choices, dict) or not set(choices) <= old.keys() or not set(choices.values()) <= selected.keys():
            raise ValueError("Migration requires explicitly selected codes")
        comparisons = []
        for key, previous in old.items():
            replacement = selected.get(choices.get(key))
            before = set(previous["symbols"])
            after = set(replacement["symbols"]) if replacement else set()
            comparisons.append(dict(
                old_key=key, old_name=previous["name"], old_count=len(before),
                new_key=replacement["key"] if replacement else None,
                new_name=replacement["name"] if replacement else None,
                new_count=len(after), common=len(before & after), old_only=len(before - after),
                new_only=len(after - before),
            ))
        return comparisons

    def legacy_view(self):
        return dict(
            pending=self.migration_pending,
            sectors=[dict(key=s["key"], name=s["name"], count=len(s["symbols"])) for s in self.legacy["sectors"]],
            groups=self.legacy["groups"], aliases=self.legacy["aliases"],
        )

    def view(self, market_day):
        status = self.taxonomy.status()
        catalog = []
        errors = {}
        for key, board in self.taxonomy.boards.items():
            check = self.taxonomy.board_check(key)
            count = len(check["rows"]) if check else board["count"]
            data_day = iso_day(check["day"]) if check else status["market_day"]
            target_day = iso_day(self.taxonomy.target_day)
            stale = bool(target_day and data_day < target_day)
            state = "STALE" if stale else "VERIFIED_EMPTY" if count == 0 else "READY"
            catalog.append(dict(board, count=count, status=state, data_day=data_day,
                                available=True, aliases=self.snapshot["aliases"].get(key, [])))
            marked = self.taxonomy.daily.get("checked", {}).get(key, {}).get("error")
            if marked:
                errors[key] = marked
        universe = self.taxonomy.active_scope()
        selected = self.snapshot["members"].keys()
        catalog_error = status["error"]
        if selected - universe:
            catalog_error = catalog_error or "DC_SCOPE_OUTDATED"
        if universe and selected >= universe:
            catalog_error = "MARKET_WIDE_SELECTION"
        return dict(
            **self.snapshot, catalog=catalog, taxonomy=status, legacy=self.legacy_view(),
            catalog_checked_day=status["market_day"], catalog_stale=status["stale"],
            catalog_error=catalog_error, sector_errors=errors, market_day=market_day,
        )

    def lookup(self, args, _market_day=None):
        if not self.run or args.get("run") != self.run:
            return dict(ok=False, error="SCOPE_CONFLICT")
        offset = args.get("offset", 0)
        if type(offset) is not int or offset < 0:
            raise ValueError("Invalid membership offset")
        board, symbol = args.get("board"), args.get("symbol")
        if isinstance(board, str) and board in self.taxonomy.boards:
            check = self.taxonomy.board_check(board)
            meta = self.taxonomy.boards[board]
            if check:
                rows = [dict(symbol=code, name=check["rows"][code][0], reason=check["rows"][code][1])
                        for code in sorted(check["rows"])[offset:offset + 50]]
                total = len(check["rows"])
            else:
                rows = self.taxonomy.store.members(self.run, board, offset=offset, limit=50)
                total = meta["count"]
            return dict(ok=True, board=dict(meta, count=total, source_ms=None,
                        data_day=iso_day(check.get("day") or self.taxonomy.active["day"]),
                        observed_ms=check.get("observed_ms", meta["observed_ms"])),
                        rows=rows, total=total, offset=offset)
        if isinstance(symbol, str) and SYMBOL.fullmatch(symbol):
            if symbol not in self.taxonomy.active_scope():
                return dict(ok=False, error="DC_STOCK_OUTSIDE_UNIVERSE")
            memberships = self.taxonomy.store.stock_memberships(self.run, symbol)
            names = {board for board, _ in memberships}
            name = next((name for _, name in memberships if name), None)
            for key in self.taxonomy.daily.get("checked", {}):
                check = self.taxonomy.board_check(key)
                if check:
                    if symbol in check["rows"]:
                        names.add(key)
                    else:
                        names.discard(key)
            path = sorted((key for key in names if self.taxonomy.boards[key]["kind"] == "industry"),
                          key=lambda key: self.taxonomy.boards[key]["level"])
            other = sorted(names - set(path))
            keys = path + other
            return dict(ok=True, symbol=symbol, name=name, industry_path=[self.taxonomy.boards[key]["name"] for key in path],
                        total=len(keys), rows=keys[offset:offset + 50], offset=offset)
        raise ValueError("Invalid membership lookup")
