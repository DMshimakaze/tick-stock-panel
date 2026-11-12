"""Official extension API v1 registration; plugin-owned market HTTP v2."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import sqlite3

from fastapi import APIRouter, HTTPException, Query

from .grid import CAPACITY, latest_point, now_cn, wall
from .runtime import Worker

EXTENSION_ID = "lan.marketdata"
EXTENSION_API_VERSION = 1
worker = None


def startup(context):
    global worker
    path = Path(context.data_dir) / "user_data" / "lan_marketdata" / "settings.json"
    settings = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    host = os.environ.get("BACKEND_HOST", "127.0.0.1")
    if host == "0.0.0.0":
        host = "127.0.0.1"
    settings.setdefault(
        "api_base", f"http://{host}:{os.environ.get('BACKEND_PORT', '3018')}"
    )
    worker = Worker(context, settings)
    worker.start()


def setup(registrar):
    router = APIRouter(prefix="/api/extensions/lan.marketdata/v2")

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

    @router.get("/status")
    def status():
        return worker.status() if worker else dict(api_version=2, phase="STARTING")

    @router.get("/universe")
    def universe(offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=100)):
        with ready() as store:
            page = store.universe_page(offset, limit)
            return dict(
                page,
                api_version=2,
                offset=offset,
                latest_completed_at=latest_point(page["sessions"], str(now_cn())),
            )

    @router.get("/bars")
    def bars(
        symbols: str,
        timeframes: str = "15m",
        asof: str | None = None,
        limit: int = Query(250, ge=1, le=250),
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
            page = store.universe_page(0, 1)
            current = latest_point(page["sessions"], str(now_cn()))
            try:
                cutoff = str(wall(asof)) if asof else current
            except ValueError:
                raise HTTPException(422, "asof must be Beijing time") from None
            if not cutoff or not current or cutoff > current:
                raise HTTPException(422, "asof exceeds latest completed exchange point")
            rows = [store.read(s, tf, cutoff, limit) for s in codes for tf in tfs]
            worker.demand(
                [
                    r["symbol"]
                    for r in rows
                    if r["timeframe"] == "15m"
                    and (
                        r["row_count"] < 250 or r.get("source_id") != "TICKFLOW_RAW_5M"
                    )
                ]
            )
            return dict(
                api_version=2,
                asof=cutoff,
                latest_completed_at=current,
                generation=page["generation"],
                data_revision=page["data_revision"],
                sessions=page["sessions"],
                service_status=worker.status(),
                rows=rows,
            )

    @router.get("/inventory")
    def inventory(symbol: str):
        if not re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", symbol):
            raise HTTPException(422, "canonical symbol required")
        with ready() as store:
            return dict(api_version=2, rows=store.inventory(symbol))

    registrar.include_router(router)
