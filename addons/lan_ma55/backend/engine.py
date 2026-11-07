"""Frozen Rules/advance/minimal_features from MA55 commit 23477622536d.

Input is completed, consistently adjusted 15m OHLCV. No data acquisition or DB.
Structure blob: 0e777b61d9c33c9fdae4892508491e24824b0a5e.
Features blob: e682a304c493b1ff8c78a67980f51096056451ab.
"""

from copy import deepcopy
from dataclasses import dataclass
from math import fsum, isfinite
from statistics import median

RULE_VERSION = "MA55_15M_MINIMAL_V5"


@dataclass(frozen=True)
class Rules:
    near_above: float = 0.010
    near_below: float = 0.018
    impulse_body: float = 0.008
    impulse_volume: float = 1.5
    impulse_memory: int = 48
    active_range: float = 0.025
    test_window: int = 4
    full_departure: float = 0.015
    deeper_foot: float = 0.005


def minimal_features(history, bar, slot_volumes):
    nan = float("nan")
    rows = history + [bar]
    known = fsum(r["close"] for r in history[-55:]) / 55 if len(history) >= 55 else nan
    ma = fsum(r["close"] for r in rows[-55:]) / 55 if len(rows) >= 55 else nan
    baseline = median(r["volume"] for r in history[-20:]) if len(history) >= 20 else nan
    slot_base = median(slot_volumes[-5:]) if len(slot_volumes) >= 3 else nan
    tr = [
        max(
            r["high"] - r["low"],
            abs(r["high"] - rows[i - 1]["close"]),
            abs(r["low"] - rows[i - 1]["close"]),
        )
        if i
        else r["high"] - r["low"]
        for i, r in enumerate(rows)
    ]
    return dict(
        bar,
        known_ma55=known,
        ma55=ma,
        range16=max(r["high"] for r in rows[-16:]) / min(r["low"] for r in rows[-16:])
        - 1
        if len(rows) >= 16
        else nan,
        volume_baseline=baseline,
        vol20=bar["volume"] / baseline if baseline > 0 else nan,
        vol_slot=bar["volume"] / slot_base if slot_base > 0 else nan,
        slot_baseline=slot_base,
        atr20=fsum(tr[-20:]) / 20 if len(tr) >= 20 else nan,
    )


def advance(previous, bar, raw_tick=0.01, rules=Rules()):
    s = (
        deepcopy(previous)
        if previous
        else dict(
            index=-1,
            last_impulse=None,
            pending=None,
            locked=False,
            last_buy=None,
            saw_downward_retest=False,
            previous_at=None,
            previous_close=None,
        )
    )
    i = s["index"] + 1
    stamp = str(bar["timestamp"])
    level, ma = bar["known_ma55"], bar["ma55"]
    ready = isfinite(level) and level > 0
    tick = raw_tick * bar["adjust_factor"]
    red = bar["close"] - bar["open"] >= 2 * tick
    volume_push = any(
        isfinite(bar[k]) and bar[k] >= rules.impulse_volume
        for k in ("vol20", "vol_slot")
    )
    body = bar["close"] / bar["open"] - 1
    impulse = bool(
        red
        and volume_push
        and (
            body >= rules.impulse_body
            or (body >= 0.003 and bar["range16"] >= rules.active_range)
        )
    )
    if impulse:
        s["last_impulse"] = dict(
            index=i,
            at=stamp,
            volume=bar["volume"],
            ratio20=bar["vol20"],
            ratio_slot=bar["vol_slot"],
        )
    active = (
        s["last_impulse"] is not None
        and i - s["last_impulse"]["index"] <= rules.impulse_memory
    )
    contact = bool(
        ready
        and level * (1 - rules.near_below)
        <= bar["low"]
        <= level * (1 + rules.near_above)
        and bar["high"] >= level * (1 - rules.near_above)
    )
    failed = ready and bar["close"] < ma * (1 - rules.near_above)
    departed = (
        ready
        and bar["low"] > level * (1 + rules.full_departure)
        and bar["close"] > ma * (1 + rules.full_departure)
    )
    buy = s["last_buy"]
    deeper = bool(
        s["locked"]
        and ready
        and buy is not None
        and i - buy["index"] >= 2
        and contact
        and s["previous_close"] is not None
        and bar["low"] < buy["low"] - rules.deeper_foot * level
        and bar["close"] < s["previous_close"]
    )
    event = veto = ""
    test = None
    if s["previous_at"] and stamp[:10] != s["previous_at"][:10]:
        s["pending"] = None
    if s["locked"] and departed:
        s.update(locked=False, pending=None, saw_downward_retest=False)
    fresh = bool(
        s["locked"]
        and impulse
        and contact
        and buy is not None
        and i - buy["index"] >= 5
        and s["saw_downward_retest"]
    )
    if fresh or deeper:
        s.update(
            locked=False,
            saw_downward_retest=False,
            pending=dict(index=i, at=stamp, low=bar["low"]),
        )
    if s["locked"] and contact and bar["close"] < bar["open"]:
        s["saw_downward_retest"] = True
    if not ready:
        veto = "WARMUP"
    elif failed:
        s.update(locked=False, saw_downward_retest=False, pending=None)
        veto = "CLOSE_BELOW_SUPPORT"
    elif s["locked"]:
        veto = "SAME_SUPPORT_SWING"
    else:
        if s["pending"] is not None and i - s["pending"]["index"] > rules.test_window:
            s["pending"] = None
        prior_low = s["pending"]["low"] if s["pending"] else float("nan")
        if contact:
            if s["pending"] is None:
                s["pending"] = dict(index=i, at=stamp, low=bar["low"])
            else:
                s["pending"]["low"] = min(s["pending"]["low"], bar["low"])
        test = deepcopy(s["pending"])
        if test is None:
            veto = "AWAIT_SUPPORT_TEST"
        elif deeper:
            veto = "NEW_LOWER_FOOT"
        elif (
            red
            and bar["close"] >= ma
            and active
            and (impulse or bar["range16"] >= rules.active_range)
            and (
                impulse
                or i - test["index"] >= 2
                or (isfinite(prior_low) and bar["low"] < prior_low - tick)
            )
        ):
            event = "BUY_STRUCTURE"
            s.update(
                locked=True,
                last_buy=dict(index=i, at=stamp, low=test["low"]),
                saw_downward_retest=False,
                pending=None,
            )
        elif not active:
            veto = "NO_REAL_ADVANCE"
        else:
            veto = "AWAIT_FIRST_RED"
    s.update(index=i, previous_at=stamp, previous_close=bar["close"])
    decision = dict(
        timestamp=stamp,
        event=event,
        veto=veto,
        pattern="SINGLE_BAR"
        if event and test["at"] == stamp
        else "MULTI_BAR"
        if event
        else "",
        support_test_time=test["at"] if test else None,
        support_test_low=test["low"] if test else None,
        known_ma55=level,
        ma55=ma,
        volume_ratio20=bar["vol20"],
        volume_ratio_same_clock=bar["vol_slot"],
        impulse_now=impulse,
        impulse_recent=active,
        recent_range_pct=100 * bar["range16"],
        contact=contact,
        close_distance_pct=100 * (bar["close"] / ma - 1) if ready else None,
    )
    return s, decision
