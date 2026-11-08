"""Plugin-owned SQLite only: one process-local writer, bounded transactions, RO lists."""

from contextlib import closing, nullcontext
import json
from pathlib import Path
import sqlite3
from threading import Lock

from .pipeline import SCHEMA_VERSION, serializable

_writers = set()
_writers_lock = Lock()

DDL = """
CREATE TABLE IF NOT EXISTS symbol_state (
 symbol TEXT PRIMARY KEY, rule_version TEXT NOT NULL, schema_version INTEGER NOT NULL,
 processed_at TEXT, data_asof TEXT, payload TEXT NOT NULL, updated_at TEXT NOT NULL,
 last_cycle TEXT NOT NULL, category TEXT NOT NULL, phase TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS state_cycle ON symbol_state(last_cycle, category);
CREATE TABLE IF NOT EXISTS signal_event (
 event_id TEXT PRIMARY KEY, symbol TEXT NOT NULL, rule_version TEXT NOT NULL,
 setup_at TEXT NOT NULL, confirmed_at TEXT NOT NULL, first_seen_at TEXT NOT NULL,
 source_asof TEXT NOT NULL, pattern TEXT NOT NULL, origin TEXT NOT NULL,
 payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS event_time ON signal_event(confirmed_at DESC, event_id);
CREATE INDEX IF NOT EXISTS event_symbol ON signal_event(symbol, rule_version);
CREATE TABLE IF NOT EXISTS event_risk (
 event_id TEXT PRIMARY KEY REFERENCES signal_event(event_id) ON DELETE CASCADE,
 payload TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS scan_cycle (
 cycle_id TEXT PRIMARY KEY, trade_date TEXT NOT NULL, bar_end TEXT NOT NULL,
 mode TEXT NOT NULL, expected_symbols INTEGER NOT NULL, processed_symbols INTEGER NOT NULL,
 valid_symbols INTEGER NOT NULL, insufficient_symbols INTEGER NOT NULL,
 gap_symbols INTEGER NOT NULL, failed_symbols INTEGER NOT NULL, new_signals INTEGER NOT NULL,
 source_asof TEXT, elapsed_ms REAL NOT NULL, status TEXT NOT NULL,
 source_health TEXT NOT NULL, updated_at TEXT NOT NULL, completed_at TEXT, error TEXT
);
"""


def encoded(value):
    return json.dumps(
        serializable(value), ensure_ascii=False, allow_nan=False, separators=(",", ":")
    )


def read_connection(path):
    conn = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def read_page(
    path,
    kind,
    *,
    limit=50,
    offset=0,
    symbol=None,
    day=None,
    phase=None,
    rule_version=None,
):
    if (
        kind not in ("events", "symbols", "cycles")
        or not 1 <= limit <= 200
        or offset < 0
    ):
        raise ValueError("Invalid list or pagination")
    table = {
        "events": "signal_event",
        "symbols": "symbol_state",
        "cycles": "scan_cycle",
    }[kind]
    where, values = [], []
    if kind != "cycles" and rule_version:
        where.append("rule_version=?")
        values.append(rule_version)
    if symbol and kind != "cycles":
        where.append("symbol=?")
        values.append(symbol)
    if day:
        field = (
            "confirmed_at"
            if kind == "events"
            else "data_asof"
            if kind == "symbols"
            else "trade_date"
        )
        where.append(f"substr({field},1,10)=?")
        values.append(day)
    if phase and kind == "symbols":
        where.append("phase=?")
        values.append(phase)
    clause = " WHERE " + " AND ".join(where) if where else ""
    order = (
        "confirmed_at DESC,event_id"
        if kind == "events"
        else "symbol"
        if kind == "symbols"
        else "bar_end DESC,cycle_id"
    )
    with closing(read_connection(path)) as conn:
        total = conn.execute(
            f"SELECT count(*) FROM {table}{clause}", values
        ).fetchone()[0]
        rows = [
            dict(r)
            for r in conn.execute(
                f"SELECT * FROM {table}{clause} ORDER BY {order} LIMIT ? OFFSET ?",
                [*values, limit, offset],
            )
        ]
        for row in rows:
            if "payload" in row:
                row["payload"] = json.loads(row["payload"])
            if kind == "events":
                risk = conn.execute(
                    "SELECT payload FROM event_risk WHERE event_id=?",
                    (row["event_id"],),
                ).fetchone()
                row["risk"] = json.loads(risk[0]) if risk else None
    return dict(total=total, rows=rows, limit=limit, offset=offset)


