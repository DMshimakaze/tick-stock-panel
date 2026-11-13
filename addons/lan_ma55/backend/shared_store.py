"""Shared completed 15m -> frozen R1 -> existing immutable SQLite events."""

from copy import deepcopy
from contextlib import nullcontext
import json

from .engine import RULE_VERSION, STATE_REVISION, minimal_features
from .store import StateWriter
from .pipeline import empty_record, process_symbol, invalidate, previous_end, wall
from .store import encoded


def source_receipt(source, target):
    return [
        source.get("source_id", "QUANTDASH_LEGACY"),
        source.get("revision", 0),
        source.get("basis_revision", 0),
        target,
        RULE_VERSION,
        STATE_REVISION,
    ]


def category(record, end):
    s = record["state"]
    if s["data_asof"] == end and s["valid_run"] >= 56:
        return "valid"
    if s["reason"] in ("WARMUP", "INSUFFICIENT_WARMUP"):
        return "insufficient"
    return "gap"


def phase(record, kind):
    s = record["state"]
    return (
        "DATA_PENDING"
        if kind != "valid"
        else "CONFIRMED"
        if (s.get("structure") or {}).get("locked")
        else "WATCHING"
        if (s.get("decision") or {}).get("impulse_recent")
        else "NO_SETUP"
    )


