from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from backend.clock import closed_index, close_label, position, completed_through
from backend.energy import (
    Config,
    aggregate,
    build_profile,
    evaluate,
    predict,
    alert_direction,
)
from backend.providers import (
    Day,
    Quote,
    parse_tencent_quotes,
    parse_sina_quotes,
    parse_tencent_days,
    parse_eastmoney_days,
    aggregate_quote,
)
from backend.store import Store
from backend.projection import build_view
from backend.collector import publish_cycle


def dt(value):
    return datetime.fromisoformat("2026-11-26T" + value + "+08:00")


def series(factor=1):
    return {k: (k + 1) * 100 * factor + 50 for k in range(240)}


class Core(unittest.TestCase):
    def test_clock(self):
        for k in range(240):
            self.assertEqual(closed_index(close_label(k)), k)
        for label in ("09:30", "13:00", "15:01", "11:31", "0960", "bad"):
            self.assertIsNone(closed_index(label))
        self.assertEqual(position(dt("09:30:00")), (0, 0))
        self.assertEqual(position(dt("11:30:00")), (119, 60))
        self.assertIsNone(position(dt("12:00:00")))
        self.assertEqual(completed_through(dt("12:00:00")), 119)
        self.assertEqual(position(dt("13:00:00")), (120, 0))
        self.assertEqual(position(dt("15:00:00")), (239, 60))
        self.assertEqual(completed_through(dt("09:30:59")), -1)

    def test_original_hand_calculated_case(self):
        # Adapted from upstream tests/core/energyEngine.test.ts, first case.
        bars = [100, 100, 100, 100, 100, 100, 110, 120, 130, 140, 150]
        today = {k: sum(bars[: k + 1]) for k in range(11)}
        prev = {k: (k + 1) * 100 for k in range(240)}
        m = evaluate(today, prev, 10)
        self.assertEqual(m["diff"], 150)
        self.assertAlmostEqual(m["ratio_pct"], 150 / 1100 * 100)
        self.assertAlmostEqual(m["multiple"], 1.3)
        self.assertAlmostEqual(m["slope"], 7)
        w = 1100 / 24000
        alpha = w / (w + 0.12)
        self.assertAlmostEqual(
            m["predicted_amount"],
            1250 + (alpha * 1250 / 1100 + 1 - alpha) * (24000 - 1100),
        )

    def test_profiles_and_predictions(self):
        normal = {k: 500 + (10000 - 500) / 239 * k for k in range(240)}
        spike = {k: 1000 + (10000 - 1000) / 239 * k for k in range(240)}
        normal[239] = spike[239] = 10000
        prof = build_profile([(normal, 150)] * 4 + [(spike, 300), ({0: 1}, None)])
        self.assertEqual(prof.sample_days, 5)
        self.assertAlmostEqual(prof.shares[0], 0.06)
        m = evaluate({0: 560}, spike, 0, profile=prof)
        self.assertGreater(m["predicted_amount"], 9500)
        self.assertLess(m["predicted_amount"], 10000)
        self.assertAlmostEqual(predict(600, 10000, 0.06), 10000)
        self.assertEqual(predict(12345, 10000, 1), 12345)
        self.assertAlmostEqual(predict(560, 10000, 0.1, 0), 5600)
        corrupt = normal.copy()
        corrupt[21] = 0
        self.assertIsNone(build_profile([(corrupt, None)]))

    def test_exact_thresholds(self):
        self.assertEqual(evaluate({0: 105}, {0: 100}, 0)["state"], "EXPANDING")
        self.assertEqual(evaluate({0: 95}, {0: 100}, 0)["state"], "SHRINKING")
        self.assertEqual(evaluate({0: 100}, {0: 100}, 0)["state"], "FLAT")

    def test_partial_interpolation_and_auction(self):
        p = {0: 200, 1: 300}
        self.assertIsNone(evaluate({}, p, 0, current=150, elapsed=30)["ratio_pct"])
        self.assertEqual(
            evaluate({}, p, 0, current=150, elapsed=30, prev_auction=100)["ratio_pct"],
            0,
        )
        self.assertEqual(
            evaluate({0: 200}, p, 1, current=250, elapsed=30)["ratio_pct"], 0
        )
        self.assertIsNone(evaluate({0: 200}, p, 1, elapsed=30)["today_amount"])

    def test_gap_invalid_and_auction_isolated(self):
        p = series()
        t = p.copy()
        t.pop(8)
        self.assertIsNone(evaluate(t, p, 10)["multiple"])
        self.assertIsNone(evaluate({0: 100}, {0: 0}, 0)["ratio_pct"])
        self.assertIsNone(evaluate(p, p, 0)["multiple"])
        self.assertEqual(evaluate(p, p, 10)["multiple"], 1)
        self.assertEqual(aggregate({0: 1, 1: 2}, {0: 3, 2: 4}), {0: 4})
        self.assertEqual(evaluate({239: 500}, {}, 239)["predicted_amount"], 500)

    def test_alerts(self):
        m = {
            "minute_index": 10,
            "window": 5,
            "multiple": 1.5,
            "slope": 3,
            "ratio_pct": 0,
        }
        self.assertEqual(alert_direction(m), "SURGE")
        self.assertIsNone(alert_direction(m, degraded=True))
        self.assertIsNone(alert_direction({**m, "minute_index": 4}))
        self.assertIsNone(alert_direction({**m, "slope": 2.99}))
        self.assertEqual(
            alert_direction({**m, "multiple": 0.65, "slope": -3}), "SHRINK"
        )
        for w in (3, 5, 10):
            self.assertEqual(
                evaluate(series(), series(), 20, config=Config(window=w))["multiple"], 1
            )


