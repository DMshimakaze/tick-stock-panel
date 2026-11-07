"""Deterministic request-budget contention; no network or wall-clock sleeps."""
from __future__ import annotations

import threading
import unittest

import test_provider  # installs the fake SDK used by these protocol tests
from quantdash_tsp_plugin.client import QuantDashClient, RollingLimiter


def simulate_contention(depth_symbols, realtime_reserve):
    class Clock:
        now = 0.0
        next_quote = 0.0

        def sleep(self, duration):
            target = self.now + duration
            while self.next_quote <= target:
                due = self.next_quote
                self.next_quote += 30
                self.now = max(self.now, due)
                for _ in range(3):  # stock, ETF, indices per quote refresh
                    budget.acquire(realtime=True)
                    waits.append(self.now - due)
                    requests.append((self.now, "quote"))
            self.now = max(self.now, target)

    clock = Clock()
    budget = RollingLimiter(12, realtime_reserve=realtime_reserve,
                            clock=lambda: clock.now, sleep=clock.sleep)
    waits, requests = [], []
    remaining = {"minute": 59, "depth": depth_symbols}
    minute_finished = None
    while any(remaining.values()):
        for kind in remaining:
            if remaining[kind]:
                budget.acquire()
                requests.append((clock.now, kind))
                remaining[kind] -= 1
                if kind == "minute" and remaining[kind] == 0:
                    minute_finished = clock.now
    ordered = sorted(requests)
    max_window = max(sum(t - 60 < other <= t for other, _ in ordered) for t, _ in ordered)
    return {"depth_symbols": depth_symbols, "realtime_reserve": realtime_reserve,
            "max_quote_wait_seconds": max(waits), "minute_round_seconds": minute_finished,
            "total_seconds": clock.now, "http_requests": len(requests),
            "max_requests_in_rolling_minute": max_window}


class ClientTests(unittest.TestCase):
    def test_quotes_keep_reserved_capacity_for_all_requested_pool_sizes(self):
        for count in (20, 50, 100):
            with self.subTest(depth_symbols=count):
                baseline = simulate_contention(count, 0)
                current = simulate_contention(count, 6)
                self.assertGreater(baseline["max_quote_wait_seconds"], 30)
                self.assertLess(current["max_quote_wait_seconds"], 0.1)
                self.assertLessEqual(current["max_requests_in_rolling_minute"], 12)

    def test_budget_wait_does_not_hold_sdk_lock(self):
        waiting, release, quote_done = threading.Event(), threading.Event(), threading.Event()
        errors = []

        class Clock:
            now = 0.0

            def sleep(self, duration):
                waiting.set()
                if not release.wait(2):
                    raise AssertionError("quote could not run while background waited")
                self.now += duration

        clock = Clock()
        client = QuantDashClient("safe-test-key")
        self.assertIsInstance(client._ensure_sdk(), test_provider.FakeQuantDash)
        client.budget = RollingLimiter(2, realtime_reserve=1, clock=lambda: clock.now, sleep=clock.sleep)

        def request(*, realtime=False):
            client.call(lambda sdk: client._before_http_request(None), realtime=realtime)

        def background():
            try:
                request()
            except Exception as exc:  # noqa: BLE001 - propagate every worker failure to the parent assertion.
                errors.append(exc)

        def quote():
            try:
                request(realtime=True)
                quote_done.set()
            except Exception as exc:  # noqa: BLE001 - propagate every worker failure to the parent assertion.
                errors.append(exc)

        request()
        worker = threading.Thread(target=background)
        worker.start()
        self.assertTrue(waiting.wait(1))
        priority = threading.Thread(target=quote)
        priority.start()
        try:
            self.assertTrue(quote_done.wait(1), "realtime blocked behind SDK lock")
        finally:
            release.set()
            worker.join(2)
            priority.join(2)
            client.close()
        self.assertFalse(errors)

    def test_nonblocking_depth_budget_never_sleeps(self):
        budget = RollingLimiter(2, realtime_reserve=1,
                                sleep=lambda _: self.fail("small depth query must not wait"))
        self.assertTrue(budget.acquire(wait=False))
        self.assertFalse(budget.acquire(wait=False))
        self.assertTrue(budget.acquire(realtime=True, wait=False))


if __name__ == "__main__":
    unittest.main()
