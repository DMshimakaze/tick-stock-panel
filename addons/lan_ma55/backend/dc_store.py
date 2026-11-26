"""Versioned EastMoney classification inside the existing MA55 SQLite owner."""
import json

from .dc_taxonomy import DCStructureError, HIERARCHY, KINDS, derive_industry


class DCStore:
    def __init__(self, conn):
        self.conn = conn
        with conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS dc_generation_v1 (
                    run TEXT PRIMARY KEY, day TEXT NOT NULL, state TEXT NOT NULL,
                    scope TEXT NOT NULL, excluded TEXT NOT NULL, observed_ms INTEGER NOT NULL,
                    unclassified TEXT NOT NULL DEFAULT '[]'
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS dc_node_v1 (
                    run TEXT NOT NULL, key TEXT NOT NULL, kind TEXT NOT NULL, name TEXT NOT NULL,
                    level INTEGER, source_level TEXT, parent TEXT, observed_ms INTEGER,
                    raw_count INTEGER, scoped_count INTEGER, PRIMARY KEY(run,key)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS dc_membership_v1 (
                    run TEXT NOT NULL, board TEXT NOT NULL, symbol TEXT NOT NULL,
                    name TEXT NOT NULL, reason TEXT, in_scope INTEGER NOT NULL,
                    PRIMARY KEY(run,board,symbol)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS dc_membership_symbol_v1 "
                         "ON dc_membership_v1(run,symbol,in_scope,board)")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS dc_receipt_v1 (
                    id INTEGER PRIMARY KEY, run TEXT, day TEXT NOT NULL, payload TEXT NOT NULL
                )
            """)

    def generation(self, state):
        row = self.conn.execute(
            "SELECT * FROM dc_generation_v1 WHERE state=?", (state,)
        ).fetchone()
        if row is None:
            return None
        entry = dict(row)
        for field in ("scope", "excluded", "unclassified"):
            entry[field] = json.loads(entry[field])
        return entry

    def catalogs(self, run):
        return {row["kind"] for row in self.conn.execute(
            "SELECT DISTINCT kind FROM dc_node_v1 WHERE run=?", (run,)
        )}

    def begin(self, run, day, stockmaster, observed_ms):
        if self.generation("STAGING"):
            raise DCStructureError("DC_GENERATION_ALREADY_STAGING")
        scope = sorted(stockmaster["stocks"])
        if not scope or len(scope) != len(set(scope)):
            raise DCStructureError("DC_UNIVERSE_UNVERIFIED")
        with self.conn:
            self.conn.execute(
                "INSERT INTO dc_generation_v1(run,day,state,scope,excluded,observed_ms) "
                "VALUES (?,?,'STAGING',?,?,?)",
                (run, day, json.dumps(scope), json.dumps(stockmaster["excluded"]), observed_ms),
            )

    def accept_catalog(self, run, nodes):
        with self.conn:
            self.conn.executemany(
                "INSERT INTO dc_node_v1(run,key,kind,name,level,source_level) VALUES (?,?,?,?,?,?)",
                [(run, key, row["kind"], row["name"], row["level"], row["source_level"])
                 for key, row in nodes.items()],
            )

    def pending(self, run, kind, limit):
        return [row[0] for row in self.conn.execute(
            "SELECT key FROM dc_node_v1 WHERE run=? AND kind=? AND raw_count IS NULL ORDER BY key LIMIT ?",
            (run, kind, limit),
        )]

    def progress(self, run):
        total, ready = self.conn.execute(
            "SELECT count(*),count(raw_count) FROM dc_node_v1 WHERE run=?", (run,)
        ).fetchone()
        return dict(board_total=total, board_ready=ready)

    def accept_members(self, generation, members, observed_ms):
        run = generation["run"]
        scope = set(generation["scope"])
        with self.conn:
            for board, items in members.items():
                update = self.conn.execute(
                    "UPDATE dc_node_v1 SET observed_ms=?,raw_count=?,scoped_count=? "
                    "WHERE run=? AND key=? AND raw_count IS NULL",
                    (observed_ms, len(items), len(scope & items.keys()), run, board),
                )
                if update.rowcount != 1:
                    raise DCStructureError("DC_UNEXPECTED_MEMBERSHIP_WRITE")
                self.conn.executemany(
                    "INSERT INTO dc_membership_v1 VALUES (?,?,?,?,?,?)",
                    [(run, board, symbol, name, reason, int(symbol in scope))
                     for symbol, (name, reason) in items.items()],
                )

    def receipt(self, run, day, details):
        with self.conn:
            self.conn.execute(
                "INSERT INTO dc_receipt_v1(run,day,payload) VALUES (?,?,?)",
                (run, day, json.dumps(details, ensure_ascii=False)),
            )

    def outside(self, generation):
        known = set(generation["scope"]) | set(generation["excluded"])
        return {row[0] for row in self.conn.execute(
            "SELECT DISTINCT symbol FROM dc_membership_v1 WHERE run=? AND in_scope=0",
            (generation["run"],),
        )} - known

    def _delete_generation(self, run):
        self.conn.execute("DELETE FROM dc_membership_v1 WHERE run=?", (run,))
        self.conn.execute("DELETE FROM dc_node_v1 WHERE run=?", (run,))
        self.conn.execute("DELETE FROM dc_generation_v1 WHERE run=?", (run,))

    def abandon(self, run):
        with self.conn:
            self._delete_generation(run)

    def publish(self, generation):
        run = generation["run"]
        nodes = {row["key"]: dict(row) for row in self.conn.execute(
            "SELECT * FROM dc_node_v1 WHERE run=?", (run,)
        )}
        if {node["kind"] for node in nodes.values()} != set(KINDS) or any(
            node["raw_count"] is None for node in nodes.values()
        ):
            raise DCStructureError("DC_GENERATION_INCOMPLETE")
        memberships = {key: set() for key, node in nodes.items() if node["kind"] == "industry"}
        for board, symbol in self.conn.execute(
            "SELECT m.board,m.symbol FROM dc_membership_v1 m "
            "JOIN dc_node_v1 n ON n.run=m.run AND n.key=m.board "
            "WHERE m.run=? AND n.kind='industry'",
            (run,),
        ):
            memberships[board].add(symbol)
        parents, unclassified = derive_industry(nodes, memberships, generation["scope"])
        active = self.generation("ACTIVE")
        if active and generation["day"] < active["day"]:
            raise DCStructureError("DC_OLDER_GENERATION")
        with self.conn:
            previous = self.generation("PREVIOUS")
            if previous:
                self._delete_generation(previous["run"])
            if active:
                self.conn.execute("UPDATE dc_generation_v1 SET state='PREVIOUS' WHERE run=?", (active["run"],))
            self.conn.executemany("UPDATE dc_node_v1 SET parent=? WHERE run=? AND key=?",
                                  [(v, run, k) for k, v in parents.items()])
            update = self.conn.execute(
                "UPDATE dc_generation_v1 SET state='ACTIVE',unclassified=? WHERE run=? AND state='STAGING'",
                (json.dumps(unclassified), run),
            )
            if update.rowcount != 1:
                raise DCStructureError("DC_UNEXPECTED_GENERATION")

    def board_data(self, run):
        boards = {row["key"]: dict(row) for row in self.conn.execute(
            "SELECT key,kind,name,level,source_level,parent,observed_ms,"
            "raw_count,scoped_count AS count FROM dc_node_v1 WHERE run=? ORDER BY key", (run,)
        )}
        for board in boards.values():
            path = [board["name"]]
            cursor = board["parent"]
            while cursor:
                path.insert(0, boards[cursor]["name"])
                cursor = boards[cursor]["parent"]
            board["path"] = path if board["kind"] == "industry" else []
            board["hierarchy_status"] = HIERARCHY if board["kind"] == "industry" else None
        return boards

    def members(self, run, key, *, offset=0, limit=-1):
        return [dict(row) for row in self.conn.execute(
            "SELECT symbol,name,reason FROM dc_membership_v1 "
            "WHERE run=? AND board=? AND in_scope=1 ORDER BY symbol LIMIT ? OFFSET ?",
            (run, key, limit, offset),
        )]

    def raw_symbols(self, run, key):
        return {row[0] for row in self.conn.execute(
            "SELECT symbol FROM dc_membership_v1 WHERE run=? AND board=?", (run, key),
        )}

    def stock_memberships(self, run, symbol):
        return self.conn.execute(
            "SELECT board,name FROM dc_membership_v1 WHERE run=? AND symbol=? AND in_scope=1 ORDER BY board",
            (run, symbol),
        ).fetchall()
