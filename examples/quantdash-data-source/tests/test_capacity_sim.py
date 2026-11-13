"""Offline capacity and data-contract checks; never open production storage."""

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from capacity_sim.contracts import aggregate_native, compare_native, coverage, fault_contracts
from capacity_sim.model import Config, Request, RollingWindow, Simulator, at, session_points
from capacity_sim.scenarios import build, lower_bounds, replay, search


def test_rolling_window_is_not_an_aligned_minute_or_credit_bucket():
    quota = RollingWindow(2)
    quota.charge(59)
    quota.charge(60)
    assert quota.ready(61) == 119
    assert quota.ready(119) == 119
    quota.charge(119)
    assert quota.ready(119.9) == 120
    assert quota.peak == 2
    fractional = RollingWindow(1)
    fractional.charge(0.123456789)
    assert fractional.ready(60.123456789) == 60.123456789
    fractional.charge(60.123456789)
    assert fractional.peak == 1
    with pytest.raises(ValueError):
        RollingWindow(0)


def test_two_clients_need_a_shared_gate_and_do_not_get_early_credit():
    jobs = [Request(0, "host", "minute_batch", "probe", i, 1, period="1m")
            for i in range(20)]
    jobs += [Request(0, "marketdata", "minute_batch", "probe", i, 1, period="5m")
             for i in range(20)]
    independent = Simulator(Config(policy="independent", host_rpm=30, reserve=0,
                                   market_rpm=24, stop_s=120))
    independent.add(jobs)
    bad = independent.run()
    assert bad["requests"]["rejected_429"] > 0
    assert bad["peaks_60s"]["vendor_attempts"]["minute_shared"] > 30
    coordinated = Simulator(Config(policy="broker", host_rpm=30, reserve=0,
                                   market_rpm=24, stop_s=180))
    coordinated.add(jobs)
    good = coordinated.run()
    assert good["requests"]["rejected_429"] == 0
    assert good["peaks_60s"]["vendor_attempts"]["minute_shared"] <= 24
    assert good["requests"]["completed"] == 40


def test_current_static_six_plus_twenty_four_is_a_minute_ceiling_not_36():
    cfg = Config(policy="independent", stop_s=180)
    sim = Simulator(cfg)
    sim.add([Request(0, client, "minute_batch", "probe", i, 1, period=period)
             for client, period in (("host", "1m"), ("marketdata", "5m"))
             for i in range(30)])
    result = sim.run()
    assert result["peaks_60s"]["vendor_attempts"]["minute_shared"] <= 30
    assert result["peaks_60s"]["local"]["host_background"] <= 6


def test_account_coordinator_reserves_quotes_across_both_clients():
    result = replay("S3-broker", Config(universe=201, vendor_scope="account"))
    peaks = result["peaks_60s"]["coordinator"]
    assert peaks["account_all_http"] <= 24
    assert peaks["account_background"] <= 18
    assert result["slo"]["snapshot_60s"]
    assert result["requests"]["rejected_429"] == 0


@pytest.mark.parametrize("day,holiday", [("2026-11-08", False), ("2026-11-09", True)])
def test_closed_session_has_no_intraday_or_daily_requests(day, holiday):
    result = replay("S1", Config(day=day, holiday=holiday))
    assert result["requests"]["sent"] == 0
    assert session_points(5, day=day, holiday=holiday) == []


def test_lunch_and_finished_bars_are_exchange_anchored():
    points = session_points(15)
    assert len(points) == 16
    assert at("11:30") in points and at("13:15") in points
    assert not any(at("11:30") < p < at("13:15") for p in points)
    jobs = build("S2", Config()).jobs
    native = [j for j in jobs if j.stream == "ma55"]
    assert native
    assert all(j.ready_s >= j.target_s + 60 for j in native)
    assert all(j.target_s in points for j in native)


