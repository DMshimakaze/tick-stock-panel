from datetime import date, timedelta
from math import fsum
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from addons.lan_marketdata.backend.grid import (
    CAPACITY,
    COUNTS,
    aggregate,
    points,
    latest_point,
    stamp,
)
from addons.lan_marketdata.backend.store import Store
from addons.lan_marketdata.backend.lease import ProcessLease
from addons.lan_marketdata.backend.runtime import task, classify
from addons.lan_marketdata.backend import extension


def days(n):
    result = []
    d = date(2024, 1, 2)
    while len(result) < n:
        if d.weekday() < 5:
            result.append(str(d))
        d += timedelta(days=1)
    return result


def source(sessions):
    return {
        t: dict(
            open=10 + i / 1000,
            high=12 + i / 1000,
            low=9 + i / 1000,
            close=11 + i / 1000,
            volume=100 + i,
            amount=None,
        )
        for i, t in enumerate(points(sessions, sessions[0], sessions[-1] + " 15:00:00"))
    }


def seed(store, sessions, symbol="600000.SH"):
    store.save_master(
        [
            dict(
                symbol=symbol,
                name=symbol,
                source_type="stock",
                raw_tick=0.01,
                eligible=True,
            )
        ],
        dict(expected=1),
        sessions,
    )


def test_real_bucket_ohlcv_lunch_day_gaps_and_completion():
    sessions = days(2)
    raw = source(sessions)
    frames = aggregate(raw, sessions, max(raw))
    assert {tf: len(frames[tf]) for tf in COUNTS if tf in frames} == {
        "5m": 96,
        "15m": 32,
        "30m": 16,
        "60m": 8,
        "120m": 4,
        "daily": 2,
    }
    for tf in ("15m", "30m", "60m", "120m", "daily"):
        bar = frames[tf][0]
        parts = list(raw.values())[: COUNTS[tf]]
        assert bar["open"] == parts[0]["open"] and bar["close"] == parts[-1]["close"]
        assert bar["high"] == max(r["high"] for r in parts) and bar["low"] == min(
            r["low"] for r in parts
        )
        assert (
            bar["volume"] == fsum(r["volume"] for r in parts)
            and bar["source_count"] == COUNTS[tf]
        )
    partial = aggregate(raw, sessions, sessions[-1] + " 14:59:00")
    assert len(partial["daily"]) == 1 and len(partial["120m"]) == 3
    del raw[stamp(sessions[-1], 580)]
    broken = aggregate(raw, sessions, max(raw))
    assert (
        len(broken["15m"]) == 31
        and len(broken["60m"]) == 7
        and len(broken["daily"]) == 1
    )
    assert all(
        b["timestamp"][11:16] not in ("12:00", "13:00")
        for rows in broken.values()
        for b in rows
    )


def test_every_persisted_window_rolls_restarts_and_ma233_survives(tmp_path):
    sessions = days(530)
    path = tmp_path / "rolling.duckdb"
    store = Store(path)
    seed(store, sessions)
    for offset in range(0, len(sessions), 80):
        page = sessions[offset : offset + 80]
        raw = source(page)
        store.accept(
            "600000.SH",
            raw,
            sessions=sessions,
            start=min(raw),
            end=max(raw),
            now=max(raw),
            events=[],
            factor_response=True,
        )
        assert all(r["row_count"] <= r["capacity"] for r in store.inventory())
    counts = {r["timeframe"]: r["row_count"] for r in store.inventory()}
    assert counts == CAPACITY
    before = store.read("600000.SH", "60m", sessions[-1] + " 15:00:00")
    assert before["bars"][-1]["ma233"] is not None
    assert before["bars"][-1]["ma233"] == pytest.approx(
        fsum(b["close"] for b in before["bars"][-233:]) / 233
    )
    store.close()
    store = Store(path)
    assert store.read("600000.SH", "60m", sessions[-1] + " 15:00:00") == before
    assert all(r["row_count"] <= r["capacity"] for r in store.inventory())
    store.close()


def test_factor_pending_event_chain_tick_shares_and_asof(tmp_path):
    sessions = days(2)
    store = Store(tmp_path / "r.duckdb")
    seed(store, sessions)
    raw = source(sessions)
    store.accept(
        "600000.SH", raw, sessions=sessions, start=min(raw), end=max(raw), now=max(raw)
    )
    assert store.read("600000.SH", "15m", max(raw))["state"] == "FACTOR_PENDING"
    events = [dict(day=sessions[-1], ex_factor=2)]
    store.accept(
        "600000.SH",
        {},
        sessions=sessions,
        start=max(raw),
        end=max(raw),
        now=max(raw),
        events=events,
        factor_response=True,
    )
    read = store.read("600000.SH", "15m", max(raw))
    assert (
        read["bars"][0]["adjust_factor"] == 0.5
        and read["bars"][-1]["adjust_factor"] == 1
    )
    assert read["raw_tick"] == 0.01 and read["volume_unit"] == "shares"
    historical = store.read("600000.SH", "15m", sessions[0] + " 15:00:00")
    assert (
        historical["bars"][0]["adjust_factor"] == 1
    )  # later event cannot leak into historical asof
    store.close()


