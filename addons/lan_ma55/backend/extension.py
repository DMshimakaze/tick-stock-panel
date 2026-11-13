"""Official lan.ma55 package: startup owns the shared-data consumer."""

import json
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from contextlib import contextmanager
import re
import sqlite3
from .market_grid import CAPACITY, latest_point, now_cn, wall

from .runtime import Worker
from .store import read_page
from .native15_owner import candidates_page

EXTENSION_ID = "lan.ma55"
EXTENSION_API_VERSION = 1
worker = None
data_path = None


def startup(context):
    global worker, data_path
    folder = Path(context.data_dir) / "user_data" / "lan_ma55"
    config_file = folder / "settings.json"
    config = (
        json.loads(config_file.read_text(encoding="utf-8"))
        if config_file.exists()
        else {}
    )
    retention = config.get("retention_sessions", 60)
    if type(retention) is not int or not 20 <= retention <= 252:
        raise ValueError("retention_sessions must be an integer in 20..252")
    host = os.environ.get("BACKEND_HOST", "127.0.0.1")
    if host == "0.0.0.0":
        host = "127.0.0.1"
    settings = dict(
        config,
        retention_sessions=retention,
        api_base=config.get(
            "api_base", f"http://{host}:{os.environ.get('BACKEND_PORT', '3018')}"
        ),
        api_token=config.get("api_token") or os.environ.get("MA55_TSP_EXT_TOKEN"),
    )
    data_path = folder / "state.sqlite3"
    worker = Worker(context, settings)
    worker.start()


def setup(registrar):
    router = APIRouter(prefix="/api/extensions/lan.ma55")
    public = APIRouter(prefix="/api/extensions/lan.ma55/v1")

    @public.get("/status")
    @router.get("/status")
    def status():
        return (
            worker.status()
            if worker
            else dict(phase="NOT_STARTED", backend_scanning=False)
        )

    @public.get("/events")
    @router.get("/events")
    def events(
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
        symbol: str | None = None,
        day: str | None = None,
        start: str | None = None,
        end: str | None = None,
        rule_version: str | None = None,
    ):
        if symbol and not re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", symbol):
            raise HTTPException(422, "canonical symbol required")
        try:
            start = str(wall(start)) if start else None
            end = str(wall(end)) if end else None
        except ValueError:
            raise HTTPException(422, "start/end must be Beijing time") from None
        if start and end and start > end:
            raise HTTPException(422, "start exceeds end")
        result = listing(
            "events",
            limit,
            offset,
            symbol=symbol,
            day=day,
            start=start,
            end=end,
            rule_version=rule_version,
        )
        result["symbol_total"] = (
            listing("events", 1, 0, symbol=symbol, rule_version=rule_version)["total"]
            if symbol
            else result["total"]
        )
        return dict(api_version=1, **result)

    @public.get("/candidates")
    def candidates(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
                   symbol: str | None = None, start: str | None = None, end: str | None = None):
        if symbol and not re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", symbol):
            raise HTTPException(422, "canonical symbol required")
        try:
            start = str(wall(start)) if start else None
            end = str(wall(end)) if end else None
        except ValueError:
            raise HTTPException(422, "start/end must be Beijing time") from None
        if start and end and start > end:
            raise HTTPException(422, "start exceeds end")
        if data_path is None or not data_path.exists():
            raise HTTPException(503, "Native15 starting")
        try:
            return candidates_page(data_path, limit=limit, offset=offset, symbol=symbol, start=start, end=end)
        except sqlite3.Error:
            raise HTTPException(503, "Native15 candidate partition not initialized") from None

    @router.get("/symbols")
    def symbols(
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
        phase: str | None = None,
    ):
        return listing("symbols", limit, offset, phase=phase)

    @router.get("/cycles")
    def cycles(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
        return listing("cycles", limit, offset)

    def listing(kind, limit, offset, **filters):
        if data_path is None or not data_path.exists():
            raise HTTPException(503, "MA55后台尚未初始化，查看状态中的具体原因")
        return read_page(data_path, kind, limit=limit, offset=offset, **filters)

    @contextmanager
    def ready():
        if worker is None or worker.store is None:
            raise HTTPException(503, "DATA_SOURCE_UNAVAILABLE: SQLite starting")
        try:
            yield worker.store
        except sqlite3.Error:
            raise HTTPException(
                503, "DATA_SOURCE_UNAVAILABLE: SQLite read failed; saved file retained"
            ) from None

    @public.get("/universe")
    def universe(offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=100)):
        with ready() as store:
            page = store.universe_page(offset, limit)
            return dict(
                page,
                api_version=1,
                offset=offset,
                latest_completed_at=latest_point(page["sessions"], str(now_cn())),
            )

    @public.get("/bars")
    def bars(
        symbols: str,
        timeframes: str = "15m",
        asof: str | None = None,
        limit: int = Query(250, ge=1, le=250),
        source: str = "native",
    ):
        codes, tfs = (
            list(dict.fromkeys(symbols.split(","))),
            list(dict.fromkeys(timeframes.split(","))),
        )
        if not 1 <= len(codes) <= 32 or any(
            not re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", s) for s in codes
        ):
            raise HTTPException(422, "1..32 canonical exchange symbols required")
        if (
            not tfs
            or any(tf not in CAPACITY for tf in tfs)
            or len(codes) * len(tfs) * limit > 8000
        ):
            raise HTTPException(422, "15m/30m/60m only; response limited to 8000 bars")
        with ready() as store:
            if source not in ("native", "archive"):
                raise HTTPException(422, "source must be native or archive")
            if source == "archive":
                store = getattr(worker.owner, "archive_market", store)
            page = store.universe_page(0, 1)
            current = latest_point(page["sessions"], str(now_cn()))
            try:
                cutoff = str(wall(asof)) if asof else current
            except ValueError:
                raise HTTPException(422, "asof must be Beijing time") from None
            if not cutoff or not current or cutoff > current:
                raise HTTPException(422, "asof exceeds latest completed exchange point")
            rows = [store.read(s, tf, cutoff, limit) for s in codes for tf in tfs]
            worker.demand_cache(
                [
                    r["symbol"]
                    for r in rows
                    if not r.get("cache", {}).get("hit")
                ]
            )
            return dict(
                api_version=1,
                asof=cutoff,
                latest_completed_at=current,
                generation=page["generation"],
                data_revision=page["data_revision"],
                sessions=page["sessions"],
                service_status=worker.status(),
                rows=rows,
            )

    @public.get("/inventory")
    def inventory(symbol: str):
        if not re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", symbol):
            raise HTTPException(422, "canonical symbol required")
        with ready() as store:
            return dict(api_version=1, rows=store.inventory(symbol))

    registrar.include_router(router)
    registrar.include_router(public)
