"""Source normalization and completed QFQ input for the research replay."""

from datetime import datetime
from itertools import takewhile
from math import isfinite

from .market_grid import BEIJING, FIELDS, combined, derived, project_bar, stamp, valid_five
from .native15_shadow import SOURCE

DAILY_SOURCE = "TICKFLOW_RAW_1D"
MIN_HISTORY = 150
DAILY_HISTORY = 1000


def normalize_daily(data, start, end):
    epochs = data["timestamp"]
    amounts = data.get("amount", [None] * len(epochs))
    if any(len(column) != len(epochs) for column in [amounts, *(data[k] for k in FIELDS[:5])]):
        raise ValueError("MISALIGNED_DAILY")
    rows = {}
    for i, epoch in enumerate(epochs):
        if type(epoch) not in (int, float) or not isfinite(epoch):
            raise ValueError("INVALID_DAILY_TIMESTAMP")
        source_at = datetime.fromtimestamp(epoch / 1000, BEIJING)
        day = source_at.date().isoformat()
        if day < start[:10] or day > end[:10]:
            continue
        if (source_at.hour, source_at.minute, source_at.second, source_at.microsecond) != (0, 0, 0, 0):
            raise ValueError("DAILY_TIMESTAMP_NOT_TRADING_DATE")
        bar = {k: data[k][i] for k in FIELDS[:5]}
        bar["amount"] = amounts[i]
        if not valid_five(bar):
            raise ValueError("INVALID_DAILY_OHLCV")
        bar["volume"] *= 100  # TickFlow compact history is in lots.
        at = stamp(day, 900)
        bar = combined([bar], at, DAILY_SOURCE, 1)
        if at in rows and rows[at] != bar:
            raise ValueError("CONFLICTING_DAILY_BAR")
        rows[at] = bar
    return rows


def frame_bars(raw, timeframe, sessions, weeks, target, events, basis_at):
    if not target:
        return []
    expected_source = DAILY_SOURCE if timeframe in ("daily", "weekly") else SOURCE
    trading_days = set(sessions)
    available = [
        bar for bar in raw if bar["timestamp"] <= target and bar["source_id"] == expected_source
        and bar["source_count"] == 1 and bar["complete"] and valid_five(bar)
        and bar["timestamp"][:10] in trading_days
    ]
    if timeframe.endswith("m"):
        return [project_bar(bar, events, basis_at) for bar in derived(available, timeframe, sessions, target)]
    daily = [project_bar(bar, events, basis_at) for bar in available]
    if timeframe == "daily":
        return daily
    by_day = {bar["timestamp"][:10]: bar for bar in daily}
    result = []
    for week in weeks:
        end = stamp(week[-1], 900)
        if end <= target and all(day in by_day and by_day[day]["volume"] > 0 for day in week):
            bars = [by_day[day] for day in week]
            last = bars[-1]
            result.append(dict(
                combined(bars, end, DAILY_SOURCE, len(week)),
                adjust_factor=last["adjust_factor"], price_basis=last["price_basis"],
                volume_unit="shares", valid=last["valid"],
            ))
    return result


def replay_input(bars, schedule, start):
    """A first successful replay pins its history origin. Later gaps block it."""
    if not schedule:
        return [], "CALENDAR_PENDING"
    by_end = {row["timestamp"]: row for row in bars if row["volume"] > 0}
    if schedule[-1] not in by_end:
        return [], "MISSING_COMPLETED_BAR"
    if start:
        required = [at for at in schedule if at >= start]
        if not required or required[0] != start or any(at not in by_end for at in required):
            return [], "GAP_IN_REPLAY_WINDOW"
    else:
        required = list(reversed(list(takewhile(by_end.__contains__, reversed(schedule)))))
        if len(required) < MIN_HISTORY:
            prior = any(at < required[0] for at in by_end)
            return [], "GAP_IN_REPLAY_WINDOW" if prior else "INSUFFICIENT_HISTORY"
    return [by_end[at] for at in required], None
