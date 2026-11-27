"""Display windows for events confirmed by the observation worker."""

from .exchange_sessions import INTRADAY
from .market_grid import wall
from .multiframe_core.multiframe import TIMEFRAMES, VERSION

WAITING = {"WAITING_INPUT", "WAITING_FOR_COMPLETED_TARGET", "CALENDAR_PENDING", "WAITING_FOR_REVIEW"}


def display_targets(targets, market_day):
    return {
        frame: end if frame not in INTRADAY or (end and end[:10] == market_day) else None
        for frame in TIMEFRAMES
        for end in (targets.get(frame),)
    }


def in_window(at, target, timeframe):
    return bool(at and target and at <= target and
                (at[:10] == target[:10] if timeframe in INTRADAY else at == target))


def completed_analysis(data):
    result = data.get("result") or {}
    latest = result.get("latest") or {}
    at = latest.get("timestamp")
    return str(wall(at)) if data.get("status") == "READY" and result.get("rule_version") == VERSION and at else None


def confirmations(data):
    if "confirmations" in data:
        return data["confirmations"]
    # Older observation payloads prove the latest candidate of their successful
    # replay. The append-only event table alone cannot prove earlier validity.
    through = completed_analysis(data)
    if not through or through != data.get("expected_end"):
        return None
    latest = data.get("latest_candidate")
    period = data["result"]["timeframe"]
    events = [latest] if latest and in_window(latest["at"], through, period) else []
    if events and latest["id"] == data["result"]["latest"].get("event_id"):
        events = [dict(latest, price=data["result"]["latest"].get("close"))]
    return dict(through=through, events=events, invalidated=[])


def activation(frame, target, timeframe):
    saved = confirmations(frame)
    recorded = bool(saved and in_window(saved["through"], target, timeframe))
    verified = recorded and saved.get("verified", True)
    events = saved["events"] if verified else []
    ready = bool(target and completed_analysis(frame) == target)
    if ready:
        state = "ACTIVATED" if events else "INACTIVE"
    else:
        state = "PENDING" if not target or frame["status"] in WAITING else "MISSING"
    return dict(state=state, events=events, through=saved["through"] if verified else None,
                invalidated=len(saved["invalidated"]) if recorded else 0,
                review_pending=bool(recorded and not verified and saved["events"]))
