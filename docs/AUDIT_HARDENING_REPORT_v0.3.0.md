# Audit & Hardening Report — v0.3.0

**Date:** 18 September 2026
**Scope:** Independent security/robustness audit of the v0.2.0 audited baseline (24/24 tests), followed by fixes
**Result:** 48 passed, 0 failed (24 pre-existing + 24 new)

## What this audit did differently from the v0.2.0 one

The v0.2.0 audit (`AUDIT_TEST_REPORT.md`) verified the analyzer's *contract*
— determinism, score ranges, label thresholds, input validation. It didn't
cover the parts of the system that talk to the outside world: the
`/v1/analyze/url` fetcher, and the complete absence of any request
throttling. This pass reads every file that handles untrusted input
(`routes.py`, `tasks.py`, `analyzer.py`'s image decoder) with an adversarial
lens: what happens if the caller is actively hostile, not just malformed.

## Findings and fixes

### 1. DNS-rebinding SSRF bypass (real, not hypothetical) — FIXED

The v0.2.0 SSRF guard (`_assert_public_host` in the old `tasks.py`) resolved
the target hostname, checked every returned address against a
private/loopback/link-local/reserved/multicast blocklist, and only *then*
made the actual `httpx` request against the hostname — which triggers a
**second**, independent DNS resolution at connection time. A domain with a
very short TTL (or a resolver returning different answers to successive
queries — both trivial for an attacker who controls the domain's DNS) can
present a public IP to the validation check and a private/internal IP to
the actual connection. This is the standard "DNS rebinding" SSRF bypass
class, not a made-up edge case — it's the reason most SSRF-hardening guides
explicitly call out "validate, don't re-resolve."

**Fix**: `app/core/safe_fetch.py` resolves the hostname exactly once,
validates every address in that one answer set, and connects directly to
the validated IP (substituted into the request URL) while preserving the
original hostname as the `Host` header and TLS SNI value — so
virtual-hosted/CDN-fronted targets and certificate validation both still
work correctly against the real hostname, only the actual socket
connection is pinned to the address that was checked. Applied on every hop
of a redirect chain, not just the initial URL — a malicious redirect
target gets the exact same treatment as the original.

Verified with `tests/test_safe_fetch.py` (11 tests): scheme rejection,
missing-hostname rejection, DNS-failure handling, rejection of loopback /
private / link-local / the AWS-Azure-GCP metadata address specifically,
rejection when *any* address in a multi-answer DNS response is private
(an attacker mixing one public and one private answer, hoping validation
only checks the first), a redirect chain that points at an internal
address after the original URL passed validation, oversized remote media,
and — the actual regression test for the bug itself — that the connection
httpx makes is provably to the pre-validated IP with the original hostname
preserved as the `Host` header, using a mocked transport to inspect the
real outgoing request rather than trusting the code's own claim about
what it does.

### 2. No rate limiting anywhere — FIXED

Every endpoint (`/v1/analyze/image`, `/video`, `/url`) was completely
open: unauthenticated and unlimited. Each does real, non-trivial compute
(FFT, JPEG round-trip, image decode), and `/analyze/url` additionally
makes an outbound network request on the caller's behalf — both are real
cost/abuse vectors with nothing stopping a single caller from driving
unbounded compute or using the service to hammer arbitrary third-party
URLs at volume.

**Fix**: `app/core/rate_limit.py`, a minimal in-memory fixed-window
limiter, applied per-endpoint-class (30/min image, 10/min video, 10/min
URL — video and URL are tighter since they're heavier or involve outbound
fetches). Explicitly documented as process-local state, not
distributed — correct for a single API instance, under-enforces across
multiple replicas behind a load balancer since each keeps its own counts.
Flagged as a follow-up (swap the store for Redis, already a dependency
here via Celery) rather than solved now, to keep this fix small and
independently correct.

Verified with `tests/test_rate_limit.py` (6 tests: allow-under-limit,
block-over-limit, per-client independence, window expiry, missing-client
fallback, test-only reset) and `tests/test_routes_rate_limit.py` (4
route-level integration tests using the real FastAPI app, proving the
limiter is actually wired into the live endpoints and fires *before* a
video/URL task is ever queued — not just correct in isolation).

### 3. Image decompression bomb — FIXED

`_decode_image` had no size limit before doing a full `cv2.imdecode`. A
compressed file of a few hundred KB — a solid-color image compresses
extremely well — can still decode to a bitmap of hundreds of MB or more
(a 10000×10000 image is 100 megapixels; uncompressed RGB at that size is
~300MB). The upload-size cap (`MAX_UPLOAD_SIZE_MB`) only limits the
*compressed* file size on disk, not the decoded footprint in memory.

**Fix**: check image dimensions from the file header alone (via
`PIL.Image.open`, which reads only enough of the file to determine
size/format and does not decode pixel data until explicitly asked) before
the expensive full OpenCV decode ever runs. Rejects anything over 50
megapixels — comfortably above real photos (a 48MP phone camera is
48MP) — with a clean `ValueError` rather than letting the decode happen
and then discovering the problem.

Verified with 3 new tests in `tests/test_analyzer.py`: a genuine
decompression-bomb-shaped image (100MP, compresses to ~1.5MB — the actual
disk-vs-memory gap this guard exists to catch) is rejected before full
decode, a normal-sized image still works (the guard isn't so aggressive it
breaks real photos), and the rejection surfaces as a clean error through
the full `analyze_image()` path, not just the low-level decoder.

### 4. Unused `torch`/`torchvision` dependency — FIXED (packaging, not a bug)

Confirmed directly, not assumed: installed every dependency the live
analysis path actually uses (fastapi, celery, redis, httpx, opencv,
Pillow, numpy) **without** torch/torchvision, then ran the full test suite
(48/48 passed) and imported every module in the live request path
(`app.main`, `app.workers.tasks`, `app.services.analyzer`) — all clean.
`app/services/heatmap_engine.py` (the PyTorch Grad-CAM module from the
original handover PDF) is the only file that imports torch, and nothing
in the live path imports it — it's currently unused scaffolding, kept for
when a real trained model is wired in (see README "Next Steps").

**Fix**: moved `torch`/`torchvision` out of `requirements.txt` into a new
`requirements-ml.txt`, to be installed only once `heatmap_engine.py` is
actually wired to a real model. `heatmap_engine.py` itself now raises a
clear, actionable `ImportError` if imported without torch installed,
instead of a bare `ModuleNotFoundError` deep in an unrelated stack trace.
This isn't a correctness fix — it's a real cost fix: every deployment or
CI run was previously paying for a multi-GB dependency for code that
isn't called yet.

## What this audit did NOT find a problem with (checked, not assumed)

- `AnalyzeUrlRequest.url` already uses Pydantic's `HttpUrl` type — real
  validation, not a bare string.
- CORS is `allow_origins=["*"]` with `allow_credentials=False` — safe as
  configured (wildcard origins without credentials doesn't leak
  session/cookie data per the CORS spec), but worth a note for whoever
  eventually adds authentication: flipping `allow_credentials=True` while
  origins stay wildcarded would be a real regression, not this audit's
  problem to pre-empt but worth flagging in the next round that touches
  auth.
- File upload content-type checking still trusts the client-supplied
  `Content-Type` header (trivially spoofable) rather than sniffing actual
  file magic bytes — flagged as a real, lower-severity gap (OpenCV's
  `imdecode` returning `None` on a non-image already fails closed
  reasonably well) rather than fixed in this pass, to keep this round's
  scope to the higher-severity findings above.

## Live verification

Beyond the 48 automated tests, the actual server was booted
(`uvicorn app.main:app`) and hit with real HTTP requests, not just
pytest's in-process test client:
- `/v1/health` — real response
- `/v1/analyze/image` with a real synthetic-noise JPEG — real forensic
  scores, real heatmap data URI, real findings
- 31 consecutive requests to `/v1/analyze/image` from the same client —
  confirmed the 30/min limit fires with a real `429` on the 31st
- A real 100-megapixel solid-color JPEG (~1.5MB on disk) — confirmed
  rejected with the exact "exceeding the 50 megapixel safety limit"
  message, not a crash or hang

## Command

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt pytest pytest-asyncio
PYTHONPATH=. .venv/bin/pytest -v
# 48 passed
```

## Explicit non-claims (unchanged from v0.2.0)

This audit hardens the system's *security and robustness* boundary. It
does not, and was never intended to, establish scientific detection
accuracy. The forensic score remains a heuristic signal, not a calibrated
probability. That work is still the evaluation harness described in
`DEVELOPMENT_STATUS_v0.2.0.md` and this document's "Next gate" below.

## Next gate (unchanged in substance from v0.2.0)

1. Trained ensemble interface
2. Fixed train/val/test splits
3. Cross-generator test sets
4. JPEG/crop/resize/re-encode robustness
5. ROC-AUC, PR-AUC, TPR@FPR, calibration
6. Model/version provenance on every API result
7. (New, from this round) File-magic-byte validation on uploads, ahead of
   or alongside the trained-model work
8. (New, from this round) Redis-backed rate limiting once there's more
   than one API replica
