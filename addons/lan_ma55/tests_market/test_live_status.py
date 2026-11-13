from types import SimpleNamespace

import httpx
import pytest

from addons.lan_ma55.backend.market_grid import as_ms, latest_point
from addons.lan_ma55.backend.request_budget import RequestDeferred
from addons.lan_ma55.backend.runtime import Worker
from addons.lan_ma55.backend.unified_store import UnifiedStore
from .test_collector import source
from .test_rolling import sample
from addons.lan_ma55.backend.runtime_status import business_status


def test_daily_bar_can_lag_while_completed_five_confirms_today(tmp_path):
    calls = []

    def reply(request):
        calls.append(request)
        if request.url.params["period"] == "1d":
            return httpx.Response(
                200, json={"data": {"timestamp": [as_ms("2026-11-12 00:00:00")]}}
            )
        at = "2026-11-13 10:45:00"
        bar = dict(
            timestamp=[as_ms(at)],
            open=[10],
            high=[11],
            low=[9],
            close=[10],
            volume=[200],
            amount=[200000],
        )
        return httpx.Response(200, json={"data": {"000001.SH": bar}})

    c = source(tmp_path, reply)
    try:
        sessions = c.calendar()
        assert sessions == ["2026-11-12"]
        proof = c.session_evidence("2026-11-13 10:52:00")
        assert proof["confirmed_day"] == "2026-11-13"
        assert proof["source_asof"] == "2026-11-13 10:45:00"
        assert (
            latest_point([*sessions, proof["confirmed_day"]], "2026-11-13 10:52:00")
            == "2026-11-13 10:45:00"
        )
        assert c.metrics["minute_requests"] == 1
        assert len(calls) == 2
        assert all(r.url.params["period"] != "1m" for r in calls)
    finally:
        c.close()


@pytest.mark.parametrize("now", ["2026-11-13 09:20:00", "2026-11-14 10:52:00"])
def test_no_probe_before_completed_five_or_on_weekend(tmp_path, now):
    c = source(tmp_path, lambda request: pytest.fail("No vendor request is due"))
    try:
        assert c.session_evidence(now)["confirmed_day"] is None
        assert c.metrics["http_requests"] == 0
    finally:
        c.close()


def test_unknown_or_verified_holiday_cannot_create_a_session(tmp_path):
    empty = dict(timestamp=[], open=[], high=[], low=[], close=[], volume=[])
    c = source(
        tmp_path,
        lambda request: httpx.Response(200, json={"data": {"000001.SH": empty}}),
    )
    try:
        assert c.session_evidence("2026-10-01 10:52:00")["confirmed_day"] is None
        assert (
            latest_point(["2026-09-30"], "2026-10-01 10:52:00") == "2026-09-30 15:00:00"
        )
    finally:
        c.close()


def worker_at(tmp_path, reply):
    worker = Worker(
        SimpleNamespace(data_dir=tmp_path), {"api_base": "http://localhost"}
    )
    worker.http.close()
    worker.http = httpx.Client(
        transport=httpx.MockTransport(reply), base_url="http://localhost"
    )
    return worker


@pytest.mark.parametrize("status", [401, 403, 404, 429, 500])
def test_host_error_is_not_reported_as_a_running_job(tmp_path, status):
    calls = []

    def reply(request):
        calls.append(request)
        return httpx.Response(status)

    worker = worker_at(tmp_path, reply)
    try:
        for _ in range(2):
            with pytest.raises(RequestDeferred) as caught:
                worker.host_busy()
            assert caught.value.reason == f"HOST_HTTP_{status}"
        assert worker.state["host_job_status"]["active_id"] is None
        assert len(calls) == 1  # Failed endpoint checks cool down independently.
    finally:
        worker.http.close()
        worker.publisher.close()


@pytest.mark.parametrize(
    "body,busy", [({"active_id": "real-job"}, True), ({"active_id": None}, False)]
)
def test_only_real_host_active_id_yields_busy(tmp_path, body, busy):
    worker = worker_at(tmp_path, lambda request: httpx.Response(200, json=body))
    try:
        assert worker.host_busy() is busy
        assert worker.state["host_job_status"]["active_id"] == body["active_id"]
    finally:
        worker.http.close()
        worker.publisher.close()


