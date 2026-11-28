import json
from types import SimpleNamespace

import httpx

from addons.lan_ma55.backend.result_publisher import (
    COMMAND_ID,
    RECEIPT_ID,
    RUNTIME_ID,
    WORKBENCH_ID,
    Publisher,
)


def test_prepare_only_registers_the_four_current_tables_and_preserves_other_configs():
    existing = {"unrelated_user_table": {"id": "unrelated_user_table"}}
    created = []

    def official(request):
        assert request.url.path == "/api/ext-data"
        if request.method == "GET":
            return httpx.Response(200, json={"items": list(existing.values())})
        assert request.method == "POST"
        config = json.loads(request.content)
        created.append(config["id"])
        existing[config["id"]] = config
        return httpx.Response(200, json={"id": config["id"]})

    publisher = Publisher("http://localhost", transport=httpx.MockTransport(official))
    try:
        publisher.prepare()
        publisher.prepare()
    finally:
        publisher.close()

    assert created == [WORKBENCH_ID, RUNTIME_ID, COMMAND_ID, RECEIPT_ID]
    assert existing["unrelated_user_table"] == {"id": "unrelated_user_table"}


def test_stage_changes_are_coalesced_without_delaying_results_or_shutdown(tmp_path, monkeypatch):
    from addons.lan_ma55.backend import runtime

    clock = [100.0]
    monkeypatch.setattr(runtime, "monotonic", lambda: clock[0])
    writes = []

    def official(request):
        assert request.url.path == f"/api/ext-data/{RUNTIME_ID}/ingest"
        writes.append(json.loads(json.loads(request.content)["rows"][0]["payload_json"]))
        return httpx.Response(200, json={})

    worker = runtime.Worker(SimpleNamespace(data_dir=tmp_path), {"api_base": "http://localhost"})
    worker.publisher.close()
    worker.publisher = Publisher("http://localhost", transport=httpx.MockTransport(official))
    worker.bridge_prepared = True
    try:
        worker.publish_runtime()
        for tick in range(1, 50):
            clock[0] = 100 + tick / 10
            worker.report(phase="ANALYZING" if tick % 2 else "QUOTA_WAIT")
            worker.publish_runtime()
        assert len(writes) == 1
        clock[0] = 105
        worker.report(phase="QUOTA_WAIT")
        worker.publish_runtime()
        assert len(writes) == 2
        assert writes[-1]["phase"] == "QUOTA_WAIT"
        clock[0] = 114.9
        worker.publish_runtime()
        assert len(writes) == 2
        clock[0] = 115
        worker.publish_runtime()
        assert len(writes) == 3  # Unchanged heartbeat stays at 10 seconds.
        clock[0] = 115.1
        worker.published_view = dict(id="new-result", scope=(36, 1, 620), published_ms=123)
        worker.publish_runtime()
        assert len(writes) == 4
        assert writes[-1]["result_id"] == "new-result"
        clock[0] = 115.15
        worker.report(phase="A", active_operation="COLLECTING_NATIVE15", active_symbols=1)
        worker.publish_runtime()
        assert len(writes) == 5
        assert writes[-1]["active_operation"] == "COLLECTING_NATIVE15"
        clock[0] = 115.2
        worker.publish_runtime(alive=False)
        assert len(writes) == 6
        assert writes[-1]["worker_alive"] is False
    finally:
        worker.publisher.close()
        worker.http.close()
