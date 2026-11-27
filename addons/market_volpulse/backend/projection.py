"""Read the plugin cache and project a view; never write source data or emit alerts."""
from dataclasses import asdict, dataclass, replace
from datetime import datetime, time

from .clock import CN, close_label, completed_through, position
from .energy import Config, Profile, aggregate, build_profile, complete, evaluate, multiple_at, valid
from .providers import DISPLAY, NAMES, Day, aggregate_quote

VIEW_TABLE = 'market_volpulse_view_v1'
MARKETS = ('000001.SH', '399106.SZ')


@dataclass
class Baseline:
    today: Day
    previous: Day
    profile: Profile | None
    points: list
    cutoff: int


def _day(store, symbol, day, source):
    if symbol != 'MARKET_TOTAL':
        return store.load(symbol, day, source)
    sh, sz = (store.load(part, day, source) for part in MARKETS)
    auction = sh.auction + sz.auction if valid(sh.auction) and valid(sz.auction) else None
    return Day(symbol, day, source, auction, aggregate(sh.amounts, sz.amounts), {})


def _delta(day, k):
    value, anchor = day.amounts.get(k), day.auction if k == 0 else day.amounts.get(k-1)
    return value - anchor if valid(value) and valid(anchor) and value >= anchor else None


def cutoff_for(day, now):
    today = now.astimezone(CN).date().isoformat()
    if day != today:
        return 239 if day < today else -1
    return completed_through(now)


def _coverage(pair, cutoff):
    today, previous = pair
    points = {k for k in today.amounts if k <= cutoff}
    return len(points & previous.amounts.keys()), len(points), int(valid(today.auction)), len(previous.amounts)


def prepare_view(store, *, day, previous, prior_sessions, cutoff, history_source, window):
    available = store.available_days()
    prior = sorted({d for d in prior_sessions if d < day and d in available}, reverse=True)
    sources = ('tencent', 'eastmoney') if history_source == 'auto' else (history_source,)
    baselines = []
    for symbol in DISPLAY:
        pairs = [(_day(store, symbol, day, source), _day(store, symbol, previous, source))
                 for source in sources]
        t, p = max(pairs, key=lambda pair: _coverage(pair, cutoff))
        t = replace(t, amounts={k: v for k, v in t.amounts.items() if k <= cutoff})
        profile_days = []
        for date in prior:
            past = p if date == previous else _day(store, symbol, date, t.source)
            if complete(past.amounts):
                profile_days.append((past.amounts, past.auction))
            if len(profile_days) == 10:
                break
        profile = build_profile(profile_days)
        points = [
            dict(k=k, time=close_label(k), today=t.amounts.get(k), previous=p.amounts.get(k),
                 today_delta=_delta(t, k), previous_delta=_delta(p, k),
                 multiple=multiple_at(t.amounts, p.amounts, k, window) if k >= window and k in t.amounts else None,
                 price=t.prices.get(k) if k in t.amounts else None)
            for k in range(240)
        ]
        baselines.append(Baseline(t, p, profile, points, cutoff))
    return baselines


