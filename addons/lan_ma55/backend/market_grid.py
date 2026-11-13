"""Beijing completed bars: raw 5m -> 15m; larger views are computed on read."""

from datetime import datetime, timedelta, timezone
from math import fsum, isfinite, prod

BEIJING = timezone(timedelta(hours=8))
CAPACITY = {"15m": 250, "30m": 125, "60m": 62}
MAS = (5, 10, 20, 30, 55, 233)
STRATEGY_CAPACITY = 250


def required_history(timeframe, visible_count=None):
    """Same-period completed bars, including the first visible SMA window."""
    return (CAPACITY[timeframe] if visible_count is None else visible_count) + max(MAS) - 1


def history_capacity():
    # Up to three leading 15m slots can be lost to 60m bucket alignment.
    return max(1280, *(required_history(tf) * (int(tf[:-1]) // 15) + int(tf[:-1]) // 15 - 1 for tf in CAPACITY))
FIVE_MINUTES = (*range(575, 691, 5), *range(785, 901, 5))
SOURCE = "TICKFLOW_RAW_5M"
LEGACY = "QUANTDASH_LEGACY"
FIELDS = ("open", "high", "low", "close", "volume", "amount")


def wall(value):
    at = datetime.fromisoformat(str(value))
    return at.astimezone(BEIJING).replace(tzinfo=None) if at.tzinfo else at


def now_cn():
    return datetime.now(BEIJING).replace(tzinfo=None, microsecond=0)


def stamp(day, minute):
    return f"{day} {minute // 60:02}:{minute % 60:02}:00"


def points(sessions, start, end, *, step=5):
    minutes = [m for m in FIVE_MINUTES if (m - (570 if m <= 690 else 780)) % step == 0]
    return [
        stamp(d, m)
        for d in sessions
        if start[:10] <= d <= end[:10]
        for m in minutes
        if start <= stamp(d, m) <= end
    ]


def latest_point(sessions, now, *, step=15, margin=75):
    cutoff = str(wall(now) - timedelta(seconds=margin))
    days = [d for d in sessions if d <= cutoff[:10]][-2:]
    available = points(days, days[0] if days else cutoff, cutoff, step=step)
    return available[-1] if available else None


def as_ms(value):
    return int(wall(value).replace(tzinfo=BEIJING).timestamp() * 1000)


def valid_five(row):
    return (
        all(
            isinstance(row.get(k), (int, float)) and isfinite(row[k]) and row[k] > 0
            for k in FIELDS[:4]
        )
        and isinstance(row.get("volume"), (int, float))
        and isfinite(row["volume"])
        and row["volume"] >= 0
        and row["low"]
        <= min(row["open"], row["close"])
        <= max(row["open"], row["close"])
        <= row["high"]
        and (
            row.get("amount") is None
            or (isfinite(row["amount"]) and row["amount"] >= 0)
        )
    )


def normalize_five(data, start, end):
    fields = ("timestamp", *FIELDS[:5])
    length = len(data["timestamp"])
    if length > 5000 or any(len(data[k]) != length for k in fields):
        raise ValueError("TRUNCATED_OR_MISALIGNED_5M")
    amounts = data.get("amount", [None] * length)
    if len(amounts) != length:
        raise ValueError("TRUNCATED_OR_MISALIGNED_5M")
    rows, invalid, seen = {}, [], {}
    for i, epoch in enumerate(data["timestamp"]):
        at = datetime.fromtimestamp(epoch / 1000, BEIJING).replace(tzinfo=None)
        text = str(at)
        if (
            at.second
            or at.microsecond
            or at.hour * 60 + at.minute not in FIVE_MINUTES
            or not start <= text <= end
        ):
            raise ValueError("NON_COMPLETED_OR_OUT_OF_RANGE_5M")
        row = {k: data[k][i] for k in FIELDS[:5]}
        row["volume"] *= 100  # TickFlow compact volume is lots; HTTP v2 is shares.
        row["amount"] = amounts[i]
        if text in seen and seen[text] != row:
            raise ValueError("CONFLICTING_5M")
        seen[text] = row
        if not valid_five(row):
            invalid.append(text)
            continue
        rows[text] = row
    return rows, invalid


def combined(parts, end, source, count):
    return dict(
        timestamp=end,
        open=parts[0]["open"],
        high=max(r["high"] for r in parts),
        low=min(r["low"] for r in parts),
        close=parts[-1]["close"],
        volume=fsum(r["volume"] for r in parts),
        amount=fsum(r["amount"] for r in parts)
        if all(r.get("amount") is not None for r in parts)
        else None,
        source_count=count,
        complete=True,
        source=source,
        source_id=source,
    )


def bucket_end(at, width):
    minute = wall(at).hour * 60 + wall(at).minute
    base = 570 if minute <= 690 else 780
    return stamp(at[:10], base + ((minute - base + width - 1) // width) * width)


def aggregate(rows, sessions, asof, *, source=SOURCE):
    real = {
        t: r
        for t, r in rows.items()
        if t[:10] in sessions and t <= asof and valid_five(r)
    }
    bars = []
    for end in sorted({bucket_end(t, 15) for t in real}):
        required = [str(wall(end) - timedelta(minutes=n)) for n in (10, 5, 0)]
        if end <= asof and all(t in real for t in required):
            bars.append(combined([real[t] for t in required], end, source, 3))
    return {"15m": bars}


def derived(rows, timeframe, sessions, asof):
    if timeframe == "15m":
        return rows
    width, count = int(timeframe[:-1]), int(timeframe[:-1]) // 15
    real = {r["timestamp"]: r for r in rows}
    result = []
    for end in sorted({bucket_end(t, width) for t in real}):
        required = [
            str(wall(end) - timedelta(minutes=15 * n)) for n in reversed(range(count))
        ]
        parts = [real[t] for t in required if t in real]
        if (
            end <= asof
            and end[:10] in sessions
            and len(parts) == count
            and len({r["source_id"] for r in parts}) == 1
        ):
            result.append(
                combined(
                    parts,
                    end,
                    parts[0]["source_id"],
                    sum(r["source_count"] for r in parts),
                )
            )
    return result


def adjustment(events, day, asof):
    if events is None:
        return None
    return 1 / prod(e["ex_factor"] for e in events if day < e["day"] <= asof[:10])


def project(rows, events, asof, *, sessions, timeframe, periods=MAS):
    result, continuous, previous = [], [], None
    schedule = (
        points(sessions, rows[0]["timestamp"][:10], asof, step=int(timeframe[:-1]))
        if rows
        else []
    )
    ordinal = {t: i for i, t in enumerate(schedule)}
    for raw in rows:
        factor = adjustment(events, raw["timestamp"][:10], asof)
        bar = dict(
            raw,
            adjust_factor=factor,
            price_basis="QFQ" if factor is not None else "RAW",
            volume_unit="shares",
            valid=factor is not None,
        )
        if factor is not None:
            bar.update({k: raw[k] * factor for k in FIELDS[:4]})
        if not bar["valid"] or (
            previous is not None
            and (
                ordinal.get(bar["timestamp"], -2)
                != ordinal.get(previous["timestamp"], -2) + 1
                or previous["source_id"] != bar["source_id"]
            )
        ):
            continuous = []
        if bar["valid"]:
            continuous.append(bar["close"])
        for n in periods:
            bar[f"ma{n}"] = fsum(continuous[-n:]) / n if len(continuous) >= n else None
        result.append(bar)
        previous = bar
    return result
