"""Finite native production-path load; reuses the existing host replay and HTTP readers."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from threading import Event, Thread
from time import perf_counter

import httpx
import psutil
from tickflow import TickFlow

from addons.lan_ma55.backend.collector import Collector
from addons.lan_ma55.backend.market_grid import as_ms
from addons.lan_ma55.backend.market_store import DEFAULTS, encoded
from addons.lan_ma55.backend.native15_owner import NativeOwner
from addons.lan_ma55.backend.native15_shadow import SOURCE, PROOF
from addons.lan_ma55.backend.native15_signal_adapter import scan
from addons.lan_ma55.backend.indicator_cache import metadata
from addons.lan_ma55.migrate_unified import row_digests
from addons.lan_ma55.tools.stress_db import fixture, quantiles, serve, reader_phase


def seed(owner, count):
    sessions, five, old_bars = fixture()
    bars = [dict(b, source=SOURCE, source_id=SOURCE, source_count=1, source_proof=PROOF) for b in old_bars]
    base, end = bars[:1280], bars[1279]["timestamp"]
    symbols = [f"{i:06d}.SZ" for i in range(count)]
    rows = [dict(symbol=s, name="SYNTHETIC", eligible=True, source_type="etf" if i < count//10 else "stock",
                 raw_tick=.001 if i < count//10 else .01) for i, s in enumerate(symbols)]
    owner.refresh_master(rows, dict(expected=count, stock=count-count//10, etf=count//10), sessions)
    state, events = scan(symbols[0], [dict(b, adjust_factor=1) for b in base[-250:]], sessions=sessions,
                         raw_tick=.01, target=end, now=end, live_after=end, replay=True)
    assert not events
    cache = None
    for offset in range(0, count, 100):
        with owner.conn:
            for info in rows[offset:offset+100]:
                symbol = info["symbol"]
                info = dict(DEFAULTS, **info, source_id=SOURCE, state="READY", latest_completed15=end,
                            # Steady-state fixture: [] was verified before today's first target.
                            factor_coverage="EXPLICIT", factors_day=bars[1280]["timestamp"][:10], revision=1)
                owner.market._put(info)
                owner.market._write(symbol, base)
                owner.conn.execute("INSERT INTO native15_signal_state VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                                   (symbol, encoded(state), 0, 0, end, end, "valid", state["reason"], 1, 1, end))
                if cache is None:
                    owner.market.update_cache(symbol, info, base, [], sessions, rebuild=True)
                    cache = [list(r) for r in owner.conn.execute("SELECT * FROM native15_chart_cache WHERE symbol=?", (symbol,))]
                else:
                    for frame in cache:
                        cloned = list(frame)
                        cloned[0], cloned[7] = symbol, metadata(info)
                        owner.conn.execute("INSERT INTO native15_chart_cache VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", cloned)
                for i in range(8):
                    at = base[80+20*i]["timestamp"]
                    identity = f"SYNTHETIC_ARCHIVE:{symbol}:{i}"
                    event = dict(event_id=identity, symbol=symbol, rule_version="STRESS_ARCHIVE", confirmed_at=at,
                                 setup_at=at, first_seen_at=at, source_asof=at, pattern="SINGLE_BAR", mode="REPLAY")
                    owner.conn.execute("INSERT INTO signal_event VALUES(?,?,?,?,?,?,?,?,?,?)",
                                       (identity, symbol, "STRESS_ARCHIVE", at, at, at, at, "SINGLE_BAR", "REPLAY", encoded(event)))
                    owner.conn.execute("INSERT INTO event_risk VALUES(?,?,?)", (identity, "{}", at))
        if offset % 1000 == 0:
            print(json.dumps(dict(stage="native_seed", symbols=min(count, offset+100))), flush=True)
    return sessions, five, bars, symbols


def run(folder, count=5600):
    from addons.lan_ma55.tools.stress_host import run as request_replay
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder/"native-stress.sqlite3"
    if path.exists() or not 5600 <= count <= 5900:
        raise ValueError("Use a new private DB with 5600..5900 securities")
    process, rss, wal = psutil.Process(), [], []
    stopped = Event()
    def memory():
        while not stopped.wait(.1):
            rss.append(process.memory_info().rss)
            file = Path(str(path)+"-wal")
            wal.append(file.stat().st_size if file.exists() else 0)
    monitor = Thread(target=memory, daemon=True)
    monitor.start()
    cpu, began = sum(process.cpu_times()[:2]), perf_counter()
    report = dict(synthetic=True, commercial_http=0, symbols=count, raw15_per_symbol=1280,
                  factor_fixture="EXPLICIT_EMPTY_VERIFIED_BEFORE_TARGETS; factor recovery tested separately")
    traces, logical, rounds = [], [0.0], []
    try:
        with NativeOwner(path) as owner:
            sessions, five, bars, symbols = seed(owner, count)
            report["seed_seconds"] = perf_counter()-began
            archive = {t: row_digests(owner.conn, t) for t in ("signal_event", "event_risk")}
            def transport(request):
                assert request.url.params["period"] == "15m"
                codes = request.url.params["symbols"].split(",")
                rows = [b for b in bars if int(request.url.params["start_time"]) <= as_ms(b["timestamp"]) <= int(request.url.params["end_time"])]
                data = dict(timestamp=[as_ms(b["timestamp"]) for b in rows], **{
                    k: [b[k]/100 if k == "volume" else b[k] for b in rows] for k in ("open", "high", "low", "close", "volume", "amount")})
                traces.append(dict(at=logical[0], owner="ma55-native15", endpoint=request.url.path,
                                   capability="kline.minute.batch", symbols=len(codes), status=200, retries=0))
                return httpx.Response(200, json={"data": {s: data for s in codes}})
            tf = TickFlow(api_key="synthetic", max_retries=0)
            tf._client._client.close()
            tf._client._client = httpx.Client(base_url="https://synthetic.invalid", transport=httpx.MockTransport(transport))
            collector = Collector("synthetic", folder/"sdk", client=tf)
            for budget in collector.budgets.values():
                budget.clock = lambda: logical[0]
            try:
                with serve(owner) as (base, quote), ThreadPoolExecutor(max_workers=4) as background:
                    readers, active, broadcasts = [], None, Event()
                    def broadcast():
                        while not broadcasts.wait(.02):
                            quote._broadcast_quote_updated()
                    broadcast_thread = Thread(target=broadcast, daemon=True)
                    broadcast_thread.start()
                    def tick(second):
                        nonlocal active
                        logical[0] = second
                        if second in (0, 60, 900, 960):
                            clients = {0:1,60:4,900:8,960:16}[second]
                            readers.append(background.submit(reader_phase, base, symbols, clients, Event(), Event()))
                        if second in (0, 900):
                            number = second//900
                            active = dict(number=number, position=0, target=bars[1280+number]["timestamp"],
                                          began=perf_counter(), transactions=[], requests=[], due=second)
                            with owner.conn:
                                owner.set_meta("target", active["target"])
                        if active is None or second < active["due"] or collector.budgets["minute"].delay() > 0:
                            return
                        job = active
                        batch = symbols[job["position"]:job["position"]+100]
                        network = perf_counter()
                        received, errors = collector.fetch(batch, job["target"], job["target"])
                        job["requests"].append((perf_counter()-network)*1000)
                        transaction = perf_counter()
                        owner.accept_batch(batch, received, errors, sessions=sessions, start=job["target"], end=job["target"],
                                           now=job["target"], factors={}, stage="A", live_after=job["target"])
                        job["transactions"].append((perf_counter()-transaction)*1000)
                        job["position"] += len(batch)
                        job["due"] = second+7.5
                        if job["position"] >= count:
                            elapsed = perf_counter()-job["began"]
                            logical_span = second-job["number"]*900
                            status = owner.status_snapshot(target=job["target"])
                            assert status["today_native15_saved"] == status["today_analyzed_candidates"] == count
                            rounds.append(dict(seconds=elapsed, quota_schedule_seconds=logical_span,
                                               target_pass=elapsed+logical_span < 900, transaction_ms=quantiles(job["transactions"]),
                                               request_ms=quantiles(job["requests"]), target=job["target"], saved=count, analyzed=count))
                            print(json.dumps(dict(stage="native_bucket_complete", **rounds[-1])), flush=True)
                            active = None
                    request_replay(folder, symbols, traces, tick)
                    report["concurrent"] = [job.result() for job in readers]
                    broadcasts.set()
                    broadcast_thread.join()
                    assert len(rounds) == 2 and all(not r["errors"] for r in report["concurrent"])
            finally:
                collector.close()
            report.update(rounds=rounds, requests=dict(collector.metrics), old_rows_unchanged={t: row_digests(owner.conn,t) == rows for t,rows in archive.items()},
                          quick_check=owner.conn.execute("PRAGMA quick_check").fetchone()[0],
                          checkpoint=owner.checkpoint(), native_bar_count=owner.conn.execute("SELECT count(*) FROM native15_bar").fetchone()[0])
            report["timings"] = [dict(r) for r in owner.conn.execute("SELECT * FROM native15_timing")]
        with NativeOwner(path) as reopened:
            report["restart_saved"] = reopened.status_snapshot(target=bars[1281]["timestamp"])["today_native15_saved"]
        report.update(cpu_seconds=sum(process.cpu_times()[:2])-cpu, total_seconds=perf_counter()-began,
                      peak_rss=max(rss), peak_wal=max(wal), db_bytes=path.stat().st_size)
    finally:
        stopped.set()
        monitor.join()
    (folder/"native-db-metrics.json").write_text(json.dumps(report, indent=2), "utf-8")
    return report
