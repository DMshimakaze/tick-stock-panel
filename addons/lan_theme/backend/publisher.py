"""Two bounded publication banks keep the last complete snapshot readable."""

import httpx

from .schema import InvalidSnapshot, encoded

TREE = "lan_theme_tree"
STATUS = "lan_theme_status"
MAX_PARTS = 4096
ROW_LIMIT = MAX_PARTS * 2
PART_BYTES = 512 * 1024
TREE_FIELDS = [
    ("scope", "string"),
    ("bank", "string"),
    ("theme_id", "string"),
    ("snapshot_id", "string"),
    ("part", "int"),
    ("parts", "int"),
    ("tree_json", "string"),
]


def configs():
    return [
        dict(
            id=table,
            label=label,
            mode="snapshot",
            market_level=True,
            description="lan.theme owned bounded snapshot projection; no market bars or BUY authority",
            fields=[dict(name=n, dtype=t, label=n) for n, t in fields],
        )
        for table, label, fields in [
            (TREE, "题材分层快照", TREE_FIELDS),
            (
                STATUS,
                "题材快照来源状态",
                [("scope", "string"), ("payload_json", "string")],
            ),
        ]
    ]


def split_theme(theme):
    header = {k: v for k, v in theme.items() if k not in ("nodes", "memberships")}
    parts = []
    piece = dict(theme=header, nodes=[], memberships=[])
    size = len(encoded(piece).encode())
    for field in ("nodes", "memberships"):
        for row in theme[field]:
            amount = len(encoded(row).encode()) + 1
            if amount > PART_BYTES // 2:
                raise InvalidSnapshot("SINGLE_RELATION_EXCEEDS_PUBLICATION_BOUND")
            if size + amount > PART_BYTES:
                parts.append(piece)
                piece = dict(theme_id=theme["theme_id"], nodes=[], memberships=[])
                size = len(encoded(piece).encode())
            piece[field].append(row)
            size += amount
    parts.append(piece)
    return parts


class Publisher:
    def __init__(self, base, token=None, *, session=None, transport=None):
        self.http = httpx.Client(
            base_url=base,
            timeout=40,
            trust_env=False,
            transport=transport,
            headers={"Authorization": "Bearer " + token} if token else {},
            cookies={"tf_session": session} if session else {},
        )

    def prepare(self):
        response = self.http.get("/api/ext-data")
        response.raise_for_status()
        existing = {r["id"]: r for r in response.json()["items"]}
        for config in configs():
            old = existing.get(config["id"])
            if old:
                if (
                    old["mode"] != "snapshot"
                    or old.get("market_level") is not True
                    or [(f["name"], f["dtype"]) for f in old["fields"]]
                    != [(f["name"], f["dtype"]) for f in config["fields"]]
                ):
                    raise InvalidSnapshot("OWNED_VIEW_SCHEMA_CONFLICT")
            else:
                self.http.post("/api/ext-data", json=config).raise_for_status()

    def rows(self, table, *, columns=None, limit=ROW_LIMIT):
        params = dict(limit=limit)
        if columns:
            params["columns"] = ",".join(columns)
        response = self.http.get(f"/api/ext-data/{table}/rows", params=params)
        response.raise_for_status()
        data = response.json()
        if data["total"] > limit:
            raise InvalidSnapshot("OWNED_VIEW_ROW_BOUND_EXCEEDED")
        return data["rows"]

    def ingest(self, table, rows):
        # Bound each HTTP write; individual large themes use multiple JSON parts.
        batch, size = [], 0
        for row in rows:
            amount = len(encoded(row).encode())
            if batch and size + amount > 1024 * 1024:
                self.http.post(
                    f"/api/ext-data/{table}/ingest", json=dict(rows=batch)
                ).raise_for_status()
                batch, size = [], 0
            batch.append(row)
            size += amount
        if batch:
            self.http.post(
                f"/api/ext-data/{table}/ingest", json=dict(rows=batch)
            ).raise_for_status()

    def publish(self, snapshot, state):
        import json

        previous_rows = self.rows(STATUS, limit=1)
        previous = json.loads(previous_rows[0]["payload_json"]) if previous_rows else {}
        snapshot_id = snapshot["snapshot_id"] if snapshot else None
        bank, total = previous.get("bank"), previous.get("parts_total", 0)
        if snapshot and previous.get("snapshot_id") != snapshot_id:
            bank = "B" if bank == "A" else "A"
            rows = []
            for theme in snapshot["themes"]:
                pieces = split_theme(theme)
                for part, piece in enumerate(pieces):
                    rows.append(
                        dict(
                            scope=f"{bank}:{len(rows)}",
                            bank=bank,
                            theme_id=theme["theme_id"],
                            snapshot_id=snapshot_id,
                            part=part,
                            parts=len(pieces),
                            tree_json=encoded(piece),
                        )
                    )
            if len(rows) > MAX_PARTS:
                raise InvalidSnapshot("SNAPSHOT_EXCEEDS_4096_PARTS")
            total = len(rows)
            stale = self.rows(TREE, columns=["scope", "bank"], limit=ROW_LIMIT)
            current = {r["scope"] for r in rows}
            rows.extend(
                dict(
                    scope=r["scope"],
                    bank=bank,
                    theme_id="",
                    snapshot_id="",
                    part=0,
                    parts=0,
                    tree_json="",
                )
                for r in stale
                if r["bank"] == bank and r["scope"] not in current
            )
            self.ingest(TREE, rows)
        # Only this final atomic row switches readers to the complete new bank.
        published = dict(state, bank=bank, parts_total=total, snapshot_id=snapshot_id)
        self.ingest(STATUS, [dict(scope="lan.theme", payload_json=encoded(published))])
        return published

    def close(self):
        self.http.close()
