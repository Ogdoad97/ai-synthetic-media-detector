"""Gate D3: every API result names the detector that produced it, and the thresholds are wired to it."""
import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.rate_limit import image_limiter, url_limiter, video_limiter
from app.main import app
from app.services import analyzer
from app.services.analyzer import analyze_image, analyze_video, detector_info
from media_helpers import jpeg_bytes, mp4_bytes

client = TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _reset():
    image_limiter.reset(); video_limiter.reset(); url_limiter.reset()


def test_sync_image_endpoint_returns_detector_provenance():
    res = client.post("/v1/analyze/image", files={"file": ("x.jpg", jpeg_bytes(), "image/jpeg")})
    assert res.status_code == 200
    d = res.json()["detector"]
    assert d["name"] == "forensic-baseline"
    assert d["version"] == get_settings().VERSION
    assert d["kind"] == "heuristic"
    assert d["validated"] is False                     # flips only with a published harness report
    assert d["synthetic_threshold"] == analyzer.SYNTHETIC_THRESHOLD
    assert d["uncertain_band"] == [analyzer.UNCERTAIN_LOW, analyzer.SYNTHETIC_THRESHOLD]


def test_video_result_carries_the_same_provenance(tmp_path):
    p = tmp_path / "v.mp4"; p.write_bytes(mp4_bytes(n_frames=6))
    assert analyze_video(str(p)).to_dict()["detector"] == detector_info()


def test_thresholds_in_provenance_are_the_ones_the_decision_actually_uses(monkeypatch):
    monkeypatch.setattr(analyzer, "SYNTHETIC_THRESHOLD", 0.9)
    monkeypatch.setattr(analyzer, "UNCERTAIN_LOW", 0.7)
    assert analyzer._score_label(0.8) == "uncertain"           # 0.8 sits in the patched band
    assert analyzer._score_label(0.95) == "synthetic_signal"
    assert analyzer._score_label(0.6) == "authentic_signal"
    info = detector_info()
    assert info["synthetic_threshold"] == 0.9 and info["uncertain_band"] == [0.7, 0.9]


def test_is_ai_generated_follows_the_named_threshold(monkeypatch):
    baseline = analyze_image(jpeg_bytes(size=(64, 64)), include_heatmap=False)
    s = baseline.overall_score
    monkeypatch.setattr(analyzer, "SYNTHETIC_THRESHOLD", max(0.0, s - 1e-6))
    assert analyze_image(jpeg_bytes(size=(64, 64)), include_heatmap=False).is_ai_generated is (s > max(0.0, s - 1e-6))
    monkeypatch.setattr(analyzer, "SYNTHETIC_THRESHOLD", min(1.0, s + 1e-6))
    assert analyze_image(jpeg_bytes(size=(64, 64)), include_heatmap=False).is_ai_generated is False


def test_provenance_is_a_copy_not_shared_mutable_state():
    a = analyze_image(jpeg_bytes(), include_heatmap=False)
    a.detector["validated"] = True
    assert analyze_image(jpeg_bytes(), include_heatmap=False).detector["validated"] is False
