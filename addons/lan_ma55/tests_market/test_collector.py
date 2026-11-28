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
    stamp = as_ms("2026-11-12")
    event = {"timestamp": stamp, "ex_factor": 1.2}
    c = source(
        tmp_path,
        lambda r: httpx.Response(
            200,
            json={
                "data": {
                    "600000.SH": [],
                    "000001.SZ": [{"timestamp": stamp, "ex_factor": -1}],
                    "920001.BJ": [event, event],
                    "873001.BJ": None,
                    "920179.BJ": [event, {**event, "ex_factor": 1.3}],
                    "603893.SH": [{"ex_factor": 2}],
                    "688071.SH": {"timestamp": stamp},
                }
            },
        ),
    )
    try:
        result, errors = c.factor_responses([
            "600000.SH", "000001.SZ", "688827.SH", "920001.BJ", "873001.BJ",
            "920179.BJ", "603893.SH", "688071.SH",
        ])
        assert result["600000.SH"] == []
        assert result["920001.BJ"] == [{"day": "2026-11-12", "ex_factor": 1.2}]
        assert {s for s, events in result.items() if events is None} == errors.keys()
        assert errors == {
            "000001.SZ": "INVALID_FACTOR_EVENT",
            "688827.SH": "FACTOR_KEY_MISSING",
            "873001.BJ": "FACTOR_NULL_RESPONSE",
            "920179.BJ": "CONFLICTING_FACTOR_EVENTS",
            "603893.SH": "INVALID_FACTOR_EVENT",
            "688071.SH": "INVALID_FACTOR_SCHEMA",
        }
        assert c.metrics["http_requests"] == 1
    finally:
        c.close()


def test_factor_invalid_envelope_does_not_fail_the_owner(tmp_path):
    c = source(tmp_path, lambda request: httpx.Response(200, json={"unexpected": []}))
    try:
        assert c.factor_responses(["600000.SH"]) == ({"600000.SH": None}, {"600000.SH": "INVALID_FACTOR_ENVELOPE"})
    finally:
        c.close()


def test_native_single_slot_uses_previous_completed_slot_without_admitting_it(tmp_path):
    prior, target = "2026-11-26 15:00:00", "2026-11-27 09:45:00"
    calls = []

    def reply(request):
        params = request.url.params
        calls.append((params["start_time"], params["end_time"], params["count"]))
        warm = params["start_time"] == str(as_ms(prior))
        compact = dict(timestamp=[as_ms(prior), as_ms(target)] if warm else [as_ms(target)],
                       open=["bad preceding row", 10] if warm else [10.000000000000002],
                       low=[10, 10] if warm else [10.000000000000004], high=[11, 11] if warm else [11],
                       close=[11, 11] if warm else [11], volume=[2, 2] if warm else [2])
        return httpx.Response(200, json={"data": {"920001.BJ": compact}})

    c = source(tmp_path, reply)
    try:
        _, errors = c.fetch(["920001.BJ"], target, target, count=1)
        assert errors["920001.BJ"]["timestamps"] == [target]
        bars, errors = c.fetch(["920001.BJ"], target, target, count=1, previous=prior)
        assert errors == {} and list(bars["920001.BJ"]) == [target]
        assert calls == [(str(as_ms(target)), str(as_ms(target)), "1"), (str(as_ms(prior)), str(as_ms(target)), "2")]
        assert c.metrics["collection_requests"] == 2
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
        assert errors=={'000001.SZ':dict(code='SOURCE_SYMBOL_OMITTED',timestamps=None)}
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
