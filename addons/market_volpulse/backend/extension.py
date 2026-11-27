"""TSP extension v1 entry point. Startup schedules work on the host's single event loop."""
from pathlib import Path

from fastapi import APIRouter

from .worker import Worker

EXTENSION_ID = 'market.volpulse'
EXTENSION_API_VERSION = 1
_worker = None


def setup(registrar):
    router = APIRouter(prefix='/api/extensions/market.volpulse/v1')

    @router.get('/status')
    async def status():
        return dict(api_version=1, **(_worker.status if _worker else {'state': 'STARTING'}))

    registrar.include_router(router)


def startup(context):
    global _worker
    if _worker is None or _worker.task.done():
        _worker = Worker(Path(context.data_dir) / 'user_data' / 'market_volpulse')
        _worker.start()
