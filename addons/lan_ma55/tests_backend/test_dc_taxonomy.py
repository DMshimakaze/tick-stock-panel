from threading import Event
from time import time
from uuid import uuid4

import httpx
import pytest

from addons.lan_ma55.backend.dc_focus_owner import DCFocusOwner
from addons.lan_ma55.backend.dc_store import DCStore
from addons.lan_ma55.backend.dc_taxonomy import (
    DCStructureError, decode_catalog, decode_members, derive_industry,
)
from addons.lan_ma55.backend.dc_transport import DCTransport, DCUnavailable
from addons.lan_ma55.backend.focus_pool import FocusPool
from addons.lan_ma55.backend.native15_owner import NativeOwner


DAY = "20261124"
RUN = "dc-fixture"
A, B, C = "600000.SH", "000001.SZ", "920179.BJ"
ROOT, MID, LEAF = (f"dc:industry:BK000{n}.DC" for n in (1, 2, 3))
ROOT2, MID2, LEAF2 = (f"dc:industry:BK000{n}.DC" for n in (4, 5, 6))
CONCEPT = "dc:concept:BK0007.DC"
THEME = "dc:theme:000008.DC"
LEGACY = "industry:881001.TI"


def inventory():
    nodes = {
        key: dict(name=key, kind="industry", level=level, source_level=str(level))
        for chain in ((ROOT, MID, LEAF), (ROOT2, MID2, LEAF2))
        for level, key in enumerate(chain, 1)
    }
    nodes[CONCEPT] = dict(name="概念", kind="concept", level=None, source_level=None)
    nodes[THEME] = dict(name="题材", kind="theme", level=None, source_level=None)
    members = {key: {A: ("甲", None), B: ("乙", None)} for key in (ROOT, MID, LEAF)}
    members.update({key: {C: ("丙", None)} for key in (ROOT2, MID2, LEAF2)})
    members[CONCEPT] = {B: ("乙", None)}
    members[THEME] = {A: ("甲", "解释")}
    return nodes, members


def publish(owner, run=RUN, day=DAY):
    store = DCStore(owner.conn)
    nodes, members = inventory()
    store.begin(run, day, dict(stocks=[A, B, C], excluded=[]), 100)
    store.accept_catalog(run, nodes)
    staging = store.generation("STAGING")
    store.accept_members(staging, members, 200)
    store.publish(staging)
    return store


@pytest.mark.parametrize("case,message", [
    ("overlap", "DC_OVERLAPPING_INDUSTRY"),
    ("orphan", "DC_INDUSTRY_PARENT_UNRESOLVED"),
    ("empty", "DC_EMPTY_OR_PENDING_INDUSTRY"),
    ("missing", "DC_MISSING_INDUSTRY_LEVEL_MEMBERSHIP"),
])
def test_industry_rejects_non_unique_or_incomplete_tree(case, message):
    nodes, members = inventory()
    if case == "overlap":
        members[LEAF2][A] = ("甲", None)
    if case == "orphan":
        members[LEAF][C] = members[LEAF2].pop(C)
        members[LEAF2][B] = members[LEAF].pop(B)
    if case == "empty":
        members[LEAF] = {}
    if case == "missing":
        members[LEAF].pop(A)
    with pytest.raises(DCStructureError, match=message):
        derive_industry(nodes, members, [A, B, C])


def test_independent_member_date_scope_and_empty_contract():
    fields = ["trade_date", "theme_code", "ts_code", "name", "reason"]
    item = [DAY, "000008.DC", A, "甲", None]
    data = dict(fields=fields, items=[item])
    assert decode_members("theme", [THEME], DAY, data) == {THEME: {A: ("甲", None)}}
    assert decode_members("theme", [THEME], DAY, dict(fields=[], items=[])) == {THEME: {}}
    with pytest.raises(DCStructureError, match="DC_MEMBER_PAGE_LIMIT"):
        decode_members("theme", [THEME], DAY, data, limit=1)
    with pytest.raises(DCStructureError, match="DC_UNEXPECTED_MEMBER"):
        decode_members("theme", [THEME], "20261123", data)
    with pytest.raises(DCStructureError, match="DC_DUPLICATE_MEMBER"):
        decode_members("theme", [THEME], DAY, dict(fields=fields, items=[item, item]))
    with pytest.raises(DCStructureError, match="DC_MISSING_BATCH_GROUP"):
        decode_members("theme", [THEME, "dc:theme:000009.DC"], DAY, data)


@pytest.mark.parametrize("st", [401, 403, 429, "timeout"])
def test_transport_records_the_failure_and_only_the_configured_destination(st):
    calls = []

    def request(req):
        calls.append(req)
        assert req.url.host == "example.test"
        if st == "timeout":
            raise httpx.ReadTimeout("timeout")
        return httpx.Response(st, headers={"Retry-After": "80"})

    transport = DCTransport("fixture-token", "https://example.test/api", Event(),
                            transport=httpx.MockTransport(request))
    try:
        with pytest.raises(DCUnavailable):
            transport.request("dc_member", {"trade_date": DAY}, "")
        receipt = transport.last_receipt
        assert receipt["source_ms"] is None and receipt["error"].startswith("DC_")
        assert "fixture-token" not in str(receipt)
        if st in (401, 403):
            with pytest.raises(DCUnavailable, match="DC_AUTHORIZATION"):
                transport.request("dc_index", {"trade_date": DAY}, "")
        assert len(calls) == 1
    finally:
        transport.close()


