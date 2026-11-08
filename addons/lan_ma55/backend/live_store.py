"""One SQLite transaction owns receipt journal, real 15m inputs and decisions."""

from collections import Counter, defaultdict
from copy import deepcopy
import json
from time import perf_counter

from .engine import RULE_VERSION
from .market import adjustment, aggregate_five, next_missing, points, recent_start
from .pipeline import empty_record, invalidate, process_symbol
from .store import StateWriter, encoded

LIVE_DDL = """
CREATE TABLE IF NOT EXISTS runtime_meta(key TEXT PRIMARY KEY,payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS security_master(symbol TEXT PRIMARY KEY,payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS collection_journal(symbol TEXT PRIMARY KEY,payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS recent_bar(symbol TEXT,bar_end TEXT,payload TEXT NOT NULL,
 PRIMARY KEY(symbol,bar_end));
CREATE TABLE IF NOT EXISTS day_state(symbol TEXT,trade_date TEXT,payload TEXT NOT NULL,
 PRIMARY KEY(trade_date,symbol));
CREATE TABLE IF NOT EXISTS cycle_member(cycle_id TEXT,symbol TEXT,category TEXT NOT NULL,
 PRIMARY KEY(cycle_id,symbol));
CREATE INDEX IF NOT EXISTS member_symbol ON cycle_member(symbol,cycle_id);
CREATE TABLE IF NOT EXISTS collection_batch(
 id INTEGER PRIMARY KEY,created_at TEXT NOT NULL,range_start TEXT NOT NULL,range_end TEXT NOT NULL,
 symbols INTEGER NOT NULL,received_symbols INTEGER NOT NULL,received_5m INTEGER NOT NULL,
 http_requests INTEGER,minute_requests INTEGER,fetch_ms REAL NOT NULL,apply_ms REAL NOT NULL
);
"""


def category(record, end):
    s = record["state"]
    if s["data_asof"] == end and s["valid_run"] >= 56:
        return "valid"
    if s["reason"] in ("WARMUP", "INSUFFICIENT_WARMUP"):
        return "insufficient"
    return "gap"


def phase(record, kind):
    s = record["state"]
    return ("DATA_PENDING" if kind != "valid" else "CONFIRMED" if (s.get("structure") or {}).get("locked")
            else "WATCHING" if (s.get("decision") or {}).get("impulse_recent") else "NO_SETUP")


