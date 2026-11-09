"""Run from examples/quantdash-data-source: python -m capacity_sim.cli --help."""

import argparse
from dataclasses import replace
import json
from pathlib import Path

from .contracts import fault_contracts
from .model import Config
from .scenarios import lower_bounds, replay, search


PARAMETERS = {
    "universe": "stocks; scenario 5500/5891/6100, 5891 observed master denominator",
    "etfs": "securities; assumed 1500 for full ETF snapshot/daily load, not measured coverage",
    "metadata_symbols": "securities; observed candidate master 8275, batches of 100",
    "day": "Beijing replay date; locally confirmed 2026-11-09 session",
    "holiday": "boolean; explicit closed-session fault, no holiday-library assumption",
    "seed": "integer; deterministic lognormal latency RNG seed",
    "stop_s": "seconds after 09:00 Beijing; 30600=17:30, no later sends or commits",
    "host_rpm": "actual HTTP / rolling 60s; observed default 12, proposed 30 only offline",
    "reserve": "HTTP / rolling 60s; existing quote reserve 6, background cap host_rpm-reserve",
    "market_rpm": "5m batch HTTP / rolling 60s; existing collector ceiling 24",
    "policy": "independent=current objects; static=explicit minute split; broker=shared rolling admission",
    "minute_budget": "minute-batch HTTP / rolling 60s; conservative scenario 24=80% of assumed 30",
    "static_host_minute": "minute HTTP / rolling 60s; proposed host share, remainder to marketdata",
    "vendor_scope": "minute_shared=1m/5m/15m same 30 pool; account=assumed total 30 HTTP pool; broker caps 24 with 6 quote reserve",
    "independent_periods": "boolean; unverified hypothetical separate 30 pools per period",
    "full_interval_minutes": "minutes; host max(start+interval,finish), no catch-up/overlapping rounds",
    "post_minute_bars": "1m bars/security; explicit catch-up stress 1200=five sessions, not the official days=5 incremental getter; host stream chunks 50 symbols",
    "quote_seconds": "seconds; existing host sleeps after whole 3-HTTP snapshot cycle",
    "ma_period": "minutes; existing raw 5m or experimental raw native 15m, same stock coverage",
    "focus": "securities; assumed 10/50/100, ceil(focus/100) HTTP per refresh",
    "depth": "securities; assumed 1/5/10, one single-depth HTTP each",
    "focus_seconds": "seconds; assumed on-demand refresh period 120",
    "depth_seconds": "seconds; assumed depth refresh period 120",
    "latency_p50_s": "seconds; assumed per-HTTP network p50 before payload/processing",
    "latency_p95_s": "seconds; assumed per-HTTP network p95 before payload/processing",
    "bandwidth_MB_s": "decimal MB/s; assumed 20 effective payload bandwidth",
    "parse_rows_s": "rows/s; assumed 200000 SDK/Polars decode throughput",
    "write_rows_s": "bar entries/s; assumed 100000 effective rolling-compute/SQL throughput; retained-window upserts count",
    "transaction_ms": "ms/security; assumed 1 per symbol transaction overhead",
    "history_rows_s": "rows/s; assumed history aggregation/indicator processing, S6 derived from 60/120min CPU work",
    "consumer_s": "seconds; assumed 10 extra MA55 poll/publish delay, not measured full-market CPU latency",
    "enriched_s": "seconds; assumed 120 serial post-market enriched compute",
    "wire_row_bytes": "bytes/bar; assumed 120 compact JSON average; quotes assume 500 bytes/security",
    "disk_row_bytes": "bytes/bar entry; assumed 400 including JSON/MA/provenance, excludes WAL/index/compaction",
    "live_rewrite_bars": "bar entries/security/live batch; assumed 465=144+48+24+12+6+3+128 from bounded raw/derived/2d upserts, same cost for hypothetical native15",
    "warmup_minutes": "minutes; imposed 64-session CPU workload 60/120, not a measured duration",
    "fault": "injection: 429 / outage / factor_unverified / late_bar; empty=nominal synthetic source",
    "outage_minutes": "minutes; injected network interruption starting 10:00, candidate 5/10",
}


def suite(config):
    results = {name: replay(name, config) for name in
               ("S0", "S1", "S2", "S3-static", "S3-broker", "S4", "S5")}
    results["S3-recommended"] = replay("S3-broker", replace(config, full_interval_minutes=7))
    for minutes in (60, 120):
        results[f"S6-{minutes}"] = replay("S6", replace(config, warmup_minutes=minutes))
    for fault, duration in (("429", 10), ("outage", 5), ("outage", 10), ("late_bar", 10)):
        results[f"S5-{fault}-{duration}"] = replay("S3-broker", replace(
            config, full_interval_minutes=7, fault=fault, outage_minutes=duration))
    return results


