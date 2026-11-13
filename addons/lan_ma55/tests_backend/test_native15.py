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
        with owner.conn:
            for symbol in store.universe():
                store.factor_response(symbol, [], max(bars), sessions)
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
        prepare(owner, sessions, 3)
        accept(owner, bars, sessions, symbols=["000000.SZ", "000001.SZ"], factors={"000000.SZ": [], "000001.SZ": None})
        owner.accept_batch(['000002.SZ'],{}, {'000002.SZ':dict(code='SOURCE_UNAVAILABLE',timestamps=None)},
                           sessions=sessions,start=min(bars),end=max(bars),now=max(bars),factors={},stage='B',live_after=max(bars))
        status = owner.status_snapshot(target=max(bars))
        assert status["today_native15_saved"] == 2 and status["today_analyzed_candidates"] == 1
        assert status["factor_pending"] == 2
        assert status['exclusive']==dict(analyzed=1,factor_missing=1,warmup=0,invalid_input=0,missing_target=1)
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


def test_same_day_unknown_factor_recovers_after_restart_without_refetching_bars(tmp_path, scenarios, monkeypatch):
    from addons.lan_ma55.backend import native15_store, native15_scheduler
    clock = [1000000.0]
    monkeypatch.setattr(native15_store, "time", lambda: clock[0])
    monkeypatch.setattr(native15_scheduler, "time", lambda: clock[0])
    case = scenarios['single']
    bars = {b['timestamp']:dict(b,source_id=SOURCE,source_count=1,source_proof=PROOF,amount=None) for b in case['bars']}
    target, symbol, path = max(bars), '000000.SZ', tmp_path/'state.sqlite3'
    with NativeOwner(path) as owner:
        prepare(owner, case['sessions'])
        accept(owner,bars,case['sessions'],factors={symbol:None})
        info = owner.market.instrument(symbol)
        assert info['factor_coverage']=='MISSING' and info['factors_day'] is None
        assert owner.conn.execute('SELECT count(*) FROM native15_candidate').fetchone()[0]==0
        assert owner.conn.execute('SELECT next_eligible FROM native15_factor_retry').fetchone()[0]==clock[0]+300
        market_before = [tuple(r) for r in owner.conn.execute('SELECT * FROM native15_bar')]
        legacy_before = {t:row_digests(owner.conn,t) for t in ('signal_event','event_risk')}
    clock[0] += 301
    with NativeOwner(path) as owner:
        selected = task(owner.market,owner.market.universe(),case['sessions'],target,target)
        assert selected[2:] == ('F',[symbol])
        owner.market.factor_attempt([symbol],target)
        owner.accept_factors([symbol],{symbol:[]},sessions=case['sessions'],target=target,now=target,live_after=target)
        info = owner.market.instrument(symbol)
        # factors_day means a successful explicit response, including [].
        assert info['factor_coverage']=='EXPLICIT' and info['factors_day']==target[:10]
        assert owner.status_snapshot(target=target)['today_analyzed_candidates']==1
        events = [tuple(r) for r in owner.conn.execute('SELECT * FROM native15_candidate')]
        assert events and all(r['mode']=='REPLAY' for r in owner.conn.execute('SELECT mode FROM native15_candidate'))
        owner.accept_factors([symbol],{symbol:[]},sessions=case['sessions'],target=target,now=target,live_after=target)
        assert [tuple(r) for r in owner.conn.execute('SELECT * FROM native15_candidate')]==events
        assert [tuple(r) for r in owner.conn.execute('SELECT * FROM native15_bar')]==market_before
        assert {t:row_digests(owner.conn,t) for t in legacy_before}==legacy_before


def test_factor_cooldown_does_not_block_new_market_target_and_backoff_is_persistent(tmp_path, monkeypatch):
    from addons.lan_ma55.backend import native15_store, native15_scheduler
    clock = [1000000.0]
    monkeypatch.setattr(native15_store,'time',lambda:clock[0])
    monkeypatch.setattr(native15_scheduler,'time',lambda:clock[0])
    sessions,five = sample(64)
    bars = native(five,sessions)
    target = max(bars)
    next_target = points(sessions,target,sessions[-1]+' 15:00:00',step=15)[1]
    path,symbol = tmp_path/'state.sqlite3','000000.SZ'
    with NativeOwner(path) as owner:
        prepare(owner,sessions)
        accept(owner,bars,sessions,factors={symbol:None})
        selected = task(owner.market,owner.market.universe(),sessions,next_target,next_target)
        assert selected[2:] == ('A',[symbol])
        accept(owner,{next_target:dict(bars[target],timestamp=next_target)},sessions,factors={},stage='A')
        assert owner.market.instrument(symbol)['latest_completed15']==next_target
        assert owner.status_snapshot(target=next_target)['today_analyzed_candidates']==0
        for expected in (900,1800,3600,7200,21600,21600):
            owner.market.factor_failed([symbol],next_target,'MISSING_RESPONSE')
            assert owner.conn.execute('SELECT next_eligible FROM native15_factor_retry').fetchone()[0]==clock[0]+expected
        owner.market.factor_failed([symbol],next_target,'RATE_LIMITED',delay=25000)
    with NativeOwner(path) as owner:
        retry = owner.conn.execute('SELECT * FROM native15_factor_retry').fetchone()
        assert retry['error_type']=='RATE_LIMITED' and retry['next_eligible']==clock[0]+25000
        assert owner.market.instrument(symbol)['factors_day'] is None


