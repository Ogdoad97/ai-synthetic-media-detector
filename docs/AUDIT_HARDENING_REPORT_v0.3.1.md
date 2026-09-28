# Audit & Hardening Report — v0.3.1 (Gate D2: input validation completion)

**Result:** 103 passed, 0 failed (48 pre-existing + 55 new)
**Method:** every fix was verified three ways — automated tests, a mutation
check (each fix reverted in turn and the matching tests confirmed to fail),
and live requests against a running uvicorn server.

## Findings and fixes

### 1. Upload type decided by a client-controlled label (fixed)
Image and video uploads were accepted or rejected on the client's
`Content-Type` header, and remote URL media was routed to the image or video
pipeline by the URL's file extension. Both are attacker-chosen.
`app/core/file_validation.py` now sniffs the real byte signature
(`filetype`, pure Python, no libmagic) and is the only source of truth on all
three ingestion paths. A real JPEG sent as `application/octet-stream` is
accepted; HTML, executables, archives, PDFs or video bytes labelled
`image/jpeg` are rejected with 415. The old 415 also echoed the client's
Content-Type string back; it now names only the expected category.

### 2. Internal details leaked in error responses (fixed)
- `POST /v1/analyze/image` returned `str(exc)` as the 500 body.
- `POST /v1/analyze/video` failures ended up as Celery failure text that
  `GET /v1/tasks/{id}` returned verbatim; `sample_video_frames` embedded the
  internal temp path in its "Cannot open video" message.
- There was no global handler, and `settings.DEBUG` was never wired to the app.

Now: routes return fixed generic messages; tasks raise `MediaAnalysisError`
with a client-safe message (real exception chained and logged); a global
exception handler returns a fixed body; `DEBUG` is passed to `FastAPI(...)`.
Also removed dead `update_state(state="FAILURE", ...)` calls that stored raw
`str(exc)` and were overwritten by Celery when the task re-raised.

### 3. Oversized video frames decoded before any size check (fixed)
The 50 MP guard only ran on the re-encoded JPEG, after `cap.read()` had
already allocated the full frame. A 0.45 MB file declaring 8000x7000 was
fully decoded before rejection. The sampler now checks the container's
declared frame dimensions before reading any frame.

### 4. Uploads read fully into memory before the size limit applied (fixed)
The body was read completely, then length-checked. Reads are now bounded to
`limit + 1` bytes, so oversized uploads are rejected without being held in
memory in full.

### 5. Malformed video containers (verified, no change needed)
Garbage, empty, header-only and truncated containers, zero-frame videos, an
unknown codec tag, and lying extensions all fail fast with a clean
`ValueError` (nothing crashed or hung); now locked in by tests.

## Notes on test changes
`test_routes_rate_limit.py` posted `b"fake video bytes"` labelled `video/mp4`
and counted queued tasks. That fixture is now correctly rejected, so it uses
a real MP4 (`tests/media_helpers.py` builds small genuine media files).

## Residual risk / not done
- A signature check validates the header, not the body. A crafted file with
  valid magic bytes still reaches OpenCV/FFmpeg. Keep them patched; consider
  sandboxed, memory/CPU-limited workers (Gate D6).
- The multipart parser still spools an oversized body to disk before the
  handler runs; a reverse-proxy body-size limit (e.g. nginx
  `client_max_body_size`) should back this up in deployment.
- Rate limiter is still in-memory/single-instance (Gate D6).
- Validation proves *what kind of file* something is, not that the detector's
  score means anything: detection accuracy is unchanged and still unvalidated
  (Gate D3).

## Next gate
Gate D3 — evaluation harness.