class Providers(unittest.TestCase):
    def test_tencent_quote_yuan(self):
        fields = [""] * 38
        fields[3] = "4000"
        fields[4] = "3900"
        fields[30] = "20261126134500"
        fields[35] = "4000/50/123456789"
        fields[37] = "12345"

        def parse():
            return parse_tencent_quotes('v_sh000001="' + "~".join(fields) + '";')[
                "000001.SH"
            ]

        self.assertEqual(parse().amount, 123456789)
        fields[35] = ""
        self.assertEqual(parse().amount, 123450000)
        self.assertEqual(parse().stamp, dt("13:45:00"))
        fields[37] = "NaN"
        self.assertEqual(
            parse_tencent_quotes('v_sh000001="' + "~".join(fields) + '";'), {}
        )

    def test_sina_quote(self):
        fields = [""] * 32
        fields[2] = "3900"
        fields[3] = "4000"
        fields[9] = "123456789"
        fields[30] = "2026-11-26"
        fields[31] = "13:45:00"
        q = parse_sina_quotes('var hq_str_sh000001="' + ",".join(fields) + '";')[
            "000001.SH"
        ]
        self.assertEqual(q.amount, 123456789)
        self.assertEqual(q.stamp, dt("13:45:00"))

    def test_tencent_minute_labels(self):
        data = {
            "data": {
                "sh000001": {
                    "data": [
                        {
                            "date": "20261126",
                            "data": [
                                "1301 4000 20 300",
                                "0930 3999 10 100",
                                "0931 4000 12 120",
                                "1300 4000 19 270",
                                "1500 4000 30 500",
                                "1501 4000 31 510",
                            ],
                        }
                    ]
                }
            }
        }
        day = parse_tencent_days(data, "000001.SH")[0]
        self.assertEqual(day.auction, 100)
        self.assertEqual(day.amounts, {0: 120, 120: 300, 239: 500})

    def test_eastmoney_gap(self):
        def row(label, amount):
            return f"2026-11-26 {label},1,4000,1,1,20,{amount},1"

        d = parse_eastmoney_days(
            {
                "data": {
                    "trends": [
                        row("09:32", 30),
                        row("09:30", 100),
                        row("09:31", 20),
                        row("09:34", 50),
                    ]
                }
            },
            "000001.SH",
        )[0]
        self.assertEqual(d.amounts, {0: 120, 1: 150})
        self.assertEqual(d.auction, 100)

    def test_snapshot_alignment(self):
        a = Quote("000001.SH", dt("13:45:03"), 100, 4000, 1, "tencent")
        b = Quote("399106.SZ", dt("13:45:04"), 200, 2000, 1, "tencent")
        self.assertEqual(aggregate_quote({a.symbol: a, b.symbol: b}).amount, 300)
        self.assertIsNone(aggregate_quote({a.symbol: a}))
        self.assertIsNone(
            aggregate_quote(
                {
                    a.symbol: a,
                    b.symbol: Quote(b.symbol, dt("13:44:58"), 200, 2000, 1, "tencent"),
                }
            )
        )
        self.assertIsNone(
            aggregate_quote(
                {
                    a.symbol: a,
                    b.symbol: Quote(b.symbol, dt("13:45:04"), 200, 2000, 1, "sina"),
                }
            )
        )