class StateWriter:
    """Only the owning service thread may use this connection (sqlite default)."""

    def __init__(self, path):
        self.path = Path(path).resolve()
        with _writers_lock:
            if self.path in _writers:
                raise RuntimeError("MA55 already has a writer for this database")
            _writers.add(self.path)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.conn = sqlite3.connect(self.path)
            self.conn.row_factory = sqlite3.Row
            self.conn.execute("PRAGMA foreign_keys=ON")
            version = self.conn.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, SCHEMA_VERSION):
                raise ValueError(
                    "Unsupported MA55 state schema; preserve the existing database"
                )
            self.conn.executescript(DDL)
            self.conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            self.conn.commit()
        except Exception:
            if hasattr(self, "conn"):
                self.conn.close()
            with _writers_lock:
                _writers.remove(self.path)
            raise

    def close(self):
        self.conn.close()
        with _writers_lock:
            _writers.remove(self.path)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def cycle(self, cycle_id):
        row = self.conn.execute(
            "SELECT * FROM scan_cycle WHERE cycle_id=?", (cycle_id,)
        ).fetchone()
        return dict(row) if row else None

    def records(self, symbols):
        if len(symbols) > 128:
            raise ValueError("A state transaction is limited to 128 symbols")
        marks = ",".join("?" for _ in symbols)
        result, completed = {}, set()
        for row in self.conn.execute(
            f"SELECT * FROM symbol_state WHERE symbol IN ({marks})", symbols
        ):
            result[row["symbol"]] = dict(
                symbol=row["symbol"],
                rule_version=row["rule_version"],
                schema_version=row["schema_version"],
                state=json.loads(row["payload"]),
                confirmations=[],
                risks={},
            )
            completed.add((row["symbol"], row["last_cycle"]))
        for row in self.conn.execute(
            f"SELECT e.*,r.payload AS risk FROM signal_event e LEFT JOIN event_risk r USING(event_id) WHERE e.symbol IN ({marks}) ORDER BY e.confirmed_at,e.event_id",
            symbols,
        ):
            record = result.get(row["symbol"])
            if record:
                record["confirmations"].append(json.loads(row["payload"]))
                record["risks"][row["event_id"]] = (
                    json.loads(row["risk"]) if row["risk"] else {}
                )
        return result, completed

    def save(self, records, categories, cycle, *, commit=True):
        if len(records) > 128:
            raise ValueError("A state transaction is limited to 128 symbols")
        inserted = 0
        with self.conn if commit else nullcontext():
            for record in records:
                state = record["state"]
                decision = state.get("decision") or {}
                phase = (
                    "DATA_PENDING"
                    if categories[record["symbol"]] != "valid"
                    else "CONFIRMED"
                    if (state.get("structure") or {}).get("locked")
                    else "WATCHING"
                    if decision.get("impulse_recent")
                    else "NO_SETUP"
                )
                self.conn.execute(
                    """INSERT INTO symbol_state VALUES(?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(symbol) DO UPDATE SET rule_version=excluded.rule_version,
                    schema_version=excluded.schema_version,processed_at=excluded.processed_at,
                    data_asof=excluded.data_asof,payload=excluded.payload,updated_at=excluded.updated_at,
                    last_cycle=excluded.last_cycle,category=excluded.category,phase=excluded.phase""",
                    (
                        record["symbol"],
                        record["rule_version"],
                        record["schema_version"],
                        state["processed_at"],
                        state["data_asof"],
                        encoded(state),
                        record["updated_at"],
                        cycle["cycle_id"],
                        categories[record["symbol"]],
                        phase,
                    ),
                )
                for event in record["confirmations"]:
                    cursor = self.conn.execute(
                        """INSERT INTO signal_event VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(event_id) DO NOTHING""",
                        (
                            event["event_id"],
                            event["symbol"],
                            event["rule_version"],
                            event["setup_at"],
                            event["confirmed_at"],
                            event["first_seen_at"],
                            event["source_asof"],
                            event["pattern"],
                            event["mode"],
                            encoded(event),
                        ),
                    )
                    inserted += cursor.rowcount
                    self.conn.execute(
                        """INSERT INTO event_risk VALUES(?,?,?) ON CONFLICT(event_id)
                        DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at""",
                        (
                            event["event_id"],
                            encoded(record["risks"].get(event["event_id"], {})),
                            record["updated_at"],
                        ),
                    )
            cycle["new_signals"] += inserted
            columns = list(cycle)
            marks = ",".join("?" for _ in columns)
            update = ",".join(
                f"{key}=excluded.{key}" for key in columns if key != "cycle_id"
            )
            self.conn.execute(
                f"INSERT INTO scan_cycle ({','.join(columns)}) VALUES({marks}) ON CONFLICT(cycle_id) DO UPDATE SET {update}",
                list(cycle.values()),
            )
        return inserted

    def prune(self, before_day):
        # Calendar cutoff is supplied by the offline dataset, never guessed as N days.
        with self.conn:
            self.conn.execute(
                "DELETE FROM signal_event WHERE substr(confirmed_at,1,10)<?",
                (before_day,),
            )
            self.conn.execute(
                "DELETE FROM scan_cycle WHERE trade_date<?", (before_day,)
            )
