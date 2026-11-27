"""Independent SQLite cache for observed sessions, local settings and event cooldowns."""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

from .energy import valid
from .providers import Day
from .settings import Settings

SCHEMA = '''
CREATE TABLE IF NOT EXISTS minute (
 symbol TEXT NOT NULL, day TEXT NOT NULL, source TEXT NOT NULL,
 k INTEGER NOT NULL CHECK(k BETWEEN 0 AND 239), amount REAL NOT NULL CHECK(amount>=0), price REAL,
 PRIMARY KEY(symbol, day, source, k));
CREATE TABLE IF NOT EXISTS auction (
 symbol TEXT NOT NULL, day TEXT NOT NULL, source TEXT NOT NULL, amount REAL NOT NULL CHECK(amount>=0),
 PRIMARY KEY(symbol, day, source));
CREATE TABLE IF NOT EXISTS event (
 id INTEGER PRIMARY KEY, day TEXT NOT NULL, symbol TEXT NOT NULL, direction TEXT NOT NULL,
 stamp TEXT NOT NULL, k INTEGER NOT NULL, multiple REAL NOT NULL, slope REAL NOT NULL,
 UNIQUE(day,symbol,direction,stamp));
CREATE TABLE IF NOT EXISTS verified_sina (
 day TEXT NOT NULL, symbol TEXT NOT NULL, agrees INTEGER NOT NULL,
 PRIMARY KEY(day,symbol));
CREATE TABLE IF NOT EXISTS settings (
 id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL,
 request_id TEXT NOT NULL, payload TEXT NOT NULL);
'''


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript(SCHEMA)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=3)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def save(self, day: Day, completed: int = 239) -> bool:
        """Merge closed rows. A thinner response cannot replace observations it overlaps."""
        if not -1 <= completed <= 239:
            raise ValueError('invalid completion cutoff')
        incoming = {k: v for k, v in day.amounts.items() if 0 <= k <= completed and valid(v)}
        ordered = [incoming[k] for k in sorted(incoming)]
        if any(b < a for a, b in zip(ordered, ordered[1:])) or (
                len(ordered) >= 5 and ordered[0] == ordered[-1]):
            raise ValueError('累计成交额倒退或整段停滞')
        key = (day.symbol, day.date, day.source)
        with self.connect() as db:
            existing = self._load(db, *key)
            covers_saved = existing.amounts.keys() <= incoming.keys()
            if not covers_saved:
                incoming = {k: v for k, v in incoming.items() if k not in existing.amounts}
            merged = existing.amounts | incoming
            auction = day.auction if valid(day.auction) and (covers_saved or existing.auction is None) else existing.auction
            values = [merged[k] for k in sorted(merged)]
            if valid(auction):
                values.insert(0, auction)
            if any(b < a for a, b in zip(values, values[1:])):
                raise ValueError('补拉分时与已保存的累计额不连续')
            changes = [
                (*key, k, amount, day.prices.get(k, existing.prices.get(k)))
                for k, amount in incoming.items()
                if amount != existing.amounts.get(k) or
                (k in day.prices and day.prices[k] != existing.prices.get(k))
            ]
            if auction != existing.auction:
                db.execute('INSERT OR REPLACE INTO auction VALUES(?,?,?,?)', (*key, auction))
            db.executemany('INSERT OR REPLACE INTO minute VALUES(?,?,?,?,?,?)', changes)
            return bool(changes) or auction != existing.auction

    @staticmethod
    def _load(db, symbol, day, source):
        key = (symbol, day, source)
        rows = db.execute('SELECT k, amount, price FROM minute WHERE symbol=? AND day=? AND source=? ORDER BY k',
                          key).fetchall()
        auc = db.execute('SELECT amount FROM auction WHERE symbol=? AND day=? AND source=?', key).fetchone()
        return Day(symbol, day, source, auc['amount'] if auc else None,
                   {r['k']: r['amount'] for r in rows},
                   {r['k']: r['price'] for r in rows if r['price'] is not None})

    def load(self, symbol, day, source):
        with self.connect() as db:
            return self._load(db, symbol, day, source)

    def available_days(self):
        with self.connect() as db:
            return {row['day'] for row in db.execute('SELECT DISTINCT day FROM minute')}

    def retain(self, confirmed_sessions, limit=30):
        dates = sorted(set(confirmed_sessions))
        if len(dates) <= limit:
            return
        cutoff = dates[-limit]
        with self.connect() as db:
            for table in ('minute', 'auction', 'event', 'verified_sina'):
                db.execute(f'DELETE FROM {table} WHERE day < ?', (cutoff,))

    def record_sina_close(self, day, comparisons):
        with self.connect() as db:
            db.executemany('INSERT OR REPLACE INTO verified_sina VALUES(?,?,?)',
                           [(day, symbol, agrees) for symbol, agrees in comparisons.items()])

    def has_sina_check(self, day):
        with self.connect() as db:
            return db.execute('SELECT 1 FROM verified_sina WHERE day=? LIMIT 1', (day,)).fetchone() is not None

    def sina_verified(self, previous_sessions):
        if len(previous_sessions) < 2:
            return set()
        with self.connect() as db:
            return {row['symbol'] for row in db.execute(
                'SELECT symbol FROM verified_sina WHERE day IN (?,?) AND agrees=1 '
                'GROUP BY symbol HAVING count(*)=2', previous_sessions[:2])}

    def load_settings(self):
        with self.connect() as db:
            row = db.execute('SELECT * FROM settings WHERE id=1').fetchone()
        if row is None:
            return Settings(), 0, None
        return Settings(**json.loads(row['payload'])), row['revision'], row['request_id']

    def save_settings(self, values, revision, request_id):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO settings VALUES(1,?,?,?)',
                       (revision, request_id, json.dumps(values.public(), allow_nan=False)))

    def record_event(self, day, symbol, direction, stamp, metrics, cooldown=5):
        """Keep one cooldown per symbol and direction across process restarts."""
        if stamp.tzinfo is None or stamp.date().isoformat() != day:
            raise ValueError('event requires Beijing timestamp and matching session')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            last = db.execute('SELECT stamp FROM event WHERE day=? AND symbol=? AND direction=? '
                              'ORDER BY stamp DESC LIMIT 1', (day, symbol, direction)).fetchone()
            if last and stamp - datetime.fromisoformat(last['stamp']) < timedelta(minutes=cooldown):
                return False
            db.execute('INSERT INTO event(day,symbol,direction,stamp,k,multiple,slope) VALUES(?,?,?,?,?,?,?)',
                       (day, symbol, direction, stamp.isoformat(), metrics['minute_index'],
                        metrics['multiple'], metrics['slope']))
            return True

    def events(self, day, limit=500):
        with self.connect() as db:
            return [dict(row) for row in db.execute(
                'SELECT * FROM event WHERE day=? ORDER BY stamp DESC, id DESC LIMIT ?', (day, limit))]
