"""Read-only MA55 price map; no signal rules, I/O or other moving averages."""
from datetime import datetime, timedelta, timezone
from math import isfinite

from .multiframe_core.multiframe import TIMEFRAMES, VERSION

CHINA = timezone(timedelta(hours=8))


def _time(value):
    stamp = datetime.fromisoformat(str(value))
    return stamp.astimezone(CHINA).replace(tzinfo=None) if stamp.tzinfo else stamp


def _positive(value):
    return type(value) in (int, float) and isfinite(value) and value > 0


def project_levels(symbol, frames, *, price, price_at, asof, basis):
    """Compare completed-period MA55s with one same-basis reference price.

    frames[period] supplies status, basis, expected_end, and the research
    observation under result. The caller proves the completion grid, gap
    coverage, factor revision and source freshness before assigning READY.
    A stale or unavailable frame cannot be used as a pressure level.
    distance_pct is (MA55 / reference_price - 1) * 100; this is descriptive,
    not a projected return, SELL signal or modification of BUY logic.
    """
    cutoff = _time(asof)
    if not _positive(price) or not basis or _time(price_at) > cutoff:
        raise ValueError("Invalid price reference")
    rows = []
    for timeframe in TIMEFRAMES:
        frame = frames.get(timeframe) or {}
        status = frame.get("status", "UNAVAILABLE")
        row = dict(timeframe=timeframe, status=status, ma55=None, at=None,
                   side=None, distance_pct=None, pressure_test=None)
        rows.append(row)
        if status != "READY":
            continue
        result = frame.get("result") or {}
        latest = result.get("latest") or {}
        if (frame.get("basis") != basis or result.get("symbol") != symbol or
                result.get("timeframe") != timeframe or
                result.get("rule_version") != VERSION):
            row["status"] = "IDENTITY_OR_BASIS_MISMATCH"
            continue
        try:
            end = _time(latest["timestamp"])
            expected = _time(frame["expected_end"])
            valid = end == expected and end <= cutoff
        except (KeyError, ValueError, TypeError):
            valid = False
        if not valid:
            row["status"] = "STALE_OR_INVALID_END"
            continue
        row["at"] = str(end)
        level = latest.get("ma55")
        if not _positive(level):
            row["status"] = "WARMUP"
            continue
        side = "PRESSURE" if level > price else "SUPPORT_REFERENCE" if level < price else "ON"
        row.update(ma55=level, side=side, distance_pct=(level/price-1)*100,
                   pressure_test=bool(latest.get("pressure_test")))
    pressure = sorted((r for r in rows if r["side"] == "PRESSURE"),
                      key=lambda r: r["ma55"])
    return dict(symbol=symbol, price=price, price_at=str(_time(price_at)),
                asof=str(cutoff), basis=basis, levels=rows, pressures=pressure)
