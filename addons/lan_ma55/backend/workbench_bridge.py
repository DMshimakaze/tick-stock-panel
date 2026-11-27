"""Versioned Page SDK command and receipt projections serviced by the sole owner."""

import json
import re
from time import time, time_ns
from uuid import UUID

from .focus_pool import SYMBOL
from .market_grid import CAPACITY, wall
from .market_store import encoded
from .multiframe_core.multiframe import TIMEFRAMES
from .native15_owner import candidates_page
from .result_publisher import (
    COMMAND_ID,
    COMMAND_LEASE_MS,
    COMMAND_SLOTS,
    RECEIPT_ID,
    WORKBENCH_ID,
)
from .store import read_page

SLOT = re.compile(r"(edit|chart|history|members)-(\d{1,2})")


def _identity(value):
    try:
        return str(UUID(value, version=4)) == value
    except (TypeError, AttributeError, ValueError):
        return False


def focus_view(focus, market_day):
    return dict(focus.view(market_day), asof_us=time_ns() // 1000)


class WorkbenchBridge:
    def __init__(self, owner, focus, observations, publisher):
        self.owner, self.focus, self.observations = owner, focus, observations
        self.publisher = publisher
        self.published = {}
        self.previews = {}
        self.last_view = None

    def publish(self, payload):
        current = encoded(payload)
        if self.last_view != current:
            self.publisher.ingest(WORKBENCH_ID, [dict(scope="lan.ma55", payload_json=current)])
            self.last_view = current

    def consume(self, market_day, sessions, schedules, weeks):
        rows = self.publisher.rows(COMMAND_ID, 4 * COMMAND_SLOTS)
        received = int(time() * 1000)
        replies = []
        ordered = sorted(rows, key=lambda r: not str(r.get("scope")).startswith("edit-"))
        for row in ordered:
            scope = row.get("scope")
            slot = SLOT.fullmatch(scope or "")
            request_ms = row.get("request_ms")
            if (not slot or not 0 <= int(slot[2]) < COMMAND_SLOTS or
                    type(request_ms) is not int or not 0 <= received - request_ms < COMMAND_LEASE_MS or
                    not _identity(row.get("client_id")) or not _identity(row.get("request_id"))):
                continue
            identity = (row["client_id"], row["request_id"])
            if self.published.get(scope) == identity:
                continue
            kind = slot[1]
            prior = self.owner.meta(f"focus_command_receipt.{scope}") if kind == "edit" else None
            if prior and (prior["client_id"], prior["request_id"]) == identity:
                replies.append(prior)
                continue
            try:
                args = json.loads(row["payload_json"])
                if not isinstance(args, dict):
                    raise ValueError("INVALID_COMMAND")
                if kind == "edit":
                    action = args.get("action")
                    if action not in ("save", "refresh", "preview", "estimate"):
                        raise ValueError("INVALID_COMMAND")
                    migration = {key: args[key] for key in ("migration", "confirm_migration") if key in args}
                    preview = self.previews.get(scope)
                    confirmation = (preview[2] if preview and preview[:2] ==
                                    (identity[0], args.get("preview_id")) else None)
                    result = self.focus.edit(action, args.get("expected_revision"), args.get("run"), market_day,
                                             selection=args.get("selection"), preferences=args.get("preferences"),
                                             confirmation=confirmation, **migration)
                    updated = result.pop("_confirmation", None)
                    if updated is not None:
                        self.previews[scope] = (identity[0], identity[1], updated)
                        result.get("preview", result)["preview_id"] = identity[1]
                    if (action in ("save", "refresh") and result["ok"]) or result.get("error") == "SCOPE_CONFLICT":
                        result["focus"] = focus_view(self.focus, market_day)
                elif kind == "members":
                    result = self.focus.lookup(args, market_day)
                elif kind == "history":
                    offset = args.get("offset", 0)
                    if type(offset) is not int or offset < 0:
                        raise ValueError("INVALID_HISTORY_PAGE")
                    result = read_page(self.owner.market.path, "events", limit=50, offset=offset)
                    names = self.owner.archive_market.universe({row["symbol"] for row in result["rows"]})
                    for row in result["rows"]:
                        row["name"] = names.get(row["symbol"], {}).get("name")
                else:
                    result = self.chart(args, sessions, schedules, weeks)
            except (KeyError, TypeError, ValueError):
                result = dict(ok=False, error="INVALID_COMMAND")
            reply = dict(scope=scope, client_id=identity[0], request_id=identity[1],
                         request_ms=request_ms, payload_json=encoded(result))
            if kind == "edit":
                with self.owner.conn:
                    self.owner.set_meta(f"focus_command_receipt.{scope}", reply)
            replies.append(reply)
        if replies:
            self.publisher.ingest(RECEIPT_ID, replies)
            self.published.update({r["scope"]: (r["client_id"], r["request_id"]) for r in replies})

    def chart(self, args, sessions, schedules, weeks):
        symbol, timeframe = args.get("symbol"), args.get("timeframe")
        source = args.get("source", "focus")
        if not isinstance(symbol, str) or not SYMBOL.fullmatch(symbol) or timeframe not in TIMEFRAMES:
            raise ValueError("INVALID_CHART")
        anchor = str(wall(args["anchor"])) if args.get("anchor") else None
        if source == "focus":
            if symbol not in self.focus.snapshot["members"]:
                return dict(symbol=symbol, timeframe=timeframe, bars=[], events=[], status="OUTSIDE_FOCUS")
            return self.observations.chart(symbol, timeframe, sessions, schedules, weeks,
                                           anchor_event=anchor, event_id=args.get("eventId"))
        if source not in ("archive", "native") or timeframe not in CAPACITY:
            return dict(symbol=symbol, timeframe=timeframe, bars=[], events=[], status="LEGACY_PERIOD_UNAVAILABLE")
        store = self.owner.archive_market if source == "archive" else self.owner.market
        target = schedules["15m"][-1] if schedules["15m"] else None
        if anchor and target:
            # The old event clock remains the anchor even when the default window
            # has rolled far past the frozen confirmation.
            target = min(anchor, target)
        if not target:
            return dict(symbol=symbol, timeframe=timeframe, bars=[], events=[], status="WAITING_INPUT")
        series = store.read(symbol, timeframe, target, limit=250, connection=self.owner.conn, chart=True)
        bars = series["bars"]
        kwargs = dict(symbol=symbol, start=bars[0]["timestamp"], end=bars[-1]["timestamp"]) if bars else None
        events = []
        if kwargs and timeframe == "15m":
            events = (read_page(store.path, "events", limit=200, **kwargs)["rows"] if source == "archive"
                      else candidates_page(store.path, limit=200, **kwargs)["rows"])
            events = [dict(event, at=event["confirmed_at"]) for event in events]
        return dict(symbol=symbol, timeframe=timeframe, name=series.get("name"), bars=bars,
                    events=events, status=series.get("state"), expected_end=target,
                    asof=bars[-1]["timestamp"] if bars else None, tick=series.get("raw_tick"),
                    basis=series.get("price_basis"), source=source)
