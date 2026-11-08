"""Official lifecycle and read-only operations; page uses only ctx.api."""
import json
import os
from pathlib import Path

from fastapi import APIRouter

from .service import Worker

EXTENSION_ID = 'lan.kline'
EXTENSION_API_VERSION = 1
worker = None


def startup(context):
    global worker
    config_path = Path(context.data_dir)/'user_data'/'lan_kline'/'settings.json'
    config = json.loads(config_path.read_text(encoding='utf-8')) if config_path.exists() else {}
    host = os.environ.get('BACKEND_HOST','127.0.0.1')
    if host == '0.0.0.0':
        host = '127.0.0.1'
    settings = dict(config, api_base=config.get('api_base', f"http://{host}:{os.environ.get('BACKEND_PORT','3018')}"),
                    api_token=config.get('api_token') or os.environ.get('KLINE_TSP_EXT_TOKEN'))
    worker = Worker(context, settings)
    worker.start()


def setup(registrar):
    router = APIRouter(prefix='/api/extensions/lan.kline')

    @router.get('/status')
    def status():
        return worker.status() if worker else dict(phase='NOT_STARTED')

    registrar.include_router(router)
