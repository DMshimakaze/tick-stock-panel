import json

import httpx
import pytest

from addons.lan_theme.backend.normalize import normalize_info
from addons.lan_theme.backend import publisher as module
from addons.lan_theme.backend.publisher import Publisher, STATUS, TREE


def test_atomic_banks_chunking_and_owned_only(raw_theme, monkeypatch):
    views = {}
    writes = []
    fail = False

    def server(request):
        nonlocal fail
        path = request.url.path
        if path == "/api/ext-data":
            if request.method == "GET":
                return httpx.Response(
                    200, json=dict(items=[v["config"] for v in views.values()])
                )
            config = json.loads(request.content)
            assert config["id"] in (TREE, STATUS)
            views[config["id"]] = dict(config=config, rows={})
            return httpx.Response(200, json=config)
        _, _, _, table, action = path.split("/")
        assert table in (TREE, STATUS)
        if action == "rows":
            rows = list(views[table]["rows"].values())
            return httpx.Response(200, json=dict(rows=rows, total=len(rows)))
        writes.append(table)
        if fail and table == TREE:
            fail = False
            return httpx.Response(503)
        for row in json.loads(request.content)["rows"]:
            views[table]["rows"][row["scope"]] = row
        return httpx.Response(200, json=dict(ok=True))

    client = Publisher("http://local", transport=httpx.MockTransport(server))
    client.prepare()
    theme = normalize_info(raw_theme, asof="2026-11-09 01:00:00", known_symbols=[])
    monkeypatch.setattr(module, "PART_BYTES", 1200)
    theme["memberships"] *= 20
    snapshot = dict(snapshot_id="one", themes=[theme])
    first = client.publish(snapshot, dict(api_version=1))
    assert first["bank"] == "A" and first["parts_total"] > 1
    tree_writes = writes.count(TREE)
    client.publish(snapshot, dict(api_version=1, snapshot_state="STALE"))
    assert writes.count(TREE) == tree_writes
    snapshot["snapshot_id"] = "two"
    fail = True
    with pytest.raises(httpx.HTTPStatusError):
        client.publish(snapshot, dict(api_version=1))
    assert (
        json.loads(client.rows(STATUS, limit=1)[0]["payload_json"])["snapshot_id"]
        == "one"
    )
    second = client.publish(snapshot, dict(api_version=1))
    assert second["bank"] == "B"
    snapshot["snapshot_id"] = "three"
    theme["memberships"] = theme["memberships"][:1]
    third = client.publish(snapshot, dict(api_version=1))
    rows = client.rows(TREE)
    assert third["bank"] == "A"
    assert len([r for r in rows if r["snapshot_id"] == "three"]) == third["parts_total"]
    assert len(rows) <= 8192
    client.close()
