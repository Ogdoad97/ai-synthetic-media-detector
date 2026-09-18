"""
Audit & verification suite for the v0.2.0 forensic baseline analyzer.

These tests enforce the contracts documented in DEVELOPMENT_STATUS_v0.2.0.md:
- deterministic, model-free signals
- explicit uncertainty labels
- structured findings + heatmap
- rejection of invalid inputs
- video sampler guards

They do NOT claim scientific validity of the forensic heuristics.
"""
from __future__ import annotations

import io
import re

import numpy as np
import pytest
from PIL import Image

from app.services.analyzer import (
    analyze_image,
    sample_video_frames,
    _score_label,
    _fft_score,
    _jpeg_recompression_score,
    _normalized_entropy,
    _decode_image,
)


def make_jpeg(arr: np.ndarray, quality: int = 90) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(arr.astype(np.uint8)).save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def noise_image(seed: int = 7, size: int = 128) -> bytes:
    rng = np.random.default_rng(seed)
    return make_jpeg(rng.integers(0, 256, (size, size, 3)))


def smooth_gradient(size: int = 128) -> bytes:
    x = np.linspace(0, 255, size, dtype=np.float32)
    y = np.linspace(0, 255, size, dtype=np.float32)
    xx, yy = np.meshgrid(x, y)
    gray = ((xx + yy) / 2).astype(np.uint8)
    rgb = np.stack([gray, gray, gray], axis=-1)
    return make_jpeg(rgb)


def test_analyze_image_is_deterministic_and_structured():
    data = noise_image(seed=7)
    a = analyze_image(data, include_heatmap=True).to_dict()
    b = analyze_image(data, include_heatmap=True).to_dict()

    assert a["overall_score"] == b["overall_score"]
    assert 0.0 <= a["overall_score"] <= 1.0
    assert a["score_label"] in {"authentic_signal", "uncertain", "synthetic_signal"}
    assert a["media_type"] == "image"
    assert a["frames_analyzed"] == 1
    assert a["heatmap_base64"].startswith("data:image/jpeg;base64,")
    assert len(a["findings"]) >= 3
    assert abs(a["confidence_percentage"] - a["overall_score"] * 100) < 0.2


def test_invalid_image_is_rejected():
    with pytest.raises(ValueError, match="not a decodable image"):
        analyze_image(b"not-an-image", include_heatmap=False)


def test_empty_bytes_rejected():
    with pytest.raises(ValueError):
        analyze_image(b"", include_heatmap=False)


def test_video_sampler_rejects_invalid_fps():
    with pytest.raises(ValueError, match="target_fps"):
        sample_video_frames("/does/not/exist.mp4", target_fps=0)


def test_video_sampler_rejects_invalid_max_frames():
    with pytest.raises(ValueError, match="max_frames"):
        sample_video_frames("/does/not/exist.mp4", target_fps=5.0, max_frames=0)


def test_video_sampler_rejects_missing_file():
    with pytest.raises(ValueError, match="Cannot open video"):
        sample_video_frames("/does/not/exist.mp4", target_fps=5.0, max_frames=10)


@pytest.mark.parametrize(
    "score,expected",
    [
        (0.0, "authentic_signal"),
        (0.39, "authentic_signal"),
        (0.40, "uncertain"),
        (0.50, "uncertain"),
        (0.60, "uncertain"),
        (0.61, "synthetic_signal"),
        (1.0, "synthetic_signal"),
    ],
)
def test_score_label_boundaries(score: float, expected: str):
    assert _score_label(score) == expected


def test_is_ai_generated_threshold_matches_label():
    for seed in range(5):
        result = analyze_image(noise_image(seed=seed), include_heatmap=False)
        if result.score_label == "synthetic_signal":
            assert result.is_ai_generated is True
            assert result.overall_score > 0.60
        else:
            assert result.is_ai_generated is False
            assert result.overall_score <= 0.60


def test_fft_score_returns_valid_range_and_map():
    gray = np.random.default_rng(1).integers(0, 256, (200, 200), dtype=np.uint8)
    score, artifact = _fft_score(gray)
    assert 0.0 <= score <= 1.0
    assert artifact.ndim == 2
    assert artifact.min() >= 0.0
    assert artifact.max() <= 1.0 + 1e-6


def test_jpeg_recompression_score_range():
    img = np.random.default_rng(2).integers(0, 256, (100, 100, 3), dtype=np.uint8)
    score = _jpeg_recompression_score(img)
    assert 0.0 <= score <= 1.0


