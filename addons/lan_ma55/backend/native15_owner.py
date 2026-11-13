"""Native market + candidate state commit on the established single writer connection."""

from contextlib import closing
import json
from time import perf_counter, time

from .unified_store import UnifiedStore
from .shared_store import SharedStore
from .market_store import encoded, reader
from .market_grid import now_cn, history_capacity, project
from .native15_store import NativeStore
from .native15_signal_adapter import scan, FAMILY
from .native15_shadow import SOURCE


class NativeOwner(UnifiedStore):
    def __init__(self, path):
        super().__init__(path)
        self.archive_market = self.market
        self.market = NativeStore(path, connection=self.conn)
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS native15_signal_state(
              symbol TEXT PRIMARY KEY,payload TEXT,basis_revision INTEGER,factor_revision INTEGER,
              target TEXT,analysis_asof TEXT,category TEXT,reason TEXT,generation INTEGER,
              source_revision INTEGER,updated_at TEXT);
            CREATE TABLE IF NOT EXISTS native15_candidate(
              event_id TEXT PRIMARY KEY,symbol TEXT,confirmed_at TEXT,first_seen_at TEXT,mode TEXT,
              signal_family TEXT,source_id TEXT,source_count INTEGER CHECK(source_count=1),payload TEXT);
            CREATE INDEX IF NOT EXISTS native15_candidate_time ON native15_candidate(symbol,confirmed_at);
            CREATE TABLE IF NOT EXISTS native15_candidate_risk(event_id TEXT PRIMARY KEY,payload TEXT,updated_at TEXT);
            CREATE TABLE IF NOT EXISTS native15_day_state(symbol TEXT,trade_date TEXT,payload TEXT,PRIMARY KEY(trade_date,symbol));
            CREATE TABLE IF NOT EXISTS native15_timing(id INTEGER PRIMARY KEY,at TEXT,stage TEXT,symbols INTEGER,
              write_ms REAL,scan_ms REAL,commit_ms REAL);
            CREATE TABLE IF NOT EXISTS native15_request_receipt(id INTEGER PRIMARY KEY,at TEXT,endpoint TEXT,
              capability TEXT,status INTEGER,latency_ms REAL,symbol_count INTEGER,stage TEXT,retries INTEGER,
              period TEXT,quota_headers TEXT);
        """)
        with self.conn:
            self.set_meta("data_mode", FAMILY)

    def meta(self, key, default=None):
        return SharedStore.meta(self, "native15."+key, default)

    def set_meta(self, key, value):
        SharedStore.set_meta(self, "native15."+key, value)

    def refresh_master(self, rows, overview, sessions):
        with self.conn:
            self.market.save_master(rows, overview, sessions)
            self.set_meta("generation", self.market.meta("generation", 0))
            self.set_meta("universe", overview)
            self.set_meta("sessions", self.market.meta("sessions", []))

    def accept_batch(self, symbols, received, errors, *, sessions, start, end, now, factors, stage, live_after):
        began = perf_counter()
        self.conn.execute("BEGIN IMMEDIATE")
        self.last_lock_wait_ms = (perf_counter()-began)*1000
        try:
            for symbol in symbols:
                if symbol in factors:
                    self.market.factor_response(symbol, factors[symbol], now, sessions)
                error = errors.get(symbol)
                code = error["code"] if error and error["timestamps"] is None else None
                if code:
                    self.market.failed([symbol], end, now, code, stage=stage)
                    continue
                try:
                    self.market.accept(symbol, received.get(symbol, {}), sessions=sessions, start=start, end=end, now=now,
                                       stage=stage)
                except ValueError as exc:
                    if str(exc) not in ("NATIVE15_PROVENANCE_REQUIRED", "NATIVE15_NON_TRADING_POINT"):
                        raise
                    self.market.failed([symbol], end, now, str(exc), stage=stage)
                    continue
                if error:
                    # Valid securities and valid rows survive a bad OHLC in the same HTTP.
                    self.market.failed([symbol], end, now, error["code"], stage=stage)
                if stage == "C" and received.get(symbol):
                    listing = self.market.instrument(symbol)["listing_date"]
                    gap = self.market.history_progress(symbol).get("first_gap")
                    stop = "GAP" if gap else "INSUFFICIENT" if listing and start[:10] <= listing else None
                    self.conn.execute("UPDATE native15_history_progress SET scanned_before=?,stop_reason=?,next_retry=? WHERE symbol=?",
                                      (start, stop, 0 if not stop else time()+(300 if stop == "GAP" else 86400), symbol))
            written = perf_counter()
            self.analyze(symbols, sessions, self.meta("target", end), now, live_after, replay=stage == "C")
            scanned = perf_counter()
            self.conn.commit()
            committed = perf_counter()
            with self.conn:
                self.conn.execute("INSERT INTO native15_timing(at,stage,symbols,write_ms,scan_ms,commit_ms) VALUES(?,?,?,?,?,?)",
                                  (now, stage, len(symbols), (written-began)*1000, (scanned-written)*1000, (committed-scanned)*1000))
                self.conn.execute("DELETE FROM native15_timing WHERE id<(SELECT max(id)-1000 FROM native15_timing)")
        except Exception:
            self.conn.rollback()
            raise

    def accept_factors(self, symbols, factors, *, sessions, target, now, live_after):
        with self.conn:
            self.conn.execute("BEGIN IMMEDIATE")
            changed = [s for s in symbols if self.market.factor_response(s, factors.get(s), now, sessions)]
            self.analyze(changed, sessions, target, now, live_after, replay=True)
            self.market.set_meta("last_factor_check", now)
            if any(factors.get(s) is not None for s in symbols):
                self.market.set_meta("last_factor_success", now)

    def analyze(self, symbols, sessions, target, now, live_after, *, replay=False):
        changed = []
        for symbol in symbols:
            symbol_replay = replay
            source = self.market.read(symbol, "15m", target, connection=self.conn, strategy=True)
            info = self.market.instrument(symbol)
            factor_ready = info['factor_coverage'] == 'EXPLICIT' and (info['factors_day'] or '') >= target[:10]
            prior = self.conn.execute("SELECT * FROM native15_signal_state WHERE symbol=?", (symbol,)).fetchone()
            generation = self.market.meta("generation", 0)
            if prior and (prior["target"], prior["source_revision"], prior["generation"]) == (target, source.get("revision", 0), generation):
                continue
            state = json.loads(prior["payload"]) if prior else None
            reset = not prior or (prior["basis_revision"], prior["factor_revision"]) != (source.get("basis_revision", 0), source.get("factor_revision", 0))
            revision_blocked = False
            # Failed fetches hold the causal structure and watermarks. Valid later
            # rows handle actual gaps in the adapter, just as the frozen core expects.
            allowed = factor_ready and source.get("price_basis") == "QFQ" and source.get("raw_tick") and source.get("bars")
            added = []
            structure = (state or {}).get("structure") or {}
            lock_at = (structure.get("last_buy") or {}).get("at") if structure.get("locked") else None
            if reset and prior and lock_at and allowed:
                # Reconstruct the known lock using real native history and the
                # same pure functions. Missing evidence holds it; it never rearms.
                raw = self.market.raw_rows(symbol, target, limit=history_capacity(), conn=self.conn)
                history = [dict(b, source_proof="NATIVE_15M_COMPLETED") for b in project(
                    raw, self.market.events(symbol), target, sessions=sessions, timeframe="15m", periods=())]
                lock_index = next((i for i, b in enumerate(history) if b["timestamp"] == lock_at), -1)
                restored = None
                if lock_index >= 55:
                    restored, _ = scan(symbol, history[:lock_index+1], sessions=sessions, raw_tick=source["raw_tick"],
                                       target=target, now=now, live_after=live_after, replay=True)
                restored_lock = (restored or {}).get("structure") or {}
                verified = restored_lock.get("locked") and (restored_lock.get("last_buy") or {}).get("at") == lock_at
                if verified:
                    state, reset = restored, False
                    source["bars"] = history[lock_index+1:]
                    symbol_replay = True
                else:
                    revision_blocked = True
                    allowed = False
                    state["reason"] = "INPUT_REVISION_LOCK_HISTORY_REQUIRED"
            if allowed:
                state, added = scan(symbol, source["bars"], sessions=sessions, raw_tick=source["raw_tick"],
                                    target=target, now=now, previous=None if reset else state,
                                    basis_revision=source["basis_revision"], factor_revision=source["factor_revision"],
                                    live_after=live_after, replay=symbol_replay or reset)
            else:
                from .pipeline import empty_record
                state = state or empty_record(symbol)["state"]
                if not revision_blocked:
                    state["reason"] = "TICK_UNAVAILABLE" if not source.get("raw_tick") else "FACTOR_PENDING" if not factor_ready else source.get("state", "WARMUP")
            analyzed = state.get("data_asof")
            category = "gap" if revision_blocked else "valid" if allowed and analyzed == target and state["valid_run"] >= 56 else "insufficient" if state["reason"] in ("WARMUP", "INSUFFICIENT_WARMUP") else "gap"
            self.conn.execute("INSERT INTO native15_signal_state VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(symbol) DO UPDATE SET "
                              "payload=excluded.payload,basis_revision=excluded.basis_revision,factor_revision=excluded.factor_revision,"
                              "target=excluded.target,analysis_asof=excluded.analysis_asof,category=excluded.category,reason=excluded.reason,"
                              "generation=excluded.generation,source_revision=excluded.source_revision,updated_at=excluded.updated_at",
                              (symbol, encoded(state), prior["basis_revision"] if revision_blocked else source.get("basis_revision", 0),
                               prior["factor_revision"] if revision_blocked else source.get("factor_revision", 0), target,
                               analyzed, category, state["reason"], generation, source.get("revision", 0), now))
            for event in added:
                self.conn.execute("INSERT OR IGNORE INTO native15_candidate VALUES(?,?,?,?,?,?,?,?,?)",
                                  (event["event_id"], symbol, event["confirmed_at"], now, event["mode"], FAMILY, SOURCE, 1, encoded(event)))
            for row in self.conn.execute("SELECT event_id,payload FROM native15_candidate WHERE symbol=?", (symbol,)):
                event = json.loads(row["payload"])
                risk = dict(data_asof=analyzed, data_pending_at=None if category == "valid" else target,
                            reason=None if category == "valid" else state["reason"], basis_revision=source.get("basis_revision"),
                            extended=bool(((state.get("decision") or {}).get("close_distance_pct") or 0) > 3))
                old = self.conn.execute("SELECT payload FROM native15_candidate_risk WHERE event_id=?", (row["event_id"],)).fetchone()
                prior_risk = json.loads(old[0]) if old else {}
                same_basis = event["basis_revision"] == source.get("basis_revision")
                risk["invalidated_at"] = prior_risk.get("invalidated_at") or (analyzed if same_basis and state.get("close", event["defense"]) < event["defense"] else None)
                if not same_basis:
                    risk["reason"] = "PRICE_BASIS_CHANGED"
                self.conn.execute("INSERT OR REPLACE INTO native15_candidate_risk VALUES(?,?,?)", (row["event_id"], encoded(risk), now))
            info = self.market.instrument(symbol)
            events = [json.loads(r[0]) for r in self.conn.execute("SELECT payload FROM native15_candidate WHERE symbol=? AND confirmed_at>=? AND confirmed_at<=? ORDER BY confirmed_at", (symbol, target[:10], target))]
            row = dict(symbol=symbol, name=info["name"], market_date=target[:10], generation=generation,
                       phase="DATA_PENDING" if category != "valid" else "CONFIRMED" if (state.get("structure") or {}).get("locked") else "WATCHING" if (state.get("decision") or {}).get("impulse_recent") else "NO_SETUP",
                       category=category, reason=state["reason"], bar_end=analyzed or "", processed_at=now,
                       events_json=encoded(events), decision_json=encoded(state.get("decision") or {}))
            self.conn.execute("INSERT OR REPLACE INTO native15_day_state VALUES(?,?,?)", (symbol, target[:10], encoded(row)))
            changed.append(symbol)
        if changed:
            self.set_meta("revision", self.meta("revision", 0)+1)
            self.set_meta("pending_publish", dict(day=target[:10], symbols=sorted(set((self.meta("pending_publish") or {}).get("symbols", [])) | set(changed))))
        return changed

    def catch_up(self, target, now, live_after):
        return [r[0] for r in self.conn.execute("SELECT i.symbol FROM native15_instrument_progress i "
                "LEFT JOIN native15_signal_state s USING(symbol) WHERE i.eligible=1 AND i.latest_completed15>=? "
                "AND (s.symbol IS NULL OR s.target!=? OR s.source_revision!=i.revision OR s.generation!=?)",
                (target, target, self.market.meta("generation", 0)))]

    def day_rows(self, day, symbols=None):
        rows = [json.loads(r[0]) for r in self.conn.execute("SELECT payload FROM native15_day_state WHERE trade_date=? ORDER BY symbol", (day,))]
        return rows if symbols is None else [r for r in rows if r["symbol"] in symbols]

    def history(self):
        days = [r[0] for r in self.conn.execute("SELECT trade_date FROM native15_day_state UNION "
                "SELECT substr(confirmed_at,1,10) FROM native15_candidate ORDER BY 1 DESC LIMIT 60")]
        history = {}
        for day in days:
            rows = {r["symbol"]: r for r in self.day_rows(day)}
            for saved in self.conn.execute("SELECT e.symbol,e.payload,i.name FROM native15_candidate e "
                         "JOIN native15_instrument_progress i USING(symbol) WHERE substr(e.confirmed_at,1,10)=? ORDER BY e.confirmed_at", (day,)):
                event = json.loads(saved["payload"])
                row = rows.setdefault(saved["symbol"], dict(symbol=saved["symbol"], name=saved["name"], market_date=day,
                        phase="CONFIRMED", category="valid", reason="REPLAY_CANDIDATE", bar_end=event["confirmed_at"],
                        processed_at=event["first_seen_at"], events_json="[]", decision_json=encoded(event["decision"])))
                events = json.loads(row["events_json"])
                if event["event_id"] not in {e["event_id"] for e in events}:
                    row["events_json"] = encoded(events+[event])
            history[day] = dict(rows=list(rows.values()), total=len(rows))
        return history

    def summary(self, end):
        return dict(market_date=end[:10] if end else None,
                    available_dates=[r[0] for r in self.conn.execute("SELECT trade_date FROM native15_day_state UNION SELECT substr(confirmed_at,1,10) FROM native15_candidate ORDER BY 1")],
                    revision=self.meta("revision", 0), generation=self.market.meta("generation", 0),
                    universe=self.market.meta("universe", {}), signal_family=FAMILY)

    def save_requests(self, rows, stage):
        self.conn.executemany("INSERT INTO native15_request_receipt(at,endpoint,capability,status,latency_ms,symbol_count,stage,retries,period,quota_headers) "
                              "VALUES(?,?,?,?,?,?,?,?,?,?)",
                              [(r["at"], r["endpoint"], r["capability"], r["status"], r["latency_ms"], r["symbol_count"], stage,
                                r["retries"], r.get("period"), encoded(r.get("quota_headers", {}))) for r in rows])
        self.conn.execute("DELETE FROM native15_request_receipt WHERE id<(SELECT max(id)-10000 FROM native15_request_receipt)")

    def status_snapshot(self, *, scope=None, target=None):
        with closing(reader(self.market.path)) as conn:
            conn.execute("BEGIN")
            result = self.market.snapshot(scope=scope, target=target, connection=conn)
            target = result["refresh"]["target_15m"]
            where, params = "i.eligible=1", ()
            if scope is not None:
                where += " AND i.symbol IN (SELECT value FROM json_each(?))"
                params = (encoded(scope),)
            counts = conn.execute("SELECT count(*),max(s.analysis_asof),max(s.updated_at) FROM native15_instrument_progress i "
                         "JOIN native15_signal_state s USING(symbol) "
                         f"WHERE {where} AND s.category='valid' AND s.analysis_asof=? AND s.generation=? "
                         "AND i.factor_coverage='EXPLICIT' AND i.factors_day>=substr(s.target,1,10) AND s.source_revision=i.revision "
                         "AND EXISTS(SELECT 1 FROM native15_bar b WHERE b.symbol=i.symbol AND b.bar_end=? AND b.source_count=1)",
                         (*params, target, result["generation"], target)).fetchone()
            result["analysis"] = dict(target_15m=target, generation=result["generation"], calculated=counts[0],
                                      expected=result["refresh"]["expected"], latest_completed15=counts[1], updated_at=counts[2])
            def count(condition, args=()):
                return conn.execute(f"SELECT count(*) FROM native15_instrument_progress i WHERE {where} AND {condition}", (*params, *args)).fetchone()[0]
            retries = conn.execute("SELECT count(*) FROM native15_retry r JOIN native15_instrument_progress i USING(symbol) "
                                   f"WHERE {where} AND r.next_eligible>strftime('%s','now')", params).fetchone()[0]
            day = target[:10] if target else None
            candidate_count = conn.execute("SELECT count(*) FROM native15_candidate e JOIN native15_instrument_progress i USING(symbol) "
                                          f"WHERE {where} AND substr(e.confirmed_at,1,10)=?", (*params, day)).fetchone()[0]
            modes = dict(conn.execute("SELECT e.mode,count(*) FROM native15_candidate e JOIN native15_instrument_progress i USING(symbol) "
                                     f"WHERE {where} AND substr(e.confirmed_at,1,10)=? GROUP BY e.mode", (*params, day)))
            exclusive = dict(conn.execute("SELECT CASE "
                "WHEN NOT EXISTS(SELECT 1 FROM native15_bar b WHERE b.symbol=i.symbol AND b.bar_end=? AND b.source_count=1) THEN 'missing_target' "
                "WHEN i.factor_coverage!='EXPLICIT' OR i.factors_day IS NULL OR i.factors_day<substr(?,1,10) THEN 'factor_missing' "
                "WHEN s.category='valid' AND s.analysis_asof=? AND s.generation=? AND s.source_revision=i.revision THEN 'analyzed' "
                "WHEN s.reason IN ('WARMUP','INSUFFICIENT_WARMUP','MISSING_COMPLETED_15M') OR i.state IN ('WARMUP','GAP') THEN 'warmup' "
                "ELSE 'invalid_input' END AS input_group,count(*) FROM native15_instrument_progress i "
                f"LEFT JOIN native15_signal_state s USING(symbol) WHERE {where} GROUP BY input_group",
                (target,target,target,result['generation'],*params)))
            factor_pending = "(i.factor_coverage!='EXPLICIT' OR i.factors_day IS NULL OR i.factors_day!=?)"
            factor_stats = conn.execute("SELECT count(*),sum(COALESCE(f.next_eligible,0)<=?),sum(f.next_eligible>?),min(f.next_eligible),max(f.last_attempt),max(f.verified_at) "
                "FROM native15_instrument_progress i LEFT JOIN native15_factor_retry f USING(symbol) "
                f"WHERE {where} AND {factor_pending}", (*[time()]*2,*params,day)).fetchone()
            next_market = conn.execute("SELECT min(r.next_eligible) FROM native15_retry r JOIN native15_instrument_progress i USING(symbol) "
                f"WHERE {where} AND r.stage!='C' AND r.next_eligible>?", (*params,time())).fetchone()[0]
            pending = conn.execute("SELECT payload FROM runtime_meta WHERE key='native15.pending_publish'").fetchone()
            result.update(market_date=day, latest_buy_date=day, latest_day_buy_count=candidate_count,
                          today_candidate_modes=modes, signal_family=FAMILY, today_target=target,
                          today_native15_saved=result["refresh"]["refreshed"], today_analyzed_candidates=counts[0],
                          warmup_ready=count("state='READY'"), waiting_retry=retries,
                          source_unavailable=count("state IN ('SOURCE_UNAVAILABLE','EMPTY_NATIVE15','SOURCE_LAG')"),
                          factor_pending=count("factor_coverage!='EXPLICIT'"), tick_pending=count("raw_tick IS NULL OR raw_tick<=0"),
                          input_revision_pending=conn.execute("SELECT count(*) FROM native15_signal_state s JOIN native15_instrument_progress i USING(symbol) "
                                                              f"WHERE {where} AND s.reason='INPUT_REVISION_LOCK_HISTORY_REQUIRED'", params).fetchone()[0],
                          history_233_ready=conn.execute("SELECT count(*) FROM native15_history_progress p JOIN native15_instrument_progress i USING(symbol) "
                                                        f"WHERE {where} AND p.raw_count>=1280 AND p.first_gap IS NULL AND i.factor_coverage='EXPLICIT'", params).fetchone()[0],
                          last_success_live=self.market.meta("last_success_live", conn=conn),
                          last_success_history=self.market.meta("last_success_history", conn=conn),
                          exclusive={key:exclusive.get(key,0) for key in ('analyzed','factor_missing','warmup','invalid_input','missing_target')},
                          factor_retry=dict(pending=factor_stats[0],due=factor_stats[1] or 0,cooling=factor_stats[2] or 0,
                                            next_eligible=factor_stats[3],last_attempt=factor_stats[4],last_verified=self.market.meta("last_factor_success",conn=conn)),
                          factors_verified_today=count("factor_coverage='EXPLICIT' AND factors_day=?",(day,)),
                          next_market_retry=next_market,
                          legacy_frozen_buy_count=conn.execute("SELECT count(*) FROM signal_event").fetchone()[0],
                          legacy_bar_count=conn.execute("SELECT count(*) FROM bar15").fetchone()[0],
                          pending_publish=bool(pending and json.loads(pending[0])), status_at=str(now_cn()))
            result["history_backfill_pending"] = result["history_queue_count"]
            ready = "EXISTS(SELECT 1 FROM native15_history_progress p WHERE p.symbol=i.symbol AND p.raw_count>=56) AND rebuild_from IS NULL"
            result["queues"] = dict(A=dict(pending=count(f"latest_completed15<? AND {ready}", (target,)) if target else 0),
                                    B=dict(pending=count(f"latest_completed15 IS NULL OR NOT ({ready})")),
                                    F=dict(pending=factor_stats[0]),
                                    C=dict(pending=result["history_queue_count"]))
            return result


def candidates_page(path, *, limit=50, offset=0, symbol=None, start=None, end=None):
    with closing(reader(path)) as conn:
        conn.execute("BEGIN")
        where, params = "1=1", []
        for column, op, value in (("symbol", "=", symbol), ("confirmed_at", ">=", start), ("confirmed_at", "<=", end)):
            if value:
                where += f" AND e.{column}{op}?"
                params.append(value)
        total = conn.execute(f"SELECT count(*) FROM native15_candidate e WHERE {where}", params).fetchone()[0]
        rows = [dict(json.loads(r[0]), risk=json.loads(r[1]) if r[1] else {}) for r in conn.execute(
            f"SELECT e.payload,r.payload FROM native15_candidate e LEFT JOIN native15_candidate_risk r USING(event_id) WHERE {where} ORDER BY e.confirmed_at DESC,e.event_id LIMIT ? OFFSET ?", (*params, limit, offset))]
        symbol_total = conn.execute("SELECT count(*) FROM native15_candidate WHERE symbol=?", (symbol,)).fetchone()[0] if symbol else total
        return dict(api_version=1, signal_family=FAMILY, total=total, symbol_total=symbol_total, rows=rows)
