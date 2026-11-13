"""SQLite projections published only through official ext-data HTTP contracts."""

import json

import httpx

SUMMARY_ID = "lan_ma55_summary"
DAY_ID = "lan_ma55_day"
HISTORY_ID = "lan_ma55_history"
PRICES_ID = "lan_ma55_prices_v1"
NATIVE_SUMMARY_ID = "lan_ma55_native15_summary_v1"
NATIVE_DAY_ID = "lan_ma55_native15_day_v1"
NATIVE_HISTORY_ID = "lan_ma55_native15_history_v1"
NATIVE_PRICES_ID = "lan_ma55_native15_prices_v1"
DAY_FIELDS = [
    ("symbol", "string"),
    ("name", "string"),
    ("market_date", "string"),
    ("generation", "int"),
    ("phase", "string"),
    ("category", "string"),
    ("reason", "string"),
    ("bar_end", "string"),
    ("processed_at", "string"),
    ("events_json", "string"),
    ("decision_json", "string"),
]


def view_configs():
    legacy = [
        dict(id=PRICES_ID, label="MA55 完成15m价格", mode="snapshot", market_level=True,
             description="单库已保存原价收盘，按交易日读取；不代表实时快照。",
             fields=[dict(name=n, dtype="string", label=n) for n in ("scope", "payload_json")]),
        dict(
            id=SUMMARY_ID,
            label="MA55 后台扫描概览",
            mode="snapshot",
            market_level=True,
            description="lan.ma55 SQLite派生展示缓存；行情与权威状态不在此表。",
            fields=[
                dict(name=n, dtype=t, label=n)
                for n, t in [("scope", "string"), ("payload_json", "string")]
            ],
        ),
        dict(
            id=DAY_ID,
            label="MA55 最新交易日结果",
            mode="snapshot",
            market_level=False,
            description="每股每交易日的后台派生结果，原始确认时间与来源保留。",
            fields=[dict(name=n, dtype=t, label=n) for n, t in DAY_FIELDS],
        ),
        dict(
            id=HISTORY_ID,
            label="MA55 历史确认与观察",
            mode="snapshot",
            market_level=True,
            description="保留窗口内的历史派生视图，固定scope覆盖写，不累积不可删除的日期分区。",
            fields=[
                dict(name=n, dtype="string", label=n) for n in ("scope", "payload_json")
            ],
        ),
    ]
    native_ids = {SUMMARY_ID: NATIVE_SUMMARY_ID, DAY_ID: NATIVE_DAY_ID,
                  HISTORY_ID: NATIVE_HISTORY_ID, PRICES_ID: NATIVE_PRICES_ID}
    return legacy + [dict(config, id=native_ids[config["id"]],
                          label="原生15m人工复核候选 · "+config["label"],
                          description="NATIVE15_REVIEW_V1，独立候选投影，不写入历史冻结BUY契约。") for config in legacy]


class Publisher:
    def __init__(self, base_url, token=None, *, session=None, transport=None, native=False):
        self.native = native
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
        return self.ingest(
            NATIVE_SUMMARY_ID if self.native else SUMMARY_ID,
            [
                dict(
                    scope="lan.ma55",
                    payload_json=json.dumps(
                        payload, ensure_ascii=False, allow_nan=False
                    ),
                )
            ],
        )

    def day(self, day, rows):
        if rows:
            return self.ingest(NATIVE_DAY_ID if self.native else DAY_ID, rows, day)

    def history(self, days):
        return self.ingest(
            NATIVE_HISTORY_ID if self.native else HISTORY_ID,
            [
                dict(
                    scope="lan.ma55",
                    payload_json=json.dumps(
                        dict(days=days), ensure_ascii=False, allow_nan=False
                    ),
                )
            ],
        )

    def prices(self, day, rows):
        return self.ingest(NATIVE_PRICES_ID if self.native else PRICES_ID, [dict(scope="lan.ma55", payload_json=json.dumps(
            dict(day=day, rows=rows), ensure_ascii=False, allow_nan=False))])

    def ingest(self, table, rows, day=None):
        response = self.http.post(
            f"/api/ext-data/{table}/ingest", json=dict(rows=rows, date=day)
        )
        response.raise_for_status()
        return response.json()

    def close(self):
        self.http.close()
