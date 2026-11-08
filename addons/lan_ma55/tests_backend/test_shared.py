from copy import deepcopy
from datetime import datetime, timedelta
import csv
import json
import os
from pathlib import Path

import pytest

from addons.lan_ma55.backend.engine import RULE_VERSION, STATE_REVISION
from addons.lan_ma55.backend.pipeline import process_symbol
from addons.lan_ma55.backend.shared_store import SharedStore
from addons.lan_ma55.backend.store import read_page


def packet(case, revision=1):
    return dict(
        symbol="600000.SH",
        name="合成股票",
        bars=case["bars"],
        revision=revision,
        basis_revision=0,
        state="READY",
        price_basis="QFQ",
        raw_tick=0.01,
        source_asof=case["bars"][-1]["timestamp"],
    )


def test_v5_history_revision4_schema1_replay_and_restart(tmp_path, scenarios):
    case = scenarios["single"]
    end = case["bars"][-1]["timestamp"]
    symbol = "600000.SH"
    path = tmp_path / "state.sqlite3"
    prior, _ = process_symbol(
        symbol,
        case["bars"],
        sessions=case["sessions"],
        raw_tick=0.01,
        source_asof=end,
        first_seen_at=end,
    )
    old = deepcopy(prior)
    old["rule_version"] = "MA55_15M_MINIMAL_V5"
    old["state"].pop("state_revision")
    for e in old["confirmations"]:
        e["rule_version"] = old["rule_version"]
        e["event_id"] = e["event_id"].replace(RULE_VERSION, old["rule_version"])
    old["risks"] = {e["event_id"]: {} for e in old["confirmations"]}
    with SharedStore(path) as store:
        store.source_master(
            [dict(symbol=symbol, name=symbol, eligible=True)], dict(expected=1), 1
        )
        store.save([old], {symbol: "valid"}, store.new_cycle(end, 1, end, "REPLAY"))
        original = [tuple(r) for r in store.conn.execute("SELECT * FROM signal_event")]
        store.consume(
            [packet(case)],
            sessions=case["sessions"],
            target=end,
            now="2026-11-08 12:00:00",
            live_after=end,
        )
        assert store.conn.execute("PRAGMA user_version").fetchone()[0] == 1
        assert store.records([symbol])[0][symbol]["state"]["state_revision"] == 4
        assert [
            tuple(r) for r in store.conn.execute("SELECT * FROM signal_event")
        ] == original
        assert read_page(path, "events")["total"] == len(original)
        assert (
            store.conn.execute("SELECT count(*) FROM rule_state_archive").fetchone()[0]
            >= 1
        )
        assert (
            store.consume(
                [packet(case)],
                sessions=case["sessions"],
                target=end,
                now="2026-11-08 12:00:00",
                live_after=end,
            )
            == []
        )
        assert store.conn.execute("SELECT count(*) FROM recent_bar").fetchone()[0] == 0
        assert (
            store.conn.execute("SELECT count(*) FROM collection_journal").fetchone()[0]
            == 0
        )
    with SharedStore(path) as store:
        assert [
            tuple(r) for r in store.conn.execute("SELECT * FROM signal_event")
        ] == original
        assert store.history()[end[:10]]["buy_count"] == len(original)


def test_unavailable_factor_does_not_create_buy(tmp_path, scenarios):
    case = scenarios["single"]
    data = packet(case)
    data.update(state="FACTOR_PENDING", price_basis="RAW")
    with SharedStore(tmp_path / "s.sqlite3") as store:
        store.source_master(
            [dict(symbol=data["symbol"], eligible=True)], dict(expected=5891), 1
        )
        store.consume(
            [data],
            sessions=case["sessions"],
            target=case["bars"][-1]["timestamp"],
            now="2026-11-08 12:00:00",
            live_after="2026-11-08 10:00:00",
        )
        assert (
            store.conn.execute("SELECT count(*) FROM signal_event").fetchone()[0] == 0
        )
        assert (
            store.summary(case["bars"][-1]["timestamp"])["cycle"]["expected_symbols"]
            == 5891
        )


def test_history_keeps_watch_gap_rows_and_real_cycle(tmp_path):
    from addons.lan_ma55.backend.store import encoded

    day = "2026-11-04"
    with SharedStore(tmp_path / "s.sqlite3") as store:
        cycle = store.new_cycle(day + " 15:00:00", 2, day + " 15:02:00", "REPLAY")
        store.save([], {}, cycle)
        with store.conn:
            for symbol, phase, category in [
                ("600000.SH", "WATCHING", "valid"),
                ("000001.SZ", "DATA_PENDING", "gap"),
            ]:
                row = dict(
                    symbol=symbol,
                    name=symbol,
                    phase=phase,
                    category=category,
                    events_json="[]",
                    market_date=day,
                )
                store.conn.execute(
                    "INSERT INTO day_state VALUES(?,?,?)", (symbol, day, encoded(row))
                )
        history = store.history()[day]
        assert history["cycle"]["expected_symbols"] == 2
        assert history["total"] == 2 and history["buy_count"] == 0
        assert {r["phase"] for r in history["rows"]} == {"WATCHING", "DATA_PENDING"}


def test_private_tuowei_real_5m_replay_r1_first_red_and_rolling_restart(tmp_path):
    folder = os.environ.get("MA55_TUOWEI_INPUT")
    if not folder or not (Path(folder) / "tuowei_15m.csv").exists():
        pytest.skip("Private verified Tuowei inputs not mounted")
    with (Path(folder) / "tuowei_15m.csv").open(encoding="utf-8-sig") as f:
        raw = list(csv.DictReader(f))
    with (Path(folder) / "tuowei_raw_5m.csv").open(encoding="utf-8-sig") as f:
        five = {r["timestamp"]: r for r in csv.DictReader(f)}
    for row in raw:
        end = datetime.fromisoformat(row["timestamp"])
        parts = [five[str(end - timedelta(minutes=n))] for n in (10, 5, 0)]
        factor = float(row["close"]) / float(row["raw_close"])
        grouped = dict(
            open=float(parts[0]["open"]),
            high=max(float(p["high"]) for p in parts),
            low=min(float(p["low"]) for p in parts),
            close=float(parts[-1]["close"]),
            volume=sum(float(p["volume"]) for p in parts),
        )
        for field, value in grouped.items():
            assert float(row[field]) == pytest.approx(
                value * (1 if field == "volume" else factor), rel=1e-10
            )
    bars = [
        dict(
            timestamp=r["timestamp"],
            **{k: float(r[k]) for k in ("open", "high", "low", "close", "volume")},
            source_count=3,
            adjust_factor=float(r["close"]) / float(r["raw_close"]),
        )
        for r in raw
    ]
    sessions = sorted({r["timestamp"][:10] for r in bars})
    record = None
    for i, bar in enumerate(bars):
        record, added = process_symbol(
            "002261.SZ",
            bars[max(0, i - 255) : i + 1],
            sessions=sessions,
            raw_tick=0.01,
            source_asof=bar["timestamp"],
            first_seen_at="2026-11-08 12:00:00",
            previous=record,
        )
        record = json.loads(json.dumps(record))
        if bar["timestamp"] == "2026-11-04 11:00:00":
            assert not record["state"]["decision"]["event"]
        if bar["timestamp"] == "2026-11-04 11:15:00":
            assert (
                record["state"]["decision"]["event"] == "BUY_STRUCTURE"
                and len(added) == 1
            )
        if bar["timestamp"] == "2026-11-04 11:30:00":
            assert not added
    assert STATE_REVISION == 4 and RULE_VERSION.endswith("_V5_R1")
