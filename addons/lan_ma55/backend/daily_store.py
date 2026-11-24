"""Raw daily history and its request watermarks in the shared MA55 SQLite."""

from collections import defaultdict
import json
from time import time

from .market_grid import FIELDS, stamp
from .multiframe_input import DAILY_HISTORY, DAILY_SOURCE


class DailyStore:
    def __init__(self, connection):
        self.conn = connection
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS ma55_daily_bar_v1(
                symbol TEXT, bar_end TEXT, open REAL, high REAL, low REAL, close REAL,
                volume REAL, amount REAL, revision INTEGER NOT NULL DEFAULT 1, PRIMARY KEY(symbol, bar_end));
            CREATE TABLE IF NOT EXISTS ma55_daily_progress_v1(
                symbol TEXT PRIMARY KEY, target TEXT, revision INTEGER NOT NULL,
                first_gap TEXT, next_retry REAL NOT NULL, last_error TEXT);
        """)

    def raw_rows(self, symbol, target, *, start=""):
        return [
            dict(row, timestamp=row["bar_end"], source_id=DAILY_SOURCE, source=DAILY_SOURCE,
                 source_count=1, complete=True)
            for row in self.conn.execute(
                "SELECT * FROM ma55_daily_bar_v1 WHERE symbol=? AND bar_end BETWEEN ? AND ? ORDER BY bar_end",
                (symbol, start, target),
            )
        ]

    def progress(self, symbol):
        row = self.conn.execute("SELECT * FROM ma55_daily_progress_v1 WHERE symbol=?", (symbol,)).fetchone()
        return dict(row) if row else {}

    def frame_revision(self, symbol, target):
        return self.conn.execute(
            "SELECT coalesce(sum(revision),0) FROM ma55_daily_bar_v1 WHERE symbol=? AND bar_end<=?",
            (symbol, target),
        ).fetchone()[0]

    def task(self, master, schedule):
        if not schedule or not master:
            return None
        target = schedule[-1]
        history = schedule[-DAILY_HISTORY:]
        progress = {
            row["symbol"]: row for row in self.conn.execute(
                "SELECT * FROM ma55_daily_progress_v1 JOIN json_each(?) ON value=symbol",
                (json.dumps(list(master)),),
            )
        }
        groups = defaultdict(list)
        now = time()
        for symbol, info in master.items():
            if not info["eligible"]:
                continue
            prior = progress.get(symbol)
            if prior and prior["target"] == target:
                if prior["first_gap"] and prior["next_retry"] <= now and prior["first_gap"] >= history[0]:
                    gap = prior["first_gap"]
                    groups[(False, gap, gap, 1)].append(symbol)
                continue
            # Use the available window for new members. Existing members only
            # fetch the new dates or their earliest still-missing date.
            need_from = max(history[0], stamp(info["listing_date"], 900)) if info["listing_date"] else history[0]
            if prior and prior["target"] < target:
                need_from = next(at for at in history if at > prior["target"])
            count = sum(need_from <= at <= target for at in history)
            if count:
                groups[(count > 64, need_from, target, count)].append(symbol)
        if not groups:
            return None
        cold, start, end, count = min(groups, key=lambda key: (key[0], key[2] != target, key[1]))
        return start, end, count, groups[(cold, start, end, count)][:16 if cold else 100], cold

    def accept(self, symbols, received, errors, schedule, master, *, retry=300):
        if not schedule:
            return
        target, earliest = schedule[-1], schedule[-DAILY_HISTORY:][0]
        sessions = set(schedule)
        with self.conn:
            for symbol in symbols:
                existing = self.progress(symbol)
                changed = 0
                for bar in received.get(symbol, {}).values():
                    if bar["timestamp"] not in sessions or bar["source_id"] != DAILY_SOURCE:
                        continue
                    values = tuple(bar.get(k) for k in FIELDS)
                    old = self.conn.execute(
                        "SELECT open,high,low,close,volume,amount FROM ma55_daily_bar_v1 WHERE symbol=? AND bar_end=?",
                        (symbol, bar["timestamp"]),
                    ).fetchone()
                    if old is not None and tuple(old) == values:
                        continue
                    update = ",".join(f"{field}=excluded.{field}" for field in FIELDS)
                    self.conn.execute(
                        "INSERT INTO ma55_daily_bar_v1(symbol,bar_end,open,high,low,close,volume,amount) VALUES (?,?,?,?,?,?,?,?) "
                        f"ON CONFLICT(symbol,bar_end) DO UPDATE SET {update},revision=ma55_daily_bar_v1.revision+1",
                        (symbol, bar["timestamp"], *values),
                    )
                    changed += 1
                listing = master[symbol]["listing_date"]
                actual = {r[0] for r in self.conn.execute(
                    "SELECT bar_end FROM ma55_daily_bar_v1 WHERE symbol=? AND bar_end BETWEEN ? AND ?",
                    (symbol, earliest, target),
                )}
                gap = next((at for at in schedule if at >= earliest and (not listing or at[:10] >= listing)
                            and at not in actual), None)
                self.conn.execute("INSERT OR REPLACE INTO ma55_daily_progress_v1 VALUES (?,?,?,?,?,?)",
                                  (symbol, target, existing.get("revision", 0) + changed, gap, time() + retry,
                                   errors.get(symbol) if gap else None))
