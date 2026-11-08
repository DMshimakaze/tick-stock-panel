"""OOM regression: real DuckDB transactions and the public service lifecycle."""

from contextlib import closing
from datetime import datetime
from time import perf_counter
from types import SimpleNamespace

import duckdb
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from addons.lan_marketdata.backend import extension
from addons.lan_marketdata.backend import runtime
from addons.lan_marketdata.backend.lease import ProcessLease
from addons.lan_marketdata.backend.store import Store
from addons.lan_marketdata.backend.grid import CAPACITY
from .test_rolling import days, source, seed


def test_default_and_explicit_memory_limits_are_effective(tmp_path):
    with closing(Store(tmp_path / "default.duckdb")) as store:
        assert store.connection_status()["duckdb_memory_limit"] == "7.4 GiB"
        assert store.connection_status()["duckdb_threads"] == 2
    with closing(Store(tmp_path / "explicit.duckdb", memory_limit="16GB")) as store:
        assert store.connection_status()["duckdb_memory_limit"] == "14.9 GiB"
    for invalid in (None, 0, "unlimited", "1TB", "0GB", "33GB", "8GiB"):
        with pytest.raises(ValueError, match="duckdb_memory_limit"):
            Store(tmp_path / "invalid.duckdb", memory_limit=invalid)


def test_failed_worker_returns_error_instead_of_starting(monkeypatch):
    app = FastAPI()
    extension.setup(SimpleNamespace(include_router=app.include_router))
    monkeypatch.setattr(
        extension,
        "worker",
        SimpleNamespace(
            store=None,
            status=lambda: dict(
                phase="ERROR", error_code="DUCKDB_OOM", writer_alive=False
            ),
        ),
    )
    response = TestClient(app).get(
        "/api/extensions/lan.marketdata/v1/inventory", params={"symbol": "300863.SZ"}
    )
    assert response.status_code == 503
    assert "ERROR" in response.json()["detail"]
    assert "DUCKDB_OOM" in response.json()["detail"]
    assert "starting" not in response.json()["detail"]


def test_known_unwarmed_stock_is_demanded_and_existing_bars_are_readable(
    tmp_path, monkeypatch
):
    sessions = days(10)
    with closing(Store(tmp_path / "partial.duckdb")) as store:
        seed(store, sessions, "300863.SZ")
        raw = source(sessions)
        store.accept(
            "300863.SZ",
            raw,
            sessions=sessions,
            start=min(raw),
            end=max(raw),
            now=max(raw),
            events=[],
            factor_response=True,
        )
        worker = runtime.Worker(SimpleNamespace(data_dir=tmp_path), {})
        worker.store = store
        monkeypatch.setattr(
            worker, "status", lambda: dict(phase="WARM", writer_alive=True)
        )
        monkeypatch.setattr(extension, "worker", worker)
        app = FastAPI()
        extension.setup(SimpleNamespace(include_router=app.include_router))
        client = TestClient(app)
        for _ in range(2):
            response = client.get(
                "/api/extensions/lan.marketdata/v1/bars",
                params=dict(symbols="300863.SZ", timeframes="15m"),
            )
            assert response.status_code == 200
            assert len(response.json()["rows"][0]["bars"]) == 160
        assert list(worker.demands) == ["300863.SZ"]
        assert worker.collector is None  # Reading/queuing never starts an HTTP fetch.


def test_view_priority_does_not_displace_live_sweep():
    sessions = days(70)
    end = sessions[-1] + " 15:00:00"
    master = {
        "600000.SH": dict(eligible=True, latest5=end, live_attempt=end),
        "300863.SZ": dict(
            eligible=True, cold_done=True, warm_count=0, latest5=end, live_attempt=end
        ),
    }
    work = runtime.task(master, sessions, end, end, {"300863.SZ"})
    assert work[2:] == ("WARM", ["300863.SZ", "600000.SH"])
    master["600000.SH"]["latest5"] = sessions[-2] + " 15:00:00"
    master["600000.SH"]["live_attempt"] = ""
    assert runtime.task(master, sessions, end, end, {"300863.SZ"})[2:] == (
        "LIVE",
        ["600000.SH"],
    )


