"""One connection commits raw market input, frozen analysis and receipts together."""

from .market_store import Store
from .shared_store import SharedStore, source_receipt
from .worker_lease import ProcessLease
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
    ):
        began = perf_counter()
        self.conn.execute("BEGIN IMMEDIATE")
        self.last_lock_wait_ms = (perf_counter() - began) * 1000
        try:
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
            changed = self.analyze([symbol], sessions, end, now, live_after)
            if request_counts is not None:
                self.market.set_meta("request_counts", request_counts)
            self.conn.commit()
            return changed
        except Exception:
            self.conn.rollback()
            raise

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
        pending = [
            s
            for s, r in self.market.universe().items()
            if r["eligible"] and receipts.get(s) != source_receipt(r, target)
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
