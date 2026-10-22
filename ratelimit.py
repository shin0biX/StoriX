"""Small in-process rate limiter for credential endpoints.

This is intentionally dependency-free. For multi-worker deployments, replace
it with a shared store (Redis) so the limit is global rather than per worker.
"""
from __future__ import annotations

from collections import defaultdict, deque
from threading import Lock
from time import monotonic


class SlidingWindowLimiter:
    def __init__(self, limit: int, window_seconds: int) -> None:
        self.limit = max(1, limit)
        self.window = max(1, window_seconds)
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def hit(self, key: str) -> int | None:
        """Record an attempt. Return retry-after seconds when blocked."""
        now = monotonic()
        cutoff = now - self.window
        with self._lock:
            bucket = self._hits[key]
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= self.limit:
                return max(1, int(bucket[0] + self.window - now) + 1)
            bucket.append(now)
            return None

    def reset(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)


def client_key(request, suffix: str = "") -> str:  # noqa: ANN001
    host = request.client.host if request.client else "unknown"
    return f"{host}:{suffix}" if suffix else host
