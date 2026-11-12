"""Offline migration from consistent snapshots; never replaces an online file.

The operator stops TSP, backs up both databases, then gives a new output path.
The existing MA55 snapshot is the base; all its business tables stay intact.
"""

import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3

from .backend.unified_store import UnifiedStore


def row_digests(connection, table):
    if table not in ("signal_event", "event_risk"):
        raise ValueError("Only immutable migration evidence tables are accepted")
    return {
        row[0]: hashlib.sha256(
            json.dumps(tuple(row), ensure_ascii=False, separators=(",", ":")).encode()
        ).hexdigest()
        for row in connection.execute(f"SELECT * FROM {table} ORDER BY 1")
    }


def readonly(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)


def migrate(state, market, output):
    state, market, output = (Path(p).resolve() for p in (state, market, output))
    if output in (state, market):
        raise ValueError("Output must be separate from both source snapshots")
    with closing(readonly(state)) as old:
        if old.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("MA55 snapshot failed quick_check")
        before = {t: row_digests(old, t) for t in ("signal_event", "event_risk")}
        if not output.exists():
            output.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(output)) as target:
                old.backup(target)
    with closing(readonly(market)) as source:
        if source.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("Market snapshot failed quick_check")
    with UnifiedStore(output) as owner:
        db = owner.conn
        db.execute("ATTACH DATABASE ? AS old_market", (market.as_uri() + "?mode=ro",))
        with db:
            if not owner.meta("market_migrated_v1"):
                for table in (
                    "meta",
                    "instrument_state",
                    "bar15",
                    "tail5",
                    "factor_event",
                ):
                    db.execute(
                        f"INSERT OR IGNORE INTO main.{table} SELECT * FROM old_market.{table}"
                    )
                owner.market.set_meta("metadata_day", owner.market.meta("master_day"))
                owner.set_meta("market_migrated_v1", True)
            # These retired v0.3 collector caches are already in the state backup.
            # The current runtime has no reader for them; keep one active bar table.
            for table in ("recent_bar", "collection_journal", "collection_batch"):
                db.execute(f"DROP TABLE IF EXISTS {table}")
        db.execute("DETACH DATABASE old_market")
        for table, expected in before.items():
            actual = row_digests(db, table)
            if any(actual.get(key) != value for key, value in expected.items()):
                raise ValueError(f"Original {table} content changed during migration")
        capacity = (
            db.execute(
                "SELECT max(n) FROM (SELECT count(*) n FROM bar15 GROUP BY symbol)"
            ).fetchone()[0]
            or 0
        )
        tails = (
            db.execute(
                "SELECT max(n) FROM (SELECT count(*) n FROM tail5 GROUP BY symbol)"
            ).fetchone()[0]
            or 0
        )
        if capacity > 250 or tails > 2:
            raise ValueError("Source snapshot violates rolling capacity")
        check = db.execute("PRAGMA quick_check").fetchone()[0]
        if check != "ok":
            raise ValueError("Unified output failed quick_check")
        counts = {
            t: db.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
            for t in (
                "bar15",
                "tail5",
                "instrument_state",
                "signal_event",
                "event_risk",
            )
        }
        owner.checkpoint()
    return dict(
        quick_check=check,
        original_rows_equal={t: len(r) for t, r in before.items()},
        counts=counts,
        max_bar15=capacity,
        max_tail5=tails,
        migration_version=1,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("state", "market", "output"):
        parser.add_argument("--" + name, required=True)
    print(json.dumps(migrate(**vars(parser.parse_args())), indent=2))
