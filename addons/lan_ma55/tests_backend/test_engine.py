from copy import deepcopy
import json
import os
import subprocess
import sys

import pytest

from addons.lan_ma55.backend.engine import advance, minimal_features
from addons.lan_ma55.backend.pipeline import process_symbol, serializable
from addons.lan_ma55.tests.oracle import POSITIVE, SUPPRESSED, NEGATIVE
from .conftest import PLUGIN


def assert_same(actual, expected):
    if isinstance(expected, dict):
        assert actual.keys() == expected.keys()
        for key in expected:
            assert_same(actual[key], expected[key])
    elif isinstance(expected, list):
        assert len(actual) == len(expected)
        for a, b in zip(actual, expected):
            assert_same(a, b)
    elif type(expected) in (float, int) and type(actual) in (float, int):
        assert actual == pytest.approx(expected, rel=2e-12, abs=2e-12)
    else:
        assert actual == expected


def test_every_synthetic_step_matches_actual_pinned_python(scenarios):
    if not os.environ.get("MA55_REFERENCE_REPO"):
        pytest.skip("Pinned reference repository not mounted")
    cases = [
        dict(name=name, bars=case["bars"], tick=case["tick"])
        for name, case in scenarios.items()
    ]
    result = subprocess.run(
        [sys.executable, "-B", "-X", "utf8", str(PLUGIN / "tests" / "oracle.py")],
        input=json.dumps(dict(action="synthetic", cases=cases)),
        capture_output=True,
        text=True,
        check=True,
    )
    for c in json.loads(result.stdout):
        state, history, slots = None, [], {}
        for bar, expected in zip(scenarios[c["name"]]["bars"], c["trace"]):
            slot = bar["timestamp"][11:16]
            feature = minimal_features(history, bar, slots.get(slot, []))
            state, decision = advance(state, feature)
            assert_same(
                serializable(dict(structure=state, decision=decision)), expected
            )
            history = (history + [bar])[-55:]
            slots[slot] = (slots.get(slot, []) + [bar["volume"]])[-5:]


CASES = [
    (kind, symbol[2:] + "." + symbol[:2].upper(), when + ":00")
    for kind, rows in (
        ("positive", POSITIVE),
        ("suppressed", SUPPRESSED),
        ("negative", NEGATIVE),
    )
    for symbol, times in rows.items()
    for when in times
]


@pytest.mark.parametrize("kind,symbol,when", CASES)
def test_private_windows_against_original_analyze(frozen_cases, kind, symbol, when):
    case = next(
        c
        for c in frozen_cases
        if (c["kind"], c["symbol"], c["when"]) == (kind, symbol, when)
    )
    bars = [
        dict(b, source_count=3, adjust_factor=case["factors"][b["timestamp"][:10]])
        for b in case["bars"]
    ]
    record, _ = process_symbol(
        symbol,
        bars,
        sessions=case["sessions"],
        raw_tick=case["tick"],
        source_asof=when,
        first_seen_at="2026-11-07 20:00:00",
    )
    assert_same(record["state"]["decision"], case["decision"])
    assert_same(record["state"]["structure"], case["structure"])
    assert [
        {k: e[k] for k in ("event_id", "confirmed_at", "setup_at", "pattern")}
        for e in record["confirmations"]
    ] == case["events"]
    if kind == "positive":
        assert record["state"]["decision"]["event"] == "BUY_STRUCTURE"
    elif kind == "suppressed":
        assert record["state"]["decision"]["event"] == ""
    else:
        assert not [
            e for e in record["confirmations"] if e["confirmed_at"][:10] == when[:10]
        ]


def test_completed_cutoff_gaps_factors_and_restart(scenarios):
    c = scenarios["departure"]
    arguments = dict(
        sessions=c["sessions"],
        raw_tick=0.01,
        source_asof=c["bars"][-1]["timestamp"],
        first_seen_at="2026-11-07 20:00:00",
    )
    full, events = process_symbol("600000.SH", c["bars"], **arguments)
    saved = None
    for n in range(1, len(c["bars"]) + 1):
        saved, _ = process_symbol(
            "600000.SH", c["bars"][max(0, n - 64) : n], previous=saved, **arguments
        )
        saved = json.loads(json.dumps(saved, allow_nan=False))
    assert saved == full
    same, again = process_symbol("600000.SH", c["bars"], previous=saved, **arguments)
    assert again == [] and same == full and len(events) == 2
    assert len(full["state"]["feature_history"]) == 55
    assert all(len(v) <= 5 for v in full["state"]["slot_volumes"].values())
    future = dict(c["bars"][-1], timestamp="2026-11-06 13:45:00")
    before, events = process_symbol(
        "600000.SH",
        [future],
        sessions=["2026-11-06"],
        raw_tick=0.01,
        source_asof="2026-11-06 13:33:00",
        first_seen_at="2026-11-06 13:33:00",
    )
    assert before["state"]["processed_at"] is None and not events
    for change in (
        dict(adjust_factor=None),
        dict(adjust_factor=0.5),
        dict(open=None),
        dict(source_count=2),
    ):
        record, _ = process_symbol(
            "600000.SH",
            [dict(c["bars"][-1], **change)],
            previous=process_symbol("600000.SH", c["bars"][:-1], **arguments)[0],
            **arguments,
        )
        assert record["state"]["valid_run"] == 0
    gap = deepcopy(c["bars"])
    gap.pop(-2)
    record, _ = process_symbol("600000.SH", gap, **arguments)
    assert record["state"]["reason"] == "MISSING_COMPLETED_15M"


def test_tick_and_discovery_modes_are_explicit(scenarios):
    case = scenarios["single"]
    for seen, origin, expected in (
        ("09:46:00", "LIVE", "LIVE"),
        ("10:00:00", "LIVE", "DELAYED"),
        ("10:00:00", "REPLAY", "REPLAY"),
    ):
        end = case["bars"][-1]["timestamp"]
        record, _ = process_symbol(
            "600000.SH",
            case["bars"],
            sessions=case["sessions"],
            raw_tick=0.01,
            source_asof=end,
            first_seen_at=end[:10] + " " + seen,
            origin=origin,
            live_after=end[:10] + " 08:00:00",
        )
        assert record["confirmations"][-1]["mode"] == expected
