"""Read the saved repair facts for a single chart selection."""

from bisect import bisect_left
from datetime import datetime
from time import time

from .exchange_sessions import INTRADAY
from .market_grid import BEIJING
from .multiframe_input import MIN_HISTORY


def chart_diagnostic(market, daily, symbol, timeframe, schedules, weeks, frame):
    schedule = schedules[timeframe]
    target = schedule[-1] if schedule else None
    reason = frame.get("status", "WAITING_INPUT")
    if reason == "READY":
        return None
    minute = timeframe in INTRADAY
    table = "native15_bar" if minute else "ma55_daily_bar_v1"
    conn = market.conn
    last_bar = conn.execute(
        f"SELECT max(bar_end) FROM {table} WHERE symbol=? AND bar_end<=? AND volume>0", (symbol, target),
    ).fetchone()[0]
    missing = frame.get("missing_raw")
    info = market.instrument(symbol) or {}
    listing = info.get("listing_date")
    capacity = None
    if listing and target:
        listed = (sum(week[0] >= listing and week[-1] <= target[:10] for week in weeks) if timeframe == "weekly"
                  else len(schedule) - bisect_left(schedule, listing))
        if listed < MIN_HISTORY:
            capacity = dict(listed=listed, required=MIN_HISTORY)
    zero = None
    if target and reason == "ZERO_VOLUME_BAR":
        start = frame.get("replay_start") or schedule[-MIN_HISTORY:][0]
        zero = conn.execute(f"SELECT min(bar_end), max(bar_end) FROM {table} "
                            "WHERE symbol=? AND bar_end BETWEEN ? AND ? AND volume=0", (symbol, start, target)).fetchone()
    stage, retry = None, {}
    if reason == "FACTOR_PENDING":
        row = conn.execute("SELECT * FROM native15_factor_retry WHERE symbol=?", (symbol,)).fetchone()
        stage, retry = "F", dict(row) if row else {}
    elif missing and minute:
        row = conn.execute("SELECT * FROM native15_retry WHERE symbol=?", (symbol,)).fetchone()
        retry = dict(row) if row else {}
        stage = retry.get("stage")
    elif missing:
        stage = "D" if timeframe == "daily" else "W"
        retry = daily.progress(symbol)
        if not schedules["daily"] or retry.get("target") != schedules["daily"][-1]:
            retry = {}
    eligible = retry.get("next_eligible") or retry.get("next_retry")
    if eligible and eligible > time():
        queue = "COOLING"
    elif missing or reason == "FACTOR_PENDING":
        queue = "ELIGIBLE"
    else:
        queue = "UNKNOWN"
    retry_at = (datetime.fromtimestamp(eligible, BEIJING).replace(tzinfo=None).isoformat(sep=" ", timespec="seconds")
                if eligible else None)
    return dict(reason=reason, target=target, missing_raw=missing, replay_start=frame.get("replay_start"),
                listing_date=listing, listed_capacity=capacity, zero_raw=list(zero) if zero and zero[0] else None,
                factor_day=info.get("factors_day") if reason == "FACTOR_PENDING" else None,
                source="15m" if minute else "daily", last_bar=last_bar, stage=stage, queue=queue, retry_at=retry_at,
                requested_at=retry.get("last_attempt"), requested_through=retry.get("target"),
                last_success=retry.get("last_success") or retry.get("verified_at"),
                error=retry.get("error_type") or retry.get("last_error"))
