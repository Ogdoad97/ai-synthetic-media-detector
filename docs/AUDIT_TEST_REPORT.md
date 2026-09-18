# Audit Test Report — Forensic Baseline (v0.2.0+)

**Date:** 17 September 2026  
**Scope:** Extracted v0.2.0 analyzer + expanded audit suite  
**Result:** **24 passed, 0 failed**

## What was extracted

From `ai-synthetic-media-detector-v0.2.0.zip`:

| Item | Status |
|------|--------|
| `backend/app/services/analyzer.py` | Forensic baseline (FFT + JPEG recompression + entropy) |
| `backend/tests/test_analyzer.py` | Original 3 tests (determinism, invalid image, bad FPS) |
| `docs/DEVELOPMENT_STATUS_v0.2.0.md` | Change log and next engineering gate |

## What was implemented

Expanded `tests/test_analyzer.py` into a proper **audit suite** covering:

1. **API / contract stability**
   - Deterministic scores for the same bytes
   - `score_label` ∈ {authentic_signal, uncertain, synthetic_signal}
   - `to_dict()` key set stable for clients
   - Heatmap optional and valid data-URI when requested

2. **Uncertainty contract**
   - Parametrized boundary tests for `_score_label` (0.40–0.60 = uncertain)
   - `is_ai_generated` only True when label is `synthetic_signal`

3. **Forensic signal unit checks**
   - FFT score + artifact map in [0, 1]
   - JPEG recompression score in [0, 1]
   - Entropy: solid < noisy

4. **Input rejection**
   - Invalid bytes, empty bytes, missing video path
   - `target_fps <= 0` and `max_frames <= 0`

5. **Findings structure**
   - Required layers: Frequency/FFT, JPEG/Recompression, Pixel Entropy, C2PA/Provenance
   - Status values restricted to passed | warning | failed

6. **Hardening applied**
   - `_decode_image` now rejects empty / zero-size buffers with a clean `ValueError` (previously OpenCV assertion)

## Command

```bash
cd backend
PYTHONPATH=. pytest -q tests/test_analyzer.py
# ........................  24 passed
```

## Explicit non-claims

These tests **do not** establish scientific detection accuracy. They only enforce:

- determinism
- API shape
- label thresholds
- input validation
- signal numeric ranges

Scientific validity still requires the evaluation harness listed in `DEVELOPMENT_STATUS_v0.2.0.md` (cross-generator sets, ROC-AUC, robustness suite, calibration).

## Next gate (unchanged)

1. Trained ensemble interface  
2. Fixed train/val/test splits  
3. Cross-generator test sets  
4. JPEG/crop/resize/re-encode robustness  
5. ROC-AUC, PR-AUC, TPR@FPR, calibration  
6. Model/version provenance on every API result  
