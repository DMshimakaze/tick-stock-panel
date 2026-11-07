"""Completed-bar validation, bounded state and immutable confirmations.

Only consumes explicitly supplied offline inputs. There is no host/vendor reader.
"""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from math import isfinite

from .engine import RULE_VERSION, advance, minimal_features

SCHEMA_VERSION = 1
END_MINUTES = (*range(585, 691, 15), *range(795, 901, 15))
BEIJING = timezone(timedelta(hours=8))


def wall(value):
    at = datetime.fromisoformat(str(value))
    return at.astimezone(BEIJING).replace(tzinfo=None) if at.tzinfo else at


def serializable(value):
    if isinstance(value, dict):
        return {k: serializable(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [serializable(v) for v in value]
    return None if isinstance(value, float) and not isfinite(value) else value


def empty_record(symbol):
    return dict(
        symbol=symbol,
        rule_version=RULE_VERSION,
        schema_version=SCHEMA_VERSION,
        state=dict(
            processed_at=None,
            data_asof=None,
            structure=None,
            valid_run=0,
            feature_history=[],
            slot_volumes={},
            last_factor=None,
            sequence=0,
            decision=None,
            reason="INSUFFICIENT_WARMUP",
        ),
        confirmations=[],
        risks={},
    )


def previous_end(at, sessions):
    minute = at.hour * 60 + at.minute
    if minute == 585:
        earlier = [d for d in sessions if d < str(at.date())]
        return earlier[-1] + " 15:00:00" if earlier else None
    if minute == 795:
        return str(at.replace(hour=11, minute=30))
    return str(at - timedelta(minutes=15))


def invalidate(state, reason):
    state.update(
        valid_run=0, feature_history=[], structure=None, decision=None, reason=reason
    )


def process_symbol(
    symbol,
    bars,
    *,
    sessions,
    raw_tick,
    source_asof,
    first_seen_at,
    previous=None,
    origin="REPLAY",
    live_after=None,
):
    """Sorted completed adjusted 15m with per-bar adjust_factor + source_count=3.

    source_asof and sessions must come with the supplied dataset. Neither is inferred
    from a computer's timezone, a price difference, or a TSP private object.
    """
    compatible = (
        previous
        and previous.get("rule_version") == RULE_VERSION
        and previous.get("schema_version") == SCHEMA_VERSION
    )
    record = deepcopy(previous) if compatible else empty_record(symbol)
    if previous and not compatible:
        record["confirmations"] = deepcopy(previous.get("confirmations", []))
        record["risks"] = deepcopy(previous.get("risks", {}))
    state, added = record["state"], []
    source, seen = wall(source_asof), wall(first_seen_at)
    after = wall(live_after) if live_after else seen
    if origin not in ("LIVE", "DELAYED", "REPLAY"):
        raise ValueError("Unknown discovery mode")
    for raw in bars:
        at = wall(raw["timestamp"])
        stamp = str(at)
        if at > source:
            continue
        if state["processed_at"] and stamp <= state["processed_at"]:
            continue
        factor = raw.get("adjust_factor")
        factor_ok = isinstance(factor, (float, int)) and isfinite(factor) and factor > 0
        prices = [raw.get(k) for k in ("open", "high", "low", "close", "volume")]
        prices_ok = all(
            isinstance(v, (int, float)) and isfinite(v) and v > 0 for v in prices
        )
        complete = (
            at.second == at.microsecond == 0
            and at.hour * 60 + at.minute in END_MINUTES
            and raw.get("source_count") == 3
            and raw.get("valid") is not False
        )
        changed = (
            factor_ok
            and state["last_factor"]
            and abs(factor / state["last_factor"] - 1) > 1e-6
        )
        reason = (
            "NON_TRADING_SESSION"
            if str(at.date()) not in sessions or at.weekday() >= 5
            else "INVALID_OR_INCOMPLETE_OHLCV"
            if not complete or not prices_ok
            else "INVALID_OR_INCOMPLETE_OHLCV"
            if not raw["low"]
            <= min(raw["open"], raw["close"])
            <= max(raw["open"], raw["close"])
            <= raw["high"]
            else "TICK_UNAVAILABLE"
            if not isinstance(raw_tick, (int, float))
            or not isfinite(raw_tick)
            or raw_tick <= 0
            else "HISTORICAL_FACTOR_UNAVAILABLE"
            if not factor_ok
            else "PRICE_BASIS_CHANGED"
            if changed
            else "MISSING_COMPLETED_15M"
            if state["processed_at"]
            and previous_end(at, sessions) != state["processed_at"]
            else ""
        )
        state.update(
            processed_at=stamp,
            last_factor=factor if factor_ok else None,
            sequence=state["sequence"] + 1,
        )
        if reason:
            invalidate(state, reason)
            for event in record["confirmations"]:
                risk = record["risks"].setdefault(event["event_id"], {})
                risk.update(
                    data_pending_at=stamp,
                    reason="PRICE_BASIS_CHANGED"
                    if risk.get("reason") == "PRICE_BASIS_CHANGED"
                    else reason,
                )
            continue
        sample = {k: raw[k] for k in ("open", "high", "low", "close", "volume")}
        sample.update(timestamp=stamp, adjust_factor=factor)
        slot = stamp[11:16]
        volumes = state["slot_volumes"].get(slot, [])
        bar = minimal_features(state["feature_history"], sample, volumes)
        structure, decision = advance(state["structure"], bar, raw_tick)
        state.update(
            structure=structure,
            decision=decision,
            valid_run=state["valid_run"] + 1,
            data_asof=stamp,
            reason=decision["veto"] or decision["event"],
            close=bar["close"],
            ma55=bar["ma55"],
            feature_history=(state["feature_history"] + [sample])[-55:],
        )
        state["slot_volumes"][slot] = (volumes + [bar["volume"]])[-5:]
        for event in record["confirmations"]:
            if event["rule_version"] != RULE_VERSION:
                continue
            risk = record["risks"].setdefault(event["event_id"], {})
            risk["age_bars"] = state["sequence"] - event["sequence"]
            if risk.get("reason") == "PRICE_BASIS_CHANGED":
                continue
            risk.update(
                data_asof=stamp,
                data_pending_at=None
                if state["valid_run"] >= 56
                else risk.get("data_pending_at"),
                invalidated_at=risk.get("invalidated_at")
                or (stamp if bar["close"] < event["defense"] else None),
                extended=bool(
                    decision["close_distance_pct"]
                    and decision["close_distance_pct"] > 3
                ),
            )
        if decision["event"]:
            identity = f"{RULE_VERSION}:{symbol}:SUPPORT:{decision['support_test_time'].replace(' ', 'T')}:TURN:{stamp.replace(' ', 'T')}"
            if not any(e["event_id"] == identity for e in record["confirmations"]):
                mode = "REPLAY"
                if origin != "REPLAY" and at > after and seen.date() == at.date():
                    mode = (
                        "LIVE"
                        if origin == "LIVE" and (seen - at).total_seconds() <= 90
                        else "DELAYED"
                    )
                event = dict(
                    event_id=identity,
                    symbol=symbol,
                    rule_version=RULE_VERSION,
                    event_kind="BUY_STRUCTURE",
                    setup_at=decision["support_test_time"],
                    confirmed_at=stamp,
                    first_seen_at=str(seen),
                    source_asof=str(source),
                    data_asof=stamp,
                    mode=mode,
                    pattern=decision["pattern"],
                    sequence=state["sequence"],
                    confirm_close=bar["close"],
                    defense=decision["support_test_low"],
                    raw_tick=raw_tick,
                    adjust_factor=factor,
                    decision=deepcopy(decision),
                    impulse=deepcopy(structure["last_impulse"]),
                    risk_text="离均线较远，不追价"
                    if decision["close_distance_pct"] > 3
                    else "",
                )
                record["confirmations"].append(event)
                record["risks"][identity] = dict(
                    data_asof=stamp,
                    age_bars=0,
                    invalidated_at=None,
                    extended=decision["close_distance_pct"] > 3,
                )
                added.append(event)
    record["updated_at"] = str(seen)
    return serializable(record), serializable(added)
