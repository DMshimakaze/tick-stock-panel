from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from copy import deepcopy
import json
import sqlite3
from time import perf_counter

import pytest

from addons.lan_ma55.backend.engine import RULE_VERSION
from addons.lan_ma55.backend.pipeline import process_symbol
from addons.lan_ma55.backend.service import OfflineService
from addons.lan_ma55.backend.store import StateWriter, read_connection, read_page
from .conftest import batches, universe


def arguments(case, stocks, bars=None):
    bars = bars or case["bars"][-56:]
    end = bars[-1]["timestamp"]
    return dict(
        universe=stocks,
        inputs=batches(stocks, bars),
        bar_end=end,
        sessions=case["sessions"],
        source_asof=end,
        first_seen_at="2026-11-07 20:00:00",
    )


def test_5000_synthetic_states_warmup_increment_and_readonly_paging(
    tmp_path, scenarios
):
    path = tmp_path / "state.sqlite3"
    stocks = universe(5000)
    started = perf_counter()
    with OfflineService(path) as service:
        first = service.scan(**arguments(scenarios["single"], stocks))
        warm_ms = (perf_counter() - started) * 1000
        assert first["status"] == "COMPLETE" and first["mode"] == "OFFLINE"
        assert (
            first["expected_symbols"]
            == first["processed_symbols"]
            == first["valid_symbols"]
            == 5000
        )
        assert first["new_events_this_call"] == 5000
        assert first["completed_at"] is not None
        bar = scenarios["duplicate"]["bars"][-2]
        started = perf_counter()
        steady = service.scan(**arguments(scenarios["duplicate"], stocks, [bar]))
        steady_ms = (perf_counter() - started) * 1000
        assert steady["processed_symbols"] == steady["valid_symbols"] == 5000
        assert steady["new_events_this_call"] == 0
    names = []
    for offset in range(0, 5000, 200):
        page = read_page(path, "symbols", limit=200, offset=offset)
        assert page["total"] == 5000
        names.extend(row["symbol"] for row in page["rows"])
        for row in page["rows"]:
            assert len(row["payload"]["feature_history"]) == 55
            assert all(len(v) <= 5 for v in row["payload"]["slot_volumes"].values())
    assert len(set(names)) == 5000
    assert read_page(path, "events")["total"] == 5000
    with closing(read_connection(path)) as conn:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("DELETE FROM signal_event")
    print(
        "SYNTHETIC_5000 "
        + json.dumps(
            dict(
                warmup_ms=round(warm_ms, 2),
                increment_ms=round(steady_ms, 2),
                sqlite_bytes=path.stat().st_size,
                actual_market_throughput=False,
            )
        )
    )


def test_restart_repeated_cycle_and_bounded_transaction_recovery(tmp_path, scenarios):
    path = tmp_path / "state.sqlite3"
    stocks = universe(300)
    case = scenarios["single"]

    def interrupted():
        yield next(batches(stocks, case["bars"][-56:]))
        raise RuntimeError("Synthetic interrupted input")

    with OfflineService(path) as service:
        args = arguments(case, stocks)
        args["inputs"] = interrupted()
        with pytest.raises(RuntimeError, match="Synthetic interrupted"):
            service.scan(**args)
    partial = read_page(path, "cycles")["rows"][0]
    assert (
        partial["processed_symbols"] == 128
        and partial["expected_symbols"] == 300
        and partial["status"] == "FAILED"
    )
    assert read_page(path, "events")["total"] == 128
    with OfflineService(path) as service:
        resumed = service.scan(**arguments(case, stocks))
        assert (
            resumed["processed_symbols"] == 300
            and resumed["new_events_this_call"] == 172
        )
        frozen = read_page(path, "events")["rows"]
        repeated = service.scan(**arguments(case, stocks))
        assert repeated["new_events_this_call"] == 0
        assert read_page(path, "events")["rows"] == frozen
    assert read_page(path, "events")["total"] == 300


def test_single_writer_and_simultaneous_clients_do_not_duplicate(tmp_path, scenarios):
    path = tmp_path / "state.sqlite3"
    stocks = universe(2)
    case = scenarios["single"]
    with OfflineService(path) as service:
        with pytest.raises(RuntimeError, match="already has a writer"):
            OfflineService(path)
        with ThreadPoolExecutor(2) as pool:
            jobs = [
                pool.submit(lambda: service.scan(**arguments(case, stocks)))
                for _ in range(2)
            ]
            results = [job.result() for job in jobs]
        assert sum(r["new_events_this_call"] for r in results) == 2
    assert read_page(path, "events")["total"] == 2
    with StateWriter(path) as store:
        with ThreadPoolExecutor(1) as pool:
            with pytest.raises(sqlite3.ProgrammingError):
                pool.submit(store.cycle, "anything").result()


