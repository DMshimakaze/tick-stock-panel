"""Verified historical sessions and the exchange's published 2026 closures."""

from datetime import date, timedelta
from itertools import groupby

from .market_grid import COMPLETE_BAR_SECONDS, points, stamp, wall

# SSE announcement 2025-45, confirmed by the SZSE 2026 holiday schedule.
HOLIDAYS_2026 = (
    ("01-01", "01-03"), ("02-15", "02-23"), ("04-04", "04-06"),
    ("05-01", "05-05"), ("06-19", "06-21"), ("09-25", "09-27"),
    ("10-01", "10-07"),
)
INTRADAY = ("15m", "30m", "60m", "120m")


def planned_session(day):
    if date.fromisoformat(day).weekday() >= 5:
        return False
    if day[:4] == "2026":
        return not any(start <= day[5:] <= end for start, end in HOLIDAYS_2026)
    return None


def complete_weeks(sessions, cutoff):
    """Use confirmed sessions, including Thursday ends before Friday holidays."""
    days = [day for day in sessions if day <= cutoff[:10]]
    groups = [list(group) for _, group in groupby(days, lambda day: date.fromisoformat(day).isocalendar()[:2])]
    weeks = []
    for i, group in enumerate(groups):
        first, last = map(date.fromisoformat, (group[0], group[-1]))
        if i == 0 and first.weekday() and any(
            planned_session(str(first - timedelta(days=n))) is not False
            for n in range(1, first.weekday() + 1)
        ):
            continue  # History may have started inside a trading week.
        later_session = i + 1 < len(groups)
        no_more_sessions = all(
            planned_session(str(last + timedelta(days=n))) is False
            for n in range(1, 5 - last.weekday())
        )
        if (later_session or no_more_sessions) and stamp(group[-1], 900) <= cutoff:
            weeks.append(group)
    return weeks


def frame_schedules(sessions, now):
    cutoff = str(wall(now) - timedelta(seconds=COMPLETE_BAR_SECONDS))
    start = sessions[0] if sessions else cutoff
    weeks = complete_weeks(sessions, cutoff)
    schedules = {
        frame: points(sessions, start, cutoff, step=int(frame[:-1])) for frame in INTRADAY
    }
    schedules["daily"] = [stamp(day, 900) for day in sessions if stamp(day, 900) <= cutoff]
    schedules["weekly"] = [stamp(week[-1], 900) for week in weeks]
    return schedules, weeks


def next_native15_request(sessions, now):
    """Next eligible request on today's verified session, including the close margin."""
    day = now[:10]
    if day not in sessions:
        return None
    margin = timedelta(seconds=COMPLETE_BAR_SECONDS)
    cutoff = str(wall(now) - margin)
    after = next((at for at in points([day], day, stamp(day, 900), step=15) if at > cutoff), None)
    return wall(after) + margin if after else None
