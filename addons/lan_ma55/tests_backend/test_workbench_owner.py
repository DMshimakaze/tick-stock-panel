import json
from datetime import date, timedelta
from threading import Event
from time import time

import httpx
import pytest

from addons.lan_ma55.backend.daily_store import DailyStore
from addons.lan_ma55.backend.exchange_sessions import frame_schedules, planned_session
from addons.lan_ma55.backend.focus_owner import FocusOwner, THSTransport
from addons.lan_ma55.backend.market_grid import combined, stamp
from addons.lan_ma55.backend.multiframe_input import DAILY_SOURCE, frame_bars
from addons.lan_ma55.backend.native15_owner import NativeOwner
from addons.lan_ma55.backend.native15_shadow import SOURCE
from addons.lan_ma55.backend.observations import Observations
from addons.lan_ma55.backend.ths_focus import CATALOG, MEMBERS, THSUnavailable

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

    def vendor(request):
        requests.append(request)
        item = {"thscode": SYMBOL}
        return httpx.Response(200, json=dict(code=0, data=dict(timestamp=int(time() * 1000), item=[item])))

    with NativeOwner(tmp_path / "state.sqlite3") as owner:
        focus = FocusOwner(owner, "test", Event(), transport=httpx.MockTransport(vendor))
        focus.catalog = dict(checked_day=day, entries={BOARD: "板块"})
        try:
            assert focus.edit("save", [BOARD, BOARD], 0, day)["added"] == [SYMBOL]
            assert focus.edit("save", [BOARD], 1, day)["ok"]
            assert focus.edit("save", [], 0, day)["error"] == "SCOPE_CONFLICT"
            assert focus.edit("save", [], 1, day)["removed"] == [SYMBOL]
            assert focus.pool.read()["revision"] == 2
            assert not focus.pool.read()["members"]
            assert len(requests) == 1
            assert owner.conn.execute("SELECT count(*) FROM focus_scope_history_v1").fetchone()[0] == 2
        finally:
            focus.source.close()


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
