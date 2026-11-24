"""Synthetic generation tests; no commercial data or provider requests."""
import pytest

from addons.lan_ma55.backend.ths_taxonomy import TaxonomyStore, decode_reverse

A, B = "industry:881001.TI", "cn_concept:886001.TI"
X, Y, Z = "600001.SH", "000001.SZ", "830001.BJ"
CAT = {A: "行业甲", B: "概念甲"}


def response(*groups):
    return {"code": 0, "data": {"item": list(groups)}}


def group(symbol, keys):
    return dict(thscode=symbol, index_list=[
        dict(thscode=key.split(":")[1], type=key.split(":")[0], name=CAT[key])
        for key in keys])


def begin(store, run="r1", at=101):
    store.begin(run, CAT, [X, Y], source_ms={"industry": 100, "cn_concept": 100},
                observed_ms=at)


def direct(store, run, board, members):
    store.accept_board(run, board, members, source_ms=100, observed_ms=101,
                       proof="DIRECT" if members else "DIRECT_EMPTY")


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
    store.accept_reverse("r1", [X], response(group(X, [A, B])), observed_ms=101)
    store = TaxonomyStore(store.path)
    begin(store)
    assert store.progress("r1")["pending_stocks"] == [Y]
    assert store.active() is None
    with pytest.raises(ValueError, match="incomplete"):
        store.reverse_candidate("r1")
    store.accept_reverse("r1", [Y], response(group(Y, [A])), observed_ms=102)
    candidate = store.reverse_candidate("r1")
    assert candidate["boards"] == {A: sorted([X, Y]), B: [X]}
    store.accept_board("r1", A, [X, Y], source_ms=None, observed_ms=102,
                       proof="BULK_REVERSE_CHECKED")
    with pytest.raises(ValueError, match="coverage"):
        store.publish("r1")
    direct(store, "r1", B, [X])
    store.publish("r1")
    assert store.active()["boards"][A]["symbols"] == sorted([X, Y])
    assert store.active()["boards"][A]["source_ms"] is None
    assert store.active()["boards"][A]["observed_ms"] == 102
    begin(store, "r2", 102)
    direct(store, "r2", A, [Y])
    assert store.active()["run"] == "r1"
    direct(store, "r2", B, [])
    store.publish("r2")
    assert store.active()["boards"][B]["proof"] == "DIRECT_EMPTY"
    assert store.active()["hierarchy_status"] == "UNVERIFIED"


def test_invalid_members_and_immutable_generation(tmp_path):
    store = TaxonomyStore(tmp_path / "state.sqlite3")
    begin(store)
    with pytest.raises(ValueError, match="Unknown board or stock"):
        direct(store, "r1", A, [Z])
    with pytest.raises(ValueError, match="Empty membership"):
        store.accept_board("r1", A, [], source_ms=100, observed_ms=101, proof="DIRECT")
    direct(store, "r1", A, [X])
    direct(store, "r1", B, [Y])
    store.publish("r1")
    with pytest.raises(ValueError, match="immutable"):
        direct(store, "r1", A, [Y])
