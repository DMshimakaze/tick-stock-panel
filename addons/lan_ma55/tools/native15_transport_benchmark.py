"""Zero-network SDK payload/batch comparison. Native15 remains shadow-only.

Both current chains schedule 16 targets/day; a smaller period does not imply
48 full-market scans/day. This measures payload/decoding, not account capacity.
"""

import argparse
from collections import Counter
import json
from pathlib import Path
from time import perf_counter

import httpx
import psutil
from tickflow import TickFlow

from addons.lan_ma55.backend.collector import Collector
from addons.lan_ma55.backend.market_grid import aggregate, as_ms
from addons.lan_ma55.tests_market.test_rolling import sample


def run(output):
    output.mkdir(parents=True, exist_ok=False)
    days, five = sample(1280)
    fifteen = aggregate(five, days, max(five))["15m"]
    compact = {}
    for period, rows in (("5m", [dict(timestamp=t, **v) for t,v in five.items()]), ("15m", fifteen)):
        compact[period] = dict(timestamp=[as_ms(r["timestamp"]) for r in rows], **{
            k:[r[k]/100 if k=='volume' else r[k] for r in rows] for k in ('open','high','low','close','volume','amount')})
    receipts, results = [], []
    process = psutil.Process()

    def reply(request):
        period = request.url.params['period']
        symbols = request.url.params['symbols'].split(',')
        payload = json.dumps(dict(data={s:compact[period] for s in symbols})).encode()
        receipts.append(dict(period=period,symbols=len(symbols),bytes=len(payload)))
        return httpx.Response(200,content=payload,headers={'Content-Type':'application/json'})

    tf = TickFlow(api_key='synthetic',max_retries=0)
    tf._client._client.close()
    tf._client._client = httpx.Client(base_url='https://synthetic.invalid',transport=httpx.MockTransport(reply))
    collector = Collector('synthetic',output/'sdk',client=tf)
    try:
        for size in (8,20,50,100):
            for period in ('5m','15m'):
                began=perf_counter()
                data=collector.request(lambda:tf.klines.batch([f'{i:06d}.SZ' for i in range(size)],
                    period=period,count=3840 if period=='5m' else 1280,adjust='none',as_dataframe=False,
                    max_workers=1,batch_size=100,show_progress=False))
                results.append(dict(batch=size,period=period,elapsed_ms=(perf_counter()-began)*1000,
                    rss_bytes=process.memory_info().rss,response_bytes=receipts[-1]['bytes'],
                    records=sum(len(r['timestamp']) for r in data.values())))
                del data
    finally:
        collector.close()
    report=dict(synthetic=True,commercial_http=0,account_pool='UNKNOWN',production_admitted=False,
                symbols=5600,targets_per_day=16,requests_per_day_each_period=56*16,
                latest_candle_records_per_day=dict(derived5=5600*16*3,native15=5600*16),
                measurements=results,request_counts=dict(Counter(r['period'] for r in receipts)),
                note='Local JSON/SDK cost only; no network latency or real-account RPM guarantee')
    (output/'metrics.json').write_text(json.dumps(report,indent=2),'utf-8')
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(run(args.output.resolve()),indent=2))
