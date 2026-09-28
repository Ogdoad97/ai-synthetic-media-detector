"""
Procedural toy dataset so the harness can be run end to end without any
real data. NOT a benchmark: the "generators" are arithmetic, not models.
It exists to show the manifest format and to smoke-test the pipeline.
"""
from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np


def _base(rng, size):
    """Smooth 'natural-ish' image: a few big blurry blobs."""
    img = np.zeros((size, size, 3), np.float32)
    for _ in range(6):
        cx, cy = rng.integers(0, size, 2)
        color = rng.uniform(40, 220, 3)
        cv2.circle(img, (int(cx), int(cy)), int(rng.integers(size // 8, size // 3)), color.tolist(), -1)
    return cv2.GaussianBlur(img, (0, 0), size / 20)


def make_demo_dataset(root: Path, n_per_class: int = 40, size: int = 96, seed: int = 7) -> Path:
    root = Path(root)
    (root / "images").mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    rows = []
    kinds = [("real:toycam", 0), ("toygen_a", 1), ("toygen_b", 1)]
    for gen, label in kinds:
        for i in range(n_per_class):
            img = _base(rng, size)
            img += rng.normal(0, 2.0, img.shape)                       # sensor-ish noise
            if gen == "toygen_a":                                     # periodic upsampling-style pattern
                yy, xx = np.mgrid[0:size, 0:size]
                img += (((yy + xx) % 2) * 6.0)[..., None]
            if gen == "toygen_b":                                     # over-smooth
                img = cv2.GaussianBlur(img, (0, 0), 1.5)
            img = np.clip(img, 0, 255).astype(np.uint8)
            rel = f"images/{gen.replace(':', '_')}_{i:03d}.{'jpg' if label == 0 else 'png'}"
            cv2.imwrite(str(root / rel), img, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
            rows.append({"path": rel, "label": label, "generator": gen,
                         "media_type": "image", "group": f"{gen}-{i}"})
    manifest = root / "manifest.jsonl"
    manifest.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return manifest
