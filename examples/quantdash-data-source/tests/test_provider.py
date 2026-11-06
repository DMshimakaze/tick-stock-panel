"""Protocol tests against an injected fake QuantDash, no account/network/Polars.

The host TSP + real Polars/QuantDash integration remains an additional test.
"""
from __future__ import annotations

from datetime import datetime
from types import ModuleType, SimpleNamespace
from unittest.mock import patch
import sys
import unittest
from zoneinfo import ZoneInfo


class FakeDF:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
    @property
    def columns(self):
        return list(self.rows[0]) if self.rows else []
    @property
    def height(self):
        return len(self.rows)
    def is_empty(self):
        return not self.rows
    def select(self, cols):
        return FakeDF([{c: row.get(c) for c in cols} for row in self.rows])
    def unique(self, subset, keep="last"):
        d = {}
        for row in self.rows:
            d[tuple(row[x] for x in subset)] = row
        return FakeDF(d.values())
    def sort(self, columns):
        return FakeDF(sorted(self.rows, key=lambda row: tuple(row[x] for x in columns)))
    def head(self, n):
        return FakeDF(self.rows[:n])
    def to_dicts(self):
        return list(self.rows)


class FakeKlines:
    def __init__(self):
        self.calls = []
    def batch(self, symbols, **kwargs):
        self.calls.append((symbols, kwargs))
        assert kwargs["adjust"] == "none"
        assert kwargs["to_dataframe"] is False
        assert kwargs["batch_size"] <= 100
        dt = datetime(2026, 11, 6, 9, 31 if kwargs["period"] == "1m" else 0)
        stamp = int(dt.replace(tzinfo=ZoneInfo("Asia/Shanghai")).timestamp() * 1000)
        return {s: {"timestamp": [stamp], "open": [100], "high": [101],
                    "low": [99], "close": [100.5], "volume": [30],
                    "amount": [300300]} for s in symbols}
    def ex_factors(self, symbols, **kwargs):
        return {s: [{"trade_date": "2026-11-06", "ex_factor": 1.0}] for s in symbols}
    def get(self, symbol, **kwargs):
        return FakeDF([{"symbol": symbol}])


class FakeQuotes:
    def get(self, **kwargs):
        symbol = "588170.SH" if kwargs.get("universes") == "CN_ETF" else "600519.SH"
        return [{"symbol": symbol, "last_price": 102,
                 "prev_close": 100, "open": 100, "high": 103, "low": 99,
                 "volume": 100, "amount": 1020000, "ext": {"name": "茅台"}}]


class FakeDepth:
    def __init__(self):
        self.single_calls = []
    def get(self, symbol):
        self.single_calls.append(symbol)
        return {"symbol": symbol, "bid_prices": [100] * 5, "bid_volumes": [1] * 5,
                "ask_prices": [101] * 5, "ask_volumes": [2] * 5}
    def batch(self, symbols):
        return {s: {"bid_prices": [100] * 5, "bid_volumes": [1] * 5,
                    "ask_prices": [101] * 5, "ask_volumes": [2] * 5} for s in symbols}


class FakeQuantDash:
    created = []
    def __init__(self, **kwargs):
        FakeQuantDash.created.append(kwargs)
        self.klines = FakeKlines()
        self.quotes = FakeQuotes()
        self.depth = FakeDepth()
        self._client = SimpleNamespace(_client=SimpleNamespace(event_hooks={"request": [], "response": []}))
    def close(self):
        pass


# Minimal fake host modules, matching only the subset this plugin uses.
polars = ModuleType("polars")
polars.DataFrame = FakeDF
polars.concat = lambda frames, **kwargs: FakeDF([row for frame in frames for row in frame.rows])
app = ModuleType("app")
app.__path__ = []
secrets_store = ModuleType("app.secrets_store")
secrets_store.get_env_backed_secret = lambda *args: "safe-test-key"
app.secrets_store = secrets_store
app_data = ModuleType("app.data_providers")
app_data.__path__ = []
app_base = ModuleType("app.data_providers.base")
app_base.ProviderRequestError = type("ProviderRequestError", (RuntimeError,), {})
qd = ModuleType("quantdash")
qd.QuantDash = FakeQuantDash
from importlib.machinery import ModuleSpec
qd.__spec__ = ModuleSpec("quantdash", loader=None)

