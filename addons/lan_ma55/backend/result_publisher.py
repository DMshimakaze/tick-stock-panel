"""SQLite projections published only through official ext-data HTTP contracts."""

import httpx

WORKBENCH_ID = "lan_ma55_sector_workbench_v1"
RUNTIME_ID = "lan_ma55_sector_runtime_v1"
COMMAND_ID = "lan_ma55_sector_command_v1"
RECEIPT_ID = "lan_ma55_sector_receipt_v1"
COMMAND_SLOTS = 16
COMMAND_LEASE_MS = 90_000
COMMAND_FIELDS = [("scope", "string"), ("client_id", "string"), ("request_id", "string"),
                  ("request_ms", "int"), ("payload_json", "string")]
def workbench_configs():
    return [
        dict(id=table, label=label, mode="snapshot", market_level=True,
             description="MA55 板块观察工作台；权威范围及行情保存在插件 SQLite。",
             fields=[dict(name=n, dtype=t, label=n) for n, t in fields])
        for table, label, fields in [
            (WORKBENCH_ID, "MA55 板块观察", [("scope", "string"), ("payload_json", "string")]),
            (RUNTIME_ID, "MA55 运行状态", [("scope", "string"), ("payload_json", "string")]),
            (COMMAND_ID, "MA55 观察请求", COMMAND_FIELDS),
            (RECEIPT_ID, "MA55 观察回执", COMMAND_FIELDS),
        ]
    ]


class Publisher:
    def __init__(self, base_url, token=None, *, session=None, transport=None):
        headers = {"Authorization": "Bearer " + token} if token else {}
        self.http = httpx.Client(
            base_url=base_url,
            headers=headers,
            cookies={"tf_session": session} if session else {},
            timeout=30,
            trust_env=False,
            transport=transport,
        )

    def prepare(self):
        """Explicit installation step; never edits an unrelated existing table."""
        response = self.http.get("/api/ext-data")
        response.raise_for_status()
        existing = {r["id"]: r for r in response.json()["items"]}
        for config in workbench_configs():
            if config["id"] in existing:
                current = existing[config["id"]]
                wanted = {(f["name"], f["dtype"]) for f in config["fields"]}
                actual = {(f["name"], f["dtype"]) for f in current["fields"]}
                if (not wanted <= actual or current["mode"] != config["mode"] or
                        current.get("market_level", False) != config["market_level"]):
                    raise ValueError("MA55 view ID already has a different schema")
                continue
            result = self.http.post("/api/ext-data", json=config)
            result.raise_for_status()

    def ingest(self, table, rows, day=None, *, timeout=httpx.USE_CLIENT_DEFAULT):
        response = self.http.post(
            f"/api/ext-data/{table}/ingest", json=dict(rows=rows, date=day), timeout=timeout
        )
        response.raise_for_status()
        return response.json()

    def rows(self, table, limit):
        response = self.http.get(f"/api/ext-data/{table}/rows", params={"limit": limit})
        response.raise_for_status()
        return response.json()["rows"]

    def close(self):
        self.http.close()
