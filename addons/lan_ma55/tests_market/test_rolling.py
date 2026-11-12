"""Synthetic exchange fixtures; no commercial data or inferred production calendars."""

from datetime import date, timedelta
from math import fsum
import sqlite3

import pytest

from addons.lan_ma55.backend.market_grid import (
    aggregate,
    as_ms,
    latest_point,
    normalize_five,
    points,
    wall,
)
from addons.lan_ma55.backend.market_store import Store


def sample(count=250):
    days = []
    day = date(2026, 1, 5)
    while len(days) < (count + 15) // 16 + 1:
        if day.weekday() < 5:
            days.append(str(day))
        day += timedelta(days=1)
    stamps = points(days, days[0], days[-1] + " 15:00:00")[: count * 3]
    rows = {
        t: dict(
            open=10 + i / 10000,
            high=10.1 + i / 10000,
            low=9.9 + i / 10000,
            close=10.02 + i / 10000,
            volume=1000 + i,
            amount=(1000 + i) * (10.02 + i / 10000),
        )
        for i, t in enumerate(stamps)
    }
    return days, rows


def opened(tmp_path, sessions):
    store = Store(tmp_path / "rolling15.sqlite3")
    store.save_master(
        [
            dict(
                symbol="600000.SH",
                name="Synthetic",
                eligible=True,
                raw_tick=0.01,
                source_type="stock",
            )
        ],
        {"expected": 1},
        sessions,
    )
    return store


def put(store, rows, sessions, **kw):
    return store.accept(
        "600000.SH",
        rows,
        sessions=sessions,
        start=min(rows),
        end=max(rows),
        now=max(rows),
        events=[],
        factor_response=True,
        **kw,
    )


