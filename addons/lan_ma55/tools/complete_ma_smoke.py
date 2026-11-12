"""Bounded real 5m replay in the sole production owner, after a stopped-service backup.

Commercial input and detailed evidence remain in the private --output directory.
This is a historical replay, never evidence of a live next-bar arrival.
"""

import argparse
from collections import deque
from datetime import timedelta
import json
from math import fsum, isclose, prod
from pathlib import Path

from dotenv import dotenv_values

from addons.lan_ma55.backend.collector import Collector
from addons.lan_ma55.backend.market_grid import CAPACITY, MAS, latest_point, now_cn, stamp, wall
from addons.lan_ma55.backend.request_budget import CollectionStopped, RollingBudget
from addons.lan_ma55.backend.runtime import classify
from addons.lan_ma55.backend.unified_store import UnifiedStore
from addons.lan_ma55.migrate_unified import row_digests


def oracle(five, sessions, events, asof, timeframe):
    """Independent raw5 -> same-period grid -> QFQ -> six arithmetic means."""
    values, result = deque(maxlen=233), {}
    width = int(timeframe[:-1])
    for day in sessions:
        if day > asof[:10]:
            break
        scale = None if events is None else 1 / prod(e["ex_factor"] for e in events if day < e["day"] <= asof[:10])
        for base in (570, 780):
            for minute in range(base + width, base + 121, width):
                end = stamp(day, minute)
                if end > asof:
                    continue
                times = [str(wall(end) - timedelta(minutes=i)) for i in reversed(range(0, width, 5))]
                if scale is None or any(t not in five for t in times):
                    values.clear()
                    continue
                values.append(five[times[-1]]["close"] * scale)
                result[end] = {f"ma{n}": fsum(list(values)[-n:]) / n if len(values) >= n else None for n in MAS}
    return result


def run(data, output, symbols):
    output.mkdir(parents=True, exist_ok=False)
    config = json.loads((data / "user_data/lan_ma55/settings.json").read_text("utf-8"))
    key = config.get("tickflow_api_key") or dotenv_values(data.parent / ".env").get("TICKFLOW_API_KEY")
    collector = Collector(key, output / "sdk", limit=8)
    cold = RollingBudget(2)
    cap = 12

    def limit(request):
        if collector.metrics["http_requests"] >= cap:
            raise CollectionStopped()

    collector.tf._client._client.event_hooks["request"].insert(0, limit)
    report = dict(source="REAL_TICKFLOW_5M_HISTORICAL_REPLAY", live_next_bar="NOT_VERIFIED", max_http=cap, samples={})
    truth = {s: {} for s in symbols}
    try:
        with UnifiedStore(data / "user_data/lan_ma55/state.sqlite3") as owner:
            old = {t: row_digests(owner.conn, t) for t in ("signal_event", "event_risk")}
            sessions = collector.calendar()
            rows, overview = classify(collector.master(), str(now_cn())[:10])
            owner.refresh_master(rows, overview, sessions)
            report["universe"] = overview
            target = latest_point(sessions, str(now_cn()))
            days = [d for d in sessions if d <= target[:10]][-90:]
            factors = collector.factors(symbols)
            report["target"] = target
            # SDK count is per symbol. Each bounded exchange window has <=864
            # expected slots; a 1200 count cannot silently truncate this window.
            for window in range(5):
                selected = days[max(0, len(days) - 18 * (window + 1)):len(days) - 18 * window]
                start = selected[0] + " 09:35:00"
                end = target if window == 0 else selected[-1] + " 15:00:00"
                cold.acquire(collector.stop)
                received, errors = collector.fetch(symbols, start, end, cold=True)
                for symbol in symbols:
                    truth[symbol].update(received.get(symbol, {}))
                    info = owner.market.instrument(symbol)
                    if not info or symbol not in received:
                        continue
                    if window == 0:
                        owner.accept_market(symbol, received[symbol], sessions=sessions, start=start, end=end,
                            now=str(now_cn()), events=factors.get(symbol), factor_response=True,
                            rebuild=True, live_after=str(now_cn()))
                    else:
                        owner.market.backfill(symbol, received[symbol], sessions=sessions, start=start, end=end, now=str(now_cn()))
                with owner.conn:
                    owner.save_requests(list(collector.observations), "BOUNDED_COMPLETE_MA_REPLAY")
                (output / f"window-{window}.json").write_text(json.dumps(dict(start=start, end=end, rows=received, errors=errors, observations=list(collector.observations))), "utf-8")
                collector.observations.clear()
                print(json.dumps(dict(window=window+1, raw5={s:len(truth[s]) for s in symbols})), flush=True)
            for symbol in symbols:
                sample = dict(metadata=owner.market.instrument(symbol), periods={})
                for tf in CAPACITY:
                    actual = owner.market.read(symbol, tf, target)
                    expected = oracle(truth[symbol], sessions, factors.get(symbol), target, tf)
                    failures = []
                    for bar in actual["bars"]:
                        want = expected.get(bar["timestamp"], {})
                        for n in MAS:
                            a, b = bar.get(f"ma{n}"), want.get(f"ma{n}")
                            if a is None or b is None or not isclose(a, b, rel_tol=1e-10, abs_tol=1e-9):
                                failures.append(dict(at=bar["timestamp"], period=n, actual=a, expected=b))
                    sample["periods"][tf] = dict(state=actual["ma_history"], checked=len(actual["bars"])*6,
                        failures=failures, first=actual["bars"][:2], last=actual["bars"][-2:],
                        status="PASS" if actual["ma_history"]["state"] == "READY" and not failures else "FAIL")
                report["samples"][symbol] = sample
            after = {t:row_digests(owner.conn,t) for t in old}
            report["old_rows_equal"] = {t: all(after[t].get(k)==v for k,v in prior.items()) for t,prior in old.items()}
            report["quick_check"] = owner.conn.execute("PRAGMA quick_check").fetchone()[0]
            report["refresh"] = owner.market.snapshot()["refresh"]
            report["status"] = "PASS" if all(p["status"]=="PASS" for s in report["samples"].values() for p in s["periods"].values()) and all(report["old_rows_equal"].values()) else "FAIL"
    finally:
        report["metrics"] = dict(collector.metrics)
        collector.close()
        (output / "evidence.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
    return dict(status=report["status"], metrics=report["metrics"], old_rows_equal=report["old_rows_equal"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--symbols", nargs="+", required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.data.resolve(), args.output.resolve(), args.symbols)), flush=True)
