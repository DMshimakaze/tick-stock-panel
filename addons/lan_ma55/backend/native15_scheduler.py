"""Prioritize completed targets, required 15m repairs and factor evidence."""

from bisect import bisect_right
from collections import defaultdict
from time import time

from .market_grid import points
from .multiframe_input import MAX_BATCH_SYMBOLS, MAX_NATIVE_BARS, MIN_HISTORY, window_batch
from .native15_store import bj_factor_deferred_until


def task(store, master, sessions, target, now, *, needs=None, grid=None, cold_allowed=True, factor_day=None):
    if grid is None:
        grid = points(sessions, sessions[0], target, step=15) if sessions else []
    if not grid:
        return None
    retries = {r["symbol"]: dict(r) for r in store.conn.execute("SELECT * FROM native15_retry")}
    factors = {r["symbol"]: dict(r) for r in store.conn.execute("SELECT * FROM native15_factor_retry")}
    groups = defaultdict(list)
    clock = time()
    factor_day = factor_day or target[:10]
    before_first_bar = factor_day > target[:10]
    warm_start = grid[max(0, len(grid) - MIN_HISTORY)]
    for symbol, info in master.items():
        if not info["eligible"] or (info["listing_date"] and info["listing_date"] > target[:10]):
            continue
        retry = retries.get(symbol, {})
        factor = factors.get(symbol, {})
        explicit = info["factor_coverage"] == "EXPLICIT"
        factor_due = max(factor.get("next_eligible", 0), bj_factor_deferred_until(symbol, info["factor_coverage"], factor))
        if ((explicit or not before_first_bar) and (not explicit or (info["factors_day"] or "") < factor_day)
                and factor_due <= clock):
            groups["F"].append((factor.get("last_attempt") or "", symbol, target, target))
        latest = info["latest_completed15"]
        if latest and latest < target:
            stage = "A"
            bounds = (max(warm_start, grid[bisect_right(grid, latest)]), target)
        elif not latest:
            stage, bounds = "B", (max(warm_start, info["listing_date"] or warm_start), target)
        else:
            required = (needs or {}).get((symbol, "15m"))
            if needs is None and info["rebuild_from"]:
                required = (warm_start, target)
            stage, bounds = "B", required
            if bounds is None and cold_allowed:
                other = [(needs or {}).get((symbol, tf)) for tf in ("30m", "60m", "120m")]
                missing = [window for window in other if window]
                if missing:
                    stage, bounds = "C", (min(w[0] for w in missing), max(w[1] for w in missing))
        if not bounds:
            continue
        if (retry.get("next_eligible", 0) > clock and retry.get("stage") == stage and (
                stage != "A" or (retry.get("target") or "") >= target)):
            continue
        start, end = bounds
        groups[stage].append((retry.get("last_attempt") or "", symbol, max(start, info["listing_date"] or start), end))
    if not groups:
        return None
    rank = {"A": 0, "F": 1, "B": 2 if before_first_bar else 1, "C": 3}
    stage = min(groups, key=lambda name: (rank[name], min(groups[name]), name))
    if stage == "F":
        return target, target, stage, [row[1] for row in sorted(groups[stage])[:MAX_BATCH_SYMBOLS]], 0
    batch = window_batch(grid, groups[stage], max_count=MAX_NATIVE_BARS, max_points=32000)
    if batch:
        start, end, count, symbols = batch
        return start, end, stage, symbols, count
