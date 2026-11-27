"""Beijing session phases and source retry cadence."""
from datetime import datetime, time, timedelta
from email.utils import parsedate_to_datetime

import httpx

from .clock import CN

OPEN_AUCTION = time(9, 25)
CONTINUOUS = time(9, 30)
LUNCH = time(11, 30)
RESUME = time(13)
CLOSE = time(15)


def session_at(now, sessions):
    today = now.date().isoformat()
    previous = next((day for day in reversed(sessions) if day < today), None)
    if today not in sessions:
        return previous, 'CLOSED_DAY'
    clock = now.time()
    if clock < OPEN_AUCTION:
        return previous, 'PREOPEN'
    if clock < CONTINUOUS:
        return today, 'AUCTION'
    if clock < LUNCH or RESUME <= clock < CLOSE:
        return today, 'LIVE'
    if clock < RESUME:
        return today, 'LUNCH'
    return today, 'POST_CLOSE'


def next_open(now, sessions):
    today = now.date().isoformat()
    for day in sessions:
        if day < today:
            continue
        for at in (OPEN_AUCTION, RESUME):
            stamp = datetime.fromisoformat(day).replace(hour=at.hour, minute=at.minute, tzinfo=CN)
            if stamp > now:
                return stamp
    return None


def request_failure(exc, now, default):
    if isinstance(exc, httpx.HTTPStatusError):
        response = exc.response
        status = response.status_code
        if status in (403, 429):
            delay = 900 if status == 403 else 60
            header = response.headers.get('Retry-After')
            if header:
                try:
                    seconds = int(header) if header.isdigit() else (parsedate_to_datetime(header) - now).total_seconds()
                    delay = max(delay, seconds)
                except (TypeError, ValueError, OverflowError):
                    pass
            return f'HTTP {status}', now + timedelta(seconds=delay)
        return f'HTTP {status}', now + timedelta(seconds=default)
    message = ('连接超时' if isinstance(exc, httpx.TimeoutException) else
               '连接失败' if isinstance(exc, httpx.HTTPError) else '响应为空或数据校验失败')
    return message, now + timedelta(seconds=default)
