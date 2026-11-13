"""Native source partition in the existing owner DB; old five-minute facts stay put."""

import json
from math import isclose
from time import time

from .market_store import Store, BAR_COLUMNS
from .market_grid import FIELDS, points, history_capacity, STRATEGY_CAPACITY
from .native15_shadow import SOURCE, PROOF


NATIVE_DDL = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS native15_instrument_progress(
 symbol TEXT PRIMARY KEY,name TEXT,eligible INTEGER,source_type TEXT,raw_tick REAL,listing_date TEXT,
 listing_status TEXT,source_id TEXT,latest_completed15 TEXT,latest_source5 TEXT,
 revision INTEGER,basis_revision INTEGER,factor_revision INTEGER,factor_coverage TEXT,factors_day TEXT,
 state TEXT,gaps TEXT,transition TEXT,correction INTEGER,rebuild_from TEXT,attempted TEXT,
 next_retry REAL,updated_at TEXT,metadata_source TEXT);
CREATE TABLE IF NOT EXISTS native15_bar(
 symbol TEXT,bar_end TEXT,open REAL,high REAL,low REAL,close REAL,volume REAL,amount REAL,
 source_id TEXT,source_count INTEGER CHECK(source_count=1),PRIMARY KEY(symbol,bar_end));
CREATE TABLE IF NOT EXISTS native15_factor_event(
 symbol TEXT,ordinal INTEGER,day TEXT,ex_factor REAL CHECK(ex_factor>0),PRIMARY KEY(symbol,ordinal));
CREATE TABLE IF NOT EXISTS native15_history_progress(
 symbol TEXT PRIMARY KEY,scanned_before TEXT,stop_reason TEXT,next_retry REAL NOT NULL DEFAULT 0,
 raw_count INTEGER NOT NULL DEFAULT 0,earliest TEXT,first_gap TEXT);
