"""Single SQLite WAL writer, raw 250 x 15m retention, independent read connections."""

from contextlib import closing, contextmanager
import json
from math import isclose
from pathlib import Path
import sqlite3
from time import time

from .grid import (
    CAPACITY,
    FIELDS,
    LEGACY,
    SOURCE,
    aggregate,
    bucket_end,
    derived,
    points,
    project,
)
from .lease import ProcessLease

COLUMNS = (
    "symbol",
    "name",
    "eligible",
    "source_type",
    "raw_tick",
    "listing_date",
    "listing_status",
    "source_id",
    "latest_completed15",
    "latest_source5",
    "revision",
    "basis_revision",
    "factor_revision",
    "factor_coverage",
    "factors_day",
    "state",
    "gaps",
    "transition",
    "correction",
    "rebuild_from",
    "attempted",
    "next_retry",
    "updated_at",
    "metadata_source",
)
BAR_COLUMNS = ("symbol", "bar_end", *FIELDS, "source_id", "source_count")
DEFAULTS = dict(
    name="",
    eligible=False,
    source_type="stock",
    raw_tick=None,
    listing_date=None,
    listing_status="NOT_PROVIDED",
    source_id=SOURCE,
    latest_completed15=None,
    latest_source5=None,
    revision=0,
    basis_revision=0,
    factor_revision=0,
    factor_coverage="MISSING",
    factors_day=None,
    state="WARMUP",
    gaps=[],
    transition="NEW",
    correction=False,
    rebuild_from=None,
    attempted=None,
    next_retry=0,
    updated_at=None,
    metadata_source="TICKFLOW_INSTRUMENTS",
)
DDL = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS instrument_state(
 symbol TEXT PRIMARY KEY,name TEXT,eligible INTEGER,source_type TEXT,raw_tick REAL,listing_date TEXT,
 listing_status TEXT,source_id TEXT,latest_completed15 TEXT,latest_source5 TEXT,
 revision INTEGER,basis_revision INTEGER,factor_revision INTEGER,factor_coverage TEXT,factors_day TEXT,
 state TEXT,gaps TEXT,transition TEXT,correction INTEGER,rebuild_from TEXT,attempted TEXT,
 next_retry REAL,updated_at TEXT,metadata_source TEXT);
CREATE TABLE IF NOT EXISTS bar15(
 symbol TEXT,bar_end TEXT,open REAL,high REAL,low REAL,close REAL,volume REAL,amount REAL,
 source_id TEXT,source_count INTEGER CHECK(source_count=3),PRIMARY KEY(symbol,bar_end));
CREATE TABLE IF NOT EXISTS tail5(
 symbol TEXT,bar_end TEXT,open REAL,high REAL,low REAL,close REAL,volume REAL,amount REAL,
 PRIMARY KEY(symbol,bar_end));
CREATE TABLE IF NOT EXISTS factor_event(
 symbol TEXT,ordinal INTEGER,day TEXT,ex_factor REAL CHECK(ex_factor>0),PRIMARY KEY(symbol,ordinal));