def _project_row(baseline, quote, now, config, phase, compatible_sources, max_age):
    t, p = baseline.today, baseline.previous
    profile, points, cutoff = baseline.profile, baseline.points, baseline.cutoff
    pos = position(quote.stamp) if quote else None
    market_pos = position(now)
    allowed = t.source == 'tencent' and quote and quote.source in compatible_sources
    fresh = bool(phase == 'LIVE' and allowed and pos and market_pos and pos[0] == market_pos[0]
                 and quote.stamp.date().isoformat() == t.date and 0 <= (now - quote.stamp).total_seconds() <= max_age)
    snapshot, stamp, price, reason = None, None, None, None
    if fresh:
        k, elapsed = pos
        anchor = t.auction if k == 0 else t.amounts.get(k - 1)
        fresh = k not in t.amounts and (anchor is None or quote.amount >= anchor)
    if fresh:
        metrics = evaluate(t.amounts, p.amounts, k, elapsed=elapsed, current=quote.amount,
                           prev_auction=p.auction, profile=profile, config=config)
        stamp, price = quote.stamp.isoformat(), quote.price
        snapshot = dict(k=k, elapsed=elapsed, stamp=stamp, today=quote.amount,
                        previous=metrics['prev_amount'], multiple=metrics['multiple'], price=price)
    elif t.amounts:
        k = max(t.amounts)
        metrics = evaluate(t.amounts, p.amounts, k, prev_auction=p.auction, profile=profile, config=config)
        stamp, price = t.date + 'T' + close_label(k) + ':00+08:00', t.prices.get(k)
    else:
        metrics = {**evaluate({}, {}, 0, config=config), 'minute_index': None}

    auction_available = t.date != now.date().isoformat() or now.time() >= time(9, 25)
    auction = dict(today=t.auction if auction_available else None, previous=p.auction)
    covered = len(t.amounts)
    expected = cutoff + 1
    status = 'UNAVAILABLE'
    if phase == 'AUCTION':
        status = 'AUCTION' if auction['today'] is not None else 'UNAVAILABLE'
        reason = None if status == 'AUCTION' else '等待 09:25 集合竞价终态'
    elif phase == 'LIVE' and not fresh:
        status = 'DEGRADED' if covered else 'UNAVAILABLE'
        if t.source == 'eastmoney':
            reason = '东方财富同源分时，实时比较和异动暂停'
        elif quote and quote.source == 'sina' and not allowed:
            reason = '新浪快照与腾讯基线的连续两日口径待确认'
        else:
            reason = '等待同分钟的有效行情快照'
    elif fresh or covered:
        if covered < expected:
            status, reason = 'DEGRADED', '闭合分钟待补齐'
        elif metrics['ratio_pct'] is None:
            status, reason = 'DEGRADED', '昨日同期分时缺失'
        else:
            status = 'LIVE' if fresh else 'HISTORICAL'
    else:
        reason = '等待有效分时'
    previous_close = p.prices.get(239)
    change = (price / previous_close - 1) * 100 if price and previous_close else None
    if fresh and quote.change_pct is not None:
        change = quote.change_pct
    return dict(symbol=t.symbol, name=NAMES.get(t.symbol, '沪深两市'), source=t.source,
                quote_source=quote.source if quote else None, status=status, reason=reason, stamp=stamp,
                price=price, change_pct=change, metrics=metrics, points=points,
                snapshot=snapshot, auction=auction, covered_minutes=covered, expected_minutes=expected)


def build_view(store, *, day: str, previous: str, prior_sessions, now: datetime, quotes: dict,
               history_source='tencent', config: Config = Config(), live=False, phase=None,
               compatible_snapshot_sources=('tencent',), sina_verified=(), max_age=20, baselines=None) -> dict:
    if now.tzinfo is None or previous >= day:
        raise ValueError('verified previous session and timezone-aware clock required')
    now = now.astimezone(CN)
    phase = phase or ('LIVE' if live else 'HISTORICAL')
    mode = 'LIVE' if phase == 'LIVE' else 'HISTORICAL'
    if baselines is None:
        baselines = prepare_view(store, day=day, previous=previous, prior_sessions=prior_sessions,
                                 cutoff=cutoff_for(day, now), history_source=history_source, window=config.window)
    all_quotes = dict(quotes, MARKET_TOTAL=aggregate_quote(quotes))
    indices = []
    for baseline in baselines:
        symbol = baseline.today.symbol
        verified = set(MARKETS) <= set(sina_verified) if symbol == 'MARKET_TOTAL' else symbol in sina_verified
        sources = (*compatible_snapshot_sources, 'sina') if verified else compatible_snapshot_sources
        indices.append(_project_row(baseline, all_quotes.get(symbol), now, config, phase, sources, max_age))
    events = [event for event in store.events(day) if datetime.fromisoformat(event['stamp']) <= now]
    return dict(schema_version=1, trade_date=day, previous_date=previous, phase=phase,
                generated_at=now.isoformat(), mode=mode, window=config.window,
                settings=asdict(config), indices=indices, events=events)
