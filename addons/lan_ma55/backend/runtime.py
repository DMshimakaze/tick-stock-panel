"""TSP-owned background lifetime: one lease, one SQLite writer, no browser work."""

import atexit
from datetime import timedelta
import json
import os
from pathlib import Path
from threading import Event, Lock, Thread
from time import monotonic, perf_counter

import httpx

from .collector import Collector
from .live_store import LiveStore
from .market import latest_point, now_cn, recent_start
from .pipeline import wall
from .request_budget import CollectionStopped, RequestDeferred
from .result_publisher import Publisher


class ProcessLease:
    def __init__(self, path):
        self.file = open(path, "a+b")
        self.file.seek(0, 2)
        if self.file.tell() == 0:
            self.file.write(b"0")
            self.file.flush()
        self.file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise RuntimeError("Another MA55 backend owns this data directory") from None

    def close(self):
        self.file.close()  # OS releases the lease after both orderly exit and crash.


def classify(candidates, metadata, pool, day):
    rows, counts = [], dict(candidates=len(candidates), quote_pool=len(pool), expected=0,
                           excluded=0, unclassified=0, non_snapshot_stocks=0, unpriced=0)
    for symbol in sorted(candidates):
        row = metadata.get(symbol)
        ext = row.get("ext", {}) if row else {}
        stock = bool(row and row.get("type") == "stock" and row.get("region") == "CN"
                     and row.get("exchange") in ("SH", "SZ", "BJ"))
        tick, listing = ext.get("tick_size"), ext.get("listing_date")
        explicit_other = bool(row and row.get("type") in ("etf", "index", "fund", "bond"))
        eligible = bool(stock and (not listing or listing <= day))
        if eligible:
            counts["expected"] += 1
            counts["non_snapshot_stocks"] += int(symbol not in pool)
            counts["unpriced"] += int(not isinstance(tick, (int, float)) or tick <= 0)
        elif explicit_other:
            counts["excluded"] += 1
        else:
            counts["unclassified"] += 1
        rows.append(dict(symbol=symbol, name=row.get("name", symbol) if row else candidates[symbol],
                         eligible=eligible, raw_tick=tick, listing_date=listing,
                         source_type=row.get("type") if row else None, in_quote_pool=symbol in pool,
                         listing_status=ext.get("status") or ext.get("listing_status") or "NOT_PROVIDED",
                         metadata_day=day))
    # Metadata confirms types/ticks, but does not expose a definitive current
    # listing-status flag for non-quote members. Keep that scope visible.
    counts["listing_status_unconfirmed"] = sum(r["eligible"] and not r["in_quote_pool"]
                                               and r["listing_status"] == "NOT_PROVIDED" for r in rows)
    return rows, counts