"""


def encoded(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def reader(path):
    conn = sqlite3.connect(
        Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=5
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def unpack(row):
    if row is None:
        return None
    info = dict(row)
    info["gaps"] = json.loads(info["gaps"])
    info["eligible"] = bool(info["eligible"])
    info["correction"] = bool(info["correction"])
    info["gap_count"] = len(info["gaps"])
    return info


class Store:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lease = ProcessLease(self.path.with_suffix(".writer.lock"))
        try:
            self.conn = sqlite3.connect(self.path, timeout=5)
            self.conn.row_factory = sqlite3.Row
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA synchronous=NORMAL")
            self.conn.execute("PRAGMA busy_timeout=5000")
            self.conn.executescript(DDL)
            self.conn.commit()
        except Exception:
            self.lease.close()
            raise

    @contextmanager
    def transaction(self):
        with self.conn:
            yield

    def meta(self, key, default=None, *, conn=None):
        row = (
            (conn or self.conn)
            .execute("SELECT payload FROM meta WHERE key=?", (key,))
            .fetchone()
        )
        return json.loads(row[0]) if row else default

    def set_meta(self, key, value):
        self.conn.execute(
            "INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET payload=excluded.payload",
            (key, encoded(value)),
        )

    def instrument(self, symbol, *, conn=None):
        return unpack(
            (conn or self.conn)
            .execute("SELECT * FROM instrument_state WHERE symbol=?", (symbol,))
            .fetchone()
        )

    def _put(self, info):
        values = [encoded(info[k]) if k == "gaps" else info[k] for k in COLUMNS]
        update = ",".join(f"{k}=excluded.{k}" for k in COLUMNS[1:])
        self.conn.execute(
            f"INSERT INTO instrument_state VALUES({','.join('?' for _ in COLUMNS)}) "
            f"ON CONFLICT(symbol) DO UPDATE SET {update}",
            values,
        )

    def save_master(self, rows, overview, sessions):
        with self.conn:
            old_members = {
                r[0]
                for r in self.conn.execute(
                    "SELECT symbol FROM instrument_state WHERE eligible=1"
                )
            }
            members = {r["symbol"] for r in rows if r.get("eligible")}
            self.conn.execute("UPDATE instrument_state SET eligible=0")
            for row in rows:
                previous = self.instrument(row["symbol"]) or dict(DEFAULTS)
                self._put(
                    dict(previous, **{k: v for k, v in row.items() if k in COLUMNS})
                )
            self.set_meta(
                "generation", self.meta("generation", 0) + int(old_members != members)
            )
            self.set_meta("universe", overview)
            self.set_meta("master_day", max(sessions, default=None))
            self.set_sessions(sessions)
            self.set_meta("data_revision", self.meta("data_revision", 0) + 1)

    def put_metadata(self, rows):
        """Apply an explicit small metadata response without changing full-market membership."""
        with self.conn:
            for row in rows:
                previous = self.instrument(row["symbol"]) or dict(DEFAULTS)
                self._put(
                    dict(previous, **{k: v for k, v in row.items() if k in COLUMNS})
                )
            self.set_meta("data_revision", self.meta("data_revision", 0) + 1)

    def set_sessions(self, sessions):
        old = self.meta("sessions", [])
        combined = sorted(set(old) | set(sessions))
        if old != combined:
            self.set_meta("calendar_revision", self.meta("calendar_revision", 0) + 1)
            self.set_meta("sessions", combined)

    def universe(self):
        return {
            r["symbol"]: unpack(r)
            for r in self.conn.execute("SELECT * FROM instrument_state ORDER BY symbol")
        }

    def universe_page(self, offset, limit):
        with closing(reader(self.path)) as conn:
            conn.execute("BEGIN")
            total = conn.execute("SELECT count(*) FROM instrument_state").fetchone()[0]
            rows = [
                unpack(r)
                for r in conn.execute(
                    "SELECT * FROM instrument_state ORDER BY symbol LIMIT ? OFFSET ?",
                    (limit, offset),
                )
            ]
            return dict(
                total=total,
                rows=rows,
                generation=self.meta("generation", 0, conn=conn),
                data_revision=self.meta("data_revision", 0, conn=conn),
                universe=self.meta("universe", {}, conn=conn),
                sessions=self.meta("sessions", [], conn=conn),
            )

    def events(self, symbol, *, conn=None):
        db = conn or self.conn
        info = self.instrument(symbol, conn=db)
        if not info or info["factor_coverage"] != "EXPLICIT":
            return None
        return [
            dict(r)
            for r in db.execute(
                "SELECT day,ex_factor FROM factor_event WHERE symbol=? ORDER BY ordinal",
                (symbol,),
            )
        ]

    def _factors(self, symbol, events):
        self.conn.execute("DELETE FROM factor_event WHERE symbol=?", (symbol,))
        if events is not None:
            self.conn.executemany(
                "INSERT INTO factor_event VALUES(?,?,?,?)",
                [(symbol, i, e["day"], e["ex_factor"]) for i, e in enumerate(events)],
            )

    def raw_rows(self, symbol, asof="9999", *, conn=None):
        rows = (
            (conn or self.conn)
            .execute(
                "SELECT * FROM bar15 WHERE symbol=? AND bar_end<=? ORDER BY bar_end DESC LIMIT 250",
                (symbol, asof),
            )
            .fetchall()
        )
        return [
            dict(
                timestamp=r["bar_end"],
                **{k: r[k] for k in FIELDS},
                source_id=r["source_id"],
                source=r["source_id"],
                source_count=r["source_count"],
                complete=True,
            )
            for r in reversed(rows)
        ]

    def _write(self, symbol, bars):
        self.conn.executemany(
            "INSERT INTO bar15 VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(symbol,bar_end) DO UPDATE SET "
            "open=excluded.open,high=excluded.high,low=excluded.low,close=excluded.close,"
            "volume=excluded.volume,amount=excluded.amount,source_id=excluded.source_id,source_count=excluded.source_count",
            [
                (
                    symbol,
                    b["timestamp"],
                    *(b.get(k) for k in FIELDS),
                    b["source_id"],
                    b["source_count"],
                )
                for b in bars[-250:]
            ],
        )
        self.conn.execute(
            "DELETE FROM bar15 WHERE symbol=? AND bar_end < "
            "(SELECT bar_end FROM bar15 WHERE symbol=? ORDER BY bar_end DESC LIMIT 1 OFFSET 249)",
            (symbol, symbol),
        )

    def accept(
        self,
        symbol,
        received,
        *,
        sessions,
        start,
        end,
        now,
        events=None,
        factor_response=False,
        rebuild=False,
    ):
        info = self.instrument(symbol)
        if info is None:
            raise ValueError("Metadata must precede bars")
        old = self.raw_rows(symbol)
        switching = info["source_id"] != SOURCE
        replace = switching or (rebuild and end >= (info["latest_completed15"] or ""))
        five = (
            {}
            if replace
            else {
                r["bar_end"]: {k: r[k] for k in FIELDS}
                for r in self.conn.execute(
                    "SELECT * FROM tail5 WHERE symbol=?", (symbol,)
                )
            }
        )
        five.update(received)
        incoming = aggregate(five, sessions, end)["15m"]
        if (not incoming and not received) or (
            switching and len(incoming) < 56 and len(old) >= 56
        ):
            self.failed([symbol], end, now, "SOURCE_UNAVAILABLE")
            return
        old_factors = self.events(symbol)
        new_events = events if factor_response else (None if switching else old_factors)
        by_old = {b["timestamp"]: b for b in old}
        overlaps = [b for b in incoming if b["timestamp"] in by_old]
        mismatch = any(
            any(
                not isclose(
                    b[k], by_old[b["timestamp"]][k], rel_tol=1e-12, abs_tol=1e-9
                )
                for k in FIELDS
                if b.get(k) is not None and by_old[b["timestamp"]].get(k) is not None
            )
            or any(
                (b.get(k) is None) != (by_old[b["timestamp"]].get(k) is None)
                for k in FIELDS
            )
            for b in overlaps
        )
        changed = bool(overlaps and mismatch)
        transition = info["transition"]
        if switching:
            transition = (
                "OVERLAP_VERIFIED"
                if len(overlaps) >= 56
                and not mismatch
                and new_events is not None
                and old_factors == new_events
                else "SOURCE_DISCREPANCY"
                if mismatch
                else "REBUILT_TICKFLOW"
            )
        corrected = changed and not switching
        if corrected:
            transition = "HISTORY_REVISED"
        info["correction"] = corrected
        if corrected or switching or (factor_response and old_factors != events):
            info["basis_revision"] += 1
        if factor_response and old_factors != events:
            info["factor_revision"] += 1
        retained = {b["timestamp"]: b for b in ([] if replace else old)}
        retained.update({b["timestamp"]: b for b in incoming})
        raw = [retained[t] for t in sorted(retained)][-250:]
        expected = points(
            sessions,
            max(start[:10], info["listing_date"] or start[:10]) + " 09:35:00",
            end,
        )
        missing5 = [t for t in expected if t >= start and t not in five]
        wanted = points(
            sessions,
            raw[0]["timestamp"] if raw else start,
            max(end, raw[-1]["timestamp"] if raw else end),
            step=15,
        )
        gaps = [t for t in wanted if t not in retained]
        tail_end = max((bucket_end(t, 15) for t in five), default=None)
        used = {b["timestamp"] for b in incoming}
        tail = [
            (t, r)
            for t, r in sorted(five.items())
            if bucket_end(t, 15) == tail_end and tail_end not in used
        ][-2:]
        state = (
            "FACTOR_PENDING"
            if new_events is None
            else "SOURCE_DISCREPANCY"
            if transition == "SOURCE_DISCREPANCY" and len(raw) < 56
            else "GAP"
            if gaps or missing5
            else "READY"
            if len(raw) >= 56
            else "WARMUP"
        )
        modified = (
            switching
            or corrected
            or (factor_response and old_factors != events)
            or any(b["timestamp"] not in by_old for b in incoming)
            or state != info["state"]
            or gaps[-250:] != info["gaps"]
        )
        with self.conn:
            if replace:
                self.conn.execute("DELETE FROM bar15 WHERE symbol=?", (symbol,))
            self._write(symbol, incoming)
            self.conn.execute("DELETE FROM tail5 WHERE symbol=?", (symbol,))
            self.conn.executemany(
                "INSERT INTO tail5 VALUES(?,?,?,?,?,?,?,?)",
                [(symbol, t, *(r.get(k) for k in FIELDS)) for t, r in tail],
            )
            if factor_response or switching:
                self._factors(symbol, new_events)
                info.update(
                    factor_coverage="EXPLICIT" if new_events is not None else "MISSING",
                    factors_day=now[:10],
                )
            info.update(
                source_id=SOURCE,
                transition=transition,
                state=state,
                gaps=gaps[-250:],
                latest_completed15=raw[-1]["timestamp"] if raw else None,
                latest_source5=max(
                    [
                        t
                        for t in (info["latest_source5"], max(received, default=None))
                        if t
                    ],
                    default=None,
                ),
                revision=info["revision"] + int(modified),
                attempted=max(end, info["attempted"] or ""),
                updated_at=now,
                next_retry=0 if state in ("READY", "WARMUP") else time() + 300,
                rebuild_from=gaps[0] if gaps else None,
            )
            self._put(info)
            self.set_meta("last_commit", now)
            self.set_meta(
                "data_revision", self.meta("data_revision", 0) + int(modified)
            )
        return info

    def import_legacy(self, info, bars, events, now):
        if self.instrument(info["symbol"]) is not None:
            return False
        row = dict(DEFAULTS)
        row.update({k: v for k, v in info.items() if k in COLUMNS})
        row.update(
            source_id=LEGACY,
            state="REBUILD_PENDING",
            transition="LEGACY_DISPLAY_ONLY",
            factor_coverage="EXPLICIT" if events is not None else "MISSING",
            updated_at=now,
        )
        for b in bars:
            b.update(source=LEGACY, source_id=LEGACY)
        row["latest_completed15"] = bars[-1]["timestamp"] if bars else None
        row["latest_source5"] = None
        with self.conn:
            self._put(row)
            self._write(row["symbol"], bars)
            self._factors(row["symbol"], events)
        return True

    def failed(self, symbols, end, now, message):
        with self.conn:
            for symbol in symbols:
                info = self.instrument(symbol)
                if info:
                    info.update(
                        state=message,
                        attempted=end,
                        updated_at=now,
                        next_retry=time() + 300,
                        revision=info["revision"] + 1,
                    )
                    self._put(info)
            self.set_meta("data_revision", self.meta("data_revision", 0) + 1)

    def read(self, symbol, tf, asof, limit=250):
        with closing(reader(self.path)) as conn:
            conn.execute("BEGIN")
            info = self.instrument(symbol, conn=conn)
            if info is None:
                return dict(
                    symbol=symbol,
                    timeframe=tf,
                    bars=[],
                    state="METADATA_PENDING",
                    row_count=0,
                    capacity=CAPACITY[tf],
                    source_id=SOURCE,
                )
            sessions = self.meta("sessions", [], conn=conn)
            raw = derived(self.raw_rows(symbol, asof, conn=conn), tf, sessions, asof)
            bars = project(
                raw,
                self.events(symbol, conn=conn),
                asof,
                sessions=sessions,
                timeframe=tf,
            )
            return dict(
                symbol=symbol,
                name=info["name"],
                timeframe=tf,
                bars=bars[-limit:],
                state=info["state"]
                if info["state"]
                in (
                    "READY",
                    "WARMUP",
                    "GAP",
                    "FACTOR_PENDING",
                    "REBUILD_PENDING",
                    "SOURCE_DISCREPANCY",
                    "SOURCE_UNAVAILABLE",
                )
                else "SOURCE_UNAVAILABLE",
                error_code=info["state"]
                if info["state"]
                not in (
                    "READY",
                    "WARMUP",
                    "GAP",
                    "FACTOR_PENDING",
                    "REBUILD_PENDING",
                    "SOURCE_DISCREPANCY",
                    "SOURCE_UNAVAILABLE",
                )
                else None,
                row_count=len(raw),
                capacity=CAPACITY[tf],
                latest_completed_at=raw[-1]["timestamp"] if raw else None,
                source_asof=info["updated_at"],
                source_id=info["source_id"],
                transition=info["transition"],
                analysis_allowed=info["source_id"] == SOURCE
                and info["state"] in ("READY", "WARMUP", "GAP"),
                price_basis="QFQ" if info["factor_coverage"] == "EXPLICIT" else "RAW",
                basis_id=f"{info['source_id']}:{asof[:10]}:{info['basis_revision']}",
                basis_revision=info["basis_revision"],
                factor_revision=info["factor_revision"],
                revision=info["revision"],
                correction=info["correction"],
                raw_tick=info["raw_tick"],
                asset_type=info["source_type"],
                volume_unit="shares",
                factor_source=info["source_id"] + "_EX_FACTORS",
                factor_coverage=info["factor_coverage"],
                gap_count=len(info["gaps"]),
                gaps=info["gaps"],
                warm_count=len(self.raw_rows(symbol, asof, conn=conn)),
                warm_target=250,
                eligible=info["eligible"],
                listing_status=info["listing_status"],
            )

    def snapshot(self):
        with closing(reader(self.path)) as conn:
            states = dict(
                conn.execute(
                    "SELECT state,count(*) FROM instrument_state WHERE eligible=1 GROUP BY state"
                )
            )
            oldest, newest, count = conn.execute(
                "SELECT min(latest_completed15),max(latest_completed15),count(*) FROM instrument_state"
            ).fetchone()
            return dict(
                data_revision=self.meta("data_revision", 0, conn=conn),
                generation=self.meta("generation", 0, conn=conn),
                states=states,
                source_counts=dict(
                    conn.execute(
                        "SELECT source_id,count(*) FROM instrument_state WHERE eligible=1 GROUP BY source_id"
                    )
                ),
                pending_symbols=sum(v for k, v in states.items() if k != "READY"),
                oldest_15m=oldest,
                latest_15m=newest,
                instruments=count,
                last_commit=self.meta("last_commit", conn=conn),
                calendar_last=max(self.meta("sessions", [], conn=conn), default=None),
                universe=self.meta("universe", {}, conn=conn),
            )

    def inventory(self, symbol):
        with closing(reader(self.path)) as conn:
            return [
                dict(timeframe=tf, row_count=n, earliest=a, latest=b, capacity=cap)
                for tf, table, cap in (("15m", "bar15", 250), ("5m_tail", "tail5", 2))
                for n, a, b in [
                    conn.execute(
                        f"SELECT count(*),min(bar_end),max(bar_end) FROM {table} WHERE symbol=?",
                        (symbol,),
                    ).fetchone()
                ]
            ]

    def close(self):
        self.conn.close()
        self.lease.close()
