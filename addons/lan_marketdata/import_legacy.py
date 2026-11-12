"""Idempotent offline import; DuckDB is read-only, destination has one writer lease."""

import argparse
import json
from math import isfinite
from pathlib import Path
import sys

import duckdb

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from addons.lan_marketdata.backend.grid import FIELDS, LEGACY, now_cn, valid_five, wall
from addons.lan_marketdata.backend.store import Store


def migrate(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if source == destination:
        raise ValueError("Source and destination must differ")
    old = duckdb.connect(
        str(source), read_only=True, config={"threads": 2, "memory_limit": "4GB"}
    )
    store = Store(destination)
    counts = dict(imported=0, skipped=0, rejected_bars=0, bars15=0, source_id=LEGACY)
    try:
        row = old.execute("SELECT payload FROM meta WHERE key='sessions'").fetchone()
        if not row:
            raise ValueError("Legacy verified sessions are required")
        with store.conn:
            store.set_sessions(json.loads(row[0]))
        # Drop large old indicator snapshots at SQL projection, never deserialize them.
        metadata = old.execute("""SELECT symbol,json_merge_patch(payload,'{"indicators":null,"gaps":null}')
                                  FROM instrument ORDER BY symbol""").fetchall()
        for symbol, payload in metadata:
            info = json.loads(payload)
            if store.instrument(symbol) is not None:
                counts["skipped"] += 1
                continue
            bars = []
            for (raw,) in old.execute(
                """SELECT payload FROM bars WHERE symbol=? AND timeframe='15m'
                                         ORDER BY stamp DESC LIMIT 250""",
                [symbol],
            ).fetchall()[::-1]:
                b = json.loads(raw)
                at = wall(b["timestamp"])
                minute = at.hour * 60 + at.minute
                if (
                    b.get("source_count") != 3
                    or not b.get("complete")
                    or not valid_five(b)
                    or at.second
                    or at.microsecond
                    or minute not in (*range(585, 691, 15), *range(795, 901, 15))
                ):
                    counts["rejected_bars"] += 1
                    continue
                bars.append(
                    dict(
                        timestamp=str(at),
                        **{k: b.get(k) for k in FIELDS},
                        source_count=3,
                        complete=True,
                        source_id=LEGACY,
                    )
                )
            events = info.get("events") if info.get("factors_day") else None
            if events is not None and any(
                not isfinite(e["ex_factor"]) or e["ex_factor"] <= 0 for e in events
            ):
                events = None
            store.import_legacy(info, bars, events, str(now_cn()))
            counts["imported"] += 1
            counts["bars15"] += len(bars)
        with store.conn:
            overview = json.loads(
                old.execute("SELECT payload FROM meta WHERE key='universe'").fetchone()[
                    0
                ]
            )
            store.set_meta("universe", overview)
            store.set_meta("generation", 1)
            store.set_meta(
                "data_revision",
                store.meta("data_revision", 0) + int(counts["imported"] > 0),
            )
            store.set_meta("legacy_import", dict(counts, complete=True))
        if store.conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise RuntimeError("SQLite integrity check failed")
        return counts
    finally:
        store.close()
        old.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--destination", required=True)
    args = parser.parse_args()
    print(json.dumps(migrate(args.source, args.destination), ensure_ascii=False))