def test_calendar_recheck_is_independent_from_metadata_day_and_bounded(tmp_path):
    worker = worker_at(
        tmp_path, lambda request: httpx.Response(200, json={"active_id": None})
    )
    worker.owner = UnifiedStore(tmp_path / "user_data/lan_ma55/state.sqlite3")
    worker.store = worker.owner.market
    calls = []
    worker.collector = SimpleNamespace(
        calendar=lambda: calls.append("daily") or ["2026-11-12"],
        session_evidence=lambda now: (
            calls.append("five") or {"confirmed_day": None, "source_asof": None}
        ),
    )
    try:
        with worker.owner.conn:
            worker.store.set_sessions(["2026-11-12"])
            worker.store.set_meta("metadata_day", "2026-11-13")
        worker.refresh_calendar("2026-11-13 10:52:00")
        worker.refresh_calendar("2026-11-13 10:52:30")
        assert calls == ["daily", "five"]
        assert worker.state["calendar"]["pending_day"] == "2026-11-13"
        worker.collector.session_evidence = lambda now: (
            calls.append("five")
            or {"confirmed_day": "2026-11-13", "source_asof": "2026-11-13 10:55:00"}
        )
        worker.refresh_calendar("2026-11-13 10:57:01")
        assert calls == ["daily", "five", "five"]
        assert worker.store.meta("sessions") == ["2026-11-12", "2026-11-13"]
        assert worker.owner.meta("sessions") == worker.store.meta("sessions")
        assert worker.state["calendar"]["pending_day"] is None
    finally:
        worker.owner.close()
        worker.http.close()
        worker.publisher.close()


@pytest.mark.parametrize(
    "failure,reason",
    [
        (lambda r: httpx.Response(200, content="invalid"), "HOST_INVALID_JSON"),
        (lambda r: httpx.Response(200, json={}), "HOST_INVALID_JSON"),
        (
            lambda r: (_ for _ in ()).throw(httpx.ReadTimeout("synthetic")),
            "HOST_TIMEOUT",
        ),
        (
            lambda r: (_ for _ in ()).throw(httpx.ConnectError("synthetic")),
            "HOST_CONNECTION_ERROR",
        ),
    ],
)
def test_host_network_and_json_failures_remain_visible(tmp_path, failure, reason):
    worker = worker_at(tmp_path, failure)
    try:
        with pytest.raises(RequestDeferred) as exc:
            worker.host_busy()
        assert exc.value.reason == reason
        assert worker.state["host_job_status"]["state"] == "UNAVAILABLE"
        assert worker.state["host_job_status"]["retry_seconds"] == 30
        worker.http.close()
        worker.http = httpx.Client(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(200, json={"active_id": None})
            ),
            base_url="http://localhost",
        )
        worker.host_retry_at = 0
        assert worker.host_busy() is False
        assert worker.state["host_job_status"]["state"] == "IDLE"
    finally:
        worker.http.close()
        worker.publisher.close()


