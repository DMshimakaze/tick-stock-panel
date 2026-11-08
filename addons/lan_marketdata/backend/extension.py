"""Official extension v1 router; host auth middleware owns authentication."""

import json
from contextlib import contextmanager
from pathlib import Path
import re

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
    worker = Worker(context, settings)
    worker.start()


def setup(registrar):
    router = APIRouter(prefix="/api/extensions/lan.marketdata/v1")

    @contextmanager
    def ready():
        state = worker.status() if worker else dict(phase="STARTING")
        phase = state.get("phase", "STARTING")
        message = f"DATA_SOURCE_UNAVAILABLE: lan.marketdata {phase}"
        if state.get("error_code"):
            message += " (" + state["error_code"] + ")"
        store = worker.store if worker else None
        if (
            store is None
            or phase in ("STARTING", "ERROR", "RECOVERING_DB")
            or state.get("writer_alive") is False
        ):
            raise HTTPException(503, message)
        # Reopen and API reads use the same store lock. A request cannot use a
        # connection that the writer just closed for recovery.
        with store.lock:
            if store.conn is None:
                raise HTTPException(503, message)
            yield store

    @router.get("/status")
    def status():
        return worker.status() if worker else dict(api_version=1, phase="NOT_STARTED")

    @router.get("/universe")
    def universe(offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=100)):
        with ready() as store:
            total, rows = store.universe_page(offset, limit)
            sessions = store.meta("sessions", [])
            return dict(
                api_version=1,
                generation=store.meta("generation", 0),
                total=total,
                offset=offset,
                rows=rows,
                universe=store.meta("universe", {}),
                sessions=sessions,
                latest_completed_at=latest_point(sessions, str(now_cn()), step=15),
            )

    @router.get("/bars")
    def bars(
        symbols: str,
        timeframes: str = "15m",
        asof: str | None = None,
        limit: int = Query(256, ge=1, le=256),
    ):
        codes = list(dict.fromkeys(symbols.split(",")))
        tfs = list(dict.fromkeys(timeframes.split(",")))
        if not 1 <= len(codes) <= 32 or any(
            not re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", s) for s in codes
        ):
            raise HTTPException(
                422, "symbols must contain 1..32 canonical exchange symbols"
            )
        if (
            not tfs
            or any(tf not in CAPACITY for tf in tfs)
            or len(codes) * len(tfs) * limit > 8192
        ):
            raise HTTPException(422, "Invalid timeframes or response exceeds 8192 bars")
        with ready() as store:
            current = latest_point(store.meta("sessions", []), str(now_cn()))
            try:
                cutoff = str(wall(asof)) if asof else current
            except ValueError:
                raise HTTPException(422, "asof must be Beijing wall time") from None
            if not current or not cutoff or cutoff > current:
                raise HTTPException(422, "asof exceeds latest completed exchange point")
            metadata = {s: store.instrument(s) for s in codes}
            missing = [
                s
                for s, row in metadata.items()
                if row is None
                or row.get("source_type") == "etf"
                or row.get("warm_count", 0) < 256
                or row.get("rebuild_pending")
                or row.get("gap_count")
            ]
            worker.demand(missing)
            return dict(
                api_version=1,
                asof=cutoff,
                latest_completed_at=current,
                generation=store.meta("generation", 0),
                sessions=store.meta("sessions", []),
                service_status=worker.status(),
                rows=[store.read(s, tf, cutoff, limit) for s in codes for tf in tfs],
            )

    @router.get("/inventory")
    def inventory(symbol: str):
        if not re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", symbol):
            raise HTTPException(422, "canonical symbol required")
        with ready() as store:
            return dict(api_version=1, rows=store.inventory(symbol))

    registrar.include_router(router)
