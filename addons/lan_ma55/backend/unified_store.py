"""One connection commits raw market input, frozen analysis and receipts together."""

from .market_store import Store, encoded, reader
from .market_grid import SOURCE
from .engine import RULE_VERSION
from .shared_store import SharedStore, source_receipt
from .worker_lease import ProcessLease
from contextlib import closing
import json
from pathlib import Path
from time import perf_counter


class UnifiedStore(SharedStore):
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.writer_lease = ProcessLease(Path(path).with_suffix(".writer.lock"))
        try:
            super().__init__(path)
        except Exception:
            self.writer_lease.close()
            raise
        self.market = Store(path, connection=self.conn)
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS schema_migration(version INTEGER PRIMARY KEY);
            INSERT OR IGNORE INTO schema_migration VALUES(1);
            CREATE TABLE IF NOT EXISTS processing_receipt(
                symbol TEXT PRIMARY KEY,source_asof TEXT,analysis_asof TEXT,
                source_revision INTEGER,updated_at TEXT);
            CREATE INDEX IF NOT EXISTS event_symbol_time ON signal_event(symbol,confirmed_at,event_id);
            CREATE TABLE IF NOT EXISTS request_receipt(
                id INTEGER PRIMARY KEY,at TEXT,endpoint TEXT,capability TEXT,
                status INTEGER,latency_ms REAL,symbol_count INTEGER,stage TEXT,retries INTEGER);
        """)
        with self.conn:
            self.set_meta("data_mode", "UNIFIED_SQLITE_V1")

    def refresh_master(self, rows, overview, sessions):
        with self.conn:
            self.conn.execute("BEGIN IMMEDIATE")
            self.market.save_master(rows, overview, sessions)
            self.source_master(rows, overview, self.market.meta("generation", 1))
            self.set_meta("sessions", self.market.meta("sessions", []))

    def accept_market(
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
        error=None,
        live_after,
        request_counts=None,
        operation=None,
    ):
        began = perf_counter()
        self.conn.execute("BEGIN IMMEDIATE")
        self.last_lock_wait_ms = (perf_counter() - began) * 1000
        try:
            if operation:
                operation("AGGREGATING_15M")
            self.market.accept(
                symbol,
                received,
                sessions=sessions,
                start=start,
                end=end,
                now=now,
                events=events,
                factor_response=factor_response,
                rebuild=rebuild,
            )
            if error:
                self.market.failed([symbol], end, now, error)
            if operation:
                operation("CALCULATING_MA55")
            changed = self.analyze([symbol], sessions, end, now, live_after)
            if request_counts is not None:
                self.market.set_meta("request_counts", request_counts)
            self.conn.commit()
            return changed
        except Exception:
            self.conn.rollback()
            raise
        finally:
            if operation:
                operation("IDLE")

    def status_snapshot(self, *, scope=None, target=None):
        """One RO transaction: saved bars and actual successful analysis differ."""
        with closing(reader(self.market.path)) as conn:
            conn.execute("BEGIN")
            result = self.market.snapshot(scope=scope, target=target, connection=conn)
            target = result["refresh"]["target_15m"]
            where, params = "i.eligible=1 AND i.source_id=?", (SOURCE,)
            if scope is not None:
                where += " AND i.symbol IN (SELECT value FROM json_each(?))"
                params += (encoded(scope),)
            # A processing receipt also exists for blocked attempts. Only a
            # current-generation cycle with valid/insufficient causal output and
            # corroborated saved input proves that the frozen algorithm ran.
            counts = conn.execute(
                "SELECT count(*),max(p.analysis_asof),max(p.updated_at) "
                "FROM instrument_state i JOIN processing_receipt p USING(symbol) "
                "JOIN symbol_state s USING(symbol) "
                f"WHERE {where} AND s.category IN ('valid','insufficient') "
                "AND p.analysis_asof=s.data_asof AND p.analysis_asof=? AND s.last_cycle=? "
                "AND EXISTS(SELECT 1 FROM bar15 b WHERE b.symbol=i.symbol AND b.bar_end=? "
                "AND b.source_id=? AND b.source_count=3)",
                (*params, target, f"PRO:{RULE_VERSION}:{result['generation']}:{target}", target, SOURCE),
            ).fetchone()
            latest = conn.execute(
                "SELECT max(p.analysis_asof) FROM instrument_state i "
                "JOIN processing_receipt p USING(symbol) JOIN symbol_state s USING(symbol) "
                f"WHERE {where} AND p.analysis_asof=s.data_asof",
                params,
            ).fetchone()[0]
            result["analysis"] = dict(target_15m=target, generation=result['generation'],
                                      calculated=counts[0], expected=result['refresh']['expected'],
                                      latest_completed15=latest, updated_at=counts[2])
            saved = result['refresh']['latest_completed15']
            day = saved[:10] if saved else None
            result.update(market_date=day, latest_buy_date=day,
                          latest_day_buy_count=conn.execute(
                              "SELECT count(*) FROM signal_event WHERE confirmed_at>=? AND confirmed_at<=?",
                              (day + " 00:00:00", day + " 23:59:59"),
                          ).fetchone()[0] if day else None)
            pending = conn.execute("SELECT payload FROM runtime_meta WHERE key='pending_publish'").fetchone()
            result['pending_publish'] = bool(pending and json.loads(pending[0]))
            return result

    def analyze(self, symbols, sessions, target, now, live_after):
        """May run inside accept_market, or in bounded restart catch-up batches."""
        sources = [
            self.market.read(s, "15m", target, connection=self.conn, strategy=True) for s in symbols
        ]
        changed = self.consume(
            sources,
            sessions=sessions,
            target=target,
            now=now,
            live_after=live_after,
            commit=False,
        )
        for source in sources:
            if source["symbol"] in changed:
                saved = self.conn.execute(
                    "SELECT data_asof FROM symbol_state WHERE symbol=?",
                    (source["symbol"],),
                ).fetchone()
                self.conn.execute(
                    """INSERT INTO processing_receipt VALUES(?,?,?,?,?)
                    ON CONFLICT(symbol) DO UPDATE SET source_asof=excluded.source_asof,
                    analysis_asof=excluded.analysis_asof,source_revision=excluded.source_revision,
                    updated_at=excluded.updated_at""",
                    (
                        source["symbol"],
                        source.get("latest_completed_at"),
                        saved[0],
                        source.get("revision", 0),
                        now,
                    ),
                )
        return changed

    def catch_up(self, target, now, live_after):
        receipts = self.receipts()
        members = {r[0] for r in self.conn.execute("SELECT symbol FROM cycle_member WHERE cycle_id=?", (self.identity(target),))}
        pending = [
            s
            for s, r in self.market.universe().items()
            if r["eligible"] and (receipts.get(s) != source_receipt(r, target) or s not in members)
        ]
        return pending

    def checkpoint(self):
        return tuple(self.conn.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone())

    def save_requests(self, rows, stage):
        self.conn.executemany(
            """INSERT INTO request_receipt
            (at,endpoint,capability,status,latency_ms,symbol_count,stage,retries)
            VALUES(?,?,?,?,?,?,?,?)""",
            [
                (
                    r["at"],
                    r["endpoint"],
                    r["capability"],
                    r["status"],
                    r["latency_ms"],
                    r["symbol_count"],
                    stage,
                    r["retries"],
                )
                for r in rows
            ],
        )
        self.conn.execute(
            "DELETE FROM request_receipt WHERE id < (SELECT max(id)-10000 FROM request_receipt)"
        )

    def close(self):
        try:
            super().close()
        finally:
            self.writer_lease.close()
