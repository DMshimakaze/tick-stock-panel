"""One interruptible rolling budget, charged by actual HTTP request hooks."""

from collections import deque
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import re
import threading
import time

REQUESTS_PER_MINUTE = 8
RETRY_MARGIN = 1.0


class CollectionStopped(Exception):
    pass


class RequestDeferred(Exception):
    """Return control to the scheduler while a failed batch cools down."""

    def __init__(self, until, *, status=None, reason=None):
        self.until = until
        self.status_code = status
        self.reason = reason


class RollingBudget:
    def __init__(self, limit=REQUESTS_PER_MINUTE, clock=time.monotonic):
        self.limit = limit
        self.clock = clock
        self.sent = deque()
        self.cooldown = 0.0
        self.lock = threading.Lock()

    def delay(self):
        with self.lock:
            now = self.clock()
            while self.sent and now - self.sent[0] >= 60:
                self.sent.popleft()
            return max(
                0,
                self.cooldown - now,
                self.sent[0] + 60 - now if len(self.sent) >= self.limit else 0,
            )

    def acquire(self, stop, report=lambda seconds: None):
        while True:
            if stop.is_set():
                raise CollectionStopped()
            with self.lock:
                now = self.clock()
                while self.sent and now - self.sent[0] >= 60:
                    self.sent.popleft()
                delay = max(
                    0,
                    self.cooldown - now,
                    self.sent[0] + 60 - now if len(self.sent) >= self.limit else 0,
                )
                if delay <= 0:
                    self.sent.append(now)
                    return
            report(delay)
            if stop.wait(delay):
                raise CollectionStopped()

    def defer(self, seconds):
        with self.lock:
            self.cooldown = max(self.cooldown, self.clock() + seconds)
            return self.cooldown


def retry_seconds(exc=None, header=None, now=None):
    """SDK details first, then Retry-After, finally the SDK's ms message."""
    details = (
        exc.get("details") if isinstance(exc, dict) else getattr(exc, "details", None)
    )
    for source in (exc, details):
        for name, divisor in (("retry_after_ms", 1000), ("retry_after", 1)):
            value = (
                source.get(name)
                if isinstance(source, dict)
                else getattr(source, name, None)
            )
            if isinstance(value, (int, float)) and value >= 0:
                return value / divisor
    if header:
        try:
            return max(0, float(header))
        except ValueError:
            try:
                return max(
                    0,
                    (
                        parsedate_to_datetime(header)
                        - (now or datetime.now(timezone.utc))
                    ).total_seconds(),
                )
            except (TypeError, ValueError, OverflowError):
                pass
    match = re.search(r"Retry\s+after\s+(\d+(?:\.\d+)?)\s*(ms|s)\b", str(exc), re.I)
    return float(match[1]) / (1000 if match[2].lower() == "ms" else 1) if match else 0


def rate_limit_delay(exc, header, attempt):
    return max(
        retry_seconds(exc, header) + RETRY_MARGIN,
        min(300, 2 ** min(attempt, 9)),
    )
