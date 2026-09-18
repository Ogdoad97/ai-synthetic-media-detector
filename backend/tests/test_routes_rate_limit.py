"""
Route-level test proving the rate limiter is actually enforced by the live
FastAPI endpoints - not just correct in isolation (see test_rate_limit.py).
Celery's .delay() is mocked so this doesn't need a real Redis broker running.
"""
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.core.rate_limit import image_limiter, url_limiter, video_limiter
from app.main import app

client = TestClient(app)


def _reset_limiters():
    image_limiter.reset()
    video_limiter.reset()
    url_limiter.reset()


def _tiny_jpeg_bytes() -> bytes:
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (16, 16), color=(10, 20, 30)).save(buf, format="JPEG")
    return buf.getvalue()


class TestImageEndpointRateLimit:
    def setup_method(self):
        _reset_limiters()

    def test_requests_within_the_limit_succeed(self):
        jpeg = _tiny_jpeg_bytes()
        for _ in range(3):
            res = client.post("/v1/analyze/image", files={"file": ("t.jpg", jpeg, "image/jpeg")})
            assert res.status_code == 200

    def test_exceeding_the_limit_returns_429(self):
        jpeg = _tiny_jpeg_bytes()
        # image_limiter allows 30/min - drive it past that.
        last_status = None
        for _ in range(31):
            last_status = client.post(
                "/v1/analyze/image", files={"file": ("t.jpg", jpeg, "image/jpeg")}
            ).status_code
        assert last_status == 429


class TestVideoEndpointRateLimit:
    def setup_method(self):
        _reset_limiters()

    def test_exceeding_the_limit_returns_429_without_ever_queuing_a_real_task(self):
        with patch("app.api.routes.process_video_task") as mock_task:
            mock_task.delay.return_value = MagicMock(id="fake-task-id")
            last_status = None
            for _ in range(11):  # video_limiter allows 10/min
                last_status = client.post(
                    "/v1/analyze/video",
                    files={"file": ("t.mp4", b"fake video bytes", "video/mp4")},
                ).status_code
            assert last_status == 429
            # Confirms the limiter runs BEFORE the expensive/queued work,
            # not after - only the allowed requests should have reached
            # the point of actually queuing a Celery task.
            assert mock_task.delay.call_count == 10


class TestUrlEndpointRateLimit:
    def setup_method(self):
        _reset_limiters()

    def test_exceeding_the_limit_returns_429(self):
        with patch("app.api.routes.process_url_task") as mock_task:
            mock_task.delay.return_value = MagicMock(id="fake-task-id")
            last_status = None
            for _ in range(11):  # url_limiter allows 10/min
                last_status = client.post(
                    "/v1/analyze/url", json={"url": "https://example.com/photo.jpg"}
                ).status_code
            assert last_status == 429
