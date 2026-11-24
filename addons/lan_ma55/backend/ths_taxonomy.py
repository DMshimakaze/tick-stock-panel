"""Persistent THS index membership inventory, separate from MA55 focus.

Only the existing owner may write. A generation stages a fixed verified stock
universe and both catalog kinds. Partial refreshes never replace the published
generation. Reverse coverage is explicit, not proof of a parent/child hierarchy.
No HTTP, market data, credentials, or strategy execution in this module.
"""
import json
import sqlite3
from collections import Counter
from contextlib import closing

from .focus_pool import KEY, KINDS, SYMBOL, _canonical, decode_members

REVERSE = "/api/a-share-index/constituents/ths-index-list-by-stock"
SAMPLE_NAMES = {
    "消费电子", "半导体材料", "光刻胶", "光刻机", "消费电子零部件及组装", "品牌消费电子",
    "半导体", "半导体设备", "电子化学品", "银行", "人形机器人", "机器人概念",
    "消费电子概念", "东数西算(算力)", "算力租赁", "AIPC",
}


def decode_reverse(requested, catalog, payload, *, discrepancies=None):
    """Strictly decode one all-types reverse response, including empty groups."""
    expected = set(requested)
    if not expected or len(expected) > 100 or len(requested) != len(expected):
        raise ValueError("Reverse request must contain 1..100 unique stocks")
    for symbol in expected:
        _canonical(symbol, SYMBOL)
    if not isinstance(payload, dict) or type(payload.get("code")) is not int or payload["code"] != 0:
        raise ValueError("THS reverse request not successful")
    data = payload.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("item"), list):
        raise ValueError("THS response missing item list")
    # Documented reverse responses have no source timestamp. Do not invent one.
    source_ms = data.get("timestamp")
    if source_ms is not None and (type(source_ms) is not int or source_ms <= 0):
        raise ValueError("Invalid optional source timestamp")
    items = data["item"]
    groups = {}
    for row in items:
        if not isinstance(row, dict):
            raise ValueError("Invalid reverse group")
        symbol = _canonical(row.get("thscode"), SYMBOL)
        if symbol not in expected or symbol in groups:
            raise ValueError("Unexpected or duplicate reverse group")
        if not isinstance(row.get("index_list"), list):
            raise ValueError("Missing index_list")
        keys = set()
        for index in row["index_list"]:
            if not isinstance(index, dict):
                raise ValueError("Invalid index identity")
            kind = index.get("type")
            if kind in ("region", "tszs"):
                continue
            if kind not in KINDS:
                raise ValueError("Unknown index type")
            key = _canonical(f'{kind}:{index.get("thscode")}', KEY)
            name, listed = index.get("name"), catalog.get(key)
            if not isinstance(name, str) or not name.strip():
                raise ValueError("Missing reverse board name")
            if name != listed:
                if discrepancies is None:
                    raise ValueError("Reverse result differs from catalog")
                issue = ("UNKNOWN_ID" if listed is None else "DISPLAY_WHITESPACE"
                         if "".join(name.split()) == "".join(listed.split()) else "DIFFERENT_NAME")
                discrepancies.append(dict(key=key, symbol=symbol, kind=kind, name=name,
                                          catalog_name=listed, issue=issue))
                if issue != "DISPLAY_WHITESPACE":
                    continue
            keys.add(key)
        groups[symbol] = sorted(keys)
    if set(groups) != expected:
        raise ValueError("Incomplete reverse batch")
    return source_ms, groups


