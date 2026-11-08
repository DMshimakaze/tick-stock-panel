"""Explicit offline import from consistent SQLite backups. Originals stay read-only.

Run as: python -m addons.lan_marketdata.import_legacy --destination ...
  --ma55-backup ... --kline-backup ...
Stop the new writer before importing; its OS lease is also acquired here.
"""

import argparse
import json
from pathlib import Path
import sqlite3

from .backend.grid import points, valid_five, now_cn, two_day, wall, FIVE_MINUTES
from .backend.lease import ProcessLease
from .backend.store import Store


def readonly(path):
    conn = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def ma55_import(store, path, symbols=None):
    conn = readonly(path)
    try:
        meta = {
            r["key"]: json.loads(r["payload"])
            for r in conn.execute("SELECT * FROM runtime_meta")
        }
        if meta.get("data_mode") != "QUANTDASH_PRO_RAW_5M_TO_QFQ_15M_V1":
            raise ValueError("Legacy MA55 raw-price provenance is not established")
        sessions = meta["sessions"]
        master = [
            json.loads(r["payload"])
            for r in conn.execute("SELECT * FROM security_master ORDER BY symbol")
        ]
        if not store.instruments():
            store.save_master(master, meta["universe"], sessions)
            with store.transaction():
                store.set_meta("generation", meta.get("generation", 1))
        counts = dict(symbols=0, imported_15m=0, rejected=0)
        for info in master:
            symbol = info["symbol"]
            if not info.get("eligible") or (
                symbols is not None and symbol not in symbols
            ):
                continue
            journal = conn.execute(
                "SELECT payload FROM collection_journal WHERE symbol=?", (symbol,)
            ).fetchone()
            if not journal:
                continue
            journal = json.loads(journal[0])
            bars = []
            for row in conn.execute(
                "SELECT payload FROM recent_bar WHERE symbol=? ORDER BY bar_end",
                (symbol,),
            ):
                bar = json.loads(row[0])
                at = bar["timestamp"]
                if (
                    bar.get("source_count") != 3
                    or not valid_five(bar)
                    or at not in points(sessions, at, at, step=15)
                    or at > (journal.get("received") or "")
                ):
                    counts["rejected"] += 1
                    continue
                bars.append(
                    dict(
                        bar,
                        volume=bar["volume"] * 100,
                        complete=True,
                        amount=None,
                        available_at=at,
                        source="LEGACY_MA55_VERIFIED_RAW_3X5M",
                    )
                )
            if bars:
                existing = store.instrument(symbol) or info
                with store.transaction():
                    store._write(symbol, {"15m": bars})
                    existing.update(
                        events=journal.get("factors"),
                        factors_day=journal.get("factors_day"),
                        factor_source="LEGACY_QUANTDASH_EXPLICIT_SYMBOL_RECEIPT",
                        latest5=journal.get("received") or "",
                        live_attempt=journal.get("last_live_target") or "",
                        source_asof=journal.get("received"),
                        revision=existing.get("revision", 0) + 1,
                        basis_revision=existing.get("basis_revision", 0),
                        cold_done=len(bars) >= 56,
                        gap_count=int(bool(journal.get("gap"))),
                        gaps=[],
                        legacy_import="MA55_RAW_15M_VERIFIED",
                        warm_count=0,
                    )
                    store.put_instrument(existing)
                counts["symbols"] += 1
                counts["imported_15m"] += min(256, len(bars))
        return counts
    finally:
        conn.close()


