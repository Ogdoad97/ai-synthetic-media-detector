"""
FastAPI route definitions for the AI Synthetic Media Detector.

Endpoints
---------
POST /v1/analyze/image   – synchronous image analysis (small files)
POST /v1/analyze/video   – async video analysis (returns task_id)
POST /v1/analyze/url     – fetch remote media (Chrome extension & web clients)
GET  /v1/tasks/{task_id} – poll status / retrieve result
"""
from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path
from typing import Optional

from celery.result import AsyncResult
from fastapi import APIRouter, File, HTTPException, Request, UploadFile, status
from fastapi.responses import JSONResponse

from app.api.schemas import (
    AnalysisResponse,
    AnalyzeUrlRequest,
    TaskAcceptedResponse,
    TaskStatusResponse,
)
from app.core.celery_app import celery_app
from app.core.config import get_settings
from app.core.rate_limit import image_limiter, url_limiter, video_limiter
from app.services.analyzer import analyze_image
from app.workers.tasks import process_image_task, process_url_task, process_video_task

logger = logging.getLogger(__name__)
settings = get_settings()
router = APIRouter(prefix=settings.API_V1_PREFIX)


def _save_upload(file: UploadFile) -> Path:
    """Persist an uploaded file to the shared temp directory."""
    temp_dir = Path(settings.TEMP_MEDIA_DIR)
    temp_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(file.filename or "upload.bin").suffix
    dest = temp_dir / f"{uuid.uuid4().hex}{suffix}"
    with open(dest, "wb") as f:
        content = file.file.read()
        if len(content) > settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"File exceeds {settings.MAX_UPLOAD_SIZE_MB} MB limit",
            )
        f.write(content)
    return dest


@router.post(
    "/analyze/image",
    response_model=AnalysisResponse,
    summary="Synchronous image analysis",
    tags=["Analysis"],
)
async def analyze_image_endpoint(request: Request, file: UploadFile = File(...)):
    """
    Analyze a single image immediately.
    Suitable for photos < ~10 MB. For larger media prefer the async endpoints.
    """
    image_limiter.check(request)
    content_type = (file.content_type or "").lower()
    if content_type not in settings.ALLOWED_IMAGE_TYPES and not content_type.startswith("image/"):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported image type: {content_type}",
        )

    image_bytes = await file.read()
    if len(image_bytes) > settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File too large")

    try:
        result = analyze_image(image_bytes, include_heatmap=True)
        return result.to_dict()
    except Exception as exc:
        logger.exception("Synchronous image analysis failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post(
    "/analyze/video",
    response_model=TaskAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Asynchronous video analysis",
    tags=["Analysis"],
)
async def analyze_video_endpoint(request: Request, file: UploadFile = File(...)):
    """
    Queue a video for background processing (5 FPS sampling + aggregation).
    Returns a task_id that can be polled via /v1/tasks/{task_id}.
    """
    video_limiter.check(request)
    content_type = (file.content_type or "").lower()
    if content_type not in settings.ALLOWED_VIDEO_TYPES and not content_type.startswith("video/"):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported video type: {content_type}",
        )

    dest = _save_upload(file)
    task = process_video_task.delay(str(dest), original_filename=file.filename or "video.mp4")

    return TaskAcceptedResponse(
        task_id=task.id,
        status="queued",
        message="Video analysis queued. Poll the status_url for progress.",
        status_url=f"{settings.API_V1_PREFIX}/tasks/{task.id}",
        result_url=f"{settings.API_V1_PREFIX}/tasks/{task.id}",
    )


@router.post(
    "/analyze/url",
    response_model=TaskAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Analyze remote media by URL",
    tags=["Analysis"],
)
async def analyze_url_endpoint(request: Request, body: AnalyzeUrlRequest):
    """
    Fetch a publicly reachable image or video URL and run the detection pipeline.
    Designed for the Chrome extension background service worker and any
    client that already has a media URL (social share sheets, web scrapers, etc.).

    The task runs asynchronously; poll /v1/tasks/{task_id} for status and result.
    """
    url_limiter.check(request)
    url_str = str(body.url)
    task = process_url_task.delay(url_str)

    return TaskAcceptedResponse(
        task_id=task.id,
        status="queued",
        message="Remote media fetch & analysis queued.",
        status_url=f"{settings.API_V1_PREFIX}/tasks/{task.id}",
        result_url=f"{settings.API_V1_PREFIX}/tasks/{task.id}",
    )


@router.get(
    "/tasks/{task_id}",
    response_model=TaskStatusResponse,
    summary="Poll task status / retrieve result",
    tags=["Tasks"],
)
async def get_task_status(task_id: str):
    """
    Returns current state of a Celery task.
    When status == SUCCESS the full analysis payload is included under `result`.
    """
    result = AsyncResult(task_id, app=celery_app)

    response = TaskStatusResponse(task_id=task_id, status=result.status)

    if result.status == "PROGRESS" and isinstance(result.info, dict):
        response.progress = result.info.get("progress")
        response.stage = result.info.get("stage")
        response.frames_done = result.info.get("frames_done")
        response.frames_total = result.info.get("frames_total")
    elif result.status == "SUCCESS":
        response.result = result.result
        response.progress = 100
        response.stage = "completed"
    elif result.status == "FAILURE":
        response.error = str(result.info) if result.info else "Unknown error"
        response.stage = "failed"

    return response


@router.get("/health", tags=["System"])
async def health():
    """Liveness probe."""
    return {"status": "ok", "service": settings.PROJECT_NAME, "version": settings.VERSION}
