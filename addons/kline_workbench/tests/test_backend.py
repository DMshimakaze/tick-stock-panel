"""Shared read-only bridge tests. Temporary stores are not live-market evidence."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
import zipfile

import httpx

from addons.kline_workbench.backend.service import Worker
from addons.kline_workbench.backend.publisher import Publisher, configs
from addons.kline_workbench.backend.market_client import Client
from addons.kline_workbench.build_backend import build


def payload(symbol="600519.SH"):
    return dict(
        api_version=1,
        sessions=["2026-11-06"],
        rows=[
            dict(
                symbol=symbol,
                timeframe=tf,
                bars=[
                    dict(
                        timestamp="2026-11-06 15:00:00",
                        close=10,
                        ma55=9.9,
                        complete=True,
                    )
                ],
                state="READY",
                row_count=256,
                revision=1,
                name="合成",
                price_basis="QFQ",
                basis_id="QFQ:test",
                asset_type="stock",
                warm_count=256,
            )
            for tf in ("15m", "30m", "60m")
        ],
    )


class BridgeTests(unittest.TestCase):
    def test_no_sdk_no_database_writer_and_saved_payload(self):
        with TemporaryDirectory() as folder:
            worker = Worker(
                SimpleNamespace(data_dir=folder), {"api_base": "http://local"}
            )
            try:
                self.assertFalse(hasattr(worker, "collector"))
                demand = dict(
                    scope="0",
                    client_id="a",
                    request_id="1",
                    requested_symbol="600519.SH",
                    period="15m",
                )
                row = worker.view(demand, payload())
                body = json.loads(row["payload_json"])
                self.assertEqual(body["series"]["60m"][0]["ma55"], 9.9)
                self.assertNotIn("raw", body)
                self.assertFalse(list(Path(folder).rglob("*.sqlite3")))
                self.assertEqual(worker.status()["minute_request_limit"], 0)
            finally:
                worker.publisher.close()
                worker.source.close()

    def test_multi_tab_reads_are_batched_and_failure_is_visible(self):
        with TemporaryDirectory() as folder:
            worker = Worker(
                SimpleNamespace(data_dir=folder), {"api_base": "http://local"}
            )
            calls = []
            published = []

            class Source:
                def bars(self, symbols, **kwargs):
                    calls.append((symbols, kwargs))
                    return payload()

            worker.source.close()
            worker.source = Source()
            worker.publisher.publish = lambda rows: published.extend(rows)
            demand = dict(
                scope="0",
                client_id="a",
                request_id="1",
                requested_symbol="600519.SH",
                period="15m",
            )
            worker.publish([demand, dict(demand, scope="1", client_id="b")])
            self.assertEqual(len(calls), 1)
            self.assertEqual(len(published), 2)
            self.assertTrue(all(r["phase"] == "READY" for r in published))
            worker.publish_error([demand], "HTTP 401")
            self.assertEqual(published[-1]["phase"], "DATA_SOURCE_UNAVAILABLE")
            worker.publisher.close()

    def test_official_bridge_and_versioned_client_auth(self):
        requests = []

        def handle(request):
            requests.append(request)
            return httpx.Response(
                200,
                json={"items": []}
                if request.url.path == "/api/ext-data" and request.method == "GET"
                else dict(api_version=1, rows=[]),
            )

        publisher = Publisher("http://local", transport=httpx.MockTransport(handle))
        publisher.prepare()
        publisher.close()
        self.assertEqual(len(requests), 3)
        self.assertTrue(all(c["fields"][0]["name"] == "scope" for c in configs()))
        client = Client(
            "http://local", session="isolated", transport=httpx.MockTransport(handle)
        )
        client.bars(["600519.SH"], timeframes="15m,30m,60m")
        client.close()
        self.assertEqual(
            requests[-1].url.path, "/api/extensions/lan.marketdata/v1/bars"
        )
        self.assertIn("tf_session=isolated", requests[-1].headers["cookie"])

    def test_installable_package_contains_only_bridge_modules(self):
        archive = build()
        with zipfile.ZipFile(archive) as z:
            names = z.namelist()
            self.assertIn("backend/market_client.py", names)
            self.assertNotIn("backend/collector.py", names)
            self.assertNotIn("backend/store.py", names)
            self.assertNotIn("quantdash", z.read("extension.yaml").decode())


if __name__ == "__main__":
    unittest.main()
