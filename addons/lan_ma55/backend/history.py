"""Low-priority, non-destructive 5m history windows for the existing SDK owner."""

from datetime import timedelta
from time import time

from .market_grid import history_capacity, points, wall

WINDOW_SESSIONS = 18  # <=864 genuine 5m slots, below the existing 1200 response count.


def history_task(store, master, sessions, target, demanded=()):
    candidates = store.history_candidates(target)
    candidates.sort(key=lambda r: (r["symbol"] not in demanded, r.get("scanned_before") or "9999", r["symbol"]))
    for progress in candidates:
        if progress["next_retry"] > time():
            continue
        symbol = progress["symbol"]
        info = master.get(symbol)
        if not info:
            continue
        raw = store.raw_rows(symbol, limit=history_capacity())
        actual = {b["timestamp"] for b in raw}
        expected = points(sessions, raw[0]["timestamp"], raw[-1]["timestamp"], step=15) if raw else []
        missing = next((t for t in reversed(expected) if t not in actual), None)
        if missing:
            return str(wall(missing) - timedelta(minutes=10)), missing, "HISTORY", [symbol]
        before = min(t for t in (progress.get("scanned_before"), raw[0]["timestamp"] if raw else target) if t)
        end = str(wall(before) - timedelta(seconds=1))
        days = [d for d in sessions if d <= end[:10]][-WINDOW_SESSIONS:]
        listing = info.get("listing_date")
        days = [d for d in days if not listing or d >= listing]
        if not days:
            with store.transaction():
                store.conn.execute(
                    "INSERT INTO ma_history_progress(symbol,scanned_before,stop_reason,next_retry) VALUES(?,?,?,?) "
                    "ON CONFLICT(symbol) DO UPDATE SET stop_reason=excluded.stop_reason,next_retry=excluded.next_retry",
                    (symbol, before, "INSUFFICIENT" if listing else "CALENDAR_LIMIT", time() + 86400),
                )
            continue
        return days[0] + " 09:35:00", end, "HISTORY", [symbol]
    return None


def history_outcome(info, received, start):
    if info.get("listing_date") and start[:10] <= info["listing_date"]:
        return "INSUFFICIENT"
    return None
