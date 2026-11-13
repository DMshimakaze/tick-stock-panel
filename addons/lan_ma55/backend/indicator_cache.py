"""Bounded chart snapshots and resumable SMA sums, in the owner's SQLite.

Raw15 remains authoritative. Cache updates share the raw write transaction;
HTTP readers only select snapshots. No strategy state or supplier access here.
"""

from collections import Counter, deque
import json
from math import fsum

from .market_grid import CAPACITY, FIELDS, MAS, adjustment, derived, points

VERSION = 1
BAR_KEYS = ("timestamp", *FIELDS, "source_id", "source_count", "adjust_factor", *(f"ma{n}" for n in MAS))
DDL = """
CREATE TABLE IF NOT EXISTS chart_cache(
 symbol TEXT,timeframe TEXT,version INTEGER,source_id TEXT,basis_revision INTEGER,
 factor_revision INTEGER,calendar_revision INTEGER,metadata TEXT,basis_day TEXT,
 raw_first TEXT,raw_last TEXT,raw_count INTEGER,history_gaps INTEGER,
 frame_count INTEGER,bars TEXT,rolling TEXT,PRIMARY KEY(symbol,timeframe));
"""


def dumps(value):
    return json.dumps(value, separators=(",", ":"), allow_nan=False)


def metadata(info):
    return dumps([info.get(k) for k in ("source_type", "raw_tick", "listing_date", "factor_coverage")])


def matches(row, info, calendar_revision):
    return bool(row and row["version"] == VERSION
                and row["source_id"] == info["source_id"]
                and row["basis_revision"] == info["basis_revision"]
                and row["factor_revision"] == info["factor_revision"]
                and row["calendar_revision"] == calendar_revision
                and row["metadata"] == metadata(info))


def unpack(values):
    return [dict(zip(BAR_KEYS, row), source=row[7], complete=True,
                 price_basis="QFQ" if row[9] is not None else "RAW",
                 volume_unit="shares", valid=row[9] is not None) for row in json.loads(values)]


def frame_count(raw15, tf):
    if tf == "15m":
        return len(raw15)
    width = int(tf[:-1])
    groups = Counter()
    for row in raw15:
        at = row["timestamp"]
        minute = int(at[11:13]) * 60 + int(at[14:16])
        base = 570 if minute <= 690 else 780
        end = base + (minute - base + width - 1) // width * width
        groups[(at[:10], end, row["source_id"])] += 1
    return sum(size == width // 15 for size in groups.values())


def append(raw, events, asof, sessions, tf, rolling, saved, *, exact=False):
    queue = deque(rolling.get("closes", []), maxlen=max(MAS))
    sums = {n: rolling.get("sums", {}).get(str(n), 0.0) for n in MAS}
    previous = rolling.get("last")
    previous_source = rolling.get("source_id")
    steps = rolling.get("steps", 0)
    for row in raw:
        at = row["timestamp"]
        factor = adjustment(events, at[:10], asof)
        continuous = previous is None or (len(points(sessions, previous, at, step=int(tf[:-1]))) == 2
                                          and previous_source == row["source_id"])
        if factor is None or not continuous:
            queue.clear()
            sums = {n: 0.0 for n in MAS}
            steps = 0
        bar = dict(row, adjust_factor=factor)
        if factor is not None:
            bar.update({k: row[k] * factor for k in FIELDS[:4]})
            close = bar["close"]
            for n in MAS:
                sums[n] = fsum((sums[n], close, -queue[-n] if len(queue) >= n else 0.0))
            queue.append(close)
            steps += 1
            # Rebase every largest window; bounded error across long runtimes.
            if exact or steps % max(MAS) == 0:
                values = list(queue)
                sums = {n: fsum(values[-n:]) for n in MAS}
        for n in MAS:
            bar[f"ma{n}"] = sums[n] / n if len(queue) >= n else None
        saved.append([bar.get(k) for k in BAR_KEYS])
        previous = at
        previous_source = row["source_id"]
    return dict(closes=list(queue), sums=sums, last=previous, source_id=previous_source, steps=steps)


def update(conn, symbol, info, raw15, events, sessions, calendar_revision, *, rebuild=False, append_only=False):
    """Append new complete buckets, or rebuild bounded history after a real revision."""
    if not raw15:
        conn.execute("DELETE FROM chart_cache WHERE symbol=?", (symbol,))
        return dict(appended=0, rebuilt=0)
    asof = raw15[-1]["timestamp"]
    gaps = len(points(sessions, raw15[0]["timestamp"], asof, step=15)) - len(raw15)
    result = dict(appended=0, rebuilt=0)
    for tf, capacity in CAPACITY.items():
        prior = conn.execute("SELECT * FROM chart_cache WHERE symbol=? AND timeframe=?", (symbol, tf)).fetchone()
        compatible = not rebuild and matches(prior, info, calendar_revision)
        # A new event entering the projection day changes all prior QFQ values.
        if compatible and adjustment(events, prior["basis_day"], asof) not in (None, 1):
            compatible = False
        if append_only and not compatible:
            # Live BUY processing must not wait for an MA233 cold rebuild.
            # Delete in the raw transaction; the idle writer rebuilds saved facts.
            conn.execute("DELETE FROM chart_cache WHERE symbol=? AND timeframe=?", (symbol, tf))
            continue
        if compatible:
            rolling, saved = json.loads(prior["rolling"]), json.loads(prior["bars"])
            last = rolling.get("last")
            incoming = derived([b for b in raw15 if last is None or b["timestamp"] > last], tf, sessions, asof)
        else:
            rolling, saved = {}, []
            incoming = derived(raw15, tf, sessions, asof)
            result["rebuilt"] += 1
        rolling = append(incoming, events, asof, sessions, tf, rolling, saved, exact=not compatible)
        result["appended"] += len(incoming)
        available = frame_count(raw15, tf)
        conn.execute("INSERT OR REPLACE INTO chart_cache VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            symbol, tf, VERSION, info["source_id"], info["basis_revision"], info["factor_revision"],
            calendar_revision, metadata(info), asof[:10], raw15[0]["timestamp"], asof, len(raw15), gaps,
            available, dumps(saved[-capacity:]), dumps(rolling),
        ))
    return result


def read(conn, symbol, tf, asof, limit, info, calendar_revision, events):
    row = conn.execute("SELECT * FROM chart_cache WHERE symbol=? AND timeframe=?", (symbol, tf)).fetchone()
    if not matches(row, info, calendar_revision):
        return None
    bars = [b for b in unpack(row["bars"]) if b["timestamp"] <= asof]
    # A requested earlier full window can extend beyond this bounded snapshot.
    # Let the caller reconstruct that historical window from bounded raw facts.
    if row["raw_last"] > asof and len(bars) < min(limit, row["frame_count"]):
        return None
    scale = (adjustment(events, row["basis_day"], asof)
             if asof[:10] >= row["basis_day"] else
             1 / adjustment(events, asof[:10], row["basis_day"]) if events is not None else None)
    if scale not in (None, 1):
        for bar in bars:
            for k in (*FIELDS[:4], "adjust_factor", *(f"ma{n}" for n in MAS)):
                if bar.get(k) is not None:
                    bar[k] *= scale
    return dict(bars=bars[-limit:], available=row["frame_count"],
                raw15_count=row["raw_count"], history_gaps=row["history_gaps"],
                cache=dict(hit=True, version=VERSION, source_id=row["source_id"],
                           basis_revision=row["basis_revision"], factor_revision=row["factor_revision"]))
