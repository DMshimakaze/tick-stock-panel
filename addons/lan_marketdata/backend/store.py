"""One bounded DuckDB. Raw bars, source receipts and watermarks commit together."""

from contextlib import contextmanager
import json
from pathlib import Path
from threading import RLock

import duckdb
import pandas as pd

from .grid import CAPACITY, MAS, aggregate, project, two_day, points, now_cn, basis_key


def encoded(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = RLock()
        self.conn = duckdb.connect(
            str(path), config={"threads": 2, "memory_limit": "1GB"}
        )
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS meta(key VARCHAR PRIMARY KEY,payload VARCHAR NOT NULL)"
        )
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS instrument(symbol VARCHAR PRIMARY KEY,payload VARCHAR NOT NULL)"
        )
        self.conn.execute("""CREATE TABLE IF NOT EXISTS bars(symbol VARCHAR,timeframe VARCHAR,stamp VARCHAR,
                             payload VARCHAR NOT NULL,PRIMARY KEY(symbol,timeframe,stamp))""")

    @contextmanager
    def transaction(self):
        with self.lock:
            self.conn.execute("BEGIN TRANSACTION")
            try:
                yield
                self.conn.execute("COMMIT")
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise

    def meta(self, key, default=None):
        with self.lock:
            row = self.conn.execute(
                "SELECT payload FROM meta WHERE key=?", [key]
            ).fetchone()
            return json.loads(row[0]) if row else default

    def set_meta(self, key, value):
        self.conn.execute(
            "INSERT OR REPLACE INTO meta VALUES(?,?)", [key, encoded(value)]
        )

    def instruments(self):
        with self.lock:
            return {
                s: json.loads(p)
                for s, p in self.conn.execute(
                    "SELECT * FROM instrument ORDER BY symbol"
                ).fetchall()
            }

    def instrument(self, symbol):
        with self.lock:
            row = self.conn.execute(
                "SELECT payload FROM instrument WHERE symbol=?", [symbol]
            ).fetchone()
            return json.loads(row[0]) if row else None

    def universe(self):
        # Indicator close windows and event chains are source-owned state,
        # never part of a market-wide metadata response.
        with self.lock:
            rows = self.conn.execute("""SELECT symbol,json_merge_patch(payload,'{"indicators":null,"events":null}')
                                      FROM instrument ORDER BY symbol""").fetchall()
            return {s: json.loads(p) for s, p in rows}

    def universe_page(self, offset, limit):
        with self.lock:
            total = self.conn.execute("SELECT count(*) FROM instrument").fetchone()[0]
            rows = self.conn.execute(
                """SELECT json_merge_patch(payload,'{"indicators":null,"events":null}')
                                      FROM instrument ORDER BY symbol LIMIT ? OFFSET ?""",
                [limit, offset],
            ).fetchall()
            return total, [json.loads(r[0]) for r in rows]

    def put_instrument(self, row):
        self.conn.execute(
            "INSERT OR REPLACE INTO instrument VALUES(?,?)",
            [row["symbol"], encoded(row)],
        )

    def save_master(self, rows, overview, sessions):
        with self.transaction():
            old = self.instruments()
            generation = self.meta("generation", 0) + int(
                {s for s, r in old.items() if r.get("eligible")}
                != {r["symbol"] for r in rows if r.get("eligible")}
            )
            merged = []
            for row in rows:
                previous = old.pop(row["symbol"], {})
                merged.append(dict(previous, **row))
            for row in old.values():
                merged.append(dict(row, eligible=False))
            frame = pd.DataFrame(
                [(r["symbol"], encoded(r)) for r in merged],
                columns=["symbol", "payload"],
            )
            self.conn.register("master_incoming", frame)
            try:
                self.conn.execute(
                    "INSERT OR REPLACE INTO instrument SELECT * FROM master_incoming"
                )
            finally:
                self.conn.unregister("master_incoming")
            self.set_meta("generation", generation)
            self.set_meta("universe", overview)
            self.set_meta(
                "sessions", sorted(set(self.meta("sessions", [])) | set(sessions))
            )
            self.set_meta("master_day", str(now_cn().date()))

    def raw_rows(self, symbol, tf, asof="9999", limit=256):
        rows = self.conn.execute(
            """SELECT payload FROM bars WHERE symbol=? AND timeframe=? AND stamp<=?
                                   ORDER BY stamp DESC LIMIT ?""",
            [symbol, tf, asof, limit],
        ).fetchall()
        return [json.loads(row[0]) for row in reversed(rows)]

    def _write(self, symbol, frames):
        rows = [
            (symbol, tf, r["timestamp"], encoded(r))
            for tf, bars in frames.items()
            for r in bars
        ]
        if rows:
            incoming = pd.DataFrame(
                rows, columns=["symbol", "timeframe", "stamp", "payload"]
            )
            self.conn.register("incoming", incoming)
            try:
                self.conn.execute("INSERT OR REPLACE INTO bars SELECT * FROM incoming")
            finally:
                self.conn.unregister("incoming")
        for tf, capacity in CAPACITY.items():
            self.conn.execute(
                """DELETE FROM bars WHERE symbol=? AND timeframe=? AND stamp IN
                (SELECT stamp FROM bars WHERE symbol=? AND timeframe=? ORDER BY stamp DESC OFFSET ?)""",
                [symbol, tf, symbol, tf, capacity],
            )

    def indicators(self, symbol, frames, info, asof, sessions):
        """Persist <=233 raw closes per period, including 5m's shorter K window.

        A new batch touches bounded windows only. Factor changes reproject these
        real closes; an affected MA without its full causal window stays null.
        """
        snapshots = info.get("indicators", {})
        for tf, incoming in frames.items():
            if not incoming:
                continue
            window = {r["timestamp"]: r for r in snapshots.get(tf, [])}
            window.update(
                {
                    r["timestamp"]: r
                    for r in self.raw_rows(symbol, tf, limit=CAPACITY[tf])
                }
            )
            window.update({r["timestamp"]: r for r in incoming})
            ordered = [window[t] for t in sorted(window)]
            projected = {
                r["timestamp"]: r
                for r in project(
                    ordered, info.get("events"), asof, sessions=sessions, timeframe=tf
                )
            }
            for row in incoming:
                row["_ma"] = {
                    f"ma{n}": projected[row["timestamp"]][f"ma{n}"] for n in MAS
                }
                row["_ma_basis"] = basis_key(
                    info.get("events"), asof, info.get("basis_revision", 0)
                )
            snapshots[tf] = [
                dict(timestamp=r["timestamp"], close=r["close"]) for r in ordered[-233:]
            ]
        info["indicators"] = snapshots

    def accept(
        self,
        symbol,
        received,
        *,
        sessions,
        start,
        end,
        now,
        events=None,
        factor_response=False,
        checkpoint=None,
        source="QUANTDASH_RAW_5M",
        receipt=None,
    ):
        """Input may exceed a window during warmup; disk retention never does.

        Corrections outside the retained raw tail require a bounded source replay.
        Until it completes consumers see REBUILD_PENDING, not stale valid MA.
        """
        with self.transaction():
            info = self.instrument(symbol)
            if info is None:
                raise ValueError("Metadata must precede bars")
            old_raw = self.raw_rows(symbol, "5m", limit=144)
            old_by_time = {
                r["timestamp"]: {
                    k: r.get(k)
                    for k in ("open", "high", "low", "close", "volume", "amount")
                }
                for r in old_raw
            }
            raw = dict(old_by_time)
            raw.update(received)
            frames = aggregate(raw, sessions, end)
            correction = any(
                t <= info.get("latest5", "") and t not in old_by_time for t in received
            )
            changed = any(
                t in old_by_time and old_by_time[t] != r for t, r in received.items()
            )
            if changed:
                # Invalidate precisely the corrected source buckets before replacing.
                for tf, count in (
                    ("15m", 3),
                    ("30m", 6),
                    ("60m", 12),
                    ("120m", 24),
                    ("daily", 48),
                ):
                    for t, r in received.items():
                        if t not in old_by_time or old_by_time[t] == r:
                            continue
                        minute = int(t[11:13]) * 60 + int(t[14:16])
                        base = 570 if minute <= 690 else 780
                        target = (
                            t[:10] + " 15:00:00"
                            if tf == "daily"
                            else f"{t[:10]} {(base + ((minute - base + count * 5 - 1) // (count * 5)) * count * 5) // 60:02}:{(base + ((minute - base + count * 5 - 1) // (count * 5)) * count * 5) % 60:02}:00"
                        )
                        self.conn.execute(
                            "DELETE FROM bars WHERE symbol=? AND timeframe=? AND stamp=?",
                            [symbol, tf, target],
                        )
                info["rebuild_pending"] = True
            if factor_response:
                if info.get("events") != events:
                    info["basis_revision"] = info.get("basis_revision", 0) + 1
                info.update(
                    events=events,
                    factors_day=now[:10],
                    factor_source="QUANTDASH_EX_FACTORS_EXPLICIT_SYMBOL",
                )
            expected = points(sessions, start, end)
            missing = [t for t in expected if t not in raw]
            if len(received) >= 3072 and not missing:
                info["rebuild_pending"] = False
            elif correction and len(received) < 3072:
                info["rebuild_pending"] = True
            # Already derived longer history survives eviction of its raw inputs.
            daily = {r["timestamp"]: r for r in self.raw_rows(symbol, "daily")}
            daily.update({r["timestamp"]: r for r in frames["daily"]})
            frames["2d"] = two_day(list(daily.values()), sessions)
            for bars in frames.values():
                for bar in bars:
                    if source != "QUANTDASH_RAW_5M":
                        bar["source"] = source
                    bar["available_at"] = bar["timestamp"]
            self.indicators(symbol, frames, info, now, sessions)
            self._write(symbol, frames)
            latest = max(received, default=info.get("latest5", ""))
            info.update(
                latest5=max(info.get("latest5", ""), latest),
                source_asof=now,
                attempted=end,
                gap_count=len(missing),
                gaps=missing[:64],
                retry_at="" if not missing else str(now_cn()),
                revision=info.get("revision", 0) + 1,
                correction=changed or correction,
                warm_count=len(self.raw_rows(symbol, "60m")),
                warm_target=256,
            )
            if not missing and len(self.raw_rows(symbol, "15m")) >= 56:
                info["cold_done"] = True
            if not missing:
                if not info.get("warm_start") or start < info["warm_start"]:
                    info["warm_start"] = start
            if receipt:
                info.update(receipt)
                info["cold_done"] = info.get("cold_done", False) or (
                    receipt.get("stage") == "COLD" and not missing
                )
                info.pop("request_error", None)
            self.put_instrument(info)
            self.set_meta("last_commit", now)
            if checkpoint is not None:
                self.set_meta("source_checkpoint", checkpoint)
        return info

    def failed(self, symbols, *, target, now, message, checkpoint):
        from datetime import timedelta
        from .grid import wall

        with self.transaction():
            for symbol in symbols:
                info = self.instrument(symbol)
                attempts = info.get("attempts", 0) + 1
                info.update(
                    attempts=attempts,
                    live_attempt=target,
                    request_error=message,
                    retry_at=str(
                        wall(now)
                        + timedelta(seconds=min(3600, 60 * 2 ** min(attempts, 5)))
                    ),
                )
                self.put_instrument(info)
            self.set_meta("source_checkpoint", checkpoint)

    def read(self, symbol, tf, asof, limit=256):
        with self.lock:
            info = self.instrument(symbol)
            if info is None:
                return dict(
                    symbol=symbol,
                    timeframe=tf,
                    bars=[],
                    state="METADATA_PENDING",
                    row_count=0,
                    capacity=CAPACITY[tf],
                )
            raw = self.raw_rows(symbol, tf, asof, CAPACITY[tf])
            bars = project(
                raw,
                info.get("events") if info.get("factors_day") else None,
                asof,
                sessions=self.meta("sessions", []),
                timeframe=tf,
            )
            for bar in bars:
                if bar.get("_ma_basis") == basis_key(
                    info.get("events"), asof, info.get("basis_revision", 0)
                ):
                    bar.update(bar.get("_ma", {}))
                bar.pop("_ma", None)
                bar.pop("_ma_basis", None)
            state = (
                "REBUILD_PENDING"
                if info.get("rebuild_pending")
                else "FACTOR_PENDING"
                if info.get("events") is None
                else "GAP"
                if info.get("gap_count")
                else "READY"
                if bars
                else "WARMING"
            )
            if state in ("REBUILD_PENDING", "FACTOR_PENDING"):
                for bar in bars:
                    bar["valid"] = False
                    for n in MAS:
                        bar[f"ma{n}"] = None
            return dict(
                symbol=symbol,
                name=info.get("name", symbol),
                timeframe=tf,
                bars=bars[-limit:],
                state=state,
                row_count=len(raw),
                capacity=CAPACITY[tf],
                latest_completed_at=raw[-1]["timestamp"] if raw else None,
                source_asof=info.get("source_asof"),
                price_basis="QFQ" if info.get("events") is not None else "RAW",
                basis_id=f"QFQ:{asof[:10]}:{info.get('basis_revision', 0)}",
                basis_revision=info.get("basis_revision", 0),
                revision=info.get("revision", 0),
                raw_tick=info.get("raw_tick"),
                asset_type=info.get("source_type"),
                volume_unit="shares",
                factor_source=info.get("factor_source"),
                factor_coverage="EXPLICIT_SYMBOL_RESPONSE"
                if info.get("events") is not None
                else "UNVERIFIED",
                gap_count=info.get("gap_count", 0),
                gaps=info.get("gaps", []),
                warm_count=info.get("warm_count", 0),
                warm_target=256,
                correction=info.get("correction", False),
                eligible=info.get("eligible", False),
                listing_status=info.get("listing_status", "NOT_PROVIDED"),
            )

    def inventory(self, symbol=None):
        with self.lock:
            sql = "SELECT symbol,timeframe,count(*),min(stamp),max(stamp) FROM bars"
            rows = self.conn.execute(
                sql
                + (" WHERE symbol=?" if symbol else "")
                + " GROUP BY symbol,timeframe ORDER BY symbol,timeframe",
                [symbol] if symbol else [],
            ).fetchall()
            return [
                dict(
                    symbol=s,
                    timeframe=tf,
                    row_count=n,
                    capacity=CAPACITY[tf],
                    earliest=a,
                    latest=b,
                )
                for s, tf, n, a, b in rows
            ]

    def close(self):
        with self.lock:
            self.conn.execute("CHECKPOINT")
            self.conn.close()
