"""Official backend extension contract; exposes read-only derived results.

startup only owns user_data/lan_ma55. No private host object or market file access.
No scanning worker is started while the host lacks the required public contracts.
"""

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

from .engine import RULE_VERSION
from .service import BLOCKED
from .store import StateWriter, read_page

EXTENSION_ID = "lan.ma55.state"
EXTENSION_API_VERSION = 1


class Runtime:
    """One instance per officially loaded extension module; startup owns its path."""

    def __init__(self):
        self.path = None
        self.retention_sessions = 60


runtime = Runtime()


def startup(context):
    folder = Path(context.data_dir) / "user_data" / "lan_ma55"
    settings = folder / "settings.json"
    config = (
        json.loads(settings.read_text(encoding="utf-8")) if settings.exists() else {}
    )
    retention = config.get("retention_sessions", 60)
    if type(retention) is not int or not 20 <= retention <= 252:
        raise ValueError("retention_sessions must be an integer in 20..252")
    path = folder / "state.sqlite3"
    with StateWriter(path):
        pass
    runtime.path = path
    runtime.retention_sessions = retention


def setup(registrar):
    router = APIRouter(prefix=f"/api/extensions/{EXTENSION_ID}")

    @router.get("/status")
    def status():
        return dict(
            status=BLOCKED,
            message="尚未完成全市场监控",
            full_market_complete=False,
            rule_version=RULE_VERSION,
            storage_ready=runtime.path is not None,
            backend_scanning=False,
            expected_symbols=None,
            processed_symbols=0,
            source_health="UNAVAILABLE_THROUGH_EXTENSION_CONTRACT",
            retention_sessions=runtime.retention_sessions,
            missing_contracts=[
                "stock_universe",
                "local_only_adjusted_minute_batch",
                "page_own_backend_bridge",
            ],
        )

    @router.get("/events")
    def events(
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
        symbol: str | None = None,
        day: str | None = None,
    ):
        return listing("events", limit, offset, symbol=symbol, day=day)

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
        if runtime.path is None:
            raise HTTPException(503, "MA55状态库尚未初始化")
        return {
            "full_market_complete": False,
            "mode": "OFFLINE",
            **read_page(runtime.path, kind, limit=limit, offset=offset, **filters),
        }

    registrar.include_router(router)