def _dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class TaxonomyStore:
    """Additive tables in lan.ma55 state.sqlite3; never modifies market tables."""

    def __init__(self, path):
        self.path = path
        with closing(self.connect()) as db, db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS ths_map_run_v1 (
                run TEXT PRIMARY KEY, catalog TEXT NOT NULL, universe TEXT NOT NULL,
                metadata TEXT NOT NULL, state TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS ths_map_reverse_v1 (
                run TEXT NOT NULL, symbol TEXT NOT NULL, keys_json TEXT NOT NULL,
                source_ms INTEGER, observed_ms INTEGER NOT NULL, PRIMARY KEY(run,symbol)
            );
            CREATE TABLE IF NOT EXISTS ths_map_board_v1 (
                run TEXT NOT NULL, board TEXT NOT NULL, symbols_json TEXT NOT NULL,
                source_ms INTEGER, observed_ms INTEGER NOT NULL,
                proof TEXT NOT NULL, PRIMARY KEY(run,board)
            );
            CREATE TABLE IF NOT EXISTS ths_map_active_v1 (
                id INTEGER PRIMARY KEY CHECK(id=1), run TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS ths_map_receipt_v1 (
                run TEXT NOT NULL, step TEXT NOT NULL, target TEXT NOT NULL,
                payload TEXT NOT NULL, PRIMARY KEY(run,step,target)
            );
            """)

    def connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def begin(self, run, catalog, universe, *, source_ms, observed_ms, market_day=None,
              universe_receipt=None, excluded=(), catalog_receipts=None):
        """Start or resume the exact same input generation; never reset it."""
        if not isinstance(run, str) or not run or not isinstance(catalog, dict):
            raise ValueError("Invalid generation")
        for key, name in catalog.items():
            _canonical(key, KEY)
            if not isinstance(name, str) or not name.strip():
                raise ValueError("Missing board name")
        if {key.split(":")[0] for key in catalog} != set(KINDS):
            raise ValueError("Both catalog kinds required")
        if (set(source_ms) != set(KINDS) or
                any(type(v) is not int or v <= 0 for v in [*source_ms.values(), observed_ms])):
            raise ValueError("Missing catalog receipt")
        stocks = sorted(set(_canonical(s, SYMBOL) for s in universe))
        excluded = sorted(set(_canonical(s, SYMBOL) for s in excluded))
        if not stocks or set(stocks).intersection(excluded):
            raise ValueError("Invalid stock universe")
        fixed = (_dump(catalog), _dump(stocks), _dump(dict(
            source_ms=source_ms, observed_ms=observed_ms, market_day=market_day,
            universe_receipt=universe_receipt, excluded=excluded,
            catalog_receipts=catalog_receipts or {})))
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT catalog,universe,metadata FROM ths_map_run_v1 WHERE run=?",
                             (run,)).fetchone()
            if row is not None:
                if row != fixed:
                    raise ValueError("Generation inputs changed")
            else:
                db.execute("INSERT INTO ths_map_run_v1 VALUES (?,?,?,?,'STAGING')",
                           (run, *fixed))
                for abandoned, in db.execute("SELECT run FROM ths_map_run_v1 WHERE state='STAGING' AND run<>?",
                                             (run,)).fetchall():
                    self._delete(db, abandoned)

    @staticmethod
    def _delete(db, run):
        for table in ("ths_map_reverse_v1", "ths_map_board_v1", "ths_map_receipt_v1", "ths_map_run_v1"):
            db.execute(f"DELETE FROM {table} WHERE run=?", (run,))

    @staticmethod
    def _run(db, run, *, staging=False):
        row = db.execute("SELECT catalog,universe,metadata,state FROM ths_map_run_v1 WHERE run=?",
                         (run,)).fetchone()
        if row is None or (staging and row[3] != "STAGING"):
            raise ValueError("Unknown or immutable generation")
        return json.loads(row[0]), set(json.loads(row[1])), json.loads(row[2])

    def progress(self, run):
        with closing(self.connect()) as db:
            catalog, stocks, _ = self._run(db, run)
            seen = {row[0] for row in db.execute(
                "SELECT symbol FROM ths_map_reverse_v1 WHERE run=?", (run,))}
            boards = {row[0] for row in db.execute(
                "SELECT board FROM ths_map_board_v1 WHERE run=?", (run,))}
            return dict(stock_total=len(stocks), stock_seen=len(seen),
                        board_total=len(catalog), board_ready=len(boards),
                        pending_stocks=sorted(stocks - seen), pending_boards=sorted(set(catalog) - boards))

    def staging(self):
        with closing(self.connect()) as db:
            row = db.execute("SELECT run FROM ths_map_run_v1 WHERE state='STAGING'").fetchone()
            return row[0] if row else None

    @staticmethod
    def _verified(receipt, capability, count):
        observed = receipt.get("observed_ms")
        if (receipt.get("status") != 200 or receipt.get("code") != 0 or
            receipt.get("capability") != capability or receipt.get("symbol_count") != count or
            type(observed) is not int or observed <= 0):
            raise ValueError("Missing successful request receipt")
        return observed

    @staticmethod
    def _receipt(db, run, step, target, payload):
        db.execute("INSERT INTO ths_map_receipt_v1 VALUES (?,?,?,?)", (run, step, target, _dump(payload)))

    def accept_reverse(self, run, requested, payload, *, receipt):
        observed_ms = self._verified(receipt, "ths.reverse", len(requested))
        if sorted(receipt.get("target", "").split(",")) != sorted(requested):
            raise ValueError("Reverse receipt belongs to another batch")
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            catalog, stocks, _ = self._run(db, run, staging=True)
            issues = []
            source, groups = decode_reverse(requested, catalog, payload, discrepancies=issues)
            if not set(groups) <= stocks:
                raise ValueError("Reverse stock outside fixed universe")
            if source != receipt.get("source_ms"):
                raise ValueError("Reverse source time differs from receipt")
            try:
                db.executemany("INSERT INTO ths_map_reverse_v1 VALUES (?,?,?,?,?)", [
                    (run, symbol, _dump(keys), source, observed_ms) for symbol, keys in groups.items()
                ])
            except sqlite3.IntegrityError:
                raise ValueError("Reverse batch was already recorded") from None
            self._receipt(db, run, "reverse", min(requested), dict(**receipt, stocks=sorted(requested), issues=issues))

    def reverse_candidate(self, run):
        """Return inverse memberships after complete stock coverage."""
        with closing(self.connect()) as db:
            catalog, stocks, _ = self._run(db, run)
            return self._candidate(db, run, catalog, stocks)

    @staticmethod
    def _candidate(db, run, catalog, stocks):
        rows = db.execute("SELECT symbol,keys_json,source_ms,observed_ms FROM ths_map_reverse_v1 WHERE run=?",
                          (run,)).fetchall()
        if {row[0] for row in rows} != stocks:
            raise ValueError("Reverse coverage incomplete")
        boards = {key: [] for key in catalog}
        empty = []
        for symbol, keys, _, _ in rows:
            identities = json.loads(keys)
            if not identities:
                empty.append(symbol)
            for key in identities:
                boards[key].append(symbol)
        times = [row[2] for row in rows if row[2] is not None]
        return dict(boards={key: sorted(v) for key, v in boards.items()}, empty=sorted(empty),
                    source_min_ms=min(times) if len(times) == len(rows) else None,
                    source_max_ms=max(times) if len(times) == len(rows) else None,
                    observed_min_ms=min(row[3] for row in rows), observed_max_ms=max(row[3] for row in rows))

    @staticmethod
    def forward_snapshot(board, name, payload, receipt, stocks, excluded):
        observed = TaxonomyStore._verified(receipt, "ths.members", 1)
        snapshot = decode_members(board, name, payload, observed, verified=True)
        if snapshot["source_ms"] != receipt.get("source_ms"):
            raise ValueError("Forward source time differs from receipt")
        outside = set(snapshot["symbols"]) - stocks
        if outside - set(excluded):
            raise ValueError(f"Unknown forward stocks for {board}: {', '.join(sorted(outside - set(excluded)))}")
        detail = dict(**receipt, raw_count=len(payload["data"]["item"]), distinct_count=len(snapshot["symbols"]),
                      outside_universe=sorted(outside))
        snapshot["symbols"] = sorted(set(snapshot["symbols"]) & stocks)
        if not snapshot["symbols"]:
            snapshot["proof"] = "DIRECT_EMPTY"
        return snapshot, detail

    def accept_forward(self, run, board, payload, *, receipt):
        """Use only a successful exact-board response bound to the fixed catalog."""
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            catalog, stocks, meta = self._run(db, run, staging=True)
            if board not in catalog:
                raise ValueError("Board outside fixed catalog")
            if receipt.get("target") != board.split(":")[1]:
                raise ValueError("Forward receipt belongs to another board")
            snap, detail = self.forward_snapshot(board, catalog[board], payload, receipt, stocks, meta["excluded"])
            self._receipt(db, run, "forward", board, detail)
            db.execute("INSERT INTO ths_map_board_v1 VALUES (?,?,?,?,?,?)", (
                run, board, _dump(snap["symbols"]), snap["source_ms"], snap["observed_ms"], snap["proof"]))

    @staticmethod
    def samples(catalog):
        selected = {key for key, name in catalog.items() if name in SAMPLE_NAMES}
        for kind in KINDS:
            keys = sorted(key for key in catalog if key.startswith(kind + ":"))
            if len(selected.intersection(keys)) < min(2, len(keys)):
                selected.update(keys[:2])
        return selected

    def capture_plan(self, run):
        with closing(self.connect()) as db:
            catalog, _, _ = self._run(db, run)
            if self._validation(db, run):
                return None
            return sorted(self.samples(catalog))

    @staticmethod
    def _validation(db, run):
        row = db.execute("SELECT payload FROM ths_map_receipt_v1 WHERE run=? AND step='validation'", (run,)).fetchone()
        return json.loads(row[0]) if row else None

    def reconcile(self, run):
        """Authorize the bulk projection once, after exact positive comparisons.

        Any divergent sample disqualifies its kind from extrapolation. Unknown
        reverse identities require direct evidence for the affected kind too.
        """
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            catalog, stocks, _ = self._run(db, run, staging=True)
            if self._validation(db, run):
                return
            samples = self.samples(catalog)
            forward = {key: (set(json.loads(symbols)), source, observed) for key, symbols, source, observed in
                       db.execute("SELECT board,symbols_json,source_ms,observed_ms FROM ths_map_board_v1 WHERE run=?", (run,))}
            if not samples <= forward.keys():
                raise ValueError("Missing forward sample receipts")
            candidate = self._candidate(db, run, catalog, stocks)
            issues = [issue for payload, in db.execute("SELECT payload FROM ths_map_receipt_v1 WHERE run=? AND step='reverse'",
                                                      (run,))
                      for issue in json.loads(payload)["issues"]]
            fallback = {i["kind"] for i in issues if i["issue"] == "UNKNOWN_ID"}
            direct_required = {i["key"] for i in issues if i["issue"] == "DIFFERENT_NAME"}
            comparisons = {}
            for key in sorted(forward):
                direct, source, observed = forward[key]
                reverse = set(candidate["boards"][key])
                comparisons[key] = dict(forward_count=len(direct), reverse_count=len(reverse),
                    forward_only=sorted(direct-reverse), reverse_only=sorted(reverse-direct),
                    source_ms=source, observed_ms=observed)
                if key in samples and direct != reverse:
                    fallback.add(key.split(":")[0])
            for kind in KINDS:
                if not any(candidate["boards"][key] for key in samples if key.startswith(kind + ":")):
                    fallback.add(kind)
            bulk = [(run, key, _dump(members), candidate["source_max_ms"], candidate["observed_max_ms"],
                     "BULK_REVERSE_CHECKED") for key, members in candidate["boards"].items()
                    if members and key not in forward and key not in direct_required
                    and key.split(":")[0] not in fallback]
            db.executemany("INSERT INTO ths_map_board_v1 VALUES (?,?,?,?,?,?)", bulk)
            self._receipt(db, run, "validation", "reverse-vs-forward", dict(
                samples=sorted(samples), comparisons=comparisons, fallback=sorted(fallback),
                reverse_empty=candidate["empty"], source_min_ms=candidate["source_min_ms"],
                source_max_ms=candidate["source_max_ms"], observed_min_ms=candidate["observed_min_ms"],
                observed_max_ms=candidate["observed_max_ms"], issues=dict(Counter(i["issue"] for i in issues)),
                bulk_board_count=len(bulk)))

    def direct_only(self, run, reason, *, receipt=None):
        with closing(self.connect()) as db, db:
            self._run(db, run, staging=True)
            self._receipt(db, run, "validation", "direct-only", dict(
                fallback=list(KINDS), bulk_board_count=0, reverse_failure=reason, failed_receipt=receipt))

    def publish(self, run):
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            catalog, _, meta = self._run(db, run, staging=True)
            boards = {row[0] for row in db.execute(
                "SELECT board FROM ths_map_board_v1 WHERE run=?", (run,))}
            if boards != set(catalog):
                raise ValueError("Board coverage incomplete")
            validation = self._validation(db, run)
            if not validation:
                raise ValueError("Missing forward reconciliation")
            old = db.execute("SELECT run FROM ths_map_active_v1 WHERE id=1").fetchone()
            if old:
                _, _, previous = self._run(db, old[0])
                if (meta["observed_ms"] < previous["observed_ms"] or any(
                        meta["source_ms"][kind] < previous["source_ms"][kind] for kind in KINDS)):
                    raise ValueError("Older generation")
                if db.execute("SELECT newer.board FROM ths_map_board_v1 newer JOIN ths_map_board_v1 prior "
                              "ON newer.board=prior.board WHERE newer.run=? AND prior.run=? AND "
                              "(newer.observed_ms < prior.observed_ms OR newer.source_ms < prior.source_ms) LIMIT 1",
                              (run, old[0])).fetchone():
                    raise ValueError("Older board evidence")
                db.execute("UPDATE ths_map_run_v1 SET state='PREVIOUS' WHERE run=?", (old[0],))
            for expired, in db.execute("SELECT run FROM ths_map_run_v1 WHERE state='PREVIOUS' AND run<>?",
                                       (old[0] if old else "",)).fetchall():
                self._delete(db, expired)
            db.execute("UPDATE ths_map_run_v1 SET state='PUBLISHED' WHERE run=?", (run,))
            db.execute("INSERT INTO ths_map_active_v1 VALUES (1,?) ON CONFLICT(id) "
                       "DO UPDATE SET run=excluded.run", (run,))

    def active(self, *, include_members=True):
        with closing(self.connect()) as db:
            db.execute("BEGIN")
            row = db.execute("SELECT run FROM ths_map_active_v1 WHERE id=1").fetchone()
            if not row:
                return None
            run = row[0]
            catalog, stocks, meta = self._run(db, run)
            boards = {}
            values = "symbols_json" if include_members else "json_array_length(symbols_json)"
            for key, members, source, observed, proof in db.execute(
                f"SELECT board,{values},source_ms,observed_ms,proof FROM ths_map_board_v1 WHERE run=?", (run,)
            ):
                boards[key] = dict(key=key, name=catalog[key], source_ms=source, observed_ms=observed, proof=proof)
                if include_members:
                    boards[key]["symbols"] = json.loads(members)
                else:
                    boards[key]["count"] = members
            previous = db.execute("SELECT run,catalog FROM ths_map_run_v1 WHERE state='PREVIOUS'").fetchone()
            delta = {}
            if previous:
                prior = json.loads(previous[1])
                delta = dict(added=sorted(catalog.keys()-prior.keys()), removed=sorted(prior.keys()-catalog.keys()),
                             renamed=sorted(key for key in catalog.keys() & prior.keys() if catalog[key] != prior[key]))
            return dict(run=run, catalog=catalog, boards=boards, stock_total=len(stocks), catalog_delta=delta,
                        previous_run=previous[0] if previous else None, hierarchy_status="UNVERIFIED",
                        source_ms=meta["source_ms"], catalog_observed_ms=meta["observed_ms"],
                        observed_ms=max(b["observed_ms"] for b in boards.values()), market_day=meta["market_day"])

    def snapshots(self, run, keys):
        with closing(self.connect()) as db:
            catalog, _, _ = self._run(db, run)
            result = {}
            for key in keys:
                row = db.execute("SELECT symbols_json,source_ms,observed_ms,proof FROM ths_map_board_v1 "
                                 "WHERE run=? AND board=?", (run, key)).fetchone()
                if row:
                    symbols, source, observed, proof = row
                    result[key] = dict(key=key, name=catalog[key], symbols=json.loads(symbols), source_ms=source,
                                       observed_ms=observed, proof=proof, run=run)
            return result

    def universe(self, run):
        with closing(self.connect()) as db:
            _, stocks, meta = self._run(db, run)
            return stocks, meta["excluded"]

    def stock_boards(self, run, symbol):
        with closing(self.connect()) as db:
            return [row[0] for row in db.execute("SELECT board FROM ths_map_board_v1 WHERE run=? AND "
                     "EXISTS(SELECT 1 FROM json_each(symbols_json) WHERE value=?) ORDER BY board", (run, symbol))]
