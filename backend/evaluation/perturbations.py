"""
Image robustness transforms applied to *test* images before scoring.

Real-world media is recompressed, resized, cropped and screenshotted long
before a detector sees it; forensic signals (notably high-frequency
energy, which is what the baseline leans on) are exactly what those
operations destroy. Each transform is deterministic. Non-JPEG transforms
emit lossless PNG so the effect measured is the transform, not an extra
JPEG generation.
"""
from __future__ import annotations

import zlib
from typing import Callable, Dict

import cv2
import numpy as np


def _decode(data: bytes) -> np.ndarray:
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("cannot decode image for perturbation")
    return img


def _png(img: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".png", img)
    if not ok:
        raise RuntimeError("png encode failed")
    return buf.tobytes()


def _jpeg(quality: int) -> Callable[[bytes], bytes]:
    def f(data: bytes) -> bytes:
        ok, buf = cv2.imencode(".jpg", _decode(data), [int(cv2.IMWRITE_JPEG_QUALITY), quality])
        if not ok:
            raise RuntimeError("jpeg encode failed")
        return buf.tobytes()
    return f


def _downscale_50(data: bytes) -> bytes:
    img = _decode(data)
    h, w = img.shape[:2]
    return _png(cv2.resize(img, (max(8, w // 2), max(8, h // 2)), interpolation=cv2.INTER_AREA))


def _resize_roundtrip_50(data: bytes) -> bytes:
    img = _decode(data)
    h, w = img.shape[:2]
    small = cv2.resize(img, (max(8, w // 2), max(8, h // 2)), interpolation=cv2.INTER_AREA)
    return _png(cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR))


def _center_crop_80(data: bytes) -> bytes:
    img = _decode(data)
    h, w = img.shape[:2]
    ch, cw = max(8, int(h * 0.8)), max(8, int(w * 0.8))
    y0, x0 = (h - ch) // 2, (w - cw) // 2
    return _png(img[y0:y0 + ch, x0:x0 + cw])


def _blur(data: bytes) -> bytes:
    return _png(cv2.GaussianBlur(_decode(data), (0, 0), 1.0))


def _noise(data: bytes) -> bytes:
    img = _decode(data)
    rng = np.random.default_rng(zlib.crc32(data))          # deterministic per image
    noisy = img.astype(np.float32) + rng.normal(0, 3.0, img.shape).astype(np.float32)
    return _png(np.clip(noisy, 0, 255).astype(np.uint8))


PERTURBATIONS: Dict[str, Callable[[bytes], bytes]] = {
    "jpeg_q90": _jpeg(90),
    "jpeg_q70": _jpeg(70),
    "jpeg_q50": _jpeg(50),
    "downscale_50": _downscale_50,
    "resize_roundtrip_50": _resize_roundtrip_50,
    "center_crop_80": _center_crop_80,
    "blur_sigma_1": _blur,
    "noise_sigma_3": _noise,
}
