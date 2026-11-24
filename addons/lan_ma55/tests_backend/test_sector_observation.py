"""Targeted THS refresh and price-map contracts (synthetic, no paid I/O)."""
import pytest

from addons.lan_ma55.backend.focus_pool import FocusPool, ScopeConflict
from addons.lan_ma55.backend.ths_focus import CATALOG, MEMBERS, load_catalog, refresh_selection
from addons.lan_ma55.backend.ma55_levels import project_levels
from addons.lan_ma55.backend.multiframe_core.multiframe import VERSION

A, B = "industry:881001.TI", "cn_concept:886001.TI"
X, Y = "600001.SH", "000001.SZ"
AT = "2026-11-24 15:00:00"


def response(items, source=100):
    return {"code": 0, "data": {"timestamp": source, "item": items}}


class FakeTHS:
    def __init__(self):
        self.calls = []
        self.members = {A.split(":")[1]: [X], B.split(":")[1]: [X, Y]}
        self.source = 100

    def __call__(self, path, params):
        self.calls.append((path, params))
        if path == CATALOG:
            key = A if params["tag"] == "industry" else B
            return response([{"thscode": key.split(":")[1], "name": key}], self.source)
        assert path == MEMBERS
        return response([{"thscode": s} for s in self.members[params["thscode"]]], self.source)


def test_focus_transport_delta_and_pause(tmp_path):
    pool, fetch = FocusPool(tmp_path / "state.sqlite3"), FakeTHS()
    catalog = load_catalog(fetch)
    assert len(fetch.calls) == 2

    def apply(keys, **kwargs):
        return refresh_selection(pool, keys, catalog, fetch,
                                 expected_revision=pool.read()["revision"],
                                 observed_ms=200, **kwargs)
    assert apply([A])["added"] == [X]
    calls = len(fetch.calls)
    assert not apply([A])["membership_changed"]
    assert len(fetch.calls) == calls
    assert apply([A, B])["added"] == [Y]
    assert len(fetch.calls) == calls + 1
    assert not apply([B])["removed"]
    assert len(fetch.calls) == calls + 1
    assert set(apply([])["removed"]) == {X, Y}
    assert len(fetch.calls) == calls + 1


def test_failed_new_and_retained_refresh(tmp_path):
    pool, fetch = FocusPool(tmp_path / "state.sqlite3"), FakeTHS()
    catalog = load_catalog(fetch)

    def apply(keys, refresh=False):
        return refresh_selection(pool, keys, catalog, fetch,
                                 expected_revision=pool.read()["revision"],
                                 observed_ms=200, refresh_existing=refresh)
    apply([A])
    before = pool.read()
    fetch.members[A.split(":")[1]] = []
    fetch.members[B.split(":")[1]] = []
    with pytest.raises(ValueError, match="THS_MEMBER_UNVERIFIED"):
        apply([A, B])
    assert pool.read() == before
    result = apply([A], True)
    assert result["sector_errors"] == {A: "THS_MEMBER_UNVERIFIED"}
    assert pool.read() == before
    with pytest.raises(ScopeConflict):
        refresh_selection(pool, [], catalog, fetch, expected_revision=0, observed_ms=200)


def frame(period, level, *, status="READY", basis="factor-v1", at=AT):
    return dict(status=status, basis=basis, expected_end=AT,
                result=dict(symbol=X, timeframe=period, rule_version=VERSION,
                            latest=dict(ma55=level, timestamp=at, pressure_test=False)))


def test_pressure_only_sorted_and_missing_explicit():
    frames = {"15m": frame("15m", 10.5), "30m": frame("30m", 9.8),
              "60m": frame("60m", 10.2), "120m": frame("120m", None),
              "daily": frame("daily", 11, basis="old"),
              "weekly": frame("weekly", 12, at="2026-11-20 15:00:00")}
    result = project_levels(X, frames, price=10, price_at=AT, asof=AT, basis="factor-v1")
    assert [r["timeframe"] for r in result["pressures"]] == ["60m", "15m"]
    assert result["pressures"][0]["distance_pct"] == pytest.approx(2)
    assert [r["status"] for r in result["levels"]] == [
        "READY", "READY", "READY", "WARMUP",
        "IDENTITY_OR_BASIS_MISMATCH", "STALE_OR_INVALID_END",
    ]
    assert result["levels"][1]["side"] == "SUPPORT_REFERENCE"


def test_invalid_reference_and_future_frame():
    with pytest.raises(ValueError):
        project_levels(X, {}, price=0, price_at=AT, asof=AT, basis="factor-v1")
    result = project_levels(X, {"15m": frame("15m", 11, at="2026-11-25 15:00:00")},
                            price=10, price_at=AT, asof=AT, basis="factor-v1")
    assert not result["pressures"]
    assert result["levels"][0]["status"] == "STALE_OR_INVALID_END"
    assert result["levels"][1]["status"] == "UNAVAILABLE"
