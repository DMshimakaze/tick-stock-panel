"""Single raw-history writer; chart lookback and frozen strategy windows differ."""

from contextlib import closing, contextmanager, nullcontext
import json
from math import isclose
from pathlib import Path
import sqlite3
from time import time

from .market_grid import (
    CAPACITY,
    FIELDS,
    LEGACY,
    SOURCE,
    aggregate,
    bucket_end,
    derived,
    points,
    project,
    history_capacity,
    required_history,
    MAS,
    STRATEGY_CAPACITY,
    latest_point,
    now_cn,
)
from .worker_lease import ProcessLease
from . import indicator_cache

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
CREATE TABLE IF NOT EXISTS ma_history_progress(
 symbol TEXT PRIMARY KEY,scanned_before TEXT,stop_reason TEXT,next_retry REAL NOT NULL DEFAULT 0,
 raw_count INTEGER NOT NULL DEFAULT 0,earliest TEXT);
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

    source = SOURCE
    source_count = 3
    schema = DDL
    instrument_table = "instrument_state"
    bar_table = "bar15"
    factor_table = "factor_event"
    progress_table = "ma_history_progress"
    cache_table = "chart_cache"
    meta_prefix = ""

    def __init__(self, path, *, connection=None):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.owns_connection = connection is None
        self.lease = (
            ProcessLease(self.path.with_suffix(".writer.lock"))
            if self.owns_connection
            else None
        )
        try:
            self.conn = (
                connection
                if connection is not None
                else sqlite3.connect(self.path, timeout=5)
            )
            self.conn.row_factory = sqlite3.Row
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA synchronous=NORMAL")
            self.conn.execute("PRAGMA busy_timeout=5000")
            self.conn.executescript(self.schema)
            self.conn.executescript(indicator_cache.DDL.replace("chart_cache", self.cache_table))
            self.conn.commit()
        except Exception:
            if self.lease:
                self.lease.close()
            raise

    @contextmanager
    def transaction(self):
        # Unified owner begins the encompassing market + strategy transaction.
        with nullcontext() if self.conn.in_transaction else self.conn:
            yield

    def meta(self, key, default=None, *, conn=None):
        row = (
            (conn or self.conn)
            .execute("SELECT payload FROM meta WHERE key=?", (self.meta_prefix + key,))
            .fetchone()
        )
        return json.loads(row[0]) if row else default

    def set_meta(self, key, value):
        self.conn.execute(
            "INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET payload=excluded.payload",
            (self.meta_prefix + key, encoded(value)),
        )

    def instrument(self, symbol, *, conn=None):
        return unpack(
            (conn or self.conn)
            .execute(f"SELECT * FROM {self.instrument_table} WHERE symbol=?", (symbol,))
            .fetchone()
        )

    def _put(self, info):
        values = [encoded(info[k]) if k == "gaps" else info[k] for k in COLUMNS]
        update = ",".join(f"{k}=excluded.{k}" for k in COLUMNS[1:])
        self.conn.execute(
            f"INSERT INTO {self.instrument_table} VALUES({','.join('?' for _ in COLUMNS)}) "
            f"ON CONFLICT(symbol) DO UPDATE SET {update}",
            values,
        )

    def save_master(self, rows, overview, sessions):
        with self.transaction():
            old_members = {
                r[0]
                for r in self.conn.execute(
                    f"SELECT symbol FROM {self.instrument_table} WHERE eligible=1"
                )
            }
            members = {r["symbol"] for r in rows if r.get("eligible")}
            self.conn.execute(f"UPDATE {self.instrument_table} SET eligible=0")
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
        with self.transaction():
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
            for r in self.conn.execute(f"SELECT * FROM {self.instrument_table} ORDER BY symbol")
        }

    def universe_page(self, offset, limit):
        with closing(reader(self.path)) as conn:
            conn.execute("BEGIN")
            total = conn.execute(f"SELECT count(*) FROM {self.instrument_table}").fetchone()[0]
            rows = [
                unpack(r)
                for r in conn.execute(
                    f"SELECT * FROM {self.instrument_table} ORDER BY symbol LIMIT ? OFFSET ?",
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
                f"SELECT day,ex_factor FROM {self.factor_table} WHERE symbol=? ORDER BY ordinal",
                (symbol,),
            )
        ]

    def _factors(self, symbol, events):
        self.conn.execute(f"DELETE FROM {self.factor_table} WHERE symbol=?", (symbol,))
        if events is not None:
            self.conn.executemany(
                f"INSERT INTO {self.factor_table} VALUES(?,?,?,?)",
                [(symbol, i, e["day"], e["ex_factor"]) for i, e in enumerate(events)],
            )

    def raw_rows(self, symbol, asof="9999", *, limit=STRATEGY_CAPACITY, conn=None):
        rows = (
            (conn or self.conn)
            .execute(
                f"SELECT * FROM {self.bar_table} WHERE symbol=? AND bar_end<=? ORDER BY bar_end DESC LIMIT ?",
                (symbol, asof, limit),
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
            f"INSERT INTO {self.bar_table} VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(symbol,bar_end) DO UPDATE SET "
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
                for b in bars[-history_capacity():]
            ],
        )
        self.conn.execute(
            f"DELETE FROM {self.bar_table} WHERE symbol=? AND bar_end < "
            f"(SELECT bar_end FROM {self.bar_table} WHERE symbol=? ORDER BY bar_end DESC LIMIT 1 OFFSET ?)",
            (symbol, symbol, history_capacity() - 1),
        )
        self.conn.execute(
            f"INSERT INTO {self.progress_table}(symbol,raw_count,earliest) "
            f"SELECT ?,count(*),min(bar_end) FROM {self.bar_table} WHERE symbol=? "
            "ON CONFLICT(symbol) DO UPDATE SET raw_count=excluded.raw_count,earliest=excluded.earliest",
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
        old = self.raw_rows(symbol, limit=history_capacity())
        switching = info["source_id"] != self.source
        # Same-source repairs merge into history. Only an actual source switch
        # replaces incompatible raw input; historical backfill has its own path.
        replace = switching
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
        raw = [retained[t] for t in sorted(retained)][-history_capacity():]
        expected = points(
            sessions,
            max(start[:10], info["listing_date"] or start[:10]) + " 09:35:00",
            end,
        )
        missing5 = [t for t in expected if t >= start and t not in five]
        wanted = points(
            sessions,
            raw[-STRATEGY_CAPACITY]["timestamp"] if len(raw) >= STRATEGY_CAPACITY else raw[0]["timestamp"] if raw else start,
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
        with self.transaction():
            if replace:
                self.conn.execute(f"DELETE FROM {self.bar_table} WHERE symbol=?", (symbol,))
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
                source_id=self.source,
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
            older_insert = any(b["timestamp"] not in by_old and old and b["timestamp"] <= old[-1]["timestamp"] for b in incoming)
            self.update_cache(symbol, info, raw, new_events, sessions,
                              rebuild=corrected or switching or older_insert, append_only=True)
            self.set_meta("last_commit", now)
            self.set_meta(
                "data_revision", self.meta("data_revision", 0) + int(modified)
            )
        return info

    def history_progress(self, symbol, *, conn=None):
        row = (conn or self.conn).execute(
            f"SELECT * FROM {self.progress_table} WHERE symbol=?", (symbol,)
        ).fetchone()
        return dict(row) if row else dict(scanned_before=None, stop_reason=None, next_retry=0)

    def backfill(self, symbol, received, *, sessions, start, end, now, stop_reason=None):
        """Older raw UPSERT only: no tail replacement, strategy replay or BUY write."""
        info = self.instrument(symbol)
        if not info or info["source_id"] != self.source:
            raise ValueError("Historical backfill requires the established TickFlow source")
        incoming = aggregate(received, sessions, end)["15m"]
        if any(t > (info["latest_completed15"] or "") for t in received):
            raise ValueError("History job cannot advance the live watermark")
        with self.transaction():
            self._write(symbol, incoming)
            raw = self.raw_rows(symbol, limit=history_capacity())
            # A missing bucket inside known history is a bounded repair, not a
            # new older-history page. Empty/partial replies must cool down.
            failed_gap = bool(raw and start >= raw[0]["timestamp"] and not incoming)
            if failed_gap:
                stop_reason = "GAP"
            expected = points(sessions, raw[-STRATEGY_CAPACITY]["timestamp"] if len(raw) >= STRATEGY_CAPACITY else raw[0]["timestamp"],
                              info["latest_completed15"], step=15) if raw else []
            actual = {b["timestamp"] for b in raw}
            gaps = [t for t in expected if t not in actual]
            info.update(revision=info["revision"] + 1, updated_at=now, gaps=gaps,
                        rebuild_from=gaps[0] if gaps else None)
            if info["factor_coverage"] == "EXPLICIT" and info["state"] in ("READY", "WARMUP", "GAP"):
                info["state"] = "GAP" if gaps else "READY" if len(raw) >= 56 else "WARMUP"
            self._put(info)
            self.update_cache(symbol, info, raw, self.events(symbol), sessions, rebuild=True, append_only=True)
            self.conn.execute(
                f"INSERT INTO {self.progress_table}(symbol,scanned_before,stop_reason,next_retry) VALUES(?,?,?,?) ON CONFLICT(symbol) DO UPDATE SET "
                "scanned_before=excluded.scanned_before,stop_reason=excluded.stop_reason,next_retry=excluded.next_retry",
                (symbol, start, stop_reason, time() + (300 if stop_reason == "GAP" else 86400) if stop_reason else 0),
            )
            self.set_meta("data_revision", self.meta("data_revision", 0) + 1)
            self.set_meta("last_commit", now)
        return info

    def history_candidates(self, target):
        return [dict(r) for r in self.conn.execute(
            "SELECT i.symbol,p.scanned_before,p.stop_reason,coalesce(p.next_retry,0) next_retry,"
            f"coalesce(p.raw_count,0) raw_count,p.earliest FROM {self.instrument_table} i "
            f"LEFT JOIN {self.progress_table} p USING(symbol) "
            "WHERE i.eligible=1 AND i.source_id=? AND i.latest_completed15>=? "
            "AND (coalesce(p.raw_count,0)<? OR p.stop_reason='GAP') ORDER BY i.symbol",
            (self.source, target, history_capacity()),
        )]

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
        with self.transaction():
            self._put(row)
            self._write(row["symbol"], bars)
            self._factors(row["symbol"], events)
            self.update_cache(row["symbol"], row, bars, events, self.meta("sessions", []), rebuild=True)
        return True

    def update_cache(self, symbol, info, raw, events, sessions, *, rebuild=False, append_only=False):
        return indicator_cache.update(self.conn, symbol, info, raw, events, sessions,
                                      self.meta("calendar_revision", 0), rebuild=rebuild, append_only=append_only, table=self.cache_table)

    def warm_cache(self, symbols=(), limit=8):
        """Writer-only rebuild of saved facts; never calls a supplier or strategy."""
        candidates = list(dict.fromkeys(symbols))[:limit]
        if not candidates:
            candidates = [r[0] for r in self.conn.execute(
                f"SELECT i.symbol FROM {self.instrument_table} i LEFT JOIN {self.cache_table} c "
                "ON c.symbol=i.symbol AND c.timeframe='15m' WHERE i.latest_completed15 IS NOT NULL "
                "AND (c.symbol IS NULL OR c.version!=? OR c.calendar_revision!=? "
                "OR c.basis_revision!=i.basis_revision OR c.factor_revision!=i.factor_revision "
                "OR c.source_id!=i.source_id OR c.metadata!=json_array(i.source_type,i.raw_tick,i.listing_date,i.factor_coverage)) "
                "ORDER BY i.symbol LIMIT ?", (indicator_cache.VERSION, self.meta("calendar_revision", 0), limit))]
        with self.transaction():
            for symbol in candidates:
                info = self.instrument(symbol)
                if info:
                    self.update_cache(symbol, info, self.raw_rows(symbol, limit=history_capacity()),
                                      self.events(symbol), self.meta("sessions", []), rebuild=True)
        return candidates

    def failed(self, symbols, end, now, message):
        with self.transaction():
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

    def read(self, symbol, tf, asof, limit=250, *, connection=None, strategy=False):
        with (
            nullcontext(connection)
            if connection is not None
            else closing(reader(self.path)) as conn
        ):
            if connection is None:
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
                    source_id=self.source,
                )
            sessions = self.meta("sessions", [], conn=conn)
            visible = min(limit, CAPACITY[tf])
            events = self.events(symbol, conn=conn)
            cached = None if strategy else indicator_cache.read(conn, symbol, tf, asof, visible, info,
                self.meta("calendar_revision", 0, conn=conn), events, table=self.cache_table)
            if cached:
                visible_bars = cached["bars"]
                available, raw_count, history_gaps = cached["available"], cached["raw15_count"], cached["history_gaps"]
            else:
                raw15 = self.raw_rows(symbol, asof, limit=STRATEGY_CAPACITY if strategy else history_capacity(), conn=conn)
                raw = derived(raw15, tf, sessions, asof)
                visible_bars = project(raw, events, asof, sessions=sessions, timeframe=tf,
                                       periods=() if strategy else MAS)[-visible:]
                available, raw_count = len(raw), len(raw15)
                history_gaps = len(points(sessions, raw15[0]["timestamp"], raw15[-1]["timestamp"], step=15)) - len(raw15) if raw15 else 0
            complete_ma = bool(visible_bars) and all(b.get(f"ma{n}") is not None for b in visible_bars for n in MAS)
            progress = self.history_progress(symbol, conn=conn)
            ma_state = "FACTOR_PENDING" if info["factor_coverage"] != "EXPLICIT" else "REBUILD_PENDING" if info["source_id"] != self.source else "GAP" if history_gaps else "READY" if complete_ma and len(visible_bars) == visible else progress["stop_reason"] or "PENDING"
            return dict(
                symbol=symbol,
                name=info["name"],
                timeframe=tf,
                bars=visible_bars,
                ma_history=dict(state=ma_state, required=required_history(tf, visible), available=available,
                                raw15_count=raw_count, raw15_capacity=history_capacity(),
                                visible=len(visible_bars), complete=sum(all(b.get(f"ma{n}") is not None for n in MAS) for b in visible_bars),
                                first=visible_bars[0]["timestamp"] if visible_bars else None,
                                last=visible_bars[-1]["timestamp"] if visible_bars else None,
                                factor_revision=info["factor_revision"], basis_revision=info["basis_revision"],
                                source_id=info["source_id"], stop_reason=progress["stop_reason"]),
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
                row_count=len(visible_bars),
                capacity=CAPACITY[tf],
                latest_completed_at=visible_bars[-1]["timestamp"] if visible_bars else None,
                source_asof=info["updated_at"],
                source_id=info["source_id"],
                transition=info["transition"],
                analysis_allowed=info["source_id"] == self.source
                and info["state"] in ("READY", "WARMUP"),
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
                cache=cached["cache"] if cached else dict(hit=False, version=indicator_cache.VERSION, state="REBUILD_PENDING"),
                warm_count=min(raw_count, STRATEGY_CAPACITY),
                warm_target=250,
                eligible=info["eligible"],
                listing_status=info["listing_status"],
            )

    def snapshot(self, *, scope=None, connection=None, target=None):
        with nullcontext(connection) if connection is not None else closing(reader(self.path)) as conn:
            if connection is None:
                conn.execute("BEGIN")
            target = target or latest_point(self.meta("sessions", [], conn=conn), str(now_cn()))
            where = "i.eligible=1"
            params = ()
            if scope is not None:
                where += " AND i.symbol IN (SELECT value FROM json_each(?))"
                params = (encoded(scope),)
            refreshed = conn.execute(
                f"SELECT count(*) FROM {self.instrument_table} i WHERE {where} AND i.source_id=? "
                f"AND EXISTS(SELECT 1 FROM {self.bar_table} b WHERE b.symbol=i.symbol AND b.bar_end=? "
                f"AND b.source_id=? AND b.source_count={self.source_count})",
                (*params, self.source, target, self.source),
            ).fetchone()[0]
            expected = conn.execute(f"SELECT count(*) FROM {self.instrument_table} i WHERE {where}", params).fetchone()[0]
            saved = conn.execute(f"SELECT max(latest_completed15) FROM {self.instrument_table} i WHERE {where} AND source_id=?", (*params, self.source)).fetchone()[0]
            states = dict(
                conn.execute(
                    f"SELECT state,count(*) FROM {self.instrument_table} i WHERE {where} GROUP BY state", params
                )
            )
            oldest, newest, count = conn.execute(
                f"SELECT min(latest_completed15),max(latest_completed15),count(*) FROM {self.instrument_table}"
            ).fetchone()
            return dict(
                data_revision=self.meta("data_revision", 0, conn=conn),
                generation=self.meta("generation", 0, conn=conn),
                states=states,
                source_counts=dict(
                    conn.execute(
                        f"SELECT source_id,count(*) FROM {self.instrument_table} i WHERE {where} GROUP BY source_id", params
                    )
                ),
                pending_symbols=sum(v for k, v in states.items() if k != "READY"),
                oldest_15m=oldest,
                latest_15m=newest,
                instruments=count,
                last_commit=self.meta("last_commit", conn=conn),
                calendar_last=max(self.meta("sessions", [], conn=conn), default=None),
                universe=self.meta("universe", {}, conn=conn),
                calendar=self.meta("calendar_check", {}, conn=conn),
                refresh=dict(generation=self.meta("generation", 0, conn=conn), target_15m=target,
                             latest_completed15=saved, refreshed=refreshed, expected=expected,
                             updated_at=self.meta("last_commit", conn=conn),
                             updating=expected == 0 or refreshed < expected),
                asset_counts=dict(conn.execute(f"SELECT source_type,count(*) FROM {self.instrument_table} i WHERE {where} GROUP BY source_type", params)),
                history_queue_count=conn.execute(
                    f"SELECT count(*) FROM {self.instrument_table} i LEFT JOIN {self.progress_table} p USING(symbol) "
                    f"WHERE {where} AND i.source_id=? AND (coalesce(p.raw_count,0)<? OR p.stop_reason='GAP')",
                    (*params, self.source, history_capacity()),
                ).fetchone()[0],
            )

    def prices(self, day):
        """One batch, saved raw completed closes only, as of the selected day."""
        with closing(reader(self.path)) as conn:
            conn.execute("BEGIN")
            return [dict(r, price_basis="RAW", volume_unit="shares") for r in conn.execute(
                "SELECT b.symbol,i.name,i.source_type asset_type,i.raw_tick,b.close price,b.bar_end,"
                f"b.source_id,i.factor_revision FROM {self.instrument_table} i CROSS JOIN {self.bar_table} b ON b.symbol=i.symbol "
                f"AND b.bar_end=(SELECT max(x.bar_end) FROM {self.bar_table} x WHERE x.symbol=i.symbol AND x.bar_end<=?) "
                "WHERE "
                f"substr(b.bar_end,1,10)=? AND b.source_count={self.source_count}",
                (day + " 15:00:00", day),
            )]

    def inventory(self, symbol):
        with closing(reader(self.path)) as conn:
            return [
                dict(timeframe=tf, row_count=n, earliest=a, latest=b, capacity=cap)
                for tf, table, cap in (("15m", "bar15", history_capacity()), ("5m_tail", "tail5", 2))
                for n, a, b in [
                    conn.execute(
                        f"SELECT count(*),min(bar_end),max(bar_end) FROM {table} WHERE symbol=?",
                        (symbol,),
                    ).fetchone()
                ]
            ]

    def close(self):
        if self.owns_connection:
            self.conn.close()
            self.lease.close()
