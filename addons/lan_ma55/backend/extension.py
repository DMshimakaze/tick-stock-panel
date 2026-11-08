"""Official lan.ma55 package: startup owns the dedicated QuantDash worker."""

import json
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

from .runtime import Worker
from .store import read_page

EXTENSION_ID = 'lan.ma55'
EXTENSION_API_VERSION = 1
worker = None
data_path = None


def startup(context):
    global worker, data_path
    folder = Path(context.data_dir) / 'user_data' / 'lan_ma55'
    config_file = folder / 'settings.json'
    config = json.loads(config_file.read_text(encoding='utf-8')) if config_file.exists() else {}
    retention = config.get('retention_sessions', 60)
    if type(retention) is not int or not 20 <= retention <= 252:
        raise ValueError('retention_sessions must be an integer in 20..252')
    host = os.environ.get('BACKEND_HOST', '127.0.0.1')
    if host == '0.0.0.0':
        host = '127.0.0.1'
    settings = dict(config, retention_sessions=retention,
                    api_base=config.get('api_base', f"http://{host}:{os.environ.get('BACKEND_PORT', '3018')}"),
                    api_token=config.get('api_token') or os.environ.get('MA55_TSP_EXT_TOKEN'))
    data_path = folder / 'state.sqlite3'
    worker = Worker(context, settings)
    worker.start()


def setup(registrar):
    router = APIRouter(prefix='/api/extensions/lan.ma55')

    @router.get('/status')
    def status():
        return worker.status() if worker else dict(phase='NOT_STARTED', backend_scanning=False)

    @router.get('/events')
    def events(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
               symbol: str | None = None, day: str | None = None):
        return listing('events', limit, offset, symbol=symbol, day=day)

    @router.get('/symbols')
    def symbols(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
                phase: str | None = None):
        return listing('symbols', limit, offset, phase=phase)

    @router.get('/cycles')
    def cycles(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
        return listing('cycles', limit, offset)

    def listing(kind, limit, offset, **filters):
        if data_path is None or not data_path.exists():
            raise HTTPException(503, 'MA55后台尚未初始化，查看状态中的具体原因')
        return read_page(data_path, kind, limit=limit, offset=offset, **filters)

    registrar.include_router(router)
