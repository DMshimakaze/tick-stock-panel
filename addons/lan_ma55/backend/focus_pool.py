"""MA55 focus scope: verified THS snapshots, atomic selection and union deltas.

No network, market requests, algorithm calls or deletion of cached bars/events.
The caller owns scheduling and exposes refresh failures separately.
"""

import json
import re
import sqlite3
from contextlib import closing

KINDS = ("industry", "cn_concept")
KEY = re.compile(r"(?:industry|cn_concept):\d{6}\.TI")
SYMBOL = re.compile(r"\d{6}\.(?:SH|SZ|BJ)")


def _canonical(value, pattern):
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ValueError("Invalid canonical identity")
    return value


def _items(payload):
    if not isinstance(payload, dict) or type(payload.get("code")) is not int or payload["code"] != 0:
        raise ValueError("THS request not successful")
    data = payload.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("item"), list):
        raise ValueError("THS response missing item list")
    if type(data.get("timestamp")) is not int or data["timestamp"] <= 0:
        raise ValueError("THS response missing source timestamp")
    return data["timestamp"], data["item"]


def decode_catalog(kind, payload):
    """Preserve catalog identity; never auto-resolve aliases or fine themes."""
    if kind not in KINDS:
        raise ValueError("Unsupported THS catalog kind")
    source_ms, items = _items(payload)
    entries = {}
    for row in items:
        if not isinstance(row, dict):
            raise ValueError("Invalid catalog row")
        key = _canonical(f'{kind}:{row.get("thscode")}', KEY)
        name = row.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Missing catalog name")
        name = name.strip()
        if key in entries and entries[key] != name:
            raise ValueError("Conflicting catalog identity")
        entries[key] = name
    if not entries:
        raise ValueError("Empty catalog is not authoritative")
    return {"source_ms": source_ms, "entries": entries}


def _snapshot(value):
    key = _canonical(value["key"], KEY)
    name = value["name"]
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Missing sector name")
    source, observed = value["source_ms"], value["observed_ms"]
    proof, run = value.get("proof"), value.get("run")
    if (type(observed) is not int or observed <= 0 or
            (source is None and proof != "BULK_REVERSE_CHECKED") or
            (source is not None and (type(source) is not int or source <= 0))):
        raise ValueError("Missing snapshot time")
    symbols = value["symbols"]
    if not isinstance(symbols, (tuple, list)) or (not symbols and proof != "DIRECT_EMPTY"):
        raise ValueError("Empty/unverified membership cannot replace snapshot")
    snapshot = dict(key=key, name=name.strip(), source_ms=source, observed_ms=observed,
                    symbols=sorted({_canonical(s, SYMBOL) for s in symbols}))
    if proof:
        snapshot["proof"] = proof
    if run:
        snapshot["run"] = run
    return snapshot


def decode_members(key, name, payload, observed_ms, *, verified=False):
    """Decode a successful complete single-index constituent response.

    The caller binds the response to its requested catalog ID and validates
    entitlement/completeness before decoding; no guessing bare tickers.
    """
    source_ms, items = _items(payload)
    if any(not isinstance(row, dict) for row in items):
        raise ValueError("Invalid constituent row")
    symbols = [_canonical(row.get("thscode"), SYMBOL) for row in items]
    snapshot = dict(key=key, name=name, source_ms=source_ms,
                    observed_ms=observed_ms, symbols=symbols)
    if verified:
        snapshot["proof"] = "DIRECT" if symbols else "DIRECT_EMPTY"
    return _snapshot(snapshot)


def _union(sectors):
    members = {}
    for sector in sectors:
        for symbol in sector["symbols"]:
            members.setdefault(symbol, []).append(sector["key"])
    return {symbol: sorted(keys) for symbol, keys in sorted(members.items())}


class ScopeConflict(RuntimeError):
    """Caller must reread the current revision before applying edits."""