class SharedStore(StateWriter):
    def __init__(self, path):
        super().__init__(path)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript("""CREATE TABLE IF NOT EXISTS runtime_meta(key TEXT PRIMARY KEY,payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS security_master(symbol TEXT PRIMARY KEY,payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS day_state(symbol TEXT,trade_date TEXT,payload TEXT NOT NULL,PRIMARY KEY(trade_date,symbol));
            CREATE TABLE IF NOT EXISTS cycle_member(cycle_id TEXT,symbol TEXT,category TEXT NOT NULL,PRIMARY KEY(cycle_id,symbol));""")
        self.conn.execute("""CREATE TABLE IF NOT EXISTS rule_state_archive(
            symbol TEXT,rule_version TEXT,payload TEXT NOT NULL,PRIMARY KEY(symbol,rule_version))""")
        with self.conn:
            self.set_meta("data_mode", "LAN_MARKETDATA_HTTP_V2_COMPLETED_15M")

    def meta(self, key, default=None):
        row = self.conn.execute(
            "SELECT payload FROM runtime_meta WHERE key=?", (key,)
        ).fetchone()
        return json.loads(row[0]) if row else default

    def set_meta(self, key, value):
        self.conn.execute(
            "INSERT INTO runtime_meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET payload=excluded.payload",
            (key, encoded(value)),
        )

    def master(self):
        return {
            row["symbol"]: json.loads(row["payload"])
            for row in self.conn.execute("SELECT * FROM security_master")
        }

    def identity(self, end):
        return f"PRO:{RULE_VERSION}:{self.meta('generation', 1)}:{end}"

    def new_cycle(self, end, expected, now, mode):
        return dict(
            cycle_id=self.identity(end),
            trade_date=end[:10],
            bar_end=end,
            mode=mode,
            expected_symbols=expected,
            processed_symbols=0,
            valid_symbols=0,
            insufficient_symbols=0,
            gap_symbols=0,
            failed_symbols=0,
            new_signals=0,
            source_asof=None,
            elapsed_ms=0.0,
            status="RUNNING",
            source_health="REPLAY" if mode == "REPLAY" else "UPDATING",
            updated_at=now,
            completed_at=None,
            error=None,
        )

    @staticmethod
    def day_row(info, record, end, kind, generation):
        state = record["state"]
        events = [
            e for e in record["confirmations"] if e["confirmed_at"][:10] == end[:10]
        ]
        return dict(
            symbol=info["symbol"],
            name=info["name"],
            market_date=end[:10],
            generation=generation,
            phase=phase(record, kind),
            category=kind,
            reason=state["reason"],
            bar_end=state["data_asof"] or "",
            processed_at=record.get("updated_at", ""),
            events_json=encoded(events),
            decision_json=encoded(state.get("decision") or {}),
        )

    def day_rows(self, day, symbols=None):
        rows = [
            json.loads(r[0])
            for r in self.conn.execute(
                "SELECT payload FROM day_state WHERE trade_date=? ORDER BY symbol",
                (day,),
            )
        ]
        return rows if symbols is None else [r for r in rows if r["symbol"] in symbols]

    def source_master(self, rows, overview, generation):
        with nullcontext() if self.conn.in_transaction else self.conn:
            current = {r["symbol"] for r in rows}
            for symbol, prior in self.master().items():
                if symbol not in current and prior.get("eligible"):
                    prior["eligible"] = False
                    self.conn.execute("UPDATE security_master SET payload=? WHERE symbol=?", (encoded(prior), symbol))
            for row in rows:
                self.conn.execute(
                    "INSERT INTO security_master VALUES(?,?) ON CONFLICT(symbol) DO UPDATE SET payload=excluded.payload",
                    (row["symbol"], encoded(row)),
                )
            self.set_meta("universe", overview)
            self.set_meta("generation", generation)

    def consume(self, rows, *, sessions, target, now, live_after, commit=True):
        symbols = [r["symbol"] for r in rows]
        previous, _ = self.records(symbols)
        records, kinds, day_rows, members = [], {}, [], []
        expected = self.meta("universe", {}).get("expected", 0)
        source_marks = {
            s: r["state"].get("shared_receipt") for s, r in previous.items()
        }
        for source in rows:
            symbol = source["symbol"]
            prior = previous.get(symbol)
            receipt = source_receipt(source, target)
            if source_marks.get(symbol) == receipt and self.conn.execute(
                "SELECT 1 FROM cycle_member WHERE cycle_id=? AND symbol=?", (self.identity(target), symbol)
            ).fetchone():
                continue
            incompatible = bool(
                prior
                and (
                    prior["rule_version"] != RULE_VERSION
                    or prior["state"].get("state_revision") != STATE_REVISION
                )
            )
            source_id = source.get("source_id", "QUANTDASH_LEGACY")
            source_changed = bool(
                prior
                and prior["state"].get("shared_source_id", "QUANTDASH_LEGACY")
                != source_id
            )
            verified = source.get("transition") == "OVERLAP_VERIFIED"
            basis_changed = bool(
                prior
                and prior["state"].get("source_basis_revision")
                != source.get("basis_revision")
                and not (source_changed and verified)
            )
            bars = source.get("bars", [])
            history_lost = bool(
                prior
                and bars
                and prior["state"].get("processed_at")
                and bars[0]["timestamp"] > prior["state"]["processed_at"]
                and previous_end(wall(bars[0]["timestamp"]), sessions)
                != prior["state"]["processed_at"]
            )
            allowed = source.get("analysis_allowed", True) and source.get(
                "state"
            ) not in (
                "FACTOR_PENDING",
                "REBUILD_PENDING",
                "METADATA_PENDING",
                "SOURCE_DISCREPANCY",
                "SOURCE_UNAVAILABLE",
                "GAP",
            )
            allowed = allowed and source.get("price_basis") == "QFQ"
            restore = bool(
                not prior
                or incompatible
                or basis_changed
                or source.get("correction", False)
                or history_lost
                or (source_changed and not verified)
            )
            record = (
                empty_record(symbol)
                if restore and allowed
                else deepcopy(prior)
                if prior
                else empty_record(symbol)
            )
            if restore and prior and allowed:
                record["confirmations"] = deepcopy(prior["confirmations"])
                record["risks"] = deepcopy(prior["risks"])
            if incompatible:
                self.conn.execute(
                    "INSERT OR IGNORE INTO rule_state_archive VALUES(?,?,?)",
                    (symbol, prior["rule_version"], encoded(prior)),
                )
            old_lock = (prior or {}).get("state", {}).get("structure") or {}
            lock_at = (prior or {}).get("state", {}).get("migration_lock_at") or (
                (old_lock.get("last_buy") or {}).get("at")
                if old_lock.get("locked")
                else None
            )
            # A restored old lock needs the real causal warmup preceding it.
            # Neither missing close/MA nor the previous green body is guessed.
            lock_index = next(
                (i for i, b in enumerate(bars) if b["timestamp"] == lock_at), -1
            )
            check_lock = bool(
                lock_at
                and (
                    incompatible
                    or history_lost
                    or basis_changed
                    or source.get("correction", False)
                    or (source_changed and not verified)
                    or (prior or {}).get("state", {}).get("migration_lock_at")
                )
            )
            lock_reproduced = False
            if check_lock and allowed and lock_index >= 55:
                prefix, _ = process_symbol(
                    symbol,
                    bars[: lock_index + 1],
                    sessions=sessions,
                    raw_tick=source.get("raw_tick"),
                    source_asof=target,
                    first_seen_at=now,
                )
                reconstructed = prefix["state"].get("structure") or {}
                lock_reproduced = bool(
                    reconstructed.get("locked")
                    and (reconstructed.get("last_buy") or {}).get("at") == lock_at
                )
            unrecoverable = check_lock and not lock_reproduced
            reset_at = None
            if unrecoverable:
                # A real full departure or failed support resets a prior lock
                # regardless of its missing internals, using 55 causal closes.
                for i, bar in enumerate(bars[55:], 55):
                    if not bar.get("valid") or bar["timestamp"] <= lock_at:
                        continue
                    feature = minimal_features(bars[max(0, i - 55) : i], bar, [])
                    if bar["close"] < feature["ma55"] * 0.99 or (
                        bar["low"] > feature["known_ma55"] * 1.015
                        and bar["close"] > feature["ma55"] * 1.015
                    ):
                        reset_at = bar["timestamp"]
                        unrecoverable = False
                        break
            if not allowed:
                # Hold causal state/locks until a usable source window arrives.
                # A failed supplier response cannot erase the last verified state.
                record["state"]["reason"] = source.get("state") or "FACTOR_PENDING"
                record["updated_at"] = now
            elif unrecoverable:
                invalidate(record["state"], "VERSION_HISTORY_INSUFFICIENT")
                record["state"]["migration_lock_at"] = lock_at
                record["updated_at"] = now
            else:
                # A source-basis correction replays real saved rows and keeps all
                # original versioned confirmations. Same-time old BUY stays old.
                record, _ = process_symbol(
                    symbol,
                    bars,
                    sessions=sessions,
                    raw_tick=source.get("raw_tick"),
                    source_asof=target,
                    first_seen_at=now,
                    previous=record,
                    origin="REPLAY" if restore else "LIVE",
                    live_after=live_after,
                )
                if reset_at:
                    old_ids = {
                        e["event_id"] for e in (prior or {}).get("confirmations", [])
                    }
                    record["confirmations"] = [
                        e
                        for e in record["confirmations"]
                        if e["event_id"] in old_ids or e["confirmed_at"] >= reset_at
                    ]
                record["state"].pop("migration_lock_at", None)
            if restore and prior:
                record["risks"].update(deepcopy(prior["risks"]))
            old_ids = {e["event_id"] for e in (prior or {}).get("confirmations", [])}
            for event in record["confirmations"]:
                if event["event_id"] not in old_ids:
                    event.update(
                        source_id=source_id,
                        basis_id=source.get("basis_id"),
                        factor_revision=source.get("factor_revision"),
                        basis_revision=source.get("basis_revision"),
                    )
                elif (
                    source_id != event.get("source_id", "QUANTDASH_LEGACY")
                    and not verified
                    and prior
                ):
                    record["risks"][event["event_id"]] = deepcopy(
                        prior["risks"].get(event["event_id"], {})
                    )
            record["state"].update(
                state_revision=STATE_REVISION,
                shared_source_asof=source.get("source_asof"),
                shared_receipt=receipt,
            )
            if allowed:
                record["state"].update(
                    source_basis_revision=source.get("basis_revision"),
                    shared_source_id=source_id,
                )
            kind = category(record, target) if allowed else "gap"
            records.append(record)
            kinds[symbol] = kind
            info = dict(symbol=symbol, name=source.get("name", symbol))
            day_rows.append(
                self.day_row(info, record, target, kind, self.meta("generation", 1))
            )
            members.append((self.identity(target), symbol, kind))
        if not records:
            return []
        cycle = self.cycle(self.identity(target)) or self.new_cycle(
            target, expected, now, "LIVE" if target[:10] == now[:10] else "REPLAY"
        )
        with self.conn if commit else nullcontext():
            self.conn.executemany(
                "INSERT INTO cycle_member VALUES(?,?,?) ON CONFLICT(cycle_id,symbol) DO UPDATE SET category=excluded.category",
                members,
            )
            counts = dict(
                self.conn.execute(
                    "SELECT category,count(*) FROM cycle_member WHERE cycle_id=? GROUP BY category",
                    (cycle["cycle_id"],),
                )
            )
            cycle.update(
                processed_symbols=sum(counts.values()),
                source_asof=target,
                updated_at=now,
                status="COMPLETE" if sum(counts.values()) == expected else "RUNNING",
                source_health="SHARED_COMPLETED_15M",
            )
            for kind in ("valid", "insufficient", "gap", "failed"):
                cycle[kind + "_symbols"] = counts.get(kind, 0)
            if cycle["status"] == "COMPLETE":
                cycle["completed_at"] = now
            self.save(records, kinds, cycle, commit=False)
            for row in day_rows:
                # Archive old day projections before refreshing the current view.
                old = self.conn.execute(
                    "SELECT payload FROM day_state WHERE symbol=? AND trade_date=?",
                    (row["symbol"], target[:10]),
                ).fetchone()
                if old:
                    self.conn.execute(
                        "INSERT OR IGNORE INTO rule_state_archive VALUES(?,?,?)",
                        (row["symbol"], f"DAY:{target[:10]}:V5", old[0]),
                    )
                self.conn.execute(
                    "INSERT INTO day_state VALUES(?,?,?) ON CONFLICT(trade_date,symbol) DO UPDATE SET payload=excluded.payload",
                    (row["symbol"], target[:10], encoded(row)),
                )
            self.set_meta("shared_asof", target)
            self.set_meta("revision", self.meta("revision", 0) + 1)
            self.set_meta(
                "pending_publish",
                dict(day=target[:10], symbols=sorted(
                    set((self.meta("pending_publish") or {}).get("symbols", []))
                    | {r["symbol"] for r in records})),
            )
        return [r["symbol"] for r in records]

    def receipts(self):
        return {
            row["symbol"]: json.loads(row["payload"]).get("shared_receipt")
            for row in self.conn.execute("SELECT symbol,payload FROM symbol_state")
        }

    def summary(self, end):
        actual = self.meta("shared_asof")
        analyzed = self.conn.execute(
            "SELECT max(data_asof) FROM symbol_state"
        ).fetchone()[0]
        actual = actual or analyzed
        return dict(
            cycle=self.cycle(self.identity(end)) if end else None,
            market_date=actual[:10] if actual else None,
            data_asof=actual,
            analysis_asof=analyzed,
            available_dates=[
                r[0]
                for r in self.conn.execute(
                    "SELECT trade_date FROM day_state UNION SELECT substr(confirmed_at,1,10) FROM signal_event ORDER BY 1"
                )
            ],
            universe=self.meta("universe", {}),
            generation=self.meta("generation", 1),
            revision=self.meta("revision", 0),
            signals=self.conn.execute("SELECT count(*) FROM signal_event").fetchone()[
                0
            ],
            symbol_states=self.conn.execute(
                "SELECT count(*) FROM symbol_state"
            ).fetchone()[0],
        )

    def history(self):
        # Preserve saved WATCH/gap rows and their real scan cycle alongside
        # immutable confirmations from every rule version.
        sessions = self.meta("sessions", [])
        cutoff = sessions[-60] if len(sessions) >= 60 else ""
        master = self.master()
        entries, rows, events = {}, {}, {}

        def entry(day):
            if day not in entries:
                cycle = self.conn.execute(
                    "SELECT * FROM scan_cycle WHERE trade_date=? ORDER BY bar_end DESC,updated_at DESC LIMIT 1",
                    (day,),
                ).fetchone()
                entries[day] = dict(
                    cycle=dict(cycle) if cycle else None, total=0, buy_count=0, rows=[]
                )
            return entries[day]

        for saved in self.conn.execute(
            "SELECT * FROM day_state WHERE trade_date>=? ORDER BY trade_date,symbol",
            (cutoff,),
        ):
            day = saved["trade_date"]
            entry(day)
            rows[(day, saved["symbol"])] = json.loads(saved["payload"])
        for saved in self.conn.execute(
            "SELECT * FROM signal_event WHERE confirmed_at>=? ORDER BY confirmed_at,event_id",
            (cutoff,),
        ):
            day = saved["confirmed_at"][:10]
            entry(day)
            event = json.loads(saved["payload"])
            key = (day, saved["symbol"])
            events.setdefault(key, []).append(event)
            if key not in rows:
                info = master.get(saved["symbol"], {})
                rows[key] = dict(
                    symbol=saved["symbol"],
                    name=info.get("name", saved["symbol"]),
                    market_date=day,
                    phase="CONFIRMED",
                    category="valid",
                    reason="BUY_STRUCTURE",
                    bar_end=saved["confirmed_at"],
                    processed_at=saved["first_seen_at"],
                    decision_json=encoded(event.get("decision", {})),
                )
        for key, row in rows.items():
            confirmed = events.get(key, [])
            row["events_json"] = encoded(confirmed)
            result = entry(key[0])
            result["total"] += 1
            result["buy_count"] += len(confirmed)
            if (
                confirmed
                or row["phase"] == "WATCHING"
                or row["category"] in ("gap", "failed")
            ):
                result["rows"].append(row)
        return entries
