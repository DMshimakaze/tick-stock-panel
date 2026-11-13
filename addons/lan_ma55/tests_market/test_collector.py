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
    at = "2026-11-11 09:45:00"

    def reply(r):
        assert r.url.params["period"] == "15m" and r.url.params["adjust"] == "none"
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
        assert errors == {"688827.SH": dict(code="INVALID_NATIVE15_OHLCV", timestamps=[at])}
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
            c.fetch(["600000.SH"], "2026-11-11 09:45:00", "2026-11-11 09:45:00")
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
            c.fetch(["600000.SH"], "2026-11-11 09:45:00", "2026-11-11 09:45:00")
        assert c.metrics["http_requests"] == 0
    finally:
        c.close()


def test_master_calls_real_sdk_stock_and_etf_exchange_filters(tmp_path):
    calls=[]
    def reply(request):
        calls.append((request.url.path,request.url.params.get("type")))
        return httpx.Response(200,json={"data":[]})
    c=source(tmp_path,reply)
    try:
        assert c.master()==[]
        assert [t for _,t in calls]==["stock","stock","stock","etf","etf"]
        assert all(path.endswith('/instruments') for path,_ in calls)
    finally:
        c.close()


def test_factor_and_native_market_share_eight_request_budget(tmp_path):
    c=source(tmp_path,lambda r:httpx.Response(200,json={'data':{'600000.SH':[]}}),limit=1)
    try:
        assert c.factors(['600000.SH'])=={'600000.SH':[]}
        with pytest.raises(RequestDeferred):
            c.fetch(['600000.SH'],'2026-11-11 09:45:00','2026-11-11 09:45:00')
        assert c.metrics['http_requests']==c.metrics['collection_requests']==1
    finally:
        c.close()


@pytest.mark.parametrize('status',[401,403,429])
def test_factor_http_failure_is_explicit_without_sdk_retry(tmp_path,status):
    from addons.lan_ma55.backend.collector import SourceError
    c=source(tmp_path,lambda r:httpx.Response(status,headers={'Retry-After':'600'},json={'error':{'message':'synthetic'}}))
    try:
        with pytest.raises(RequestDeferred if status==429 else SourceError) as caught:
            c.factors(['600000.SH'])
        assert caught.value.status_code==status
        assert c.metrics['http_requests']==1
        if status==429:
            assert c.budgets['minute'].delay()>599
    finally:
        c.close()


def test_native_partial_batch_distinguishes_omitted_and_explicit_empty(tmp_path):
    at='2026-11-11 09:45:00'
    c=source(tmp_path,lambda r:httpx.Response(200,json={'data':{'600000.SH':dict(timestamp=[],open=[],high=[],low=[],close=[],volume=[])}}))
    try:
        valid,errors=c.fetch(['600000.SH','000001.SZ'],at,at)
        assert valid=={'600000.SH':{}}
        assert errors=={'000001.SZ':dict(code='SOURCE_UNAVAILABLE',timestamps=None)}
        # Omission/empty alone carries no suspension or delisting identity.
        assert 'suspend' not in str(errors).lower()
    finally:
        c.close()


def test_cold_budget_is_charged_at_actual_native_http(tmp_path):
    from addons.lan_ma55.backend.request_budget import RollingBudget
    clock=[0.0]
    cold=RollingBudget(2,clock=lambda:clock[0])
    c=source(tmp_path,lambda r:httpx.Response(200,json={'data':{}}),cold_budget=cold)
    c.budgets['minute'].clock=lambda:clock[0]
    try:
        for at in (4.0,34.0):
            clock[0]=at
            c.fetch(['600000.SH'],'2026-11-11 09:45:00','2026-11-11 09:45:00',cold=True)
        # Preparation/publication time cannot shift a third actual HTTP into this minute.
        clock[0]=62.0
        with pytest.raises(RequestDeferred):
            c.fetch(['600000.SH'],'2026-11-11 09:45:00','2026-11-11 09:45:00',cold=True)
        assert c.metrics['http_requests']==2
        clock[0]=65.0
        c.fetch(['600000.SH'],'2026-11-11 09:45:00','2026-11-11 09:45:00',cold=True)
        assert c.metrics['http_requests']==3
    finally:
        c.close()
