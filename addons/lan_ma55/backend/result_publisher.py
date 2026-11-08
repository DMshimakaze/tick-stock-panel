"""SQLite projections published only through official ext-data HTTP contracts."""

import json

import httpx

SUMMARY_ID = "lan_ma55_summary"
DAY_ID = "lan_ma55_day"
HISTORY_ID = "lan_ma55_history"
DAY_FIELDS = [("symbol", "string"), ("name", "string"), ("market_date", "string"),
              ("generation", "int"), ("phase", "string"), ("category", "string"),
              ("reason", "string"), ("bar_end", "string"), ("processed_at", "string"),
              ("events_json", "string"), ("decision_json", "string")]


def view_configs():
    return [dict(id=SUMMARY_ID, label="MA55 后台扫描概览", mode="snapshot", market_level=True,
                 description="lan.ma55 SQLite派生展示缓存；行情与权威状态不在此表。",
                 fields=[dict(name=n, dtype=t, label=n) for n, t in
                         [("scope", "string"), ("payload_json", "string")]]),
            dict(id=DAY_ID, label="MA55 最新交易日结果", mode="snapshot", market_level=False,
                 description="每股每交易日的后台派生结果，原始确认时间与来源保留。",
                 fields=[dict(name=n, dtype=t, label=n) for n, t in DAY_FIELDS]),
            dict(id=HISTORY_ID, label="MA55 历史确认与观察", mode="snapshot", market_level=True,
                 description="保留窗口内的历史派生视图，固定scope覆盖写，不累积不可删除的日期分区。",
                 fields=[dict(name=n, dtype="string", label=n) for n in ("scope", "payload_json")])]


class Publisher:
    def __init__(self, base_url, token=None, *, transport=None):
        headers = {"Authorization": "Bearer " + token} if token else {}
        self.http = httpx.Client(base_url=base_url, headers=headers, timeout=30, trust_env=False, transport=transport)

    def prepare(self):
        """Explicit installation step; never edits an unrelated existing table."""
        response = self.http.get("/api/ext-data")
        response.raise_for_status()
        existing = {r["id"]: r for r in response.json()["items"]}
        for config in view_configs():
            if config["id"] in existing:
                current = existing[config["id"]]
                wanted = {(f["name"], f["dtype"]) for f in config["fields"]}
                actual = {(f["name"], f["dtype"]) for f in current["fields"]}
                if not wanted <= actual or current["mode"] != config["mode"]:
                    raise ValueError("MA55 view ID already has a different schema")
                continue
            result = self.http.post("/api/ext-data", json=config)
            result.raise_for_status()

    def summary(self, payload):
        return self.ingest(SUMMARY_ID, [dict(scope="lan.ma55", payload_json=json.dumps(payload, ensure_ascii=False, allow_nan=False))])

    def day(self, day, rows):
        if rows:
            return self.ingest(DAY_ID, rows, day)

    def history(self, days):
        return self.ingest(HISTORY_ID, [dict(scope="lan.ma55", payload_json=json.dumps(dict(days=days), ensure_ascii=False, allow_nan=False))])

    def ingest(self, table, rows, day=None):
        response = self.http.post(f"/api/ext-data/{table}/ingest", json=dict(rows=rows, date=day))
        response.raise_for_status()
        return response.json()

    def close(self):
        self.http.close()
