"""Independent versioned MA55 observations backed by the existing owner."""

from bisect import bisect_left, bisect_right
from datetime import timedelta
import json
from math import isfinite

from .exchange_sessions import INTRADAY
from .ma55_levels import project_levels
from .market_grid import history_capacity, wall, with_averages
from .market_store import encoded
from .multiframe_core.multiframe import TIMEFRAMES, VERSION, analyze_timeframe
from .multiframe_input import DAILY_HISTORY, frame_bars, replay_input

CHART_LIMIT = 250


class Observations:
    def __init__(self, market, daily):
        self.market, self.daily, self.conn = market, daily, market.conn
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS ma55_observation_v1(
                rule_version TEXT, symbol TEXT, timeframe TEXT,
                replay_start TEXT, input_key TEXT NOT NULL, payload TEXT NOT NULL,
                PRIMARY KEY(rule_version, symbol, timeframe));
            CREATE TABLE IF NOT EXISTS ma55_observation_event_v1(
                event_id TEXT PRIMARY KEY, rule_version TEXT, symbol TEXT, timeframe TEXT,
                confirmed_at TEXT NOT NULL, first_seen_at TEXT NOT NULL,
                mode TEXT NOT NULL, payload TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS ma55_observation_event_time_v1
                ON ma55_observation_event_v1(rule_version, symbol, timeframe, confirmed_at);
        """)

    def saved(self, symbol):
        return {r["timeframe"]: r for r in self.conn.execute(
            "SELECT * FROM ma55_observation_v1 WHERE rule_version=? AND symbol=?", (VERSION, symbol))}

    def reference_basis(self, symbol, info, target):
        if not info or not target or (info["factors_day"] or "") < target[:10]:
            return None, None
        factors = self.market.events(symbol)
        if factors is None:
            return None, None
        effective = [event for event in factors if event["day"] <= target[:10]]
        last = effective[-1]["day"] if effective else "NONE"
        return effective, f"TickFlow:QFQ:{symbol}:{info['factor_revision']}:{last}"

    def refresh(self, symbols, sessions, schedules, weeks, now, live_after, *, live=False):
        target = schedules["15m"][-1] if schedules["15m"] else None
        changed = False
        with self.conn:
            for symbol in symbols:
                info = self.market.instrument(symbol)
                if not info or not info["eligible"]:
                    continue
                saved = self.saved(symbol)
                factors, basis = self.reference_basis(symbol, info, target)
                tick = info["raw_tick"]
                market_revision = self.market.meta("calendar_revision", 0)
                latest_minute = {}
                raw = {}
                for timeframe in TIMEFRAMES:
                    schedule = schedules[timeframe]
                    end = schedule[-1] if schedule else None
                    previous = saved.get(timeframe)
                    prior = json.loads(previous["payload"]) if previous else {}
                    start = previous["replay_start"] if previous else None
                    minute = timeframe in INTRADAY
                    revision = info["basis_revision"] if minute else self.daily.frame_revision(symbol, end)
                    # A forming 30/60/120m bucket leaves its completed input alone.
                    # A change in the covered row count also detects completed repairs.
                    if minute and end and end not in latest_minute:
                        latest_minute[end] = self.conn.execute(
                            "SELECT count(*) FROM native15_bar WHERE symbol=? AND bar_end<=?", (symbol, end)
                        ).fetchone()[0]
                    key = encoded([end, basis, tick, revision, market_revision, latest_minute.get(end) if minute else None])
                    if previous and key == previous["input_key"]:
                        continue
                    if end and minute not in raw:
                        if minute:
                            origins = [str(wall(r["replay_start"]) - timedelta(hours=2))
                                       for tf, r in saved.items() if tf in INTRADAY and r["replay_start"]]
                            recent = schedules["15m"][-history_capacity():]
                            lower = min([*origins, recent[0]]) if recent else ""
                            raw[minute] = self.market.raw_rows(symbol, target, start=lower, limit=-1)
                        else:
                            origins = [str(wall(r["replay_start"]) - timedelta(days=7))
                                       for tf, r in saved.items() if tf not in INTRADAY and r["replay_start"]]
                            recent = schedules["daily"][-DAILY_HISTORY:]
                            lower = min([*origins, recent[0]]) if recent else ""
                            raw[minute] = self.daily.raw_rows(symbol, schedules["daily"][-1], start=lower)
                    bars = frame_bars(raw.get(minute, ()), timeframe, sessions, weeks, end, factors, target)
                    inputs, reason = replay_input(bars, schedule, start)
                    if not basis:
                        reason = "FACTOR_PENDING"
                    elif not tick or not isfinite(tick) or tick <= 0:
                        reason = "TICK_UNAVAILABLE"
                    result, candidate = None, None
                    reference = self.price_reference(bars) if timeframe == "15m" else None
                    if not reason:
                        result = analyze_timeframe(symbol, timeframe, inputs, asof=end, raw_tick=tick)
                        start = inputs[0]["timestamp"]
                        candidate = self.save_events(result.pop("events"), prior, end, now, live_after, live, basis, revision)
                    payload = dict(status=reason or "READY", basis=basis, expected_end=end, result=result,
                                   latest_candidate=candidate, reference=reference, replay_start=start,
                                   source_revision=revision, asset_type=info["source_type"], raw_tick=tick)
                    self.conn.execute("INSERT OR REPLACE INTO ma55_observation_v1 VALUES(?,?,?,?,?,?)",
                                      (VERSION, symbol, timeframe, start, key, encoded(payload)))
                    changed = True
        return changed

    def save_events(self, events, prior, end, now, live_after, live, basis, revision):
        previous_at = ((prior.get("result") or {}).get("latest") or {}).get("timestamp")
        for event in events:
            confirmed = str(wall(event["timestamp"]))
            live_event = bool(live and previous_at and str(wall(previous_at)) < confirmed
                              and confirmed >= live_after and confirmed == end)
            mode = "REPLAY"
            if live_event:
                mode = "LIVE" if (wall(now) - wall(confirmed)).total_seconds() <= 180 else "DELAYED"
            self.conn.execute("INSERT OR IGNORE INTO ma55_observation_event_v1 VALUES(?,?,?,?,?,?,?,?)",
                              (event["event_id"], VERSION, event["symbol"], event["timeframe"],
                               confirmed, now, mode, encoded(dict(event, basis=basis, source_revision=revision))))
        if not events:
            return None
        latest = events[-1]
        saved = self.conn.execute("SELECT mode,first_seen_at FROM ma55_observation_event_v1 WHERE event_id=?",
                                  (latest["event_id"],)).fetchone()
        return dict(at=str(wall(latest["timestamp"])), id=latest["event_id"],
                    mode=saved["mode"], first_seen_at=saved["first_seen_at"])

    @staticmethod
    def price_reference(bars):
        last = next((bar for bar in reversed(bars) if bar["volume"] > 0), None)
        if not last:
            return None
        at = last["timestamp"]
        previous = next((row for row in reversed(bars) if row["timestamp"][:10] < at[:10]), None)
        change = (last["close"] / previous["close"] - 1) * 100 if (
            previous and previous["timestamp"].endswith("15:00:00") and previous["volume"] > 0
        ) else None
        return dict(price=last["close"], at=at, change_pct=change, price_basis=last["price_basis"])

    def rows(self, snapshot, master, schedules):
        members = snapshot["members"]
        symbols = json.dumps(list(members))
        saved = {symbol: {} for symbol in members}
        for item in self.conn.execute(
            "SELECT symbol,timeframe,payload FROM ma55_observation_v1 WHERE rule_version=? "
            "AND symbol IN (SELECT value FROM json_each(?))", (VERSION, symbols),
        ):
            saved[item["symbol"]][item["timeframe"]] = json.loads(item["payload"])
        target = schedules["15m"][-1] if schedules["15m"] else None
        rows = []
        for symbol, keys in members.items():
            info = master.get(symbol) or {}
            frames = {}
            for tf in TIMEFRAMES:
                data = saved[symbol].get(tf) or {}
                end = schedules[tf][-1] if schedules[tf] else None
                status = data.get("status")
                if not info.get("eligible"):
                    status = "METADATA_PENDING"
                elif data.get("expected_end") != end:
                    status = "WAITING_FOR_COMPLETED_TARGET"
                frames[tf] = dict(data, expected_end=end, status=status or "WAITING_INPUT")
            minute = frames["15m"]
            ref = minute.get("reference")
            if ref and (not target or ref["at"] > target):
                ref = None
            levels = (project_levels(symbol, frames, price=ref["price"], price_at=ref["at"],
                                     asof=target, basis=minute["basis"])
                      if target and ref and ref["price_basis"] == "QFQ" and minute.get("basis") else None)
            rows.append(dict(symbol=symbol, name=info.get("name") or symbol, raw_tick=info.get("raw_tick"),
                             sectors=keys, frames=frames, reference=ref, levels=levels))
        return rows

    def chart(self, symbol, timeframe, sessions, schedules, weeks, *, anchor_event=None):
        schedule = schedules[timeframe]
        target = schedules["15m"][-1] if schedules["15m"] else None
        info = self.market.instrument(symbol)
        factors, basis = self.reference_basis(symbol, info, target)
        if not schedule or not info:
            return dict(symbol=symbol, timeframe=timeframe, bars=[], events=[], status="WAITING_INPUT", basis=basis)
        end = schedule[-1]
        if anchor_event and anchor_event < end:
            pos = bisect_left(schedule, anchor_event)
            end = schedule[min(len(schedule) - 1, pos + CHART_LIMIT // 3)]
        end_index = bisect_right(schedule, end)
        grid = schedule[max(0, end_index - CHART_LIMIT - 55):end_index]
        start = grid[0]
        if timeframe in INTRADAY:
            raw = self.market.raw_rows(symbol, end, start=str(wall(start) - timedelta(hours=2)), limit=-1)
        else:
            raw = self.daily.raw_rows(symbol, end, start=str(wall(start) - timedelta(days=7)))
        bars = frame_bars(raw, timeframe, sessions, weeks, end, factors, target)
        for bar in bars:
            bar["valid"] = bar["valid"] and bar["volume"] > 0
        bars = with_averages(bars, grid, (55,))[-CHART_LIMIT:]
        events = []
        if bars:
            events = [dict(json.loads(row["payload"]), mode=row["mode"], at=row["confirmed_at"])
                      for row in self.conn.execute(
                          "SELECT * FROM ma55_observation_event_v1 WHERE rule_version=? AND symbol=? "
                          "AND timeframe=? AND confirmed_at BETWEEN ? AND ? ORDER BY confirmed_at",
                          (VERSION, symbol, timeframe, bars[0]["timestamp"], end),
                      )]
        saved = self.saved(symbol).get(timeframe)
        status = json.loads(saved["payload"])["status"] if saved else "WAITING_INPUT"
        return dict(symbol=symbol, timeframe=timeframe, name=info["name"], tick=info["raw_tick"],
                    bars=bars, events=events, status=status, basis=basis, expected_end=schedule[-1],
                    asof=bars[-1]["timestamp"] if bars else None, grid=grid)
