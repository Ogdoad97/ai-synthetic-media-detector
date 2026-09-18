# Development Plan — Gate-Based (matching the SOAR / Code Blue-X / KRATOS discipline)

Same operating rule as the rest of the portfolio:

> Build → test → verify → harden → promote → document → move to the next gate.

Completion percentages reflect verified functionality, not code volume.
This project is currently at the end of **Gate D1** (security/robustness
hardening) — see below.

## Current position: ~30-35%

The forensic baseline (image + video analysis, heatmap, findings) and its
security perimeter (SSRF resistance, rate limiting, decompression-bomb
guard) are built and verified. The two things that would make this a real
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

## Gate D2 — Input validation completion

- [ ] File magic-byte validation (stop trusting client-supplied Content-Type)
- [ ] Video-specific adversarial tests (malformed containers, codec mismatches, zero-frame videos)
- [ ] Structured error responses audited for information leakage (stack traces, internal paths)

## Gate D3 — Evaluation harness (the highest-value gate — turns "a heuristic signal" into a measured detector)

- [ ] Fixed train/val/test splits, held out before any threshold tuning
- [ ] Cross-generator test sets (not just one generator's outputs)
- [ ] JPEG/crop/resize/re-encode robustness suite
- [ ] ROC-AUC, PR-AUC, TPR@fixed-FPR, calibration curves
- [ ] Per-generator and per-media-type reporting, not just an aggregate number
- [ ] Model/version provenance recorded on every API result

No threshold or scoring-formula change should ship without being run
through this harness once it exists — the same "no gate/threshold change
without evidence" discipline as KRATOS's promotion gate.

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
