import sys
from types import SimpleNamespace

import pytest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from addons.lan_ma55.build_backend import build
from addons.lan_ma55.backend.service import OfflineService, BLOCKED
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

    app = FastAPI()
    registry = BackendExtensionRegistry()
    registry.freeze()
    app.state.extension_registry = registry
    start_managed_extensions(app, ExtensionContext(1, folder, NamesOnly()))
    return app, TestClient(app)


def test_official_install_start_disable_uninstall_preserve_derived_data(
    tmp_path, scenarios
):
    sys.path.insert(0, str(ROOT / "backend"))
    from app.extensions.packages import ExtensionPackageStore

    archive = build(tmp_path / "package.zip")
    folder = tmp_path / "host data with spaces"
    packages = ExtensionPackageStore(folder)
    preview = packages.inspect(archive.read_bytes())
    assert not preview["errors"] and preview["manifest"]["id"] == "lan.ma55.state"
    assert "frontend" not in preview["manifest"]
    packages.install(archive.read_bytes(), preview["digest"], preview["revision"])
    app, client = boot(folder)
    status = client.get("/api/extensions/lan.ma55.state/status").json()
    assert status["status"] == BLOCKED and status["storage_ready"]
    assert status["expected_symbols"] is None and not status["full_market_complete"]
    assert not status["backend_scanning"]
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
        events = client.get("/api/extensions/lan.ma55.state/events?limit=1").json()
        assert (
            events["total"] == 1
            and events["mode"] == "OFFLINE"
            and not events["full_market_complete"]
        )
        assert events["rows"][0]["origin"] == "REPLAY"
    assert (
        client.get("/api/extensions/lan.ma55.state/events?offset=1").json()["rows"]
        == []
    )
    assert (
        client.get("/api/extensions/lan.ma55.state/events?limit=1000").status_code
        == 422
    )
    assert (
        client.post("/api/extensions/lan.ma55.state/events", json={}).status_code == 405
    )
    packages.manage("lan.ma55.state", "disable")
    _, disabled = boot(folder)
    assert disabled.get("/api/extensions/lan.ma55.state/status").status_code == 404
    assert path.is_file() and read_page(path, "events")["total"] == 1
    packages.manage("lan.ma55.state", "enable")
    _, enabled = boot(folder)
    assert enabled.get("/api/extensions/lan.ma55.state/events").json()["total"] == 1
    packages.manage("lan.ma55.state", "remove")
    _, removed = boot(folder)
    assert removed.get("/api/extensions/lan.ma55.state/status").status_code == 404
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
