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
from app.core.file_validation import UnsupportedMediaError, sniff_mime
from app.core.safe_fetch import safe_download
from app.services.analyzer import analyze_image, analyze_video

logger = logging.getLogger(__name__)
settings = get_settings()


def _ensure_temp_dir() -> Path:
    path = Path(settings.TEMP_MEDIA_DIR)
    path.mkdir(parents=True, exist_ok=True)
    return path


class MediaAnalysisError(Exception):
    """
    The only exception type a media task raises to Celery on failure.

    Its message is what `GET /v1/tasks/{id}` returns to the client as
    `error`, so it must always be safe to show: no internal file paths,
    no library exception text, no stack detail. The real exception is
    logged server-side (logger.exception) and chained via `__cause__`.
    """


def _public_failure(exc: Exception, default_message: str) -> MediaAnalysisError:
    # UnsupportedMediaError messages are written to be client-safe; every
    # other exception (OpenCV/PIL/OS errors, ValueErrors that may embed
    # paths) is replaced with the generic message for this task type.
    if isinstance(exc, UnsupportedMediaError):
        return MediaAnalysisError(str(exc))
    return MediaAnalysisError(default_message)




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
        raise _public_failure(
            exc, "Image analysis failed. The file may be corrupted or in an unsupported format."
        ) from exc
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
        raise _public_failure(
            exc, "Video analysis failed. The file may be corrupted or in an unsupported format."
        ) from exc
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

        # Decide image-vs-video from the downloaded bytes' real signature,
        # not the URL's file extension or the server's Content-Type - both
        # are chosen by whoever controls the remote URL (Gate D2).
        with open(dest, "rb") as head_f:
            detected = sniff_mime(head_f.read(8192))
        if detected in settings.ALLOWED_VIDEO_TYPES:
            is_video = True
        elif detected in settings.ALLOWED_IMAGE_TYPES:
            is_video = False
        else:
            raise UnsupportedMediaError(
                "Remote content does not match a supported image or video format."
            )

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
        raise _public_failure(
            exc, "Could not fetch or analyze the remote media."
        ) from exc
    finally:
        try:
            if dest.exists():
                dest.unlink()
        except OSError:
            pass