def test_real_duckdb_oom_rolls_back_then_reopens_without_refetch(tmp_path, monkeypatch):
    sessions = days(2)
    path = tmp_path / "recover.duckdb"
    worker = runtime.Worker(SimpleNamespace(data_dir=tmp_path), {})
    worker.store = store = Store(path)
    worker.lease = ProcessLease(tmp_path / "writer.lock")
    seed(store, sessions)
    with store.transaction():
        store.set_meta("source_checkpoint", {"before": 1})
    worker.collector = SimpleNamespace(
        metrics={"http_requests": 1, "period_5m": 1},
        checkpoint=lambda: {"http_requests": 1, "period_5m": 1},
    )
    original = store._write
    calls, waits = [], []

    def fail_once(symbol, frames):
        original(symbol, frames)
        calls.append(symbol)
        if len(calls) == 1:
            # Force a real engine allocation failure after uncommitted writes.
            # The query is bounded by DuckDB's 1MB test-only budget.
            store.conn.execute("SET memory_limit='1MB'")
            store.conn.execute(
                "SELECT list(repeat(CAST(i AS VARCHAR),1024)) FROM range(100000) t(i)"
            )

    def backoff(seconds):
        waits.append(seconds)
        assert worker.status()["phase"] == "RECOVERING_DB"
        assert worker.status()["database_connected"] is False
        with pytest.raises(RuntimeError):
            ProcessLease(tmp_path / "writer.lock")
        with closing(Store(path)) as probe:
            assert probe.inventory() == []
            assert probe.instrument("600000.SH").get("latest5") is None
            assert probe.meta("last_commit") is None
            assert probe.meta("source_checkpoint") == {"before": 1}
        return False

    monkeypatch.setattr(store, "_write", fail_once)
    monkeypatch.setattr(worker.stop, "wait", backoff)
    raw = source(sessions)
    data = {"600000.SH": raw}
    try:
        worker.commit_batch(
            ["600000.SH"],
            data,
            {"600000.SH": []},
            sessions=sessions,
            start=min(raw),
            end=max(raw),
            now=max(raw),
            target=max(raw),
            stage="WARM",
        )
        assert waits == [5]
        assert len(calls) == 2 and data == {}
        assert store.instrument("600000.SH")["latest5"] == max(raw)
        assert store.meta("source_checkpoint")["http_requests"] == 1
        assert store.connection_status()["duckdb_memory_limit"] == "7.4 GiB"
        assert worker.state["db_retry_count"] == 1
        assert worker.state["last_error"].startswith("DUCKDB_OOM")
    finally:
        store.close()
        worker.lease.close()


