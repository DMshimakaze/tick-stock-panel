"""Synthetic fault probes and native-15m comparison, separate from BUY logic.

These are offline admission checks, not replacements for any production parser.
"""

from datetime import datetime, timedelta
import math


def coverage(requested, response):
    return {symbol: "UNVERIFIED" if response.get(symbol) is None else
            "HAS_EVENTS" if response[symbol] else "CONFIRMED_NO_EVENTS"
            for symbol in requested}


def compare_native(derived, native, *, label, volume_unit):
    """Canonical timestamps / raw OHLC / shares / yuan; labels are explicit.

    A caller must establish timestamp convention and units from source evidence.
    An equal synthetic comparison does not establish account or BUY equivalence.
    """
    if label not in ("start", "end") or volume_unit not in ("lots", "shares"):
        raise ValueError("native timestamp label and volume unit must be explicit")
    expected = {r["timestamp"]: r for r in derived}
    actual = {}
    for row in native:
        value = dict(row)
        stamp = datetime.fromisoformat(row["timestamp"])
        if label == "start":
            stamp += timedelta(minutes=15)
        value["timestamp"] = str(stamp)
        value["volume"] *= 100 if volume_unit == "lots" else 1
        actual[str(stamp)] = value
    fields = ("open", "high", "low", "close", "volume", "amount")
    max_error = dict.fromkeys(fields, 0.0)
    differences = 0
    for timestamp in expected.keys() & actual.keys():
        different = False
        for field in fields:
            a, b = expected[timestamp].get(field), actual[timestamp].get(field)
            if a is None or b is None:
                different |= a != b
            else:
                max_error[field] = max(max_error[field], abs(a - b))
                different |= not math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-10)
        differences += different
    missing, extra = len(expected.keys() - actual.keys()), len(actual.keys() - expected.keys())
    count = differences + missing + extra
    return dict(status="PASS_COMPARISON_ONLY" if count == 0 else "FAIL",
                mismatched_bars=count, missing=missing, extra=extra,
                max_absolute_error=max_error, provenance="QUANTDASH_NATIVE_15M",
                input_equivalence="NOT_VERIFIED", buy_equivalence="NOT_VERIFIED")


def aggregate_native(bars, minutes, asof):
    """Experimental native-15m chart buckets; never fabricate 5m provenance."""
    if minutes not in (30, 60):
        raise ValueError("native chart comparison supports 30/60 minutes")
    lookup = {r["timestamp"]: r for r in bars if r["timestamp"] <= asof}
    result = []
    for day in sorted({t[:10] for t in lookup}):
        for hour in (9.5, 13):
            start = datetime.fromisoformat(day) + timedelta(hours=hour)
            for offset in range(minutes, 121, minutes):
                end = start + timedelta(minutes=offset)
                required = [str(end - timedelta(minutes=15 * i)) for i in reversed(range(minutes // 15))]
                if str(end) > asof or not all(t in lookup for t in required):
                    continue
                parts = [lookup[t] for t in required]
                result.append(dict(timestamp=str(end), open=parts[0]["open"],
                    high=max(r["high"] for r in parts), low=min(r["low"] for r in parts),
                    close=parts[-1]["close"], volume=sum(r["volume"] for r in parts),
                    amount=sum(r["amount"] for r in parts) if all(r.get("amount") is not None for r in parts) else None,
                    native_source_count=minutes // 15, source_period="15m",
                    source="QUANTDASH_NATIVE_15M", complete=True))
    return result


def admit_window(expected, received, *, cutoff, factor_response, session_day,
                 listing_day="1900-01-01", suspended=False, corrected=False):
    """Admission state for the synthetic replay, preserving genuine empty states."""
    if factor_response is None:
        return "FACTOR_PENDING"
    if listing_day > session_day:
        return "NOT_YET_LISTED"
    if any(t > cutoff or t[:10] != session_day for t in received):
        return "INVALID_TIME"
    if corrected:
        return "REBUILD_PENDING"
    missing = set(expected) - set(received)
    if missing:
        return "SUSPENDED" if suspended and not received else "DATA_PENDING"
    return "COMPLETE"


def fault_contracts():
    """Executable admission probes; FAIL denotes feature unavailable, not a test failure."""
    day = "2026-11-09"
    expected = [day + " " + t for t in ("09:35:00", "09:40:00", "09:45:00")]
    args = dict(cutoff=expected[-1], factor_response=[], session_day=day)
    cases = {}

    def record(name, observed, expected_state, feature="FAIL"):
        cases[name] = dict(observed=observed, expected=expected_state,
                           check_passed=observed == expected_state, feature_status=feature,
                           evidence="SYNTHETIC_ADMISSION_PROBE")

    record("missing_symbol_200", coverage(["good", "missing"], {"good": []})["missing"], "UNVERIFIED")
    record("factor_unverified", admit_window(expected, expected,
           **dict(args, factor_response=None)), "FACTOR_PENDING")
    record("suspension", admit_window(expected, [], **args, suspended=True), "SUSPENDED", "EXPECTED_NO_BAR")
    record("late_bar", admit_window(expected, expected[:-1], **args), "DATA_PENDING")
    record("new_listing", admit_window(expected, [], **args, listing_day="2026-11-10"), "NOT_YET_LISTED", "EXPECTED_EXCLUSION")
    record("lunch_gap", admit_window([day + " 13:05:00"], [day + " 11:35:00"],
           **dict(args, cutoff=day + " 13:05:00")), "DATA_PENDING")
    record("unclosed_bar", admit_window(expected, expected,
           **dict(args, cutoff=day + " 09:44:59")), "INVALID_TIME")
    record("single_1m_gap", admit_window([day + " 09:31:00", day + " 09:32:00"],
           [day + " 09:32:00"], **args), "DATA_PENDING")
    record("historical_correction", admit_window(expected, expected, **args, corrected=True), "REBUILD_PENDING")
    record("cross_day_cache", admit_window(expected, ["2026-11-06 15:00:00"], **args), "INVALID_TIME")
    # Display date must come from the source row, independent of cache/file mtime.
    source_date, display_date = "2026-11-06", day
    record("stale_frontend", "STALE_DISPLAY" if source_date != display_date else "CURRENT", "STALE_DISPLAY")
    bar = dict(timestamp=expected[-1], open=10, high=12, low=9, close=11, volume=300, amount=300000)
    native = dict(bar, timestamp=day + " 09:30:00", volume=3)
    record("timestamp_start_end", compare_native([bar], [native], label="start", volume_unit="lots")["mismatched_bars"], 0,
           "EQUAL_SYNTHETIC_ONLY")
    record("price_mismatch", compare_native([bar], [dict(bar, close=11.1)], label="end", volume_unit="shares")["status"], "FAIL")
    return cases
