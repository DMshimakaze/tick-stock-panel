from threading import Event

import httpx
import pytest
from quantdash import QuantDash

from addons.lan_ma55.backend.collector import Collector
from addons.lan_ma55.backend.market import as_ms
from addons.lan_ma55.backend.request_budget import RequestDeferred, RollingBudget, retry_seconds
from addons.lan_ma55.backend.result_publisher import Publisher


def collector(tmp_path, response):
    qd = QuantDash(api_key="synthetic-test-key", base_url="https://synthetic.invalid", max_retries=0)
    qd._client._client.close()
    qd._client._client = httpx.Client(transport=httpx.MockTransport(response), base_url="https://synthetic.invalid")
    return Collector("synthetic-test-key", tmp_path, qd=qd)


def test_actual_sdk_http_hook_and_raw_completed_compact_shape(tmp_path):
    start, end = "2026-11-06 09:35:00", "2026-11-06 09:45:00"
    def response(request):
        assert request.url.params["period"] == "5m"
        assert request.url.params["adjust"] == "none"
        assert request.url.params["count"] == "5000"
        return httpx.Response(200, json={"data": {"600000.SH": dict(
            timestamp=[as_ms("2026-11-06 " + t) for t in ("09:35:00", "09:40:00", "09:45:00")],
            open=[100] * 3, high=[102] * 3, low=[99] * 3, close=[101] * 3, volume=[100] * 3)}})
    source = collector(tmp_path, response)
    try:
        data = source.fetch(["600000.SH"], start, end)
        assert list(data["600000.SH"]) == ["2026-11-06 " + t for t in ("09:35:00", "09:40:00", "09:45:00")]
        assert source.metrics["http_requests"] == source.metrics["minute_requests"] == source.responses == 1
        assert source.budgets["minute"].limit == 24
    finally:
        source.close()


def test_429_counts_real_failed_http_and_structured_cooldown(tmp_path):
    source = collector(tmp_path, lambda r: httpx.Response(429, headers={"Retry-After": "2"},
                       json={"error": {"message": "synthetic quota", "retry_after": 3}}))
    try:
        with pytest.raises(RequestDeferred) as error:
            source.fetch(["600000.SH"], "2026-11-06 09:35:00", "2026-11-06 09:45:00")
        assert error.value.status_code == 429
        assert source.metrics["http_requests"] == source.metrics["http_429_count"] == 1
        assert source.budgets["minute"].delay() >= 3.5
        checkpoint = source.checkpoint()
        resumed = collector(tmp_path, lambda r: httpx.Response(200, json={"data": {}}))
        try:
            resumed.restore(checkpoint)
            assert resumed.metrics["http_requests"] == resumed.metrics["http_429_count"] == 1
            assert resumed.budgets["minute"].delay() >= 3
        finally:
            resumed.close()
    finally:
        source.close()


def test_sdk_swallowed_transport_failure_is_never_a_successful_empty_batch(tmp_path):
    def failed(request):
        raise httpx.ConnectError("synthetic disconnected source", request=request)
    source = collector(tmp_path, failed)
    try:
        with pytest.raises(RequestDeferred):
            source.fetch(["600000.SH"], "2026-11-06 09:35:00", "2026-11-06 09:45:00")
        assert source.metrics["http_requests"] == 1 and source.responses == 0
    finally:
        source.close()


def test_invalid_factor_only_marks_affected_stock_pending(tmp_path):
    data = {"600000.SH": [{"timestamp": as_ms("2026-11-06 09:35:00"), "ex_factor": 2}],
            "600001.SH": [{"timestamp": as_ms("2026-11-06 09:35:00"), "ex_factor": 0}]}
    source = collector(tmp_path, lambda request: httpx.Response(200, json={"data": data}))
    try:
        result = source.factors(["600000.SH", "600001.SH", "600002.SH"])
        assert result["600000.SH"] == [dict(day="2026-11-06", ex_factor=2)]
        assert result["600001.SH"] is result["600002.SH"] is None
        assert source.metrics["invalid_factor_symbols"] == 1
        assert source.metrics["http_requests"] == 1
    finally:
        source.close()


def test_rolling_budget_stop_and_retry_after_semantics():
    clock = [0]
    budget = RollingBudget(limit=24, clock=lambda: clock[0])
    for _ in range(24):
        budget.acquire(Event())
    assert budget.delay() == 60
    clock[0] = 60
    assert budget.delay() == 0
    assert retry_seconds({"details": {"retry_after_ms": 2500}}) == 2.5


def test_projection_uses_ingest_not_parquet_or_custom_frontend_bridge():
    requests = []
    def response(request):
        requests.append(request)
        return httpx.Response(200, json={"rows": 1, "status": "ok"})
    publisher = Publisher("http://host.invalid", transport=httpx.MockTransport(response))
    try:
        publisher.summary({"market_date": "2026-11-06"})
        publisher.history({"2026-11-06": {"rows": []}})
        assert [r.url.path for r in requests] == ["/api/ext-data/lan_ma55_summary/ingest", "/api/ext-data/lan_ma55_history/ingest"]
    finally:
        publisher.close()