def test_market_arrival_is_not_a_blocked_analysis_and_scope_is_real(tmp_path):
    sessions, rows = sample(60)
    target = max(rows)
    with UnifiedStore(tmp_path / "state.sqlite3") as owner:
        master = [
            dict(symbol=s, name=s, source_type=asset, eligible=True, raw_tick=tick)
            for s, asset, tick in [
                ("600000.SH", "stock", 0.01),
                ("159231.SZ", "etf", 0.001),
            ]
        ]
        owner.refresh_master(master, {"expected": 2}, sessions)
        operations = []
        for symbol, events in [("600000.SH", []), ("159231.SZ", None)]:
            owner.accept_market(
                symbol,
                rows,
                sessions=sessions,
                start=min(rows),
                end=target,
                now=target,
                events=events,
                factor_response=True,
                live_after=target,
                operation=operations.append,
            )
        assert operations == ["AGGREGATING_15M", "CALCULATING_MA55", "IDLE"] * 2
        snapshot = owner.status_snapshot(target=target)
        assert snapshot["refresh"]["refreshed"] == 2
        assert snapshot["analysis"]["calculated"] == 1
        assert snapshot["analysis"]["expected"] == 2
        assert snapshot["asset_counts"] == {"stock": 1, "etf": 1}
        limited = owner.status_snapshot(target=target, scope=["159231.SZ"])
        assert limited["asset_counts"] == {"etf": 1}
        assert limited["refresh"]["refreshed"] == limited["refresh"]["expected"] == 1
        assert limited["analysis"]["calculated"] == 0
        assert (
            owner.status_snapshot(target=target, scope=[])["refresh"]["expected"] == 0
        )
        # Generation expansion must not pass old-cycle attempts as new analysis.
        owner.refresh_master(
            [
                *master,
                dict(
                    symbol="600001.SH",
                    name="new",
                    source_type="stock",
                    eligible=True,
                    raw_tick=0.01,
                ),
            ],
            {"expected": 3},
            sessions,
        )
        assert owner.status_snapshot(target=target)["analysis"]["calculated"] == 0
        assert "600000.SH" in owner.catch_up(target, target, target)
        with owner.conn:
            owner.analyze(["600000.SH"], sessions, target, target, target)
        assert owner.status_snapshot(target=target)["analysis"]["calculated"] == 1


@pytest.mark.parametrize(
    "now,expected",
    [
        ("2026-11-13 09:20:00", "2026-11-12 15:00:00"),
        ("2026-11-13 10:52:00", "2026-11-13 10:45:00"),
        ("2026-11-13 12:15:00", "2026-11-13 11:30:00"),
        ("2026-11-13 16:00:00", "2026-11-13 15:00:00"),
        ("2026-11-14 10:52:00", "2026-11-13 15:00:00"),
    ],
)
def test_verified_session_target_boundaries(now, expected):
    assert latest_point(["2026-11-12", "2026-11-13"], now) == expected


