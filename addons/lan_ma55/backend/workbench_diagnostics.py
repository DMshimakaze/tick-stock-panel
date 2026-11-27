"""Read the saved repair facts for a single chart selection."""

from datetime import datetime
from time import time

from .exchange_sessions import INTRADAY
from .market_grid import BEIJING


def chart_diagnostic(market, daily, symbol, timeframe, schedules, frame):
    target = schedules[timeframe][-1] if schedules[timeframe] else None
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
                source="15m" if minute else "daily", last_bar=last_bar, stage=stage, queue=queue, retry_at=retry_at,
                requested_at=retry.get("last_attempt"), requested_through=retry.get("target"),
                last_success=retry.get("last_success") or retry.get("verified_at"),
                error=retry.get("error_type") or retry.get("last_error"))
