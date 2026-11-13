"""Offline dataset consumer with one writer. No scheduler or market-data adapter.

The running TSP extension remains BLOCKED: this service cannot obtain host data.
"""

from concurrent.futures import Future
from datetime import datetime
from queue import Queue
from threading import Thread
from time import perf_counter

from .engine import RULE_VERSION
from .pipeline import (
    BEIJING,
    END_MINUTES,
    empty_record,
    invalidate,
    process_symbol,
    wall,
)
from .store import StateWriter

BLOCKED = "FULL_MARKET_BLOCKED_BY_HOST_CONTRACT"


class OfflineService:
    def __init__(self, path, *, retention_sessions=60):
        if not 20 <= retention_sessions <= 252:
            raise ValueError("retention_sessions must be between 20 and 252")
        self.retention_sessions = retention_sessions
        self.queue = Queue(maxsize=2)
        self.closed = False
        ready = Future()
        self.thread = Thread(
            target=self._worker,
            args=(path, ready),
            name="lan-ma55-offline-writer",
            daemon=True,
        )
        self.thread.start()
        ready.result()

    def _worker(self, path, ready):
        try:
            store = StateWriter(path)
        except Exception as error:
            ready.set_exception(error)
            return
        ready.set_result(None)
        try:
            while True:
                job = self.queue.get()
                if job is None:
                    break
                arguments, future = job
                try:
                    future.set_result(self._scan(store, **arguments))
                except Exception as error:
                    future.set_exception(error)
        finally:
            store.close()

    def scan(
        self,
        *,
        universe,
        inputs,
        bar_end,
        sessions,
        source_asof,
        first_seen_at,
        source_healthy=True,
    ):
        """Explicit offline universe: [{symbol, asset_type, raw_tick}].

        inputs yields <=128 (symbol, completed15m_rows) pairs per batch. The input
        iterator runs in the sole writer; it must not fetch vendor/host data.
        """
        if self.closed:
            raise RuntimeError("Offline writer is closed")
        future = Future()
        self.queue.put(
            (
                dict(
                    universe=universe,
                    inputs=inputs,
                    bar_end=bar_end,
                    sessions=sessions,
                    source_asof=source_asof,
                    first_seen_at=first_seen_at,
                    source_healthy=source_healthy,
                ),
                future,
            )
        )
        return future.result()

    def _scan(
        self,
        store,
        *,
        universe,
        inputs,
        bar_end,
        sessions,
        source_asof,
        first_seen_at,
        source_healthy,
    ):
        started = perf_counter()
        end = wall(bar_end)
        if (
            end.second
            or end.microsecond
            or end.hour * 60 + end.minute not in END_MINUTES
        ):
            raise ValueError("Cycle must name a completed 15m end")
        if str(end.date()) not in sessions or end.weekday() >= 5:
            raise ValueError("Cycle is outside the supplied exchange sessions")
        if end > wall(source_asof):
            raise ValueError("Source has not reached the completed point")
        symbols = {row["symbol"]: row for row in universe}
        if (
            not symbols
            or len(symbols) != len(universe)
            or any(row["asset_type"] != "stock" for row in universe)
        ):
            raise ValueError(
                "Offline A-share universe must be nonempty, unique and explicitly stock"
            )
        identity = f"OFFLINE:{RULE_VERSION}:{end}"
        cycle = store.cycle(identity)
        if cycle and cycle["expected_symbols"] != len(symbols):
            raise ValueError("Cannot change the denominator of a resumed cycle")
        if cycle and cycle["status"] == "COMPLETE":
            return {**cycle, "new_events_this_call": 0}
        if cycle is None:
            cycle = dict(
                cycle_id=identity,
                trade_date=str(end.date()),
                bar_end=str(end),
                mode="OFFLINE",
                expected_symbols=len(symbols),
                processed_symbols=0,
                valid_symbols=0,
                insufficient_symbols=0,
                gap_symbols=0,
                failed_symbols=0,
                new_signals=0,
                source_asof=source_asof,
                elapsed_ms=0.0,
                status="RUNNING",
                source_health="HEALTHY",
                updated_at=first_seen_at,
                completed_at=None,
                error=None,
            )
        if not source_healthy:
            cycle.update(
                status="SOURCE_STALE", source_health="STALE", updated_at=first_seen_at
            )
            store.save([], {}, cycle)
            return {**cycle, "new_events_this_call": 0}
        cycle.update(
            status="RUNNING",
            source_health="HEALTHY",
            source_asof=source_asof,
            error=None,
            updated_at=first_seen_at,
        )
        new_events = 0
        seen = set()
        try:
            for batch in inputs:
                names = [symbol for symbol, _ in batch]
                if (
                    not 1 <= len(batch) <= 128
                    or len(set(names)) != len(names)
                    or not set(names) <= symbols.keys()
                ):
                    raise ValueError(
                        "Input batch must contain 1..128 unique universe members"
                    )
                if seen.intersection(names):
                    raise ValueError("A symbol was supplied twice in one cycle")
                seen.update(names)
                saved, done = store.records(names)
                records, categories = [], {}
                updated = dict(cycle)
                for symbol, bars in batch:
                    if (symbol, identity) in done:
                        continue
                    info = symbols[symbol]
                    record = saved.get(symbol) or empty_record(symbol)
                    category = None
                    try:
                        if any(
                            not isinstance(b, dict) or "timestamp" not in b
                            for b in bars
                        ):
                            raise ValueError("Missing completed-bar timestamp")
                        eligible = [b for b in bars if wall(b["timestamp"]) <= end]
                        if eligible:
                            record, _ = process_symbol(
                                symbol,
                                eligible,
                                sessions=sessions,
                                raw_tick=info["raw_tick"],
                                source_asof=source_asof,
                                first_seen_at=first_seen_at,
                                previous=record,
                            )
                        else:
                            invalidate(record["state"], "MISSING_COMPLETED_15M")
                    except ValueError:
                        invalidate(record["state"], "INVALID_INPUT")
                        category = "failed"
                    if "updated_at" not in record or category:
                        record["updated_at"] = first_seen_at
                    state = record["state"]
                    category = category or (
                        "valid"
                        if state["valid_run"] >= 56 and state["data_asof"] == str(end)
                        else "insufficient"
                        if state["reason"] in ("WARMUP", "INSUFFICIENT_WARMUP")
                        else "gap"
                    )
                    records.append(record)
                    categories[symbol] = category
                    if category in ("gap", "failed"):
                        for event in record["confirmations"]:
                            risk = record["risks"].setdefault(event["event_id"], {})
                            risk.update(
                                data_pending_at=str(end),
                                reason=risk.get("reason") or state["reason"],
                            )
                    updated["processed_symbols"] += 1
                    updated[category + "_symbols"] += 1
                updated["elapsed_ms"] += (perf_counter() - started) * 1000
                started = perf_counter()
                new_events += store.save(records, categories, updated)
                cycle = updated
            cycle["status"] = (
                "COMPLETE"
                if cycle["processed_symbols"] == cycle["expected_symbols"]
                else "INCOMPLETE"
            )
        except Exception as error:
            cycle.update(
                status="FAILED", error=type(error).__name__ + ": " + str(error)
            )
            cycle["elapsed_ms"] += (perf_counter() - started) * 1000
            store.save([], {}, cycle)
            raise
        cycle["elapsed_ms"] += (perf_counter() - started) * 1000
        if cycle["status"] == "COMPLETE":
            cycle["completed_at"] = str(
                datetime.now(BEIJING).replace(tzinfo=None, microsecond=0)
            )
        store.save([], {}, cycle)
        known_sessions = sorted(d for d in sessions if d <= str(end.date()))
        if len(known_sessions) > self.retention_sessions:
            store.prune(known_sessions[-self.retention_sessions])
        return {**cycle, "new_events_this_call": new_events}

    def close(self):
        if not self.closed:
            self.closed = True
            self.queue.put(None)
            self.thread.join()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
