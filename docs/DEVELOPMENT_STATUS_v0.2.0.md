# Development Status — v0.2.0 — 17 September 2026

## What changed
- Removed the random/untrained neural-network mock from the analysis decision path.
- Added deterministic forensic baseline signals:
  - FFT/high-frequency artifact signal
  - JPEG/recompression inconsistency signal
  - normalized pixel entropy
- Added a combined artifact heatmap using the forensic signals.
- Added `score_label` (`authentic_signal`, `uncertain`, `synthetic_signal`) to make the uncertainty boundary explicit.
- Video aggregation now reuses the image analyzer and only creates the visual heatmap for the first frame, avoiding the previous duplicate Grad-CAM pass.
- Added SSRF protections to URL fetching:
  - public-address DNS validation
  - manual redirect handling
  - validation on every redirect target
  - response-size enforcement
- CORS credentials are disabled while wildcard origins remain configured for development.
- Mobile picker now supports both image and video media and routes to the corresponding API endpoint.
- Added automated analyzer tests.

## Verification
`pytest -q` → **3 passed**

## Current limitation
This is still not a scientifically validated detector. The forensic score is a heuristic signal, not a calibrated probability and not proof that media is AI-generated or authentic.

## Next engineering gate
Build the trained ensemble interface and evaluation harness before marketing or production claims:
1. fixed train/validation/test splits
2. cross-generator test sets
3. JPEG/crop/resize/re-encode robustness suite
4. ROC-AUC, PR-AUC, TPR at fixed FPR, calibration
5. per-generator and per-media-type reporting
6. model/version provenance in every API result

## Files modified
- `backend/app/services/analyzer.py`
- `backend/app/workers/tasks.py`
- `backend/app/api/schemas.py`
- `backend/app/core/config.py`
- `backend/app/main.py`
- `mobile/src/ScanScreen.tsx`
- `README.md`
- `docs/DEVELOPMENT_STATUS_v0.2.0.md`
- `backend/tests/test_analyzer.py`
