"""Independent versioned MA55 observations backed by the existing owner."""

import json
from bisect import bisect_left, bisect_right
from datetime import timedelta
from math import isfinite

from .activation_results import activation, confirmations, display_targets, in_window
from .exchange_sessions import INTRADAY
from .ma55_levels import project_levels
from .market_grid import MAS, history_capacity, wall, with_averages
from .market_store import encoded
from .multiframe_core.multiframe import TIMEFRAMES, VERSION, analyze_timeframe
from .multiframe_input import DAILY_HISTORY, frame_bars, missing_window, replay_input, source_window
from .native15_store import in_verified_halt
from .workbench_diagnostics import chart_diagnostic

CHART_LIMIT = 250
INPUT_KEY_VERSION = 2


class Observations:
    def __init__(self, market, daily):
        self.market, self.daily, self.conn = market, daily, market.conn
        self.needs = {}
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

    def refresh(self, symbols, sessions, schedules, weeks, now, live_after, *, live=False, frames=TIMEFRAMES):
        target = schedules["15m"][-1] if schedules["15m"] else None
        changed = False
        market_revision = self.market.meta("calendar_revision", 0)
        halts = self.market.meta("verified_trading_halts", {})
        with self.conn:
            for symbol in symbols:
                info = self.market.instrument(symbol)
                if not info or not info["eligible"]:
                    continue
                saved = self.saved(symbol)
                factors, basis = self.reference_basis(symbol, info, target)
                tick = info["raw_tick"]
                halt = halts.get(symbol)
                latest_minute = {}
                raw, available = {}, {}
                for timeframe in frames:
                    schedule = schedules[timeframe]
                    end = schedule[-1] if schedule else None
                    previous = saved.get(timeframe)
                    start = previous["replay_start"] if previous else None
                    minute = timeframe in INTRADAY
                    source_grid = schedules["15m" if minute else "daily"]
                    recent = source_grid[-(history_capacity() if minute else DAILY_HISTORY):]
                    origins = [str(wall(r["replay_start"]) - (
                        timedelta(hours=2) if minute else timedelta(days=7)))
                        for tf, r in saved.items() if (tf in INTRADAY) == minute and r["replay_start"]]
                    lower = min([*origins, recent[0]]) if recent else ""
                    revision = info["basis_revision"] if minute else self.daily.frame_revision(symbol, end, start=lower)
                    # A forming 30/60/120m bucket leaves its completed input alone.
                    # A change in the covered row count also detects completed repairs.
                    if minute and end and end not in latest_minute:
                        latest_minute[end] = self.conn.execute(
                            "SELECT count(*) FROM native15_bar WHERE symbol=? AND bar_end BETWEEN ? AND ?",
                            (symbol, lower, end),
                        ).fetchone()[0]
                    inputs_key = [INPUT_KEY_VERSION, end, basis, tick, revision, market_revision,
                                  latest_minute.get(end) if minute else None, info["listing_date"]]
                    if halt:
                        inputs_key.append(halt)
                    key = encoded(inputs_key)
                    fact = (symbol, timeframe)
                    prior = json.loads(previous["payload"]) if previous else {}
                    if previous and key == previous["input_key"]:
                        self.needs[fact] = prior.get("missing_raw")
                        continue
                    if end and minute not in raw:
                        if minute:
                            raw[minute] = self.market.raw_rows(symbol, target, start=lower, limit=-1)
                        else:
                            raw[minute] = self.daily.raw_rows(symbol, schedules["daily"][-1], start=lower)
                        if halt:
                            raw[minute] = [bar for bar in raw[minute] if not in_verified_halt(halt, bar["timestamp"])]
                        available[minute] = {bar["timestamp"] for bar in raw[minute]}
                    required = source_window(timeframe, schedule, source_grid, weeks, start, info["listing_date"])
                    halted = in_verified_halt(halt, end)
                    if halt:
                        required = [at for at in required if not in_verified_halt(halt, at)]
                    missing = None if halted else missing_window(required, available.get(minute, ()))
                    self.needs[fact] = missing
                    bars = (frame_bars(raw.get(minute, ()), timeframe, sessions, weeks, end, factors, target)
                            if basis or timeframe == "15m" else [])
                    if halted:
                        reason = "TRADING_HALTED_VERIFIED"
                    elif not basis:
                        reason = "FACTOR_PENDING"
                    elif not tick or not isfinite(tick) or tick <= 0:
                        reason = "TICK_UNAVAILABLE"
                    else:
                        inputs, reason = replay_input(bars, schedule, start)
                    result, candidate = None, None
                    prior_window = confirmations(prior)
                    confirmed = None
                    reference = self.price_reference(bars) if timeframe == "15m" else None
                    if not reason:
                        result = analyze_timeframe(symbol, timeframe, inputs, asof=end, raw_tick=tick)
                        start = inputs[0]["timestamp"]
                        candidate, confirmed = self.save_events(
                            result.pop("events"), prior, prior_window, timeframe, end, now, live_after, live, basis, revision,
                        )
                    elif prior_window:
                        confirmed = dict(prior_window, verified=bool(
                            minute and in_window(prior_window["through"], end, timeframe)
                            and prior_window["through"] < end and prior_window.get("verified", True)
                            and basis == prior.get("basis") and prior.get("source_revision") == revision
                            and reason in ("MISSING_COMPLETED_BAR", "ZERO_VOLUME_BAR", "GAP_IN_REPLAY_WINDOW")
                            and (not missing or missing[0] > prior_window["through"])
                        ))
                    payload = dict(status=reason or "READY", basis=basis, expected_end=end, result=result,
                                   latest_candidate=candidate, reference=reference, replay_start=start,
                                   missing_raw=missing, confirmations=confirmed,
                                   source_revision=revision, asset_type=info["source_type"], raw_tick=tick)
                    self.conn.execute("INSERT OR REPLACE INTO ma55_observation_v1 VALUES(?,?,?,?,?,?)",
                                      (VERSION, symbol, timeframe, start, key, encoded(payload)))
                    changed = True
        return changed

    def save_events(self, events, prior, prior_window, timeframe, end, now, live_after, live, basis, revision):
        previous_at = ((prior.get("result") or {}).get("latest") or {}).get("timestamp")
        current = {}
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
            if in_window(confirmed, end, timeframe) or event is events[-1]:
                current[event["event_id"]] = (confirmed, event["close"])
        saved = {}
        for row in self.conn.execute(
            "SELECT event_id,mode,first_seen_at FROM ma55_observation_event_v1 "
            "WHERE event_id IN (SELECT value FROM json_each(?))", (encoded(list(current)),),
        ):
            at, price = current[row["event_id"]]
            saved[row["event_id"]] = dict(at=at, id=row["event_id"], mode=row["mode"],
                                           first_seen_at=row["first_seen_at"], price=price)
        active = [saved[key] for key, (at, _) in current.items() if in_window(at, end, timeframe)]
        previous = set()
        if prior_window:
            previous.update(prior_window["invalidated"])
            if prior_window["through"] <= end:
                previous.update(event["id"] for event in prior_window["events"])
        invalidated = sorted(previous - {event["event_id"] for event in events}) if previous else []
        latest = saved[events[-1]["event_id"]] if events else None
        return latest, dict(through=end, events=active, invalidated=invalidated)

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

    def rows(self, snapshot, master, schedules, market_day=None):
        members = snapshot["members"]
        symbols = json.dumps(list(members))
        saved = {symbol: {} for symbol in members}
        for item in self.conn.execute(
            "SELECT symbol,timeframe,payload FROM ma55_observation_v1 WHERE rule_version=? "
            "AND symbol IN (SELECT value FROM json_each(?))", (VERSION, symbols),
        ):
            saved[item["symbol"]][item["timeframe"]] = json.loads(item["payload"])
        target = schedules["15m"][-1] if schedules["15m"] else None
        targets = display_targets({tf: times[-1] for tf, times in schedules.items() if times}, market_day)
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
                reviewed = confirmations(data)
                frame = dict(data, expected_end=end, status=status or "WAITING_INPUT", confirmations=reviewed)
                # A correction queued for replay cannot present the earlier inputs as current.
                basis_changed = data.get("basis") and "factor_revision" in info and not data["basis"].startswith(
                    f"TickFlow:QFQ:{symbol}:{info['factor_revision']}:",
                )
                corrected = tf in INTRADAY and data and "basis_revision" in info and (
                    data.get("source_revision") != info["basis_revision"]
                )
                if not info.get("eligible") or basis_changed or corrected:
                    frame["confirmations"] = dict(reviewed, verified=False) if reviewed else None
                    if info.get("eligible") and status in ("READY", "WAITING_FOR_COMPLETED_TARGET"):
                        frame["status"] = "WAITING_FOR_REVIEW"
                frame["activation"] = activation(frame, targets[tf], tf)
                frame.pop("confirmations")
                frames[tf] = frame
            minute = frames["15m"]
            ref = minute.get("reference")
            if ref and (not target or ref["at"] > target):
                ref = None
            levels = (project_levels(symbol, frames, price=ref["price"], price_at=ref["at"],
                                     asof=target, basis=minute["basis"])
                      if target and ref and ref["price_basis"] == "QFQ" and minute.get("basis") else None)
            rows.append(dict(symbol=symbol, name=info.get("name") or symbol, raw_tick=info.get("raw_tick"),
                             raw_15m_end=info.get("latest_completed15"),
                             sectors=keys, frames=frames, reference=ref, levels=levels))
        return rows

    def chart(self, symbol, timeframe, sessions, schedules, weeks, *, anchor_event=None, event_id=None):
        schedule = schedules[timeframe]
        target = schedules["15m"][-1] if schedules["15m"] else None
        info = self.market.instrument(symbol)
        factors, basis = self.reference_basis(symbol, info, target)
        if not schedule or not info:
            reason = "METADATA_PENDING" if schedule else "WAITING_FOR_COMPLETED_TARGET"
            detail = chart_diagnostic(self.market, self.daily, symbol, timeframe, schedules, weeks, {"status": reason})
            return dict(symbol=symbol, timeframe=timeframe, bars=[], events=[], status=reason, basis=basis,
                        expected_end=detail["target"], diagnostic=detail)
        end = schedule[-1]
        if anchor_event and anchor_event < end:
            pos = bisect_left(schedule, anchor_event)
            end = schedule[min(len(schedule) - 1, pos + CHART_LIMIT // 3)]
        end_index = bisect_right(schedule, end)
        grid = schedule[max(0, end_index - CHART_LIMIT - max(MAS) + 1):end_index]
        visible = grid[-CHART_LIMIT:]
        start = grid[0]
        if timeframe in INTRADAY:
            raw = self.market.raw_rows(symbol, end, start=str(wall(start) - timedelta(hours=2)), limit=-1)
        else:
            raw = self.daily.raw_rows(symbol, end, start=str(wall(start) - timedelta(days=7)))
        halt = self.market.meta("verified_trading_halts", {}).get(symbol)
        if halt:
            raw = [bar for bar in raw if not in_verified_halt(halt, bar["timestamp"])]
        aligned = set(grid)
        bars = [bar for bar in frame_bars(raw, timeframe, sessions, weeks, end, factors, target)
                if bar["timestamp"] in aligned]
        for bar in bars:
            bar["valid"] = bar["valid"] and bar["volume"] > 0
        bars = [bar for bar in with_averages(bars, grid, MAS) if bar["timestamp"] >= visible[0]]
        saved = self.saved(symbol).get(timeframe)
        frame = json.loads(saved["payload"]) if saved else {}
        current = frame if frame.get("expected_end") == schedule[-1] else {}
        confirmed = confirmations(frame) or {}
        invalidated = set(confirmed.get("invalidated", ()))
        reviewing = {item["id"] for item in confirmed["events"]} if confirmed.get("verified") is False else set()
        events = []
        if bars:
            events = [dict(json.loads(row["payload"]), mode=row["mode"], at=row["confirmed_at"])
                      for row in self.conn.execute(
                          "SELECT * FROM ma55_observation_event_v1 WHERE rule_version=? AND symbol=? "
                          "AND timeframe=? AND confirmed_at BETWEEN ? AND ? ORDER BY confirmed_at",
                          (VERSION, symbol, timeframe, bars[0]["timestamp"], end),
                      ) if row["event_id"] not in invalidated and row["event_id"] not in reviewing]
        located = any(event["event_id"] == event_id and event["at"] == anchor_event for event in events)
        return dict(symbol=symbol, timeframe=timeframe, name=info["name"], tick=info["raw_tick"],
                    bars=bars, events=events, status=current.get("status", "WAITING_INPUT"), basis=basis,
                    expected_end=schedule[-1], asof=bars[-1]["timestamp"] if bars else None, grid=visible,
                    event_verified=located if event_id else None, event_invalidated=event_id in invalidated,
                    diagnostic=chart_diagnostic(self.market, self.daily, symbol, timeframe, schedules, weeks, current))
