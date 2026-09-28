"""
Gate D2: Celery task failures must surface only client-safe text, and URL
media routing must come from real content, not the URL's extension.

Tasks are invoked via `.run(...)` with update_state stubbed, so no broker
or result backend is needed.
"""
from unittest.mock import patch

import pytest

from app.core.config import get_settings
from app.workers import tasks
from app.workers.tasks import (
    MediaAnalysisError,
    process_image_task,
    process_url_task,
    process_video_task,
)
from media_helpers import jpeg_bytes, mp4_bytes

settings = get_settings()


@pytest.fixture(autouse=True)
def _stub_state_and_tempdir(tmp_path, monkeypatch):
    monkeypatch.setattr(tasks.settings, "TEMP_MEDIA_DIR", str(tmp_path))
    with patch.object(process_video_task, "update_state"), \
         patch.object(process_image_task, "update_state"), \
         patch.object(process_url_task, "update_state"):
        yield tmp_path


class TestFailureMessagesAreClientSafe:
    def test_video_task_failure_has_no_path_or_library_text(self, tmp_path):
        f = tmp_path / "secretname_9876.mp4"
        f.write_bytes(b"not a video")
        with pytest.raises(MediaAnalysisError) as ei:
            process_video_task.run(str(f))
        msg = str(ei.value)
        assert msg.startswith("Video analysis failed")
        assert "secretname_9876" not in msg and str(tmp_path) not in msg
        assert isinstance(ei.value.__cause__, ValueError)   # real cause kept for logs
        assert not f.exists()                                # temp file still cleaned up

    def test_video_task_missing_file_has_no_path(self, tmp_path):
        missing = str(tmp_path / "nope_4321.mp4")
        with pytest.raises(MediaAnalysisError) as ei:
            process_video_task.run(missing)
        assert "nope_4321" not in str(ei.value)

    def test_image_task_failure_is_generic_and_cleans_up(self, tmp_path):
        f = tmp_path / "img_5555.jpg"
        f.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 32)
        with pytest.raises(MediaAnalysisError) as ei:
            process_image_task.run(str(f))
        assert "img_5555" not in str(ei.value)
        assert not f.exists()

    def test_arbitrary_internal_exception_text_is_replaced(self, tmp_path):
        f = tmp_path / "v.mp4"
        f.write_bytes(mp4_bytes())
        with patch("app.workers.tasks.analyze_video",
                   side_effect=RuntimeError("cuda OOM at /opt/model/weights.pt")):
            with pytest.raises(MediaAnalysisError) as ei:
                process_video_task.run(str(f))
        assert "/opt/model" not in str(ei.value) and "cuda" not in str(ei.value)


class TestUrlTaskRoutesByRealContent:
    @staticmethod
    def _fake_download(content: bytes):
        def _dl(url, dest):
            dest.write_bytes(content)
        return _dl

    def test_jpeg_served_from_an_mp4_url_is_analyzed_as_an_image(self):
        with patch("app.workers.tasks.safe_download", self._fake_download(jpeg_bytes())):
            out = process_url_task.run("https://example.com/totally_a_video.mp4")
        assert out["media_type"] == "image"

    def test_mp4_served_from_a_jpg_url_is_analyzed_as_a_video(self):
        with patch("app.workers.tasks.safe_download", self._fake_download(mp4_bytes(n_frames=5))):
            out = process_url_task.run("https://example.com/photo.jpg")
        assert out["media_type"] == "video"

    def test_non_media_content_is_rejected_with_a_safe_message(self):
        with patch("app.workers.tasks.safe_download",
                   self._fake_download(b"<html>login page</html>")):
            with pytest.raises(MediaAnalysisError) as ei:
                process_url_task.run("https://example.com/photo.jpg")
        assert str(ei.value) == "Remote content does not match a supported image or video format."

    def test_url_task_temp_file_is_removed_after_rejection(self, _stub_state_and_tempdir):
        with patch("app.workers.tasks.safe_download", self._fake_download(b"garbage")):
            with pytest.raises(MediaAnalysisError):
                process_url_task.run("https://example.com/x.jpg")
        assert list(_stub_state_and_tempdir.iterdir()) == []
