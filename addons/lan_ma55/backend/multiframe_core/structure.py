"""Lan MA55: drive -> support test -> first turn, one BUY per swing.

V0.2 thresholds with a later drive/actual MA55 retest and first-red recovery.
The caller supplies causal features and owns completed-bar/source validation.
Indices are persisted counters, independent of the caller's rolling K window.
"""
from copy import deepcopy
from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True)
class Rules:
    near_above: float = .010
    near_below: float = .018
    impulse_body: float = .008
    impulse_volume: float = 1.5
    impulse_memory: int = 48
    active_range: float = .025
    test_window: int = 4
    full_departure: float = .015
    deeper_foot: float = .005


def advance(previous, bar, raw_tick=.01, rules=Rules(), *, reset_pending_on_new_day=True):
    """One validated completed adjusted-price bar -> state and decision."""
    s = deepcopy(previous) if previous else dict(index=-1, last_impulse=None,
        pending=None, locked=False, last_buy=None, saw_downward_retest=False,
        previous_at=None, previous_close=None, previous_open=None,
        previous_touch=False)
    i = s['index'] + 1
    stamp = str(bar['timestamp'])
    level, ma = bar['known_ma55'], bar['ma55']
    ready = isfinite(level) and level > 0
    tick = raw_tick * bar['adjust_factor']
    red = bar['close'] - bar['open'] >= 2 * tick
    volume_push = any(isfinite(bar[k]) and bar[k] >= rules.impulse_volume
                      for k in ('vol20', 'vol_slot'))
    body = bar['close'] / bar['open'] - 1
    impulse = bool(red and volume_push and (body >= rules.impulse_body or
        (body >= .003 and bar['range16'] >= rules.active_range)))
    if impulse:
        s['last_impulse'] = dict(index=i, at=stamp, volume=bar['volume'],
            ratio20=bar['vol20'], ratio_slot=bar['vol_slot'],
            close=bar['close'], ma55=ma)
    active = s['last_impulse'] is not None and i - s['last_impulse']['index'] <= rules.impulse_memory
    contact = bool(ready and level * (1-rules.near_below) <= bar['low'] <= level * (1+rules.near_above)
                   and bar['high'] >= level * (1-rules.near_above))
    failed = ready and bar['close'] < ma * (1-rules.near_above)
    departed = ready and bar['low'] > level * (1+rules.full_departure) and bar['close'] > ma * (1+rules.full_departure)
    buy = s['last_buy']
    push = s['last_impulse']
    touch = bool(contact and bar['low'] <= level+tick)
    # A later drive must leave the test zone and progress beyond the last BUY.
    # Re-arm on its subsequent green test of the actual line, not on the drive.
    independent_test = bool(s['locked'] and buy and push and
        buy['index'] < push['index'] < i and push['close'] > buy['close']+tick and
        push['close'] > push['ma55']*(1+rules.near_above) and
        touch and bar['close'] < bar['open'] and bar['close'] < push['close'])
    deeper = bool(s['locked'] and ready and buy is not None and i-buy['index'] >= 2 and contact
        and s['previous_close'] is not None and bar['low'] < buy['low']-rules.deeper_foot*level
        and bar['close'] < s['previous_close'])
    event = veto = ''
    test = None
    if reset_pending_on_new_day and s['previous_at'] and stamp[:10] != s['previous_at'][:10]:
        s['pending'] = None
    if s['locked'] and departed:
        s.update(locked=False, pending=None, saw_downward_retest=False)
    fresh = bool(s['locked'] and impulse and contact and buy is not None
                 and i-buy['index'] >= 5 and s['saw_downward_retest'])
    if fresh or deeper or independent_test:
        s.update(locked=False, saw_downward_retest=False,
                 pending=dict(index=i, at=stamp, low=bar['low']))
    if s['locked'] and contact and bar['close'] < bar['open']:
        s['saw_downward_retest'] = True
    if not ready:
        veto = 'WARMUP'
    elif failed:
        s.update(locked=False, saw_downward_retest=False, pending=None)
        veto = 'CLOSE_BELOW_SUPPORT'
    elif s['locked']:
        veto = 'SAME_SUPPORT_SWING'
    else:
        if s['pending'] is not None and i-s['pending']['index'] > rules.test_window:
            s['pending'] = None
        prior_low = s['pending']['low'] if s['pending'] else float('nan')
        if contact:
            if s['pending'] is None:
                s['pending'] = dict(index=i, at=stamp, low=bar['low'])
            else:
                s['pending']['low'] = min(s['pending']['low'], bar['low'])
        test = deepcopy(s['pending'])
        first_reclaim = bool(test and test['index']<i and s['previous_touch']
            and s['previous_close'] < s['previous_open'] and
            bar['close'] > s['previous_close']+tick)
        if test is None:
            veto = 'AWAIT_SUPPORT_TEST'
        elif deeper:
            veto = 'NEW_LOWER_FOOT'
        elif (red and bar['close'] >= ma and active and
              (impulse or bar['range16'] >= rules.active_range or first_reclaim) and
              (impulse or i-test['index'] >= 2 or first_reclaim or
               (isfinite(prior_low) and bar['low'] < prior_low-tick))):
            event = 'BUY_STRUCTURE'
            s.update(locked=True, last_buy=dict(index=i, at=stamp, low=test['low'],close=bar['close']),
                     saw_downward_retest=False, pending=None)
        elif not active:
            veto = 'NO_REAL_ADVANCE'
        else:
            veto = 'AWAIT_FIRST_RED'
    s.update(index=i, previous_at=stamp, previous_close=bar['close'],
        previous_open=bar['open'],previous_touch=touch)
    decision = dict(timestamp=stamp, event=event, veto=veto,
        pattern='SINGLE_BAR' if event and test['at']==stamp else 'MULTI_BAR' if event else '',
        support_test_time=test['at'] if test else None, support_test_low=test['low'] if test else None,
        known_ma55=level, ma55=ma, volume_ratio20=bar['vol20'],
        volume_ratio_same_clock=bar['vol_slot'], impulse_now=impulse,
        impulse_recent=active, recent_range_pct=100*bar['range16'], contact=contact,
        close_distance_pct=100*(bar['close']/ma-1) if ready else None)
    return s, decision
