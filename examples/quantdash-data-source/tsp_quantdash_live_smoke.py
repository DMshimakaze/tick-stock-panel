"""Read-only, live TSP registry smoke check for an INSTALLED QuantDash plugin.

Usage with TSP's backend Python (do not use a random Python/nga environment):
  E:\\tick stock panel\\backend\\.venv\\Scripts\\python.exe tsp_quantdash_live_smoke.py --tsp-root "E:\\tick stock panel"

No API token, quotes, or full market rows are printed or written to files.
Does not change TSP data-source routing and does not write market data.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import json
from pathlib import Path
import sys
import traceback
from zoneinfo import ZoneInfo

BJ = ZoneInfo("Asia/Shanghai")


def _audit(frame):
    if frame is None or frame.is_empty():
        return {"rows": 0, "amount_samples": 0, "inconsistent_amount": 0}
    rows = frame.head(20).to_dicts()
    samples = bad = 0
    for row in rows:
        volume, amount = row.get("volume"), row.get("amount")
        low, high = row.get("low"), row.get("high")
        if not (amount and volume and low and high):
            continue
        samples += 1
        traded_avg = amount / (volume * 100)
        if not low * 0.8 <= traded_avg <= high * 1.2:
            bad += 1
    return {"rows": frame.height, "amount_samples": samples, "inconsistent_amount": bad}


def run(tsp_root: Path, datasets: list[str]):
    backend = tsp_root / "backend"
    if not (backend / "app" / "data_providers" / "custom").is_dir():
        print("ERROR: --tsp-root must contain backend/app/data_providers/custom", file=sys.stderr)
        return 2
    sys.path.insert(0, str(backend))
    from app.data_providers import custom

    custom.load_all()
    status = next((x for x in custom.list_plugins() if x["name"] == "quantdash_user"), None)
    print("CHECK VERSION: TSP external plugin; no route changes")
    if status is None:
        print("ERROR: quantdash_user is NOT installed in this TSP data directory", file=sys.stderr)
        return 2
    print("PLUGIN:", json.dumps({k: status.get(k) for k in
                                 ("name", "version", "available", "status", "datasets")},
                                ensure_ascii=False, default=str))
    if not status.get("available"):
        return 2
    provider = custom.get_provider("quantdash_user")
    now = datetime.now(BJ).replace(tzinfo=None)
    output = {}
    for dataset in datasets:
        try:
            if dataset == "daily":
                frame = provider.get_daily(["600519.SH", "000001.SZ"], now - timedelta(days=20), now)
                output[dataset] = {"ok": frame.height > 0, **_audit(frame), "columns": frame.columns}
                if output[dataset]["inconsistent_amount"]:
                    output[dataset]["ok"] = False
            elif dataset == "minute":
                frame = provider.get_minute(["600519.SH"], now - timedelta(days=3), now)
                output[dataset] = {"ok": frame.height > 0, **_audit(frame), "columns": frame.columns}
                if frame.height:
                    last = frame.select("datetime").max().item()
                    output[dataset]["last_beijing_wallclock"] = str(last)
                if output[dataset]["inconsistent_amount"]:
                    output[dataset]["ok"] = False
            elif dataset == "realtime":
                rows = provider.get_realtime()
                codes = {r.get("symbol") for r in rows}
                output[dataset] = {
                    "ok": len(rows) >= 1000 and "600519.SH" in codes,
                    "rows": len(rows),
                    "required_columns": list(rows[0]) if rows else [],
                    "sample_timestamp_ms": rows[0].get("timestamp") if rows else None,
                }
                if rows:
                    samples = bad = 0
                    for r in rows[:30]:
                        if r.get("amount") and r.get("volume") and r.get("high") and r.get("low"):
                            samples += 1
                            avg = r["amount"] / (r["volume"] * 100)
                            bad += int(not r["low"] * 0.8 <= avg <= r["high"] * 1.2)
                    output[dataset].update(amount_samples=samples, inconsistent_amount=bad)
                    if samples >= 5 and bad >= 2:
                        output[dataset]["ok"] = False
            elif dataset in ("adj_factor", "depth5"):
                verdict = provider.test_dataset(dataset, ["600519.SH"])
                output[dataset] = {
                    "ok": not verdict.get("error"),
                    "rows": verdict.get("rows"),
                    "columns": verdict.get("columns"),
                    "error": verdict.get("error"),
                }
            elif dataset == "full_minute":
                verdict = provider.test_dataset(dataset, ["600519.SH", "000001.SZ"])
                output[dataset] = {
                    "ok": not verdict.get("error") and verdict.get("rows", 0) > 0,
                    "rows": verdict.get("rows"),
                    "columns": verdict.get("columns"),
                    "error": verdict.get("error"),
                }
            else:
                raise ValueError("unsupported dataset")
        except Exception as exc:
            output[dataset] = {"ok": False, "error_type": type(exc).__name__}
        print(f"RESULT {dataset}: {json.dumps(output[dataset], ensure_ascii=False, default=str)}")
    failures = [k for k, v in output.items() if not v["ok"]]
    print("SUMMARY:", "PASS" if not failures else "FAIL: " + ", ".join(failures))
    return 0 if not failures else 1


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Read-only QuantDash TSP integration smoke")
    p.add_argument("--tsp-root", type=Path, required=True)
    p.add_argument("--datasets", nargs="+", choices=["daily", "minute", "realtime", "adj_factor", "depth5", "full_minute"],
                   default=["daily", "minute", "realtime"])
    args = p.parse_args()
    raise SystemExit(run(args.tsp_root.resolve(), args.datasets))
