"""Persistent THS index membership inventory, separate from MA55 focus.

Only the existing owner may write. A generation stages a fixed verified stock
universe and both catalog kinds. Partial refreshes never replace the published
generation. Reverse coverage is explicit, not proof of a parent/child hierarchy.
No HTTP, market data, credentials, or strategy execution in this module.
"""
import json
import sqlite3
from contextlib import closing

from .focus_pool import KEY, KINDS, SYMBOL, _canonical

REVERSE = "/api/a-share-index/constituents/ths-index-list-by-stock"


def decode_reverse(requested, catalog, payload):
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
            if key not in catalog or catalog[key] != index.get("name"):
                raise ValueError("Reverse result differs from catalog")
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
            """)

    def connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def begin(self, run, catalog, universe, *, source_ms, observed_ms):
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
        if not stocks:
            raise ValueError("Empty stock universe")
        fixed = (_dump(catalog), _dump(stocks), _dump(dict(
            source_ms=source_ms, observed_ms=observed_ms)))
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

    def accept_reverse(self, run, requested, payload, *, observed_ms):
        if type(observed_ms) is not int or observed_ms <= 0:
            raise ValueError("Missing reverse observation time")
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            catalog, stocks, _ = self._run(db, run, staging=True)
            source, groups = decode_reverse(requested, catalog, payload)
            if not set(groups) <= stocks:
                raise ValueError("Reverse stock outside fixed universe")
            for symbol, keys in groups.items():
                prior = db.execute(
                    "SELECT keys_json,source_ms,observed_ms FROM ths_map_reverse_v1 WHERE run=? AND symbol=?",
                    (run, symbol)).fetchone()
                value = (_dump(keys), source, observed_ms)
                if prior is not None and prior != value:
                    raise ValueError("Conflicting reverse retry; start new generation")
                db.execute("INSERT OR IGNORE INTO ths_map_reverse_v1 VALUES (?,?,?,?,?)",
                           (run, symbol, *value))

    def reverse_candidate(self, run):
        """Return all inverse memberships only after every stock has a receipt.

        Empty boards are candidates, not a verified absence. Caller compares
        direct memberships, tests coverage, and obtains explicit proof before
        accepting board rows. Source timestamp bounds are observation bounds,
        not a common market effective time.
        """
        with closing(self.connect()) as db:
            catalog, stocks, _ = self._run(db, run)
            rows = db.execute("SELECT symbol,keys_json,source_ms,observed_ms FROM ths_map_reverse_v1 WHERE run=?",
                              (run,)).fetchall()
            if {row[0] for row in rows} != stocks:
                raise ValueError("Reverse coverage incomplete")
            boards = {key: [] for key in catalog}
            for symbol, keys, _, _ in rows:
                for key in json.loads(keys):
                    boards[key].append(symbol)
            source_times = [row[2] for row in rows if row[2] is not None]
            full_source_times = len(source_times) == len(rows)
            return dict(boards={key: sorted(v) for key, v in boards.items()},
                        source_min_ms=min(source_times) if full_source_times else None,
                        source_max_ms=max(source_times) if full_source_times else None,
                        observed_min_ms=min(row[3] for row in rows),
                        observed_max_ms=max(row[3] for row in rows))

    def accept_board(self, run, board, symbols, *, source_ms, observed_ms, proof):
        """Stage verified membership; direct empty response is explicitly marked.

        BULK_REVERSE_CHECKED is allowed only after caller's entitlement, full
        reverse coverage and forward reconciliation checks. It is NOT a claim
        that every board had a separate direct query.
        """
        if proof not in ("DIRECT", "DIRECT_EMPTY", "BULK_REVERSE_CHECKED"):
            raise ValueError("Missing membership proof")
        if type(observed_ms) is not int or observed_ms <= 0:
            raise ValueError("Missing membership observation time")
        if source_ms is None:
            if proof != "BULK_REVERSE_CHECKED":
                raise ValueError("Direct membership needs source timestamp")
        elif type(source_ms) is not int or source_ms <= 0:
            raise ValueError("Invalid membership source time")
        members = sorted(set(_canonical(s, SYMBOL) for s in symbols))
        if (not members) != (proof == "DIRECT_EMPTY"):
            raise ValueError("Empty membership requires direct empty proof")
        if proof == "BULK_REVERSE_CHECKED":
            candidate = self.reverse_candidate(run)
            if (candidate["boards"].get(board) != members or
                    source_ms != candidate["source_max_ms"] or
                    observed_ms < candidate["observed_max_ms"]):
                raise ValueError("Membership differs from complete reverse candidate")
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            catalog, stocks, _ = self._run(db, run, staging=True)
            if board not in catalog or not set(members) <= stocks:
                raise ValueError("Unknown board or stock; reconcile universe")
            prior = db.execute("SELECT symbols_json,source_ms,observed_ms,proof FROM ths_map_board_v1 "
                               "WHERE run=? AND board=?", (run, board)).fetchone()
            value = (_dump(members), source_ms, observed_ms, proof)
            if prior is not None and prior != value:
                raise ValueError("Conflicting staged board; start new generation")
            db.execute("INSERT OR IGNORE INTO ths_map_board_v1 VALUES (?,?,?,?,?,?)",
                       (run, board, *value))

    def publish(self, run):
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            catalog, _, meta = self._run(db, run, staging=True)
            boards = {row[0] for row in db.execute(
                "SELECT board FROM ths_map_board_v1 WHERE run=?", (run,))}
            if boards != set(catalog):
                raise ValueError("Board coverage incomplete")
            old = db.execute("SELECT run FROM ths_map_active_v1 WHERE id=1").fetchone()
            if old:
                _, _, previous = self._run(db, old[0])
                if (meta["observed_ms"] < previous["observed_ms"] or any(
                        meta["source_ms"][kind] < previous["source_ms"][kind] for kind in KINDS)):
                    raise ValueError("Older generation")
            db.execute("UPDATE ths_map_run_v1 SET state='PUBLISHED' WHERE run=?", (run,))
            db.execute("INSERT INTO ths_map_active_v1 VALUES (1,?) ON CONFLICT(id) "
                       "DO UPDATE SET run=excluded.run", (run,))

    def active(self):
        with closing(self.connect()) as db:
            db.execute("BEGIN")
            row = db.execute("SELECT run FROM ths_map_active_v1 WHERE id=1").fetchone()
            if not row:
                return None
            run = row[0]
            catalog, stocks, meta = self._run(db, run)
            boards = {}
            for key, symbols, source, observed, proof in db.execute(
                    "SELECT board,symbols_json,source_ms,observed_ms,proof FROM ths_map_board_v1 WHERE run=?",
                    (run,)):
                boards[key] = dict(key=key, name=catalog[key], symbols=json.loads(symbols),
                                   source_ms=source, observed_ms=observed, proof=proof)
            return dict(run=run, catalog=catalog, boards=boards, stock_total=len(stocks),
                        hierarchy_status="UNVERIFIED", **meta)
