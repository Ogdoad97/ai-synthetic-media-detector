"""Detectors as the harness sees them: bytes/path in, score in [0, 1] out."""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional, Protocol


class Scorer(Protocol):
    def score_image(self, data: bytes) -> float: ...
    def score_video(self, path: str) -> float: ...
    def info(self) -> Dict[str, Any]: ...


class BaselineScorer:
    """The shipped forensic baseline, exactly as the API runs it."""

    def score_image(self, data: bytes) -> float:
        from app.services.analyzer import analyze_image
        return analyze_image(data, include_heatmap=False).overall_score

    def score_video(self, path: str) -> float:
        from app.services.analyzer import analyze_video
        return analyze_video(path).overall_score

    def info(self) -> Dict[str, Any]:
        from app.services.analyzer import detector_info
        return detector_info()


class FunctionScorer:
    """Wrap plain callables (used by tests and by anyone plugging in a model)."""

    def __init__(self, image_fn: Callable[[bytes], float],
                 video_fn: Optional[Callable[[str], float]] = None,
                 info: Optional[Dict[str, Any]] = None):
        self._image_fn, self._video_fn = image_fn, video_fn
        self._info = info or {"name": "function-scorer", "version": "0", "validated": False}

    def score_image(self, data: bytes) -> float:
        return float(self._image_fn(data))

    def score_video(self, path: str) -> float:
        if self._video_fn is None:
            raise NotImplementedError("no video scorer configured")
        return float(self._video_fn(path))

    def info(self) -> Dict[str, Any]:
        return dict(self._info)
