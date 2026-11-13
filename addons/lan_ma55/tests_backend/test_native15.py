from copy import deepcopy
import json
from time import time

import pytest

from addons.lan_ma55.backend.native15_owner import NativeOwner, candidates_page
from addons.lan_ma55.backend.native15_shadow import SOURCE, PROOF
from addons.lan_ma55.backend.native15_scheduler import task
from addons.lan_ma55.backend.native15_signal_adapter import FAMILY
from addons.lan_ma55.backend.market_grid import aggregate, project, derived, MAS, points
from addons.lan_ma55.backend.unified_store import UnifiedStore
from addons.lan_ma55.tests_market.test_rolling import sample
from addons.lan_ma55.migrate_unified import row_digests


def native(rows, sessions):
    return {b["timestamp"]: dict(b, source_id=SOURCE, source=SOURCE, source_count=1, source_proof=PROOF)
            for b in aggregate(rows, sessions, max(rows))["15m"]}


def prepare(owner, sessions, count=1):
    rows = [dict(symbol=f"{i:06d}.SZ", name=f"sample{i}", source_type="stock", raw_tick=.01, eligible=True) for i in range(count)]
    owner.refresh_master(rows, dict(expected=count, stock=count, etf=0), sessions)
    return [r["symbol"] for r in rows]


def accept(owner, bars, sessions, *, symbols=None, factors=None, stage="B"):
    symbols = symbols or ["000000.SZ"]
    end = max(bars)
    with owner.conn:
        owner.set_meta("target", end)
    owner.market.attempt(symbols, end, end, stage)
    owner.accept_batch(symbols, {s: bars for s in symbols}, {}, sessions=sessions, start=min(bars), end=end,
                       now=end, factors=factors if factors is not None else {s: [] for s in symbols}, stage=stage, live_after=end)


def test_native_partition_preserves_archive_and_source_count_on_restart(tmp_path):
    sessions, five = sample(64)
    path = tmp_path/"state.sqlite3"
    with UnifiedStore(path) as old:
        prepare(old, sessions)
        old.accept_market("000000.SZ", five, sessions=sessions, start=min(five), end=max(five), now=max(five), events=[], factor_response=True, live_after=max(five))
        archive = [tuple(r) for r in old.conn.execute("SELECT * FROM bar15")]
        old_facts = {t: row_digests(old.conn, t) for t in ("signal_event", "event_risk")}
    for _ in range(2):
        with NativeOwner(path) as owner:
            accept(owner, native(five, sessions), sessions)
            assert [tuple(r) for r in owner.conn.execute("SELECT * FROM bar15")] == archive
            assert {t: row_digests(owner.conn, t) for t in old_facts} == old_facts
            assert set(r[0] for r in owner.conn.execute("SELECT source_count FROM native15_bar")) == {1}
            status = owner.status_snapshot(target=max(five))
            assert status["today_native15_saved"] == status["today_analyzed_candidates"] == 1
            assert owner.conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"


@pytest.mark.parametrize("case_name", ["single", "departure", "deeper", "fresh"])
def test_real_core_candidate_trace_and_restart_idempotency(tmp_path, scenarios, case_name):
    case = scenarios[case_name]
    bars = {b["timestamp"]: dict(b, source_id=SOURCE, source=SOURCE, source_count=1, source_proof=PROOF, amount=None) for b in case["bars"]}
    path = tmp_path/"state.sqlite3"
    with NativeOwner(path) as owner:
        prepare(owner, case["sessions"])
        accept(owner, bars, case["sessions"])
        events = candidates_page(path)["rows"]
        assert events and all(e["signal_family"] == FAMILY and e["source_count"] == 1 and e["mode"] == "REPLAY" for e in events)
        # Oracle uses the same frozen core on a legal original three-five fixture.
        from addons.lan_ma55.backend.pipeline import process_symbol
        expected, _ = process_symbol("000000.SZ", case["bars"], sessions=case["sessions"], raw_tick=.01,
                                    source_asof=max(bars), first_seen_at=max(bars))
        assert [(e["confirmed_at"], e["setup_at"]) for e in reversed(events)] == [(e["confirmed_at"], e["setup_at"]) for e in expected["confirmations"]]
        state = json.loads(owner.conn.execute("SELECT payload FROM native15_signal_state").fetchone()[0])
        assert state["structure"] == expected["state"]["structure"]
        facts = [tuple(r) for r in owner.conn.execute("SELECT * FROM native15_candidate")]
        assert owner.conn.execute("SELECT count(*) FROM signal_event").fetchone()[0] == 0
    with NativeOwner(path) as owner:
        accept(owner, bars, case["sessions"])
        assert [tuple(r) for r in owner.conn.execute("SELECT * FROM native15_candidate")] == facts


