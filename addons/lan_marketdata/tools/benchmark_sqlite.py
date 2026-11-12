"""Synthetic 5600 x 250 WAL benchmark. Writes only the explicitly supplied new DB."""

import argparse
from contextlib import closing
from datetime import date, timedelta
import json
from pathlib import Path
import statistics
import sys
from threading import Event, Thread
from time import perf_counter

import psutil

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from addons.lan_marketdata.backend.grid import SOURCE, aggregate, points
from addons.lan_marketdata.backend.store import DEFAULTS, Store, reader


def run(path, count=5600):
    if Path(path).exists():
        raise ValueError("Benchmark destination must be a new file")
    sessions = []
    day = date(2026, 1, 5)
    while len(sessions) < 17:
        if day.weekday() < 5:
            sessions.append(str(day))
        day += timedelta(days=1)
    stamps = points(sessions, sessions[0], sessions[-1] + " 15:00:00")[:750]
    five = {
        t: dict(open=10, high=11, low=9, close=10, volume=1000, amount=10000)
        for t in stamps
    }
    bars = aggregate(five, sessions, max(five))["15m"]
    store = Store(path)
    with store.conn:
        store.set_sessions(sessions)
    stop, latencies, errors, rss = Event(), [], [], []
    written = [0]

    def observe():
        proc = psutil.Process()
        while not stop.is_set():
            rss.append(proc.memory_info().rss)
            if written[0]:
                began = perf_counter()
                try:
                    with closing(reader(path)) as conn:
                        rows = conn.execute(
                            "SELECT close FROM bar15 WHERE symbol=? ORDER BY bar_end",
                            (f"{written[0] - 1:06d}.SH",),
                        ).fetchall()
                    if len(rows) != 250:
                        errors.append("incomplete committed read")
                    latencies.append((perf_counter() - began) * 1000)
                except Exception as exc:
                    errors.append(type(exc).__name__)
            stop.wait(0.01)

    observer = Thread(target=observe)
    observer.start()
    began = perf_counter()
    try:
        for i in range(count):
            symbol = f"{i:06d}.SH"
            info = dict(DEFAULTS, symbol=symbol)
            info.update(
                name="SYNTHETIC",
                eligible=True,
                source_id=SOURCE,
                raw_tick=0.01,
                state="READY",
                factor_coverage="EXPLICIT",
                latest_completed15=bars[-1]["timestamp"],
            )
            with store.conn:
                store._put(info)
                store._write(symbol, bars)
            written[0] = i + 1
        elapsed = perf_counter() - began
    finally:
        stop.set()
        observer.join()
        store.close()
    store = Store(path)
    try:
        actual = store.conn.execute("SELECT count(*) FROM bar15").fetchone()[0]
        maximum = store.conn.execute(
            "SELECT max(n) FROM (SELECT count(*) n FROM bar15 GROUP BY symbol)"
        ).fetchone()[0]
        integrity = store.conn.execute("PRAGMA quick_check").fetchone()[0]
    finally:
        store.close()
    result = dict(
        synthetic=True,
        symbols=count,
        rows=actual,
        max_rows_per_symbol=maximum,
        write_seconds=elapsed,
        rows_per_second=actual / elapsed,
        reader_samples=len(latencies),
        reader_p95_ms=statistics.quantiles(latencies, n=100)[94],
        read_errors=errors,
        peak_rss_bytes=max(rss),
        sqlite_bytes=Path(path).stat().st_size,
        restart_integrity=integrity,
        retention_pass=maximum == 250 and actual == count * 250,
    )
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--symbols", type=int, default=5600)
    args = parser.parse_args()
    print(json.dumps(run(args.database, args.symbols), indent=2))
