"""Official ext-data bridge. Fixed slots bound snapshot merge cardinality."""

import re
import time

import httpx

DEMAND = "lan_kline_demand"
VIEW = "lan_kline_view"
SLOTS = 16
LEASE_MS = 90_000
DEMAND_FIELDS = [
    ("scope", "string"),
    ("client_id", "string"),
    ("requested_symbol", "string"),
    ("request_ms", "int"),
    ("request_id", "string"),
    ("period", "string"),
    ("pinned", "int"),
]
VIEW_FIELDS = [
    ("scope", "string"),
    ("client_id", "string"),
    ("requested_symbol", "string"),
    ("request_id", "string"),
    ("revision", "string"),
    ("asof", "string"),
    ("phase", "string"),
    ("error", "string"),
    ("status_json", "string"),
    ("payload_json", "string"),
]


def configs():
    return [
        dict(
            id=table,
            label=label,
            mode="snapshot",
            market_level=True,
            description="lan.kline专用固定16槽请求/当前图表视图；权威行情由lan.marketdata管理。",
            fields=[dict(name=n, dtype=t, label=n) for n, t in fields],
        )
        for table, label, fields in [
            (DEMAND, "K线工作台需求", DEMAND_FIELDS),
            (VIEW, "K线工作台当前视图", VIEW_FIELDS),
        ]
    ]


class Publisher:
    def __init__(self, base, token=None, *, session=None, transport=None):
        self.http = httpx.Client(
            base_url=base,
            headers={"Authorization": "Bearer " + token} if token else {},
            cookies={"tf_session": session} if session else {},
            timeout=30,
            trust_env=False,
            transport=transport,
        )

    def prepare(self):
        response = self.http.get("/api/ext-data")
        response.raise_for_status()
        existing = {r["id"]: r for r in response.json()["items"]}
        for config in configs():
            current = existing.get(config["id"])
            if current:
                if (
                    current["mode"] != "snapshot"
                    or not current["market_level"]
                    or [(f["name"], f["dtype"]) for f in current["fields"]]
                    != [(f["name"], f["dtype"]) for f in config["fields"]]
                ):
                    raise ValueError("Kline bridge ID has a different schema")
            else:
                self.http.post("/api/ext-data", json=config).raise_for_status()

    def demands(self):
        response = self.http.get(
            f"/api/ext-data/{DEMAND}/rows", params={"limit": SLOTS}
        )
        response.raise_for_status()
        now = int(time.time() * 1000)
        return [
            r
            for r in response.json()["rows"]
            if str(r["scope"]) in {str(i) for i in range(SLOTS)}
            and 0 <= now - r["request_ms"] < LEASE_MS
            and re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", r["requested_symbol"])
            and r["period"] in ("15m", "30m", "60m")
            and r["pinned"] in (-1, 0, 1)
        ]

    def publish(self, rows):
        if rows:
            self.http.post(
                f"/api/ext-data/{VIEW}/ingest", json={"rows": rows}
            ).raise_for_status()

    def close(self):
        self.http.close()
