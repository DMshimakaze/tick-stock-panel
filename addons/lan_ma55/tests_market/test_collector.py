import httpx
import pytest
from tickflow import TickFlow
from addons.lan_ma55.backend.collector import Collector
from addons.lan_ma55.backend.request_budget import RequestDeferred
from addons.lan_ma55.backend.market_grid import as_ms


def source(tmp_path, reply, **kw):
    tf = TickFlow(
        api_key="synthetic", base_url="https://synthetic.invalid", max_retries=0
    )
    tf._client._client.close()
    tf._client._client = httpx.Client(
        transport=httpx.MockTransport(reply), base_url="https://synthetic.invalid"
    )
    return Collector("synthetic", tmp_path, client=tf, **kw)


def test_real_sdk_shape_and_one_bad_symbol_does_not_block_others(tmp_path):
    at = "2026-11-11 09:35:00"

    def reply(r):
        assert r.url.params["period"] == "5m" and r.url.params["adjust"] == "none"
        good = dict(
            timestamp=[as_ms(at)],
            open=[10],
            high=[11],
            low=[9],
            close=[10],
            volume=[2],
            amount=[2000],
        )
        return httpx.Response(
            200, json={"data": {"600000.SH": good, "688827.SH": dict(good, high=[1])}}
        )

    c = source(tmp_path, reply)
    try:
        valid, errors = c.fetch(["600000.SH", "688827.SH"], at, at, cold=True)
        assert valid["600000.SH"][at]["volume"] == 200
        assert errors == {"688827.SH": dict(code="INVALID_5M_OHLCV", timestamps=[at])}
        assert valid["688827.SH"] == {}
        assert c.metrics["http_requests"] == c.metrics["minute_requests"] == 1
    finally:
        c.close()


def test_missing_factor_is_not_explicit_empty(tmp_path):
    c = source(
        tmp_path,
        lambda r: httpx.Response(
            200,
            json={
                "data": {
                    "600000.SH": [],
                    "000001.SZ": [{"timestamp": 1, "ex_factor": -1}],
                }
            },
        ),
    )
    try:
        assert c.factors(["600000.SH", "000001.SZ", "688827.SH"]) == {
            "600000.SH": [],
            "000001.SZ": None,
            "688827.SH": None,
        }
    finally:
        c.close()


def test_429_cooldown_no_automatic_retry(tmp_path):
    c = source(
        tmp_path,
        lambda r: httpx.Response(
            429, headers={"Retry-After": "80"}, json={"error": {"message": "synthetic"}}
        ),
    )
    try:
        with pytest.raises(RequestDeferred):
            c.fetch(["600000.SH"], "2026-11-11 09:35:00", "2026-11-11 09:45:00")
        assert c.metrics["http_429_count"] == c.metrics["http_requests"] == 1
        assert c.budgets["minute"].delay() > 79
    finally:
        c.close()


def test_public_host_job_owns_priority(tmp_path):
    c = source(
        tmp_path,
        lambda r: httpx.Response(200, json={"data": {}}),
        host_busy=lambda: True,
    )
    try:
        with pytest.raises(RequestDeferred):
            c.fetch(["600000.SH"], "2026-11-11 09:35:00", "2026-11-11 09:45:00")
        assert c.metrics["http_requests"] == 0
    finally:
        c.close()
