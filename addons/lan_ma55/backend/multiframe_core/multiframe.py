"""Independent MA55 observations from one completed OHLCV series.

No market universe, higher-period filters, I/O or desktop dependencies.
The original 15m production entry and rule version are unchanged.
"""
from datetime import datetime, timedelta, timezone
from math import isfinite

from .features import minimal_features
from .structure import Rules, advance

VERSION = 'MA55_MULTIFRAME_R1_RESEARCH_V1'
TIMEFRAMES = ('15m', '30m', '60m', '120m', 'daily', 'weekly')
DEFAULTS = Rules()
CHINA = timezone(timedelta(hours=8))


def _local_time(value):
    stamp = datetime.fromisoformat(str(value))
    return stamp.astimezone(CHINA).replace(tzinfo=None) if stamp.tzinfo else stamp


def _end_on_grid(stamp, timeframe):
    if stamp.second or stamp.microsecond:
        return False
    minute = stamp.hour * 60 + stamp.minute
    if timeframe in ('daily', 'weekly'):
        return minute == 900
    step = int(timeframe[:-1])
    return any(start < minute <= start + 120 and (minute - start) % step == 0
               for start in (570, 780))


def _number(value):
    return value if value is not None and isfinite(value) else None


def analyze_timeframe(symbol, timeframe, bars, *, asof, raw_tick=.01, rules=DEFAULTS):
    """Replay a single symbol/period from a fixed history origin.

    bars: ascending, complete, gap-checked adjusted OHLCV with end timestamps;
    adjust_factor (adjusted/raw) defaults to 1 for unadjusted prices.
    asof: explicit Beijing time cutoff. Caller owns session/calendar validation.
    Returns JSON-safe latest observation and historical BUY events. No state I/O.
    """
    if timeframe not in TIMEFRAMES:
        raise ValueError(f'Unsupported timeframe: {timeframe}')
    if not isfinite(raw_tick) or raw_tick <= 0:
        raise ValueError('raw_tick must be positive')
    cutoff = _local_time(asof)
    history, slots, events = [], {}, []
    state = latest = previous_at = None
    intraday = timeframe.endswith('m')

    for row in bars:
        stamp = _local_time(row['timestamp'])
        if stamp > cutoff:
            break
        if previous_at is not None and stamp <= previous_at:
            raise ValueError('Completed bars must be strictly ascending and unique')
        if not _end_on_grid(stamp, timeframe):
            raise ValueError(f'Invalid {timeframe} bar end: {stamp}')
        bar = {k: float(row[k]) for k in ('open', 'high', 'low', 'close', 'volume')}
        bar.update(timestamp=stamp, adjust_factor=float(row.get('adjust_factor', 1)))
        if not all(isfinite(v) and v > 0 for k, v in bar.items() if k != 'timestamp'):
            raise ValueError(f'Invalid OHLCV or adjustment factor: {stamp}')
        if not bar['low'] <= min(bar['open'], bar['close']) <= max(bar['open'], bar['close']) <= bar['high']:
            raise ValueError(f'Invalid price range: {stamp}')

        slot = stamp.strftime('%H:%M')
        samples = slots.get(slot, []) if intraday else []
        feature = minimal_features(history, bar, samples)
        state, decision = advance(state, feature, raw_tick, rules,
                                  reset_pending_on_new_day=(timeframe == '15m'))
        level, known = feature['ma55'], feature['known_ma55']
        close = bar['close']
        # A rejected test from below is evidence, never a sell or BUY veto.
        pressure = bool(history and isfinite(known) and
                        history[-1]['close'] < known <= bar['high'] and close < level)
        latest = {k: _number(v) if isinstance(v, float) else v for k, v in decision.items()}
        latest.update(symbol=symbol, timeframe=timeframe, rule_version=VERSION,
                      close=close, ma55_side=('ABOVE' if close > level else
                      'BELOW' if close < level else 'ON') if isfinite(level) else None,
                      pressure_test=pressure, event_id=None)
        if decision['event']:
            latest['event_id'] = f'{VERSION}:{symbol}:{timeframe}:{stamp.isoformat()}'
            events.append(latest.copy())

        history = (history + [bar])[-55:]
        if intraday:
            slots[slot] = (samples + [bar['volume']])[-5:]
        previous_at = stamp

    return dict(symbol=symbol, timeframe=timeframe, rule_version=VERSION,
                latest=latest, events=events)
