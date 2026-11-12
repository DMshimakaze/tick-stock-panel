"""Finite synthetic unified-owner load. Output belongs in a private new directory."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, ExitStack
from datetime import date, timedelta
import json
from pathlib import Path
import socket
from threading import Event, Lock, Thread
from time import perf_counter
from types import SimpleNamespace

from fastapi import FastAPI
import httpx
import psutil
from tickflow import TickFlow
import uvicorn

from addons.lan_ma55.backend import extension
from addons.lan_ma55.backend.collector import Collector
from addons.lan_ma55.backend.engine import RULE_VERSION, STATE_REVISION
from addons.lan_ma55.backend.market_grid import (
    SOURCE,
    aggregate,
    as_ms,
    points,
    LEGACY,
    latest_point,
)
from addons.lan_ma55.backend.market_store import DEFAULTS
from addons.lan_ma55.backend.pipeline import process_symbol
from addons.lan_ma55.backend.store import encoded
from addons.lan_ma55.backend.unified_store import UnifiedStore
from addons.kline_workbench.backend.service import Worker as ViewBridge


def quantiles(values):
    ordered = sorted(values)
    return {
        f"p{n}": ordered[min(len(ordered) - 1, int((len(ordered) - 1) * n / 100))]
        if ordered
        else None
        for n in (50, 95, 99)
    }


def fixture():
    sessions = []
    day = date(2026, 1, 5)
    while len(sessions) < 18:
        if day.weekday() < 5:
            sessions.append(str(day))
        day += timedelta(days=1)
    stamps = points(sessions, sessions[0], sessions[-1] + " 15:00:00")[:759]
    five = {
        t: dict(
            open=100.0, high=100.1, low=99.9, close=100.0, volume=10000.0, amount=1e6
        )
        for t in stamps
    }
    return sessions, five, aggregate(five, sessions, max(five))["15m"]


def seed(owner, count):
    sessions, five, bars = fixture()
    base = bars[:250]
    end = base[-1]["timestamp"]
    symbols = [f"{i:06d}.SZ" for i in range(count)]
    rows = [
        dict(
            symbol=s,
            name="SYNTHETIC",
            eligible=True,
            source_type="stock",
            raw_tick=0.01,
        )
        for s in symbols
    ]
    owner.refresh_master(rows, {"expected": count}, sessions)
    template, _ = process_symbol(
        symbols[0],
        [dict(b, adjust_factor=1) for b in base],
        sessions=sessions,
        raw_tick=0.01,
        source_asof=end,
        first_seen_at=end,
    )
    state = template["state"]
    state.update(
        shared_source_id=SOURCE,
        source_basis_revision=0,
        state_revision=STATE_REVISION,
        shared_receipt=[SOURCE, 1, 0, end, RULE_VERSION, STATE_REVISION],
    )
    cycle = owner.new_cycle(end, count, end, "REPLAY")
    cycle.update(processed_symbols=count, valid_symbols=count, status="COMPLETE")
    owner.save([], {}, cycle)
    day_payload = encoded(
        dict(
            symbol="SYNTHETIC",
            name="SYNTHETIC",
            phase="NO_SETUP",
            category="valid",
            events_json="[]",
            decision_json="{}",
            market_date=end[:10],
        )
    )
    for offset in range(0, count, 100):
        with owner.conn:
            for symbol in symbols[offset : offset + 100]:
                info = dict(
                    DEFAULTS,
                    symbol=symbol,
                    name="SYNTHETIC",
                    eligible=True,
                    raw_tick=0.01,
                    state="READY",
                    factor_coverage="EXPLICIT",
                    source_id=SOURCE,
                    latest_completed15=end,
                    latest_source5=end,
                    revision=1,
                    factors_day=end[:10],
                )
                cold = symbol in symbols[-16:]
                if cold:
                    info.update(source_id=LEGACY, state="REBUILD_PENDING")
                owner.market._put(info)
                owner.market._write(
                    symbol, [dict(b, source_id=LEGACY) for b in base] if cold else base
                )
                owner.conn.execute(
                    "INSERT INTO symbol_state VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        symbol,
                        RULE_VERSION,
                        1,
                        end,
                        end,
                        encoded(
                            dict(
                                state,
                                shared_source_id=LEGACY,
                                shared_receipt=[
                                    LEGACY,
                                    1,
                                    0,
                                    end,
                                    RULE_VERSION,
                                    STATE_REVISION,
                                ],
                            )
                            if cold
                            else state
                        ),
                        end,
                        cycle["cycle_id"],
                        "valid",
                        "NO_SETUP",
                    ),
                )
                for i in range(8):
                    at = base[80 + 20 * i]["timestamp"]
                    identity = f"SYNTHETIC:{symbol}:{i}"
                    event = dict(
                        event_id=identity,
                        symbol=symbol,
                        rule_version="STRESS_SAVED",
                        setup_at=at,
                        confirmed_at=at,
                        first_seen_at=at,
                        source_asof=at,
                        pattern="SINGLE_BAR",
                        mode="REPLAY",
                        defense=99.0,
                        adjust_factor=1,
                        source_id=SOURCE,
                        decision={},
                    )
                    owner.conn.execute(
                        "INSERT INTO signal_event VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (
                            identity,
                            symbol,
                            "STRESS_SAVED",
                            at,
                            at,
                            at,
                            at,
                            "SINGLE_BAR",
                            "REPLAY",
                            encoded(event),
                        ),
                    )
                    owner.conn.execute(
                        "INSERT INTO event_risk VALUES(?,?,?)", (identity, "{}", at)
                    )
                for day in sessions[:14]:
                    owner.conn.execute(
                        "INSERT INTO day_state VALUES(?,?,?)",
                        (symbol, day, day_payload),
                    )
                for version in range(5):
                    owner.conn.execute(
                        "INSERT INTO rule_state_archive VALUES(?,?,?)",
                        (symbol, f"STRESS:{version}", encoded(state)),
                    )
            # Comparable historical cycle-member volume; this is never a live market claim.
            for history in range(342):
                owner.conn.executemany(
                    "INSERT INTO cycle_member VALUES(?,?,?)",
                    [
                        (f"STRESS:{history}", s, "valid")
                        for s in symbols[offset : offset + 100]
                    ],
                )
        if offset % 1000 == 0:
            print(
                json.dumps(dict(stage="seed", symbols=min(count, offset + 100))),
                flush=True,
            )
    return sessions, five, bars, symbols


@contextmanager
def serve(owner):
    from app.api.intraday import router as stream_router
    from app.services.quote_service import QuoteService

    app = FastAPI()
    quote = QuoteService()
    quote._symbol_count = 5600
    app.state.quote_service = quote
    extension.worker = SimpleNamespace(
        store=owner.market,
        status=lambda: dict(api_version=1, phase="ISOLATED", **owner.market.snapshot()),
        demand=lambda s: None,
    )
    extension.data_path = owner.path
    extension.setup(SimpleNamespace(include_router=app.include_router))
    app.include_router(stream_router)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
    thread = Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    for _ in range(300):
        if server.started:
            break
        Event().wait(0.01)
    if not server.started:
        raise RuntimeError("Isolated API server did not start")
    try:
        yield f"http://127.0.0.1:{sock.getsockname()[1]}", quote
    finally:
        server.should_exit = True
        thread.join(10)
        sock.close()
        if thread.is_alive():
            raise RuntimeError("Isolated API server did not stop")


def reader_phase(base, symbols, clients, started, stop):
    latencies = []
    errors = []
    sse_events = []
    guard = Lock()

    def client(index):
        local = []
        received = 0
        try:
            with (
                httpx.Client(base_url=base, trust_env=False, timeout=30) as http,
                ExitStack() as streams,
            ):
                iterators = []
                # Four real official SSE connections per user, matching the four pages.
                for page in ("dashboard", "watchlist", "limit-ladder", "industry"):
                    response = streams.enter_context(
                        http.stream(
                            "GET", "/api/intraday/stream", params={"page": page}
                        )
                    )
                    response.raise_for_status()
                    lines = response.iter_lines()
                    iterators.append(lines)
                    for line in lines:
                        if line.startswith("event: quotes_updated"):
                            received += 1
                            break
                started.set()
                for i in range(24):
                    symbol = symbols[(index * 7 + i) % len(symbols)]
                    began = perf_counter()
                    response = http.get(
                        "/api/extensions/lan.ma55/v1/bars",
                        params={"symbols": symbol, "timeframes": "15m,30m,60m"},
                    )
                    response.raise_for_status()
                    data = response.json()
                    assert len(data["rows"][0]["bars"]) == 250
                    events = http.get(
                        "/api/extensions/lan.ma55/v1/events",
                        params={"symbol": symbol, "limit": 200},
                    )
                    events.raise_for_status()
                    assert events.json()["total"] == 8
                    local.append((perf_counter() - began) * 1000)
        except Exception as exc:
            with guard:
                errors.append(str(exc))
        with guard:
            latencies.extend(local)
            sse_events.append(received)

    with ThreadPoolExecutor(max_workers=clients) as pool:
        list(pool.map(client, range(clients)))
    stop.set()
    return dict(
        clients=clients,
        reads=len(latencies),
        combined_bars_events_ms=quantiles(latencies),
        errors=errors,
        sse_connections=sum(sse_events),
    )


def run(folder, count=5600):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "stress.sqlite3"
    if path.exists() or not 5600 <= count <= 5900:
        raise ValueError("Use a new private stress DB and 5600..5900 symbols")
    process = psutil.Process()
    rss = []
    stop_memory = Event()

    def memory():
        while not stop_memory.wait(0.05):
            rss.append(process.memory_info().rss)

    monitor = Thread(target=memory, daemon=True)
    monitor.start()
    cpu0 = sum(process.cpu_times()[:2])
    started = perf_counter()
    report = dict(synthetic=True, commercial_http=0, symbols=count)
    traces = []
    logical = [0.0]
    rounds = []
    with UnifiedStore(path) as owner:
        sessions, five, bars, symbols = seed(owner, count)
        report["seed_seconds"] = perf_counter() - started
        old_first = owner.market.raw_rows(symbols[0])[0]["timestamp"]
        report["seed_rows"] = {
            t: owner.conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
            for t in (
                "bar15",
                "symbol_state",
                "signal_event",
                "event_risk",
                "day_state",
                "cycle_member",
                "rule_state_archive",
            )
        }

        def transport(request):
            codes = request.url.params.get("symbols", "").split(",")
            if request.content:
                codes = json.loads(request.content).get("symbols", codes)
            start = request.url.params.get("start_time")
            end = request.url.params.get("end_time")
            if start is None:
                traces.append(
                    dict(
                        at=logical[0],
                        owner="ma55",
                        endpoint=request.url.path,
                        capability="adj_factor",
                        symbols=len(codes),
                        status=200,
                        retries=0,
                    )
                )
                return httpx.Response(200, json={"data": {s: [] for s in codes}})
            lo = int(start)
            hi = int(end)
            rows = {t: r for t, r in five.items() if lo <= as_ms(t) <= hi}
            payload = dict(
                timestamp=[as_ms(t) for t in rows],
                **{
                    k: [r[k] / 100 if k == "volume" else r[k] for r in rows.values()]
                    for k in ("open", "high", "low", "close", "volume", "amount")
                },
            )
            traces.append(
                dict(
                    at=logical[0],
                    owner="ma55",
                    endpoint=request.url.path,
                    capability="kline.minute.batch",
                    symbols=len(codes),
                    status=200,
                    retries=0,
                )
            )
            return httpx.Response(200, json={"data": {s: payload for s in codes}})

        tf = TickFlow(
            api_key="synthetic", base_url="https://synthetic.invalid", max_retries=0
        )
        tf._client._client.close()
        tf._client._client = httpx.Client(
            transport=httpx.MockTransport(transport),
            base_url="https://synthetic.invalid",
        )
        collector = Collector("synthetic", folder / "sdk", client=tf)
        for budget in collector.budgets.values():
            budget.clock = lambda: logical[0]
        try:
            with serve(owner) as (base, quote):
                broadcast_stop = Event()

                sse_peak = [0]

                def broadcast():
                    while not broadcast_stop.wait(0.02):
                        sse_peak[0] = max(sse_peak[0], len(quote._subscribers))
                        quote._broadcast_quote_updated()

                broadcaster = Thread(target=broadcast, daemon=True)
                broadcaster.start()
                from addons.lan_ma55.tools.stress_host import run as request_replay

                read_jobs = []
                active = None
                max_queue = 0
                with ThreadPoolExecutor(max_workers=4) as background:

                    def tick(second):
                        nonlocal active, max_queue
                        logical[0] = float(second)
                        if second in (0, 60, 900, 960):
                            clients = {0: 1, 60: 4, 900: 8, 960: 16}[second]
                            read_jobs.append(
                                background.submit(
                                    reader_phase,
                                    base,
                                    symbols,
                                    clients,
                                    Event(),
                                    Event(),
                                )
                            )
                        if second in (0, 900):
                            number = second // 900
                            active = dict(
                                number=number,
                                target=bars[250 + number]["timestamp"],
                                first=min(
                                    t
                                    for t in five
                                    if as_ms(t) > as_ms(bars[249 + number]["timestamp"])
                                ),
                                warm=symbols[:-16] if number == 0 else symbols,
                                cold=symbols[-16:] if number == 0 else [],
                                started=perf_counter(),
                                transactions=[],
                                lock_waits=[],
                                position=0,
                                cold_position=0,
                                next_due=second,
                            )
                        if (
                            active is None
                            or second < active["next_due"]
                            or collector.budgets["minute"].delay() > 0
                        ):
                            return
                        job = active
                        cold = job["position"] >= len(job["warm"])
                        if cold:
                            batch = job["cold"][
                                job["cold_position"] : job["cold_position"] + 8
                            ]
                            start = min(five)
                            factors = collector.factors(batch)
                        else:
                            batch = job["warm"][job["position"] : job["position"] + 100]
                            start = job["first"]
                            factors = {}
                        received, errors = collector.fetch(
                            batch, start, job["target"], cold=cold
                        )
                        assert not errors
                        for symbol in batch:
                            began = perf_counter()
                            owner.accept_market(
                                symbol,
                                received[symbol],
                                sessions=sessions,
                                start=start,
                                end=job["target"],
                                now=job["target"],
                                live_after=job["target"],
                                events=factors.get(symbol),
                                factor_response=symbol in factors,
                                rebuild=cold,
                            )
                            job["transactions"].append((perf_counter() - began) * 1000)
                            job["lock_waits"].append(owner.last_lock_wait_ms)
                        job["cold_position" if cold else "position"] += len(batch)
                        job["next_due"] = second + (30 if cold else 7.5)
                        max_queue = max(
                            max_queue,
                            len(job["warm"])
                            + len(job["cold"])
                            - job["position"]
                            - job["cold_position"],
                        )
                        if job["position"] >= len(job["warm"]) and job[
                            "cold_position"
                        ] >= len(job["cold"]):
                            elapsed = perf_counter() - job["started"]
                            span = second - job["number"] * 900
                            checkpoint_began = perf_counter()
                            checkpoint = owner.checkpoint()
                            rounds.append(
                                dict(
                                    seconds=elapsed,
                                    rows_per_second=count / elapsed,
                                    transaction_ms=quantiles(job["transactions"]),
                                    quota_schedule_seconds=span,
                                    target_pass=elapsed + span < 900,
                                    lock_wait_ms=quantiles(job["lock_waits"]),
                                    checkpoint=checkpoint,
                                    checkpoint_ms=(perf_counter() - checkpoint_began)
                                    * 1000,
                                )
                            )
                            print(
                                json.dumps(
                                    dict(
                                        stage="integrated_bucket_complete",
                                        round=job["number"] + 1,
                                        seconds=elapsed,
                                        logical_seconds=span,
                                    )
                                ),
                                flush=True,
                            )
                            active = None

                    request_replay(folder, symbols, traces, tick)
                    report["concurrent"] = [job.result() for job in read_jobs]
                assert len(rounds) == 2
                report["bounded_queue_peak"] = max_queue
                report["queue_bound"] = count
                bridge = ViewBridge(
                    SimpleNamespace(data_dir=folder), {"api_base": base}
                )
                published = []
                bridge.publisher.publish = lambda rows: published.extend(rows)
                demands = [
                    dict(
                        scope=str(i),
                        client_id=f"synthetic-{i}",
                        request_id=str(i),
                        requested_symbol=symbols[i],
                        period="15m",
                    )
                    for i in range(16)
                ]
                bridge.publish(demands)
                assert len(published) == 16
                bridge.publisher.close()
                bridge.source.close()
                report["view_slots"] = len(published)
                report["peak_sse_connections"] = sse_peak[0]
                broadcast_stop.set()
                broadcaster.join()
        finally:
            collector.close()
        report["rounds"] = rounds
        # Reachable source failures are tested without affecting the other securities.
        target = bars[251]["timestamp"]
        original = owner.conn.execute("SELECT count(*) FROM signal_event").fetchone()[0]
        for symbol, reason in zip(
            symbols[:4],
            ["GAP", "FACTOR_PENDING", "SOURCE_UNAVAILABLE", "INVALID_5M_OHLCV"],
        ):
            owner.accept_market(
                symbol,
                {},
                sessions=sessions,
                start=target,
                end=target,
                now=target,
                live_after=target,
                error=reason,
            )
        assert (
            owner.conn.execute("SELECT count(*) FROM signal_event").fetchone()[0]
            == original
        )
        # Corrections and a real factor-chain change preserve historical event IDs.
        before_revision = owner.market.instrument(symbols[4])["basis_revision"]
        corrected = {t: dict(r) for t, r in five.items() if t <= target}
        corrected[min(corrected)]["high"] += 0.2
        # Correct a retained bar, rather than an already evicted historical point.
        changed_at = owner.market.raw_rows(symbols[4])[20]["timestamp"]
        corrected[changed_at]["high"] += 0.2
        owner.accept_market(
            symbols[4],
            corrected,
            sessions=sessions,
            start=min(corrected),
            end=target,
            now=target,
            live_after=target,
            rebuild=True,
        )
        assert owner.market.instrument(symbols[4])["basis_revision"] > before_revision
        owner.accept_market(
            symbols[5],
            {t: r for t, r in five.items() if t <= target},
            sessions=sessions,
            start=min(five),
            end=target,
            now=target,
            live_after=target,
            rebuild=True,
            events=[dict(day=target[:10], ex_factor=1.1)],
            factor_response=True,
        )
        next_end = bars[252]["timestamp"]
        partial = {t: r for t, r in five.items() if target < t < next_end}
        owner.accept_market(
            symbols[6],
            partial,
            sessions=sessions,
            start=min(partial),
            end=next_end,
            now=next_end,
            live_after=next_end,
        )
        report["tail_peak"] = owner.conn.execute(
            "SELECT count(*) FROM tail5 WHERE symbol=?", (symbols[6],)
        ).fetchone()[0]
        assert report["tail_peak"] == 2
        owner.accept_market(
            symbols[6],
            {next_end: five[next_end]},
            sessions=sessions,
            start=next_end,
            end=next_end,
            now=next_end,
            live_after=next_end,
        )
        assert (
            owner.conn.execute(
                "SELECT count(*) FROM tail5 WHERE symbol=?", (symbols[6],)
            ).fetchone()[0]
            == 0
        )
        assert (
            owner.conn.execute("SELECT count(*) FROM signal_event").fetchone()[0]
            == original
        )
        report["boundary_points"] = {
            label: latest_point(sessions, at)
            for label, at in [
                ("lunch", "2026-01-26 12:30:00"),
                ("after_close", "2026-01-26 16:00:00"),
                ("non_session", "2026-01-31 12:00:00"),
            ]
        }
        assert report["boundary_points"]["lunch"].endswith("11:30:00")
        assert all(
            v.endswith("15:00:00")
            for k, v in report["boundary_points"].items()
            if k != "lunch"
        )
        assert owner.market.raw_rows(symbols[4])[0]["timestamp"] > old_first
        report["max_bar15"] = owner.conn.execute(
            "SELECT max(n) FROM (SELECT count(*) n FROM bar15 GROUP BY symbol)"
        ).fetchone()[0]
        report["max_tail5"] = owner.conn.execute(
            "SELECT coalesce(max(n),0) FROM (SELECT count(*) n FROM tail5 GROUP BY symbol)"
        ).fetchone()[0]
        report["failure_states"] = dict(
            owner.conn.execute(
                "SELECT state,count(*) FROM instrument_state GROUP BY state"
            )
        )
        report["events"] = owner.conn.execute(
            "SELECT count(*) FROM signal_event"
        ).fetchone()[0]
        report["wal_bytes"] = Path(str(path) + "-wal").stat().st_size
        report["quick_check"] = owner.conn.execute("PRAGMA quick_check").fetchone()[0]
        receipt = tuple(
            owner.conn.execute(
                "SELECT * FROM processing_receipt WHERE symbol=?", (symbols[-1],)
            ).fetchone()
        )
    with UnifiedStore(path) as reopened:
        report["restart_receipt_equal"] = receipt == tuple(
            reopened.conn.execute(
                "SELECT * FROM processing_receipt WHERE symbol=?", (symbols[-1],)
            ).fetchone()
        )
        report["restart_quick_check"] = reopened.conn.execute(
            "PRAGMA quick_check"
        ).fetchone()[0]
    stop_memory.set()
    monitor.join()
    report.update(
        peak_rss_bytes=max(rss),
        rss_start_bytes=rss[0],
        rss_end_bytes=rss[-1],
        cpu_seconds=sum(process.cpu_times()[:2]) - cpu0,
        db_bytes=path.stat().st_size,
        total_seconds=perf_counter() - started,
        writer_connections=1,
        personal_sdk_producers=1,
    )
    (folder / "db-metrics.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    (folder / "ma55-request-trace.json").write_text(
        json.dumps(traces), encoding="utf-8"
    )
    return report
