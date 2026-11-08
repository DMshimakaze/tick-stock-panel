"""Beijing exchange completion grid and the dedicated raw 5m boundary."""

from bisect import bisect_right
from datetime import datetime, timedelta
from math import isfinite, prod

from .pipeline import BEIJING, END_MINUTES, wall

FIVE_MINUTES = (*range(575, 691, 5), *range(785, 901, 5))


def now_cn():
    return datetime.now(BEIJING).replace(tzinfo=None, microsecond=0)


def stamp(day, minute):
    return f"{day} {minute // 60:02}:{minute % 60:02}:00"


def points(sessions, start, end, *, step=5):
    start, end = str(start), str(end)
    minutes = FIVE_MINUTES if step == 5 else END_MINUTES
    return [stamp(d, m) for d in sessions if start[:10] <= d <= end[:10]
            for m in minutes if start <= stamp(d, m) <= end]


def latest_point(sessions, now, *, step=5):
    # Same conservative completion margin as the installed observer. The source
    # does not expose a final flag; the dynamic last minute is never consumed.
    cutoff = str(wall(now) - timedelta(seconds=60))
    days = [d for d in sessions if d <= cutoff[:10]][-2:]
    available = points(days, days[0] if days else cutoff, cutoff, step=step)
    return available[-1] if available else None


def recent_start(sessions, target, count=10):
    days = [d for d in sessions if d <= target[:10]]
    return stamp(days[-count] if len(days) >= count else days[0], 575)


def as_ms(value):
    return int(wall(value).replace(tzinfo=BEIJING).timestamp() * 1000)


def normalize_five(data, start, end):
    expected = {k: data[k] for k in ("timestamp", "open", "high", "low", "close", "volume")}
    length = len(expected["timestamp"])
    if length >= 5000 or any(len(values) != length for values in expected.values()):
        raise ValueError("TRUNCATED_OR_MISALIGNED_5M")
    rows = {}
    for i, epoch in enumerate(expected["timestamp"]):
        at = datetime.fromtimestamp(epoch / 1000, BEIJING).replace(tzinfo=None)
        text = str(at)
        minute = at.hour * 60 + at.minute
        if at.second or at.microsecond or minute not in FIVE_MINUTES or not start <= text <= end:
            raise ValueError("NON_COMPLETED_OR_OUT_OF_RANGE_5M")
        if text in rows:
            raise ValueError("DUPLICATE_5M")
        values = {k: expected[k][i] for k in ("open", "high", "low", "close", "volume")}
        values["amount"] = data.get("amount", [None] * length)[i]
        rows[text] = values
    return rows


def valid_five(row):
    return all(isinstance(row.get(k), (int, float)) and isfinite(row[k]) and row[k] > 0
               for k in ("open", "high", "low", "close")) and (
        isinstance(row.get("volume"), (int, float)) and isfinite(row["volume"]) and row["volume"] >= 0
        and row["low"] <= min(row["open"], row["close"]) <= max(row["open"], row["close"]) <= row["high"])


def aggregate_five(rows, sessions, start, end):
    """Return only genuine three-bar groups; holes remain explicit timestamps."""
    expected = points(sessions, start, end)
    missing = [t for t in expected if t not in rows or not valid_five(rows[t])]
    groups = {}
    for text, row in rows.items():
        at = wall(text)
        minute = at.hour * 60 + at.minute
        target = stamp(text[:10], ((minute + 14) // 15) * 15)
        groups.setdefault(target, {})[text] = row
    complete, tail = [], {}
    for target, group in sorted(groups.items()):
        required = [str(wall(target) - timedelta(minutes=n)) for n in (10, 5, 0)]
        if target > end:
            tail.update(group)
            continue
        if not all(t in group and valid_five(group[t]) for t in required):
            tail.update({t: r for t, r in group.items() if valid_five(r)})
            continue
        bars = [group[t] for t in required]
        volume = sum(r["volume"] for r in bars)
        if volume <= 0:
            missing.extend(required)
            tail.update(group)
            continue
        complete.append(dict(timestamp=target, open=bars[0]["open"],
                             high=max(r["high"] for r in bars), low=min(r["low"] for r in bars),
                             close=bars[-1]["close"], volume=volume, source_count=3))
    return complete, tail, sorted(set(missing))


def adjustment(events, day):
    """QuantDash/TSP pre/post event ratios: qfq = raw / product(later events).

    None is missing coverage, [] is an explicitly covered empty event chain.
    Volume remains vendor lots; the rule's volume ratios are unitless.
    """
    if events is None:
        return None
    return 1 / prod(e["ex_factor"] for e in events if e["day"] > day)


def next_missing(start, end, sessions, covered, tail):
    available = set(tail)
    for text in covered:
        available.update(str(wall(text) - timedelta(minutes=n)) for n in (10, 5, 0))
    available = {t for t in available if start <= t <= end}
    expected = points(sessions, start, end)
    index = next((i for i, t in enumerate(expected) if t not in available), len(expected))
    through = expected[index - 1] if index else None
    if index == len(expected):
        return through, None
    # Stop at the next stored group: repair never downloads already durable K.
    following = sorted(t for t in available if t > expected[index])
    stop = expected[bisect_right(expected, following[0]) - 2] if following else end
    return through, (expected[index], max(expected[index], stop))
