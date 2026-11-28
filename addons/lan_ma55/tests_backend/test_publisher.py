import json

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
