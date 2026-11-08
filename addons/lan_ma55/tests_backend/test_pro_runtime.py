from datetime import timedelta
import json
import sqlite3

import pytest

from addons.lan_ma55.backend.live_store import LiveStore
from addons.lan_ma55.backend.market import (
    adjustment, aggregate_five, latest_point, next_missing, stamp,
)
from addons.lan_ma55.backend.pipeline import wall
from addons.lan_ma55.backend.runtime import ProcessLease, classify


def five_rows(bars):
    return {str(wall(bar["timestamp"]) - timedelta(minutes=n)):
            dict(open=bar["open"], high=bar["high"], low=bar["low"], close=bar["close"],
                 volume=bar["volume"] / 3, amount=None)
            for bar in bars for n in (10, 5, 0)}


def info(symbol="600000.SH"):
    return dict(symbol=symbol, name="合成证券", raw_tick=.01, listing_date="2000-01-01", eligible=True)


@pytest.mark.parametrize("now,expected", [
    ("2026-11-08 12:00:00", "2026-11-06 15:00:00"),
    ("2026-11-09 09:30:00", "2026-11-06 15:00:00"),
    ("2026-11-09 09:45:59", "2026-11-06 15:00:00"),
    ("2026-11-09 09:46:00", "2026-11-09 09:45:00"),
    ("2026-11-09 12:30:00", "2026-11-09 11:30:00"),
    ("2026-11-09 15:01:00", "2026-11-09 15:00:00"),
    ("2026-10-01 14:00:00", "2026-09-30 15:00:00"),
])
def test_real_session_completion_dates_not_natural_today(now, expected):
    assert latest_point(["2026-09-30", "2026-11-06", "2026-11-09"], now, step=15) == expected


def test_three_real_five_bars_and_hole_repair():
    day = "2026-11-06"
    row = dict(open=100, high=102, low=99, close=101, volume=100, amount=None)
    source = {stamp(day, m): row for m in (575, 580, 585)}
    bars, tail, missing = aggregate_five(source, [day], stamp(day, 575), stamp(day, 585))
    assert len(bars) == 1 and bars[0]["source_count"] == 3 and bars[0]["volume"] == 300
    assert not tail and not missing
    del source[stamp(day, 580)]
    bars, tail, missing = aggregate_five(source, [day], stamp(day, 575), stamp(day, 585))
    assert not bars and len(tail) == 2 and missing == [stamp(day, 580)]
    through, gap = next_missing(stamp(day, 575), stamp(day, 585), [day], {}, tail)
    assert through == stamp(day, 575) and gap == (stamp(day, 580), stamp(day, 580))


def test_factor_missing_is_not_identity_and_ratio_matches_host_contract():
    assert adjustment(None, "2026-11-05") is None
    assert adjustment([], "2026-11-05") == 1
    events = [dict(day="2026-11-06", ex_factor=2)]
    assert adjustment(events, "2026-11-05") == .5
    assert adjustment(events, "2026-11-06") == 1


def test_stock_pool_preserves_nonquoted_members_and_rejects_guessed_ticks():
    candidates = {s: s for s in ("600000.SH", "600001.SH", "588170.SH", "000001.SH", "920001.BJ")}
    def meta(symbol, kind="stock", tick=.01):
        return dict(symbol=symbol, name=symbol, region="CN", exchange=symbol[-2:], type=kind,
                    ext=dict(tick_size=tick, listing_date="2000-01-01"))
    rows, totals = classify(candidates, {s: meta(s, "etf" if s == "588170.SH" else "index" if s == "000001.SH" else "stock",
                                               0 if s == "600001.SH" else .01) for s in candidates}, {"600000.SH"}, "2026-11-08")
    assert totals["expected"] == 3 and totals["excluded"] == 2 and totals["unclassified"] == 0
    assert totals["unpriced"] == 1 and totals["non_snapshot_stocks"] == 2
    assert next(r for r in rows if r["symbol"] == "920001.BJ")["eligible"]


def test_real_receipts_state_cycles_history_and_restart_are_atomic(tmp_path, scenarios):
    case = scenarios["single"]
    symbol = "600000.SH"
    raw = five_rows(case["bars"])
    start, end = min(raw), max(raw)
    args = dict(start=start, end=end, target15=end, sessions=case["sessions"],
                master={symbol: info()}, now="2026-11-08 12:00:00", live_after="2026-11-08 11:00:00", elapsed_ms=10)
    path = tmp_path / "state.sqlite3"
    with LiveStore(path) as store:
        store.save_master([info()], dict(expected=1))
        store.accept([symbol], {symbol: raw}, {symbol: []}, **args)
        status = store.summary(end)
        assert status["cycle"]["processed_symbols"] == 1 and status["cycle"]["valid_symbols"] == 1
        assert status["data_asof"] == end and status["market_date"] == end[:10]
        assert store.journal(symbol)["through"] == end and not store.journal(symbol)["gap"]
        events = list(store.conn.execute("SELECT event_id,payload FROM signal_event"))
        assert events and all(json.loads(e[1])["mode"] == "REPLAY" for e in events)
        assert store.day_rows(end[:10])[0]["events_json"] != "[]"
    with LiveStore(path) as resumed:
        assert resumed.journal(symbol)["through"] == end
        assert list(resumed.conn.execute("SELECT event_id,payload FROM signal_event")) == events
        assert resumed.summary(end)["cycle"]["processed_symbols"] == 1


