"""
Synthetic-media analysis service.

v0.2 baseline:
- deterministic, model-free forensic signals (FFT + recompression/noise heuristics)
- optional learned model hook for future real weights
- explainability heatmap based on the forensic artifact map
- explicit uncertainty: scores are signals, not proof

A trained detector can be plugged in later without changing the API contract.
"""
from __future__ import annotations

import base64
import io
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

# A compressed file of a few KB can still decode to a bitmap of several GB
# (classic decompression-bomb DoS) - the upload-size cap alone only limits
# the *compressed* size. 50 megapixels comfortably covers real photos
# (a 48MP phone camera is ~48MP) while rejecting adversarially crafted
# dimensions.
MAX_DECODED_MEGAPIXELS = 50


@dataclass
class FrameResult:
    frame_index: int
    timestamp_sec: float
    is_ai_generated: bool
    confidence: float
    max_artifact_intensity: float
    heatmap_base64: Optional[str] = None


@dataclass
class AnalysisResult:
    is_ai_generated: bool
    confidence_percentage: float
    overall_score: float
    media_type: str
    findings: List[Dict[str, Any]] = field(default_factory=list)
    heatmap_base64: Optional[str] = None
    frame_results: List[FrameResult] = field(default_factory=list)
    frames_analyzed: int = 0
    video_duration_sec: Optional[float] = None
    c2pa_status: str = "unknown"
    score_label: str = "uncertain"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_ai_generated": self.is_ai_generated,
            "confidence_percentage": round(self.confidence_percentage, 2),
            "overall_score": round(self.overall_score, 4),
            "score_label": self.score_label,
            "media_type": self.media_type,
            "findings": self.findings,
            "heatmap_base64": self.heatmap_base64,
            "frames_analyzed": self.frames_analyzed,
            "video_duration_sec": self.video_duration_sec,
            "c2pa_status": self.c2pa_status,
            "frame_results": [
                {
                    "frame_index": fr.frame_index,
                    "timestamp_sec": round(fr.timestamp_sec, 3),
                    "is_ai_generated": fr.is_ai_generated,
                    "confidence": round(fr.confidence, 4),
                    "max_artifact_intensity": round(fr.max_artifact_intensity, 4),
                }
                for fr in self.frame_results
            ],
        }


def _decode_image(image_bytes: bytes) -> np.ndarray:
    if not image_bytes:
        raise ValueError("Input is not a decodable image.")
    arr = np.frombuffer(image_bytes, dtype=np.uint8)
    if arr.size == 0:
        raise ValueError("Input is not a decodable image.")

    # Check dimensions from the header ALONE before doing a full decode.
    # PIL.Image.open() is lazy - it reads only enough of the file to
    # determine format/size, it does not decode pixel data until you call
    # .load()/.getdata()/etc. This lets us reject an oversized image before
    # the expensive full decode (cv2.imdecode below) ever allocates the
    # full-resolution buffer - a compressed file of a few KB can still
    # decode to a bitmap of several GB (a classic decompression-bomb DoS),
    # and the upload-size cap alone only limits the *compressed* size, not
    # the decoded one.
    try:
        with Image.open(io.BytesIO(image_bytes)) as probe:
            width, height = probe.size
    except Exception as exc:
        raise ValueError("Input is not a decodable image.") from exc

    megapixels = (height * width) / 1_000_000
    if megapixels > MAX_DECODED_MEGAPIXELS:
        raise ValueError(
            f"Image is {megapixels:.1f} megapixels, exceeding the "
            f"{MAX_DECODED_MEGAPIXELS} megapixel safety limit."
        )

    image = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("Input is not a decodable image.")
    return image


def _normalized_entropy(gray: np.ndarray) -> float:
    hist = cv2.calcHist([gray], [0], None, [256], [0, 256]).ravel()
    p = hist / max(hist.sum(), 1.0)
    p = p[p > 0]
    entropy = float(-(p * np.log2(p)).sum())
    return min(1.0, entropy / 8.0)


