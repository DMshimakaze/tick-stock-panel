"""Five immutable metadata snapshots and an atomic active pointer; no K lines."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3

from .schema import InvalidSnapshot, encoded, now_cn


class ImportLease:
    def __init__(self, path):
        self.file = open(path, "a+b")
        self.file.seek(0, 2)
        if not self.file.tell():
            self.file.write(b"0")
            self.file.flush()
        self.file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise InvalidSnapshot(
                "IMPORT_BUSY: another import owns the writer lease"
            ) from None

    def close(self):
        self.file.close()


class SnapshotStore:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.path = self.folder / "themes.sqlite3"
        with self.write() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS snapshot(seq INTEGER PRIMARY KEY AUTOINCREMENT, snapshot_id TEXT UNIQUE NOT NULL, source_hash TEXT NOT NULL, payload TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,payload TEXT NOT NULL)"
            )

    @contextmanager
    def write(self):
        lease = ImportLease(self.folder / "import.lock")
        try:
            with sqlite3.connect(self.path, timeout=5) as db:
                db.execute("PRAGMA journal_mode=WAL")
                db.execute("BEGIN IMMEDIATE")
                yield db
        finally:
            lease.close()

    @staticmethod
    def meta(db, key, default=None):
        row = db.execute("SELECT payload FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    @staticmethod
    def set_meta(db, key, value):
        db.execute(
            "INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET payload=excluded.payload",
            (key, encoded(value)),
        )

    def read(self):
        with sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True) as db:
            active = self.meta(db, "active")
            row = db.execute(
                "SELECT payload FROM snapshot WHERE snapshot_id=?", (active,)
            ).fetchone()
            return dict(
                snapshot=json.loads(row[0]) if row else None,
                revision=self.meta(db, "revision", 0),
                last_error=self.meta(db, "last_error"),
                last_attempt=self.meta(db, "last_attempt"),
            )

    def list_snapshots(self):
        with sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True) as db:
            return [
                dict(
                    snapshot_id=sid,
                    **{
                        k: p.get(k)
                        for k in (
                            "asof_cn",
                            "imported_at",
                            "source",
                            "source_kind",
                            "counts",
                            "demo",
                        )
                    },
                )
                for sid, payload in db.execute(
                    "SELECT snapshot_id,payload FROM snapshot ORDER BY seq DESC LIMIT 5"
                )
                for p in [json.loads(payload)]
            ]

    def existing(self, source_hash):
        with sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True) as db:
            row = db.execute(
                "SELECT payload FROM snapshot WHERE source_hash=? ORDER BY seq DESC LIMIT 1",
                (source_hash,),
            ).fetchone()
            return json.loads(row[0]) if row else None

    def reject(self, report):
        with self.write() as db:
            self.set_meta(db, "last_error", report)
            self.set_meta(db, "last_attempt", now_cn())
            self.set_meta(db, "revision", self.meta(db, "revision", 0) + 1)

    def save(self, snapshot):
        with self.write() as db:
            current_id = self.meta(db, "active")
            row = db.execute(
                "SELECT payload FROM snapshot WHERE snapshot_id=?", (current_id,)
            ).fetchone()
            current = json.loads(row[0]) if row else None
            duplicate = db.execute(
                "SELECT snapshot_id FROM snapshot WHERE source_hash=?",
                (snapshot["source_hash"],),
            ).fetchone()
            if duplicate:
                if duplicate[0] != current_id:
                    raise InvalidSnapshot(
                        "RETAINED_SNAPSHOT_NOT_ACTIVE: use explicit rollback"
                    )
                self.set_meta(db, "last_error", None)
                self.set_meta(db, "last_attempt", now_cn())
                self.set_meta(db, "revision", self.meta(db, "revision", 0) + 1)
                return dict(
                    result="UNCHANGED", snapshot_id=current_id, **snapshot["coverage"]
                )
            if current:
                if snapshot["asof_cn"] < current["asof_cn"]:
                    raise InvalidSnapshot("OLDER_SNAPSHOT: use explicit rollback")
                if any(
                    snapshot["counts"][field] < current["counts"][field] / 2
                    for field in ("themes", "nodes", "memberships")
                ):
                    raise InvalidSnapshot(
                        "STRUCTURE_DROP: snapshot counts fell abruptly"
                    )
                old_themes = {t["theme_id"]: t for t in current["themes"]}
                for theme in snapshot["themes"]:
                    old = old_themes.get(theme["theme_id"])
                    if not old:
                        continue
                    new_depth = max((n["depth"] for n in theme["nodes"]), default=0)
                    old_depth = max((n["depth"] for n in old["nodes"]), default=0)
                    if new_depth < old_depth or any(
                        len(theme[field]) < len(old[field]) / 2
                        for field in ("nodes", "memberships")
                    ):
                        raise InvalidSnapshot(
                            "STRUCTURE_DROP: hierarchy or membership fell abruptly"
                        )
            db.execute(
                "INSERT INTO snapshot(snapshot_id,source_hash,payload) VALUES(?,?,?)",
                (snapshot["snapshot_id"], snapshot["source_hash"], encoded(snapshot)),
            )
            self.set_meta(db, "active", snapshot["snapshot_id"])
            self.set_meta(db, "last_error", None)
            self.set_meta(db, "last_attempt", now_cn())
            self.set_meta(db, "revision", self.meta(db, "revision", 0) + 1)
            db.execute(
                "DELETE FROM snapshot WHERE seq NOT IN (SELECT seq FROM snapshot ORDER BY seq DESC LIMIT 5)"
            )
        return dict(
            result="IMPORTED",
            snapshot_id=snapshot["snapshot_id"],
            **snapshot["coverage"],
        )

    def rollback(self, snapshot_id):
        with self.write() as db:
            if not db.execute(
                "SELECT 1 FROM snapshot WHERE snapshot_id=?", (snapshot_id,)
            ).fetchone():
                raise InvalidSnapshot("SNAPSHOT_NOT_RETAINED")
            self.set_meta(db, "active", snapshot_id)
            self.set_meta(db, "last_error", None)
            self.set_meta(db, "revision", self.meta(db, "revision", 0) + 1)
        return dict(result="ROLLED_BACK", snapshot_id=snapshot_id)
