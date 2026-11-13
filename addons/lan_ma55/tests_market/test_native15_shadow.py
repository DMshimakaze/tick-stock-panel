import httpx
import pytest
from tickflow import TickFlow

from addons.lan_ma55.backend.collector import Collector
from addons.lan_ma55.backend.market_grid import as_ms
from addons.lan_ma55.backend.native15_shadow import normalize_native, compare_bars
from addons.lan_ma55.backend.pipeline import process_symbol


def compact():
    return dict(timestamp=[as_ms('2026-11-13 11:30:00'), as_ms('2026-11-13 13:15:00')],
                open=[10, 10], high=[11, 11], low=[9, 9], close=[10, 10], volume=[10, 20], amount=[10000, 20000])


def test_native_provenance_and_unfinished_candle_never_claim_three_fives():
    rows, diagnostic = normalize_native(compact(), '2026-11-13 09:35:00', '2026-11-13 11:30:00')
    assert len(rows) == 1 and diagnostic['unfinished'] == 1
    assert rows[0]['source_count'] == 1 and rows[0]['volume'] == 1000
    rows[0]['adjust_factor'] = 1
    record, added = process_symbol('600000.SH', rows, sessions=['2026-11-13'], raw_tick=.01,
                                  source_asof='2026-11-13 11:30:00', first_seen_at='2026-11-13 11:32:00')
    assert record['state']['reason'] == 'INVALID_OR_INCOMPLETE_OHLCV'
    assert not added
    changed = dict(rows[0], close=10.00000000001)
    assert compare_bars(rows, [changed])['different_fields'] == 1
    bad = compact()
    bad['timestamp'][0] = as_ms('2026-11-13 11:25:00')
    with pytest.raises(ValueError, match='NOT_CLOSE_LABELLED'):
        normalize_native(bad, '2026-11-13 09:35:00', '2026-11-13 15:00:00')


def test_actual_sdk_native_path_counts_against_same_minute_budget(tmp_path):
    requests = []
    def reply(request):
        requests.append(request)
        assert request.url.params['adjust'] == 'none'
        assert request.url.params['period'] == '15m'
        return httpx.Response(200, json={'data': {'600000.SH': compact()}})
    tf = TickFlow(api_key='synthetic', max_retries=0)
    tf._client._client.close()
    tf._client._client = httpx.Client(base_url='https://synthetic.invalid', transport=httpx.MockTransport(reply))
    collector = Collector('synthetic', tmp_path, client=tf)
    try:
        result = collector.native15_shadow(['600000.SH'], '2026-11-13 09:35:00', '2026-11-13 15:00:00')
        assert len(result['600000.SH'][0]) == 2 and len(requests) == 1
        assert collector.metrics['minute_requests'] == 1
        assert collector.observations[-1]['capability'] == 'kline.minute.batch'
    finally:
        collector.close()
