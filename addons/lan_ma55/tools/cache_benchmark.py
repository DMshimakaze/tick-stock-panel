"""Same saved raw inputs, old projection versus persistent chart-cache reads.

No supplier calls. Source SQLite opens read-only; all writes use a new private DB.
"""

import argparse
from contextlib import closing
import json
from pathlib import Path
from statistics import mean
from time import perf_counter

import psutil

from addons.lan_ma55.backend.market_grid import CAPACITY, derived, project
from addons.lan_ma55.backend.market_store import Store, reader
from addons.lan_ma55.tools.stress_db import quantiles


def run(source, output):
    output.mkdir(parents=True, exist_ok=False)
    symbols, facts = [], {}
    with closing(reader(source)) as conn:
        sessions = json.loads(conn.execute("SELECT payload FROM meta WHERE key='sessions'").fetchone()[0])
        symbols = [r[0] for r in conn.execute(
            "SELECT i.symbol FROM instrument_state i JOIN ma_history_progress p USING(symbol) "
            "WHERE p.raw_count>=1280 AND i.factor_coverage='EXPLICIT' "
            "ORDER BY i.source_type,i.symbol LIMIT 8")]
        for symbol in symbols:
            info = dict(conn.execute("SELECT * FROM instrument_state WHERE symbol=?", (symbol,)).fetchone())
            info.update(gaps=json.loads(info["gaps"]), eligible=bool(info["eligible"]), correction=bool(info["correction"]))
            raw = [dict(timestamp=r["bar_end"], **{k:r[k] for k in ("open","high","low","close","volume","amount","source_id","source_count")}, complete=True, source=r["source_id"]) for r in conn.execute("SELECT * FROM bar15 WHERE symbol=? ORDER BY bar_end", (symbol,))]
            events = [dict(r) for r in conn.execute("SELECT day,ex_factor FROM factor_event WHERE symbol=? ORDER BY ordinal", (symbol,))]
            facts[symbol] = (info, raw, events)
    if not symbols:
        raise ValueError("No sufficiently warm real sample in source")
    report = dict(actual_saved_market_data=True, commercial_http=0, symbols=symbols,
                  old_read_ms=[], cache_read_ms=[], incremental_cache_ms=[], cache_build_ms=[])
    with closing(Store(output / "benchmark.sqlite3")) as store:
        store.save_master([facts[s][0] for s in symbols], dict(expected=len(symbols)), sessions)
        with store.transaction():
            for symbol, (info, raw, events) in facts.items():
                store._put(info)
                store._write(symbol, raw)
                store._factors(symbol, events)
                began = perf_counter()
                store.update_cache(symbol, info, raw, events, sessions, rebuild=True)
                report["cache_build_ms"].append((perf_counter()-began)*1000)
        errors, hits = 0, 0
        for _ in range(5):
            for symbol, (info, raw, events) in facts.items():
                end = raw[-1]["timestamp"]
                for tf, capacity in CAPACITY.items():
                    began = perf_counter()
                    with closing(reader(store.path)) as db:
                        db.execute("BEGIN")
                        original = store.raw_rows(symbol, end, limit=1280, conn=db)
                        old = project(derived(original, tf, sessions, end), events, end, sessions=sessions, timeframe=tf)[-capacity:]
                    report["old_read_ms"].append((perf_counter()-began)*1000)
                    began = perf_counter()
                    new = store.read(symbol, tf, end)
                    report["cache_read_ms"].append((perf_counter()-began)*1000)
                    hits += int(new["cache"]["hit"])
                    for a, b in zip(old, new["bars"]):
                        for n in (5,10,20,30,55,233):
                            x,y=a[f"ma{n}"],b[f"ma{n}"]
                            if x is None or y is None:
                                errors += int(x != y)
                            else:
                                errors += int(abs(x-y)>1e-11*max(1,abs(x)))
        # Replay genuine last-32 candles in order, including retention boundaries.
        for symbol, (info, raw, events) in facts.items():
            store.update_cache(symbol, info, raw[:-32], events, sessions, rebuild=True)
            for stop in range(len(raw)-31, len(raw)+1):
                began = perf_counter()
                with store.transaction():
                    store.update_cache(symbol, info, raw[:stop], events, sessions)
                report["incremental_cache_ms"].append((perf_counter()-began)*1000)
        report.update(cache_hit_rate=hits/len(report["cache_read_ms"]), arithmetic_errors=errors,
                      cache_rows=store.conn.execute("SELECT count(*) FROM chart_cache").fetchone()[0],
                      quick_check=store.conn.execute("PRAGMA quick_check").fetchone()[0])
    for metric in ("old_read_ms","cache_read_ms","incremental_cache_ms","cache_build_ms"):
        values = report[metric]
        report[metric] = dict(mean=mean(values), **quantiles(values), count=len(values))
    report.update(read_speedup=report["old_read_ms"]["mean"]/report["cache_read_ms"]["mean"],
                  rss_bytes=psutil.Process().memory_info().rss, db_bytes=(output/"benchmark.sqlite3").stat().st_size)
    (output / "metrics.json").write_text(json.dumps(report, indent=2), "utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.source.resolve(), args.output.resolve()), indent=2))