def test_host_publishes_whole_round_and_never_runs_overlapping_repair_rounds():
    result = replay("S1", Config(stop_s=at("10:30")))
    assert result["host_repair"]["publication"] == "ATOMIC_ROUND_FIXED_QUERY_END"
    assert result["host_repair"]["max_concurrent_rounds"] == 1
    assert result["host_repair"]["missed_cadence_slots"] > 0
    age = result["freshness_s"]["full_1m"]
    assert age["p95"] is None or age["p95"] > 300


def test_offline_run_is_reproducible_and_silent_after_stop(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = Config(universe=201, stop_s=at("10:10"), seed=37)
    first = replay("S3-broker", cfg)
    assert first == replay("S3-broker", cfg)
    assert first["last_http_s"] <= cfg.stop_s
    assert not list(tmp_path.iterdir())
    assert "TickFlow" not in json.dumps(first)


def test_live_http_payload_and_retained_window_upserts_are_different_costs():
    cfg = Config(universe=1, stop_s=100)
    sim = Simulator(cfg)
    sim.add([Request(0, "marketdata", "minute_batch", "ma55", 0, 1, period="5m")])
    sim.run()
    assert sim.counts["decoded_rows"] == 1
    assert sim.counts["upsert_bar_rows"] == 465
    assert sim.counts["estimated_write_bytes"] == 465 * cfg.disk_row_bytes


def test_retry_after_and_outage_are_counted_not_successful_empty_data():
    cfg = Config(universe=201, stop_s=at("10:50"), fault="429")
    result = replay("S3-broker", cfg)
    assert result["requests"]["rejected_429"] == 1
    assert result["retries"]["min_retry_after_s"] >= 31
    offline = replay("S3-broker", Config(universe=201, stop_s=at("11:00"),
                                          fault="outage", outage_minutes=10))
    assert offline["requests"]["network_timeouts"] > 0
    assert offline["requests"]["completed"] > 0
    assert offline["recovery_s"] is not None
    assert offline["requests"]["superseded"] > 0


def test_factor_omission_fails_pipeline_and_preserves_other_symbols():
    assert coverage(["A", "B", "C"], {"A": [], "B": None}) == {
        "A": "CONFIRMED_NO_EVENTS", "B": "UNVERIFIED", "C": "UNVERIFIED"}
    result = replay("S5", Config(universe=201))
    assert result["pipeline"]["status"] == "FAIL"
    assert result["pipeline"]["failed_stage"] == "factors"
    assert "enriched" not in result["pipeline"]["finished_stages"]


def test_native_comparison_reports_real_provenance_and_price_disagreement():
    bar = dict(timestamp="2026-11-09 09:45:00", open=10, high=12, low=9,
               close=11, volume=300, amount=300000)
    native = dict(bar, timestamp="2026-11-09 09:30:00", volume=3)
    saved = deepcopy(native)
    equal = compare_native([bar], [native], label="start", volume_unit="lots")
    assert equal["mismatched_bars"] == 0
    assert equal["provenance"] == "QUANTDASH_NATIVE_15M"
    assert native == saved
    native["close"] = 11.01
    assert compare_native([bar], [native], label="start", volume_unit="lots")["status"] == "FAIL"
    assert compare_native([bar], [], label="end", volume_unit="shares")["status"] == "FAIL"


def test_required_faults_are_executable_checks_with_honest_feature_status():
    cases = fault_contracts()
    expected = {"missing_symbol_200", "factor_unverified", "suspension", "late_bar",
                "new_listing", "lunch_gap", "timestamp_start_end", "unclosed_bar",
                "historical_correction", "cross_day_cache", "stale_frontend",
                "price_mismatch", "single_1m_gap"}
    assert expected <= cases.keys()
    assert all(c["check_passed"] for c in cases.values())
    assert cases["factor_unverified"]["feature_status"] == "FAIL"


def test_lower_bounds_count_batches_not_period_divided_batches():
    rows = lower_bounds()
    row = next(r for r in rows if r["universe"] == 5891)
    assert row["batches"] == 59
    assert row["minutes_at_6_rpm"] == pytest.approx(59 / 6)
    assert row["rpm_every_15m"] == pytest.approx(59 / 15)
    jobs = build("S1", Config()).jobs
    assert sum(j.stream == "source_snapshot" for j in jobs) == 1
    assert sum(j.stream == "calendar" for j in jobs) == 1
    post = [j for j in jobs if j.stage == "minute"]
    assert len(post) == 118 and sum(j.symbols for j in post) == 5891
    assert all(j.symbols <= 50 and j.bars == 1200 for j in post)


def test_native_chart_uses_two_or_four_real_15m_bars_and_never_crosses_lunch():
    times = ("10:45", "11:00", "11:15", "11:30", "13:15", "13:30")
    bars = [dict(timestamp="2026-11-09 " + t + ":00", open=10, high=12, low=9,
                 close=11, volume=100, amount=100000) for t in times]
    sixty = aggregate_native(bars, 60, "2026-11-09 13:30:00")
    assert len(sixty) == 1 and sixty[0]["timestamp"].endswith("11:30:00")
    assert sixty[0]["native_source_count"] == 4
    thirty = aggregate_native(bars, 30, "2026-11-09 13:30:00")
    assert thirty[-1]["native_source_count"] == 2
    assert "source_count" not in thirty[-1]
    assert not aggregate_native(bars, 60, "2026-11-09 11:29:59")


def test_cli_repeated_input_is_byte_identical_and_definitions_are_complete(capsys):
    from dataclasses import asdict
    from capacity_sim.cli import PARAMETERS, main

    args = ["--scenario", "S3-broker", "--universe", "201", "--seed", "42"]
    main(args)
    first = capsys.readouterr().out
    main(args)
    assert first == capsys.readouterr().out
    assert set(PARAMETERS) == set(asdict(Config()))


def test_cli_rejects_an_output_path_outside_private_diagnostics(tmp_path):
    from capacity_sim.cli import main

    with pytest.raises(SystemExit) as error:
        main(["--scenario", "S0", "--output", str(tmp_path / "outside.json")])
    assert error.value.code == 2
    assert not (tmp_path / "outside.json").exists()


def test_search_keeps_full_market_features_and_returns_all_candidates():
    result = search(Config(universe=201, stop_s=at("17:30")),
                    intervals=(2, 5), quote_intervals=(30,), modes=(5, 15),
                    policies=("broker",), budgets=(24,))
    assert result["candidate_count"] == 4
    assert result["pareto"]
    assert all(r["full_minute"] and r["focus"] == 10 and r["depth"] == 1
               for r in result["candidates"])
    assert all(r["operational_status"] == "NOT_VERIFIED" for r in result["candidates"])


def test_real_grid_gap_completion_correction_and_frozen_buy_fixture():
    from addons.lan_marketdata.backend.grid import aggregate, project
    from addons.lan_ma55.backend.pipeline import process_symbol
    from addons.lan_ma55.backend.engine import RULE_VERSION

    day = "2026-11-09"
    row = dict(open=10, high=12, low=9, close=11, volume=100, amount=1100)
    data = {day + " " + t: dict(row) for t in ("09:35:00", "09:40:00", "09:45:00")}
    before = deepcopy(data)
    assert not aggregate(data, [day], day + " 09:44:59")["15m"]
    close = {day + " " + t: dict(row) for t in ("14:50:00", "14:55:00", "15:00:00")}
    assert not aggregate(close, [day], day + " 14:59:59")["15m"]
    assert aggregate(data, [day], day + " 09:45:00")["15m"][0]["source_count"] == 3
    del data[day + " 09:40:00"]
    assert not aggregate(data, [day], day + " 09:45:00")["15m"]
    original = aggregate(before, [day], day + " 09:45:00")["15m"]
    before[day + " 09:40:00"]["high"] = 13
    assert aggregate(before, [day], day + " 09:45:00")["15m"][0]["high"] == 13
    pending = project(original, None, day + " 09:45:00")
    assert pending[0]["valid"] is False
    fixture = {"rule_version": "older-fixture", "confirmations": [
        {"event_id": "SYNTHETIC_FROZEN_BUY", "confirmed_at": "2026-11-06 11:15:00",
         "defense": 9.0, "rule_version": RULE_VERSION}], "risks": {}, "state": {}}
    frozen = deepcopy(fixture)
    result, _ = process_symbol("000001.SZ", pending, sessions=[day], raw_tick=0.01,
                               source_asof=day + " 09:45:00", first_seen_at=day + " 09:46:00",
                               previous=fixture)
    assert fixture == frozen
    assert result["confirmations"] == frozen["confirmations"]


def test_installed_sdk_http_shape_in_clean_process(tmp_path):
    # Other provider tests install a fake quantdash module; isolate the real SDK.
    script = r'''
import httpx, json, sys
from quantdash import QuantDash
seen=[]
hooked=[]
def respond(request):
    seen.append((request.method, request.url.path, dict(request.url.params)))
    return httpx.Response(200,json={'data': {} if 'klines' in request.url.path else []})
qd=QuantDash(api_key='synthetic-offline',base_url='https://synthetic.invalid',
             cache_dir=sys.argv[1],max_retries=0)
qd._client._client.close()
qd._client._client=httpx.Client(transport=httpx.MockTransport(respond),
                              base_url='https://synthetic.invalid',
                              event_hooks={'request':[lambda r:hooked.append(r.url.path)]})
try:
    qd.quotes.get(universes='CN_Stock',to_dataframe=True)
    qd.quotes.get(universes='CN_ETF',to_dataframe=True)
    qd.quotes.get(symbols=['000001.SH'],to_dataframe=True)
    result=qd.klines.batch([f'{i:06}.SZ' for i in range(100)],period='15m',
        count=5000,adjust='none',to_dataframe=False,batch_size=100,max_workers=1,
        show_progress=False)
    assert result == {}
    assert len(seen) == 4
    assert len(hooked) == 4
    assert len(seen[-1][2]['symbols'].split(',')) == 100
    assert seen[-1][2]['period'] == '15m' and seen[-1][2]['adjust'] == 'none'
    print(json.dumps({'http_calls':len(seen),'native15_sdk_shape':'PASS_MOCK_TRANSPORT'}))
finally:
    qd.close()
'''
    result = subprocess.run([sys.executable, "-B", "-X", "utf8", "-c", script, str(tmp_path)],
                            check=True, capture_output=True, text=True)
    assert json.loads(result.stdout)["http_calls"] == 4


def test_actual_host_minute_failure_does_not_fall_back_in_isolated_process(tmp_path):
    backend = Path(__file__).resolve().parents[3] / "backend"
    script = r'''
import os, sys
os.environ['DATA_DIR']=sys.argv[2]
sys.path.insert(0,sys.argv[1])
from unittest.mock import patch
from types import SimpleNamespace
from app.services import kline_sync as k
from app.data_providers.base import ProviderRequestError
def fail(*args,**kwargs):
    raise ProviderRequestError('QuantDash synthetic request failure')
with patch.object(k,'_selected_minute_provider_name',return_value='quantdash_user'), \
     patch.object(k,'_resolve_minute_provider',return_value=(SimpleNamespace(get_minute=fail),False,None)), \
     patch.object(k,'get_client',side_effect=AssertionError('unexpected cross-source request')) as fallback:
    try:
        k._try_custom_minute(['000001.SZ'],None,None,'stock')
    except ProviderRequestError:
        pass
    else:
        raise AssertionError('source error was swallowed')
    assert fallback.call_count == 0
print('PASS_NO_FALLBACK')
'''
    result = subprocess.run([sys.executable, "-B", "-X", "utf8", "-c", script, str(backend), str(tmp_path)],
                            check=True, capture_output=True, text=True, cwd=tmp_path)
    assert "PASS_NO_FALLBACK" in result.stdout
