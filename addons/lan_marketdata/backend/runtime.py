"""Single source worker: live sweep first, bounded cold/warm pages thereafter."""

import atexit
from datetime import timedelta
import logging
import os
from pathlib import Path
from threading import Event, Lock, Thread
from time import monotonic

import duckdb

from .collector import Collector, SourceError
from .grid import now_cn, latest_point, points, wall
from .lease import ProcessLease
from .request_budget import CollectionStopped, RequestDeferred
from .store import Store


def classify(candidates, metadata, pool, day):
    rows = []
    for symbol, name in sorted(candidates.items()):
        row = metadata.get(symbol) or {}
        ext = row.get("ext") or {}
        stock = (
            row.get("type") == "stock"
            and row.get("region") == "CN"
            and row.get("exchange") in ("SH", "SZ", "BJ")
        )
        listing = ext.get("listing_date")
        rows.append(
            dict(
                symbol=symbol,
                name=row.get("name") or name,
                eligible=bool(stock and (not listing or listing <= day)),
                source_type=row.get("type"),
                raw_tick=ext.get("tick_size"),
                listing_date=listing,
                listing_status=ext.get("status")
                or ext.get("listing_status")
                or "NOT_PROVIDED",
                in_quote_pool=symbol in pool,
                metadata_day=day,
                metadata_source="QUANTDASH_INSTRUMENTS",
            )
        )
    return rows, dict(
        candidates=len(rows),
        quote_pool=len(pool),
        expected=sum(r["eligible"] for r in rows),
        unclassified=sum(r["source_type"] is None for r in rows),
        excluded=sum(
            r["source_type"] in ("etf", "index", "fund", "bond") for r in rows
        ),
        unpriced=sum(r["eligible"] and not r["raw_tick"] for r in rows),
        listing_status_unconfirmed=sum(
            r["eligible"]
            and not r["in_quote_pool"]
            and r["listing_status"] == "NOT_PROVIDED"
            for r in rows
        ),
    )


def task(master, sessions, target, now, demanded=(), warm_symbols=None):
    active = {s: r for s, r in master.items() if r.get("eligible") or s in demanded}
    groups = {}
    # At every new completion all market members get their first live attempt
    # before any old warmup, including members omitted by the previous response.
    for symbol, row in active.items():
        if row.get("live_attempt", "") >= target or row.get("latest5", "") >= target:
            continue
        available = points(sessions, sessions[max(0, len(sessions) - 3)], target)
        start = next((t for t in available if t > row.get("latest5", "")), available[0])
        groups.setdefault((start, target, "LIVE"), []).append(symbol)
    if groups:
        key = next(iter(groups))
        return (*key, sorted(groups[key])[:100])
    # A 16-session cold page protects 15m continuity. A later 64-session page
    # obtains >=3072 real 5m for 256x60m, without retaining that raw history.
    for stage, days in (("COLD", 16), ("WARM", 64)):
        for symbol, row in active.items():
            if (
                stage == "WARM"
                and warm_symbols is not None
                and symbol not in warm_symbols
                and symbol not in demanded
            ):
                continue
            if (
                row.get("retry_at", "") > now
                or (
                    stage == "COLD"
                    and (row.get("cold_done") or row.get("warm_count", 0) >= 256)
                )
                or (
                    stage == "WARM"
                    and row.get("warm_count", 0) >= 256
                    and not row.get("rebuild_pending")
                )
            ):
                continue
            start_day = [d for d in sessions if d <= target[:10]][
                -min(days, len(sessions))
            ]
            start = max(start_day, row.get("listing_date") or start_day) + " 09:35:00"
            groups.setdefault((start, target, stage), []).append(symbol)
    if groups:
        # Select the viewed stock's history first, then fill the same range with
        # other eligible stocks so priority does not turn bulk HTTP into singles.
        key = min(
            groups,
            key=lambda key: (
                not any(s in demanded for s in groups[key]),
                key[2] != "COLD",
            ),
        )
        return (*key, sorted(groups[key], key=lambda s: (s not in demanded, s))[:100])
    # Retained-window gaps use bounded retries, never an infinite tight loop.
    for symbol, row in active.items():
        if row.get("gaps") and row.get("retry_at", "") <= now:
            groups.setdefault((row["gaps"][0], target, "GAP"), []).append(symbol)
    if groups:
        key = next(iter(groups))
        return (*key, sorted(groups[key])[:100])
    return None


