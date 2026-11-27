"""Pure turnover engine ported from ashare-volpulse v0.1.5 (MIT).
Amounts: CNY. Ratios: multiples. Deviations: percentage points.
Closed-minute cumulative maps use exchange indices 0..239.
"""
from dataclasses import dataclass
from math import isfinite
from typing import Mapping

Series = Mapping[int, float]


def valid(value):
    return type(value) in (int, float) and isfinite(value) and value >= 0


def amount_at(series: Series, k: int):
    v = series.get(k)
    return v if valid(v) else None


def complete(series: Series) -> bool:
    values = [amount_at(series, k) for k in range(240)]
    return all(v is not None for v in values) and values[-1] > 0 and all(
        a <= b for a, b in zip(values, values[1:]))


def aggregate(sh: Series, sz: Series) -> dict[int, float]:
    return {k: sh[k] + sz[k] for k in sorted(sh.keys() & sz.keys())
            if 0 <= k < 240 and valid(sh[k]) and valid(sz[k])}


@dataclass(frozen=True)
class Profile:
    sample_days: int
    full_day_mean: float
    shares: dict[int, float]
    auction_share: float | None


def build_profile(days: list[tuple[Series, float | None]]) -> Profile | None:
    """Use only full monotonic days. Caller supplies <=10 previous sessions."""
    eligible = [(bars, auc) for bars, auc in days if complete(bars)]
    if not eligible:
        return None
    n = len(eligible)
    shares = {k: sum(bars[k] / bars[239] for bars, _ in eligible) / n for k in range(240)}
    auction = [min(bars[0], auc) / bars[239] for bars, auc in eligible if valid(auc)]
    return Profile(n, sum(bars[239] for bars, _ in eligible) / n, shares,
                   min(shares[0], sum(auction) / len(auction)) if auction else None)


def align(series: Series, k: int, elapsed: int, auction=None) -> float | None:
    end = amount_at(series, k)
    if end is None:
        return None
    if elapsed == 60:
        return end
    start = auction if k == 0 else amount_at(series, k - 1)
    if not valid(start) or start > end:
        return None
    return start + (end - start) * elapsed / 60


def predict(today, full, share, prior=0.12):
    if not valid(today) or not valid(full) or full <= 0 or not valid(share) or share <= 0:
        return None
    w = min(1.0, share)
    if w == 1:
        return today
    alpha = w / (w + prior)
    observed = today / (full * w)
    return today + (alpha * observed + 1 - alpha) * full * (1 - w)


def ols(points: list[tuple[int, float]]) -> float | None:
    if len(points) < 2:
        return None
    xbar = sum(x for x, _ in points) / len(points)
    ybar = sum(y for _, y in points) / len(points)
    den = sum((x - xbar) ** 2 for x, _ in points)
    return sum((x - xbar) * (y - ybar) for x, y in points) / den if den else None


@dataclass(frozen=True)
class Config:
    window: int = 5
    expand_pct: float = 5
    shrink_pct: float = -5
    surge_multiple: float = 1.5
    shrink_multiple: float = 0.65
    surge_slope: float = 3.0
    shrink_slope: float = -3.0
    cooldown_minutes: int = 5
    prior: float = 0.12

    def __post_init__(self):
        nums = (self.expand_pct, self.shrink_pct, self.surge_multiple,
                self.shrink_multiple, self.surge_slope, self.shrink_slope, self.prior)
        if not all(type(x) in (int, float) and isfinite(x) for x in nums):
            raise ValueError('阈值须为有限数值')
        if not (type(self.window) is int and self.window in (3, 5, 10) and self.shrink_pct < 0 < self.expand_pct
                and 0 < self.shrink_multiple < 1 < self.surge_multiple
                and self.shrink_slope < 0 < self.surge_slope and self.prior >= 0
                and type(self.cooldown_minutes) is int and 1 <= self.cooldown_minutes <= 240):
            raise ValueError('请核对阈值方向、窗口和冷却分钟数')


def multiple_at(today: Series, prev: Series, k: int, w: int, a=None, b=None):
    if k < 1:
        return None
    start = max(0, k - w)
    if any(amount_at(today, j) is None or amount_at(prev, j) is None for j in range(start, k)):
        return None
    a = amount_at(today, k) if a is None else a
    b = amount_at(prev, k) if b is None else b
    if not valid(a) or not valid(b):
        return None
    prev_delta, today_delta = b - prev[start], a - today[start]
    if prev_delta <= 0 or today_delta < 0:
        return None
    tv = [today[j] for j in range(start, k)] + [a]
    pv = [prev[j] for j in range(start, k)] + [b]
    if any(v < u for u, v in zip(tv, tv[1:])) or any(v < u for u, v in zip(pv, pv[1:])):
        return None
    return today_delta / prev_delta


def evaluate(today: Series, prev: Series, k: int, *, elapsed=60, current=None,
             prev_auction=None, profile: Profile | None = None, config: Config = Config()) -> dict:
    """v0.1.5 partial-minute semantics. Missing interiors are rejected (stricter than upstream)."""
    if not 0 <= k < 240 or not 0 <= elapsed <= 60:
        raise ValueError('invalid exchange minute')
    a = (current if valid(current) else None) if current is not None else (amount_at(today, k) if elapsed == 60 else None)
    b = align(prev, k, elapsed, prev_auction)
    ratio = (a - b) / b * 100 if a is not None and b is not None and b > 0 else None
    state = ('UNAVAILABLE' if ratio is None else 'EXPANDING' if ratio >= config.expand_pct
             else 'SHRINKING' if ratio <= config.shrink_pct else 'FLAT')
    prediction, sample_days, prediction_basis = None, 0, None
    if a is not None and k == 239 and elapsed == 60:
        prediction = a
        prediction_basis = 'close'
    elif a is not None:
        if profile:
            share = align(profile.shares, k, elapsed, profile.auction_share)
            prediction = predict(a, profile.full_day_mean, share, config.prior)
            if prediction is not None:
                sample_days = profile.sample_days
                prediction_basis = 'profile'
        full = amount_at(prev, 239)
        if prediction is None and full and b and b > 0:
            prediction = predict(a, full, b / full, config.prior)
            if prediction is not None:
                sample_days = 1
                prediction_basis = 'previous'
    w = config.window
    prefix = multiple_at(today, prev, k, w, a, b) if a is not None and b is not None else None
    multiple = prefix if k >= w else None
    slope = None
    if k >= w and multiple is not None:
        points = []
        for j in range(k - w + 1, k + 1):
            m = multiple if j == k else multiple_at(today, prev, j, w)
            if m is None:
                break
            points.append((j, (m - 1) * 100))
        if len(points) == w:
            slope = ols(points)
    return dict(minute_index=k, elapsed_seconds=elapsed, today_amount=a, prev_amount=b,
                diff=a - b if ratio is not None else None,
                ratio_pct=ratio, state=state, predicted_amount=prediction,
                sample_days=sample_days, prediction_basis=prediction_basis, window=w, multiple=multiple,
                deviation_pct=(prefix - 1) * 100 if prefix is not None else None, slope=slope)


def alert_direction(metrics: dict, config: Config = Config(), *, degraded=False) -> str | None:
    if degraded or metrics['minute_index'] < 5 or metrics['window'] != config.window:
        return None
    m, s = metrics['multiple'], metrics['slope']
    if m is None or s is None or metrics['ratio_pct'] is None:
        return None
    if m >= config.surge_multiple and s >= config.surge_slope:
        return 'SURGE'
    if m <= config.shrink_multiple and s <= config.shrink_slope:
        return 'SHRINK'
    return None
