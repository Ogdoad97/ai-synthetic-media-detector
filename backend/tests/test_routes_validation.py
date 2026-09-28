"""
Gate D2 route-level tests: the API must decide from the bytes, ignore the
client's Content-Type, and never leak internals in error responses.
Celery .delay() is mocked so no Redis broker is required.
"""
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.rate_limit import image_limiter, url_limiter, video_limiter
from app.main import app
from media_helpers import jpeg_bytes, mp4_bytes, png_bytes

client = TestClient(app, raise_server_exceptions=False)
settings = get_settings()


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    image_limiter.reset(); video_limiter.reset(); url_limiter.reset()
    monkeypatch.setattr(settings, "TEMP_MEDIA_DIR", str(tmp_path))
    return tmp_path


class TestImageUpload:
    def test_spoofed_content_type_with_non_image_bytes_is_rejected(self):
        res = client.post("/v1/analyze/image",
                          files={"file": ("x.jpg", b"<html>not an image</html>", "image/jpeg")})
        assert res.status_code == 415

    def test_executable_labelled_as_png_is_rejected(self):
        res = client.post("/v1/analyze/image",
                          files={"file": ("x.png", b"MZ\x90\x00\x03\x00\x00\x00" + b"\x00" * 64, "image/png")})
        assert res.status_code == 415

    def test_video_bytes_labelled_as_image_are_rejected(self):
        res = client.post("/v1/analyze/image",
                          files={"file": ("x.jpg", mp4_bytes(), "image/jpeg")})
        assert res.status_code == 415

    def test_real_image_with_wrong_or_missing_content_type_is_accepted(self):
        # Proves the header is no longer the gate in EITHER direction.
        for ctype in ("application/octet-stream", "text/plain", "image/gif"):
            res = client.post("/v1/analyze/image", files={"file": ("x.bin", jpeg_bytes(), ctype)})
            assert res.status_code == 200, ctype

    def test_rejection_does_not_echo_the_client_supplied_content_type(self):
        res = client.post("/v1/analyze/image",
                          files={"file": ("x", b"junk", "image/<script>alert(1)</script>")})
        assert res.status_code == 415
        assert "script" not in res.text

    def test_correctly_signed_but_undecodable_image_returns_generic_500_not_the_exception(self):
        # Valid JPEG magic, garbage body: passes the signature gate, fails in the decoder.
        corrupt = b"\xff\xd8\xff\xe0" + b"\x00" * 64
        res = client.post("/v1/analyze/image", files={"file": ("x.jpg", corrupt, "image/jpeg")})
        assert res.status_code == 500
        assert res.json()["detail"].startswith("Image analysis failed")

    def test_internal_exception_text_and_paths_never_reach_the_client(self):
        secret = "boom at /srv/app/secret_module.py line 42: db password=hunter2"
        with patch("app.api.routes.analyze_image", side_effect=RuntimeError(secret)):
            res = client.post("/v1/analyze/image", files={"file": ("x.jpg", jpeg_bytes(), "image/jpeg")})
        assert res.status_code == 500
        for leaked in ("hunter2", "/srv/app", "secret_module", "RuntimeError", "Traceback"):
            assert leaked not in res.text


class TestVideoUpload:
    def test_image_bytes_labelled_as_video_are_rejected_and_nothing_is_queued_or_written(self, _isolate):
        with patch("app.api.routes.process_video_task") as task:
            res = client.post("/v1/analyze/video",
                              files={"file": ("x.mp4", jpeg_bytes(), "video/mp4")})
        assert res.status_code == 415
        task.delay.assert_not_called()
        assert list(_isolate.iterdir()) == []

    def test_arbitrary_bytes_labelled_as_video_are_rejected(self, _isolate):
        with patch("app.api.routes.process_video_task") as task:
            res = client.post("/v1/analyze/video",
                              files={"file": ("x.mp4", b"fake video bytes", "video/mp4")})
        assert res.status_code == 415
        task.delay.assert_not_called()

    def test_real_video_is_accepted_regardless_of_declared_content_type(self, _isolate):
        with patch("app.api.routes.process_video_task") as task:
            task.delay.return_value = MagicMock(id="tid")
            res = client.post("/v1/analyze/video",
                              files={"file": ("clip.mp4", mp4_bytes(), "application/octet-stream")})
        assert res.status_code == 202
        task.delay.assert_called_once()
        assert len(list(_isolate.iterdir())) == 1

    def test_crafted_filename_cannot_influence_the_write_path(self, _isolate):
        with patch("app.api.routes.process_video_task") as task:
            task.delay.return_value = MagicMock(id="tid")
            res = client.post("/v1/analyze/video",
                              files={"file": ("../../../../tmp/evil.mp4", mp4_bytes(), "video/mp4")})
        assert res.status_code == 202
        written = list(_isolate.iterdir())
        assert len(written) == 1
        assert written[0].parent == _isolate            # stayed inside the temp dir
        assert "evil" not in written[0].name            # name is a fresh uuid, not client text


class TestGlobalErrorHandler:
    def test_unhandled_exception_returns_fixed_generic_body(self):
        secret = "KeyError: '/var/lib/redis/dump.rdb' unreachable, token=abc123"
        with patch("app.api.routes.AsyncResult", side_effect=RuntimeError(secret)):
            res = client.get("/v1/tasks/some-task-id")
        assert res.status_code == 500
        assert res.json() == {"detail": "Internal server error."}
        assert "abc123" not in res.text and "/var/lib" not in res.text


class TestUploadSizeLimit:
    """Oversized uploads must be rejected without being read in full."""

    @pytest.fixture
    def read_sizes(self, monkeypatch):
        from starlette.datastructures import UploadFile
        seen = []
        original = UploadFile.read

        async def spy(self, size=-1):
            seen.append(size)
            return await original(self, size)

        monkeypatch.setattr(UploadFile, "read", spy)
        return seen

    def test_oversized_image_gets_413_and_read_is_bounded(self, monkeypatch, read_sizes):
        monkeypatch.setattr(settings, "MAX_UPLOAD_SIZE_MB", 1)
        res = client.post("/v1/analyze/image",
                          files={"file": ("big.jpg", b"\xff\xd8\xff" + b"0" * (3 * 1024 * 1024), "image/jpeg")})
        assert res.status_code == 413
        # Never an unbounded read(): capped at limit + 1 byte.
        assert read_sizes and all(s == 1024 * 1024 + 1 for s in read_sizes)

    def test_oversized_video_gets_413_before_anything_is_written_or_queued(self, monkeypatch, _isolate, read_sizes):
        monkeypatch.setattr(settings, "MAX_UPLOAD_SIZE_MB", 1)
        with patch("app.api.routes.process_video_task") as task:
            res = client.post("/v1/analyze/video",
                              files={"file": ("big.mp4", mp4_bytes() + b"0" * (3 * 1024 * 1024), "video/mp4")})
        assert res.status_code == 413
        task.delay.assert_not_called()
        assert list(_isolate.iterdir()) == []
        assert all(s == 1024 * 1024 + 1 for s in read_sizes)

    def test_file_exactly_at_the_limit_is_accepted(self, monkeypatch):
        data = jpeg_bytes()
        monkeypatch.setattr(settings, "MAX_UPLOAD_SIZE_MB", 1)
        padded = data + b"\x00" * (1024 * 1024 - len(data))   # exactly 1 MiB
        assert len(padded) == 1024 * 1024
        res = client.post("/v1/analyze/image", files={"file": ("x.jpg", padded, "image/jpeg")})
        assert res.status_code == 200
