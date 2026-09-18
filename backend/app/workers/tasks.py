"""
Celery task definitions for asynchronous media analysis.

Proven pattern:
- FastAPI receives upload / URL → immediately returns task_id
- Worker downloads / opens media, samples frames at 5 FPS, runs analysis
- Progress is published via self.update_state for real-time UI feedback
- Final result is stored in Redis backend and retrieved by status endpoint
"""
from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path
from typing import Any, Dict
from urllib.parse import urlparse

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded

from app.core.celery_app import celery_app
from app.core.config import get_settings
from app.core.safe_fetch import safe_download
from app.services.analyzer import analyze_image, analyze_video

logger = logging.getLogger(__name__)
settings = get_settings()


def _ensure_temp_dir() -> Path:
    path = Path(settings.TEMP_MEDIA_DIR)
    path.mkdir(parents=True, exist_ok=True)
    return path




@celery_app.task(bind=True, name="app.workers.tasks.process_image_task", max_retries=2)
def process_image_task(self, file_path: str, original_filename: str = "image.jpg") -> Dict[str, Any]:
    """
    Analyze a single local image file.

    Parameters
    ----------
    file_path : str
        Absolute path to the image on the worker filesystem.
    original_filename : str
        Original client filename (for logging / metadata).

    Returns
    -------
    dict
        Full AnalysisResult.to_dict() payload.
    """
    try:
        self.update_state(state="PROGRESS", meta={"stage": "loading", "progress": 5})
        with open(file_path, "rb") as f:
            image_bytes = f.read()

        self.update_state(state="PROGRESS", meta={"stage": "inference", "progress": 40})
        result = analyze_image(image_bytes, include_heatmap=True)

        self.update_state(state="PROGRESS", meta={"stage": "finalizing", "progress": 90})
        payload = result.to_dict()
        payload["task_id"] = self.request.id
        payload["original_filename"] = original_filename
        return payload

    except SoftTimeLimitExceeded:
        logger.error("Image task soft time limit exceeded")
        raise
    except Exception as exc:
        logger.exception("Image analysis failed")
        self.update_state(state="FAILURE", meta={"error": str(exc)})
        raise
    finally:
        # Clean up temp file
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except OSError:
            pass


@celery_app.task(bind=True, name="app.workers.tasks.process_video_task", max_retries=1)
def process_video_task(self, file_path: str, original_filename: str = "video.mp4") -> Dict[str, Any]:
    """
    Analyze a video by sampling frames at the configured FPS (default 5).

    Progress is reported as percentage of frames processed so the mobile /
    web clients can show a real progress bar.
    """
    try:
        self.update_state(
            state="PROGRESS",
            meta={"stage": "sampling", "progress": 5, "frames_done": 0, "frames_total": 0},
        )

        def progress_cb(done: int, total: int) -> None:
            pct = 10 + int(80 * done / max(total, 1))
            self.update_state(
                state="PROGRESS",
                meta={
                    "stage": "inference",
                    "progress": pct,
                    "frames_done": done,
                    "frames_total": total,
                },
            )

        result = analyze_video(
            video_path=file_path,
            target_fps=settings.VIDEO_SAMPLE_FPS,
            max_frames=settings.VIDEO_MAX_FRAMES,
            progress_callback=progress_cb,
        )

        self.update_state(state="PROGRESS", meta={"stage": "finalizing", "progress": 95})
        payload = result.to_dict()
        payload["task_id"] = self.request.id
        payload["original_filename"] = original_filename
        return payload

    except SoftTimeLimitExceeded:
        logger.error("Video task soft time limit exceeded")
        raise
    except Exception as exc:
        logger.exception("Video analysis failed")
        self.update_state(state="FAILURE", meta={"error": str(exc)})
        raise
    finally:
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except OSError:
            pass


@celery_app.task(bind=True, name="app.workers.tasks.process_url_task", max_retries=2)
def process_url_task(self, url: str) -> Dict[str, Any]:
    """
    Fetch remote media (image or video) and run the appropriate analysis pipeline.
    Used by the Chrome extension background service worker and any URL-based clients.
    """
    temp_dir = _ensure_temp_dir()
    suffix = Path(urlparse(url).path).suffix or ".bin"
    dest = temp_dir / f"{uuid.uuid4().hex}{suffix}"

    try:
        self.update_state(state="PROGRESS", meta={"stage": "downloading", "progress": 5})
        safe_download(url, dest)

        # Heuristic: treat as video if extension looks like one
        video_exts = {".mp4", ".webm", ".mov", ".avi", ".mkv", ".m4v"}
        is_video = dest.suffix.lower() in video_exts

        self.update_state(
            state="PROGRESS",
            meta={"stage": "analyzing", "progress": 15, "media_type": "video" if is_video else "image"},
        )

        if is_video:
            result = analyze_video(
                str(dest),
                target_fps=settings.VIDEO_SAMPLE_FPS,
                max_frames=settings.VIDEO_MAX_FRAMES,
                progress_callback=lambda d, t: self.update_state(
                    state="PROGRESS",
                    meta={
                        "stage": "inference",
                        "progress": 20 + int(70 * d / max(t, 1)),
                        "frames_done": d,
                        "frames_total": t,
                    },
                ),
            )
        else:
            with open(dest, "rb") as f:
                image_bytes = f.read()
            result = analyze_image(image_bytes, include_heatmap=True)

        payload = result.to_dict()
        payload["task_id"] = self.request.id
        payload["source_url"] = url
        return payload

    except SoftTimeLimitExceeded:
        raise
    except Exception as exc:
        logger.exception("URL analysis failed for %s", url)
        self.update_state(state="FAILURE", meta={"error": str(exc)})
        raise
    finally:
        try:
            if dest.exists():
                dest.unlink()
        except OSError:
            pass
