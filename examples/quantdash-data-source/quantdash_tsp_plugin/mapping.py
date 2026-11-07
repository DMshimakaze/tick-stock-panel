"""QuantDash 0.1.x -> TSP canonical schema. Pure-Python; test without SDK/network.

Assumptions are explicit: QuantDash 5m data in the MA55 production collector
is in lots (手), and TSP wants lots. No numerical-unit guessing occurs here.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo
import math
import re
import logging

logger = logging.getLogger(__name__)

_BJ = ZoneInfo("Asia/Shanghai")
_SYMBOL = re.compile(r"^([0-9]{6})\.(SH|SZ|BJ)$", re.I)
_PREFIX = re.compile(r"^(SH|SZ|BJ)([0-9]{6})$", re.I)


def symbol_code(raw):
    value = str(raw or "").strip().upper()
    if _SYMBOL.fullmatch(value):
        return value
    match = _PREFIX.fullmatch(value)
    if match:
        return f"{match.group(2)}.{match.group(1)}"
    raise ValueError("unsupported A-share symbol format")


def _value(row, *keys):
    """Return first non-null, non-NaN value from flat or ext-nested records."""
    for key in keys:
        value = row.get(key)
        if value is None and key.startswith("ext.") and isinstance(row.get("ext"), dict):
            value = row["ext"].get(key[4:])
        if value is None:
            continue
        if isinstance(value, float) and not math.isfinite(value):
            continue
        # pandas NA / NaT: detect missing without importing pandas.
        try:
            if value != value:
                continue
        except (TypeError, ValueError):
            pass
        return value
    return None


def number(value, *, allow_none=True):
    if value is None or (isinstance(value, str) and value == ""):
        if allow_none:
            return None
        raise ValueError("missing numeric field")
    try:
        x = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid numeric field") from exc
    if not math.isfinite(x):
        if allow_none:
            return None
        raise ValueError("non-finite numeric field")
    return x


def parse_bj_timestamp(value):
    """Input QuantDash UTC epoch milliseconds -> local naive Asia/Shanghai."""
    if value is None:
        raise ValueError("missing kline timestamp")
    if isinstance(value, (int, float)) and 0 <= value < 10_000_000_000_000:
        return datetime.fromtimestamp(value / 1000, timezone.utc).astimezone(_BJ).replace(tzinfo=None)
    if isinstance(value, datetime):
        return value.astimezone(_BJ).replace(tzinfo=None) if value.tzinfo else value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("unrecognized kline timestamp") from exc
        return parsed.astimezone(_BJ).replace(tzinfo=None) if parsed.tzinfo else parsed
    raise ValueError("unrecognized kline timestamp type")


def request_ms(d: datetime | None, *, end_of_day=False):
    if d is None:
        return None
    if isinstance(d, date) and not isinstance(d, datetime):
        d = datetime(d.year, d.month, d.day)
    if d.tzinfo is None:
        d = d.replace(tzinfo=_BJ)
    else:
        d = d.astimezone(_BJ)
    if end_of_day:
        d = d.replace(hour=23, minute=59, second=59, microsecond=999000)
    return int(d.timestamp() * 1000)


def _records(data):
    if data is None:
        return []
    if hasattr(data, "to_dict") and hasattr(data, "columns"):
        return data.to_dict("records")
    if isinstance(data, list):
        return data
    if isinstance(data, tuple):
        return list(data)
    if isinstance(data, dict):
        if isinstance(data.get("data"), list):
            return data["data"]
        if any(k in data for k in ("symbol", "timestamp", "trade_date", "trade_time")):
            # QuantDash compact column arrays; handled by _compact_to_rows.
            return _compact_to_rows(data)
        return [data]
    raise ValueError("unexpected QuantDash response type")


def _compact_to_rows(data):
    stamps = data.get("timestamp")
    if stamps is None:
        stamps = data.get("trade_time")
    if stamps is None:
        stamps = data.get("trade_date")
    if not isinstance(stamps, (list, tuple)):
        return [data]
    fields = {k: v for k, v in data.items() if isinstance(v, (list, tuple))}
    if any(len(v) != len(stamps) for v in fields.values()):
        raise ValueError("QuantDash compact columns have unequal lengths")
    return [{k: v[i] for k, v in fields.items()} for i in range(len(stamps))]


def _volume(value, unit):
    x = number(value, allow_none=False)
    if x < 0:
        raise ValueError("negative volume")
    if unit == "hands":
        return x
    if unit == "shares":
        return x / 100.0
    raise ValueError("unsupported volume unit")


def kline_rows(response, *, freq: str, volume_unit: str = "hands"):
    """Response is dict(symbol -> compact dataframe/dict). No price/factor guessing."""
    if not isinstance(response, dict):
        raise ValueError("QuantDash batch must return symbol->bars dict")
    if freq not in {"daily", "minute"}:
        raise ValueError("unsupported Kline frequency")
    rows = []
    for sym, data in response.items():
        symbol = symbol_code(sym)
        for row in _records(data):
            stamp = _value(row, "timestamp", "trade_time", "trade_date", "datetime", "date")
            dt = parse_bj_timestamp(stamp)
            o, h, low, close = (number(_value(row, field), allow_none=False)
                                for field in ("open", "high", "low", "close"))
            upper, lower = max(o, h, low, close), min(o, h, low, close)
            if (lower <= 0
                    or (h < upper and not math.isclose(h, upper, rel_tol=1e-12))
                    or (low > lower and not math.isclose(low, lower, rel_tol=1e-12))):
                raise ValueError("invalid OHLC (schema/price check)")
            # Real compact bars can differ by ~1e-14 after float decoding.
            # Preserve open/close and enclose them without rounding to a guessed tick.
            h, low = upper, lower
            volume = _volume(_value(row, "volume", "vol"), volume_unit)
            amount = number(_value(row, "amount", "turnover_amount"))
            # Never fabricate amount; data quality diagnostics report unavailable values.
            result = {"symbol": symbol, "open": o, "high": h, "low": low,
                      "close": close, "volume": volume, "amount": amount}
            result["date" if freq == "daily" else "datetime"] = dt.date() if freq == "daily" else dt
            rows.append(result)
    key = "date" if freq == "daily" else "datetime"
    seen = set()
    for row in rows:
        k = (row["symbol"], row[key])
        if k in seen:
            raise ValueError("duplicate Kline timestamp from QuantDash")
        seen.add(k)
    return sorted(rows, key=lambda r: (r["symbol"], r[key]))


def quote_rows(data, *, volume_unit: str = "hands"):
    """TSP contract: pct fields fractional, volume lots, amount yuan."""
    result = []
    candidates = _records(data)
    malformed = 0
    for row in candidates:
        try:
            symbol = symbol_code(_value(row, "symbol", "code"))
            last = number(_value(row, "last_price", "last"), allow_none=False)
            prev = number(_value(row, "prev_close", "pre_close"), allow_none=False)
            o = number(_value(row, "open"), allow_none=False)
            h = number(_value(row, "high"), allow_none=False)
            low = number(_value(row, "low"), allow_none=False)
            vol = _volume(_value(row, "volume"), volume_unit)
            if last <= 0 or prev <= 0 or o <= 0 or h < low:
                raise ValueError("invalid QuantDash quote prices")
            change_amount = last - prev
            raw_ts = _value(row, "timestamp")
            ts = int(raw_ts) if isinstance(raw_ts, (int, float)) and 1_000_000_000_000 <= raw_ts < 10_000_000_000_000 else None
            result.append({
                "symbol": symbol,
                "name": _value(row, "name", "ext.name"),
                "last_price": last, "prev_close": prev, "open": o, "high": h, "low": low,
                "volume": vol, "amount": number(_value(row, "amount")),
                "change_pct": change_amount / prev,
                "change_amount": change_amount,
                "amplitude": None, "turnover_rate": None, "timestamp": ts,
            })
        except (ValueError, TypeError):
            # A handful of halted/invalid rows must not block 5500 good quotes.
            malformed += 1
    if malformed:
        logger.warning("QuantDash realtime discarded %s malformed/unsuitable records of %s", malformed, len(candidates))
    if candidates and (not result or malformed > max(10, int(len(candidates) * 0.05))):
        raise ValueError("QuantDash quote schema invalid for material share of universe")
    return result


def audit_trade_units(rows):
    """Reject systemic price/volume/amount unit drift. Never auto-correct.

    A-share gross trading value / (volume[手]*100) should approximately match
    a traded price. Check only when enough complete positive rows are present.
    With no amount, callers may still use OHLCV; diagnostics must flag missing.
    """
    tested = 0
    bad = 0
    for row in rows:
        amount = row.get("amount")
        vol = row.get("volume")
        low = row.get("low")
        high = row.get("high")
        if not (isinstance(amount, (int, float)) and isinstance(vol, (int, float))
                and isinstance(low, (int, float)) and isinstance(high, (int, float))
                and amount > 0 and vol > 0 and low > 0 and high >= low):
            continue
        tested += 1
        avg = amount / (vol * 100)
        if not low * 0.8 <= avg <= high * 1.2:
            bad += 1
        if tested >= 100:
            break
    if tested >= 5 and bad / tested > 0.7:
        raise ValueError("QuantDash price-volume-amount units inconsistent with TSP lots/yuan contract")


def factor_rows(data):
    events = {}
    for row in _records(data):
        sym = symbol_code(_value(row, "symbol", "ts_code"))
        day = parse_bj_timestamp(_value(row, "trade_date", "timestamp", "date")).date()
        ex = number(_value(row, "ex_factor"), allow_none=False)
        if ex <= 0:
            raise ValueError("nonpositive ex_factor")
        key = (sym, day)
        if key in events and events[key]["ex_factor"] != ex:
            raise ValueError("duplicate conflicting factor event")
        events[key] = {"symbol": sym, "trade_date": day, "ex_factor": ex}
    return list(events.values())


def depth_rows(data, symbols):
    if isinstance(data, dict) and "symbol" in data:
        items = [data]
    elif isinstance(data, dict):
        items = []
        for sym, r in data.items():
            if not isinstance(r, dict):
                raise ValueError("invalid depth item")
            items.append({"symbol": sym, **r})
    elif isinstance(data, list):
        items = data
    else:
        raise ValueError("invalid QuantDash depth response")
    expected = set(symbols)
    result = {}
    for item in items:
        sym = symbol_code(item.get("symbol"))
        if sym not in expected:
            raise ValueError("unrequested depth symbol")
        bp = item.get("bid_prices") or []
        bv = item.get("bid_volumes") or []
        ap = item.get("ask_prices") or []
        av = item.get("ask_volumes") or []
        if not (len(bp) == len(bv) == len(ap) == len(av) == 5):
            raise ValueError("QuantDash depth must provide five aligned levels")
        prices = [number(x, allow_none=False) for x in [*bp, *ap]]
        vols = [number(x, allow_none=False) for x in [*bv, *av]]
        if min(prices) < 0 or min(vols) < 0:
            raise ValueError("invalid depth price/volume")
        result[sym] = {
            "bid_prices": prices[:5], "bid_volumes": vols[:5],
            "ask_prices": prices[5:], "ask_volumes": vols[5:],
            "timestamp": item.get("timestamp"),
        }
    return result
