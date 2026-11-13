"""A target catch-up > B causal warmup > C chart history, one persistent fair queue."""

from datetime import timedelta
from time import time

from .market_grid import points, wall, history_capacity


def task(store, master, sessions, target, now, demanded=()):
    grid = points(sessions, sessions[0], target, step=15) if sessions else []
    if not grid:
        return None
    warm_start = grid[max(0, len(grid)-64)]
    retries = {r["symbol"]: dict(r) for r in store.conn.execute("SELECT * FROM native15_retry")}
    groups = {}
    for symbol, info in master.items():
        if not info["eligible"] and symbol not in demanded:
            continue
        retry = retries.get(symbol, {})
        latest = info["latest_completed15"]
        progress = store.history_progress(symbol)
        ready = latest and info["state"] not in ("WARMUP", "GAP")
        stage = "A" if ready and latest < target else "B" if not latest or info["state"] in ("WARMUP", "GAP", "FACTOR_PENDING") else "C"
        new_target_input = stage == "A" and retry.get("target", "") < target and retry.get("error_type") in ("FACTOR_PENDING", "WARMUP")
        if retry.get("next_eligible", 0) > time() and not new_target_input and (retry.get("stage") != "C" or stage == "C"):
            continue
        if stage == "A":
            next_points = points(sessions, latest, target, step=15)[1:]
            start = next_points[0] if next_points else target
            # Short downtime stays incremental; long downtime takes a bounded causal window.
            start, end = max(start, warm_start), target
        elif stage == "B":
            start, end = warm_start, target
        else:
            if progress.get("raw_count", 0) >= history_capacity() and not progress.get("first_gap"):
                continue
            if progress["next_retry"] > time():
                continue
            if progress.get("first_gap"):
                key = (stage, progress["first_gap"], progress["first_gap"])
                groups.setdefault(key, []).append((retry.get("last_attempt") or "", symbol not in demanded, symbol))
                continue
            earliest = progress.get("earliest") or latest
            before = min(earliest, progress.get("scanned_before") or earliest)
            end = str(wall(before)-timedelta(seconds=1))
            older = [t for t in grid if t <= end and (not info["listing_date"] or t[:10] >= info["listing_date"])]
            if not older:
                with store.transaction():
                    store.conn.execute("UPDATE native15_history_progress SET stop_reason=?,next_retry=? WHERE symbol=?",
                                       ("INSUFFICIENT" if info["listing_date"] else "CALENDAR_LIMIT", time()+86400, symbol))
                continue
            start = older[max(0, len(older)-1280)]
        key = (stage, start, end)
        groups.setdefault(key, []).append((retry.get("last_attempt") or "", symbol not in demanded, symbol))
    if not groups:
        return None
    # Oldest persisted last_attempt wins within each priority; no code-prefix starvation.
    key = min(groups, key=lambda k: (k[0], min(groups[k])))
    stage, start, end = key
    return start, end, stage, [r[2] for r in sorted(groups[key])[:100]]
