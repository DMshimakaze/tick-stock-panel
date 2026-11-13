"""Bounded after-refactor smoke: <=12 supplier HTTP, zero SDK retries.

Reads the existing owner database, writes only a new private validation database.
Run after the complete offline integration load. Never starts a universe pull.
"""

import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import sys


def run(data_dir, output):
    from dotenv import dotenv_values
    from addons.lan_ma55.backend.collector import Collector, SourceError
    from addons.lan_ma55.backend.market_grid import SOURCE, latest_point, now_cn
    from addons.lan_ma55.backend.request_budget import (
        CollectionStopped,
        RequestDeferred,
    )
    from addons.lan_ma55.backend.runtime import classify
    from addons.lan_ma55.backend.unified_store import UnifiedStore
    from addons.lan_ma55.migrate_unified import row_digests

    data_dir, output = Path(data_dir).resolve(), Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    source = data_dir / "user_data/lan_ma55/state.sqlite3"
    key = os.environ.get("TICKFLOW_API_KEY") or dotenv_values(
        data_dir.parent / ".env"
    ).get("TICKFLOW_API_KEY")
    if not key:
        raise ValueError("Existing authorized TickFlow environment key is unavailable")
    db = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    with closing(db):
        sessions = json.loads(
            db.execute("SELECT payload FROM meta WHERE key='sessions'").fetchone()[0]
        )
        historical = db.execute(
            """SELECT e.symbol FROM signal_event e JOIN instrument_state i USING(symbol)
            WHERE json_extract(e.payload,'$.source_id')=? AND i.source_id=?
            ORDER BY e.confirmed_at DESC LIMIT 1""",
            (SOURCE, SOURCE),
        ).fetchone()
        buy_symbol = historical[0] if historical else "001317.SZ"
        corporate = db.execute(
            """SELECT f.symbol FROM factor_event f JOIN instrument_state i USING(symbol)
            WHERE i.source_id=? AND f.symbol NOT IN (?,?) ORDER BY f.day DESC LIMIT 1""",
            (SOURCE, buy_symbol, "000001.SZ"),
        ).fetchone()
        symbols = list(
            dict.fromkeys(
                [
                    "000001.SZ",
                    buy_symbol,
                    corporate[0] if corporate else "002152.SZ",
                    "688827.SH",
                ]
            )
        )
        marks = ",".join("?" for _ in symbols)
        old_events = [
            tuple(r)
            for r in db.execute(
                f"SELECT * FROM signal_event WHERE symbol IN ({marks})", symbols
            )
        ]
        old_risks = [
            tuple(r)
            for r in db.execute(
                f"SELECT r.* FROM event_risk r JOIN signal_event e USING(event_id) WHERE e.symbol IN ({marks})",
                symbols,
            )
        ]
    counts = [0]
    collector = Collector(key, output / "sdk", limit=8)

    def cap(request):
        if counts[0] >= 12:
            raise CollectionStopped()
        counts[0] += 1

    collector.tf._client._client.event_hooks["request"].insert(0, cap)
    now = str(now_cn())
    target = latest_point(sessions, now)
    first = [d for d in sessions if d <= target[:10]][-18] + " 09:35:00"
    result = dict(
        actual_supplier_http=True,
        max_requests=12,
        max_retries=0,
        asof=target,
        roles=dict(
            ordinary="000001.SZ",
            historical_buy=buy_symbol,
            corporate_action=corporate[0] if corporate else "NOT_VERIFIED",
            new_or_factor_gap="688827.SH",
        ),
        symbols=symbols,
    )
    try:
        metadata, overview = classify(collector.metadata(symbols), target[:10])
        factors = collector.factors(symbols)
        received, errors = collector.fetch_five(symbols, first, target, cold=True)
        with UnifiedStore(output / "smoke.sqlite3") as owner:
            owner.refresh_master(metadata, overview, sessions)
            with owner.conn:
                owner.conn.executemany(
                    "INSERT INTO signal_event VALUES(?,?,?,?,?,?,?,?,?,?)", old_events
                )
                owner.conn.executemany(
                    "INSERT INTO event_risk VALUES(?,?,?)", old_risks
                )
            old = row_digests(owner.conn, "signal_event")
            for symbol in symbols:
                if owner.market.instrument(symbol) is None:
                    continue
                error = errors.get(symbol)
                owner.accept_market(
                    symbol,
                    received.get(symbol, {}),
                    sessions=sessions,
                    start=first,
                    end=target,
                    now=now,
                    events=factors.get(symbol),
                    factor_response=symbol in factors,
                    rebuild=True,
                    live_after=now,
                    error=error["code"] if error else None,
                )
            result["rows"] = [
                dict(
                    symbol=s,
                    rows=len(owner.market.raw_rows(s)),
                    state=(owner.market.instrument(s) or {}).get(
                        "state", "METADATA_PENDING"
                    ),
                    factor_events=len(factors[s])
                    if factors.get(s) is not None
                    else None,
                    source_asof=(owner.market.instrument(s) or {}).get(
                        "latest_completed15"
                    ),
                    analysis_asof=(owner.records([s])[0].get(s) or {})
                    .get("state", {})
                    .get("data_asof"),
                )
                for s in symbols
            ]
            after = row_digests(owner.conn, "signal_event")
            result["original_event_rows_equal"] = all(
                after.get(k) == v for k, v in old.items()
            )
            result["original_events"] = len(old)
            result["new_replay_events"] = len(after) - len(old)
            result["quick_check"] = owner.conn.execute("PRAGMA quick_check").fetchone()[
                0
            ]
            result["source_errors"] = {
                s: dict(code=e["code"], invalid_rows=len(e["timestamps"] or []))
                for s, e in errors.items()
            }
            owner.save_requests(list(collector.observations), "BOUNDED_LIVE_SMOKE")
            result["status"] = (
                "PASS"
                if not errors
                and result["original_event_rows_equal"]
                and all(r["state"] == "READY" for r in result["rows"])
                else "SOURCE_LIMITATIONS"
            )
    except (SourceError, RequestDeferred, CollectionStopped, ValueError) as exc:
        result.update(
            status="STOPPED",
            error=type(exc).__name__,
            http_status=getattr(exc, "status_code", None),
        )
    finally:
        collector.close()
    result.update(
        http_requests=counts[0],
        metrics=dict(collector.metrics),
        observations=list(collector.observations),
    )
    (output / "live-metrics.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )
    return result


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(root))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--output", required=True)
    print(json.dumps(run(**vars(parser.parse_args())), indent=2))
