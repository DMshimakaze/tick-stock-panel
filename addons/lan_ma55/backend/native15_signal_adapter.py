"""Versioned input/state orchestration around the unchanged frozen pure functions."""

from math import isfinite

from .engine import RULE_VERSION, advance, minimal_features
from .pipeline import empty_record, invalidate, previous_end, serializable
from .market_grid import wall
from .native15_shadow import SOURCE, PROOF

FAMILY = "NATIVE15_REVIEW_V1"
RULE_REFERENCE = dict(version=RULE_VERSION, git_sha="5d1d0f6e8913f528110c93e054429f050b9851f6",
                      functions=["engine.minimal_features", "engine.advance"])


def scan(symbol, bars, *, sessions, raw_tick, target, now, previous=None,
         basis_revision=0, factor_revision=0, live_after, replay=False):
    """Native proof is validated as one candle, never passed to frozen pipeline."""
    state = previous or empty_record(symbol)["state"]
    added = []
    seen, end = wall(now), wall(target)
    for raw in bars:
        stamp = raw["timestamp"]
        at = wall(stamp)
        if at > end or state["processed_at"] and stamp <= state["processed_at"]:
            continue
        factor = raw.get("adjust_factor")
        factor_ok = isinstance(factor, (float, int)) and isfinite(factor) and factor > 0
        proof = raw.get("source_id") == SOURCE and raw.get("source_count") == 1 and raw.get("source_proof") == PROOF
        numeric = all(type(raw.get(k)) in (int, float) and isfinite(raw[k]) and raw[k] > 0
                      for k in ("open", "high", "low", "close", "volume"))
        reason = ("INVALID_NATIVE15_PROOF" if not proof
                  else "NON_TRADING_SESSION" if stamp[:10] not in sessions
                  else "INVALID_OR_INCOMPLETE_OHLCV" if not numeric or not raw["low"] <= min(raw["open"], raw["close"]) <= max(raw["open"], raw["close"]) <= raw["high"]
                  else "HISTORICAL_FACTOR_UNAVAILABLE" if not factor_ok
                  else "TICK_UNAVAILABLE" if type(raw_tick) not in (int, float) or not isfinite(raw_tick) or raw_tick <= 0
                  else "PRICE_BASIS_CHANGED" if state["last_factor"] and abs(factor/state["last_factor"]-1) > 1e-6
                  else "MISSING_COMPLETED_15M" if state["processed_at"] and previous_end(at, sessions) != state["processed_at"] else "")
        state.update(processed_at=stamp, last_factor=factor if factor_ok else None, sequence=state["sequence"]+1)
        if reason:
            invalidate(state, reason)
            continue
        sample = {k: raw[k] for k in ("open", "high", "low", "close", "volume")}
        sample.update(timestamp=stamp, adjust_factor=factor)
        slot = stamp[11:16]
        volumes = state["slot_volumes"].get(slot, [])
        feature = minimal_features(state["feature_history"], sample, volumes)
        structure, decision = advance(state["structure"], feature, raw_tick)
        state.update(structure=structure, decision=decision, valid_run=state["valid_run"]+1,
                     data_asof=stamp, reason=decision["veto"] or decision["event"],
                     close=feature["close"], ma55=feature["ma55"],
                     feature_history=(state["feature_history"]+[sample])[-55:])
        state["slot_volumes"][slot] = (volumes+[sample["volume"]])[-5:]
        if decision["event"]:
            mode = "REPLAY"
            if not replay and at > wall(live_after) and seen.date() == at.date():
                mode = "LIVE" if 75 <= (seen-at).total_seconds() <= 90 else "DELAYED"
            identity = f"{FAMILY}:{symbol}:SUPPORT:{decision['support_test_time'].replace(' ', 'T')}:TURN:{stamp.replace(' ', 'T')}"
            added.append(dict(event_id=identity, symbol=symbol, signal_family=FAMILY, rule_version=FAMILY,
                              rule_reference=RULE_REFERENCE, input_contract="native_15m_completed",
                              source_id=SOURCE, source_count=1, source_proof=PROOF,
                              event_kind="REVIEW_CANDIDATE", setup_at=decision["support_test_time"],
                              confirmed_at=stamp, first_seen_at=now, source_asof=target, data_asof=stamp,
                              mode=mode, pattern=decision["pattern"], confirm_close=feature["close"],
                              defense=decision["support_test_low"], raw_tick=raw_tick, price_basis="QFQ",
                              basis_asof=target[:10], basis_id=f"{SOURCE}:{target[:10]}:{basis_revision}",
                              basis_revision=basis_revision, factor_revision=factor_revision,
                              adjust_factor=factor, decision=decision, impulse=structure["last_impulse"],
                              risk_text="新行情口径，人工复核"+("；离均线较远，不追价" if decision["close_distance_pct"] > 3 else "")))
    return serializable(state), serializable(added)
