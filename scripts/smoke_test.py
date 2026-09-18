#!/usr/bin/env python3
"""
Minimal smoke test – verifies analyzer + heatmap pipeline without Redis.
Run from the backend directory after installing requirements.
"""
import sys
from pathlib import Path

# Allow running from repo root
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.services.analyzer import analyze_image
from PIL import Image
import io
import numpy as np


def main():
    # Create a tiny synthetic RGB image
    arr = np.random.randint(0, 255, (128, 128, 3), dtype=np.uint8)
    img = Image.fromarray(arr)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    image_bytes = buf.getvalue()

    print("Running analyze_image on synthetic 128×128 JPEG…")
    result = analyze_image(image_bytes, include_heatmap=True)

    print(f"  is_ai_generated      : {result.is_ai_generated}")
    print(f"  confidence_percentage: {result.confidence_percentage:.1f}%")
    print(f"  frames_analyzed      : {result.frames_analyzed}")
    print(f"  heatmap present      : {bool(result.heatmap_base64)}")
    print(f"  findings             : {len(result.findings)}")
    for f in result.findings:
        print(f"    • [{f['status']}] {f['layer']}: {f['detail'][:60]}…")

    print("\nSmoke test passed.")


if __name__ == "__main__":
    main()
