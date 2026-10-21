# AI Synthetic Media Detector — Handoff Evidence 21 Oct 2026

## Baseline
- Repository: Ogdoad97/ai-synthetic-media-detector (public)
- Branch: main
- SHA: 480ba9b1c4b985332b7f7b2fb419953895274c7c (approx; short 480ba9b)
- Latest message observed: Link media privacy and retention requirements
- CI: most recent run on this SHA completed/success (16 Oct 2026); earlier failure on same SHA was superseded.

## Architecture & status (from README + code)
- FastAPI + Celery/Redis, OpenCV forensic baseline, heatmap, C2PA scaffolding.
- Security hardening documented (SSRF/DNS-rebinding, rate limits, decompression-bomb, magic-byte validation, sanitized errors, video adversarial tests).
- Evaluation harness (Gate D3) is implemented under backend/evaluation/:
  - splits assigned from data identity only
  - operating threshold frozen on validation real samples only (target FPR)
  - test / held-out-generator / perturbed results reported at frozen + deployed thresholds
  - Wilson intervals; failures retained; group-leakage assertions
- **Explicit README statement**: “The harness is built; a real-data run has not been done yet, so detection accuracy is still unmeasured.”

## Tests present
- Analyzer, eval metrics/runner/splits, file validation, provenance, rate limiting, routes, safe_fetch, task errors, video adversarial.

## Safety & trust posture
- SAFETY_AND_TRUST_REQUIREMENTS.md and RESPONSIBLE_USE_POLICY.md present.
- Release gates require that a release not be described as scientifically validated until measured evidence exists (TRUST-02).
- Detection results intended as indicators, not conclusive proof.

## Completion gate (original handoff) residual
1. Build / acquire a documented benchmark of genuine + synthetic media with known provenance; separate by source/creator/generation system.
2. Run the existing harness on that benchmark; report precision, recall, FPR, ROC-AUC, calibration, confidence intervals, and content-type breakdowns.
3. Produce a public model card stating performance and limitations.
4. Continue adversarial and resource-exhaustion testing; keep privacy/retention defaults strict.

No real-world accuracy numbers are claimed or verified in this session. The evaluation infrastructure is the primary mature artifact.

## Priority-4 pass — measured accuracy path
1. Assemble benchmark: genuine + synthetic with known provenance; split by source/creator/generator; document licences.
2. Run `python -m evaluation` (or documented harness entry) with frozen threshold protocol already implemented.
3. Report precision/recall/FPR/ROC-AUC/calibration + CIs + content-type breakdowns; list warnings.
4. Publish model card; keep API copy as indicators not proof.
5. Task-result auth + retention tests still open per SAFETY docs.