class LiveStore(StateWriter):
    def __init__(self, path):
        super().__init__(path)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(LIVE_DDL)
        with self.conn:
            self.set_meta("data_mode", "QUANTDASH_PRO_RAW_5M_TO_QFQ_15M_V1")

    def meta(self, key, default=None):
        row = self.conn.execute("SELECT payload FROM runtime_meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_meta(self, key, value):
        self.conn.execute("INSERT INTO runtime_meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET payload=excluded.payload",
                          (key, encoded(value)))

    def master(self):
        return {row["symbol"]: json.loads(row["payload"]) for row in self.conn.execute("SELECT * FROM security_master")}

    def save_master(self, rows, overview):
        previous = self.master()
        members = {r["symbol"] for r in rows if r["eligible"]}
        old = {s for s, r in previous.items() if r["eligible"]}
        generation = self.meta("generation", 0) + int(members != old)
        present = {r["symbol"] for r in rows}
        rows = [*rows, *(dict(r, eligible=False) for s, r in previous.items() if s not in present)]
        with self.conn:
            self.conn.executemany("INSERT INTO security_master VALUES(?,?) ON CONFLICT(symbol) DO UPDATE SET payload=excluded.payload",
                                  [(r["symbol"], encoded(r)) for r in rows])
            self.set_meta("generation", generation)
            self.set_meta("universe", overview)
            if old and members != old:
                # A new listing changes the denominator, not already received K.
                # Seed the new membership from actual old receipts at the latest
                # completed point; only new/uncovered members need collection.
                sources = self.conn.execute("SELECT * FROM scan_cycle WHERE cycle_id LIKE ? ORDER BY bar_end", (f"PRO:{RULE_VERSION}:{generation - 1}:%",)).fetchall()
                for source in sources:
                    latest = source["bar_end"]
                    prior = self.conn.execute("SELECT symbol,category FROM cycle_member WHERE cycle_id=?", (source["cycle_id"],)).fetchall()
                    carried = [(s, k) for s, k in prior if s in members]
                    counts = Counter(k for _, k in carried)
                    cycle = self.new_cycle(latest, len(members), source["updated_at"], source["mode"])
                    cycle.update(processed_symbols=len(carried), source_asof=source["source_asof"], elapsed_ms=source["elapsed_ms"],
                                 new_signals=sum(r[0] in members for r in self.conn.execute("SELECT symbol FROM signal_event WHERE confirmed_at=?", (latest,))))
                    for kind in ("valid", "insufficient", "gap", "failed"):
                        cycle[kind + "_symbols"] = counts[kind]
                    self.conn.executemany("INSERT OR REPLACE INTO cycle_member VALUES(?,?,?)", [(cycle["cycle_id"], s, k) for s, k in carried])
                    self.save([], {}, cycle, commit=False)
                    for symbol in members:
                        self.conn.execute("UPDATE day_state SET payload=json_set(payload,'$.generation',?) WHERE symbol=? AND trade_date=?", (generation, symbol, latest[:10]))
        return generation

    def journal(self, symbol):
        row = self.conn.execute("SELECT payload FROM collection_journal WHERE symbol=?", (symbol,)).fetchone()
        return json.loads(row[0]) if row else dict(through=None, received=None, tail={}, gap=None,
                                                 retry_at="1970-01-01", attempts=0, last_live_target=None,
                                                 factors=None, factors_day=None)

    def bars(self, symbol):
        return {r["bar_end"]: json.loads(r["payload"]) for r in self.conn.execute(
            "SELECT * FROM recent_bar WHERE symbol=? ORDER BY bar_end", (symbol,))}

    def identity(self, end):
        return f"PRO:{RULE_VERSION}:{self.meta('generation', 1)}:{end}"

    def new_cycle(self, end, expected, now, mode):
        return dict(cycle_id=self.identity(end), trade_date=end[:10], bar_end=end, mode=mode,
                    expected_symbols=expected, processed_symbols=0, valid_symbols=0,
                    insufficient_symbols=0, gap_symbols=0, failed_symbols=0, new_signals=0,
                    source_asof=None, elapsed_ms=0.0, status="RUNNING", source_health="REPLAY" if mode == "REPLAY" else "UPDATING",
                    updated_at=now, completed_at=None, error=None)

    @staticmethod
    def day_row(info, record, end, kind, generation):
        state = record["state"]
        events = [e for e in record["confirmations"] if e["confirmed_at"][:10] == end[:10]]
        return dict(symbol=info["symbol"], name=info["name"], market_date=end[:10],
                    generation=generation, phase=phase(record, kind), category=kind,
                    reason=state["reason"], bar_end=state["data_asof"] or "",
                    processed_at=record.get("updated_at", ""), events_json=encoded(events),
                    decision_json=encoded(state.get("decision") or {}))

    def accept(self, symbols, response, factors, *, start, end, target15, sessions, master, now, live_after, elapsed_ms,
               http_requests=None, minute_requests=None):
        """Commit one actual <=100-stock response, including omitted/empty members.

        Only complete real groups enter process_symbol. A hole invalidates the
        run without inventing a bar or moving a nonexistent input watermark.
        """
        began = perf_counter()
        expected = sum(r["eligible"] for r in master.values())
        generation = self.meta("generation", 1)
        saved, _ = self.records(symbols)
        records, kinds, journals, daily, members = [], {}, {}, [], []
        for symbol in symbols:
            info, journal = master[symbol], self.journal(symbol)
            previous = saved.get(symbol) or empty_record(symbol)
            retained = self.bars(symbol)
            base = max(recent_start(sessions, target15), (info["listing_date"] or sessions[0]) + " 09:35:00")
            received = response.get(symbol, {})
            pending = {t: r for t, r in journal["tail"].items() if t >= base}
            pending.update(received)
            horizon = max(end, journal["received"] or "")
            grouped, tail, _ = aggregate_five(pending, sessions, min(start, base), horizon)
            new = {b["timestamp"]: b for b in grouped if b["timestamp"] >= base}
            repaired = any(t <= (previous["state"]["processed_at"] or "") and t not in retained for t in new)
            retained.update(new)
            retained = {t: b for t, b in retained.items() if base <= t <= target15}
            events = factors.get(symbol, journal["factors"])
            factor_changed = (previous["state"]["processed_at"] and events is not None and
                              adjustment(events, previous["state"]["processed_at"][:10]) != previous["state"]["last_factor"])
            replay = repaired or factor_changed
            record = empty_record(symbol) if replay else deepcopy(previous)
            if replay:
                record["state"]["sequence"] = max(0, previous["state"]["sequence"] - len(retained))
            origin = "REPLAY" if target15[:10] < now[:10] else "LIVE"
            beginnings = base if replay or not record["state"]["processed_at"] else record["state"]["processed_at"]
            ends = points(sessions, beginnings, target15, step=15)
            for at in ends:
                if not replay and previous["state"]["processed_at"] and at <= previous["state"]["processed_at"]:
                    continue
                raw = retained.get(at)
                if raw is not None:
                    ratio = adjustment(events, at[:10])
                    bar = dict(raw, adjust_factor=ratio)
                    if ratio:
                        bar.update({k: raw[k] * ratio for k in ("open", "high", "low", "close")})
                    record, _ = process_symbol(symbol, [bar], sessions=sessions, raw_tick=info["raw_tick"],
                                                   source_asof=at, first_seen_at=now, previous=record,
                                                   origin=origin, live_after=live_after)
                else:
                    invalidate(record["state"], "EMPTY_OR_OMITTED_5M" if not received and not retained else "MISSING_5M")
                kind = category(record, at)
                members.append((self.identity(at), symbol, kind, at))
                record["updated_at"] = now
                if at.endswith("15:00:00") or at == target15:
                    daily.append(self.day_row(info, record, at, kind, generation))
            if replay:
                old_events = {e["event_id"]: e for e in previous["confirmations"]}
                old_risks = deepcopy(previous["risks"])
                for event in record["confirmations"]:
                    old_events.setdefault(event["event_id"], event)
                old_risks.update(record["risks"])
                if factor_changed:
                    for identity, risk in old_risks.items():
                        if identity in previous["risks"]:
                            risk.update(reason="PRICE_BASIS_CHANGED", data_pending_at=now)
                record["confirmations"] = sorted(old_events.values(), key=lambda e: e["confirmed_at"])
                record["risks"] = old_risks
            record["updated_at"] = now
            through, hole = next_missing(base, horizon, sessions, retained, tail)
            journal.update(through=through, received=max([journal["received"] or "", *received]) or None,
                           tail=tail, gap=list(hole) if hole else None,
                           factors=events, factors_day=now[:10] if symbol in factors else journal["factors_day"],
                           attempts=journal["attempts"] + 1 if hole else 0)
            # Only a missing prefix backs off. New live targets retain priority.
            from datetime import timedelta
            from .pipeline import wall
            journal["retry_at"] = str(wall(now) + timedelta(minutes=min(60, 5 * 2 ** min(max(0, journal["attempts"] - 1), 4)))) if hole else now
            if end[:10] == now[:10]:
                journal["last_live_target"] = end
            records.append(record)
            kinds[symbol] = category(record, target15)
            journals[symbol] = (journal, retained)
        latest = self.cycle(self.identity(target15)) or self.new_cycle(target15, expected, now, origin)
        with self.conn:
            # Nested save is explicitly noncommitting: receipts, inputs, events,
            # per-stock watermarks and all cycle memberships commit together.
            self.save(records, kinds, latest, commit=False)
            for symbol, (journal, retained) in journals.items():
                self.conn.execute("INSERT INTO collection_journal VALUES(?,?) ON CONFLICT(symbol) DO UPDATE SET payload=excluded.payload",
                                  (symbol, encoded(journal)))
                self.conn.executemany("INSERT INTO recent_bar VALUES(?,?,?) ON CONFLICT(symbol,bar_end) DO UPDATE SET payload=excluded.payload",
                                      [(symbol, t, encoded(b)) for t, b in retained.items()])
            for row in daily:
                # The final merge below preserves original immutable event payloads.
                row["events_json"] = encoded([json.loads(r[0]) for r in self.conn.execute(
                    "SELECT payload FROM signal_event WHERE symbol=? AND substr(confirmed_at,1,10)=? ORDER BY confirmed_at",
                    (row["symbol"], row["market_date"]))])
                self.conn.execute("INSERT INTO day_state VALUES(?,?,?) ON CONFLICT(trade_date,symbol) DO UPDATE SET payload=excluded.payload",
                                  (row["symbol"], row["market_date"], encoded(row)))
            changed = defaultdict(Counter)
            for identity, symbol, kind, at in members:
                old = self.conn.execute("SELECT category FROM cycle_member WHERE cycle_id=? AND symbol=?", (identity, symbol)).fetchone()
                if old:
                    changed[at][old[0]] -= 1
                else:
                    changed[at]["processed"] += 1
                changed[at][kind] += 1
                self.conn.execute("INSERT INTO cycle_member VALUES(?,?,?) ON CONFLICT(cycle_id,symbol) DO UPDATE SET category=excluded.category",
                                  (identity, symbol, kind))
            for at, delta in changed.items():
                cycle = self.cycle(self.identity(at)) or self.new_cycle(at, expected, now, origin)
                for key, value in delta.items():
                    cycle[key + "_symbols"] += value
                cycle.update(updated_at=now, source_asof=max((r["state"]["data_asof"] or "" for r in records), default="") or None,
                             new_signals=self.conn.execute("SELECT COUNT(*) FROM signal_event WHERE confirmed_at=?", (at,)).fetchone()[0])
                if at == target15:
                    cycle["elapsed_ms"] += elapsed_ms
                cycle["status"] = "COMPLETE" if cycle["processed_symbols"] == expected else "RUNNING"
                cycle["completed_at"] = now if cycle["status"] == "COMPLETE" else None
                self.save([], {}, cycle, commit=False)
            self.set_meta("revision", self.meta("revision", 0) + 1)
            self.conn.execute("INSERT INTO collection_batch VALUES(NULL,?,?,?,?,?,?,?,?,?,?)",
                              (now, start, end, len(symbols), len(response), sum(len(v) for v in response.values()),
                               http_requests, minute_requests, elapsed_ms, (perf_counter() - began) * 1000))
        return symbols

    def day_rows(self, day, symbols=None):
        rows = [json.loads(r[0]) for r in self.conn.execute("SELECT payload FROM day_state WHERE trade_date=? ORDER BY symbol", (day,))]
        return rows if symbols is None else [r for r in rows if r["symbol"] in symbols]

    def defer_batch(self, symbols, *, start, end, target5, target15, master, now, status):
        """A failed real block is journaled; other blocks stay schedulable."""
        from datetime import timedelta
        from .pipeline import wall
        records, _ = self.records(symbols)
        cycle = self.cycle(self.identity(target15)) or self.new_cycle(
            target15, sum(r["eligible"] for r in master.values()), now, "REPLAY" if target15[:10] < now[:10] else "LIVE")
        changed = []
        with self.conn:
            for symbol in symbols:
                journal = self.journal(symbol)
                attempts = journal.get("network_failures", 0) + 1
                journal.update(network_failures=attempts,
                               retry_at=str(wall(now) + timedelta(seconds=min(60, 5 * 2 ** min(attempts - 1, 4)))),
                               gap=journal.get("gap") or [start, end], last_live_target=target5,
                               request_error=f"HTTP_{status or 'TRANSPORT'}_RETRY")
                self.conn.execute("INSERT INTO collection_journal VALUES(?,?) ON CONFLICT(symbol) DO UPDATE SET payload=excluded.payload",
                                  (symbol, encoded(journal)))
                record = records.get(symbol) or empty_record(symbol)
                record["updated_at"] = now
                record["state"]["reason"] = journal["request_error"]
                changed.append(record)
                self.conn.execute("INSERT INTO cycle_member VALUES(?,?,?) ON CONFLICT(cycle_id,symbol) DO UPDATE SET category=excluded.category",
                                  (cycle["cycle_id"], symbol, "failed"))
                row = self.day_row(master[symbol], record, target15, "failed", self.meta("generation", 1))
                self.conn.execute("INSERT INTO day_state VALUES(?,?,?) ON CONFLICT(trade_date,symbol) DO UPDATE SET payload=excluded.payload",
                                  (symbol, target15[:10], encoded(row)))
            counts = dict(self.conn.execute("SELECT category,COUNT(*) FROM cycle_member WHERE cycle_id=? GROUP BY category", (cycle["cycle_id"],)))
            cycle.update(processed_symbols=sum(counts.values()), updated_at=now, error=f"HTTP_{status or 'TRANSPORT'}_RETRY")
            for kind in ("valid", "insufficient", "gap", "failed"):
                cycle[kind + "_symbols"] = counts.get(kind, 0)
            cycle["status"] = "COMPLETE" if cycle["processed_symbols"] == cycle["expected_symbols"] else "RUNNING"
            self.save(changed, {s: "failed" for s in symbols}, cycle, commit=False)

    def summary(self, end):
        cycle = self.cycle(self.identity(end)) if end else None
        dates = [r[0] for r in self.conn.execute("SELECT DISTINCT trade_date FROM day_state ORDER BY trade_date")]
        actual = self.conn.execute("SELECT MAX(bar_end) FROM recent_bar").fetchone()[0]
        analyzed = self.conn.execute("SELECT MAX(data_asof) FROM symbol_state").fetchone()[0]
        return dict(cycle=cycle, market_date=actual[:10] if actual else None, data_asof=actual, analysis_asof=analyzed,
                    available_dates=dates, universe=self.meta("universe", {}), generation=self.meta("generation", 1),
                    revision=self.meta("revision", 0), signals=self.conn.execute("SELECT COUNT(*) FROM signal_event").fetchone()[0],
                    symbol_states=self.conn.execute("SELECT COUNT(*) FROM symbol_state").fetchone()[0])

    def retain(self, sessions, retention=60):
        if len(sessions) < retention:
            return
        cutoff, input_cutoff = sessions[-retention], sessions[-10]
        with self.conn:
            self.conn.execute("DELETE FROM recent_bar WHERE substr(bar_end,1,10)<?", (input_cutoff,))
            self.conn.execute("DELETE FROM day_state WHERE trade_date<?", (cutoff,))
            self.conn.execute("DELETE FROM collection_batch WHERE substr(range_end,1,10)<?", (cutoff,))
            self.conn.execute("DELETE FROM cycle_member WHERE cycle_id IN (SELECT cycle_id FROM scan_cycle WHERE trade_date<?)", (input_cutoff,))
            self.prune(cutoff)
