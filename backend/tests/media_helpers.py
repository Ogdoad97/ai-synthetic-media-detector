"""
Shared helpers that build small but REAL media files for tests.

Fixtures must have genuine file signatures now that ingestion sniffs
magic bytes instead of trusting Content-Type - a bare b"fake video bytes"
is (correctly) rejected as not-a-video.
"""
from __future__ import annotations

import io
import os
import tempfile

import cv2
import numpy as np
from PIL import Image


def jpeg_bytes(size=(16, 16), color=(10, 20, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color=color).save(buf, format="JPEG")
    return buf.getvalue()


def png_bytes(size=(16, 16), color=(10, 20, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color=color).save(buf, format="PNG")
    return buf.getvalue()


def webp_bytes(size=(16, 16), color=(10, 20, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color=color).save(buf, format="WEBP")
    return buf.getvalue()


def gif_bytes(size=(16, 16), color=(10, 20, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color=color).save(buf, format="GIF")
    return buf.getvalue()


def video_bytes(ext: str = ".mp4", fourcc: str = "mp4v", n_frames: int = 3,
                size=(32, 32), fps: float = 5.0) -> bytes:
    """Write a real video with cv2.VideoWriter and return its bytes."""
    rng = np.random.default_rng(0)
    fd, path = tempfile.mkstemp(suffix=ext)
    os.close(fd)
    try:
        writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*fourcc), fps, size)
        assert writer.isOpened(), f"cv2 could not open a writer for {ext}/{fourcc}"
        for _ in range(n_frames):
            writer.write(rng.integers(0, 255, (size[1], size[0], 3), dtype=np.uint8))
        writer.release()
        with open(path, "rb") as f:
            return f.read()
    finally:
        os.remove(path)


def mp4_bytes(**kw) -> bytes:
    return video_bytes(".mp4", "mp4v", **kw)


def avi_bytes(**kw) -> bytes:
    return video_bytes(".avi", "MJPG", **kw)