def test_gap_does_not_fake_watermark_and_repair_keeps_event_identity(tmp_path, scenarios):
    case = scenarios["single"]
    symbol = "600000.SH"
    raw = five_rows(case["bars"])
    start, end = min(raw), max(raw)
    hole = sorted(raw)[-190]
    missing = dict(raw)
    recovered = missing.pop(hole)
    args = dict(target15=end, sessions=case["sessions"], master={symbol: info()},
                now="2026-11-08 12:00:00", live_after="2026-11-08 11:00:00", elapsed_ms=1)
    with LiveStore(tmp_path / "state.sqlite3") as store:
        store.save_master([info()], dict(expected=1))
        store.accept([symbol], {symbol: missing}, {symbol: []}, start=start, end=end, **args)
        assert store.journal(symbol)["through"] < hole and store.journal(symbol)["gap"] == [hole, hole]
        store.accept([symbol], {symbol: {hole: recovered}}, {}, start=hole, end=hole, **args)
        assert store.journal(symbol)["through"] == end and store.journal(symbol)["gap"] is None
        assert store.summary(end)["cycle"]["processed_symbols"] == 1
        assert store.conn.execute("SELECT COUNT(*) FROM signal_event").fetchone()[0] == 1


def test_two_process_leases_cannot_write_same_owner_folder(tmp_path):
    lease = ProcessLease(tmp_path / "worker.lock")
    try:
        with pytest.raises(RuntimeError, match="Another MA55"):
            ProcessLease(tmp_path / "worker.lock")
    finally:
        lease.close()
    ProcessLease(tmp_path / "worker.lock").close()


def test_new_member_preserves_actual_old_cycle_and_does_not_need_old_k_again(tmp_path, scenarios):
    case = scenarios["single"]
    old, new = info(), info("600001.SH")
    raw = five_rows(case["bars"])
    with LiveStore(tmp_path / "state.sqlite3") as store:
        store.save_master([old], dict(expected=1))
        store.accept([old["symbol"]], {old["symbol"]: raw}, {old["symbol"]: []},
                     start=min(raw), end=max(raw), target15=max(raw), sessions=case["sessions"],
                     master={old["symbol"]: old}, now="2026-11-08 12:00:00", live_after="2026-11-08 11:00:00", elapsed_ms=1)
        signals = store.conn.execute("SELECT COUNT(*) FROM signal_event").fetchone()[0]
        store.save_master([old, new], dict(expected=2))
        cycle = store.summary(max(raw))["cycle"]
        assert cycle["expected_symbols"] == 2 and cycle["processed_symbols"] == 1
        assert cycle["valid_symbols"] == 1 and cycle["new_signals"] == signals
        assert store.journal(old["symbol"])["through"] == max(raw)


def test_journal_write_failure_rolls_back_inputs_events_and_watermarks(tmp_path, scenarios):
    case = scenarios["single"]
    stock = info()
    raw = five_rows(case["bars"])
    with LiveStore(tmp_path / "state.sqlite3") as store:
        store.save_master([stock], dict(expected=1))
        store.conn.execute("CREATE TRIGGER reject_journal BEFORE INSERT ON collection_journal BEGIN SELECT RAISE(ABORT,'synthetic disk boundary'); END")
        with pytest.raises(sqlite3.IntegrityError, match="synthetic disk boundary"):
            store.accept([stock["symbol"]], {stock["symbol"]: raw}, {stock["symbol"]: []},
                         start=min(raw), end=max(raw), target15=max(raw), sessions=case["sessions"], master={stock["symbol"]: stock},
                         now="2026-11-08 12:00:00", live_after="2026-11-08 11:00:00", elapsed_ms=1)
        assert store.conn.execute("SELECT COUNT(*) FROM symbol_state").fetchone()[0] == 0
        assert store.conn.execute("SELECT COUNT(*) FROM signal_event").fetchone()[0] == 0
        assert store.conn.execute("SELECT COUNT(*) FROM recent_bar").fetchone()[0] == 0