def test_normalized_entropy_range():
    solid = np.full((64, 64), 128, dtype=np.uint8)
    noisy = np.random.default_rng(3).integers(0, 256, (64, 64), dtype=np.uint8)
    e_solid = _normalized_entropy(solid)
    e_noisy = _normalized_entropy(noisy)
    assert 0.0 <= e_solid <= 1.0
    assert 0.0 <= e_noisy <= 1.0
    assert e_solid < e_noisy


def test_smooth_image_tends_lower_than_noise():
    smooth_scores = [
        analyze_image(smooth_gradient(), include_heatmap=False).overall_score
        for _ in range(3)
    ]
    noise_scores = [
        analyze_image(noise_image(seed=s), include_heatmap=False).overall_score
        for s in range(10, 13)
    ]
    assert np.mean(smooth_scores) <= np.mean(noise_scores) + 0.15


REQUIRED_LAYERS = {"Frequency / FFT", "JPEG / Recompression", "Pixel Entropy", "C2PA / Provenance"}


def test_findings_cover_required_layers():
    result = analyze_image(noise_image(), include_heatmap=False)
    layers = {f["layer"] for f in result.findings}
    assert REQUIRED_LAYERS.issubset(layers)
    for f in result.findings:
        assert f["status"] in {"passed", "warning", "failed"}
        assert isinstance(f["detail"], str) and len(f["detail"]) > 0


def test_heatmap_optional():
    with_hm = analyze_image(noise_image(), include_heatmap=True)
    without = analyze_image(noise_image(), include_heatmap=False)
    assert with_hm.heatmap_base64 is not None
    assert without.heatmap_base64 is None
    assert with_hm.overall_score == without.overall_score


def test_heatmap_is_valid_data_uri():
    result = analyze_image(noise_image(), include_heatmap=True)
    uri = result.heatmap_base64
    assert uri.startswith("data:image/jpeg;base64,")
    b64 = uri.split(",", 1)[1]
    assert re.fullmatch(r"[A-Za-z0-9+/=]+", b64)
    assert len(b64) > 100


def test_decode_rejects_garbage():
    with pytest.raises(ValueError):
        _decode_image(b"\x00\x01\x02\x03")


def test_png_also_accepted():
    arr = np.random.default_rng(9).integers(0, 256, (64, 64, 3), dtype=np.uint8)
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="PNG")
    result = analyze_image(buf.getvalue(), include_heatmap=False)
    assert 0.0 <= result.overall_score <= 1.0
    assert result.media_type == "image"


def test_to_dict_keys_stable():
    d = analyze_image(noise_image(), include_heatmap=True).to_dict()
    required = {
        "is_ai_generated",
        "confidence_percentage",
        "overall_score",
        "score_label",
        "media_type",
        "findings",
        "heatmap_base64",
        "frames_analyzed",
        "video_duration_sec",
        "c2pa_status",
        "frame_results",
    }
    assert required.issubset(d.keys())
    assert isinstance(d["findings"], list)
    assert isinstance(d["frame_results"], list)


def oversized_solid_color_jpeg(width: int = 10000, height: int = 10000) -> bytes:
    """
    A classic decompression-bomb shape: huge pixel dimensions but a solid
    color, so it compresses to a tiny file (JPEG loves uniform regions) -
    this is exactly the "few KB on disk, huge once decoded" pattern the
    megapixel guard exists to catch. 10000x10000 = 100 megapixels, over
    the 50MP limit.
    """
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color=(128, 128, 128)).save(buf, format="JPEG", quality=50)
    return buf.getvalue()


def test_decode_rejects_an_oversized_image_before_full_decode():
    bomb = oversized_solid_color_jpeg()
    # Confirms the actual attack shape: the compressed file is a small
    # fraction of what the raw decoded bitmap would be (10000x10000x3
    # bytes uncompressed = ~300MB) - that gap is exactly what the
    # megapixel guard exists to catch before it's ever allocated.
    assert len(bomb) < 2_000_000
    with pytest.raises(ValueError, match="megapixel"):
        _decode_image(bomb)


def test_decode_accepts_an_image_within_the_megapixel_limit():
    # A normal-sized image (128x128, well under 50MP) must still work -
    # the guard shouldn't be so aggressive it rejects real photos.
    _decode_image(noise_image(size=128))  # should not raise


def test_analyze_image_surfaces_the_megapixel_rejection_as_a_clean_error():
    bomb = oversized_solid_color_jpeg()
    with pytest.raises(ValueError, match="megapixel"):
        analyze_image(bomb)
