# Development Plan — Gate-Based (matching the SOAR / Code Blue-X / KRATOS discipline)

Same operating rule as the rest of the portfolio:

> Build → test → verify → harden → promote → document → move to the next gate.

Completion percentages reflect verified functionality, not code volume.
This project is currently mid-**Gate D3**: the evaluation harness is built and
tested, but has not yet been run on real data (see below).

## Current position: ~40%

The forensic baseline (image + video analysis, heatmap, findings) and its
security perimeter (SSRF resistance, rate limiting, decompression-bomb
guard, magic-byte input validation, sanitized errors) are built and verified. The two things that would make this a real
product — a validated detection model and any client anyone can actually
use — are both still ahead.

## Gate D1 — Security & robustness hardening (DONE, this round)

- [x] Deterministic forensic baseline (FFT, JPEG recompression, entropy)
- [x] Explicit uncertainty labeling (`score_label`, not a bare probability)
- [x] SSRF-safe URL fetching, including the DNS-rebinding class of bypass
- [x] Rate limiting on every endpoint
- [x] Decompression-bomb guard on image decode
- [x] Lean base dependencies (torch/torchvision split out, unused by the live path)
- [x] 48/48 tests, verified live against the running server, not just pytest

## Gate D2 — Input validation completion (DONE, v0.3.1)

- [x] File magic-byte validation (`app/core/file_validation.py`): image/video
      uploads AND remote URL fetches are typed by their real signature; the
      client `Content-Type` header and URL extension no longer decide anything
- [x] Video-specific adversarial tests (`tests/test_video_adversarial.py`):
      malformed/truncated/empty containers, zero-frame videos, codec mismatch,
      lying extensions, oversized frame dimensions
- [x] Structured error responses audited for information leakage: no exception
      text, internal path or stack detail reaches a client (routes, Celery
      task failures, global handler); full detail is logged server-side only
- [x] Found and fixed during the gate: video frames decoded before any size
      check (header-level megapixel guard added); uploads read fully into
      memory before the size limit was checked (reads now bounded to limit+1)
- [x] 103/103 tests; every fix mutation-checked (reverted, tests confirmed to
      fail) and verified live against a running server

Residual risk, deliberately out of scope here: a signature check proves the
*header* is right, not that the body is benign. A crafted file with valid
magic bytes still reaches OpenCV/FFmpeg, so keep those dependencies patched
and consider sandboxing/resource-limiting workers (Gate D6).

## Gate D3 — Evaluation harness (BUILT in v0.4.0; NOT COMPLETE until run on real data)

Harness capability (`backend/evaluation/`, see `docs/EVALUATION_HARNESS.md`):

- [x] Fixed splits assigned from data identity (group hash + seed), never from
      results; group-aware; freezable and hash-checked (`splits.json`)
- [x] Cross-generator mechanism: `--heldout` generators never touch validation
      or threshold choice, reported separately
- [x] JPEG (q90/70/50) / downscale / resize round-trip / crop / blur / noise
      robustness suite, threshold held frozen
- [x] ROC-AUC (+ bootstrap CI), PR-AUC, TPR/FPR at the frozen and the deployed
      threshold (Wilson CIs), calibration curve, ECE, Brier
- [x] Per-generator, per-real-source and per-media-type reporting
- [x] Detector name/version/validated/thresholds recorded on every API result
- [x] 139 new tests; 12 protocol safeguards mutation-checked

Completion criteria — still open, and the reason this gate is not ticked off:

- [ ] Run on a real, licensed dataset with >= 2 generators + a held-out one,
      and >= 100 real / 100 synthetic test samples per media type
- [ ] Publish the report (JSON + Markdown + splits.json + manifest hash)
- [ ] Only then may `detector_info()["validated"]` be considered for `True`,
      and only if the report supports it (a poor result is a valid result)

No threshold or scoring-formula change should ship without a fresh harness
report showing the effect - the same "no gate/threshold change without
evidence" discipline as KRATOS's promotion gate. The thresholds now live in
named constants (`SYNTHETIC_THRESHOLD`, `UNCERTAIN_LOW`) so such a change is a
one-line, reviewable diff.

## Gate D4 — Real detection model integration

- [ ] Train or source a real spatial/frequency ensemble
- [ ] Wire it in behind the existing `analyze_image`/`analyze_video`
      interface (the API contract shouldn't need to change)
- [ ] `app/services/heatmap_engine.py`'s Grad-CAM path gets a real model
      to explain, rather than sitting unused
- [ ] Re-run Gate D3's evaluation harness against the real model, not just the heuristic

## Gate D5 — Client completion

- [ ] Web dashboard (Next.js) consuming `/v1/analyze/url` and the task-status polling endpoint
- [ ] Chrome extension (Manifest V3) using the same URL endpoint from a context menu
- [ ] Mobile app (`mobile/src/ScanScreen.tsx` already exists) connected to a real deployed API instead of the placeholder `apiBaseUrl`

## Gate D6 — Production deployment

- [ ] Redis-backed rate limiting (current limiter is correctly scoped as
      single-instance-only, per its own docstring)
- [ ] Real object storage for temp media (S3-compatible) instead of local disk, for multi-worker clusters
- [ ] C2PA provenance parsing wired in (currently honestly scaffolded as `"unknown"`)
- [ ] GPU worker deployment for the eventual trained model

## Completion gate

This project is "production ready" when Gate D3's evaluation harness
produces real, published numbers (not "trust the heuristic"), a real
model is wired in and re-evaluated through that same harness, and at
least one client (web or mobile) is live against a deployed instance —
not when the code merely exists.

## Note on open-source status

This repo is public specifically to invite contributors, per the
priority list in `CONTRIBUTING.md`. Gate D3 (the evaluation harness) is
the single highest-leverage thing an outside contributor could do here —
it's the gate between "an interesting heuristic" and "a detector anyone
should trust a number from."