class Worker:
    def __init__(self, context, settings):
        self.context, self.settings = context, settings
        self.folder = Path(context.data_dir) / "user_data" / "lan_marketdata"
        self.folder.mkdir(parents=True, exist_ok=True)
        self.stop, self.lock = Event(), Lock()
        self.store, self.collector = None, None
        host_rpm = int(os.environ.get("TSP_QUANTDASH_RPM", "12"))
        host_background = max(1, host_rpm - min(6, host_rpm - 1))
        self.minute_limit = min(
            settings.get("minute_requests_per_minute", 24),
            24,
            max(1, 30 - host_background),
        )
        self.state = dict(
            version="1.0.2",
            api_version=1,
            phase="STARTING",
            error=None,
            capacities={"5m": 144, "derived": 256},
            source="lan.marketdata",
            writer_alive=False,
            database_connected=False,
            db_retry_count=0,
            db_retry_limit=2,
            db_retry_attempt=0,
            next_retry_at=None,
            last_error=None,
            error_code=None,
            minute_request_limit=self.minute_limit,
            host_minute_reserve=host_background,
            collection_scope=settings.get("collection_symbols", "FULL_MARKET"),
            warm_scope=settings.get("warm_symbols", "FULL_MARKET_BOUNDED_PAGES"),
        )
        self.demands = {}
        self.thread = Thread(target=self.run, name="lan-marketdata-writer", daemon=True)

    def start(self):
        self.lease = ProcessLease(self.folder / "writer.lock")
        self.thread.start()
        atexit.register(self.close)

    def demand(self, symbols):
        with self.lock:
            now = monotonic()
            self.demands = {s: t for s, t in self.demands.items() if now - t < 600}
            for s in symbols:
                if s in self.demands or len(self.demands) < 64:
                    self.demands[s] = now

    def report(self, **values):
        with self.lock:
            self.state.update(values)

    def status(self):
        with self.lock:
            result = dict(
                self.state,
                writer_alive=self.thread.is_alive(),
                requests=dict(self.collector.metrics) if self.collector else {},
            )
        store = self.store
        if store:
            with store.lock:
                result.update(store.connection_status())
                if store.conn is not None:
                    result.update(
                        last_commit=store.meta("last_commit"),
                        universe=store.meta("universe", {}),
                        generation=store.meta("generation", 0),
                        calendar_last=max(store.meta("sessions", []), default=None),
                    )
        return result

    def commit_batch(
        self, symbols, data, factors, *, sessions, start, end, now, target, stage
    ):
        """One network batch, one symbol transaction at a time; OOM never re-fetches."""
        checkpoint = self.collector.checkpoint()
        for symbol in symbols:
            received = data.pop(symbol, {})
            previous = self.store.instrument(symbol)
            attempts = 0 if received else previous.get("attempts", 0) + 1
            receipt = dict(
                live_attempt=target,
                stage=stage,
                attempts=attempts,
                retry_at=str(
                    wall(now) + timedelta(seconds=min(3600, 60 * 2 ** min(attempts, 5)))
                ),
            )
            for retry in range(3):
                try:
                    self.store.accept(
                        symbol,
                        received,
                        sessions=sessions,
                        start=start,
                        end=end,
                        now=now,
                        events=factors.get(symbol),
                        factor_response=symbol in factors,
                        checkpoint=checkpoint,
                        receipt=receipt,
                    )
                    self.report(
                        phase=stage,
                        error=None,
                        error_code=None,
                        next_retry_at=None,
                        db_retry_attempt=0,
                        last_commit=now,
                        batch_committed_symbol=symbol,
                    )
                    break
                except duckdb.OutOfMemoryException:
                    self.report(
                        last_error="DUCKDB_OOM: symbol transaction rolled back",
                        last_error_at=str(now_cn()),
                        error_code="DUCKDB_OOM",
                    )
                    if retry == 2:
                        raise
                    logging.getLogger(__name__).warning(
                        "DuckDB OOM committing %s; reopening (retry %s/2)",
                        symbol,
                        retry + 1,
                        exc_info=True,
                    )
                    delay = (5, 15)[retry]
                    self.report(
                        phase="RECOVERING_DB",
                        error="DuckDB memory budget exhausted; retrying the rolled-back symbol",
                        db_retry_count=self.state["db_retry_count"] + 1,
                        db_retry_attempt=retry + 1,
                        next_retry_at=str(now_cn() + timedelta(seconds=delay)),
                    )
                    self.store.close(checkpoint=False)
                    if self.stop.wait(delay):
                        raise CollectionStopped()
                    self.store.reopen()
            del received

    def bootstrap(self):
        cache = self.store.meta("metadata_cache", {})
        day = str(now_cn().date())
        if cache.get("day") != day:
            pool = {r["symbol"] for r in self.collector.pool()}
            names = self.context.repository.get_name_map(None)
            cache = dict(
                day=day,
                candidates=dict(names, **{s: names.get(s, s) for s in pool}),
                pool=sorted(pool),
                rows={},
            )
        missing = sorted(cache["candidates"].keys() - cache["rows"].keys())
        if missing:
            symbols = missing[:100]
            data = {r["symbol"]: r for r in self.collector.metadata(symbols)}
            cache["rows"].update({s: data.get(s) for s in symbols})
            with self.store.transaction():
                self.store.set_meta("metadata_cache", cache)
                self.store.set_meta("source_checkpoint", self.collector.checkpoint())
            self.report(
                phase="VERIFYING_UNIVERSE",
                metadata_processed=len(cache["rows"]),
                metadata_expected=len(cache["candidates"]),
            )
            return False
        sessions = self.collector.calendar()
        # An index daily request can contain today's uncompleted close. Calendar
        # membership is usable; latest_point still enforces the 60-second margin.
        rows, overview = classify(
            cache["candidates"], cache["rows"], set(cache["pool"]), day
        )
        self.store.save_master(rows, overview, sessions)
        return True

    def run(self):
        try:
            self.store = Store(
                self.folder / "rolling.duckdb",
                memory_limit=self.settings.get("duckdb_memory_limit", "8GB"),
            )
            self.report(
                **self.store.connection_status(),
                last_commit=self.store.meta("last_commit"),
                universe=self.store.meta("universe", {}),
                generation=self.store.meta("generation", 0),
                calendar_last=max(self.store.meta("sessions", []), default=None),
            )
            key = self.settings.get("quantdash_token") or os.environ.get(
                "QUANTDASH_TOKEN"
            )
            if not key:
                raise ValueError(
                    "QUANTDASH_TOKEN is required in backend environment or private settings"
                )
            self.collector = Collector(
                key,
                self.folder / "sdk_cache",
                stop=self.stop,
                report=self.report,
                limit=self.minute_limit,
            )
            checkpoint = self.store.meta("source_checkpoint")
            if checkpoint:
                self.collector.restore(checkpoint)
            # Old custom collectors hand over their quota after installation.
            self.collector.budgets["minute"].defer(60)
            failures = 0
            while not self.stop.is_set():
                work = None
                try:
                    sessions = self.store.meta("sessions", [])
                    if not sessions or self.store.universe_page(0, 1)[0] == 0:
                        self.bootstrap()
                        continue
                    now = str(now_cn())
                    master = self.store.universe()
                    with self.lock:
                        demanded = {
                            s for s, t in self.demands.items() if monotonic() - t < 600
                        }
                    unknown = sorted(demanded - master.keys())[:16]
                    if unknown:
                        data = {
                            r["symbol"]: r for r in self.collector.metadata(unknown)
                        }
                        rows, _ = classify(
                            {s: s for s in unknown}, data, set(), now[:10]
                        )
                        with self.store.transaction():
                            for row in rows:
                                self.store.put_instrument(row)
                        master = self.store.universe()
                    target = latest_point(sessions, now)
                    if not target:
                        self.report(phase="CALENDAR_PENDING")
                        self.stop.wait(10)
                        continue
                    scope = self.settings.get("collection_symbols")
                    network_master = {
                        s: r for s, r in master.items() if scope is None or s in scope
                    }
                    work = task(
                        network_master,
                        sessions,
                        target,
                        now,
                        demanded,
                        self.settings.get("warm_symbols"),
                    )
                    # Refresh one metadata page per idle/new-session turn;
                    # a weekday is never assumed to be a confirmed session.
                    if (
                        now_cn().weekday() < 5
                        and now[11:16] >= "09:25"
                        and self.store.meta("master_day") != now[:10]
                        and (not work or work[2] != "LIVE")
                    ):
                        self.bootstrap()
                        continue
                    if work:
                        start, end, stage, symbols = work
                        self.report(
                            phase=stage,
                            active_symbols=len(symbols),
                            range_start=start,
                            range_end=end,
                            target_5m=target,
                        )
                        needed = [
                            s
                            for s in symbols
                            if master[s].get("factors_day") != now[:10]
                        ]
                        factors = self.collector.factors(needed) if needed else {}
                        data = self.collector.fetch(symbols, start, end)
                        self.commit_batch(
                            symbols,
                            data,
                            factors,
                            sessions=sessions,
                            start=start,
                            end=end,
                            now=now,
                            target=target,
                            stage=stage,
                        )
                        del data, factors
                        failures = 0
                        continue
                    self.report(phase="WAITING_NEXT_5M", error=None, target_5m=target)
                    self.stop.wait(5)
                except RequestDeferred as exc:
                    delay = max(1, exc.until - monotonic())
                    with self.store.transaction():
                        self.store.set_meta(
                            "source_checkpoint", self.collector.checkpoint()
                        )
                    if work and exc.status_code != 429:
                        self.store.failed(
                            work[3],
                            target=target,
                            now=now,
                            message="HTTP_" + str(exc.status_code or "TRANSPORT"),
                            checkpoint=self.collector.checkpoint(),
                        )
                    self.stop.wait(min(30, delay))
                except (SourceError, ValueError) as exc:
                    failures += 1
                    self.report(phase="SOURCE_UNAVAILABLE", error=str(exc))
                    if work:
                        self.store.failed(
                            work[3],
                            target=target,
                            now=now,
                            message=str(exc),
                            checkpoint=self.collector.checkpoint(),
                        )
                    self.stop.wait(min(60, 2 ** min(failures, 6)))
        except CollectionStopped:
            pass
        except Exception as exc:
            logging.getLogger(__name__).exception("Public market worker stopped")
            code = (
                "DUCKDB_OOM"
                if isinstance(exc, duckdb.OutOfMemoryException)
                else "DB_OR_INITIALIZATION_ERROR"
            )
            message = (
                (
                    "DuckDB OOM after two recovery retries; committed data retained"
                    if self.state["db_retry_attempt"] == 2
                    else "DuckDB initialization/metadata OOM; worker stopped, committed data retained"
                )
                if code == "DUCKDB_OOM"
                else str(exc)
                if isinstance(exc, ValueError)
                and str(exc).startswith("duckdb_memory_limit")
                else f"Public market worker stopped ({type(exc).__name__}); inspect local backend log"
            )
            self.report(
                phase="ERROR",
                error=message,
                error_code=code,
                last_error=message,
                last_error_at=str(now_cn()),
                next_retry_at=None,
            )
        finally:
            try:
                if self.collector:
                    self.collector.close()
                if self.store:
                    self.store.close(checkpoint=self.state["phase"] != "ERROR")
            finally:
                self.store = None
                self.report(database_connected=False)
                self.lease.close()

    def close(self):
        self.stop.set()
        if self.thread.is_alive():
            self.thread.join(35)
