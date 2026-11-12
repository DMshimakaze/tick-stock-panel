"""Independent arithmetic oracle over raw synthetic history, never live evidence."""

from math import fsum

import pytest

from addons.lan_ma55.backend.market_grid import CAPACITY, MAS, aggregate, derived
from addons.lan_ma55.backend.market_store import Store
from .test_rolling import sample, opened, put


def assert_oracle(store, sessions, five, end):
    history = aggregate({t: r for t, r in five.items() if t <= end}, sessions, end)["15m"]
    for tf, cap in CAPACITY.items():
        # Aggregation is separately contract-tested; this SMA oracle uses no project()/maN.
        full = derived(history, tf, sessions, end)
        closes = [r["close"] for r in full]
        frame = store.read("600000.SH", tf, end)
        assert len(frame["bars"]) == cap
        assert frame["ma_history"]["state"] == "READY"
        for i, actual in enumerate(frame["bars"], len(full) - cap):
            for n in MAS:
                assert actual[f"ma{n}"] == pytest.approx(fsum(closes[i-n+1:i+1]) / n, abs=1e-10, rel=1e-12)


def test_all_periods_survive_1280_evictions_restart_and_old_backfill(tmp_path):
    sessions, five = sample(2560)
    times = list(five)
    store = opened(tmp_path, sessions)
    # The old 250-bar database is expanded in place, without losing its latest bars.
    put(store, {t: five[t] for t in times[3090:3840]}, sessions)
    store.backfill("600000.SH", {t: five[t] for t in times[:3090]}, sessions=sessions,
                   start=times[0], end=times[3089], now=times[3839])
    assert len(store.raw_rows("600000.SH", limit=1280)) == 1280
    assert_oracle(store, sessions, five, times[3839])
    for step in range(1, 1281):
        group = times[3840 + (step-1)*3:3840 + step*3]
        put(store, {t: five[t] for t in group}, sessions)
        if step in (1, 2, 15, 16, 64, 249, 250, 512, 1280):
            assert_oracle(store, sessions, five, group[-1])
        else:
            for tf in CAPACITY:
                frame = store.read("600000.SH", tf, group[-1])
                assert all(b[f"ma{n}"] is not None for b in frame["bars"] for n in MAS)
        if step in (16, 250):
            path = store.path
            store.close()
            store = Store(path)
            assert_oracle(store, sessions, five, group[-1])
    assert store.conn.execute("SELECT count(*) FROM bar15").fetchone()[0] == 1280
    store.close()


def test_real_missing_slot_and_unconfirmed_factors_never_claim_complete(tmp_path):
    sessions, five = sample(1280)
    store = opened(tmp_path, sessions)
    missing = dict(five)
    del missing[list(five)[-300]]
    put(store, missing, sessions)
    for tf in CAPACITY:
        frame = store.read("600000.SH", tf, max(five))
        assert frame["ma_history"]["state"] == "GAP"
        assert any(b["ma233"] is None for b in frame["bars"])
    group = list(five)[-300:-297]
    store.backfill("600000.SH", {t: five[t] for t in group}, sessions=sessions,
                   start=group[0], end=group[-1], now=max(five))
    assert_oracle(store, sessions, five, max(five))
    store.accept("600000.SH", {t: five[t] for t in list(five)[-3:]}, sessions=sessions,
                 start=list(five)[-3], end=max(five), now=max(five), events=None, factor_response=True)
    assert store.read("600000.SH", "60m", max(five))["ma_history"]["state"] == "FACTOR_PENDING"
    store.close()
