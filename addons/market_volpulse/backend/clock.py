"""Beijing exchange clock; no host local-time dependency."""
from datetime import datetime, time, timedelta, timezone

CN = timezone(timedelta(hours=8))


def closed_index(label: str) -> int | None:
    """Upstream close label 09:31 -> 0; 13:00 duplicate is not a bar."""
    s = label.replace(':', '')
    if len(s) != 4 or not s.isdigit() or int(s[2:]) >= 60:
        return None
    m = int(s[:2]) * 60 + int(s[2:])
    if 571 <= m <= 690:
        return m - 571
    if 781 <= m <= 900:
        return m - 781 + 120
    return None


def close_label(k: int) -> str:
    if not 0 <= k < 240:
        raise ValueError('minute index out of range')
    m = 571 + k if k < 120 else 781 + k - 120
    return f'{m // 60:02d}:{m % 60:02d}'


def position(stamp: datetime) -> tuple[int, int] | None:
    """(in-progress minute index, seconds elapsed); boundary closes are exact."""
    if stamp.tzinfo is None:
        raise ValueError('timezone-aware timestamp required')
    t = stamp.astimezone(CN)
    sec = t.hour * 3600 + t.minute * 60 + t.second
    if sec == 690 * 60:
        return 119, 60
    if sec == 900 * 60:
        return 239, 60
    if 570 * 60 <= sec < 690 * 60:
        return (sec - 570 * 60) // 60, t.second
    if 780 * 60 <= sec < 900 * 60:
        return 120 + (sec - 780 * 60) // 60, t.second
    return None


def completed_through(stamp: datetime) -> int:
    """Last completed minute, including lunch and close; preopen -> -1."""
    if stamp.tzinfo is None:
        raise ValueError('timezone-aware timestamp required')
    t = stamp.astimezone(CN).time()
    p = position(stamp)
    if p is not None:
        return p[0] if p[1] == 60 else p[0] - 1
    if time(11, 30) < t < time(13, 0):
        return 119
    if t >= time(15, 0):
        return 239
    return -1
