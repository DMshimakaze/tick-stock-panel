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

    def __init__(self, limit: int | None = None, *, clock=time.monotonic, sleep=time.sleep):
        self.limit = limit or _rpm()
        self.clock = clock
        self.sleep = sleep
        self._lock = threading.Lock()
        self._events = deque()

    def acquire(self):
        while True:
            with self._lock:
                now = self.clock()
                while self._events and now - self._events[0] >= 60:
                    self._events.popleft()
                if len(self._events) < self.limit:
                    self._events.append(now)
                    return
                delay = max(0.01, 60 - (now - self._events[0]) + 0.05)
            self.sleep(delay)


class QuantDashClient:
    def __init__(self, key: str, *, rpm: int | None = None):
        if not key:
            raise ValueError("QuantDash key missing")
        self._key = key
        self._qd = None
        self._lock = threading.RLock()
        self.budget = RollingLimiter(rpm)
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
        self.budget.acquire()

    def call(self, fn):
        """Single SDK call. The lock ensures shared SDK session safety."""
        with self._lock:
            qd = self._ensure_sdk()
            if not self._hooked:
                self.budget.acquire()
            return fn(qd)

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