def test_priority_retry_same_target_fairness_and_history_batch(tmp_path):
    sessions, five = sample(80)
    bars = native(five, sessions)
    with NativeOwner(tmp_path/"state.sqlite3") as owner:
        prepare(owner, sessions, 250)
        store = owner.market
        first = task(store, store.universe(), sessions, max(bars), max(bars))
        assert first[2] == "B" and len(first[3]) == 100
        store.attempt(first[3], first[1], max(bars), "B")
        store.failed(first[3], first[1], max(bars), "SOURCE_UNAVAILABLE", stage="B")
        second = task(store, store.universe(), sessions, max(bars), max(bars))
        assert not set(first[3]) & set(second[3])
        accept(owner, bars, sessions, symbols=second[3])
        # A new live bucket beats both remaining B and chart history C.
        target = next(t for t in points(sessions, max(bars), sessions[-1]+" 15:00:00", step=15) if t > max(bars))
        live = task(store, store.universe(), sessions, target, target)
        assert live[2] == "A" and set(live[3]) == set(second[3])
        with owner.conn:
            owner.conn.execute("UPDATE native15_retry SET next_eligible=0")
        # attempted==target remains eligible when its persisted retry expires.
        store.failed(second[3], target, target, "SOURCE_UNAVAILABLE", stage="A", delay=0)
        again = task(store, store.universe(), sessions, target, target)
        assert again[2] == "A" and again[1] == target
        assert time() >= store.instrument(again[3][0])["next_retry"]


def test_native_mas_are_source_isolated_and_history_does_not_replay_candidates(tmp_path, scenarios):
    sessions, five = sample(1280)
    bars = native(five, sessions)
    with NativeOwner(tmp_path/"state.sqlite3") as owner:
        prepare(owner, sessions)
        recent = dict(list(bars.items())[-64:])
        accept(owner, recent, sessions)
        state = owner.conn.execute("SELECT payload FROM native15_signal_state").fetchone()[0]
        accept(owner, dict(list(bars.items())[:-64]), sessions, stage="C")
        assert owner.conn.execute("SELECT payload FROM native15_signal_state").fetchone()[0] == state
        owner.market.warm_cache(["000000.SZ"])
        for tf in ("15m", "30m", "60m"):
            actual = owner.market.read("000000.SZ", tf, max(bars))
            expected = project(derived(list(bars.values()), tf, sessions, max(bars)), [], max(bars), sessions=sessions, timeframe=tf)
            assert actual["cache"]["hit"]
            for left, right in zip(actual["bars"], expected[-actual["row_count"]:]):
                assert left["source_id"] == SOURCE
                for n in MAS:
                    assert left.get(f"ma{n}") == pytest.approx(right.get(f"ma{n}"))


def test_missing_factor_tick_gap_and_bad_proof_do_not_publish_fake_candidate(tmp_path, scenarios):
    sessions, five = sample(64)
    bars = native(five, sessions)
    with NativeOwner(tmp_path/"state.sqlite3") as owner:
        prepare(owner, sessions, 2)
        accept(owner, bars, sessions, symbols=["000000.SZ", "000001.SZ"], factors={"000000.SZ": [], "000001.SZ": None})
        status = owner.status_snapshot(target=max(bars))
        assert status["today_native15_saved"] == 2 and status["today_analyzed_candidates"] == 1
        assert status["factor_pending"] == 1
        corrupted = deepcopy(bars)
        corrupted[max(bars)]["source_count"] = 3
        accept(owner, corrupted, sessions)
        assert owner.market.instrument("000000.SZ")["state"] == "NATIVE15_PROVENANCE_REQUIRED"
        assert owner.conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"


