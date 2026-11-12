"""Exercise the installed host's actual auth middleware, without changing core."""

from pathlib import Path
import sys
from types import SimpleNamespace

from fastapi import FastAPI
from contextlib import contextmanager
import socket
from threading import Thread
import time
import httpx
import uvicorn
from addons.lan_marketdata.backend import extension


@contextmanager
def live_client(app):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
    thread = Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    client = httpx.Client(
        base_url=f"http://127.0.0.1:{sock.getsockname()[1]}", timeout=5, trust_env=False
    )
    try:
        for _ in range(200):
            if server.started:
                break
            time.sleep(0.01)
        assert server.started
        yield client
    finally:
        client.close()
        server.should_exit = True
        thread.join(5)
        sock.close()


def test_host_password_off_on_session_and_api_token(tmp_path, monkeypatch):
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "backend"))
    from app.main import auth_middleware
    from app.api.auth import router
    from app.services import auth, api_tokens
    from app.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(auth, "_configured_cache", None)
    monkeypatch.setattr(auth, "_sessions", {})
    monkeypatch.setattr(
        extension,
        "worker",
        SimpleNamespace(status=lambda: dict(api_version=2, phase="READY")),
    )
    app = FastAPI()
    app.middleware("http")(auth_middleware)
    app.include_router(router)
    extension.setup(SimpleNamespace(include_router=app.include_router))
    with live_client(app) as client:
        path = "/api/extensions/lan.marketdata/v2/status"
        assert client.get(path).status_code == 200
        _, token = api_tokens.create_token(
            tmp_path, "test", ["read:ext", "write:ext", "read:market"]
        )
        response = client.get(path, headers={"Authorization": "Bearer " + token})
        assert response.status_code == 403 and "未对外开放" in response.json()["detail"]
        assert (
            client.post(
                "/api/auth/setup", json={"password": "isolated-test-password"}
            ).status_code
            == 200
        )
        assert client.get(path).status_code == 401
        assert (
            client.post(
                "/api/auth/login", json={"password": "isolated-test-password"}
            ).status_code
            == 200
        )
        assert client.get(path).status_code == 200
        # Token middleware is independent: a valid browser session does not enlarge it.
        assert (
            client.get(path, headers={"Authorization": "Bearer " + token}).status_code
            == 403
        )
