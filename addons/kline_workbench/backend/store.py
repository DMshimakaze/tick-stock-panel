"""One durable raw-fact store; calendar retention and restartable missing ranges."""
import json
import sqlite3
import time

from .market import window

TABLES = {'5m': 'bar_5m', '1m': 'bar_1m', '1d': 'bar_daily'}
MAX_TRACKED = 50
TTL_SECONDS = 7 * 86400


class Store:
    def __init__(self, path):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute('PRAGMA journal_mode=WAL')
        self.conn.executescript('''
          CREATE TABLE IF NOT EXISTS tracked_symbol(
            symbol TEXT PRIMARY KEY, visited REAL NOT NULL, pinned INTEGER NOT NULL,
            one_minute INTEGER NOT NULL, metadata TEXT, events TEXT, factor_day TEXT);
          CREATE TABLE IF NOT EXISTS symbol_cursor(
            symbol TEXT, period TEXT, earliest TEXT, latest TEXT, through TEXT, gaps INTEGER,
            gap_start TEXT, gap_end TEXT, attempted_to TEXT, retry_at REAL DEFAULT 0,
            attempts INTEGER DEFAULT 0, last_request TEXT, PRIMARY KEY(symbol,period));
          CREATE TABLE IF NOT EXISTS runtime_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS request_audit(
            id INTEGER PRIMARY KEY, at TEXT, period TEXT, symbols TEXT, start TEXT, end TEXT,
            returned INTEGER, seconds REAL, http_requests INTEGER, kind TEXT);
        ''')
        for table in TABLES.values():
            self.conn.execute(f'''CREATE TABLE IF NOT EXISTS {table}(
              symbol TEXT, end_time TEXT, open REAL, high REAL, low REAL, close REAL,
              volume REAL, amount REAL, source TEXT NOT NULL DEFAULT 'QuantDash',
              basis TEXT NOT NULL DEFAULT 'RAW', PRIMARY KEY(symbol,end_time))''')
        self.conn.commit()

    def close(self):
        self.conn.close()

    def meta(self, key, default=None):
        row = self.conn.execute('SELECT value FROM runtime_meta WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_meta(self, key, value):
        self.conn.execute('INSERT OR REPLACE INTO runtime_meta VALUES (?,?)', (key, json.dumps(value, allow_nan=False)))

    def tracked(self):
        return [dict(r) for r in self.conn.execute('SELECT * FROM tracked_symbol ORDER BY visited DESC')]

    def remove(self, symbol):
        for table in [*TABLES.values(), 'tracked_symbol', 'symbol_cursor']:
            self.conn.execute(f'DELETE FROM {table} WHERE symbol=?', (symbol,))

    def track(self, symbol, *, pinned, one_minute, now=None):
        now = time.time() if now is None else now
        with self.conn:
            for row in self.tracked():
                if not row['pinned'] and now-row['visited'] > TTL_SECONDS:
                    self.remove(row['symbol'])
            existing = self.conn.execute('SELECT symbol FROM tracked_symbol WHERE symbol=?', (symbol,)).fetchone()
            if not existing and len(self.tracked()) >= MAX_TRACKED:
                oldest = self.conn.execute('SELECT symbol FROM tracked_symbol WHERE pinned=0 ORDER BY visited LIMIT 1').fetchone()
                if not oldest:
                    raise ValueError('50只固定关注已满，请先取消固定')
                self.remove(oldest[0])
            self.conn.execute('''INSERT INTO tracked_symbol(symbol,visited,pinned,one_minute) VALUES (?,?,?,?)
              ON CONFLICT(symbol) DO UPDATE SET visited=excluded.visited,
              pinned=CASE WHEN ? IS NULL THEN tracked_symbol.pinned ELSE excluded.pinned END,
              one_minute=MAX(one_minute,excluded.one_minute)''', (symbol, now, int(bool(pinned)), int(one_minute), pinned))

    def metadata(self, symbol, metadata, events, day):
        with self.conn:
            self.conn.execute('UPDATE tracked_symbol SET metadata=?,events=?,factor_day=? WHERE symbol=?',
                              (json.dumps(metadata), json.dumps(events), day, symbol))

    def cursor(self, symbol, period):
        row = self.conn.execute('SELECT * FROM symbol_cursor WHERE symbol=? AND period=?', (symbol, period)).fetchone()
        return dict(row) if row else None

    def rows(self, symbol, period):
        return [dict(r) for r in self.conn.execute(f'SELECT end_time,open,high,low,close,volume,amount FROM {TABLES[period]} WHERE symbol=? ORDER BY end_time', (symbol,))]

    def reconcile(self, symbol, period, sessions, now, *, attempted=None):
        days, expected = window(sessions, period, now)
        if not expected:
            return
        table = TABLES[period]
        self.conn.execute(f'DELETE FROM {table} WHERE symbol=? AND end_time<?', (symbol, days[0]))
        actual = [r['end_time'] for r in self.rows(symbol, period)]
        present = set(actual)
        missing = [t for t in expected if t not in present]
        index = expected.index(missing[0]) if missing else len(expected)
        through = expected[index-1] if index else None
        following = next((t for t in expected[index:] if t in present), None)
        gap_end = expected[expected.index(following)-1] if following else expected[-1]
        previous = self.cursor(symbol, period) or {}
        attempts = previous.get('attempts', 0)+1 if attempted and missing else previous.get('attempts', 0)
        retry = time.time()+min(3600, 300 * 2**min(attempts-1, 4)) if attempted and missing else previous.get('retry_at', 0)
        self.conn.execute('''INSERT OR REPLACE INTO symbol_cursor VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
                          (symbol, period, actual[0] if actual else None, actual[-1] if actual else None,
                           through, len(missing), missing[0] if missing else None, gap_end if missing else None,
                           attempted or previous.get('attempted_to'), retry if missing else 0, attempts if missing else 0, str(now) if attempted else previous.get('last_request')))

    def save(self, data, period, sessions, now, start, end, *, seconds, http_requests, kind, checkpoint):
        with self.conn:
            for symbol, rows in data.items():
                self.conn.executemany(f'''INSERT OR REPLACE INTO {TABLES[period]}
                  (symbol,end_time,open,high,low,close,volume,amount) VALUES (?,?,?,?,?,?,?,?)''',
                  [(symbol, t, *[r[k] for k in ('open','high','low','close','volume','amount')]) for t, r in rows.items()])
                self.reconcile(symbol, period, sessions, now, attempted=end)
            self.conn.execute('INSERT INTO request_audit(at,period,symbols,start,end,returned,seconds,http_requests,kind) VALUES (?,?,?,?,?,?,?,?,?)',
                              (str(now), period, ','.join(data), start, end, sum(map(len, data.values())), seconds, http_requests, kind))
            self.conn.execute('DELETE FROM request_audit WHERE id <= (SELECT MAX(id)-500 FROM request_audit)')
            self.set_meta('source_checkpoint', checkpoint)

    def overview(self):
        result = []
        for row in self.tracked():
            item = {k: row[k] for k in ('symbol','visited','pinned','one_minute')}
            item['periods'] = {}
            for period in TABLES:
                stats = self.conn.execute(f'SELECT COUNT(*) AS bars,COUNT(DISTINCT substr(end_time,1,10)) AS days,MIN(end_time) AS earliest,MAX(end_time) AS latest FROM {TABLES[period]} WHERE symbol=?', (row['symbol'],)).fetchone()
                item['periods'][period] = dict(stats, cursor=self.cursor(row['symbol'], period))
            result.append(item)
        return result