def _fft_score(gray: np.ndarray) -> Tuple[float, np.ndarray]:
    """Measure excess high-frequency energy and return a normalized artifact map."""
    small = cv2.resize(gray, (256, 256), interpolation=cv2.INTER_AREA).astype(np.float32)
    small = small - cv2.GaussianBlur(small, (0, 0), 3)
    spectrum = np.fft.fftshift(np.fft.fft2(small))
    magnitude = np.log1p(np.abs(spectrum))
    h, w = magnitude.shape
    yy, xx = np.ogrid[:h, :w]
    radius = np.sqrt((yy - h / 2) ** 2 + (xx - w / 2) ** 2)
    mask = radius > min(h, w) * 0.28
    high = float(magnitude[mask].mean())
    low = float(magnitude[~mask].mean())
    ratio = high / max(low, 1e-6)
    # Map a broad empirical range to [0,1]. This is a signal, not a probability.
    score = float(np.clip((ratio - 0.75) / 0.9, 0.0, 1.0))
    artifact = np.abs(small)
    artifact = cv2.GaussianBlur(artifact, (0, 0), 2)
    artifact -= artifact.min()
    artifact /= max(float(artifact.max()), 1e-6)
    return score, artifact


def _jpeg_recompression_score(image: np.ndarray) -> float:
    """Estimate block/recompression inconsistency from a JPEG round-trip."""
    ok, buf = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), 75])
    if not ok:
        return 0.0
    recon = cv2.imdecode(buf, cv2.IMREAD_GRAYSCALE)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    a = gray.astype(np.float32)
    b = recon.astype(np.float32)
    mae = float(np.mean(np.abs(a - b))) / 255.0
    return float(np.clip(mae / 0.12, 0.0, 1.0))


