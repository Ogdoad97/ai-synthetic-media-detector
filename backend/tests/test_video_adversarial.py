"""
Gate D2: adversarial video inputs. Every malformed case must fail FAST
with a clean ValueError - never crash the worker, hang, or return a
partial "success". Behaviour was probed empirically before asserting.
"""
import os
from unittest.mock import MagicMock, patch

import pytest

from app.services import analyzer
from app.services.analyzer import analyze_video, sample_video_frames
from media_helpers import avi_bytes, mp4_bytes, video_bytes


@pytest.fixture
def write(tmp_path):
    def _write(data: bytes, name: str) -> str:
        p = tmp_path / name
        p.write_bytes(data)
        return str(p)
    return _write


def test_sanity_a_well_formed_video_is_analyzed(write):
    result = analyze_video(write(mp4_bytes(n_frames=10), "ok.mp4"))
    assert result.media_type == "video" and result.frames_analyzed == 10


# --- malformed containers ---------------------------------------------------

def test_garbage_bytes_with_a_video_extension(write):
    with pytest.raises(ValueError):
        analyze_video(write(b"this is not a video" * 100, "junk.mp4"))


def test_empty_file(write):
    with pytest.raises(ValueError):
        analyze_video(write(b"", "empty.mp4"))


def test_header_only_container(write):
    with pytest.raises(ValueError):
        analyze_video(write(mp4_bytes()[:32], "hdr.mp4"))


@pytest.mark.parametrize("keep", [0.5, 0.9])
def test_truncated_container(write, keep):
    data = mp4_bytes(n_frames=10)
    with pytest.raises(ValueError):
        analyze_video(write(data[: int(len(data) * keep)], "trunc.mp4"))


# --- zero frames ------------------------------------------------------------

def test_zero_frame_avi_is_rejected_not_reported_as_success(write):
    with pytest.raises(ValueError, match="No frames could be extracted"):
        analyze_video(write(video_bytes(".avi", "MJPG", n_frames=0), "zero.avi"))


def test_zero_frame_mp4(write):
    with pytest.raises(ValueError):
        analyze_video(write(video_bytes(".mp4", "mp4v", n_frames=0), "zero.mp4"))


# --- codec / container mismatch --------------------------------------------

def test_unknown_codec_fourcc_is_rejected(write):
    avi = avi_bytes(n_frames=5)
    assert avi.count(b"MJPG") >= 1
    with pytest.raises(ValueError):
        analyze_video(write(avi.replace(b"MJPG", b"ZZZZ"), "badcodec.avi"))


def test_extension_does_not_matter_only_content_does(write):
    # AVI content named .mp4 and MP4 content named .avi both decode: the
    # decoder probes content, so a lying extension is neither a bypass nor
    # a source of false rejections.
    assert analyze_video(write(avi_bytes(n_frames=5), "actually_avi.mp4")).frames_analyzed == 5
    assert analyze_video(write(mp4_bytes(n_frames=5), "actually_mp4.avi")).frames_analyzed == 5


# --- oversized frame dimensions (real gap found and fixed in this gate) -----

def _fake_capture(width, height):
    cap = MagicMock()
    cap.isOpened.return_value = True
    props = {
        analyzer.cv2.CAP_PROP_FRAME_WIDTH: width,
        analyzer.cv2.CAP_PROP_FRAME_HEIGHT: height,
        analyzer.cv2.CAP_PROP_FPS: 5.0,
        analyzer.cv2.CAP_PROP_FRAME_COUNT: 10,
    }
    cap.get.side_effect = lambda prop: props.get(prop, 0)
    return cap


def test_oversized_declared_frame_dimensions_are_rejected_before_any_frame_is_decoded():
    cap = _fake_capture(8000, 7000)  # 56 MP > 50 MP limit
    with patch.object(analyzer.cv2, "VideoCapture", return_value=cap):
        with pytest.raises(ValueError, match="megapixel"):
            sample_video_frames("/irrelevant.mp4")
    cap.read.assert_not_called()       # the expensive decode never happened
    cap.release.assert_called()        # and the handle was not leaked


def test_frame_dimensions_at_the_limit_are_still_allowed():
    cap = _fake_capture(5000, 10000)   # exactly 50 MP
    cap.read.return_value = (False, None)
    with patch.object(analyzer.cv2, "VideoCapture", return_value=cap):
        assert sample_video_frames("/irrelevant.mp4") == []
    cap.read.assert_called()


def test_a_real_oversized_video_is_rejected(write, tmp_path):
    import cv2, numpy as np
    p = str(tmp_path / "big.avi")
    w = cv2.VideoWriter(p, cv2.VideoWriter_fourcc(*"MJPG"), 5.0, (8000, 7000))
    w.write(np.full((7000, 8000, 3), 128, np.uint8)); w.release()
    assert os.path.getsize(p) < 5_000_000   # tiny on disk, huge once decoded
    with pytest.raises(ValueError, match="megapixel"):
        analyze_video(p)


# --- error text hygiene -----------------------------------------------------

def test_open_failure_message_does_not_contain_the_internal_path(write):
    path = write(b"junk", "secretname_1234.mp4")
    with pytest.raises(ValueError) as ei:
        sample_video_frames(path)
    assert "secretname_1234" not in str(ei.value)
    assert os.path.dirname(path) not in str(ei.value)