def test_single_writer_lease_and_rolled_back_receipt(tmp_path, monkeypatch):
    lease = ProcessLease(tmp_path / "writer.lock")
    with pytest.raises(RuntimeError):
        ProcessLease(tmp_path / "writer.lock")
    lease.close()
    sessions = days(2)
    store = Store(tmp_path / "r.duckdb")
    seed(store, sessions)
    raw = source(sessions)
    monkeypatch.setattr(
        store, "_write", lambda *a: (_ for _ in ()).throw(OSError("disk boundary"))
    )
    with pytest.raises(OSError):
        store.accept(
            "600000.SH",
            raw,
            sessions=sessions,
            start=min(raw),
            end=max(raw),
            now=max(raw),
        )
    assert not store.inventory() and not store.instrument("600000.SH").get("latest5")
    store.close()


def test_full_market_sweep_is_independent_of_ui_and_precedes_warmup():
    sessions = days(70)
    target = sessions[-1] + " 15:00:00"
    master = {
        f"{i:06}.SZ": dict(
            eligible=True,
            cold_done=True,
            warm_count=0,
            latest5=target,
            live_attempt=target,
        )
        for i in range(5101)
    }
    master["000090.SZ"]["latest5"] = sessions[-2] + " 15:00:00"
    master["000090.SZ"]["live_attempt"] = ""
    work = task(master, sessions, target, target, {"000001.SZ"})
    assert work[2] == "LIVE" and work[3] == ["000090.SZ"]
    master["000090.SZ"]["latest5"] = target
    assert task(master, sessions, target, target, {"000001.SZ"})[2] == "WARM"
    candidates = {s: s for s in ("600000.SH", "588170.SH", "920001.BJ")}
    metadata = {
        s: dict(
            type="etf" if s == "588170.SH" else "stock",
            region="CN",
            exchange=s[-2:],
            ext={"tick_size": 0.001 if s == "588170.SH" else 0.01},
        )
        for s in candidates
    }
    rows, overview = classify(candidates, metadata, {"600000.SH"}, sessions[-1])
    assert (
        overview["expected"] == 2
        and next(r for r in rows if r["symbol"] == "588170.SH")["raw_tick"] == 0.001
    )


def test_read_only_versioned_api_bounds_startup_and_errors(tmp_path, monkeypatch):
    app = FastAPI()
    extension.setup(SimpleNamespace(include_router=app.include_router))
    monkeypatch.setattr(extension, "worker", None)
    client = TestClient(app)
    assert client.get("/api/extensions/lan.marketdata/v1/universe").status_code == 503
    sessions = ["2026-11-05", "2026-11-06"]
    store = Store(tmp_path / "r.duckdb")
    seed(store, sessions)
    fake = SimpleNamespace(
        store=store,
        demand=lambda s: None,
        status=lambda: dict(api_version=1, phase="READY"),
    )
    monkeypatch.setattr(extension, "worker", fake)
    assert (
        client.get(
            "/api/extensions/lan.marketdata/v1/bars",
            params={"symbols": "600000.SH", "timeframes": "15m"},
        ).json()["api_version"]
        == 1
    )
    for params in [
        dict(symbols="600000"),
        dict(symbols="600000.SH", timeframes="1m"),
        dict(symbols=",".join(f"{i:06}.SZ" for i in range(32)), timeframes="15m,60m"),
    ]:
        assert (
            client.get(
                "/api/extensions/lan.marketdata/v1/bars", params=params
            ).status_code
            == 422
        )
    assert (
        client.post("/api/extensions/lan.marketdata/v1/bars", json={}).status_code
        == 405
    )
    store.close()


@pytest.mark.parametrize(
    "now,expected",
    [
        ("2026-11-08 12:00:00", "2026-11-06 15:00:00"),
        ("2026-11-09 09:35:59", "2026-11-06 15:00:00"),
        ("2026-11-09 09:36:00", "2026-11-09 09:35:00"),
        ("2026-11-09 12:00:00", "2026-11-09 11:30:00"),
    ],
)
def test_completion_margin_weekend_and_lunch(now, expected):
    assert latest_point(["2026-11-06", "2026-11-09"], now) == expected