class Worker:
    def __init__(self, context, settings):
        self.folder = Path(context.data_dir) / "user_data" / "lan_ma55"
        self.folder.mkdir(parents=True, exist_ok=True)
        self.context, self.settings = context, settings
        self.stop = Event()
        self.lock = Lock()
        self.state = dict(phase="STARTING", backend_scanning=True, error=None, started_at=str(now_cn()),
                          rule_version="MA55_15M_MINIMAL_V5", version="0.2.10",
                          minute_request_limit=settings.get('minute_requests_per_minute', 24))
        self.publisher = Publisher(settings["api_base"], settings.get("api_token"))
        self.last_publish = 0
        self.collector = None
        self.thread = Thread(target=self.run, name="lan-ma55-quantdash-pro", daemon=True)

    def start(self):
        self.lease = ProcessLease(self.folder / "worker.lock")
        self.thread.start()
        # Official extension v1 has startup but no shutdown context hook. Every
        # batch is durable; atexit interrupts waits and joins the sole worker.
        atexit.register(self.close)

    def status(self):
        with self.lock:
            return dict(self.state, worker_alive=self.thread.is_alive())

    def report(self, **values):
        with self.lock:
            self.state.update(values)
            if self.collector:
                self.state["requests"] = dict(self.collector.metrics)
            payload = dict(self.state, worker_alive=self.thread.is_alive())
        if monotonic() - self.last_publish >= 5:
            self.publish_status(payload)

    def publish_status(self, payload=None):
        try:
            self.publisher.summary(payload or self.status())
            self.last_publish = monotonic()
            with self.lock:
                self.state["publisher_error"] = None
        except (httpx.HTTPError, ValueError) as exc:
            status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
            with self.lock:
                self.state["publisher_error"] = f"Official ext-data publish failed ({status or type(exc).__name__}); check local URL / write:ext authorization"

    def bootstrap(self, store, *, one_chunk=False):
        today = str(now_cn().date())
        cache = store.meta("metadata_cache", {})
        if cache.get("day") != today:
            pool = {r["symbol"] for r in self.collector.pool()}
            names = self.context.repository.get_name_map(None)
            candidates = dict(names)
            candidates.update({s: names.get(s, s) for s in pool})
            cache = dict(day=today, candidates=candidates, pool=sorted(pool), rows={})
        missing = sorted(set(cache["candidates"]) - cache["rows"].keys())
        for offset in range(0, len(missing), 100):
            chunk = missing[offset:offset + 100]
            data = self.collector.metadata(chunk)
            returned = {r["symbol"]: r for r in data}
            cache["rows"].update({s: returned.get(s) for s in chunk})
            with store.conn:
                store.set_meta("metadata_cache", cache)
                store.set_meta("source_checkpoint", self.collector.checkpoint())
            self.report(phase="VERIFYING_UNIVERSE", metadata_processed=len(cache["rows"]), metadata_expected=len(cache["candidates"]))
            if one_chunk and offset + 100 < len(missing):
                return None
        # The public host search can classify vendor-omitted ETF/index names;
        # unlike get_name_map, it returns an explicit asset_type. Never infer
        # stock membership or a tick from a six-digit code.
        for symbol, data in list(cache["rows"].items()):
            if data is not None:
                continue
            response = self.publisher.http.get("/api/kline/instruments/search", params={
                "q": symbol, "limit": 50, "asset_types": "stock,etf,index"})
            if response.status_code in (401, 403):
                continue
            response.raise_for_status()
            matches = [r for r in response.json()["results"] if r["symbol"] == symbol]
            if len(matches) == 1:
                row = matches[0]
                cache["rows"][symbol] = dict(symbol=symbol, name=row["name"], type=row["asset_type"],
                                             region="CN", exchange=symbol.rsplit(".", 1)[-1], ext={},
                                             type_evidence="TSP_PUBLIC_INSTRUMENT_SEARCH")
        with store.conn:
            store.set_meta("metadata_cache", cache)
        rows, overview = classify(cache["candidates"], cache["rows"], set(cache["pool"]), today)
        overview["tsp_type_resolved"] = sum(bool(r and r.get("type_evidence")) for r in cache["rows"].values())
        store.save_master(rows, overview)
        sessions = self.collector.calendar()
        with store.conn:
            store.set_meta("sessions", sessions)
            store.set_meta("master_day", today)
            store.set_meta("universe_contract_version", 2)
            store.set_meta("source_checkpoint", self.collector.checkpoint())
        return store.master(), sessions

    def task(self, views, master, sessions, target5, now):
        # First sweep of a new live completion always precedes old gap repair.
        groups = {}
        if target5[:10] == now[:10]:
            for symbol, view in views.items():
                if (view.get("received") or "") >= target5 or view.get("last_live_target") == target5:
                    continue
                end = wall(target5)
                group_start = end.replace(minute=(end.minute // 15) * 15) + timedelta(minutes=5)
                if end.minute % 15 == 0:
                    group_start = end - timedelta(minutes=10)
                start = max(str(group_start), str(wall(view["received"]) + timedelta(minutes=5))) if view.get("received") and view["received"][:10] == now[:10] else str(group_start)
                groups.setdefault((start, target5, "LIVE"), []).append(symbol)
            if groups:
                key = next(iter(groups))
                return (*key, groups[key][:100])
        # Complete the first real sweep before retrying an older missing stock.
        ordered = sorted(views.items(), key=lambda item: bool(item[1].get("received") or item[1].get("gap")))
        for symbol, view in ordered:
            if view["retry_at"] > now:
                continue
            if view.get("received") and view.get("through") and view["through"] >= target5:
                continue
            window = view.get("gap") or [max(recent_start(sessions, target5), (master[symbol]["listing_date"] or sessions[0]) + " 09:35:00"), target5]
            start, end = max(window[0], recent_start(sessions, target5)), min(window[1], target5)
            if start <= end:
                groups.setdefault((start, end, "REPLAY"), []).append(symbol)
        if groups:
            key = next(iter(groups))
            return (*key, groups[key][:100])
        return None

    def run(self):
        try:
            with LiveStore(self.folder / "state.sqlite3") as store:
                key = self.settings.get("quantdash_token") or os.environ.get("QUANTDASH_TOKEN")
                if not key:
                    raise ValueError("QUANTDASH_TOKEN is required in the backend environment or private MA55 settings")
                self.collector = Collector(key, self.folder / "sdk_cache", stop=self.stop, report=self.report,
                                           limit=self.settings.get('minute_requests_per_minute', 24))
                checkpoint = store.meta("source_checkpoint")
                if checkpoint:
                    self.collector.restore(checkpoint)
                cooldown = store.meta("cooldown_until")
                if cooldown:
                    remaining = max(0, (wall(cooldown) - now_cn()).total_seconds())
                    for budget in self.collector.budgets.values():
                        budget.defer(remaining)
                master = store.master()
                sessions = store.meta("sessions", [])
                if not master or not sessions or store.meta("universe_contract_version") != 2:
                    while not self.stop.is_set():
                        try:
                            master, sessions = self.bootstrap(store)
                            break
                        except RequestDeferred as exc:
                            delay = max(0, exc.until - monotonic())
                            with store.conn:
                                store.set_meta("cooldown_until", str(now_cn() + timedelta(seconds=delay)))
                            self.stop.wait(min(30, delay))
                views = {s: store.journal(s) for s, info in master.items() if info["eligible"]}
                last_calendar, published_history = monotonic(), False
                while not self.stop.is_set():
                    now = str(now_cn())
                    if now_cn().weekday() < 5 and now[:10] not in sessions and "09:35" <= now[11:16] <= "15:05" and monotonic() - last_calendar >= 300:
                        sessions = self.collector.calendar()
                        last_calendar = monotonic()
                    target5 = latest_point(sessions, now)
                    target15 = latest_point(sessions, now, step=15)
                    if not target5 or not target15:
                        self.report(phase="CALENDAR_PENDING", error="No confirmed completed exchange point")
                        self.stop.wait(10)
                        continue
                    task = self.task(views, master, sessions, target5, now)
                    if (now_cn().weekday() < 5 and now[11:16] >= "09:25" and store.meta("master_day") != now[:10]
                            and (task is None or task[2] != "LIVE")):
                        try:
                            refreshed = self.bootstrap(store, one_chunk=True)
                            if refreshed:
                                master, sessions = refreshed
                                views = {s: store.journal(s) for s, info in master.items() if info["eligible"]}
                                published_history = False
                        except RequestDeferred as exc:
                            self.stop.wait(min(30, max(0, exc.until - monotonic())))
                        continue
                    if task:
                        start, end, mode, symbols = task
                        self.report(phase="COLLECTING", target_5m=target5, target_15m=target15,
                                    batch_symbols=len(symbols), range_start=start, range_end=end, error=None)
                        began = perf_counter()
                        http_before = self.collector.metrics["http_requests"]
                        minute_before = self.collector.metrics["minute_requests"]
                        try:
                            factor_symbols = [s for s in symbols if views[s]["factors_day"] != now[:10]]
                            factors = self.collector.factors(factor_symbols) if factor_symbols else {}
                            response = self.collector.fetch(symbols, start, end)
                        except RequestDeferred as exc:
                            delay = max(0, exc.until - monotonic())
                            if exc.status_code == 429:
                                with store.conn:
                                    store.set_meta("cooldown_until", str(now_cn() + timedelta(seconds=delay)))
                                self.stop.wait(min(30, delay))
                            else:
                                store.defer_batch(symbols, start=start, end=end, target5=target5, target15=target15,
                                                  master=master, now=str(now_cn()), status=exc.status_code)
                                for s in symbols:
                                    views[s] = store.journal(s)
                                self.report(**store.summary(target15), phase="NETWORK_WAIT")
                            self.publish_status()
                            with store.conn:
                                store.set_meta("source_checkpoint", self.collector.checkpoint())
                            continue
                        store.accept(symbols, response, factors, start=start, end=end, target15=target15,
                                     sessions=sessions, master=master, now=str(now_cn()),
                                     live_after=self.state["started_at"], elapsed_ms=(perf_counter() - began) * 1000,
                                     http_requests=self.collector.metrics["http_requests"] - http_before,
                                     minute_requests=self.collector.metrics["minute_requests"] - minute_before)
                        with store.conn:
                            store.set_meta("source_checkpoint", self.collector.checkpoint())
                        for s in symbols:
                            views[s] = store.journal(s)
                        # Repairs and a new session can add historical events;
                        # refresh the projection at the next idle opportunity.
                        published_history = False
                        summary = store.summary(target15)
                        self.report(**summary, phase="ANALYZING", batch_ms=round((perf_counter() - began) * 1000, 2),
                                    received_symbols=len(response), wait_seconds=0)
                        try:
                            self.publisher.day(target15[:10], store.day_rows(target15[:10], symbols))
                            self.publish_status()
                        except httpx.HTTPError as exc:
                            self.report(publisher_error=f"Official result publish HTTP {getattr(getattr(exc, 'response', None), 'status_code', None)}")
                        continue
                    summary = store.summary(target15)
                    if not published_history:
                        try:
                            store.retain(sessions, self.settings["retention_sessions"])
                            summary = store.summary(target15)
                            history = {}
                            if summary["market_date"]:
                                self.publisher.day(summary["market_date"], store.day_rows(summary["market_date"]))
                            for day in summary["available_dates"]:
                                rows = store.day_rows(day)
                                cycles = store.conn.execute("SELECT * FROM scan_cycle WHERE trade_date=? AND cycle_id LIKE ? ORDER BY bar_end DESC LIMIT 1", (day, store.identity("%"))).fetchone()
                                history[day] = dict(cycle=dict(cycles) if cycles else None,
                                                   total=len(rows), buy_count=sum(len(json.loads(r["events_json"])) for r in rows),
                                                   rows=[r for r in rows if r["events_json"] != "[]" or r["phase"] == "WATCHING" or r["category"] in ("gap", "failed")])
                            self.publisher.history(history)
                            self.report(history_published_at=str(now_cn()))
                            published_history = True
                        except httpx.HTTPError:
                            self.report(publisher_error="History publication pending; SQLite retained")
                    self.report(**summary, phase="WAITING_NEXT_SESSION" if target5[:10] < now[:10] or now[11:16] >= "15:01" else "WAITING_NEXT_5M",
                                target_5m=target5, target_15m=target15, batch_symbols=0, wait_seconds=0)
                    self.publish_status()
                    self.stop.wait(10)
        except CollectionStopped:
            pass
        except Exception as exc:
            # Optional extension boundary: publish the actual terminal failure
            # and stop. No empty-success fallback conceals a broken worker.
            self.report(phase="ERROR", backend_scanning=False, error=str(exc))
            self.publish_status()
        finally:
            if self.collector:
                self.collector.close()
            with self.lock:
                self.state["backend_scanning"] = False
            self.publisher.close()
            self.lease.close()

    def close(self):
        self.stop.set()
        if self.thread.is_alive():
            self.thread.join(timeout=35)