def test_worker_native15_to_candidates_to_separate_official_projection(
    tmp_path, monkeypatch
):
    import json
    from datetime import datetime
    from threading import Event
    from addons.lan_ma55.backend import runtime, market_store
    from addons.lan_ma55.backend.market_grid import points
    from addons.lan_ma55.backend.result_publisher import Publisher

    now, target = "2026-11-13 10:52:00", "2026-11-13 10:45:00"
    monkeypatch.setattr(runtime, "now_cn", lambda: datetime.fromisoformat(now))
    monkeypatch.setattr(market_store, "now_cn", lambda: datetime.fromisoformat(now))
    sessions = ["2026-11-09", "2026-11-10", "2026-11-11", "2026-11-12"]
    five = {
        t: dict(open=10, high=10.1, low=9.9, close=10, volume=10000, amount=100000)
        for t in points(sessions, sessions[0], sessions[-1] + " 15:00:00")
    }
    path = tmp_path / "user_data/lan_ma55/state.sqlite3"
    with UnifiedStore(path) as owner:
        owner.refresh_master(
            [
                dict(
                    symbol="600000.SH",
                    name="synthetic",
                    source_type="stock",
                    eligible=True,
                    raw_tick=0.01,
                )
            ],
            {"expected": 1},
            sessions,
        )
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
        with owner.conn:
            owner.market.set_meta("metadata_day", "2026-11-13")
            owner.set_meta("pending_publish", None)
    operations, requests, published, done = [], [], {}, Event()
    worker = worker_at(
        tmp_path, lambda r: httpx.Response(200, json={"active_id": None})
    )
    worker.settings.update(
        tickflow_api_key="synthetic", collection_symbols=["600000.SH"]
    )
    original_report = worker.report

    def report(**kw):
        if "active_operation" in kw:
            operations.append(kw["active_operation"])
        original_report(**kw)

    worker.report = report

    def vendor(request):
        requests.append(request)
        period = request.url.params.get("period")
        if period == "1d":
            return httpx.Response(
                200,
                json={
                    "data": {"timestamp": [as_ms(d + " 00:00:00") for d in sessions]}
                },
            )
        if "ex-factors" in request.url.path:
            return httpx.Response(200, json={"data": {"600000.SH": []}})
        assert period == "15m"
        codes = (
            json.loads(request.content)["symbols"]
            if request.content
            else request.url.params["symbols"].split(",")
        )
        from addons.lan_ma55.backend.market_grid import BEIJING
        start = str(datetime.fromtimestamp(int(request.url.params["start_time"])/1000, BEIJING).replace(tzinfo=None))
        stamps = points([*sessions, "2026-11-13"], start, target, step=15)
        n = len(stamps)
        bars = dict(
            timestamp=[as_ms(t) for t in stamps],
            open=[10] * n,
            high=[10.1] * n,
            low=[9.9] * n,
            close=[10] * n,
            volume=[100] * n,
            amount=[100000] * n,
        )
        return httpx.Response(200, json={"data": {s: bars for s in codes}})

    collector = source(tmp_path, vendor, report=report, host_busy=worker.host_busy)
    monkeypatch.setattr(runtime, "Collector", lambda *args, **kwargs: collector)
    worker.publisher.close()

    def official(request):
        assert request.method == "POST" and request.url.path.startswith(
            "/api/ext-data/lan_ma55_"
        )
        table = request.url.path.split("/")[-2]
        published[table] = json.loads(request.content)["rows"]
        if table == "lan_ma55_native15_summary_v1":
            state = json.loads(published[table][0]["payload_json"])
            if (
                state.get("latest_saved_15m") == target
                and state.get("analysis", {}).get("calculated") == 1
            ):
                worker.stop.set()
                done.set()
        return httpx.Response(200, json={"ok": True})

    worker.publisher = Publisher(
        "http://localhost", transport=httpx.MockTransport(official), native=True
    )
    worker.start()
    arrived = done.wait(6)
    worker.close()
    assert arrived, {
        k: worker.state.get(k)
        for k in ("phase", "error", "publisher_error", "target_15m")
    }
    assert not worker.thread.is_alive()
    assert all(
        op in operations
        for op in [
            "COLLECTING_NATIVE15",
            "CALCULATING_NATIVE15_CANDIDATES",
            "PUBLISHING",
            "IDLE",
        ]
    )
    assert operations[-1] == "IDLE"
    assert all(r.url.params.get("period") not in ("1m", "5m") for r in requests)
    state = json.loads(published["lan_ma55_native15_summary_v1"][0]["payload_json"])
    assert state["today_session_verified"] is True
    assert state["data_target_15m"] == state["latest_analyzed_15m"] == target
    assert state["analysis"]["calculated"] == state["refresh"]["refreshed"] == 1
    assert published["lan_ma55_native15_day_v1"][0]["market_date"] == "2026-11-13"
    from addons.lan_ma55.backend.native15_owner import NativeOwner
    with NativeOwner(path) as owner:
        assert owner.market.raw_rows("600000.SH")[-1]["source_count"] == 1
        assert (
            owner.market.meta("calendar_check")["verification_source"]
            == "TICKFLOW_COMPLETED_INDEX_NATIVE15"
        )
        assert (
            owner.conn.execute(
                "SELECT analysis_asof FROM native15_signal_state"
            ).fetchone()[0]
            == target
        )


