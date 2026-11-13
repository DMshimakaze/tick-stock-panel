"""Read-only native15/strict5 comparison, <=3 HTTP, <=8 securities, no retries.

Run during maintenance with the production MA55 owner stopped. All market output
belongs in a new private directory. This tool cannot change the production source.
"""

import argparse
from contextlib import closing
import json
from pathlib import Path
from time import monotonic

from dotenv import dotenv_values

from addons.lan_ma55.backend.collector import Collector, SourceError
from addons.lan_ma55.backend.market_grid import aggregate, derived, project, now_cn
from addons.lan_ma55.backend.native15_shadow import compare_bars
from addons.lan_ma55.backend.pipeline import process_symbol
from addons.lan_ma55.backend.request_budget import CollectionStopped, RequestDeferred
from addons.lan_ma55.backend.market_store import reader


def run(data, output, symbols):
    output.mkdir(parents=True, exist_ok=False)
    config = json.loads((data / "user_data/lan_ma55/settings.json").read_text("utf-8"))
    key = config.get("tickflow_api_key") or dotenv_values(data.parent / ".env").get("TICKFLOW_API_KEY")
    with closing(reader(data / "user_data/lan_ma55/state.sqlite3")) as db:
        sessions = json.loads(db.execute("SELECT payload FROM meta WHERE key='sessions'").fetchone()[0])
        target = db.execute("SELECT max(latest_completed15) FROM instrument_state").fetchone()[0]
        metadata = {s: dict(db.execute("SELECT * FROM instrument_state WHERE symbol=?", (s,)).fetchone()) for s in symbols}
        baseline = {s: [dict(timestamp=r["bar_end"], **{k: r[k] for k in ("open", "high", "low", "close", "volume", "amount")}) for r in db.execute("SELECT * FROM bar15 WHERE symbol=? AND source_id='TICKFLOW_RAW_5M' ORDER BY bar_end", (s,))] for s in symbols}
        old_buy = {s: [dict(r) for r in db.execute("SELECT event_id,confirmed_at FROM signal_event WHERE symbol=? ORDER BY confirmed_at", (s,))] for s in symbols}
    days = [d for d in sessions if d <= target[:10]]
    start, recent = days[-90] + " 09:35:00", days[-18] + " 09:35:00"
    began = monotonic()
    collector = Collector(key, output / "sdk", limit=8)

    def cap(request):
        if collector.metrics["http_requests"] >= 3 or monotonic() - began > 120:
            raise CollectionStopped()

    collector.tf._client._client.event_hooks["request"].insert(0, cap)
    report = dict(decision="NATIVE15_NO_GO", production="TICKFLOW_RAW_5M",
                  blocker="FROZEN_SOURCE_COUNT_REQUIRES_THREE_REAL_5M",
                  actual_supplier_http=True, max_http=3, max_seconds=120, max_retries=0,
                  target=target, suspension="NOT_VERIFIED", shared_account_pool="UNKNOWN", samples={})
    try:
        native = collector.native15_shadow(symbols, start, target)
        five, errors = collector.fetch_five(symbols, recent, target, cold=True)
        factors = collector.factors(symbols)
        (output / "market-private.json").write_text(json.dumps(dict(native=native, five=five, factors=factors)), "utf-8")
        for symbol in symbols:
            raw, diagnostics = native.get(symbol, ([], dict(missing_symbol=True)))
            strict = aggregate(five.get(symbol, {}), sessions, target)["15m"]
            sample = dict(metadata=metadata[symbol], native_count=len(raw), diagnostics=diagnostics,
                          recent_strict5=compare_bars(strict, [r for r in raw if r["timestamp"] >= recent]),
                          persisted_strict5=compare_bars(baseline[symbol], raw),
                          source_error=errors.get(symbol), historical_buy_count=len(old_buy[symbol]), periods={})
            for tf in ("30m", "60m"):
                sample["periods"][tf] = compare_bars(derived(strict, tf, sessions, target), derived([r for r in raw if r["timestamp"] >= recent], tf, sessions, target))
            for label, bars in (("strict5", strict), ("native15", raw)):
                record, added = process_symbol(symbol, project(bars, factors.get(symbol), target, sessions=sessions, timeframe="15m"),
                    sessions=sessions, raw_tick=metadata[symbol]["raw_tick"], source_asof=target, first_seen_at=str(now_cn()))
                sample[label + "_replay"] = dict(bars=len(bars), reason=record["state"]["reason"],
                    valid_run=record["state"]["valid_run"], events=[e["event_id"] for e in added],
                    reproduced_old_buy=sum(e["event_id"] in {b["event_id"] for b in old_buy[symbol]} for e in added))
            report["samples"][symbol] = sample
        report["comparison_completed"] = True
    except (SourceError, RequestDeferred, CollectionStopped, ValueError) as exc:
        report.update(comparison_completed=False, error=type(exc).__name__, http_status=getattr(exc, "status_code", None))
    finally:
        collector.close()
        report.update(metrics=dict(collector.metrics), observations=list(collector.observations), elapsed_seconds=monotonic()-began)
        (output / "evidence.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
    return dict(decision=report["decision"], comparison_completed=report.get("comparison_completed"), metrics=report["metrics"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--symbols", nargs="+", required=True)
    args = parser.parse_args()
    if not 1 <= len(args.symbols) <= 8:
        parser.error("1..8 securities required")
    print(json.dumps(run(args.data.resolve(), args.output.resolve(), args.symbols)))