# Insert fake only if real dependencies not loaded; this test runs separately.
for name, module in (("polars", polars), ("app", app), ("app.secrets_store", secrets_store),
                     ("app.data_providers", app_data), ("app.data_providers.base", app_base),
                     ("quantdash", qd)):
    sys.modules[name] = module

from quantdash_tsp_plugin.provider import QuantDashProvider, availability, probe_api_key
from quantdash_tsp_plugin.client import BudgetUnavailableError, RollingLimiter


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.obj = QuantDashProvider()
    def tearDown(self):
        self.obj.close()

    def test_manifest_entry_and_status(self):
        import yaml
        from pathlib import Path
        doc = yaml.safe_load((Path(__file__).parents[1] / "quantdash_tsp_plugin" / "plugin.yaml").read_text(encoding="utf-8"))
        self.assertEqual(doc["name"], "quantdash_user")
        self.assertEqual(doc["entry"], "provider:QuantDashProvider")
        self.assertIn("full_minute", doc["datasets"])
        self.assertEqual(set(doc["datasets"]), set(self.obj.config.datasets))

    def test_daily_public_contract(self):
        done = []
        rows = self.obj.get_daily(["600519.SH"], datetime(2026, 11, 1),
                                  datetime(2026, 11, 7), on_chunk_done=lambda c, t: done.append((c, t)))
        self.assertEqual(rows.columns, ["symbol", "date", "open", "high", "low", "close", "volume", "amount"])
        self.assertEqual(rows.height, 1)
        self.assertEqual(done, [(1, 1)])
        sdk = self.obj._api._qd
        self.assertEqual(sdk.klines.calls[0][1]["period"], "1d")

    def test_minute_public_contract(self):
        rows = self.obj.get_minute(["600519.SH"], datetime(2026, 11, 6, 9, 30),
                                   datetime(2026, 11, 6, 9, 35))
        self.assertEqual(rows.height, 1)
        self.assertEqual(rows.columns, ["symbol", "datetime", "open", "high", "low", "close", "volume", "amount"])
        self.assertEqual(rows.rows[0]["datetime"].hour, 9)
        self.assertEqual(self.obj._api._qd.klines.calls[0][1]["period"], "1m")

    def test_index_points_are_not_stock_turnover_prices(self):
        def index_like_bars(symbols, start, end, *, period):
            stamps = [datetime(2026, 11, 6, 9, 31 + i) if period == "1m"
                      else datetime(2026, 11, i + 1) for i in range(6)]
            bars = {
                "timestamp": [int(t.replace(tzinfo=ZoneInfo("Asia/Shanghai")).timestamp() * 1000)
                              for t in stamps],
                "open": [3300] * 6, "high": [3310] * 6,
                "low": [3290] * 6, "close": [3305] * 6,
                "volume": [10000] * 6, "amount": [20000000] * 6,
            }
            return {symbol: bars for symbol in symbols}

        with patch.object(self.obj, "_klines", side_effect=index_like_bars):
            for method in (self.obj.get_daily, self.obj.get_minute):
                with self.subTest(method=method.__name__):
                    result = method(["000001.SH"], datetime(2026, 11, 1),
                                    datetime(2026, 11, 7), asset_type="index")
                    self.assertEqual(result.height, 6)
                    self.assertEqual(result.to_dicts()[0]["volume"], 10000)
                    self.assertEqual(result.to_dicts()[0]["amount"], 20000000)
                    with self.assertRaisesRegex(RuntimeError, "units inconsistent"):
                        method(["600519.SH"], datetime(2026, 11, 1),
                               datetime(2026, 11, 7), asset_type="stock")

    def test_realtime_public_contract(self):
        row = self.obj.get_realtime()[0]
        self.assertEqual(row["symbol"], "600519.SH")
        self.assertAlmostEqual(row["change_pct"], 0.02)
        self.assertEqual(row["volume"], 100)

    def test_adj_factor_and_depth(self):
        df = self.obj.get_adj_factors(["600519.SH"])
        self.assertEqual(df.height, 1)
        self.assertEqual(df.rows[0]["ex_factor"], 1)
        self.assertEqual(self.obj.get_depth_batch(["600519.SH"])["600519.SH"]["bid_volumes"], [1]*5)

    def test_factor_audit_distinguishes_empty_missing_and_failure(self):
        sdk = self.obj._api._ensure_sdk()
        sdk.klines.ex_factors = lambda symbols, **kw: {"600519.SH": []}
        result = self.obj.audit_adj_factors(["600519.SH", "000001.SZ"])
        self.assertEqual(result["statuses"], {"600519.SH": "CONFIRMED_NO_EVENTS", "000001.SZ": "UNVERIFIED"})
        with self.assertRaisesRegex(RuntimeError, "incomplete factor coverage"):
            self.obj.get_adj_factors(["600519.SH", "000001.SZ"])
        sdk.klines.ex_factors = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("network"))
        with self.assertRaisesRegex(RuntimeError, "SDK request failed"):
            self.obj.audit_adj_factors(["600519.SH"])

    def test_depth_uses_authorized_single_access_without_batch_permission(self):
        with patch.object(FakeDepth, "batch", side_effect=PermissionError("batch access denied")) as batch:
            rows = self.obj.get_depth_batch(["600519.SH", "000001.SZ"])
        self.assertEqual(set(rows), {"600519.SH", "000001.SZ"})
        self.assertEqual(self.obj._api._qd.depth.single_calls, ["600519.SH", "000001.SZ"])
        batch.assert_not_called()

    def test_depth_single_failure_preserves_other_success_and_reports_missing(self):
        good = FakeDepth().get("600519.SH")
        with (patch.object(FakeDepth, "get", side_effect=[good, PermissionError("single access denied")]),
              self.assertLogs("quantdash_tsp_plugin.provider", level="WARNING")):
            result = self.obj.test_dataset("depth5", ["600519.SH", "000001.SZ"])
        self.assertEqual(result["rows"], 1)
        self.assertEqual(result["missing_symbols"], ["000001.SZ"])
        self.assertTrue(result["warnings"])

    def test_etf_failure_keeps_stock_snapshot_and_does_not_repeat_permission_error(self):
        good = FakeQuotes().get(universes="CN_Stock")
        denied = RuntimeError("ETF forbidden")
        denied.status_code = 403
        with patch.object(FakeQuotes, "get", side_effect=[good, denied, good]) as fetch:
            first = self.obj.get_realtime()
            second = self.obj.get_realtime()
        self.assertEqual([r["symbol"] for r in first], ["600519.SH"])
        self.assertEqual(second, first)
        self.assertEqual(fetch.call_count, 3)

    def test_etf_snapshot_is_merged_independently(self):
        self.assertEqual({r["symbol"] for r in self.obj.get_realtime()}, {"600519.SH", "588170.SH"})

    def test_depth_empty_and_forbidden_symbols_do_not_discard_good_symbol(self):
        denied = RuntimeError("forbidden")
        denied.status_code = 403
        good = FakeDepth().get("600519.SH")
        with patch.object(FakeDepth, "get", side_effect=[{}, denied, good]):
            rows = self.obj.get_depth_batch(["000001.SZ", "000002.SZ", "600519.SH"])
        self.assertEqual(set(rows), {"600519.SH"})

    def test_depth_429_stops_further_requests_and_reports_incomplete(self):
        limited = RuntimeError("rate limited")
        limited.status_code = 429
        good = FakeDepth().get("600519.SH")
        with patch.object(FakeDepth, "get", side_effect=[good, limited]) as fetch:
            rows = self.obj.get_depth_batch(["600519.SH", "000001.SZ", "000002.SZ"])
        self.assertEqual(set(rows), {"600519.SH"})
        self.assertEqual(fetch.call_count, 2)

    def test_depth_budget_exhaustion_returns_partial_without_waiting(self):
        good = FakeDepth().get("600519.SH")
        with patch.object(self.obj._api, "call", side_effect=[good, BudgetUnavailableError()]) as fetch:
            rows = self.obj.get_depth_batch(["600519.SH", "000001.SZ", "000002.SZ"])
        self.assertEqual(set(rows), {"600519.SH"})
        self.assertEqual(fetch.call_count, 2)

    def test_empty_budget_is_reported_as_failure_not_empty_success(self):
        with patch.object(self.obj._api, "call", side_effect=BudgetUnavailableError()):
            result = self.obj.test_dataset("depth5", ["600519.SH"])
        self.assertEqual(result["rows"], 0)
        self.assertIn("skipped=1", result["error"])

    def test_etf_empty_response_keeps_stocks_and_warns(self):
        stock = FakeQuotes().get(universes="CN_Stock")
        with (patch.object(FakeQuotes, "get", side_effect=[stock, []]),
              self.assertLogs("quantdash_tsp_plugin.provider", level="WARNING") as logs):
            rows = self.obj.get_realtime()
        self.assertEqual([r["symbol"] for r in rows], ["600519.SH"])
        self.assertIn("stock snapshot retained", logs.output[0])

    def test_full_minute_uses_host_fallback_and_minute_rejects_other_freq(self):
        self.assertIn("full_minute", self.obj.config.datasets)
        self.assertFalse(hasattr(self.obj, "get_intraday_batch"))
        with self.assertRaises(RuntimeError):
            self.obj.get_minute(["600519.SH"], freq="5m")

    def test_full_minute_trial_uses_current_beijing_day(self):
        class BeijingClock(datetime):
            @classmethod
            def now(cls, tz=None):
                instant = datetime(2026, 11, 6, 14, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
                return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)

        with patch("quantdash_tsp_plugin.provider.datetime", BeijingClock):
            result = self.obj.test_dataset("full_minute", ["600519.SH"])
        self.assertNotIn("error", result)
        self.assertEqual(result["rows"], 1)
        self.assertEqual(result["columns"],
                         ["symbol", "datetime", "open", "high", "low", "close", "volume", "amount"])
        call = self.obj._api._qd.klines.calls[0][1]
        self.assertEqual(call["period"], "1m")
        self.assertEqual(call["start_time"], int(datetime(2026, 11, 6,
                         tzinfo=ZoneInfo("Asia/Shanghai")).timestamp() * 1000))

    def test_trial_windows_use_beijing_on_non_beijing_host(self):
        class ElsewhereClock(datetime):
            @classmethod
            def now(cls, tz=None):
                instant = datetime(2026, 11, 7, 0, 15, tzinfo=ZoneInfo("Asia/Shanghai"))
                return instant.astimezone(tz) if tz else instant.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)

        for dataset, method in (("daily", "get_daily"), ("minute", "get_minute")):
            with (self.subTest(dataset=dataset),
                  patch("quantdash_tsp_plugin.provider.datetime", ElsewhereClock),
                  patch.object(self.obj, method, return_value=FakeDF([{"symbol": "600519.SH"}])) as fetch):
                result = self.obj.test_dataset(dataset)
                self.assertNotIn("error", result)
                self.assertEqual(fetch.call_args.args[2], datetime(2026, 11, 7, 0, 15))

    def test_probe_api_key_without_env_side_effect(self):
        result = probe_api_key("candidate-key")
        # Fake's DataFrame only has .rows and not .empty, so probe succeeds.
        self.assertTrue(result[0])
        self.assertEqual(FakeQuantDash.created[-1]["api_key"], "candidate-key")

    def test_roll_budget_acquire(self):
        class C:
            now = 0.0
            def clock(self): return self.now
            def sleep(self, t): self.now += t
        c = C()
        lim = RollingLimiter(2, clock=c.clock, sleep=c.sleep)
        lim.acquire(); lim.acquire(); lim.acquire()
        self.assertGreaterEqual(c.now, 60)

if __name__ == "__main__":
    unittest.main()
