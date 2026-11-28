import json
from datetime import date, timedelta
from threading import Event
from time import time
from uuid import uuid4

import httpx
import pytest

from addons.lan_ma55.backend.activation_results import activation, confirmations, display_targets
from addons.lan_ma55.backend.daily_store import DailyStore
from addons.lan_ma55.backend.exchange_sessions import frame_schedules, planned_session
from addons.lan_ma55.backend.focus_owner import FocusOwner, THSTransport
from addons.lan_ma55.backend.market_grid import MAS, combined, stamp
from addons.lan_ma55.backend.multiframe_core.multiframe import VERSION
from addons.lan_ma55.backend.multiframe_input import DAILY_SOURCE, frame_bars
from addons.lan_ma55.backend.native15_owner import NativeOwner
from addons.lan_ma55.backend.native15_shadow import PROOF, SOURCE
from addons.lan_ma55.backend.observations import Observations
from addons.lan_ma55.backend.runtime_status import completed_targets, observation_summary
from addons.lan_ma55.backend.ths_focus import CATALOG, MEMBERS, THSUnavailable
from addons.lan_ma55.backend.ths_taxonomy import REVERSE

SYMBOL = "600000.SH"
BOARD = "industry:881001.TI"


@pytest.mark.parametrize("status", [401, 403, 429])
def test_ths_rejection_stops_actual_http_and_429_survives_restart(tmp_path, status):
    requests = []

    def vendor(request):
        assert request.headers["X-api-key"] == "test-key"
        requests.append(request)
        return httpx.Response(status, headers={"Retry-After": "80"})

    with NativeOwner(tmp_path / "state.sqlite3") as owner:
        source = THSTransport(owner, "test-key", Event(), 12, transport=httpx.MockTransport(vendor))
        try:
            for _ in range(2):
                with pytest.raises(THSUnavailable):
                    source.request(MEMBERS, {"thscode": "881001.TI"})
            assert len(requests) == 1
            assert owner.conn.execute("SELECT status FROM native15_request_receipt").fetchone()[0] == status
        finally:
            source.close()
        if status == 429:
            assert owner.meta("ths_retry_until") > time() + 75
            restored = THSTransport(owner, "test-key", Event(), 12, transport=httpx.MockTransport(vendor))
            try:
                with pytest.raises(THSUnavailable, match="THS_RATE_LIMITED"):
                    restored.request(CATALOG, {"tag": "industry"})
                assert len(requests) == 1
            finally:
                restored.close()


def test_focus_explicit_pause_persists_and_noop_does_not_pull_members(tmp_path):
    day = "2026-11-24"
    requests = []
    concept = "cn_concept:886001.TI"
    names = {BOARD: "行业", concept: "概念"}
    other = "000001.SZ"

    def vendor(request):
        requests.append(request)
        path = request.url.path
        if path == REVERSE:
            indexes = [dict(thscode=k.split(":")[1], name=v, type=k.split(":")[0]) for k, v in names.items()]
            rows = [dict(thscode=s, index_list=indexes if s == SYMBOL else [])
                    for s in request.url.params["thscode"].split(",")]
            return httpx.Response(200, json=dict(code=0, data=dict(item=rows)))
        if path == CATALOG:
            tag = request.url.params["tag"]
            rows = [dict(thscode=k.split(":")[1], name=v) for k, v in names.items() if k.startswith(tag+":")]
        else:
            rows = [dict(thscode=SYMBOL)]
        return httpx.Response(200, json=dict(code=0, data=dict(timestamp=int(time() * 1000), item=rows)))

    with NativeOwner(tmp_path / "state.sqlite3") as owner:
        focus = FocusOwner(owner, "test", Event(), transport=httpx.MockTransport(vendor))
        try:
            for _ in range(8):
                focus.schedule(day, [day], lambda: dict(stocks=[SYMBOL, other]))
            run = focus.run
            assert run
            calls = len(requests)
            assert calls == 5
            assert focus.edit("preview", 0, run, day, selection=[BOARD, concept])["added"] == 1
            prefs = dict(groups=[dict(id=str(uuid4()), name="自定义", boards=[concept])], aliases={BOARD: ["细查"]})
            assert focus.edit("save", 0, run, day, selection=[BOARD, concept, BOARD], preferences=prefs)["added"] == 1
            assert sorted(focus.lookup(dict(run=run, symbol=SYMBOL), day)["rows"]) == sorted(names)
            assert focus.edit("save", 1, run, day, selection=[BOARD, concept])["ok"]
            assert focus.edit("save", 1, "past-run", day, selection=[])["error"] == "SCOPE_CONFLICT"
            assert focus.edit("save", 1, run, day, selection=[concept])["retained"] == 1
            assert focus.edit("save", 0, run, day, selection=[])["error"] == "SCOPE_CONFLICT"
            assert focus.edit("save", 2, run, day, selection=[])["removed"] == 1
            assert focus.pool.read()["revision"] == 3
            assert not focus.pool.read()["members"]
            assert owner.conn.execute("SELECT count(*) FROM focus_scope_history_v1").fetchone()[0] == 3
            restored = FocusOwner(owner, "test", Event(), transport=httpx.MockTransport(vendor))
            try:
                assert restored.snapshot["groups"] == prefs["groups"]
                assert restored.snapshot["aliases"] == prefs["aliases"]
                assert restored.edit("save", 3, run, day, selection=[concept])["added"] == 1
            finally:
                restored.source.close()
            assert len(requests) == calls
        finally:
            focus.source.close()


