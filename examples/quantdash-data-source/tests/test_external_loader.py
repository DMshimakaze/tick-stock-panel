"""TSP v0.3.31 packages.import_module namespace import compatibility check.

This mimics the exact import strategy (root namespace with __path__). It uses
our fake app/Polars/QuantDash from test_provider; no network or account.
"""
from __future__ import annotations

import importlib
from pathlib import Path
from types import ModuleType
import sys
import unittest

import test_provider  # inject fake host app, Polars and SDK


class InstalledNamespaceTests(unittest.TestCase):
    def test_external_namespace_relative_modules(self):
        folder = Path(__file__).parents[1] / "quantdash_tsp_plugin"
        namespace = "_tsp_provider_test_quantdash_001"
        package = ModuleType(namespace)
        package.__path__ = [str(folder)]
        package.__package__ = namespace
        sys.modules[namespace] = package
        mod = importlib.import_module(f"{namespace}.provider")
        obj = mod.QuantDashProvider()
        try:
            self.assertEqual(obj.name, "quantdash_user")
            self.assertEqual(set(obj.config.datasets), {"daily", "minute", "realtime", "adj_factor", "depth5", "full_minute"})
            self.assertTrue(callable(obj.get_realtime))
            self.assertTrue(callable(obj.get_minute))
            self.assertTrue(callable(obj.close))
        finally:
            obj.close()

    def test_101_symbol_chunks_only_100_per_sdk_call(self):
        provider = test_provider.QuantDashProvider()
        symbols = [f"{n:06d}.SH" for n in range(1, 102)]
        done = []
        result = provider.get_daily(symbols, test_provider.datetime(2026, 11, 1),
                                    test_provider.datetime(2026, 11, 10),
                                    on_chunk_done=lambda i, n: done.append((i, n)))
        self.assertEqual(done, [(1, 2), (2, 2)])
        self.assertEqual(result.height, 101)
        calls = provider._api._qd.klines.calls
        self.assertEqual([len(c[0]) for c in calls], [100, 1])
        provider.close()
