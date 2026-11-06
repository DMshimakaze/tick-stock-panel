from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import unittest

from quantdash_tsp_plugin.mapping import (
    depth_rows, factor_rows, kline_rows, parse_bj_timestamp, quote_rows, audit_trade_units,
    request_ms, symbol_code,
)

BJ = ZoneInfo("Asia/Shanghai")


def ms(dt):
    return int(dt.replace(tzinfo=BJ).timestamp() * 1000)


class MappingTests(unittest.TestCase):
    def test_symbol_suffix_and_prefix(self):
        self.assertEqual(symbol_code("sh600519"), "600519.SH")
        self.assertEqual(symbol_code("000001.sz"), "000001.SZ")
        with self.assertRaises(ValueError):
            symbol_code("600519")

    def test_timestamp_to_beijing_naive(self):
        self.assertEqual(parse_bj_timestamp(ms(datetime(2026, 11, 6, 9, 31))),
                         datetime(2026, 11, 6, 9, 31))
        self.assertEqual(request_ms(datetime(2026, 11, 6, 9, 31)),
                         ms(datetime(2026, 11, 6, 9, 31)))
        self.assertEqual(request_ms(datetime(2026, 11, 6), end_of_day=True),
                         ms(datetime(2026, 11, 6, 23, 59, 59, 999000)))

    def test_kline_compact_raw(self):
        raw = {"600519.SH": {
            "timestamp": [ms(datetime(2026, 11, 6, 9, 31))],
            "open": [100], "high": [102], "low": [99], "close": [101],
            "volume": [130], "amount": [1313000],
        }}
        bars = kline_rows(raw, freq="minute")
        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0]["datetime"], datetime(2026, 11, 6, 9, 31))
        self.assertEqual(bars[0]["volume"], 130)
        self.assertEqual(bars[0]["amount"], 1313000)
        daily = kline_rows(raw, freq="daily")
        self.assertEqual(str(daily[0]["date"]), "2026-11-06")

    def test_missing_amount_null_not_fabricated(self):
        raw = {"000001.SZ": {"timestamp": [ms(datetime(2026, 11, 6, 15))],
                              "open": [10], "high": [11], "low": [9],
                              "close": [10.1], "volume": [55]}}
        rows = kline_rows(raw, freq="daily")
        self.assertIsNone(rows[0]["amount"])

    def test_real_minute_float_roundoff_preserves_ohlc_envelope(self):
        raw = {"920116.BJ": {
            "timestamp": [ms(datetime(2026, 11, 6, 10, 26))],
            "open": [35.300000000000026], "high": [35.3],
            "low": [35.30000000000003], "close": [35.29999999999997],
            "volume": [1], "amount": [35.30000000011641],
        }}
        for freq in ("daily", "minute"):
            with self.subTest(freq=freq):
                row = kline_rows(raw, freq=freq)[0]
                self.assertEqual(row["open"], raw["920116.BJ"]["open"][0])
                self.assertEqual(row["close"], raw["920116.BJ"]["close"][0])
                prices = [raw["920116.BJ"][field][0] for field in ("open", "high", "low", "close")]
                self.assertEqual(row["high"], max(prices))
                self.assertEqual(row["low"], min(prices))
                self.assertEqual(row["volume"], 1)
                self.assertEqual(row["amount"], 35.30000000011641)

    def test_invalid_ohlc_beyond_roundoff_still_fails(self):
        for prices in ((35.31, 35.3, 35.3, 35.3),
                       (35.3, 35.31, 35.31, 35.3), (0, 1, 0, 1)):
            raw = {"920116.BJ": {
                "timestamp": [ms(datetime(2026, 11, 6, 10, 26))],
                **{field: [value] for field, value in
                   zip(("open", "high", "low", "close"), prices, strict=True)},
                "volume": [1], "amount": [3530],
            }}
            with self.subTest(prices=prices), self.assertRaisesRegex(ValueError, "invalid OHLC"):
                kline_rows(raw, freq="minute")

    def test_unit_explicit_shares(self):
        raw = {"000001.SZ": {"timestamp": [ms(datetime(2026, 11, 6, 15))],
                              "open": [10], "high": [11], "low": [9],
                              "close": [10], "volume": [1000]}}
        self.assertEqual(kline_rows(raw, freq="daily", volume_unit="shares")[0]["volume"], 10)

    def test_compact_mismatch_and_duplicates_fail(self):
        raw = {"600519.SH": {"timestamp": [ms(datetime(2026, 11, 6, 15))],
                              "open": [10], "high": [11, 12], "low": [9],
                              "close": [10], "volume": [10]}}
        with self.assertRaisesRegex(ValueError, "unequal lengths"):
            kline_rows(raw, freq="daily")
        raw["600519.SH"]["high"] = [11]
        raw["600519.SH"]["timestamp"] *= 2
        for key in ("open", "high", "low", "close", "volume"):
            raw["600519.SH"][key] *= 2
        with self.assertRaisesRegex(ValueError, "duplicate"):
            kline_rows(raw, freq="daily")

    def test_quote_changes_derived_fraction_not_source_percent(self):
        items = [{"symbol": "600519.SH", "last_price": 102,
                  "prev_close": 100, "open": 99, "high": 103, "low": 98,
                  "volume": 40, "amount": 410000, "ext": {"name": "茅台", "change_pct": 2}}]
        rec = quote_rows(items)[0]
        self.assertAlmostEqual(rec["change_pct"], 0.02)
        self.assertIsNone(rec["amplitude"])
        self.assertEqual(rec["volume"], 40)
        self.assertEqual(rec["name"], "茅台")

    def test_quote_open_missing_fails(self):
        with self.assertRaises(ValueError):
            quote_rows([{"symbol": "600519.SH", "last_price": 102,
                         "prev_close": 100, "high": 103, "low": 98, "volume": 40}])

    def test_quote_drops_one_bad_in_universe(self):
        good = {"symbol": "600519.SH", "last_price": 102, "prev_close": 100,
                "open": 100, "high": 103, "low": 98, "volume": 40}
        bad = dict(good, open=None)
        self.assertEqual(len(quote_rows([good, bad])), 1)

    def test_units_audit_fail_closed_without_fabrication(self):
        rows = [{"amount": 1000000, "volume": 100, "high": 102, "low": 99} for _ in range(6)]
        audit_trade_units(rows)
        broken = [dict(r, amount=10000) for r in rows]
        with self.assertRaisesRegex(ValueError, "units inconsistent"):
            audit_trade_units(broken)

    def test_factor_rows(self):
        rows = factor_rows([{"symbol": "600519.SH", "trade_date": "2026-11-06", "ex_factor": 0.97}])
        self.assertEqual(rows[0]["ex_factor"], 0.97)
        self.assertEqual(rows[0]["trade_date"].isoformat(), "2026-11-06")

    def test_factor_history_accepts_millisecond_epochs_before_2001(self):
        stamp = ms(datetime(1991, 4, 3))
        self.assertLess(stamp, 1_000_000_000_000)
        row = factor_rows([{"symbol": "000001.SZ", "timestamp": stamp, "ex_factor": 1.2}])[0]
        self.assertEqual(row["trade_date"].isoformat(), "1991-04-03")

    def test_depth_rows(self):
        rows = depth_rows({"600519.SH": {"bid_prices": [100] * 5, "bid_volumes": [2] * 5,
                                           "ask_prices": [101] * 5, "ask_volumes": [3] * 5,
                                           "timestamp": 1780000000000}}, ["600519.SH"])
        self.assertEqual(rows["600519.SH"]["ask_volumes"], [3] * 5)
        with self.assertRaises(ValueError):
            depth_rows({"600519.SH": {"bid_prices": [100] * 4}}, ["600519.SH"])

if __name__ == "__main__":
    unittest.main()
