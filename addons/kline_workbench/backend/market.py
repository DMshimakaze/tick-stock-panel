"""Raw QuantDash facts, Beijing completion grids and one qfq projection."""
from datetime import datetime, timedelta
from math import isfinite, prod
from zoneinfo import ZoneInfo

BEIJING = ZoneInfo('Asia/Shanghai')
WINDOWS = {'5m': 75, '1m': 20, '1d': 300}


def now_cn():
    return datetime.now(BEIJING).replace(tzinfo=None, microsecond=0)


def stamp(day, minute):
    return f'{day} {minute // 60:02}:{minute % 60:02}:00'


def as_ms(value):
    return int(datetime.fromisoformat(value).replace(tzinfo=BEIJING).timestamp() * 1000)


def grid(days, period, end=None):
    step = int(period[:-1]) if period != '1d' else 0
    minutes = [900] if not step else [*range(570 + step, 691, step), *range(780 + step, 901, step)]
    points = [stamp(day, minute) for day in days for minute in minutes]
    return [t for t in points if not end or t <= end]


def window(sessions, period, now):
    cutoff = str(datetime.fromisoformat(str(now)) - timedelta(seconds=60))
    days = [d for d in sessions if d <= cutoff[:10]]
    first_minute = 900 if period == '1d' else 570 + int(period[:-1])
    if days and stamp(days[-1], first_minute) > cutoff:
        days.pop()
    days = days[-WINDOWS[period]:]
    return days, grid(days, period, cutoff)


def valid(row):
    return (all(isinstance(row.get(k), (int, float)) and isfinite(row[k]) and row[k] > 0
                for k in ('open', 'high', 'low', 'close'))
            and isinstance(row.get('volume'), (int, float)) and isfinite(row['volume']) and row['volume'] >= 0
            and row['low'] <= min(row['open'], row['close']) <= max(row['open'], row['close']) <= row['high'])


def normalize(data, start, end, period):
    keys = ('timestamp', 'open', 'high', 'low', 'close', 'volume')
    length = len(data['timestamp'])
    if length >= 5000 or any(len(data[k]) != length for k in keys):
        raise ValueError('SOURCE_TRUNCATED_OR_MISALIGNED')
    rows = {}
    for i, epoch in enumerate(data['timestamp']):
        at = datetime.fromtimestamp(epoch / 1000, BEIJING).replace(tzinfo=None)
        if period == '1d':
            at = at.replace(hour=15, minute=0, second=0, microsecond=0)
        text = str(at)
        minute = at.hour * 60 + at.minute
        if period == '1m' and minute == 570 and not at.second:
            continue  # Source's 09:30 auction is not one continuous-auction minute.
        step = int(period[:-1]) if period != '1d' else 0
        if not start <= text <= end or at.second or (step and minute not in [*range(570+step, 691, step), *range(780+step, 901, step)]):
            raise ValueError('SOURCE_TIME_OUTSIDE_COMPLETED_GRID')
        row = {k: data[k][i] for k in keys[1:]}
        amount = data.get('amount', [None] * length)[i]
        row['amount'] = amount if isinstance(amount, (int,float)) and isfinite(amount) else None
        if text in rows:
            raise ValueError('SOURCE_DUPLICATE_TIMESTAMP')
        if valid(row):
            rows[text] = row
    return rows


def projection(events, days, asof):
    # None means the source omitted coverage. [] is a covered empty event chain.
    if events is None:
        return None
    return {day: 1 / prod(e['ex_factor'] for e in events if day < e['day'] <= asof) for day in days}