def test_exhausted_oom_stops_cleanly_without_acknowledging_other_symbols(
    tmp_path, monkeypatch
):
    sessions = days(2)
    path = tmp_path / "exhausted.duckdb"
    store = Store(path)
    seed(store, sessions)
    with store.transaction():
        store.put_instrument(
            dict(symbol="600001.SH", eligible=True, source_type="stock", raw_tick=0.01)
        )
    raw = source(sessions)
    end = max(raw)
    waits = []

    class Source:
        def __init__(self, *args, **kwargs):
            self.metrics = dict(http_requests=1, period_5m=1, period_1m=0)
            self.budgets = {"minute": SimpleNamespace(defer=lambda seconds: None)}

        def factors(self, symbols):
            return {s: [] for s in symbols}

        def fetch(self, *args):
            return {"600000.SH": dict(raw), "600001.SH": dict(raw)}

        def checkpoint(self):
            return self.metrics

        def close(self):
            pass

    original = store._write

    def write(symbol, frames):
        original(symbol, frames)
        if symbol == "600001.SH":
            raise duckdb.OutOfMemoryException(
                "forced permanent pressure after partial SQL write"
            )

    worker = runtime.Worker(
        SimpleNamespace(data_dir=tmp_path), {"quantdash_token": "isolated-test"}
    )
    worker.lease = ProcessLease(tmp_path / "writer.lock")
    monkeypatch.setattr(runtime, "Store", lambda *args, **kwargs: store)
    monkeypatch.setattr(runtime, "Collector", Source)
    monkeypatch.setattr(runtime, "now_cn", lambda: datetime(2026, 11, 8, 22))
    monkeypatch.setattr(
        runtime,
        "task",
        lambda *args: (min(raw), end, "WARM", ["600000.SH", "600001.SH"]),
    )
    monkeypatch.setattr(store, "_write", write)
    monkeypatch.setattr(
        worker.stop, "wait", lambda seconds: waits.append(seconds) or False
    )
    worker.run()
    status = worker.status()
    assert waits == [5, 15]
    assert status["phase"] == "ERROR" and status["error_code"] == "DUCKDB_OOM"
    assert status["writer_alive"] is False and status["database_connected"] is False
    assert status["last_commit"] == "2026-11-08 22:00:00"
    with closing(Store(path)) as resumed:
        assert resumed.instrument("600000.SH")["latest5"] == end
        assert resumed.instrument("600001.SH").get("latest5") is None
        assert resumed.inventory("600001.SH") == []
        assert resumed.meta("source_checkpoint")["http_requests"] == 1


def test_invalid_configuration_has_a_live_status_and_no_database_recreation(tmp_path):
    worker = runtime.Worker(
        SimpleNamespace(data_dir=tmp_path), {"duckdb_memory_limit": "unlimited"}
    )
    worker.lease = ProcessLease(tmp_path / "writer.lock")
    worker.run()
    status = worker.status()
    assert status["phase"] == "ERROR"
    assert "duckdb_memory_limit" in status["error"]
    assert not (worker.folder / "rolling.duckdb").exists()


def test_100_symbol_64_session_batches_commit_bound_windows_and_resume(
    tmp_path, record_property
):
    sessions = days(67)
    symbols = [f"{980000 + i:06}.SZ" for i in range(100)]
    worker = runtime.Worker(SimpleNamespace(data_dir=tmp_path), {})
    path = tmp_path / "batch.duckdb"
    worker.store = store = Store(path)
    store.save_master(
        [
            dict(symbol=s, eligible=True, source_type="stock", raw_tick=0.01)
            for s in symbols
        ],
        dict(expected=100),
        sessions,
    )
    began = perf_counter()
    try:
        for batch, selected in enumerate((sessions[:64], sessions[-64:]), 1):
            raw = source(selected)
            assert len(raw) == 3072
            # Independent row dictionaries match a normalized 100-stock response.
            data = {s: {t: dict(r) for t, r in raw.items()} for s in symbols}
            worker.collector = SimpleNamespace(
                checkpoint=lambda: dict(
                    http_requests=batch, period_5m=batch, period_1m=0
                )
            )
            worker.commit_batch(
                symbols,
                data,
                {s: [] for s in symbols},
                sessions=sessions,
                start=min(raw),
                end=max(raw),
                now=max(raw),
                target=max(raw),
                stage="WARM",
            )
            assert data == {}
            assert all(r["row_count"] <= r["capacity"] for r in store.inventory())
        before = store.read(symbols[0], "60m", max(raw))
        assert before["bars"][-1]["ma233"] is not None
        assert store.meta("source_checkpoint")["period_1m"] == 0
        record_property("elapsed_seconds", perf_counter() - began)
        store.close()
        worker.store = store = Store(path)
        assert store.read(symbols[0], "60m", max(raw)) == before
        assert {r["timeframe"]: r["row_count"] for r in store.inventory(symbols[0])}[
            "5m"
        ] == CAPACITY["5m"]
    finally:
        store.close()