def test_legacy_preview_requires_explicit_confirmation_and_leaves_legacy_read_only(tmp_path):
    path = tmp_path / "state.sqlite3"
    with NativeOwner(path) as owner:
        publish(owner)
        old = FocusPool(path)
        old.apply([LEGACY], [dict(key=LEGACY, name="旧板块", symbols=[A, C],
                                 source_ms=100, observed_ms=200)], expected_revision=0)
        original = old.read()
        focus = DCFocusOwner(owner, None, None, Event())
        try:
            assert focus.migration_pending and not focus.snapshot["members"]
            assert focus.edit("save", 0, RUN, DAY, selection=[THEME])["error"] == "DC_MIGRATION_CONFIRM"
            assert focus.edit("save", 0, RUN, DAY, selection=[LEGACY])["error"] == "INVALID_SELECTION"
            prefs = {"groups": [dict(id=str(uuid4()), name="对照组", boards=[THEME])],
                     "aliases": {THEME: ["例"]}}
            delta = focus.edit("preview", 0, RUN, DAY, selection=[THEME, CONCEPT],
                               migration={LEGACY: THEME}, preferences=prefs)
            assert (delta["added"], delta["removed"], delta["retained"], delta["total"]) == (1, 1, 1, 2)
            row, = delta["migration"]
            assert (row["common"], row["old_only"], row["new_only"]) == (1, 1, 0)
            saved = focus.edit("save", 0, RUN, DAY, selection=[THEME, CONCEPT, THEME],
                               migration={LEGACY: THEME}, confirm_migration=True, preferences=prefs,
                               confirmation=delta["_confirmation"])
            assert saved["ok"] and saved["total"] == 2 and old.read() == original
            assert focus.lookup(dict(run=RUN, board=THEME))["rows"] == [dict(symbol=A, name="甲", reason="解释")]
            reverse = focus.lookup(dict(run=RUN, symbol=A))
            assert reverse["industry_path"] == [ROOT, MID, LEAF]
            assert reverse["name"] == "甲"
            assert focus.edit("save", 0, "old", DAY, selection=[])["error"] == "SCOPE_CONFLICT"
            pause = focus.edit("preview", 1, "old", DAY, selection=[])
            assert focus.edit("save", 1, RUN, DAY, selection=[], confirmation=pause["_confirmation"])["ok"]
        finally:
            focus.source.close()
        restarted = DCFocusOwner(owner, None, None, Event())
        try:
            assert not restarted.snapshot["members"] and restarted.snapshot["groups"] == prefs["groups"]
            assert restarted.snapshot["aliases"] == prefs["aliases"] and old.read() == original
            restarted.taxonomy.target_day = "20261210"
            assert all(x["status"] in ("REVIEW_PENDING", "VERIFIED_EMPTY") for x in restarted.view(DAY)["catalog"])
            preview = restarted.edit("preview", 2, RUN, DAY, selection=[THEME])
            assert restarted.edit("save", 2, RUN, DAY, selection=[THEME],
                                  confirmation=preview["_confirmation"])["added"] == 1
        finally:
            restarted.source.close()


def test_pending_failure_retains_generation_and_new_generation_includes_new_directory(tmp_path):
    path = tmp_path / "state.sqlite3"
    with NativeOwner(path) as owner:
        store = publish(owner)
        nodes, members = inventory()
        new = "dc:concept:BK0020.DC"
        nodes[new] = dict(name="新增", kind="concept", level=None, source_level=None)
        data = dict(fields=["ts_code", "trade_date", "name", "idx_type", "level"],
                    items=[["BK0020.DC", "20261125", "新增", "概念板块", None]])
        assert new in decode_catalog("concept", "20261125", data)
        store.begin("next", "20261125", dict(stocks=[A, B, C], excluded=[]), 300)
        store.accept_catalog("next", nodes)
        stage = store.generation("STAGING")
        store.accept_members(stage, members, 400)
        with pytest.raises(DCStructureError, match="DC_GENERATION_INCOMPLETE"):
            store.publish(stage)
        assert store.generation("ACTIVE")["run"] == RUN
        store.accept_members(stage, {new: {}}, 400)
        store.publish(stage)
        assert store.generation("PREVIOUS")["run"] == RUN
        assert store.board_data("next")[new]["count"] == 0


