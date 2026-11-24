"""Synthetic generation tests; no commercial data or provider requests."""
import pytest

from addons.lan_ma55.backend.ths_taxonomy import TaxonomyStore, decode_reverse

A, B = "industry:881001.TI", "cn_concept:886001.TI"
C, D, E = "industry:884001.TI", "industry:881002.TI", "cn_concept:886002.TI"
X, Y, Z = "600001.SH", "000001.SZ", "830001.BJ"
CAT = {A: "半导体", B: "光刻胶", C: "额外行业", D: "银行", E: "光刻机"}


def response(*groups):
    return {"code": 0, "data": {"item": list(groups)}}


def group(symbol, keys):
    return dict(thscode=symbol, index_list=[
        dict(thscode=key.split(":")[1], type=key.split(":")[0], name=CAT[key])
        for key in keys])


def begin(store, run="r1", at=101):
    store.begin(run, CAT, [X, Y], source_ms={"industry": 100, "cn_concept": 100},
                observed_ms=at)


def direct(store, run, board, members, observed=101):
    code = board.split(":")[1]
    observation = receipt("members", code, 1, 100, observed)
    store.accept_forward(run, board, {"code": 0, "data": {"timestamp": 100,
                         "item": [{"thscode": symbol} for symbol in members]}}, receipt=observation)


def receipt(step, target, count, source, observed):
    return dict(status=200, code=0, capability=f"ths.{step}", target=target, symbol_count=count,
                source_ms=source, observed_ms=observed)


def reverse(store, run, symbol, keys, observed):
    store.accept_reverse(run, [symbol], response(group(symbol, keys)),
                         receipt=receipt("reverse", symbol, 1, None, observed))


def test_reverse_explicit_empty_and_missing_group():
    _, rows = decode_reverse([X, Y], CAT, response(group(X, [A, B]), group(Y, [])))
    assert rows == {X: sorted([A, B]), Y: []}
    with pytest.raises(ValueError, match="Incomplete"):
        decode_reverse([X, Y], CAT, response(group(X, [A])))
    with pytest.raises(ValueError, match="duplicate"):
        decode_reverse([X, Y], CAT, response(group(X, []), group(X, [])))
    with pytest.raises(ValueError, match="Unexpected"):
        decode_reverse([X], CAT, response(group(Z, [])))


def test_reverse_catalog_drift_rejected():
    with pytest.raises(ValueError, match="differs"):
        decode_reverse([X], CAT, response(dict(thscode=X, index_list=[
            dict(thscode="881001.TI", name="其他名称", type="industry")])))


def test_resume_and_atomic_visibility(tmp_path):
    store = TaxonomyStore(tmp_path / "state.sqlite3")
    begin(store)
    reverse(store, "r1", X, [A, B, C, E], 101)
    store = TaxonomyStore(store.path)
    begin(store)
    assert store.progress("r1")["pending_stocks"] == [Y]
    assert store.active() is None
    with pytest.raises(ValueError, match="incomplete"):
        store.reverse_candidate("r1")
    reverse(store, "r1", Y, [A, D], 102)
    candidate = store.reverse_candidate("r1")
    assert candidate["boards"] == {A: sorted([X, Y]), B: [X], C: [X], D: [Y], E: [X]}
    with pytest.raises(ValueError, match="Missing forward"):
        store.reconcile("r1")
    with pytest.raises(ValueError, match="coverage"):
        store.publish("r1")
    for board in (A, B, D, E):
        direct(store, "r1", board, candidate["boards"][board])
    store.reconcile("r1")
    store.publish("r1")
    assert store.active()["boards"][A]["symbols"] == sorted([X, Y])
    assert store.active()["boards"][C]["source_ms"] is None
    assert store.active()["boards"][C]["observed_ms"] == 102
    begin(store, "r2", 102)
    store.direct_only("r2", "incomplete group")
    for board in (A, C, D, E):
        direct(store, "r2", board, [Y], 103)
    assert store.active()["run"] == "r1"
    direct(store, "r2", B, [], 103)
    store.publish("r2")
    assert store.active()["boards"][B]["proof"] == "DIRECT_EMPTY"
    assert store.active()["hierarchy_status"] == "UNVERIFIED"


def test_invalid_members_and_immutable_generation(tmp_path):
    store = TaxonomyStore(tmp_path / "state.sqlite3")
    begin(store)
    with pytest.raises(ValueError, match="Unknown forward stocks"):
        direct(store, "r1", A, [Z])
    with pytest.raises(ValueError, match="successful request"):
        store.accept_forward("r1", A, {}, receipt={"target": A.split(":")[1]})
    for board in CAT:
        direct(store, "r1", board, [X])
    with pytest.raises(ValueError, match="reconciliation"):
        store.publish("r1")
    store.direct_only("r1", "incomplete group")
    store.publish("r1")
    with pytest.raises(ValueError, match="immutable"):
        direct(store, "r1", A, [Y])


def test_reverse_mismatch_restricts_projection_to_matching_category(tmp_path):
    store = TaxonomyStore(tmp_path / "state.sqlite3")
    begin(store)
    reverse(store, "r1", X, [A, B, C, E], 101)
    reverse(store, "r1", Y, [D], 102)
    for board, members in ((A, [X, Y]), (B, [X]), (D, [Y]), (E, [X])):
        direct(store, "r1", board, members)
    store.reconcile("r1")
    assert store.progress("r1")["pending_boards"] == [C]
    direct(store, "r1", C, [X])
    store.publish("r1")
    assert store.stock_boards("r1", Y) == [A, D]