def kline_import(store, path, symbols=None):
    conn = readonly(path)
    try:
        sessions = json.loads(
            conn.execute(
                "SELECT value FROM runtime_meta WHERE key='sessions'"
            ).fetchone()[0]
        )
        counts = dict(symbols=0, imported_5m=0, rejected=0)
        for tracked in conn.execute("SELECT * FROM tracked_symbol ORDER BY symbol"):
            symbol = tracked["symbol"]
            if symbols is not None and symbol not in symbols:
                continue
            metadata = json.loads(tracked["metadata"] or "{}")
            if metadata.get("symbol") != symbol or metadata.get("type") not in (
                "stock",
                "etf",
            ):
                counts["rejected"] += 1
                continue
            rows = conn.execute(
                "SELECT * FROM bar_5m WHERE symbol=? ORDER BY end_time", (symbol,)
            ).fetchall()
            if not rows:
                continue
            raw = {}
            for row in rows:
                bar = {
                    k: row[k]
                    for k in ("open", "high", "low", "close", "volume", "amount")
                }
                if (
                    row["source"] != "QuantDash"
                    or row["basis"] != "RAW"
                    or not valid_five(bar)
                    or row["end_time"][:10] not in sessions
                    or wall(row["end_time"]).second
                    or wall(row["end_time"]).microsecond
                    or wall(row["end_time"]).hour * 60 + wall(row["end_time"]).minute
                    not in FIVE_MINUTES
                ):
                    counts["rejected"] += 1
                    continue
                raw[row["end_time"]] = dict(bar, volume=bar["volume"] * 100)
            if not raw:
                continue
            info = store.instrument(symbol) or dict(
                symbol=symbol,
                name=metadata.get("name", symbol),
                raw_tick=metadata.get("ext", {}).get("tick_size"),
                source_type=metadata["type"],
                eligible=metadata["type"] == "stock",
            )
            with store.transaction():
                store.put_instrument(info)
                store.set_meta(
                    "sessions", sorted(set(store.meta("sessions", [])) | set(sessions))
                )
            store.accept(
                symbol,
                raw,
                sessions=sessions,
                start=min(raw),
                end=max(raw),
                now=str(now_cn()),
                events=json.loads(tracked["events"]) if tracked["events"] else None,
                factor_response=True,
                source="LEGACY_KLINE_VERIFIED_RAW_5M",
            )
            with store.transaction():
                info = store.instrument(symbol)
                info.update(cold_done=True, legacy_import="KLINE_RAW_5M_VERIFIED")
                daily = []
                for row in conn.execute(
                    "SELECT * FROM bar_daily WHERE symbol=? ORDER BY end_time",
                    (symbol,),
                ):
                    bar = {
                        k: row[k]
                        for k in ("open", "high", "low", "close", "volume", "amount")
                    }
                    if (
                        row["basis"] == "RAW"
                        and row["source"] == "QuantDash"
                        and valid_five(bar)
                        and row["end_time"][:10] in sessions
                        and row["end_time"] <= max(raw)
                    ):
                        daily.append(
                            dict(
                                bar,
                                volume=bar["volume"] * 100,
                                timestamp=row["end_time"],
                                source_count=1,
                                source="LEGACY_QUANTDASH_COMPLETED_DAILY",
                                complete=True,
                                available_at=row["end_time"],
                            )
                        )
                frames = {
                    "daily": daily,
                    "2d": two_day(daily, store.meta("sessions", [])),
                }
                store.indicators(
                    symbol, frames, info, str(now_cn()), store.meta("sessions", [])
                )
                store._write(symbol, frames)
                store.put_instrument(info)
            counts["symbols"] += 1
            counts["imported_5m"] += min(144, len(raw))
        return counts
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", required=True)
    parser.add_argument("--ma55-backup")
    parser.add_argument("--kline-backup")
    parser.add_argument(
        "--symbols", help="Optional comma-separated bounded acceptance symbols"
    )
    args = parser.parse_args()
    path = Path(args.destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    lease = ProcessLease(path.parent / "writer.lock")
    store = Store(path)
    try:
        symbols = set(args.symbols.split(",")) if args.symbols else None
        results = {}
        if args.ma55_backup:
            results["ma55"] = ma55_import(store, args.ma55_backup, symbols)
        if args.kline_backup:
            results["kline"] = kline_import(store, args.kline_backup, symbols)
        print(json.dumps(results, ensure_ascii=False))
    finally:
        store.close()
        lease.close()


if __name__ == "__main__":
    main()