def test_ths_reverse_optional_time_then_persisted_3002(tmp_path):
    responses = iter([{"code": 0, "data": {"item": []}}, {"code": 3002}])
    with NativeOwner(tmp_path / "state.sqlite3") as owner:
        source = THSTransport(owner, "test", Event(), 12, transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=next(responses))))
        try:
            source.request(REVERSE, {"thscode": "600000.SH,000001.SZ"})
            assert source.last_receipt["source_ms"] is None
            for _ in range(2):
                with pytest.raises(THSUnavailable, match="THS_CODE_3002"):
                    source.request(REVERSE, {"thscode": "600000.SH"})
            assert owner.meta("ths_retry_until") > time()
            rows = owner.conn.execute("SELECT capability,symbol_count FROM native15_request_receipt").fetchall()
            assert [tuple(row) for row in rows] == [("ths.reverse", 2), ("ths.reverse", 1)]
        finally:
            source.close()


def bar(at, source=SOURCE, close=10, volume=100):
    return combined([dict(open=close, high=close, low=close, close=close, volume=volume)], at, source, 1)


def test_intraday_contiguous_buckets_and_holiday_week_completion():
    sessions = ["2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24"]
    morning, weeks = frame_schedules(sessions, "2026-09-24 11:32:00")
    assert morning["15m"][-1] == stamp(sessions[-1], 690)
    assert morning["weekly"] == []
    schedules, weeks = frame_schedules(sessions, "2026-09-25 16:00:00")
    assert schedules["weekly"] == [stamp("2026-09-24", 900)]
    assert not planned_session("2026-09-25")
    opens = [at for at in schedules["15m"] if at.startswith("2026-09-24")]
    raw = [bar(at) for at in opens]
    for timeframe, count in [("30m", 8), ("60m", 4), ("120m", 2)]:
        out = frame_bars(raw, timeframe, sessions, weeks, opens[-1], [], opens[-1])
        assert len(out) == count and out[count // 2 - 1]["timestamp"] == opens[7]
        assert out[0]["source_count"] == 16 // count
    damaged = frame_bars(raw[:2] + raw[3:], "120m", sessions, weeks, opens[-1], [], opens[-1])
    assert [row["timestamp"] for row in damaged] == [opens[-1]]
    daily = [bar(stamp(day, 900), DAILY_SOURCE, close=20 if day >= sessions[1] else 10) for day in sessions]
    week = frame_bars(daily, "weekly", sessions, weeks, opens[-1],
                      [{"day": sessions[1], "ex_factor": .5}], opens[-1])[0]
    assert week["open"] == week["close"] == 20
    assert not frame_bars(daily[:2] + daily[3:], "weekly", sessions, weeks, opens[-1], [], opens[-1])


def test_fixed_origin_restart_gap_and_independent_periods(tmp_path):
    dates = [date(2026, 10, 12) + timedelta(days=i) for i in range(36)]
    sessions = [str(day) for day in dates if planned_session(str(day))]
    when, next_when = sessions[-1] + " 14:52:00", sessions[-1] + " 15:02:00"
    first_schedule, week = frame_schedules(sessions, when)
    next_schedule, next_weeks = frame_schedules(sessions, next_when)
    path = tmp_path / "state.sqlite3"
    with NativeOwner(path) as owner:
        owner.refresh_master([dict(symbol=SYMBOL, name="测试", eligible=True,
                                  source_type="etf", raw_tick=.001)], {"expected": 1}, sessions)
        market = owner.market
        with owner.conn:
            market._write(SYMBOL, [bar(at) for at in first_schedule["15m"]])
            market.factor_response(SYMBOL, [], when, sessions)
        observations = Observations(market, DailyStore(owner.conn))
        assert observations.refresh([SYMBOL], sessions, first_schedule, week, when, when)
        saved = observations.saved(SYMBOL)
        origin = saved["15m"]["replay_start"]
        assert origin == first_schedule["15m"][0]
        assert json.loads(saved["15m"]["payload"])["raw_tick"] == .001
        assert json.loads(saved["weekly"]["payload"])["status"] == "MISSING_COMPLETED_BAR"
    with NativeOwner(path) as owner:
        observations = Observations(owner.market, DailyStore(owner.conn))
        assert not observations.refresh([SYMBOL], sessions, first_schedule, week, when, when)
        with owner.conn:
            owner.market._write(SYMBOL, [bar(next_schedule["15m"][-1])])
            owner.conn.execute("DELETE FROM native15_bar WHERE symbol=? AND bar_end=?", (SYMBOL, origin))
        assert observations.refresh([SYMBOL], sessions, next_schedule, next_weeks, next_when, when)
        saved = observations.saved(SYMBOL)["15m"]
        assert saved["replay_start"] == origin
        assert json.loads(saved["payload"])["status"] == "GAP_IN_REPLAY_WINDOW"


def test_chart_diagnoses_current_gap_and_does_not_borrow_it_for_a_new_target(tmp_path):
    sessions = ["2026-11-25", "2026-11-26", "2026-11-27", "2026-11-30"]
    when = "2026-11-27 15:03:00"
    schedules, weeks = frame_schedules(sessions, when)
    target = schedules["15m"][-1]
    with NativeOwner(tmp_path / "diagnostic.sqlite3") as owner:
        owner.refresh_master([dict(symbol=SYMBOL, name="测试", eligible=True, source_type="stock", raw_tick=.01)],
                             {"expected": 1}, sessions)
        market = owner.market
        preceding = schedules["15m"][-2]
        with owner.conn:
            market._write(SYMBOL, [bar(preceding)])
            market.factor_response(SYMBOL, [], when, sessions, update_indicators=False)
        market.failed([SYMBOL], target, when, "INVALID_NATIVE15_OHLCV", stage="A", delay=3000)
        obs = Observations(market, DailyStore(owner.conn))
        obs.refresh([SYMBOL], sessions, schedules, weeks, when, when)
        detail = obs.chart(SYMBOL, "15m", sessions, schedules, weeks)["diagnostic"]
        assert (detail["reason"], detail["target"], detail["last_bar"]) == ("MISSING_COMPLETED_BAR", target, preceding)
        assert detail["missing_raw"][-1] == target
        assert (detail["queue"], detail["error"], detail["requested_at"]) == ("COOLING", "INVALID_NATIVE15_OHLCV", when)
        new, new_weeks = frame_schedules(sessions, "2026-11-30 10:33:00")
        detail = obs.chart(SYMBOL, "15m", sessions, new, new_weeks)["diagnostic"]
        assert detail["reason"] == "WAITING_INPUT" and detail["missing_raw"] is None
        assert detail["target"] == "2026-11-30 10:30:00"


def test_chart_receipt_keeps_target_and_diagnosis_before_instrument_metadata(tmp_path):
    sessions = ["2026-11-27"]
    schedules, weeks = frame_schedules(sessions, "2026-11-27 15:03:00")
    with NativeOwner(tmp_path / "metadata-pending.sqlite3") as owner:
        observations = Observations(owner.market, DailyStore(owner.conn))
        result = observations.chart(SYMBOL, "15m", sessions, schedules, weeks)
        assert result["expected_end"] == "2026-11-27 15:00:00"
        assert result["diagnostic"]["reason"] == "METADATA_PENDING"
        assert result["diagnostic"]["last_bar"] is None
        assert result["diagnostic"]["queue"] == "UNKNOWN"


def test_required_history_is_planned_from_each_completed_frame(tmp_path):
    from addons.lan_ma55.backend.multiframe_input import MIN_HISTORY, replay_input, source_window
    from addons.lan_ma55.backend.native15_scheduler import task

    dates = [date(2026, 7, 1) + timedelta(days=i) for i in range(140)]
    sessions = [str(day) for day in dates if planned_session(str(day))]
    now = sessions[-1] + " 15:02:00"
    schedules, weeks = frame_schedules(sessions, now)
    grid = schedules["15m"]
    for frame, length in (("15m", 150), ("30m", 300), ("60m", 600), ("120m", 1200)):
        required = source_window(frame, schedules[frame], grid, weeks, None, None)
        assert len(required) == length
    assert replay_input([bar(at) for at in grid[-MIN_HISTORY+1:]], grid, None)[1] == "INSUFFICIENT_HISTORY"
    assert replay_input([bar(at) for at in grid[-MIN_HISTORY:]], grid, None)[1] is None
    with NativeOwner(tmp_path / "needed.sqlite3") as owner:
        symbol = SYMBOL
        owner.refresh_master([dict(symbol=symbol, name="测试", eligible=True, source_type="stock", raw_tick=.01)],
                             {"expected": 1}, sessions)
        market = owner.market
        gap = grid[-100:-64]
        partial = {at: dict(bar(at), source_proof=PROOF) for at in grid[-1200:] if at not in gap}
        with owner.conn:
            market.factor_response(symbol, [], now, sessions, update_indicators=False)
        owner.accept_batch([symbol], {symbol: partial}, {}, sessions=sessions, start=grid[-1200], end=grid[-1],
                           now=now, factors={}, stage="A", live_after=now, analyze_candidates=False)
        obs = Observations(market, DailyStore(owner.conn))
        obs.refresh([symbol], sessions, schedules, weeks, now, now)
        assert obs.needs[symbol, "15m"] == [gap[0], gap[-1]]
        assert task(market, market.universe(), sessions, grid[-1], now, needs=obs.needs, grid=grid) == (
            gap[0], gap[-1], "B", [symbol], len(gap),
        )
        repaired = {at: dict(bar(at), source_proof=PROOF) for at in gap}
        owner.accept_batch([symbol], {symbol: repaired}, {}, sessions=sessions, start=gap[0], end=gap[-1],
                           now=now, factors={}, stage="B", live_after=now, analyze_candidates=False)
        obs.refresh([symbol], sessions, schedules, weeks, now, now, frames=("15m", "30m", "60m", "120m"))
        saved = {frame:json.loads(row["payload"]) for frame, row in obs.saved(symbol).items()}
        assert all(saved[frame]["status"] == "READY" for frame in ("15m", "30m", "60m", "120m"))
        assert saved["weekly"]["status"] != "READY" and obs.needs[symbol, "15m"] is None


def test_activity_windows_follow_session_and_independent_completed_targets():
    days = ["2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24", "2026-09-28"]
    for now, first in (
        ("09:44:00", []), ("09:47:00", ["15m"]), ("10:02:00", ["15m", "30m"]),
        ("10:32:00", ["15m", "30m", "60m"]), ("12:45:00", ["15m", "30m", "60m", "120m"]),
    ):
        schedules, _ = frame_schedules(days, f"{days[-1]} {now}")
        targets = display_targets(completed_targets(schedules), days[-1])
        assert [tf for tf, at in targets.items() if tf.endswith("m") and at] == first
        assert targets["daily"] == targets["weekly"] == "2026-09-24 15:00:00"
    on_weekend, _ = frame_schedules(days[:-1], "2026-09-27 16:00:00")
    assert display_targets(completed_targets(on_weekend), days[-2])["15m"] == "2026-09-24 15:00:00"


def test_project_current_replay_events_and_keep_revised_evidence(tmp_path):
    from addons.lan_ma55.backend.market_store import encoded

    period, day = "15m", "2026-11-27"
    t0 = "2026-11-26 15:00:00"
    t1, t2, t3 = [f"{day} {stamp}" for stamp in ("09:45:00", "10:00:00", "10:15:00")]

    def event(at, price):
        return dict(event_id=f"{VERSION}:{SYMBOL}:{period}:{at.replace(' ', 'T')}", symbol=SYMBOL,
                    timeframe=period, timestamp=at, close=price)

    def result(at, **values):
        return dict(status="READY", expected_end=at, source_revision=1,
                    result=dict(timeframe=period, rule_version=VERSION, latest=dict(timestamp=at)), **values)

    old, first, second = event(t0, 9), event(t1, 10), event(t2, 11)
    with NativeOwner(tmp_path / "state.sqlite3") as owner:
        obs = Observations(owner.market, DailyStore(owner.conn))
        prior = result(t1, latest_candidate=dict(at=t1, id=first["event_id"], mode="REPLAY"))
        assert confirmations(prior)["events"][0]["id"] == first["event_id"]
        with owner.conn:
            latest, confirmed = obs.save_events([old, first, second], prior, confirmations(prior),
                                                period, t2, f"{day} 10:04:00", t1, True, "QFQ", 1)
            frame = result(t2, latest_candidate=latest, confirmations=confirmed)
            owner.conn.execute("INSERT INTO ma55_observation_v1 VALUES(?,?,?,?,?,?)",
                               (VERSION, SYMBOL, period, t0, "key", encoded(frame)))
        assert [row["mode"] for row in confirmed["events"]] == ["REPLAY", "DELAYED"]
        assert [row["price"] for row in confirmed["events"]] == [10, 11]
        assert activation(dict(frame, status="WAITING_FOR_COMPLETED_TARGET"), t3, period)["events"] == confirmed["events"]
        assert activation(frame, None, period)["state"] == "PENDING"
        assert activation(frame, t3, "daily")["events"] == []
        sessions = [t0[:10], day]
        schedules, _ = frame_schedules(sessions, f"{day} 10:17:00")
        snapshot = {"members": {SYMBOL: ["a", "b"]}}
        master = {SYMBOL: {"eligible": True, "basis_revision": 1}}
        rows = obs.rows(snapshot, master, schedules, day)
        assert rows[0]["frames"][period]["activation"]["state"] == "PENDING"
        assert observation_summary(rows, completed_targets(schedules), day)["activation_symbols"] == 1
        master[SYMBOL]["basis_revision"] = 2
        changed = obs.rows(snapshot, master, schedules, day)[0]["frames"][period]["activation"]
        assert changed == dict(state="PENDING", through=None, events=[], invalidated=0, review_pending=True)
        with owner.conn:
            _, confirmed = obs.save_events([old, first], frame, confirmed, period, t3, t3, t1, False, "QFQ", 2)
        state = activation(result(t3, confirmations=confirmed), t3, period)
        assert state["state"] == "ACTIVATED" and [item["at"] for item in state["events"]] == [t1]
        assert state["invalidated"] == 1
        assert owner.conn.execute("SELECT count(*) FROM ma55_observation_event_v1").fetchone()[0] == 3
        assert activation(result(t3, confirmations=confirmed), "2026-11-30 09:45:00", period)["events"] == []


def test_chart_120m_uses_482_completed_buckets_without_displaying_warmup(tmp_path):
    dates = [date(2025, 1, 6) + timedelta(days=i) for i in range(400)]
    sessions = [str(day) for day in dates if day.weekday() < 5]
    now = sessions[-1] + " 15:02:00"
    schedules, weeks = frame_schedules(sessions, now)
    grid = schedules["120m"][-482:]
    native = schedules["15m"][-482 * 8:]
    path = tmp_path / "chart.sqlite3"
    with NativeOwner(path) as owner:
        owner.refresh_master([dict(symbol=SYMBOL, name="测试", eligible=True,
                                  source_type="stock", raw_tick=.01)], {"expected": 1}, sessions)
        with owner.conn:
            owner.market.factor_response(SYMBOL, [], now, sessions, update_indicators=False)
            owner.market._write(SYMBOL, [bar(at, close=i // 8 + 1) for i, at in enumerate(native)])
        obs = Observations(owner.market, DailyStore(owner.conn))
        result = obs.chart(SYMBOL, "120m", sessions, schedules, weeks)
        assert result["grid"] == grid[-250:]
        assert len(result["bars"]) == 250
        first = result["bars"][0]
        assert first["timestamp"] == grid[232] and first["source_count"] == 8
        assert [first[f"ma{n}"] for n in MAS] == [233 - (n - 1) / 2 for n in MAS]
        missing = native[240 * 8]
        with owner.conn:
            owner.conn.execute("DELETE FROM native15_bar WHERE symbol=? AND bar_end=?", (SYMBOL, missing))
        result = obs.chart(SYMBOL, "120m", sessions, schedules, weeks)
        after = [bar for bar in result["bars"] if bar["timestamp"] >= grid[240]]
        assert len(result["bars"]) == 249 and after[0]["timestamp"] == grid[241]
        assert all(after[0][f"ma{n}"] is None for n in MAS)
        assert after[231]["ma233"] is None and after[232]["ma233"] == 358


def test_chart_zero_volume_or_unverified_adjustment_breaks_same_period_sma(tmp_path):
    sessions = ["2026-11-26", "2026-11-27"]
    now = sessions[-1] + " 15:02:00"
    schedules, weeks = frame_schedules(sessions, now)
    grid = schedules["15m"]
    with NativeOwner(tmp_path / "state.sqlite3") as owner:
        instrument = dict(symbol=SYMBOL, name="测试", eligible=True, source_type="stock", raw_tick=.01)
        owner.refresh_master([instrument], {"expected": 1}, sessions)
        with owner.conn:
            owner.market._write(SYMBOL, [bar(at, close=i + 1, volume=0 if i == 20 else 100) for i, at in enumerate(grid)])
        obs = Observations(owner.market, DailyStore(owner.conn))
        unverified = obs.chart(SYMBOL, "15m", sessions, schedules, weeks)["bars"]
        assert all(row["ma5"] is None for row in unverified)
        with owner.conn:
            owner.market.factor_response(SYMBOL, [], now, sessions, update_indicators=False)
        bars = obs.chart(SYMBOL, "15m", sessions, schedules, weeks)["bars"]
        assert bars[19]["ma5"] == 18
        assert all(row["ma5"] is None for row in bars[20:25])
        assert bars[25]["ma5"] == 24 and bars[-1]["ma233"] is None
        old_native = owner.market.read(SYMBOL, "15m", grid[-1], chart=True)["bars"]
        assert [row["ma5"] for row in old_native] == [row["ma5"] for row in bars]


def test_weekly_ma233_waits_for_a_real_233rd_completed_week(tmp_path):
    days = [date(2021, 1, 4) + timedelta(days=i) for i in range(233 * 7)]
    sessions = [str(day) for day in days if day.weekday() < 5]
    thursday, friday = (day + " 15:02:00" for day in sessions[-2:])
    first, first_weeks = frame_schedules(sessions, thursday)
    latest, weeks = frame_schedules(sessions, friday)
    assert len(first_weeks) == 232 and len(weeks) == 233
    with NativeOwner(tmp_path / "weekly.sqlite3") as owner:
        info = dict(symbol=SYMBOL, name="测试", eligible=True, source_type="stock", raw_tick=.01)
        owner.refresh_master([info], {"expected": 1}, sessions)
        obs = Observations(owner.market, DailyStore(owner.conn))
        with owner.conn:
            owner.market.factor_response(SYMBOL, [], friday, sessions, update_indicators=False)
            data = [(SYMBOL, stamp(day, 900), price, price, price, price, 100)
                    for index, day in enumerate(sessions) for price in (index // 5 + 1,)]
            owner.conn.executemany("INSERT INTO ma55_daily_bar_v1(symbol,bar_end,open,high,low,close,volume) "
                                   "VALUES(?,?,?,?,?,?,?)", data)
        previous = obs.chart(SYMBOL, "weekly", sessions, first, first_weeks)
        final = obs.chart(SYMBOL, "weekly", sessions, latest, weeks)
        assert previous["asof"] == stamp(sessions[-6], 900) and previous["bars"][-1]["ma233"] is None
        assert final["asof"] == stamp(sessions[-1], 900) and final["bars"][-1]["ma233"] == 117