def test_day_publish_failure_is_visible_then_recovers_without_recalculation(tmp_path):
    from addons.lan_ma55.backend.result_publisher import Publisher
    import json

    sessions, rows = sample(60)
    target = max(rows)
    worker = worker_at(
        tmp_path, lambda r: httpx.Response(200, json={"active_id": None})
    )
    worker.owner = UnifiedStore(tmp_path / "user_data/lan_ma55/state.sqlite3")
    worker.store = worker.owner.market
    worker.owner.refresh_master(
        [
            dict(
                symbol="600000.SH",
                name="synthetic",
                source_type="stock",
                eligible=True,
                raw_tick=0.01,
            )
        ],
        {"expected": 1},
        sessions,
    )
    worker.owner.accept_market(
        "600000.SH",
        rows,
        sessions=sessions,
        start=min(rows),
        end=target,
        now=target,
        events=[],
        factor_response=True,
        live_after=target,
    )
    worker.publisher.close()
    fail, summaries = True, []

    def official(request):
        if "/lan_ma55_day/" in request.url.path and fail:
            return httpx.Response(500)
        if "/lan_ma55_summary/" in request.url.path:
            summaries.append(
                json.loads(json.loads(request.content)["rows"][0]["payload_json"])
            )
        return httpx.Response(200, json={})

    worker.publisher = Publisher(
        "http://localhost", transport=httpx.MockTransport(official)
    )
    try:
        revision = worker.owner.meta("revision")
        worker.publish(target, force=True)
        assert worker.owner.meta("pending_publish") is not None
        assert summaries[-1]["publisher_error"]
        assert summaries[-1]["pending_publish"] is True
        assert worker.state["active_operation"] == "IDLE"
        fail = False
        worker.publish(target, force=True)
        assert worker.owner.meta("pending_publish") is None
        assert worker.owner.meta("revision") == revision
        assert summaries[-1]["publisher_error"] is None
        assert worker.state["last_publish_at"]
    finally:
        worker.owner.close()
        worker.http.close()
        worker.publisher.close()


@pytest.mark.parametrize('phase,reason,operation', [
    ('SOURCE_UNAVAILABLE','SOURCE_UNAVAILABLE','BLOCKED'),
    ('RATE_LIMITED','RATE_LIMITED','BLOCKED'),
    ('CREDENTIAL_REQUIRED','CREDENTIAL_REQUIRED','BLOCKED'),
    ('QUOTA_WAIT','QUOTA_WAIT','IDLE'),
    ('HOST_BUSY','HOST_BUSY','IDLE'),
])
def test_blocking_and_waits_do_not_leave_a_stale_computation_phase(phase, reason, operation):
    snapshot=dict(refresh=dict(target_15m='2026-11-13 10:45:00',refreshed=2,expected=3),
                  analysis=dict(calculated=1,expected=3),asset_counts={'stock':2,'etf':1},
                  universe={'expected':3},source_counts={'TICKFLOW_RAW_5M':3},history_queue_count=12)
    state=dict(phase=phase,active_operation='IDLE',calendar={'confirmed_day':'2026-11-13'})
    result=business_status(state,snapshot,'2026-11-13 10:52:00')
    assert result['blocking_reason']==reason
    assert result['active_operation']==operation
    assert result['sync_mode']=='LIVE_CATCHUP'
    assert not result['all_current']


def test_empty_metadata_stays_unknown_and_complete_scope_can_wait_with_history():
    snapshot=dict(refresh=dict(target_15m='2026-11-13 10:45:00',refreshed=0,expected=0))
    state=dict(phase='WAITING_NEXT_15M',active_operation='IDLE',calendar={'confirmed_day':'2026-11-13'})
    empty=business_status(state,snapshot,'2026-11-13 10:52:00')
    assert empty['monitored_stock_count'] is None and empty['monitored_etf_count'] is None
    assert not empty['monitoring_verified']
    complete=dict(refresh=dict(target_15m='2026-11-13 10:45:00',refreshed=2,expected=2),
                  analysis=dict(calculated=2,expected=2),universe={'expected':2},source_counts={'TICKFLOW_RAW_5M':2},
                  asset_counts={'stock':1,'etf':1},history_queue_count=2)
    result=business_status(state,complete,'2026-11-13 10:52:00')
    assert result['sync_mode']=='WAIT_NEXT_BAR' and result['all_current']
    assert result['monitored_stock_count']==result['monitored_etf_count']==1