def _artifact_heatmap(image: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    _, fft_map = _fft_score(gray)
    lap = np.abs(cv2.Laplacian(gray, cv2.CV_32F))
    lap = cv2.GaussianBlur(lap, (0, 0), 1)
    lap -= lap.min()
    lap /= max(float(lap.max()), 1e-6)
    lap = cv2.resize(lap, (fft_map.shape[1], fft_map.shape[0]), interpolation=cv2.INTER_LINEAR)
    return np.clip(0.65 * fft_map + 0.35 * lap, 0.0, 1.0)


def _heatmap_data_uri(image: np.ndarray, heatmap: np.ndarray) -> str:
    h, w = image.shape[:2]
    resized = cv2.resize(heatmap, (w, h), interpolation=cv2.INTER_LINEAR)
    overlay = cv2.applyColorMap(np.uint8(resized * 255), cv2.COLORMAP_JET)
    blended = cv2.addWeighted(image, 0.58, overlay, 0.42, 0)
    ok, buf = cv2.imencode(".jpg", blended, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
    if not ok:
        raise RuntimeError("Failed to encode forensic heatmap.")
    return "data:image/jpeg;base64," + base64.b64encode(buf).decode("ascii")


def _score_label(score: float) -> str:
    if 0.40 <= score <= 0.60:
        return "uncertain"
    return "synthetic_signal" if score > 0.60 else "authentic_signal"


def analyze_image(image_bytes: bytes, include_heatmap: bool = True) -> AnalysisResult:
    """Run deterministic forensic signals over one image."""
    image = _decode_image(image_bytes)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    fft_score, _ = _fft_score(gray)
    recompression = _jpeg_recompression_score(image)
    entropy = _normalized_entropy(gray)

    # Conservative baseline: FFT is strongest; recompression is supporting evidence.
    # Entropy is used only as a weak regularizer.
    score = float(np.clip(0.60 * fft_score + 0.30 * recompression + 0.10 * (1.0 - entropy), 0, 1))
    label = _score_label(score)
    is_ai = score > 0.60

    heatmap_b64 = None
    intensity = 0.0
    if include_heatmap:
        hm = _artifact_heatmap(image)
        intensity = float(hm.max())
        heatmap_b64 = _heatmap_data_uri(image, hm)

    findings = [
        {
            "layer": "Frequency / FFT",
            "status": "failed" if fft_score > 0.60 else "warning" if fft_score > 0.40 else "passed",
            "detail": f"High-frequency artifact signal: {fft_score * 100:.1f}/100",
        },
        {
            "layer": "JPEG / Recompression",
            "status": "failed" if recompression > 0.65 else "warning" if recompression > 0.40 else "passed",
            "detail": f"Recompression inconsistency signal: {recompression * 100:.1f}/100",
        },
        {
            "layer": "Pixel Entropy",
            "status": "warning" if entropy < 0.45 else "passed",
            "detail": f"Normalized luminance entropy: {entropy:.3f}",
        },
        {
            "layer": "C2PA / Provenance",
            "status": "warning",
            "detail": "Cryptographic provenance was not evaluated by the baseline analyzer.",
        },
    ]

    return AnalysisResult(
        is_ai_generated=is_ai,
        confidence_percentage=score * 100,
        overall_score=score,
        score_label=label,
        media_type="image",
        findings=findings,
        heatmap_base64=heatmap_b64,
        frames_analyzed=1,
        c2pa_status="unknown",
    )


def sample_video_frames(
    video_path: str, target_fps: float = 5.0, max_frames: int = 60
) -> List[Tuple[int, float, bytes]]:
    if target_fps <= 0:
        raise ValueError("target_fps must be greater than zero")
    if max_frames <= 0:
        raise ValueError("max_frames must be greater than zero")
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise ValueError(f"Cannot open video: {video_path}")

    native_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)

    step = max(1, int(round(native_fps / target_fps)))
    frames: List[Tuple[int, float, bytes]] = []
    idx = 0
    while len(frames) < max_frames:
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ret, frame = cap.read()
        if not ret:
            break
        ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        if ok:
            frames.append((idx, idx / native_fps, buf.tobytes()))
        idx += step
        if idx >= total_frames:
            break
    cap.release()
    return frames


def analyze_video(
    video_path: str,
    target_fps: Optional[float] = None,
    max_frames: Optional[int] = None,
    progress_callback=None,
) -> AnalysisResult:
    from app.core.config import get_settings
    settings = get_settings()
    sampled = sample_video_frames(
        video_path,
        target_fps or settings.VIDEO_SAMPLE_FPS,
        max_frames or settings.VIDEO_MAX_FRAMES,
    )
    if not sampled:
        raise ValueError("No frames could be extracted from the video.")

    frame_results: List[FrameResult] = []
    for i, (idx, ts, jpeg) in enumerate(sampled):
        # Only generate the expensive visual output once.
        result = analyze_image(jpeg, include_heatmap=(i == 0))
        hm = result.heatmap_base64
        fr = FrameResult(
            frame_index=idx,
            timestamp_sec=ts,
            is_ai_generated=result.is_ai_generated,
            confidence=result.overall_score,
            max_artifact_intensity=1.0 if result.overall_score > 0.6 else result.overall_score,
            heatmap_base64=hm if i == 0 else None,
        )
        frame_results.append(fr)
        if progress_callback:
            progress_callback(i + 1, len(sampled))

    scores = np.array([f.confidence for f in frame_results], dtype=np.float32)
    mean_score = float(scores.mean())
    ai_ratio = float(np.mean(scores > 0.60))
    volatility = float(np.std(scores))
    # Temporal consistency is a supporting signal, not a separate detector.
    overall = float(np.clip(0.75 * mean_score + 0.25 * ai_ratio, 0, 1))

    findings = [
        {
            "layer": "Temporal Consistency",
            "status": "failed" if ai_ratio > 0.60 else "warning" if ai_ratio > 0.30 else "passed",
            "detail": f"{ai_ratio * 100:.0f}% of sampled frames exceeded the synthetic-signal threshold; score volatility {volatility:.3f}",
        },
        {
            "layer": "Frame Forensics",
            "status": "failed" if mean_score > 0.60 else "warning" if mean_score > 0.40 else "passed",
            "detail": f"Mean forensic signal: {mean_score * 100:.1f}/100 across {len(frame_results)} frames",
        },
        {
            "layer": "C2PA / Provenance",
            "status": "warning",
            "detail": "Cryptographic provenance was not evaluated by the baseline analyzer.",
        },
    ]
    return AnalysisResult(
        is_ai_generated=overall > 0.60,
        confidence_percentage=overall * 100,
        overall_score=overall,
        score_label=_score_label(overall),
        media_type="video",
        findings=findings,
        heatmap_base64=frame_results[0].heatmap_base64,
        frame_results=frame_results,
        frames_analyzed=len(frame_results),
        video_duration_sec=sampled[-1][1],
        c2pa_status="unknown",
    )