def test_focus_preview_counts_and_config_conflicts_are_separate_from_snapshots(tmp_path):
    with NativeOwner(tmp_path / "state.sqlite3") as owner:
        publish(owner)
        focus = DCFocusOwner(owner, None, None, Event())
        try:
            focus.apply([THEME], [focus.board_snapshot(THEME)])
            config = focus.snapshot["config_revision"]
            choice = [THEME, CONCEPT, THEME]
            preview = focus.edit("preview", config, RUN, DAY, selection=choice)
            assert (preview["total_memberships"], preview["unique_symbols"]) == (2, 2)
            previous = focus.snapshot["revision"]
            theme = dict(focus.board_snapshot(THEME), observed_ms=500)
            focus.apply([THEME], [theme])
            assert focus.snapshot["revision"] > previous and focus.snapshot["config_revision"] == config
            assert focus.edit("save", config, "prior-run", DAY, selection=choice,
                              confirmation=preview["_confirmation"])["ok"]
            assert focus.edit("preview", config, RUN, DAY, selection=[THEME])["error"] == "SCOPE_CONFLICT"
            config = focus.snapshot["config_revision"]
            preview = focus.edit("preview", config, RUN, DAY, selection=[THEME])
            focus.taxonomy.target_day = "20261125"
            focus.taxonomy.record_daily(dict(day="20261125", checked={THEME: dict(
                run=RUN, day="20261125", observed_ms=600, rows={A: ("甲", None), B: ("乙", None)},
            )}))
            different = focus.edit("save", config, RUN, DAY, selection=[THEME], confirmation=preview["_confirmation"])
            assert different["error"] == "FOCUS_PREVIEW_CHANGED"
            assert different["preview"]["unique_symbols"] == 2
            assert focus.edit("save", config, RUN, DAY, selection=[THEME],
                              confirmation=different["_confirmation"])["ok"]
            assert focus.view(DAY)["unique_symbols"] == 2
            assert focus.edit("preview", focus.snapshot["config_revision"], RUN, DAY,
                              selection=["dc:theme:123456.DC"])["unavailable"] == ["dc:theme:123456.DC"]
        finally:
            focus.source.close()


def test_new_daily_source_code_queues_full_recheck_without_overwriting_the_membership(tmp_path, monkeypatch):
    day = "20261125"
    outside = "920305.BJ"

    def request(req):
        return httpx.Response(200, json={"code": 0, "data": {
            "fields": ["trade_date", "theme_code", "ts_code", "name"],
            "items": [[day, "000008.DC", A, "甲"], [day, "000008.DC", outside, "新代码"]],
        }})

    with NativeOwner(tmp_path / "state.sqlite3") as owner:
        publish(owner)
        focus = DCFocusOwner(owner, "test", "https://example.test/api", Event(),
                             transport=httpx.MockTransport(request))
        try:
            nodes, _ = inventory()
            monkeypatch.setattr(focus.taxonomy, "latest_catalog", lambda _: nodes)
            focus.taxonomy.record_daily(dict(day=day, scope_checked=True, checked={}))
            focus.taxonomy.target_day = day
            focus.taxonomy.review_focus(day, [THEME])
            assert focus.taxonomy.status()["queued"]
            assert focus.view(day)["sector_errors"][THEME] == "DC_UNIVERSE_CHANGED"
            assert focus.lookup(dict(run=RUN, board=THEME))["total"] == 1
        finally:
            focus.source.close()


def test_rate_limit_and_timeout_keep_a_resumable_generation(tmp_path):
    attempts = []
    responses = iter([429, "timeout", 200])
    with NativeOwner(tmp_path / "state.sqlite3") as owner:
        store = publish(owner)
        store.begin("pending", DAY, dict(stocks=[A, B, C], excluded=[]), 300)
        def request(req):
            attempts.append(req.url.path)
            response = next(responses)
            if response == "timeout":
                raise httpx.ReadTimeout("read")
            return httpx.Response(response, headers={"Retry-After": "80"}, json={"code": 0, "data": {
                "fields": ["ts_code", "trade_date", "name", "idx_type", "level"],
                "items": [["BK0001.DC", DAY, "行业", "行业板块", "东财一级行业"]],
            }})
        focus = DCFocusOwner(owner, "test", "https://example.test/api", Event(),
                             transport=httpx.MockTransport(request))
        try:
            recent = ["2020-11-24"]
            for expected in ("DC_RATE_LIMIT", "DC_TIMEOUT"):
                focus.taxonomy.step(recent, None, [])
                assert focus.taxonomy.state["error"] == expected
                assert focus.taxonomy.state["retry_at"] > time()
                focus.taxonomy.step(recent, None, [])
                assert focus.taxonomy.staging["run"] == "pending"
                assert focus.run == RUN
                focus.taxonomy.record(retry_at=0)
                if expected == "DC_RATE_LIMIT":
                    assert focus.source.budget.delay() > 70
                    expiry = focus.source.budget.cooldown
                    focus.source.budget.clock = lambda: expiry + 1
            focus.taxonomy.step(recent, None, [])
            assert store.progress("pending") == {"board_total": 1, "board_ready": 0}
            assert len(attempts) == 3
        finally:
            focus.source.close()