def test_history_budget_wait_does_not_masquerade_as_live_catchup_or_hide_source_gaps():
    snapshot=dict(refresh=dict(target_15m='2026-11-13 10:45:00',refreshed=1,expected=2),
                  analysis=dict(calculated=1,expected=2),universe={'expected':2},
                  source_counts={'TICKFLOW_RAW_5M':2},asset_counts={'stock':1,'etf':1})
    state=dict(phase='QUOTA_WAIT',work_stage='HISTORY',active_operation='IDLE',queue_size=0,
               calendar={'confirmed_day':'2026-11-13'})
    result=business_status(state,snapshot,'2026-11-13 10:52:00')
    assert result['sync_mode']=='HISTORY_BACKFILL'
    assert result['blocking_reason']=='SOURCE_LAG'
    assert not result['all_current']


def test_native_market_ready_does_not_hide_independent_missing_factors():
    snapshot=dict(refresh=dict(target_15m='2026-11-13 10:45:00',refreshed=2,expected=2),
                  analysis=dict(calculated=1,expected=2),universe={'expected':2},
                  states={'READY':2},factor_pending=1,source_counts={'TICKFLOW_NATIVE_15M':2})
    state=dict(phase='RUNNING',work_stage='C',source_id='TICKFLOW_NATIVE_15M',active_operation='IDLE',queue_size=0,
               calendar={'confirmed_day':'2026-11-13'})
    result=business_status(state,snapshot,'2026-11-13 10:52:00')
    assert result['blocking_reason']=='FACTOR_PENDING'
    assert not result['all_current']


@pytest.mark.parametrize('factor_requests',[0,6])
def test_restart_restores_actual_total_and_cold_http_budgets(tmp_path,factor_requests):
    from addons.lan_ma55.backend.native15_owner import NativeOwner
    from addons.lan_ma55.backend.market_grid import now_cn
    now=str(now_cn())
    path=tmp_path/'state.sqlite3'
    with NativeOwner(path) as owner:
        with owner.conn:
            for stage,period,capability,n in [('C','15m','kline.minute.batch',2),('F',None,'adj_factor',factor_requests)]:
                owner.save_requests([dict(at=now,endpoint='/synthetic',capability=capability,status=200,
                                         latency_ms=1,symbol_count=100,retries=0,period=period) for _ in range(n)],stage)
    worker=worker_at(tmp_path,lambda r:httpx.Response(200,json={'active_id':None}))
    c=source(tmp_path,lambda r:pytest.fail('Restart must retain the active quota window'),cold_budget=worker.cold_budget)
    try:
        with NativeOwner(path) as owner:
            worker.owner,worker.collector=owner,c
            worker.restore_budgets(now)
            assert len(c.budgets['minute'].sent)==2+factor_requests
            assert worker.cold_budget.delay()>58
            with pytest.raises(RequestDeferred):
                c.fetch(['600000.SH'],'2026-11-11 09:45:00','2026-11-11 09:45:00',cold=True)
            if factor_requests:
                with pytest.raises(RequestDeferred):
                    c.factors(['600000.SH'])
            assert c.metrics['http_requests']==0
    finally:
        c.close()
        worker.http.close()
        worker.publisher.close()


def test_restart_preserves_supplier_retry_after_for_the_shared_pool(tmp_path):
    from time import time
    from addons.lan_ma55.backend.native15_owner import NativeOwner
    from addons.lan_ma55.backend.market_grid import now_cn
    path=tmp_path/'state.sqlite3'
    with NativeOwner(path) as owner:
        with owner.conn:
            owner.set_meta('quota_until',time()+120)
    worker=worker_at(tmp_path,lambda r:httpx.Response(200,json={'active_id':None}))
    c=source(tmp_path,lambda r:pytest.fail('Retry-After is still active'))
    try:
        with NativeOwner(path) as owner:
            worker.owner,worker.collector=owner,c
            worker.restore_budgets(str(now_cn()))
            assert c.budgets['minute'].delay()>118
            with pytest.raises(RequestDeferred):
                c.factors(['600000.SH'])
            assert c.metrics['http_requests']==0
    finally:
        c.close()
        worker.http.close()
        worker.publisher.close()
