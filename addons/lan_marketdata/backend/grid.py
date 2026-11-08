"""Completed Beijing exchange buckets; timestamps always denote the end."""

from datetime import datetime, timedelta, timezone
from math import isfinite, prod, fsum

BEIJING = timezone(timedelta(hours=8))
CAPACITY = {
    "5m": 144,
    **{tf: 256 for tf in ("15m", "30m", "60m", "120m", "daily", "2d")},
}
COUNTS = {"5m": 1, "15m": 3, "30m": 6, "60m": 12, "120m": 24, "daily": 48, "2d": 2}
MAS = (5, 10, 20, 30, 55, 233)
FIVE_MINUTES = (*range(575, 691, 5), *range(785, 901, 5))


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


def latest_point(sessions, now, *, step=5):
    cutoff = str(wall(now) - timedelta(seconds=60))
    days = [d for d in sessions if d <= cutoff[:10]][-2:]
    available = points(days, days[0] if days else cutoff, cutoff, step=step)
    return available[-1] if available else None


def as_ms(value):
    return int(wall(value).replace(tzinfo=BEIJING).timestamp() * 1000)


def normalize_five(data, start, end):
    fields = ("timestamp", "open", "high", "low", "close", "volume")
    length = len(data["timestamp"])
    if length > 5000 or any(len(data[k]) != length for k in fields):
        raise ValueError("TRUNCATED_OR_MISALIGNED_5M")
    rows = {}
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
        row = {k: data[k][i] for k in fields[1:]}
        row["volume"] *= 100  # QuantDash reports lots; the shared contract is shares.
        row["amount"] = data.get("amount", [None] * length)[i]
        if text in rows and rows[text] != row:
            raise ValueError("CONFLICTING_5M")
        if not valid_five(row):
            raise ValueError("INVALID_5M_OHLCV")
        rows[text] = row
    return rows


def valid_five(row):
    return (
        all(
            isinstance(row.get(k), (int, float)) and isfinite(row[k]) and row[k] > 0
            for k in ("open", "high", "low", "close")
        )
        and isinstance(row.get("volume"), (int, float))
        and isfinite(row["volume"])
        and row["volume"] >= 0
        and row["low"]
        <= min(row["open"], row["close"])
        <= max(row["open"], row["close"])
        <= row["high"]
    )


def adjustment(events, day, asof):
    if events is None:
        return None
    return 1 / prod(e["ex_factor"] for e in events if day < e["day"] <= asof[:10])


def basis_key(events, asof, revision):
    return [
        max(
            (e["day"] for e in events or [] if e["day"] <= asof[:10]),
            default="NO_EVENTS",
        ),
        revision,
    ]


def combined(parts, end, count, source="QUANTDASH_RAW_5M"):
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
    )


def aggregate(rows, sessions, asof):
    """No partial buckets or gap bridging, including across lunch and sessions."""
    real = {
        t: r
        for t, r in rows.items()
        if t[:10] in sessions and t <= asof and valid_five(r)
    }
    output = {
        "5m": [
            dict(
                r, timestamp=t, source_count=1, complete=True, source="QUANTDASH_RAW_5M"
            )
            for t, r in sorted(real.items())
        ]
    }
    for tf, width in (
        ("15m", 15),
        ("30m", 30),
        ("60m", 60),
        ("120m", 120),
        ("daily", 240),
    ):
        groups = {}
        for t in real:
            minute = wall(t).hour * 60 + wall(t).minute
            base = 570 if minute <= 690 else 780
            end = stamp(
                t[:10],
                900
                if tf == "daily"
                else base + ((minute - base + width - 1) // width) * width,
            )
            groups.setdefault(end, [])
        completed = []
        for end in sorted(groups):
            required = (
                [stamp(end[:10], m) for m in FIVE_MINUTES]
                if tf == "daily"
                else [
                    str(wall(end) - timedelta(minutes=5 * n))
                    for n in reversed(range(COUNTS[tf]))
                ]
            )
            if end <= asof and all(t in real for t in required):
                completed.append(combined([real[t] for t in required], end, COUNTS[tf]))
        output[tf] = completed
    return output


def two_day(daily, sessions):
    """Stable pairs anchored to the persisted real-calendar origin."""
    by_time = {r["timestamp"][:10]: r for r in daily}
    return [
        combined([by_time[a], by_time[b]], b + " 15:00:00", 2, "TWO_COMPLETED_SESSIONS")
        for a, b in zip(sessions[::2], sessions[1::2])
        if a in by_time and b in by_time
    ]


def project(rows, events, asof, *, sessions=None, timeframe=None):
    result = []
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
            bar.update(
                {
                    k: raw[k] * factor
                    for k in ("open", "high", "low", "close")
                    if k in raw
                }
            )
        result.append(bar)
    ordinal = {}
    if sessions and timeframe and result:
        if timeframe in ("daily", "2d"):
            schedule = [
                d + " 15:00:00"
                for d in (sessions if timeframe == "daily" else sessions[1::2])
            ]
        else:
            schedule = points(
                sessions, result[0]["timestamp"][:10], asof, step=int(timeframe[:-1])
            )
        ordinal = {t: i for i, t in enumerate(schedule)}
    contiguous = []
    previous = None
    for bar in result:
        at = bar["timestamp"]
        if (
            previous
            and ordinal
            and ordinal.get(at, -2) != ordinal.get(previous, -2) + 1
        ):
            contiguous = []
        contiguous.append(bar)
        for n in MAS:
            prior = contiguous[-n:]
            bar[f"ma{n}"] = (
                fsum(r["close"] for r in prior) / n
                if len(prior) == n and all(r["valid"] for r in prior)
                else None
            )
        previous = at
    return result
