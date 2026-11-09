"""Official lifecycle: publish local metadata, never request KPL or market data."""

import atexit
import json
import os
from pathlib import Path
from threading import Event, Lock, Thread

from fastapi import APIRouter
import httpx

from .publisher import Publisher
from .schema import InvalidSnapshot, now_cn
from .store import SnapshotStore

EXTENSION_ID = "lan.theme"
EXTENSION_API_VERSION = 1
worker = None


class Worker:
    def __init__(self, context, settings):
        self.folder = Path(context.data_dir) / "user_data" / "lan_theme"
        self.store = SnapshotStore(self.folder)
        self.settings = settings
        host = os.environ.get("BACKEND_HOST", "127.0.0.1")
        if host == "0.0.0.0":
            host = "127.0.0.1"
        base = settings.get(
            "api_base", f"http://{host}:{os.environ.get('BACKEND_PORT', '3018')}"
        )
        self.publisher = Publisher(
            base, settings.get("api_token"), session=settings.get("api_session")
        )
        self.stop, self.lock = Event(), Lock()
        self.state = dict(
            version="1.0.2",
            api_version=1,
            phase="STARTING",
            error=None,
            online_status="ONLINE_KPL_NOT_VERIFIED",
            online_enabled=False,
            source_requests=0,
            market_requests=0,
            period_1m=0,
            import_interface="INSTALLED_PACKAGE_CLI",
        )
        self.thread = Thread(
            target=self.run, name="lan-theme-local-publisher", daemon=True
        )

    def status(self, data=None):
        with self.lock:
            state = dict(self.state, worker_alive=self.thread.is_alive())
        if data is None:
            data = self.store.read()
        snapshot = data["snapshot"]
        state.update(
            revision=data["revision"],
            last_import_error=data["last_error"],
            snapshot_id=snapshot["snapshot_id"] if snapshot else None,
            snapshot_state="STALE"
            if data["last_error"] and snapshot
            else "READY"
            if snapshot
            else "SOURCE_UNAVAILABLE"
            if data["last_error"]
            else "AUTHORIZED_SNAPSHOT_REQUIRED",
            snapshot_asof=snapshot["asof_cn"] if snapshot else None,
            source=snapshot["source"] if snapshot else None,
            source_kind=snapshot["source_kind"] if snapshot else None,
            provenance=snapshot.get("provenance") if snapshot else None,
            identity_provenance=snapshot.get("identity_provenance")
            if snapshot
            else None,
            business_status="SNAPSHOT_IMPORTED_VALIDATION_REQUIRED"
            if snapshot and not snapshot["demo"] and snapshot.get("provenance")
            else "BLOCKED_AUTHORIZED_KPL_DATA",
            installed_cli="data/extensions/lan.theme/versions/1.0.2: python -m backend.cli --data-dir <TSP_DATA_DIR> import --help",
            catalog_coverage=snapshot["catalog_coverage"] if snapshot else "UNKNOWN",
            coverage_reason=snapshot["coverage_reason"]
            if snapshot
            else "Authorized snapshot not imported",
            coverage=snapshot["coverage"] if snapshot else {},
            counts=snapshot["counts"]
            if snapshot
            else dict(
                themes=0,
                nodes=0,
                memberships=0,
                source_stock_identities=0,
                unresolved_memberships=0,
                resolved_symbols=0,
                unresolved_stock_identities=0,
            ),
            demo=snapshot["demo"] if snapshot else False,
        )
        return state

    def start(self):
        self.thread.start()
        atexit.register(self.close)

    def run(self):
        prepared, signature = False, None
        try:
            while not self.stop.is_set():
                try:
                    if not prepared:
                        self.publisher.prepare()
                        prepared = True
                    data = self.store.read()
                    snapshot = data["snapshot"]
                    if (
                        snapshot
                        and snapshot["demo"]
                        and self.settings.get("allow_demo") is not True
                    ):
                        raise InvalidSnapshot("DEMO_NOT_ALLOWED_IN_THIS_INSTALLATION")
                    if signature != data["revision"]:
                        with self.lock:
                            self.state.update(phase="PUBLISHING", error=None)
                        state = self.status(data)
                        state.update(
                            phase=state["snapshot_state"],
                            published_at=now_cn(),
                        )
                        published = self.publisher.publish(snapshot, state)
                        signature = data["revision"]
                        with self.lock:
                            self.state.update(
                                phase=published["phase"],
                                error=None,
                                published_at=published["published_at"],
                            )
                except (httpx.HTTPError, ValueError, KeyError):
                    with self.lock:
                        self.state.update(
                            phase="PUBLICATION_UNAVAILABLE",
                            error="Local snapshot retained; official data-view publication unavailable. Check permissions/session and local import status.",
                        )
                self.stop.wait(5)
        finally:
            self.publisher.close()

    def close(self):
        self.stop.set()
        if self.thread.is_alive():
            self.thread.join(45)


def startup(context):
    global worker
    path = Path(context.data_dir) / "user_data" / "lan_theme" / "settings.json"
    settings = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    worker = Worker(context, settings)
    worker.start()


def setup(registrar):
    router = APIRouter(prefix="/api/extensions/lan.theme")

    @router.get("/status")
    def status():
        return worker.status() if worker else dict(api_version=1, phase="STARTING")

    @router.get("/snapshots")
    def snapshots():
        return dict(items=worker.store.list_snapshots() if worker else [])

    registrar.include_router(router)
