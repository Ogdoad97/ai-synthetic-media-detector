"""
Pydantic request / response models for the public API.
Keeps TypeScript clients and OpenAPI docs perfectly aligned.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, HttpUrl


class AnalyzeUrlRequest(BaseModel):
    """Request body for /v1/analyze/url."""

    url: HttpUrl = Field(..., description="Publicly reachable HTTP(S) URL of an image or video")
    media_type_hint: Optional[str] = Field(
        None,
        description="Optional hint: 'image' or 'video'. Auto-detected when omitted.",
    )


class TaskAcceptedResponse(BaseModel):
    """Immediate response when a long-running job is queued."""

    task_id: str
    status: str = "queued"
    message: str
    status_url: str
    result_url: str


class TaskStatusResponse(BaseModel):
    """Polling response for task progress."""

    task_id: str
    status: str  # PENDING | STARTED | PROGRESS | SUCCESS | FAILURE
    progress: Optional[int] = None
    stage: Optional[str] = None
    frames_done: Optional[int] = None
    frames_total: Optional[int] = None
    error: Optional[str] = None
    result: Optional[Dict[str, Any]] = None


class Finding(BaseModel):
    layer: str
    status: str  # passed | failed | warning
    detail: str


class AnalysisResponse(BaseModel):
    """Final analysis payload returned on SUCCESS."""

    is_ai_generated: bool
    confidence_percentage: float
    overall_score: float
    score_label: str = "uncertain"
    media_type: str
    findings: List[Finding]
    heatmap_base64: Optional[str] = None
    frames_analyzed: int = 1
    video_duration_sec: Optional[float] = None
    c2pa_status: str = "unknown"
    task_id: Optional[str] = None
    original_filename: Optional[str] = None
    source_url: Optional[str] = None
    frame_results: Optional[List[Dict[str, Any]]] = None
