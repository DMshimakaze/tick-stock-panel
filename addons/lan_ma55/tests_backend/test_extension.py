import sys
from types import SimpleNamespace

import httpx
import pytest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from addons.lan_ma55.build_backend import build
from addons.lan_ma55.backend.service import OfflineService
from addons.lan_ma55.backend.store import read_page
from .conftest import ROOT, batches, universe


def boot(folder):
    # Official host installer is used only in isolated protocol tests.
    sys.path.insert(0, str(ROOT / "backend"))
    from app.extensions.contracts import ExtensionContext
    from app.extensions.packages import start_managed_extensions
    from app.extensions.registry import BackendExtensionRegistry

    class NamesOnly:
        def get_name_map(self, symbols=None):
            raise AssertionError(
                "Blocked extension must not infer a market universe from names"
            )

    folder.mkdir(parents=True, exist_ok=True)
    settings = folder / "user_data" / "lan_ma55" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text('{"api_base":"http://127.0.0.1:1"}', encoding="utf-8")
    app = FastAPI()
    registry = BackendExtensionRegistry()
    registry.freeze()
    app.state.extension_registry = registry
    start_managed_extensions(app, ExtensionContext(1, folder, NamesOnly()))
    return app, TestClient(app)


def test_official_install_start_disable_uninstall_preserve_derived_data(
    tmp_path, scenarios, monkeypatch
):
    monkeypatch.delenv("QUANTDASH_TOKEN", raising=False)
    def offline_transport(self, request):
        raise httpx.ConnectError("Isolated protocol host has no HTTP listener", request=request)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", offline_transport)
    sys.path.insert(0, str(ROOT / "backend"))
    from app.extensions.packages import ExtensionPackageStore

    archive = build(tmp_path / "package.zip")
    folder = tmp_path / "host data with spaces"
    packages = ExtensionPackageStore(folder)
    preview = packages.inspect(archive.read_bytes())
    assert not preview["errors"] and preview["manifest"]["id"] == "lan.ma55"
    assert preview["manifest"]["frontend"] == "frontend/extension.mjs"
    packages.install(archive.read_bytes(), preview["digest"], preview["revision"])
    app, client = boot(folder)
    import time
    for _ in range(100):
        status = client.get("/api/extensions/lan.ma55/status").json()
        if status["phase"] == "DATA_SOURCE_UNAVAILABLE":
            break
        time.sleep(.01)
    assert status["phase"] == "DATA_SOURCE_UNAVAILABLE"
    import importlib
    for module in list(sys.modules.values()):
        if getattr(module, "EXTENSION_ID", None)=="lan.ma55" and getattr(module, "worker", None):
            module.worker.close()
    assert status["backend_scanning"]
    path = folder / "user_data" / "lan_ma55" / "state.sqlite3"
    case = scenarios["single"]
    stocks = universe(1)
    end = case["bars"][-1]["timestamp"]
    with OfflineService(path) as service:
        service.scan(
            universe=stocks,
            inputs=batches(stocks, case["bars"][-56:]),
            bar_end=end,
            sessions=case["sessions"],
            source_asof=end,
            first_seen_at="2026-11-07 20:00:00",
        )
    for _ in range(2):
        events = client.get("/api/extensions/lan.ma55/events?limit=1").json()
        assert events["total"] == 1
        assert events["rows"][0]["origin"] == "REPLAY"
    assert (
        client.get("/api/extensions/lan.ma55/events?offset=1").json()["rows"]
        == []
    )
    assert (
        client.get("/api/extensions/lan.ma55/events?limit=1000").status_code
        == 422
    )
    assert (
        client.post("/api/extensions/lan.ma55/events", json={}).status_code == 405
    )
    packages.manage("lan.ma55", "disable")
    _, disabled = boot(folder)
    assert disabled.get("/api/extensions/lan.ma55/status").status_code == 404
    assert path.is_file() and read_page(path, "events")["total"] == 1
    packages.manage("lan.ma55", "enable")
    _, enabled = boot(folder)
    enabled_response = enabled.get("/api/extensions/lan.ma55/events")
    assert enabled_response.status_code == 200, (enabled_response.text, packages.list_installed({}))
    assert enabled_response.json()["total"] == 1
    packages.manage("lan.ma55", "remove")
    _, removed = boot(folder)
    assert removed.get("/api/extensions/lan.ma55/status").status_code == 404
    assert path.is_file() and read_page(path, "events")["total"] == 1
    assert packages.list_installed({}) == []


@pytest.mark.parametrize("retention", [0, 1, 253, True, "60"])
def test_invalid_retention_fails_without_replacing_user_state(tmp_path, retention):
    import json
    from addons.lan_ma55.backend.extension import startup

    folder = tmp_path / "user_data" / "lan_ma55"
    folder.mkdir(parents=True)
    (folder / "settings.json").write_text(
        json.dumps({"retention_sessions": retention}), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="retention_sessions"):
        startup(SimpleNamespace(data_dir=tmp_path))
    assert not (folder / "state.sqlite3").exists()
