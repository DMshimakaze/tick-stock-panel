"""Synthetic parity cases from research 4440e198; private replay stays local."""
from datetime import datetime, timedelta
import json

import pytest

from addons.lan_ma55.backend.multiframe_core.multiframe import TIMEFRAMES, analyze_timeframe


def stamps(tf):
    d = datetime(2024, 1, 1)
    while True:
        if d.weekday() < 5:
            if tf == 'weekly':
                if d.weekday() == 4:
                    yield d.replace(hour=15)
            elif tf == 'daily':
                yield d.replace(hour=15)
            else:
                step = int(tf[:-1])
                for hour, minute in ((9, 30), (13, 0)):
                    for n in range(step, 121, step):
                        yield d.replace(hour=hour, minute=minute) + timedelta(minutes=n)
        d += timedelta(days=1)


def add(bars, clock, o=100, h=100.2, l=99.8, c=100, v=100):
    bars.append(dict(timestamp=next(clock), open=o, high=h, low=l, close=c, volume=v))


def warm(tf):
    clock = stamps(tf)
    bars = []
    for _ in range(55):
        add(bars, clock)
    return bars, clock


def replay(tf, bars, cutoff=None):
    return analyze_timeframe('test', tf, bars, asof=cutoff or bars[-1]['timestamp'])


@pytest.mark.parametrize('tf', TIMEFRAMES)
def test_single_bar_and_same_swing(tf):
    bars, clock = warm(tf)
    add(bars, clock, 99.8, 101.2, 99.7, 101, 250)
    signal_at = bars[-1]['timestamp']
    add(bars, clock, 100.6, 101.3, 100.4, 101, 100)
    result = replay(tf, bars)
    assert len(result['events']) == 1
    event = result['events'][0]
    assert event['pattern'] == 'SINGLE_BAR'
    assert event['timeframe'] == tf and tf in event['event_id']
    assert event['timestamp'] == str(signal_at)
    assert result['latest']['event_id'] is None
    assert result['events'] == replay(tf, bars, signal_at)['events']
    assert result == replay(tf, bars)
    json.dumps(result, allow_nan=False)
    if tf in ('daily', 'weekly'):
        assert event['volume_ratio_same_clock'] is None


@pytest.mark.parametrize('tf', TIMEFRAMES[1:])
def test_first_red_after_support_can_span_sessions(tf):
    bars, clock = warm(tf)
    add(bars, clock, 101.2, 102.8, 101.2, 102.5, 300)
    per_day = 240 // int(tf[:-1]) if tf.endswith('m') else 1
    quiet = 16 + (-(len(bars) + 16 + 1)) % per_day
    for _ in range(quiet):
        add(bars, clock, 103, 103.1, 102.9, 103, 100)
    ma = sum(b['close'] for b in bars[-55:]) / 55
    add(bars, clock, 103, 103, ma, ma + .01, 90)
    test_at = bars[-1]['timestamp']
    add(bars, clock, ma + .01, ma + .12, ma + .005, ma + .10, 60)
    assert bars[-1]['timestamp'].date() != test_at.date()
    assert not replay(tf, bars[:-1])['events']
    result = replay(tf, bars)
    assert result['latest']['event'] == 'BUY_STRUCTURE'
    assert result['latest']['pattern'] == 'MULTI_BAR'
    assert result['latest']['support_test_time'] == str(test_at)
    assert result['latest']['volume_ratio20'] < 1


@pytest.mark.parametrize('tf', TIMEFRAMES)
def test_pressure_and_no_chasing(tf):
    bars, clock = warm(tf)
    add(bars, clock, 99.7, 99.9, 99.5, 99.6, 100)
    add(bars, clock, 99.6, 100.1, 99.5, 99.8, 250)
    result = replay(tf, bars)
    assert result['latest']['pressure_test']
    assert result['latest']['ma55_side'] == 'BELOW'
    assert not result['events']
    bars, clock = warm(tf)
    add(bars, clock, 103, 105, 102.9, 104.5, 300)
    assert not replay(tf, bars)['events']


def test_cutoff_warmup_grid_and_duplicate():
    bars, clock = warm('15m')
    add(bars, clock, 99.8, 101.2, 99.7, 101, 250)
    result = replay('15m', bars, bars[-2]['timestamp'])
    assert not result['events'] and result['latest']['veto'] == 'WARMUP'
    assert analyze_timeframe('test', '15m', [], asof=bars[-1]['timestamp'])['latest'] is None
    with pytest.raises(ValueError, match='unique'):
        replay('15m', bars + [bars[-1]])
    with pytest.raises(ValueError, match='bar end'):
        replay('15m', [dict(bars[0], timestamp=datetime(2024, 1, 1, 9, 46))])


def test_invalid_price_and_period():
    bars, _ = warm('daily')
    with pytest.raises(ValueError, match='timeframe'):
        replay('5m', bars)
    bars[-1]['low'] = 101
    with pytest.raises(ValueError, match='price range'):
        replay('daily', bars)