class Persistence(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "state.sqlite")

    def tearDown(self):
        self.tmp.cleanup()

    def save(self, symbol, day, factor=1, cutoff=239, source="tencent"):
        self.store.save(
            Day(symbol, day, source, 50, series(factor), {k: 4000 for k in range(240)}),
            cutoff,
        )

    def test_save_source_and_cutoff(self):
        self.save("000001.SH", "2026-11-26", cutoff=10)
        self.save("000001.SH", "2026-11-26", 2, source="eastmoney")
        self.assertEqual(
            len(self.store.load("000001.SH", "2026-11-26", "tencent").amounts), 11
        )
        self.assertEqual(
            len(self.store.load("000001.SH", "2026-11-26", "eastmoney").amounts), 240
        )
        self.store.save(
            Day("000001.SH", "2026-11-26", "tencent", None, {1: 250}, {}), 10
        )
        self.assertEqual(
            len(self.store.load("000001.SH", "2026-11-26", "tencent").amounts), 11
        )

    def test_projection_replay_no_future(self):
        for s in ["000001.SH", "399106.SZ"]:
            self.save(s, "2026-11-25")
            self.save(s, "2026-11-26", 2)
        v = build_view(
            self.store,
            day="2026-11-26",
            previous="2026-11-25",
            prior_sessions=["2026-11-25", "2026-11-26"],
            now=dt("09:41:00"),
            quotes={},
            live=False,
        )
        r = v["indices"][0]
        self.assertEqual(r["status"], "HISTORICAL")
        self.assertEqual(r["metrics"]["minute_index"], 10)
        self.assertEqual(r["metrics"]["today_amount"], 2 * (11 * 200 + 50))
        self.assertIsNone(r["points"][11]["today"])
        self.assertIsNotNone(r["points"][239]["previous"])
        self.assertEqual(r["metrics"]["sample_days"], 1)

    def test_degraded_if_missing_side(self):
        self.save("000001.SH", "2026-11-25")
        self.save("000001.SH", "2026-11-26")
        v = build_view(
            self.store,
            day="2026-11-26",
            previous="2026-11-25",
            prior_sessions=[],
            now=dt("10:00:00"),
            quotes={},
            live=True,
        )
        self.assertEqual(v["indices"][0]["status"], "UNAVAILABLE")
        self.assertIsNone(v["indices"][0]["metrics"]["today_amount"])

    def test_stale_and_cooldown(self):
        for s in ["000001.SH", "399106.SZ"]:
            self.save(s, "2026-11-25")
            self.save(s, "2026-11-26")
        qs = {
            s: Quote(s, dt("10:00:01"), 5000, 4000, 1, "tencent")
            for s in ["000001.SH", "399106.SZ"]
        }
        args = dict(
            day="2026-11-26",
            previous="2026-11-25",
            prior_sessions=[],
            quotes=qs,
            live=True,
        )
        v = build_view(self.store, now=dt("10:00:04"), **args)
        self.assertEqual(v["indices"][0]["status"], "LIVE")
        v = build_view(self.store, now=dt("10:02:00"), **args)
        self.assertEqual(v["indices"][0]["status"], "DEGRADED")
        m = dict(minute_index=30, multiple=1.7, slope=4)
        self.assertTrue(
            self.store.record_event(
                "2026-11-26", "MARKET_TOTAL", "SURGE", dt("10:00:00"), m
            )
        )
        self.assertFalse(
            self.store.record_event(
                "2026-11-26", "MARKET_TOTAL", "SURGE", dt("10:04:59"), m
            )
        )
        self.assertTrue(
            self.store.record_event(
                "2026-11-26", "MARKET_TOTAL", "SURGE", dt("10:05:00"), m
            )
        )
        self.assertEqual(len(self.store.events("2026-11-26")), 2)

    def test_cycle_publisher(self):
        sink = []
        v = publish_cycle(
            self.store,
            now=dt("10:00:00"),
            day="2026-11-26",
            previous="2026-11-25",
            prior_sessions=[],
            quotes={},
            live=False,
            publish=sink.append,
        )
        self.assertEqual(sink, [v])
        self.assertEqual(v["events"], [])
        self.assertEqual(v["schema_version"], 1)


if __name__ == "__main__":
    unittest.main()