"""


class NativeStore(Store):
    source = SOURCE
    source_count = 1
    schema = NATIVE_DDL
    instrument_table = "native15_instrument_progress"
    bar_table = "native15_bar"
    factor_table = "native15_factor_event"
    progress_table = "native15_history_progress"
    cache_table = "native15_chart_cache"
    meta_prefix = "native15."

    def __init__(self, path, *, connection):
        super().__init__(path, connection=connection)
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS native15_retry(
              symbol TEXT PRIMARY KEY,target TEXT,stage TEXT,error_type TEXT,attempts INTEGER DEFAULT 0,
              retry_after REAL,last_attempt TEXT,last_success TEXT,next_eligible REAL DEFAULT 0,
              enqueue_reason TEXT);
            CREATE TABLE IF NOT EXISTS native15_factor_retry(
              symbol TEXT PRIMARY KEY,attempts INTEGER NOT NULL DEFAULT 0,last_attempt TEXT,
              next_eligible REAL NOT NULL DEFAULT 0,error_type TEXT,verified_at TEXT);
        """)
        # Metadata/calendar are reusable evidence, never a claim of native bars.
        with self.transaction():
            if not self.meta("initialized"):
                self.conn.execute("INSERT OR IGNORE INTO native15_instrument_progress SELECT * FROM instrument_state")
                self.conn.execute("UPDATE native15_instrument_progress SET source_id=?,latest_completed15=NULL,"
                                  "latest_source5=NULL,revision=0,basis_revision=0,state='WARMUP',gaps='[]',"
                                  "transition='NATIVE15_INITIAL',correction=0,rebuild_from=NULL,attempted=NULL,next_retry=0",
                                  (SOURCE,))
                self.conn.execute("INSERT OR IGNORE INTO native15_factor_event SELECT * FROM factor_event")
                for key in ("sessions", "calendar_revision", "calendar_check", "metadata_day", "universe", "generation"):
                    row = self.conn.execute("SELECT payload FROM meta WHERE key=?", (key,)).fetchone()
                    if row:
                        value = json.loads(row[0])
                        self.set_meta(key, value)
                # QuantDash factors are not TickFlow evidence.
                self.conn.execute("UPDATE native15_instrument_progress SET factor_coverage='MISSING',factors_day=NULL "
                                  "WHERE symbol IN (SELECT symbol FROM instrument_state WHERE source_id!='TICKFLOW_RAW_5M')")
                self.set_meta("initialized", True)
            if self.meta("factor_retry_separated") != 2:
                # 0.9.3 wrote a success day and 24h market cooldown for None.
                self.conn.execute("UPDATE native15_instrument_progress SET factors_day=NULL "
                                  "WHERE factor_coverage!='EXPLICIT' AND NOT EXISTS(SELECT 1 FROM native15_factor_retry f "
                                  "WHERE f.symbol=native15_instrument_progress.symbol AND f.verified_at IS NOT NULL)")
                repair = "error_type='FACTOR_PENDING' OR (stage!='C' AND retry_after>=86400 AND symbol IN " \
                         "(SELECT symbol FROM native15_instrument_progress WHERE factor_coverage!='EXPLICIT'))"
                self.conn.execute("UPDATE native15_instrument_progress SET next_retry=0 "
                                  f"WHERE symbol IN (SELECT symbol FROM native15_retry WHERE {repair})")
                self.conn.execute("UPDATE native15_retry SET error_type=NULL,attempts=0,next_eligible=0,retry_after=0 "
                                  f"WHERE {repair}")
                self.set_meta("factor_retry_separated", 2)

    def factor_attempt(self, symbols, now):
        with self.transaction():
            self.conn.executemany("INSERT INTO native15_factor_retry(symbol,last_attempt,next_eligible) VALUES(?,?,?) "
                                  "ON CONFLICT(symbol) DO UPDATE SET last_attempt=excluded.last_attempt,"
                                  "next_eligible=excluded.next_eligible", [(s, now, time()+300) for s in symbols])

    def factor_failed(self, symbols, now, error, *, delay=0):
        with self.transaction():
            for symbol in symbols:
                row = self.conn.execute("SELECT attempts FROM native15_factor_retry WHERE symbol=?", (symbol,)).fetchone()
                attempts = (row[0] if row else 0)+1
                wait = max(delay, (300, 900, 1800, 3600, 7200, 21600)[min(attempts-1, 5)])
                self.conn.execute("INSERT INTO native15_factor_retry(symbol,attempts,last_attempt,next_eligible,error_type) "
                                  "VALUES(?,?,?,?,?) ON CONFLICT(symbol) DO UPDATE SET attempts=excluded.attempts,"
                                  "last_attempt=excluded.last_attempt,next_eligible=excluded.next_eligible,error_type=excluded.error_type",
                                  (symbol, attempts, now, time()+wait, error))

    def factor_response(self, symbol, events, now, sessions):
        info = self.instrument(symbol)
        if events is None:
            self.factor_failed([symbol], now, "MISSING_RESPONSE")
            changed = info["factor_coverage"] != "MISSING"
            info.update(factor_coverage="MISSING", revision=info["revision"]+int(changed))
        else:
            basis_changed = self.events(symbol) != events or info["factor_coverage"] != "EXPLICIT"
            changed = basis_changed or info["factors_day"] != now[:10]
            self._factors(symbol, events)
            info.update(factor_coverage="EXPLICIT", factors_day=now[:10],
                        factor_revision=info["factor_revision"]+int(basis_changed),
                        basis_revision=info["basis_revision"]+int(basis_changed), revision=info["revision"]+int(changed))
            self.conn.execute("INSERT INTO native15_factor_retry(symbol,verified_at) VALUES(?,?) "
                              "ON CONFLICT(symbol) DO UPDATE SET verified_at=excluded.verified_at,attempts=0,"
                              "error_type=NULL,next_eligible=0", (symbol, now))
        info["updated_at"] = now
        self._put(info)
        if changed:
            raw = self.raw_rows(symbol, limit=history_capacity())
            if raw:
                self.update_cache(symbol, info, raw, self.events(symbol), sessions, rebuild=True)
            self.set_meta("data_revision", self.meta("data_revision", 0)+1)
        return changed

    def _write(self, symbol, bars):
        # Native history is retained; computational reads remain bounded.
        update = ",".join(f"{k}=excluded.{k}" for k in (*FIELDS, "source_id", "source_count"))
        self.conn.executemany(f"INSERT INTO native15_bar VALUES({','.join('?' for _ in BAR_COLUMNS)}) "
                              f"ON CONFLICT(symbol,bar_end) DO UPDATE SET {update}",
                              [(symbol, b["timestamp"], *(b.get(k) for k in FIELDS), SOURCE, b["source_count"]) for b in bars])
        self.conn.execute("INSERT INTO native15_history_progress(symbol,raw_count,earliest) "
                          "SELECT ?,count(*),min(bar_end) FROM native15_bar WHERE symbol=? "
                          "ON CONFLICT(symbol) DO UPDATE SET raw_count=excluded.raw_count,earliest=excluded.earliest", (symbol, symbol))

    def attempt(self, symbols, target, now, stage):
        with self.transaction():
            self.conn.executemany("INSERT INTO native15_retry(symbol,target,stage,last_attempt,enqueue_reason) VALUES(?,?,?,?,?) "
                                  "ON CONFLICT(symbol) DO UPDATE SET target=excluded.target,stage=excluded.stage,"
                                  "last_attempt=excluded.last_attempt,enqueue_reason=excluded.enqueue_reason",
                                  [(s, target, stage, now, stage) for s in symbols])

    def failed(self, symbols, end, now, message, *, stage="A", delay=300):
        with self.transaction():
            for symbol in symbols:
                attempts = self.conn.execute("SELECT attempts FROM native15_retry WHERE symbol=?", (symbol,)).fetchone()
                wait = max(delay, 1800 if attempts and attempts[0] >= 3 else 0)
                self.conn.execute("INSERT INTO native15_retry(symbol,target,stage,error_type,attempts,retry_after,last_attempt,next_eligible,enqueue_reason) "
                                  "VALUES(?,?,?,?,1,?,?,?,?) ON CONFLICT(symbol) DO UPDATE SET "
                                  "target=excluded.target,stage=excluded.stage,error_type=excluded.error_type,attempts=attempts+1,"
                                  "retry_after=excluded.retry_after,last_attempt=excluded.last_attempt,next_eligible=excluded.next_eligible",
                                  (symbol, end, stage, message, wait, now, time()+wait, stage))
                info = self.instrument(symbol)
                info.update(attempted=end, next_retry=time()+wait, updated_at=now)
                if stage != "C":
                    info["state"] = message
                self._put(info)

    def accept(self, symbol, received, *, sessions, start, end, now, events=None,
               factor_response=False, rebuild=False, stage="A"):
        if factor_response:
            self.factor_response(symbol, events, now, sessions)
        info = self.instrument(symbol)
        incoming = [b for t, b in sorted(received.items()) if start <= t <= end]
        if any(b.get("source_id") != SOURCE or b.get("source_count") != 1 or b.get("source_proof") != PROOF for b in incoming):
            raise ValueError("NATIVE15_PROVENANCE_REQUIRED")
        if any(b["timestamp"] not in points(sessions, start, end, step=15) for b in incoming):
            raise ValueError("NATIVE15_NON_TRADING_POINT")
        if not incoming:
            self.failed([symbol], end, now, "EMPTY_NATIVE15", stage=stage)
            return info
        old = self.raw_rows(symbol, limit=history_capacity())
        prior = {b["timestamp"]: b for b in old}
        repaired = bool(old and any(b["timestamp"] not in prior and old[-min(len(old), STRATEGY_CAPACITY)]["timestamp"] <= b["timestamp"] <= old[-1]["timestamp"] for b in incoming))
        correction = any(any(not isclose(b[k], prior[b["timestamp"]][k], rel_tol=1e-12, abs_tol=1e-9)
                             if b.get(k) is not None and prior[b["timestamp"]].get(k) is not None
                             else b.get(k) != prior[b["timestamp"]].get(k) for k in FIELDS)
                         for b in incoming if b["timestamp"] in prior)
        info["basis_revision"] += int(correction or repaired)
        self._write(symbol, incoming)
        raw = self.raw_rows(symbol, limit=history_capacity())
        actual = {b["timestamp"] for b in raw}
        # Only the causal strategy window blocks candidates, not deeper MA233 history.
        tail = raw[-STRATEGY_CAPACITY:]
        gaps = [t for t in points(sessions, tail[0]["timestamp"], max(end, raw[-1]["timestamp"]), step=15) if t not in actual]
        contiguous = 0
        expected = points(sessions, raw[0]["timestamp"], raw[-1]["timestamp"], step=15)
        history_gaps = [t for t in expected if t not in actual]
        for t in reversed(expected):
            if t not in actual:
                break
            contiguous += 1
        state = "READY" if contiguous >= 56 else "GAP" if gaps else "WARMUP"
        modified = correction or any(t not in prior for t in received) or info["state"] != state
        info.update(state=state, source_id=SOURCE, latest_completed15=raw[-1]["timestamp"],
                    latest_source5=None, correction=correction, gaps=gaps[-250:],
                    rebuild_from=gaps[-1] if gaps and contiguous < 56 else None,
                    revision=info["revision"]+int(modified), attempted=end, next_retry=0,
                    updated_at=now, transition="NATIVE15_REVIEW_INPUT")
        self._put(info)
        self.conn.execute("UPDATE native15_history_progress SET first_gap=?,stop_reason=CASE WHEN ? IS NOT NULL THEN 'GAP' "
                          "WHEN stop_reason='GAP' THEN NULL ELSE stop_reason END WHERE symbol=?",
                          (history_gaps[-1] if history_gaps else None, history_gaps[-1] if history_gaps else None, symbol))
        self.update_cache(symbol, info, raw, self.events(symbol), sessions,
                          rebuild=correction or stage == "C", append_only=True)
        self.set_meta("data_revision", self.meta("data_revision", 0)+int(modified))
        self.set_meta("last_commit", now)
        self.set_meta("last_success_"+("history" if stage == "C" else "live"), dict(at=now, stage=stage, symbol=symbol))
        if state == "READY" and raw[-1]["timestamp"] >= end:
            self.conn.execute("UPDATE native15_retry SET error_type=NULL,attempts=0,retry_after=0,last_success=?,next_eligible=0 WHERE symbol=?", (now, symbol))
        else:
            self.conn.execute("UPDATE native15_retry SET last_success=? WHERE symbol=?", (now, symbol))
            self.failed([symbol], end, now, "SOURCE_LAG" if raw[-1]["timestamp"] < end else state, stage=stage)
        return info

    def read(self, *args, **kwargs):
        result = super().read(*args, **kwargs)
        for bar in result["bars"]:
            bar["source_proof"] = PROOF if result["timeframe"] == "15m" else "DERIVED_CONTIGUOUS_NATIVE15"
        result.update(input_contract="native_15m_completed", signal_family="NATIVE15_REVIEW_V1")
        return result

    def inventory(self, symbol):
        from contextlib import closing
        from .market_store import reader
        with closing(reader(self.path)) as conn:
            rows = conn.execute("SELECT count(*),min(bar_end),max(bar_end) FROM native15_bar WHERE symbol=?", (symbol,)).fetchone()
        return [dict(timeframe="15m", source_id=SOURCE, row_count=rows[0], earliest=rows[1], latest=rows[2], capacity=None)]