class FocusPool:
    """One small additive table in the plugin's existing SQLite database."""

    def __init__(self, path):
        self.path = path
        with closing(sqlite3.connect(path)) as conn, conn:
            conn.execute("CREATE TABLE IF NOT EXISTS focus_scope_v1 ("
                         "id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL, payload TEXT NOT NULL)")
            conn.execute("INSERT OR IGNORE INTO focus_scope_v1 VALUES (1, 0, '[]')")
            conn.execute("CREATE TABLE IF NOT EXISTS focus_scope_history_v1 ("
                         "revision INTEGER PRIMARY KEY, payload TEXT NOT NULL)")
            conn.execute("CREATE TABLE IF NOT EXISTS focus_preferences_v1 ("
                         "id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)")

    @staticmethod
    def _read(conn):
        revision, payload = conn.execute("SELECT revision,payload FROM focus_scope_v1 WHERE id=1").fetchone()
        sectors = json.loads(payload)
        row = conn.execute("SELECT payload FROM focus_preferences_v1 WHERE id=1").fetchone()
        preferences = json.loads(row[0]) if row else dict(groups=[], aliases={})
        return dict(revision=revision, sectors=sectors, members=_union(sectors), **preferences)

    def read(self):
        with closing(sqlite3.connect(self.path)) as conn:
            return self._read(conn)

    def apply(self, selection, snapshots, *, expected_revision, preferences=None):
        """Apply an explicit ordered selection and verified fresh snapshots.

        Omit failed refreshes for retained IDs; never fabricate an empty board.
        Every newly selected ID requires a verified snapshot. Empty selection
        explicitly pauses collection. Late refreshes use revision comparison.
        """
        if not isinstance(selection, (list, tuple)):
            raise ValueError("Selection must be explicit")
        keys = list(dict.fromkeys(_canonical(k, KEY) for k in selection))
        fresh = {}
        for raw in snapshots:
            snap = _snapshot(raw)
            if snap["key"] in fresh or snap["key"] not in keys:
                raise ValueError("Unexpected or duplicate snapshot")
            fresh[snap["key"]] = snap
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            old = self._read(conn)
            if type(expected_revision) is not int or old["revision"] != expected_revision:
                raise ScopeConflict("Focus selection changed; reread before applying")
            previous = {s["key"]: s for s in old["sectors"]}
            sectors = []
            for key in keys:
                before, after = previous.get(key), fresh.get(key)
                if after is None and before is None:
                    raise ValueError("New sector requires verified membership")
                if before and after and (after["observed_ms"] < before["observed_ms"] or
                    (before["source_ms"] is not None and after["source_ms"] is not None and
                     after["source_ms"] < before["source_ms"])):
                    raise ValueError("Older snapshot cannot replace newer membership")
                sectors.append(after if after is not None else before)
            members = _union(sectors)
            saved = {key: old[key] for key in ("groups", "aliases")}
            changed = preferences is not None and preferences != saved
            revision = old["revision"] + int(old["revision"] == 0 or sectors != old["sectors"] or changed)
            payload = json.dumps(sectors, ensure_ascii=False)
            if revision != old["revision"]:
                conn.execute("UPDATE focus_scope_v1 SET revision=?,payload=? WHERE id=1",
                             (revision, payload))
            if changed:
                conn.execute("INSERT INTO focus_preferences_v1 VALUES (1,?) ON CONFLICT(id) "
                             "DO UPDATE SET payload=excluded.payload", (json.dumps(preferences, ensure_ascii=False),))
            before, after = set(old["members"]), set(members)
            membership_changed = members != old["members"]
            if membership_changed or keys != list(previous) or old["revision"] == 0:
                conn.execute("INSERT INTO focus_scope_history_v1 VALUES (?,?)", (revision, payload))
            return dict(revision=revision, sectors=sectors, members=members,
                        **(preferences if changed else saved),
                        added=sorted(after-before), removed=sorted(before-after),
                        retained=sorted(before & after),
                        membership_changed=membership_changed)
