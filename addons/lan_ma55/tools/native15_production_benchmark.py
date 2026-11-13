"""Same fixture/environment: SDK parse -> saved bars -> scanner -> cache -> Ext Data."""

import argparse
import cProfile
import json
from pathlib import Path
import sys
from time import perf_counter

import httpx
from tickflow import TickFlow

ROOT = Path(__file__).resolve().parents[3]


def run(folder):
    sys.path.insert(0, str(ROOT))
    from addons.lan_ma55.backend.collector import Collector
    from addons.lan_ma55.backend.market_grid import as_ms
    from addons.lan_ma55.backend.native15_owner import NativeOwner
    from addons.lan_ma55.backend.unified_store import UnifiedStore
    from addons.lan_ma55.backend.result_publisher import Publisher
    from addons.lan_ma55.tools.stress_db import seed as legacy_seed, quantiles
    from addons.lan_ma55.tools.stress_native15 import seed as native_seed

    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=False)
    result = dict(synthetic=True, supplier_http=0, symbols=100, raw15_per_symbol=1280, paths={})
    for name, kind, seed in (("five", UnifiedStore, legacy_seed), ("native15", NativeOwner, native_seed)):
        with kind(folder/(name+".sqlite3")) as owner:
            sessions, five, bars, securities = seed(owner, 116)
            symbols = securities[:100]
            with owner.conn:
                for symbol in symbols:
                    info = owner.market.instrument(symbol)
                    info.update(source_type="stock", raw_tick=.01)
                    owner.market._put(info)
            for offset in range(0, len(symbols), 8):
                owner.market.warm_cache(symbols[offset:offset+8])
            def transport(request):
                codes = request.url.params["symbols"].split(",")
                lo, hi = (int(request.url.params[k]) for k in ("start_time", "end_time"))
                rows = [dict(row, timestamp=t) for t, row in five.items() if lo <= as_ms(t) <= hi] if name == "five" else [b for b in bars if lo <= as_ms(b["timestamp"]) <= hi]
                data = dict(timestamp=[as_ms(r["timestamp"]) for r in rows], **{
                    k:[r[k]/100 if k == "volume" else r[k] for r in rows] for k in ("open", "high", "low", "close", "volume", "amount")})
                return httpx.Response(200, json={"data":{s:data for s in codes}})
            tf = TickFlow(api_key="synthetic", max_retries=0)
            tf._client._client.close()
            tf._client._client = httpx.Client(base_url="https://synthetic.invalid", transport=httpx.MockTransport(transport))
            collector = Collector("synthetic", folder/(name+"-sdk"), client=tf)
            publisher = Publisher("http://synthetic.invalid", native=name == "native15", transport=httpx.MockTransport(lambda r:httpx.Response(200,json={})))
            timings = []
            profiler = cProfile.Profile()
            profiler.enable()
            try:
                for number in (0, 1):
                    target = bars[1280+number]["timestamp"]
                    start = min(t for t in five if as_ms(t) > as_ms(bars[1279+number]["timestamp"])) if name == "five" else target
                    began = perf_counter()
                    received, errors = (collector.fetch_five if name == "five" else collector.fetch)(symbols, start, target)
                    parsed = perf_counter()
                    assert not errors
                    transactions, phases = [], {}
                    mark = [None, None]
                    def operation(value):
                        now = perf_counter()
                        if mark[0] is not None:
                            phases[mark[0]] = phases.get(mark[0], 0)+(now-mark[1])*1000
                        mark[:] = [value, now]
                    if name == "five":
                        for symbol in symbols:
                            tick = perf_counter()
                            owner.accept_market(symbol, received[symbol], sessions=sessions, start=start, end=target,
                                                now=target, live_after=target, operation=operation)
                            transactions.append((perf_counter()-tick)*1000)
                    else:
                        with owner.conn:
                            owner.set_meta("target", target)
                        tick = perf_counter()
                        owner.accept_batch(symbols, received, errors, sessions=sessions, start=start, end=target,
                                           now=target, factors={}, stage="A", live_after=target)
                        transactions.append((perf_counter()-tick)*1000)
                        phases = dict(owner.conn.execute("SELECT write_ms,scan_ms,commit_ms FROM native15_timing ORDER BY id DESC LIMIT 1").fetchone())
                    committed = perf_counter()
                    publisher.day(target[:10], owner.day_rows(target[:10], symbols))
                    publisher.prices(target[:10], owner.market.prices(target[:10]))
                    publisher.summary(owner.summary(target))
                    published = perf_counter()
                    timings.append(dict(target=target, sdk_parse_validate_ms=(parsed-began)*1000,
                                        mock_http_ms=collector.observations[-1]["latency_ms"],
                                        write_scan_cache_commit_ms=(committed-parsed)*1000, phases=phases,
                                        ext_data_ms=(published-committed)*1000, total_ms=(published-began)*1000,
                                        transaction_ms=quantiles(transactions)))
            finally:
                profiler.disable()
                profiler.dump_stats(str(folder/(name+".prof")))
                collector.close()
                publisher.close()
            result["paths"][name] = dict(rounds=timings, requests=dict(collector.metrics), quick_check=owner.conn.execute("PRAGMA quick_check").fetchone()[0])
    (folder/"metrics.json").write_text(json.dumps(result, indent=2), "utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    print(json.dumps(run(parser.parse_args().output), indent=2))
