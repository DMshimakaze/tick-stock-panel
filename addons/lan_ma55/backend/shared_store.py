"""Shared completed 15m -> frozen R1 -> existing immutable SQLite events."""

from copy import deepcopy
import json

from .engine import RULE_VERSION, STATE_REVISION, minimal_features
from .live_store import LiveStore, category
from .pipeline import empty_record, process_symbol, invalidate
from .store import encoded


class SharedStore(LiveStore):
    def __init__(self, path):
        super().__init__(path)
        self.conn.execute("""CREATE TABLE IF NOT EXISTS rule_state_archive(
            symbol TEXT,rule_version TEXT,payload TEXT NOT NULL,PRIMARY KEY(symbol,rule_version))""")
        with self.conn:
            self.set_meta("data_mode", "LAN_MARKETDATA_HTTP_V1_COMPLETED_15M")

    def source_master(self, rows, overview, generation):
        with self.conn:
            for row in rows:
                self.conn.execute(
                    "INSERT INTO security_master VALUES(?,?) ON CONFLICT(symbol) DO UPDATE SET payload=excluded.payload",
                    (row["symbol"], encoded(row)),
                )
            self.set_meta("universe", overview)
            self.set_meta("generation", generation)

    def consume(self, rows, *, sessions, target, now, live_after):
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
            receipt = [
                source.get("revision"),
                source.get("basis_revision"),
                target,
                RULE_VERSION,
                STATE_REVISION,
            ]
            if source_marks.get(symbol) == receipt:
                continue
            incompatible = bool(
                prior
                and (
                    prior["rule_version"] != RULE_VERSION
                    or prior["state"].get("state_revision") != STATE_REVISION
                )
            )
            basis_changed = bool(
                prior
                and prior["state"].get("source_basis_revision")
                != source.get("basis_revision")
            )
            bars = source.get("bars", [])
            restore = (
                not prior
                or incompatible
                or basis_changed
                or source.get("correction", False)
            )
            record = empty_record(symbol) if restore else deepcopy(prior)
            if restore and prior:
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
            unrecoverable = bool(
                (
                    incompatible
                    or (prior or {}).get("state", {}).get("migration_lock_at")
                )
                and lock_at
                and lock_index < 55
            )
            reset_at = None
            if unrecoverable:
                # A real full departure or failed support resets a prior lock
                # regardless of its missing internals, using 55 causal closes.
                for i, bar in enumerate(bars[55:], 55):
                    if not bar.get("valid"):
                        continue
                    feature = minimal_features(bars[max(0, i - 55) : i], bar, [])
                    if bar["close"] < feature["ma55"] * 0.99 or (
                        bar["low"] > feature["known_ma55"] * 1.015
                        and bar["close"] > feature["ma55"] * 1.015
                    ):
                        reset_at = bar["timestamp"]
                        unrecoverable = False
                        break
            if (
                source.get("state")
                in ("FACTOR_PENDING", "REBUILD_PENDING", "METADATA_PENDING")
                or source.get("price_basis") != "QFQ"
            ):
                invalidate(record["state"], source.get("state") or "FACTOR_PENDING")
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
            record["state"].update(
                state_revision=STATE_REVISION,
                source_basis_revision=source.get("basis_revision"),
                shared_source_asof=source.get("source_asof"),
                shared_receipt=receipt,
            )
            kind = category(record, target)
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
        with self.conn:
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
                dict(day=target[:10], symbols=[r["symbol"] for r in records]),
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
                    "SELECT DISTINCT trade_date FROM day_state ORDER BY trade_date"
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