def sensitivity(config, count):
    profiles = ((0.1, 0.5), (0.5, 2), (2, 5), (5, 10))
    results = []
    for i in range(count):
        p50, p95 = profiles[i % len(profiles)]
        cfg = replace(config, seed=i + 1, latency_p50_s=p50, latency_p95_s=p95,
                      universe=(5500, 5891, 6100)[i % 3], focus=(10, 50, 100)[i % 3],
                      depth=(1, 5, 10)[i % 3], full_interval_minutes=7,
                      parse_rows_s=(200000, 100000, 50000, 20000)[i % 4],
                      write_rows_s=(100000, 50000, 20000, 10000)[i % 4],
                      transaction_ms=(1, 5, 10, 20)[i % 4],
                      consumer_s=(10, 30, 60, 180)[i % 4])
        pairs = {str(mode): replay("S3-broker", replace(cfg, ma_period=mode)) for mode in (5, 15)}
        results.append(dict(seed=cfg.seed, universe=cfg.universe, focus=cfg.focus, depth=cfg.depth,
                            full_interval_minutes=cfg.full_interval_minutes,
                            latency_p50_s=p50, latency_p95_s=p95,
                            parse_rows_s=cfg.parse_rows_s, write_rows_s=cfg.write_rows_s,
                            transaction_ms=cfg.transaction_ms, consumer_s=cfg.consumer_s,
                            modes={mode: {k: result[k] for k in ("slo", "freshness_s", "requests",
                                    "peaks_60s", "pipeline", "estimated_volume")}
                                   for mode, result in pairs.items()}))
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", default="all", choices=("all", "S0", "S1", "S2", "S3-static", "S3-broker", "S4", "S5", "S6"))
    parser.add_argument("--universe", type=int, default=5891)
    parser.add_argument("--seed", type=int, default=11)
    parser.add_argument("--day", default="2026-11-09")
    parser.add_argument("--holiday", action="store_true")
    parser.add_argument("--quote-seconds", type=int, choices=(15, 30, 60, 120), default=30)
    parser.add_argument("--full-minutes", type=int, choices=(1, 2, 3, 4, 5, 7, 10, 15), default=2)
    parser.add_argument("--ma-period", type=int, choices=(5, 15), default=5)
    parser.add_argument("--post-minute-bars", type=int, default=1200,
                        help="offline catch-up volume/security; existing incremental history usually needs fewer bars")
    parser.add_argument("--host-rpm", type=int, choices=range(1, 31), default=12,
                        help="offline host budget for S0/S1/S2; S3 applies its proposed split")
    parser.add_argument("--market-rpm", type=int, choices=range(1, 25), default=24,
                        help="offline existing collector minute budget")
    parser.add_argument("--latency-p50", type=float, default=0.5)
    parser.add_argument("--latency-p95", type=float, default=2)
    parser.add_argument("--vendor-scope", choices=("minute_shared", "account"), default="minute_shared")
    parser.add_argument("--independent-periods", action="store_true", help="unverified counterfactual, never account evidence")
    parser.add_argument("--warmup-minutes", type=int, choices=(0, 60, 120), default=0)
    parser.add_argument("--search", action="store_true", help="128 combinations, same full-market function list")
    parser.add_argument("--sensitivity", type=int, default=0, metavar="N", help="N distinct seeds/profiles, each paired 5m/15m")
    parser.add_argument("--output", type=Path, help="optional JSON path; default writes nothing")
    args = parser.parse_args(argv)
    if args.universe < 1 or args.post_minute_bars < 1 or args.latency_p50 <= 0 or args.latency_p95 < args.latency_p50 or args.sensitivity < 0:
        parser.error("positive universe/latencies required; p95>=p50; sensitivity>=0")
    if args.output:
        private = Path(__file__).resolve().parents[3] / "data" / "diagnostics"
        args.output = args.output.resolve()
        if not args.output.is_relative_to(private.resolve()):
            parser.error("--output must be inside the ignored repository data/diagnostics directory")
    cfg = Config(universe=args.universe, seed=args.seed, day=args.day, holiday=args.holiday,
                 quote_seconds=args.quote_seconds, full_interval_minutes=args.full_minutes,
                 ma_period=args.ma_period, host_rpm=args.host_rpm, market_rpm=args.market_rpm,
                 post_minute_bars=args.post_minute_bars,
                 latency_p50_s=args.latency_p50,
                 latency_p95_s=args.latency_p95, vendor_scope=args.vendor_scope,
                 independent_periods=args.independent_periods, warmup_minutes=args.warmup_minutes)
    payload = dict(schema_version=1, evidence="SYNTHETIC_OFFLINE_PREDICTION",
                   parameter_definitions=PARAMETERS, lower_bounds=lower_bounds(),
                   scenarios=suite(cfg) if args.scenario == "all" else {args.scenario: replay(args.scenario, cfg)},
                   data_fault_probes=fault_contracts())
    if args.search:
        payload["search"] = search(cfg)
    if args.sensitivity:
        payload["sensitivity"] = sensitivity(cfg, args.sensitivity)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
        print(json.dumps(dict(output=str(args.output), scenarios=len(payload["scenarios"]),
                              search_candidates=payload.get("search", {}).get("candidate_count", 0),
                              sensitivity_runs=len(payload.get("sensitivity", []))), ensure_ascii=False))
    else:
        print(encoded, end="")


if __name__ == "__main__":
    main()
