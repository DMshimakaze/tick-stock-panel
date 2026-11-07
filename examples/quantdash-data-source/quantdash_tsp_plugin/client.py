"""Thin QuantDash SDK bridge with per-process rolling HTTP request budget.

No TSP service/MA55 dependency. For quantdash 0.1.0, the public SDK wraps
httpx.Client; the same request-hook approach was validated in MA55 collector.
"""
from __future__ import annotations

from collections import deque
import logging
import os
import threading
import time

logger = logging.getLogger(__name__)


def _rpm():
    raw = os.environ.get("TSP_QUANTDASH_RPM", "12")
    try:
        limit = int(raw)
    except ValueError as exc:
        raise ValueError("TSP_QUANTDASH_RPM must be an integer") from exc
    if not 1 <= limit <= 30:
        raise ValueError("TSP_QUANTDASH_RPM must be in [1,30]")
    return limit


class RollingLimiter:
    """Count actual HTTP requests within a rolling 60s window, including 429s."""

    def __init__(self, limit: int | None = None, *, realtime_reserve=0,
                 clock=time.monotonic, sleep=time.sleep):
        self.limit = limit or _rpm()
        self.realtime_reserve = min(realtime_reserve, self.limit - 1)
        self.clock = clock
        self.sleep = sleep
        self._lock = threading.Lock()
        self._events = deque()

    def acquire(self, *, realtime=False, wait=True):
        while True:
            with self._lock:
                now = self.clock()
                while self._events and now - self._events[0][0] >= 60:
                    self._events.popleft()
                background = [stamp for stamp, priority in self._events if not priority]
                background_full = not realtime and len(background) >= self.limit - self.realtime_reserve
                if len(self._events) < self.limit and not background_full:
                    self._events.append((now, realtime))
                    return True
                if not wait:
                    return False
                releases = []
                if len(self._events) >= self.limit:
                    releases.append(self._events[0][0] + 60)
                if background_full:
                    releases.append(background[0] + 60)
                delay = max(0.01, max(releases) - now + 0.05)
            self.sleep(delay)


class BudgetUnavailableError(RuntimeError):
    """A nonblocking background request cannot use the reserved quote capacity."""


class QuantDashClient:
    def __init__(self, key: str, *, rpm: int | None = None):
        if not key:
            raise ValueError("QuantDash key missing")
        self._key = key
        self._qd = None
        self._lock = threading.RLock()
        limit = rpm or _rpm()
        # Stock, ETF and index snapshots every 30s need six requests/minute.
        self.budget = RollingLimiter(limit, realtime_reserve=6)
        self._request_context = threading.local()
        self._hooked = False

    def _ensure_sdk(self):
        if self._qd is not None:
            return self._qd
        from quantdash import QuantDash

        self._qd = QuantDash(api_key=self._key, max_retries=0)
        # QuantDash 0.1.0: instrument an actual outgoing httpx request. This
        # counts SDK-internal calls as well, unlike a per-high-level-call budget.
        try:
            transport = self._qd._client._client
            hooks = transport.event_hooks
            hooks["request"].append(self._before_http_request)
            self._hooked = True
        except (AttributeError, KeyError, TypeError):
            # A new SDK may hide its transport. Public calls are still limited,
            # but no longer guarantee counting SDK-internal requests.
            logger.warning("QuantDash SDK transport hook unavailable: limiting public calls")
            self._hooked = False
        return self._qd

    def _before_http_request(self, request):  # noqa: ARG002
        realtime = getattr(self._request_context, "realtime", False)
        wait = getattr(self._request_context, "wait", True)
        started = time.monotonic()
        if not self.budget.acquire(realtime=realtime, wait=wait):
            raise BudgetUnavailableError("QuantDash background request budget unavailable; realtime capacity is reserved")
        elapsed = time.monotonic() - started
        if elapsed > 0.5:
            logger.info("QuantDash %s waited %.3fs for request budget",
                        "realtime" if realtime else "background", elapsed)

    def call(self, fn, *, realtime=False, wait=True):
        """HTTPX Client supports threads; only SDK construction needs our lock.

        In particular, a request hook waiting for quota must not hold the SDK
        initialization lock and block independently authorized quote requests.
        """
        with self._lock:
            qd = self._ensure_sdk()
        previous = (getattr(self._request_context, "realtime", False),
                    getattr(self._request_context, "wait", True))
        self._request_context.realtime, self._request_context.wait = realtime, wait
        try:
            if not self._hooked:
                self._before_http_request(None)
            return fn(qd)
        finally:
            self._request_context.realtime, self._request_context.wait = previous

    def close(self):
        with self._lock:
            if self._qd is None:
                return
            close = getattr(self._qd, "close", None)
            if callable(close):
                close()
            self._qd = None


def probe_candidate(key):
    """One narrow live request; never logs key or vendor error body."""
    from quantdash import QuantDash

    client = QuantDash(api_key=key, max_retries=0)
    try:
        frame = client.klines.get(
            "600519.SH", period="1d", count=1, adjust="none", to_dataframe=True,
        )
        if frame is None or (hasattr(frame, "empty") and frame.empty):
            return False, "接口返回空数据：可能是无日K权限或服务端暂不可用"
        return True, "日K试探成功；其他数据集需逐项试拉"
    except Exception:
        return False, "Key 验证未通过；请确认凭据、连通性和日K权限"
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()
