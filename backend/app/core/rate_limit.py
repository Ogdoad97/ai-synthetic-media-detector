"""
Minimal in-memory rate limiter.

Every analysis endpoint here does real, non-trivial compute (FFT, JPEG
recompression, image decode) and the URL endpoint additionally makes an
outbound network request on the caller's behalf - both are real abuse/cost
vectors with zero limiting previously in place. This is intentionally a
simple fixed-window counter, not a distributed one: it's process-local
in-memory state, which is fine for a single API instance but will under-
count (allow more than the configured limit) once there's more than one
API replica behind a load balancer, since each replica keeps its own
counts. A production multi-instance deployment should replace the store
with Redis (already a dependency here via Celery) rather than add a new
one - noted as a follow-up, not implemented here to keep this fix small
and independently testable.
"""
from __future__ import annotations

import time
from collections import defaultdict
from threading import Lock
from typing import Dict, Tuple

from fastapi import HTTPException, Request, status


class RateLimiter:
    def __init__(self, max_requests: int, window_seconds: float):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._hits: Dict[str, list] = defaultdict(list)
        self._lock = Lock()

    def _client_key(self, request: Request) -> str:
        # Falls back to a constant key if no client host is available
        # (some test clients) - degrades to a single shared bucket rather
        # than crashing.
        if request.client and request.client.host:
            return request.client.host
        return "unknown"

    def check(self, request: Request) -> None:
        key = self._client_key(request)
        now = time.monotonic()
        cutoff = now - self.window_seconds

        with self._lock:
            hits = self._hits[key]
            # Drop expired entries for this key - keeps memory bounded for
            # long-running processes rather than growing forever.
            while hits and hits[0] < cutoff:
                hits.pop(0)

            if len(hits) >= self.max_requests:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=(
                        f"Rate limit exceeded: max {self.max_requests} requests "
                        f"per {self.window_seconds:.0f}s"
                    ),
                )
            hits.append(now)

    def reset(self) -> None:
        """Test-only: clears all accumulated state without touching limits."""
        with self._lock:
            self._hits.clear()


# Separate limiters per endpoint class - image analysis is cheap-ish CPU
# work, video/URL both involve either much heavier compute or an outbound
# network call, so they get tighter limits.
image_limiter = RateLimiter(max_requests=30, window_seconds=60)
video_limiter = RateLimiter(max_requests=10, window_seconds=60)
url_limiter = RateLimiter(max_requests=10, window_seconds=60)
