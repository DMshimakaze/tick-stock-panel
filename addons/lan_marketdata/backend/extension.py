"""Retirement package. No database, SDK, worker or forwarding service."""
from fastapi import APIRouter

EXTENSION_ID = "lan.marketdata"
EXTENSION_API_VERSION = 1


def setup(registrar):
    router = APIRouter(prefix="/api/extensions/lan.marketdata")

    @router.get("/status")
    def status():
        return dict(version="2.1.0",phase="RETIRED",producer=False,writer=False,
                    replacement="/api/extensions/lan.ma55/v1")

    registrar.include_router(router)
