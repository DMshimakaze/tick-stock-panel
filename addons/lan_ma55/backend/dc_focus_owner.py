"""EastMoney focus edits within the single MA55 owner."""
from .dc_maintenance import DCMaintenance, iso_day
from .dc_taxonomy import DC_KEY
from .dc_transport import DCTransport
from .focus_pool import FocusPool, ScopeConflict, SYMBOL, _union, validate_preferences


def coverage(sectors, universe):
    scoped = [dict(row, symbols=[s for s in row["symbols"] if s in universe]) for row in sectors]
    return scoped, _union(scoped), sum(len(row["symbols"]) for row in scoped)


class DCFocusOwner:
    def __init__(self, owner, token, endpoint, stop, limit=18, *, transport=None):
        self.source = DCTransport(token, endpoint, stop, limit, transport=transport)
        self.taxonomy = DCMaintenance(owner, self.source)
        self.pool = FocusPool(owner.market.path, dc=True)
        self.snapshot = self.pool.read()
        self.legacy = FocusPool.legacy(owner.market.path)
        self._industries = None

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

    def industry_paths(self, symbols):
        signature = (self.run, frozenset(symbols))
        if self._industries and self._industries[0] == signature:
            return self._industries[1]
        paths = {}
        if self.run and symbols:
            boards = self.taxonomy.boards
            for symbol, keys in self.taxonomy.store.industry_memberships(self.run, symbols).items():
                leaves = []
                for leaf in sorted(keys):
                    node = boards[leaf]
                    parent = boards.get(node['parent'], {})
                    if node['level'] == 3 and node['parent'] in keys and parent.get('parent') in keys:
                        leaves.append(leaf)
                if leaves:
                    # The active generation certifies uniqueness. Expose ambiguity if that evidence disagrees.
                    paths[symbol] = leaves[0] if len(keys) == 3 and len(leaves) == 1 else leaves
        self._industries = (signature, paths)
        return paths

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
        self.snapshot = {key: result[key] for key in
                         ("revision", "config_revision", "sectors", "members", "groups", "aliases")}
        return result

    def edit(self, action, expected, run, market_day, *, selection=None, preferences=None,
             migration=None, confirm_migration=False, confirmation=None):
        revision = self.snapshot["revision"]
        config_revision = self.snapshot["config_revision"]
        if action == "refresh":
            self.taxonomy.request_refresh()
            return dict(ok=True, queued=True, revision=revision)
        if type(expected) is not int or expected != config_revision:
            return dict(ok=False, error="SCOPE_CONFLICT", revision=revision)
        if not isinstance(selection, list) or any(
            not isinstance(key, str) or not DC_KEY.fullmatch(key) for key in selection
        ):
            return dict(ok=False, error="INVALID_SELECTION", revision=revision)
        if not self.run and (selection or self.migration_pending):
            return dict(ok=False, error="DC_MAPPING_PENDING", revision=revision)
        keys = list(dict.fromkeys(selection))
        saved = {sector["key"]: sector for sector in self.snapshot["sectors"]}
        unavailable = [key for key in keys if key not in self.taxonomy.boards and key not in saved]
        if unavailable:
            return dict(ok=False, error="DC_BOARD_UNAVAILABLE", revision=revision, unavailable=unavailable)
        fresh = {key: self.board_snapshot(key) for key in keys if key in self.taxonomy.boards}
        fresh = {key: row for key, row in fresh.items()
                 if key not in saved or row["observed_ms"] >= saved[key]["observed_ms"]}
        chosen = [fresh[key] if key in fresh else saved[key] for key in keys]
        scope = self.taxonomy.active_scope()
        _, members, total_memberships = coverage(chosen, scope)
        wanted = set(members)
        if scope and wanted >= scope:
            return dict(ok=False, error="MARKET_WIDE_SELECTION", revision=revision)
        actual = set(self.snapshot["members"]) & scope
        before = set(self.legacy["members"]) if self.migration_pending else actual if actual < scope else set()
        added, removed, retained = wanted - before, before - wanted, before & wanted
        delta = dict(added=len(added), removed=len(removed), retained=len(retained), total=len(wanted),
                     total_memberships=total_memberships, unique_symbols=len(wanted))
        try:
            comparison = self.migration_comparison(migration or {}, fresh) if self.migration_pending else []
            validated = (validate_preferences(preferences, self.taxonomy.boards, self.snapshot)
                         if preferences is not None else None)
        except (AttributeError, KeyError, TypeError, ValueError):
            return dict(ok=False, error="INVALID_PREFERENCES", revision=revision)
        plan = (config_revision, keys, validated, migration or {}, added, removed, retained, total_memberships)
        preview = dict(ok=True, revision=revision, migration=comparison, **delta)
        if action == "estimate":
            return dict(preview, preview_changed=confirmation is not None and confirmation != plan)
        if action == "preview":
            return dict(preview, _confirmation=plan)
        if action != "save":
            return dict(ok=False, error="INVALID_COMMAND", revision=revision)
        if self.migration_pending and confirm_migration is not True:
            return dict(ok=False, error="DC_MIGRATION_CONFIRM", revision=revision)
        if confirmation != plan:
            return dict(ok=False, error="FOCUS_PREVIEW_CHANGED", revision=revision,
                        preview=preview, _confirmation=plan)
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
        target_day = iso_day(self.taxonomy.target_day)
        for key, board in self.taxonomy.boards.items():
            check = self.taxonomy.board_check(key)
            count = len(check["rows"]) if check else board["count"]
            data_day = iso_day(check["day"]) if check else status["market_day"]
            marked = self.taxonomy.board_issue(key)
            state = ("REVIEW_FAILED" if marked else "VERIFIED_EMPTY" if count == 0 else
                     "REVIEW_PENDING" if target_day and data_day < target_day else "READY")
            catalog.append(dict(board, count=count, status=state, data_day=data_day, target_day=target_day,
                                observed_ms=check.get("observed_ms", board["observed_ms"]),
                                available=True, aliases=self.snapshot["aliases"].get(key, [])))
            if marked:
                errors[key] = marked
        universe = self.taxonomy.active_scope()
        sectors, members, total = coverage(self.snapshot["sectors"], universe)
        selected = self.snapshot["members"].keys()
        catalog_error = status["error"]
        if selected - universe:
            catalog_error = catalog_error or "DC_SCOPE_OUTDATED"
        if universe and members.keys() >= universe:
            catalog_error = "MARKET_WIDE_SELECTION"
        sectors = [dict(row, **({} if row["key"] in self.taxonomy.boards else
                        dict(removed=True, available=False, status="BOARD_REMOVED"))) for row in sectors]
        return dict(
            self.snapshot, sectors=sectors, members=members if members.keys() < universe else {},
            total_memberships=total, unique_symbols=len(members),
            catalog=catalog, taxonomy=status, legacy=self.legacy_view(),
            catalog_checked_day=status["market_day"], catalog_stale=status["stale"],
            catalog_error=catalog_error, sector_errors=errors, market_day=market_day,
        )

    def lookup(self, args, _market_day=None):
        if not self.run:
            return dict(ok=False, error="DC_MAPPING_PENDING")
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
                        target_day=iso_day(self.taxonomy.target_day),
                        review_error=self.taxonomy.board_issue(board),
                        observed_ms=check.get("observed_ms", meta["observed_ms"])),
                        rows=rows, total=total, offset=offset)
        if isinstance(symbol, str) and SYMBOL.fullmatch(symbol):
            if symbol not in self.taxonomy.active_scope():
                return dict(ok=False, error="DC_STOCK_OUTSIDE_UNIVERSE")
            memberships = self.taxonomy.store.stock_memberships(self.run, symbol)
            names = {board for board, _ in memberships}
            name = next((name for _, name in memberships if name), None)
            for key in self.taxonomy.boards.keys() & self.taxonomy.verified_boards():
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
