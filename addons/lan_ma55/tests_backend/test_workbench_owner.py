import json
from datetime import date, timedelta
from threading import Event
from time import time
from uuid import uuid4

import httpx
import pytest

from addons.lan_ma55.backend.daily_store import DailyStore
from addons.lan_ma55.backend.exchange_sessions import frame_schedules, planned_session
from addons.lan_ma55.backend.focus_owner import FocusOwner, THSTransport
from addons.lan_ma55.backend.market_grid import combined, stamp
from addons.lan_ma55.backend.multiframe_input import DAILY_SOURCE, frame_bars
from addons.lan_ma55.backend.native15_owner import NativeOwner
from addons.lan_ma55.backend.native15_shadow import PROOF, SOURCE
from addons.lan_ma55.backend.observations import Observations
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
