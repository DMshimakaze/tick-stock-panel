from types import SimpleNamespace
from fastapi import FastAPI
from fastapi.testclient import TestClient
from addons.lan_marketdata.backend import extension
from .test_rolling import opened, put, sample


def test_v2_is_bounded_and_keeps_saved_history_when_writer_stops(tmp_path, monkeypatch):
    days, rows = sample(250)
    store = opened(tmp_path, days)
    put(store, rows, days)
    demands = []
    worker = SimpleNamespace(
        store=store,
        status=lambda: dict(api_version=2, phase="SOURCE_UNAVAILABLE"),
        demand=lambda values: demands.extend(values),
    )
    monkeypatch.setattr(extension, "worker", worker)
    app = FastAPI()
    extension.setup(SimpleNamespace(include_router=app.include_router))
    with TestClient(app) as c:
        assert (
            c.get("/api/extensions/lan.marketdata/v2/status").json()["phase"]
            == "SOURCE_UNAVAILABLE"
        )
        assert c.get("/api/extensions/lan.marketdata/v2/universe").json()["total"] == 1
        response = c.get(
            "/api/extensions/lan.marketdata/v2/bars",
            params={"symbols": "600000.SH", "timeframes": "15m,30m,60m"},
        )
        assert response.status_code == 200
        frames = response.json()["rows"]
        assert (
            len(frames[0]["bars"]) == 250
            and len(frames[1]["bars"]) <= 125
            and len(frames[2]["bars"]) <= 62
        )
        assert (
            c.get(
                "/api/extensions/lan.marketdata/v2/bars",
                params={"symbols": "600000.SH", "limit": 251},
            ).status_code
            == 422
        )
        store.close()
        assert (
            c.get(
                "/api/extensions/lan.marketdata/v2/bars",
                params={"symbols": "600000.SH"},
            ).status_code
            == 200
        )