def test_incomplete_coverage_failure_counts_source_stale_and_weekend(
    tmp_path, scenarios
):
    path = tmp_path / "state.sqlite3"
    stocks = universe(4)
    case = scenarios["single"]
    args = arguments(case, stocks)
    with OfflineService(path) as service:
        stale = service.scan(**{**args, "source_healthy": False})
        assert stale["status"] == "SOURCE_STALE" and stale["processed_symbols"] == 0
        end = case["bars"][-1]["timestamp"]
        short = [dict(case["bars"][-1], volume=100)]
        batch = [
            (stocks[0]["symbol"], case["bars"][-56:]),
            (stocks[1]["symbol"], short),
            (stocks[2]["symbol"], []),
            (stocks[3]["symbol"], [{"timestamp": "bad-time"}]),
        ]
        done = service.scan(**{**args, "inputs": [batch]})
        assert done["status"] == "COMPLETE"
        assert [
            done[k]
            for k in (
                "valid_symbols",
                "insufficient_symbols",
                "gap_symbols",
                "failed_symbols",
            )
        ] == [1, 1, 1, 1]
        assert (
            done["processed_symbols"]
            == sum(
                done[k]
                for k in (
                    "valid_symbols",
                    "insufficient_symbols",
                    "gap_symbols",
                    "failed_symbols",
                )
            )
            == 4
        )
        assert read_page(path, "events")["total"] == 1
        with pytest.raises(ValueError, match="outside"):
            service.scan(
                **{
                    **args,
                    "bar_end": "2026-11-07 10:00:00",
                    "source_asof": "2026-11-07 10:00:00",
                }
            )
        with pytest.raises(ValueError, match="not reached"):
            service.scan(**{**args, "source_asof": end[:10] + " 09:30:00"})
    assert read_page(path, "events")["total"] == 1


def test_missing_next_point_marks_risk_without_rewriting_confirmation(
    tmp_path, scenarios
):
    path = tmp_path / "state.sqlite3"
    stocks = universe(1)
    with OfflineService(path) as service:
        service.scan(**arguments(scenarios["single"], stocks))
        frozen = read_page(path, "events")["rows"][0]["payload"]
        next_bar = scenarios["duplicate"]["bars"][-2]
        args = arguments(scenarios["duplicate"], stocks, [next_bar])
        args["inputs"] = [[(stocks[0]["symbol"], [])]]
        result = service.scan(**args)
        assert result["gap_symbols"] == 1 and result["new_events_this_call"] == 0
    event = read_page(path, "events")["rows"][0]
    assert event["payload"] == frozen
    assert event["risk"]["data_pending_at"] == next_bar["timestamp"]


def test_confirmation_immutable_separate_risk_version_isolation_and_retention(
    tmp_path, scenarios
):
    path = tmp_path / "state.sqlite3"
    case = scenarios["single"]
    stocks = universe(1)
    with OfflineService(path) as service:
        cycle = service.scan(**arguments(case, stocks))
    record, _ = process_symbol(
        stocks[0]["symbol"],
        case["bars"],
        sessions=case["sessions"],
        raw_tick=0.01,
        source_asof=case["bars"][-1]["timestamp"],
        first_seen_at="2026-11-07 20:00:00",
    )
    original = read_page(path, "events")["rows"][0]["payload"]
    record["confirmations"][0]["first_seen_at"] = "2099-01-01 00:00:00"
    record["risks"][original["event_id"]]["invalidated_at"] = "2026-11-07 15:00:00"
    cycle.pop("new_events_this_call")
    with StateWriter(path) as store:
        store.save([record], {record["symbol"]: "valid"}, cycle)
        event = read_page(path, "events")["rows"][0]
        assert event["payload"] == original
        assert event["risk"]["invalidated_at"] == "2026-11-07 15:00:00"
        old = deepcopy(record)
        old["rule_version"] = "MA55_V4"
        old["confirmations"][0]["rule_version"] = "MA55_V4"
        old["confirmations"][0]["event_id"] = "MA55_V4:archived"
        old["risks"] = {"MA55_V4:archived": {"invalidated_at": None}}
        store.save([old], {old["symbol"]: "valid"}, cycle)
        saved, _ = store.records([record["symbol"]])
        renewed, _ = process_symbol(
            record["symbol"],
            case["bars"][-56:],
            sessions=case["sessions"],
            raw_tick=0.01,
            source_asof=case["bars"][-1]["timestamp"],
            first_seen_at="2026-11-07 21:00:00",
            previous=saved[record["symbol"]],
        )
        store.save([renewed], {record["symbol"]: "valid"}, cycle)
        assert read_page(path, "events")["total"] == 1
        assert read_page(path, "events", rule_version="MA55_V4")["total"] == 1
        assert read_page(path, "symbols")["rows"][0]["rule_version"] == RULE_VERSION
        store.prune("2026-11-01")
    assert read_page(path, "events")["total"] == 0
    with pytest.raises(ValueError):
        OfflineService(path, retention_sessions=1)