@pytest.mark.parametrize(
    "count,values", [(232, 0), (233, 1), (250, 18), (251, 18), (1000, 18)]
)
def test_retention_and_real_ma233(tmp_path, count, values):
    sessions, rows = sample(count)
    store = opened(tmp_path, sessions)
    try:
        put(store, rows, sessions)
        frame = store.read("600000.SH", "15m", max(rows))
        assert frame["row_count"] == min(250, count)
        assert sum(b["ma233"] is not None for b in frame["bars"]) == values
        if values:
            closes = [b["close"] for b in frame["bars"]]
            assert frame["bars"][-1]["ma233"] == fsum(closes[-233:]) / 233
        assert store.conn.execute("SELECT count(*) FROM tail5").fetchone()[0] <= 2
        assert set(
            r[0]
            for r in store.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        ) == {"meta", "instrument_state", "bar15", "tail5", "factor_event"}
        for tf, maximum in (("30m", 125), ("60m", 62)):
            bars = store.read("600000.SH", tf, max(rows))["bars"]
            assert len(bars) <= maximum
            assert all(b["ma233"] is None for b in bars)
            assert all(b["source_count"] == int(tf[:-1]) // 5 for b in bars)
    finally:
        store.close()


def test_session_labels_holiday_timezone_and_missing_five():
    sessions = ["2026-09-30", "2026-10-09"]
    assert latest_point(sessions, "2026-10-01 14:00:00") == "2026-09-30 15:00:00"
    assert latest_point(sessions, "2026-10-09T01:46:15+00:00") == "2026-10-09 09:45:00"
    assert latest_point(sessions, "2026-10-09 13:15:30") == "2026-10-09 11:30:00"
    assert latest_point(sessions, "2026-10-09 13:16:15") == "2026-10-09 13:15:00"
    assert latest_point(sessions, "2026-10-09 15:01:15") == "2026-10-09 15:00:00"
    assert str(wall("2026-10-08T16:00:00Z")) == "2026-10-09 00:00:00"
    days, rows = sample(2)
    del rows[next(iter(rows))]
    assert len(aggregate(rows, days, max(rows))["15m"]) == 1
    assert aggregate(rows, [], max(rows))["15m"] == []


def test_compact_units_auction_and_duplicate_conflict():
    at = "2026-11-11 09:35:00"
    data = dict(
        timestamp=[as_ms(at)],
        open=[10],
        high=[11],
        low=[9],
        close=[10],
        volume=[2],
        amount=[2000],
    )
    assert normalize_five(data, at, at)[0][at]["volume"] == 200
    data["timestamp"] = [as_ms("2026-11-11 09:30:00")]
    with pytest.raises(ValueError, match="NON_COMPLETED"):
        normalize_five(data, "2026-11-11 09:30:00", at)
    data.update(
        timestamp=[as_ms(at)] * 2,
        open=[10, 10],
        high=[11, 11],
        low=[9, 9],
        close=[10, 11],
        volume=[2, 2],
        amount=[2000, 2200],
    )
    with pytest.raises(ValueError, match="CONFLICTING"):
        normalize_five(data, at, at)


def test_gap_correction_factor_reprojection_and_short_tail(tmp_path):
    sessions, rows = sample(250)
    store = opened(tmp_path, sessions)
    try:
        missing = dict(rows)
        removed = list(rows)[-2]
        del missing[removed]
        put(store, missing, sessions)
        assert store.read("600000.SH", "15m", max(rows))["state"] == "GAP"
        assert store.conn.execute("SELECT count(*) FROM tail5").fetchone()[0] == 2
        put(store, {t: rows[t] for t in list(rows)[-3:]}, sessions)
        assert store.read("600000.SH", "15m", max(rows))["row_count"] == 250
        changed = {t: dict(rows[t], high=12) for t in list(rows)[-3:]}
        put(store, changed, sessions)
        out = store.read("600000.SH", "15m", max(rows))
        assert out["correction"] and out["bars"][-1]["high"] == 12
        event = [{"day": sessions[-1], "ex_factor": 2}]
        store.accept(
            "600000.SH",
            changed,
            sessions=sessions,
            start=min(changed),
            end=max(changed),
            now=max(rows),
            events=event,
            factor_response=True,
        )
        out = store.read("600000.SH", "15m", sessions[-1] + " 15:00:00")
        assert out["bars"][0]["adjust_factor"] == 0.5
        store.accept(
            "600000.SH",
            changed,
            sessions=sessions,
            start=min(changed),
            end=max(changed),
            now=max(rows),
            events=None,
            factor_response=True,
        )
        assert all(
            b["ma233"] is None
            for b in store.read("600000.SH", "15m", max(rows))["bars"]
        )
    finally:
        store.close()


def test_commit_failure_keeps_bars_and_receipt(tmp_path, monkeypatch):
    sessions, rows = sample(60)
    store = opened(tmp_path, sessions)
    try:
        put(store, rows, sessions)
        before = store.instrument("600000.SH")

        def fail(symbol, bars):
            raise sqlite3.OperationalError("synthetic disk failure")

        monkeypatch.setattr(store, "_write", fail)
        with pytest.raises(sqlite3.OperationalError):
            put(store, rows, sessions)
        assert store.instrument("600000.SH") == before
        assert store.read("600000.SH", "15m", max(rows))["row_count"] == 60
    finally:
        store.close()


def test_bad_vendor_bar_is_a_gap_not_a_fabricated_complete_bucket(tmp_path):
    sessions, rows = sample(250)
    stamps = list(rows)
    compact = dict(
        timestamp=[as_ms(t) for t in stamps],
        **{
            k: [row[k] / 100 if k == "volume" else row[k] for row in rows.values()]
            for k in ("open", "high", "low", "close", "volume", "amount")
        },
    )
    compact["high"][250] = 1
    valid, invalid = normalize_five(compact, min(rows), max(rows))
    assert invalid == [stamps[250]] and len(valid) == 749
    store = opened(tmp_path, sessions)
    try:
        put(store, valid, sessions)
        before = store.instrument("600000.SH")
        assert store.read("600000.SH", "15m", max(rows))["row_count"] == 249
        assert store.read("600000.SH", "15m", max(rows))["state"] == "GAP"
        group = {t: rows[t] for t in stamps[249:252]}
        put(store, group, sessions)
        assert (
            store.instrument("600000.SH")["latest_source5"] == before["latest_source5"]
        )
        assert store.read("600000.SH", "15m", max(rows))["row_count"] == 250
    finally:
        store.close()
