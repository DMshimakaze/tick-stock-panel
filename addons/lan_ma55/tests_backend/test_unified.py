from copy import deepcopy
from datetime import timedelta
import sqlite3

import pytest

from addons.lan_ma55.backend.market_grid import wall
from addons.lan_ma55.backend.unified_store import UnifiedStore
from addons.lan_ma55.migrate_unified import migrate, row_digests
from addons.lan_ma55.tests_market.test_rolling import opened, put, sample


def prepared(path, sessions):
    owner = UnifiedStore(path)
    owner.refresh_master(
        [
            dict(
                symbol="600000.SH",
                name="Synthetic",
                eligible=True,
                source_type="stock",
                raw_tick=0.01,
            )
        ],
        {"expected": 1},
        sessions,
    )
    return owner


def test_market_strategy_and_receipt_rollback_together(tmp_path, monkeypatch):
    sessions, rows = sample(250)
    with prepared(tmp_path / "state.sqlite3", sessions) as owner:
        assert owner.conn is owner.market.conn
        original = owner.consume

        def fail(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError("injected failure after strategy save")

        monkeypatch.setattr(owner, "consume", fail)
        with pytest.raises(RuntimeError, match="injected failure"):
            owner.accept_market(
                "600000.SH",
                rows,
                sessions=sessions,
                start=min(rows),
                end=max(rows),
                now=max(rows),
                events=[],
                factor_response=True,
                live_after=max(rows),
            )
        for table in (
            "bar15",
            "tail5",
            "signal_event",
            "symbol_state",
            "processing_receipt",
        ):
            assert (
                owner.conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
            )
        assert owner.market.instrument("600000.SH")["latest_completed15"] is None
        monkeypatch.setattr(owner, "consume", original)
        owner.accept_market(
            "600000.SH",
            rows,
            sessions=sessions,
            start=min(rows),
            end=max(rows),
            now=max(rows),
            events=[],
            factor_response=True,
            live_after=max(rows),
        )
        assert owner.conn.execute("SELECT count(*) FROM bar15").fetchone()[0] == 250
        receipt = tuple(
            owner.conn.execute("SELECT * FROM processing_receipt").fetchone()
        )
    with UnifiedStore(tmp_path / "state.sqlite3") as owner:
        assert (
            tuple(owner.conn.execute("SELECT * FROM processing_receipt").fetchone())
            == receipt
        )
        assert owner.catch_up(max(rows), max(rows), max(rows)) == []


def test_migration_preserves_every_column_and_is_repeatable(tmp_path):
    sessions, rows = sample(60)
    old = opened(tmp_path, sessions)
    put(old, rows, sessions)
    old.close()
    state = tmp_path / "old-state.sqlite3"
    with prepared(state, sessions) as owner:
        event = (
            "old-id",
            "600000.SH",
            "OLD_RULE",
            min(rows),
            max(rows),
            max(rows),
            max(rows),
            "A",
            "LIVE",
            '{"original": [1, 2], "defense": 9.25}',
        )
        with owner.conn:
            owner.conn.execute(
                "INSERT INTO signal_event VALUES(?,?,?,?,?,?,?,?,?,?)", event
            )
            owner.conn.execute(
                "INSERT INTO event_risk VALUES(?,?,?)",
                ("old-id", '{"risk": "original"}', max(rows)),
            )
        expected = {
            t: row_digests(owner.conn, t) for t in ("signal_event", "event_risk")
        }
    for _ in range(2):
        report = migrate(
            state, tmp_path / "rolling15.sqlite3", tmp_path / "unified.sqlite3"
        )
        assert report["original_rows_equal"] == {"signal_event": 1, "event_risk": 1}
        assert report["counts"]["bar15"] == 60
    with sqlite3.connect(tmp_path / "unified.sqlite3") as db:
        assert expected == {t: row_digests(db, t) for t in expected}


def test_idempotent_input_keeps_frozen_structure(tmp_path, scenarios):
    case = scenarios["single"]
    # The real frozen engine builds its lock from the supplied deterministic case.
    sessions = case["sessions"]
    five = {}
    for bar in case["bars"]:
        for minute in (10, 5, 0):
            five[str(wall(bar["timestamp"]) - timedelta(minutes=minute))] = {
                **{k: bar[k] for k in ("open", "high", "low", "close")},
                "volume": bar["volume"] / 3,
                "amount": None,
            }
    with prepared(tmp_path / "state.sqlite3", sessions) as owner:
        owner.accept_market(
            "600000.SH",
            five,
            sessions=sessions,
            start=min(five),
            end=max(five),
            now=max(five),
            events=[],
            factor_response=True,
            live_after=max(five),
        )
        record = owner.records(["600000.SH"])[0]["600000.SH"]
        assert record["confirmations"]
        before = deepcopy(record["state"]["structure"])
        saved = row_digests(owner.conn, "signal_event")
        # Same data is a receipt no-op and cannot add/rewrite a confirmation.
        owner.accept_market(
            "600000.SH",
            five,
            sessions=sessions,
            start=min(five),
            end=max(five),
            now=max(five),
            events=[],
            factor_response=True,
            live_after=max(five),
        )
        assert (
            owner.records(["600000.SH"])[0]["600000.SH"]["state"]["structure"] == before
        )
        assert row_digests(owner.conn, "signal_event") == saved


@pytest.mark.parametrize(
    "state", ["GAP", "FACTOR_PENDING", "SOURCE_DISCREPANCY", "SOURCE_UNAVAILABLE"]
)
def test_invalid_source_holds_saved_lock(tmp_path, scenarios, state):
    from addons.lan_ma55.tests_backend.test_shared import packet
    from addons.lan_ma55.backend.shared_store import SharedStore

    case = scenarios["single"]
    source = packet(case)
    end = source["bars"][-1]["timestamp"]
    with SharedStore(tmp_path / "state.sqlite3") as store:
        store.source_master(
            [dict(symbol="600000.SH", eligible=True)], {"expected": 1}, 1
        )
        store.consume(
            [source], sessions=case["sessions"], target=end, now=end, live_after=end
        )
        old = deepcopy(store.records(["600000.SH"])[0]["600000.SH"])
        source.update(state=state, revision=2)
        store.consume(
            [source], sessions=case["sessions"], target=end, now=end, live_after=end
        )
        new = store.records(["600000.SH"])[0]["600000.SH"]
        assert new["state"]["structure"] == old["state"]["structure"]
        assert new["confirmations"] == old["confirmations"]


def test_unchanged_risk_keeps_original_full_row_on_receipt_refresh(tmp_path, scenarios):
    from addons.lan_ma55.tests_backend.test_shared import packet
    from addons.lan_ma55.backend.shared_store import SharedStore

    case = scenarios["single"]
    source = packet(case)
    end = source["bars"][-1]["timestamp"]
    with SharedStore(tmp_path / "state.sqlite3") as store:
        store.source_master(
            [dict(symbol="600000.SH", eligible=True)], {"expected": 1}, 1
        )
        store.consume(
            [source], sessions=case["sessions"], target=end, now=end, live_after=end
        )
        old = {t: row_digests(store.conn, t) for t in ("signal_event", "event_risk")}
        source["revision"] += 1
        store.consume(
            [source],
            sessions=case["sessions"],
            target=end,
            now=str(wall(end) + timedelta(minutes=5)),
            live_after=end,
        )
        assert old == {t: row_digests(store.conn, t) for t in old}


def test_structure_lock_survives_250_bar_eviction(tmp_path, scenarios):
    from addons.lan_ma55.backend.market_grid import points
    from datetime import date

    case = scenarios["single"]
    sessions = list(case["sessions"])
    day = date.fromisoformat(sessions[-1])
    while len(sessions) < 40:
        day += timedelta(days=1)
        if day.weekday() < 5:
            sessions.append(str(day))
    five = {}
    for bar in case["bars"]:
        for minute in (10, 5, 0):
            five[str(wall(bar["timestamp"]) - timedelta(minutes=minute))] = {
                **{k: bar[k] for k in ("open", "high", "low", "close")},
                "volume": bar["volume"] / 3,
                "amount": None,
            }
    path = tmp_path / "state.sqlite3"
    with prepared(path, sessions) as owner:
        end = max(five)
        owner.accept_market(
            "600000.SH",
            five,
            sessions=sessions,
            start=min(five),
            end=end,
            now=end,
            live_after=end,
            events=[],
            factor_response=True,
        )
        old = owner.records(["600000.SH"])[0]["600000.SH"]
        lock_at = old["state"]["structure"]["last_buy"]["at"]
        assert old["state"]["structure"]["locked"]
        original_events = row_digests(owner.conn, "signal_event")
        close = old["state"]["decision"]["ma55"]
        future = points(sessions, end, sessions[-1] + " 15:00:00", step=15)[1:301]
        assert len(future) == 300
        for at in future:
            rows = {
                str(wall(at) - timedelta(minutes=n)): dict(
                    open=close,
                    high=close + 0.01,
                    low=close - 0.01,
                    close=close,
                    volume=100.0,
                    amount=None,
                )
                for n in (10, 5, 0)
            }
            owner.accept_market(
                "600000.SH",
                rows,
                sessions=sessions,
                start=min(rows),
                end=at,
                now=at,
                live_after=at,
            )
        assert owner.market.raw_rows("600000.SH")[0]["timestamp"] > lock_at
        assert owner.records(["600000.SH"])[0]["600000.SH"]["state"]["structure"][
            "locked"
        ]
        assert row_digests(owner.conn, "signal_event") == original_events


def test_real_frozen_case_with_250_bar_windows_and_persisted_restart(frozen_cases):
    from addons.lan_ma55.backend.pipeline import process_symbol
    import json

    case = next(
        c
        for c in frozen_cases
        if c["kind"] == "positive"
        and c["symbol"] == "601869.SH"
        and c["when"].endswith("11:30:00")
    )
    bars = [
        dict(b, source_count=3, adjust_factor=case["factors"][b["timestamp"][:10]])
        for b in case["bars"]
    ]
    record = None
    for i, bar in enumerate(bars):
        record, _ = process_symbol(
            case["symbol"],
            bars[max(0, i - 249) : i + 1],
            sessions=case["sessions"],
            raw_tick=case["tick"],
            source_asof=bar["timestamp"],
            first_seen_at=case["when"],
            previous=record,
        )
        if i % 100 == 0:
            record = json.loads(json.dumps(record))
    assert [
        {k: e[k] for k in ("event_id", "confirmed_at", "setup_at", "pattern")}
        for e in record["confirmations"]
    ] == case["events"]
    assert record["state"]["decision"]["event"] == "BUY_STRUCTURE"


def test_event_api_window_pagination_keeps_outside_count(
    tmp_path, scenarios, monkeypatch
):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from types import SimpleNamespace
    from addons.lan_ma55.backend import extension
    from addons.lan_ma55.tests_backend.test_shared import packet

    case = scenarios["departure"]
    with prepared(tmp_path / "state.sqlite3", case["sessions"]) as owner:
        source = packet(case)
        end = source["bars"][-1]["timestamp"]
        owner.consume(
            [source], sessions=case["sessions"], target=end, now=end, live_after=end
        )
        monkeypatch.setattr(extension, "data_path", owner.path)
        app = FastAPI()
        extension.setup(SimpleNamespace(include_router=app.include_router))
        with TestClient(app) as client:
            page = client.get(
                "/api/extensions/lan.ma55/v1/events",
                params={"symbol": "600000.SH", "limit": 1},
            ).json()
            assert page["total"] == 2 and len(page["rows"]) == 1
            latest = page["rows"][0]["confirmed_at"]
            visible = client.get(
                "/api/extensions/lan.ma55/v1/events",
                params={"symbol": "600000.SH", "start": latest, "end": latest},
            ).json()
            assert visible["total"] == 1 and visible["symbol_total"] == 2
            assert (
                client.get(
                    "/api/extensions/lan.ma55/v1/events", params={"start": "bad"}
                ).status_code
                == 422
            )


def test_history_only_buy_date_survives_summary(tmp_path):
    with prepared(tmp_path/"state.sqlite3",["2026-11-12"]) as owner:
        with owner.conn:
            owner.conn.execute("INSERT INTO signal_event VALUES(?,?,?,?,?,?,?,?,?,?)",
                ("old-buy","600000.SH","OLD","2026-09-01 10:00:00","2026-09-01 10:15:00",
                 "2026-09-01 10:16:00","2026-09-01 10:15:00","A","LIVE","{}"))
        assert owner.summary("2026-11-12 15:00:00")["available_dates"]==["2026-09-01"]