def test_factor_only_revision_uses_existing_lock_replay_rules(tmp_path, scenarios):
    case = scenarios['single']
    bars = {b['timestamp']:dict(b,source_id=SOURCE,source_count=1,source_proof=PROOF,amount=None) for b in case['bars']}
    symbol,target='000000.SZ',max(bars)
    with NativeOwner(tmp_path/'state.sqlite3') as owner:
        prepare(owner,case['sessions'])
        accept(owner,bars,case['sessions'])
        events = [tuple(r) for r in owner.conn.execute('SELECT * FROM native15_candidate')]
        revision = owner.market.instrument(symbol)['basis_revision']
        owner.accept_factors([symbol],{symbol:[dict(day=case['bars'][-20]['timestamp'][:10],ex_factor=1.1)]},
                             sessions=case['sessions'],target=target,now=target,live_after=target)
        assert owner.market.instrument(symbol)['basis_revision']==revision+1
        assert owner.conn.execute('SELECT reason FROM native15_signal_state').fetchone()[0]=='INPUT_REVISION_LOCK_HISTORY_REQUIRED'
        assert [tuple(r) for r in owner.conn.execute('SELECT * FROM native15_candidate')]==events


def test_55_bar_warmup_and_real_gap_require_repair_before_analysis(tmp_path, monkeypatch):
    from addons.lan_ma55.backend import native15_store,native15_scheduler
    clock=[1000000.0]
    monkeypatch.setattr(native15_store,'time',lambda:clock[0])
    monkeypatch.setattr(native15_scheduler,'time',lambda:clock[0])
    sessions,five=sample(64)
    bars=native(five,sessions)
    last56=dict(list(bars.items())[-56:])
    missing=list(last56)[-20]
    partial={t:b for t,b in last56.items() if t!=missing}
    with NativeOwner(tmp_path/'state.sqlite3') as owner:
        prepare(owner,sessions)
        accept(owner,partial,sessions)
        assert owner.status_snapshot(target=max(bars))['today_analyzed_candidates']==0
        assert owner.market.instrument('000000.SZ')['rebuild_from']==missing
        clock[0]+=301
        assert task(owner.market,owner.market.universe(),sessions,max(bars),max(bars))[2]=='B'
        accept(owner,last56,sessions,factors={})
        assert owner.status_snapshot(target=max(bars))['today_analyzed_candidates']==1


def test_new_trading_day_waits_for_factor_verification_then_uses_saved_target(tmp_path):
    sessions,five=sample(80)
    bars=native(five,sessions)
    initial=dict(list(bars.items())[:64])
    after=list(bars)[64]
    symbol='000000.SZ'
    assert after[:10]>max(initial)[:10]
    with NativeOwner(tmp_path/'state.sqlite3') as owner:
        prepare(owner,sessions)
        accept(owner,initial,sessions)
        previous=owner.market.instrument(symbol)
        accept(owner,{after:bars[after]},sessions,factors={},stage='A')
        assert owner.status_snapshot(target=after)['today_analyzed_candidates']==0
        owner.accept_factors([symbol],{symbol:[]},sessions=sessions,target=after,now=after,live_after=after)
        assert owner.status_snapshot(target=after)['today_analyzed_candidates']==1
        assert owner.market.instrument(symbol)['basis_revision']==previous['basis_revision']
        assert owner.market.instrument(symbol)['factors_day']==after[:10]


@pytest.mark.parametrize('error',['FACTOR_PENDING','SOURCE_LAG'])
def test_upgrade_clears_old_false_success_day_and_market_cooldown_once(tmp_path,error):
    sessions,five=sample(64)
    bars=native(five,sessions)
    path,symbol=tmp_path/'state.sqlite3','000000.SZ'
    with NativeOwner(path) as owner:
        prepare(owner,sessions)
        accept(owner,bars,sessions,factors={symbol:None})
        factor_due=owner.conn.execute('SELECT next_eligible FROM native15_factor_retry').fetchone()[0]
        with owner.conn:
            owner.conn.execute("DELETE FROM meta WHERE key='native15.factor_retry_separated'")
            owner.conn.execute("UPDATE native15_instrument_progress SET factors_day=?,state='FACTOR_PENDING',next_retry=?",(max(bars)[:10],time()+86400))
            owner.conn.execute("UPDATE native15_retry SET error_type=?,next_eligible=?,retry_after=86400",(error,time()+86400))
    with NativeOwner(path) as owner:
        assert owner.market.instrument(symbol)['factors_day'] is None
        assert owner.conn.execute('SELECT next_eligible FROM native15_retry').fetchone()[0]==0
        assert owner.conn.execute('SELECT next_eligible FROM native15_factor_retry').fetchone()[0]==factor_due
        assert owner.market.instrument(symbol)['listing_status']=='NOT_PROVIDED'
