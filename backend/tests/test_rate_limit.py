from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.core.rate_limit import RateLimiter


def make_request(host="1.2.3.4"):
    req = MagicMock()
    req.client.host = host
    return req


class TestRateLimiter:
    def test_allows_requests_under_the_limit(self):
        limiter = RateLimiter(max_requests=3, window_seconds=60)
        req = make_request()
        for _ in range(3):
            limiter.check(req)  # should not raise

    def test_blocks_the_request_that_exceeds_the_limit(self):
        limiter = RateLimiter(max_requests=3, window_seconds=60)
        req = make_request()
        for _ in range(3):
            limiter.check(req)
        with pytest.raises(HTTPException) as exc_info:
            limiter.check(req)
        assert exc_info.value.status_code == 429

    def test_tracks_clients_independently(self):
        limiter = RateLimiter(max_requests=2, window_seconds=60)
        req_a = make_request("1.1.1.1")
        req_b = make_request("2.2.2.2")
        limiter.check(req_a)
        limiter.check(req_a)
        # req_a is now at its limit, but req_b is a different client and
        # should be unaffected.
        limiter.check(req_b)
        with pytest.raises(HTTPException):
            limiter.check(req_a)

    def test_window_expiry_allows_requests_again(self, monkeypatch):
        limiter = RateLimiter(max_requests=1, window_seconds=10)
        req = make_request()

        fake_time = [1000.0]
        monkeypatch.setattr("app.core.rate_limit.time.monotonic", lambda: fake_time[0])

        limiter.check(req)
        with pytest.raises(HTTPException):
            limiter.check(req)

        fake_time[0] += 11  # advance past the window
        limiter.check(req)  # should succeed again now

    def test_missing_client_falls_back_to_a_shared_bucket_not_a_crash(self):
        limiter = RateLimiter(max_requests=1, window_seconds=60)
        req = MagicMock()
        req.client = None
        limiter.check(req)  # should not raise
        with pytest.raises(HTTPException):
            limiter.check(req)

    def test_reset_clears_accumulated_state(self):
        limiter = RateLimiter(max_requests=1, window_seconds=60)
        req = make_request()
        limiter.check(req)
        limiter.reset()
        limiter.check(req)  # should not raise - state was cleared
