"""Cache arithmetic and transaction tests; synthetic data is never source evidence."""

from concurrent.futures import ThreadPoolExecutor
from math import fsum
from unittest.mock import patch

import pytest

from addons.lan_ma55.backend.market_grid import CAPACITY, MAS, aggregate, derived, project
from addons.lan_ma55.backend.market_store import Store
from addons.lan_ma55.backend.pipeline import process_symbol
from .test_rolling import opened, put, sample


def assert_truth(store, days, five, end):
    raw = aggregate({t: r for t, r in five.items() if t <= end}, days, end)["15m"][-1280:]
    for tf, capacity in CAPACITY.items():
        truth = project(derived(raw, tf, days, end), store.events("600000.SH"), end, sessions=days, timeframe=tf)
        with patch("addons.lan_ma55.backend.market_store.project", side_effect=AssertionError("Current chart must hit persistent cache")):
            actual = store.read("600000.SH", tf, end)
        assert actual["cache"]["hit"]
        assert actual["ma_history"]["available"] == len(truth)
        for a, b in zip(actual["bars"], truth[-capacity:]):
            assert a["timestamp"] == b["timestamp"]
            for n in MAS:
                assert a[f"ma{n}"] == pytest.approx(b[f"ma{n}"], abs=1e-11, rel=1e-12)


def test_append_eviction_reopen_and_concurrent_reads(tmp_path):
    days, five = sample(1350)
    times = list(five)
    store = opened(tmp_path, days)
    put(store, {t: five[t] for t in times[:3840]}, days)
    assert not store.read("600000.SH", "15m", times[3839])["cache"]["hit"]
    store.warm_cache(["600000.SH"])
    for start in range(3840, len(times), 3):
        put(store, {t: five[t] for t in times[start:start+3]}, days)
    end = times[-1]
    assert_truth(store, days, five, end)
    path = store.path
    store.close()
    store = Store(path)
    assert_truth(store, days, five, end)
    with ThreadPoolExecutor(max_workers=4) as pool:
        frames = list(pool.map(lambda _: store.read("600000.SH", "60m", end), range(12)))
    assert all(f["cache"]["hit"] for f in frames)
    assert store.conn.execute("SELECT count(*) FROM chart_cache").fetchone()[0] == 3
    store.close()


def test_gap_revision_factors_and_metadata_rebuild(tmp_path):
    days, five = sample(1280)
    times = list(five)
    missing = dict(five)
    del missing[times[-120]]
    store = opened(tmp_path, days)
    put(store, missing, days)
    assert store.read("600000.SH", "15m", times[-1])["ma_history"]["state"] == "GAP"
    part = {t: five[t] for t in times[-120:-117]}
    store.backfill("600000.SH", part, sessions=days, start=min(part), end=max(part), now=times[-1])
    store.warm_cache(["600000.SH"])
    assert_truth(store, days, five, times[-1])
    correction = {t: dict(five[t]) for t in times[-300:-297]}
    for t, row in correction.items():
        row["close"] += .01
        five[t] = row
    put(store, correction, days)
    assert not store.read("600000.SH", "15m", times[-1])["cache"]["hit"]
    store.warm_cache(["600000.SH"])
    assert_truth(store, days, five, times[-1])
    events = [dict(day=times[-48][:10], ex_factor=1.5)]
    recent = {t: five[t] for t in times[-3:]}
    store.accept("600000.SH", recent, sessions=days, start=min(recent), end=max(recent), now=max(recent), events=events, factor_response=True)
    store.warm_cache(["600000.SH"])
    assert_truth(store, days, five, times[-1])
    store.put_metadata([dict(symbol="600000.SH", raw_tick=.001)])
    assert not store.read("600000.SH", "15m", times[-1])["cache"]["hit"]
    assert store.warm_cache(["600000.SH"]) == ["600000.SH"]
    assert store.read("600000.SH", "15m", times[-1])["cache"]["hit"]
    # Historical full windows reconstruct from saved raw facts, without mutation.
    before = store.conn.total_changes
    historic = store.read("600000.SH", "60m", times[-193])
    assert not historic["cache"]["hit"]
    assert store.conn.total_changes == before
    store.close()


def test_strategy_prices_and_frozen_outputs_ignore_display_mas(tmp_path):
    days, five = sample(1280)
    store = opened(tmp_path, days)
    put(store, five, days)
    end = max(five)
    raw = store.raw_rows("600000.SH")
    old = project(raw, [], end, sessions=days, timeframe="15m")
    new = store.read("600000.SH", "15m", end, strategy=True)["bars"]
    assert all("ma233" not in b for b in new)
    args = dict(sessions=days, raw_tick=.01, source_asof=end, first_seen_at=end)
    assert process_symbol("600000.SH", old, **args) == process_symbol("600000.SH", new, **args)
    assert new[-1]["close"] == old[-1]["close"]
    assert fsum(b["close"] for b in new[-55:]) / 55 == old[-1]["ma55"]
    store.close()


def test_first_ma233_is_incremental_and_cold_cache_does_not_block_raw(tmp_path):
    days, five = sample(233)
    times = list(five)
    store = opened(tmp_path, days)
    put(store, {t: five[t] for t in times[:-3]}, days)
    assert store.conn.execute("SELECT count(*) FROM chart_cache").fetchone()[0] == 0
    store.warm_cache(["600000.SH"])
    assert store.read("600000.SH", "15m", times[-4])["bars"][-1]["ma233"] is None
    put(store, {t: five[t] for t in times[-3:]}, days)
    result = store.read("600000.SH", "15m", times[-1])
    assert result["cache"]["hit"]
    assert sum(b["ma233"] is not None for b in result["bars"]) == 1
    assert result["bars"][-1]["ma233"] == pytest.approx(fsum(b["close"] for b in result["bars"])/233, abs=1e-11)
    store.close()
