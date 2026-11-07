"""Test-only reference: archive the pinned Git source to TEMP, never import it in the plugin.

MA55_REFERENCE_REPO is read-only. MA55_FROZEN_PACK is optional private CSV input;
neither source nor raw prices are written to this repository or test reports.
"""
import csv
from dataclasses import replace
import io
import json
from math import isfinite
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile

COMMIT = "23477622536d1206b2af3c164b4c905aa2e15e8d"
BLOBS = {
    "ma55/structure.py": "0e777b61d9c33c9fdae4892508491e24824b0a5e",
    "ma55/features.py": "e682a304c493b1ff8c78a67980f51096056451ab",
}
POSITIVE = {
    "sz001309": ["2026-09-08 09:45"],
    "sh605006": ["2026-09-09 09:45", "2026-09-11 11:30"],
    "sh601869": ["2026-09-11 09:45", "2026-09-11 11:30"],
    "sh603938": ["2026-09-14 10:00", "2026-09-15 09:45"],
    "sz000710": ["2026-09-23 11:30"], "sh600667": ["2026-09-16 10:15"],
    "sz300450": ["2026-10-12 11:30", "2026-10-12 14:15"],
    "sz000021": ["2026-10-27 11:15", "2026-10-27 13:45"],
    "sz002185": ["2026-10-27 13:45"], "sh588170": ["2026-11-04 11:15"],
}
SUPPRESSED = {
    "sz001309": ["2026-09-08 10:15"], "sh601869": ["2026-09-11 13:30"],
    "sz000710": ["2026-09-23 10:45"], "sz300450": ["2026-10-12 15:00"],
    "sh588170": ["2026-11-04 13:30"], "sz000001": ["2026-10-19 09:45"],
    "sh600009": ["2026-10-15 10:15"],
}
NEGATIVE = {"sh603678": ["2026-09-17 14:15"], "sh600176": ["2026-09-18 10:30"]}


def clean(value):
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, float) and not isfinite(value):
        return None
    return value


def main():
    repo = os.environ["MA55_REFERENCE_REPO"]
    payload = json.load(sys.stdin)
    def git(*args):
        return subprocess.check_output(["git", "-C", repo, *args])
    for path, blob in BLOBS.items():
        assert git("rev-parse", f"{COMMIT}:{path}").decode().strip() == blob
    archive = git("archive", COMMIT, "config.py", "ma55")
    with tempfile.TemporaryDirectory(prefix="lan-ma55-oracle-") as directory:
        with tarfile.open(fileobj=io.BytesIO(archive)) as source:
            source.extractall(directory, filter="data")
        sys.path.insert(0, directory)
        import pandas as pd
        from ma55.features import minimal_features
        from ma55.structure import advance
        from ma55.intraday_unified import analyze, DEFAULTS

        if payload["action"] == "synthetic":
            results = []
            for case in payload["cases"]:
                history, slots, state, trace = [], {}, None, []
                for bar in case["bars"]:
                    slot = bar["timestamp"][11:16]
                    b = minimal_features(history, bar, slots.get(slot, []))
                    state, decision = advance(state, b, case["tick"])
                    trace.append(dict(structure=state, decision=decision))
                    history = (history + [bar])[-55:]
                    slots[slot] = (slots.get(slot, []) + [bar["volume"]])[-5:]
                results.append(dict(name=case["name"], trace=trace))
            print(json.dumps(clean(results), allow_nan=False))
            return

        pack = Path(os.environ["MA55_FROZEN_PACK"])
        manifest = json.loads((pack / "manifest.json").read_text(encoding="utf-8"))
        with (pack / "exchange_sessions.csv").open(encoding="utf-8-sig") as stream:
            sessions = [r["session_date"] for r in csv.DictReader(stream)]
        cache, results = {}, []
        for kind, targets in (("positive", POSITIVE), ("suppressed", SUPPRESSED), ("negative", NEGATIVE)):
            for symbol, times in targets.items():
                if symbol not in cache:
                    frames = {}
                    for tf in ("15m", "5m", "daily"):
                        paths = sorted((pack / ("bars_" + tf)).glob(symbol + "*.csv"))
                        frame = pd.concat([pd.read_csv(p, parse_dates=["timestamp"]) for p in paths], ignore_index=True).sort_values("timestamp")
                        frame["source_count"] = 3 if tf == "15m" else 1 if tf == "5m" else 48
                        frames[tf] = frame.to_dict("records")
                    security = next(s for s in manifest["securities"] if s["symbol"] == symbol)
                    cache[symbol] = frames, security["raw_tick"]
                frames, tick = cache[symbol]
                canonical = symbol[2:] + "." + symbol[:2].upper()
                for when in times:
                    state, events = analyze(frames["15m"], sessions, canonical,
                        five=frames["5m"], higher={"daily": frames["daily"]}, asof=when,
                        parameters=replace(DEFAULTS, tick=tick))
                    unique = {e["episode_id"]: e for e in events if e["confirmed_at"] == e["last_observed_at"]}
                    bars = []
                    factors = {}
                    for row in frames["15m"]:
                        if row["timestamp"] > pd.Timestamp(when):
                            continue
                        at = str(row["timestamp"])
                        valid = all(isfinite(row[k]) and row[k] > 0 for k in ("open", "high", "low", "close", "volume")) and (
                            row["low"] <= min(row["open"], row["close"]) <= max(row["open"], row["close"]) <= row["high"])
                        bars.append({**{k: row[k] for k in ("open", "high", "low", "close", "volume")},
                            "timestamp": at, "valid": valid, "reason": "INVALID_OR_INCOMPLETE_OHLCV"})
                        factors[at[:10]] = row["close"] / row["raw_close"]
                    results.append(dict(kind=kind, symbol=canonical, when=when + ":00", tick=tick,
                        bars=bars, factors=factors, sessions=sessions,
                        decision=state["decision"], structure=state["structure"],
                        events=[dict(event_id=e["episode_id"], confirmed_at=e["confirmed_at"],
                            setup_at=e["setup_at"], pattern=e["pattern"]) for e in unique.values()]))
        print(json.dumps(clean(results), allow_nan=False))


if __name__ == "__main__":
    main()