def test_factor_revision_holds_existing_lock_until_real_history_reproduces_it(tmp_path, scenarios):
    case = scenarios["single"]
    bars = {b["timestamp"]: dict(b, source_id=SOURCE, source_count=1, source_proof=PROOF, amount=None) for b in case["bars"]}
    with NativeOwner(tmp_path/"state.sqlite3") as owner:
        prepare(owner, case["sessions"])
        accept(owner, bars, case["sessions"])
        facts = [tuple(r) for r in owner.conn.execute("SELECT * FROM native15_candidate")]
        # A real factor revision inside the causal window restarts the warmup.
        factor_day = case["bars"][-20]["timestamp"][:10]
        accept(owner, bars, case["sessions"], factors={"000000.SZ": [dict(day=factor_day, ex_factor=1.1)]})
        assert [tuple(r) for r in owner.conn.execute("SELECT * FROM native15_candidate")] == facts
        state = json.loads(owner.conn.execute("SELECT payload FROM native15_signal_state").fetchone()[0])
        assert state["structure"]["locked"]
        assert state["reason"] == "INPUT_REVISION_LOCK_HISTORY_REQUIRED"
        assert json.loads(owner.conn.execute("SELECT payload FROM native15_candidate_risk").fetchone()[0])["reason"] == "PRICE_BASIS_CHANGED"


def test_warmup_null_after_unlocked_factor_revision_keeps_risk_readable(tmp_path, scenarios):
    case = scenarios["single"]
    bars = {b["timestamp"]: dict(b, source_id=SOURCE, source_count=1, source_proof=PROOF, amount=None) for b in case["bars"]}
    after = points(case["sessions"], max(bars), case["sessions"][-1]+" 15:00:00", step=15)[1]
    bars[after] = dict(timestamp=after, open=103, high=104, low=103, close=103.5, volume=100000,
                       source_id=SOURCE, source_count=1, source_proof=PROOF, amount=None)
    with NativeOwner(tmp_path/"state.sqlite3") as owner:
        prepare(owner, case["sessions"])
        accept(owner, bars, case["sessions"])
        state = json.loads(owner.conn.execute("SELECT payload FROM native15_signal_state").fetchone()[0])
        assert not state["structure"]["locked"]
        accept(owner, bars, case["sessions"], factors={"000000.SZ": [dict(day=case["bars"][-20]["timestamp"][:10], ex_factor=1.1)]})
        state = json.loads(owner.conn.execute("SELECT payload FROM native15_signal_state").fetchone()[0])
        assert state["valid_run"] < 56 and state["decision"]["close_distance_pct"] is None
        assert owner.conn.execute("SELECT count(*) FROM native15_candidate_risk").fetchone()[0] == 1


def test_five_minute_same_target_retry_survives_restart_then_advances(tmp_path, monkeypatch):
    from addons.lan_ma55.backend import native15_store, native15_scheduler
    clock = [1000000.0]
    monkeypatch.setattr(native15_store, "time", lambda: clock[0])
    monkeypatch.setattr(native15_scheduler, "time", lambda: clock[0])
    sessions, five = sample(64)
    bars = native(five, sessions)
    target = max(bars)
    path = tmp_path/"state.sqlite3"
    with NativeOwner(path) as owner:
        prepare(owner, sessions)
        accept(owner, {t:b for t,b in bars.items() if t < target}, sessions)
        owner.market.attempt(["000000.SZ"], target, target, "A")
        owner.market.failed(["000000.SZ"], target, target, "SOURCE_UNAVAILABLE", stage="A")
    with NativeOwner(path) as owner:
        clock[0] += 299
        assert task(owner.market, owner.market.universe(), sessions, target, target) is None
        clock[0] += 2
        selected = task(owner.market, owner.market.universe(), sessions, target, target)
        assert selected[1:] == (target, "A", ["000000.SZ"])
        accept(owner, {target:bars[target]}, sessions, factors={}, stage="A")
        status = owner.status_snapshot(target=target)
        assert status["today_native15_saved"] == status["today_analyzed_candidates"] == 1
        retry = owner.conn.execute("SELECT attempts,next_eligible FROM native15_retry").fetchone()
        assert tuple(retry) == (0, 0)
