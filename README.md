# AI Synthetic Media Authenticity Detector

**v0.3.0 development build** – Cloud-first hybrid architecture for screening images and videos for synthetic-media forensic signals with an explainable artifact heatmap. Independently security-audited this round — see `docs/AUDIT_HARDENING_REPORT_v0.3.0.md` for a DNS-rebinding SSRF fix, rate limiting, and an image decompression-bomb guard, all verified live against the running server, not just in tests.

## Architecture Overview

| Layer | Technology | Role |
|-------|------------|------|
| Mobile | React Native + Expo | Native share-sheet integration, heatmap toggle, evidence cards |
| Web / Extension | Next.js + Chrome MV3 (stubs) | Dashboard + context-menu URL inspection |
| API | FastAPI | Synchronous image analysis, async video / URL pipelines |
| Workers | Celery + Redis | 5 FPS video sampling, per-frame scoring, progress reporting |
| Vision | OpenCV forensic baseline | FFT + recompression/noise signals and artifact heatmap |
| Provenance | C2PA scaffolding | Cryptographic media authenticity checks |

**Key design principles**
- Heavy inference can stay on workers; the current baseline is CPU-capable and deterministic
- Explainable diagnostics – results expose multiple forensic signals + an artifact heatmap
- Production-ready async pattern – task_id + polling, Flower monitoring, Docker Compose

## Quick Start (Local)

```bash
# 1. Start infrastructure
docker compose up -d redis

# 2. Install backend
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# Only needed once app/services/heatmap_engine.py is wired to a real
# trained model - the current live analyzer doesn't use torch at all
# (confirmed by running the full test suite without it installed).
# pip install -r requirements-ml.txt

# 3. Run API
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# 4. Run Celery worker (separate terminal)
celery -A app.core.celery_app.celery_app worker --loglevel=info -Q media

# 5. (Optional) Flower dashboard
celery -A app.core.celery_app.celery_app flower --port=5555
```

Or simply:

```bash
docker compose up --build
```

API docs: http://localhost:8000/docs  
Flower:   http://localhost:5555

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/v1/analyze/image` | Synchronous image analysis (returns heatmap + findings) |
| `POST` | `/v1/analyze/video` | Async video analysis – returns `task_id` |
| `POST` | `/v1/analyze/url` | Fetch remote media by URL (Chrome extension use-case) |
| `GET`  | `/v1/tasks/{task_id}` | Poll status / retrieve final result |
| `GET`  | `/v1/health` | Liveness probe |

### Example – Analyze remote URL

```bash
curl -X POST http://localhost:8000/v1/analyze/url \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com/suspicious.jpg"}'
# → {"task_id": "...", "status": "queued", ...}

curl http://localhost:8000/v1/tasks/<task_id>
```

## Video Pipeline (tasks.py)

1. Video is saved to a shared temp volume.
2. Celery worker samples frames at **5 FPS** (configurable via `VIDEO_SAMPLE_FPS`), hard-capped at 60 frames.
3. Each frame is run through the detection model + Grad-CAM.
4. Scores are aggregated (mean confidence + AI-frame ratio).
5. Progress is published via `self.update_state` so clients can render a real progress bar.
6. Temp files are cleaned up automatically.

## Configuration

All settings live in `app/core/config.py` and are overridable by environment variables:

| Variable | Default | Purpose |
|----------|---------|---------|
| `REDIS_URL` | `redis://localhost:6379/0` | Broker + result backend |
| `VIDEO_SAMPLE_FPS` | `5.0` | Frame sampling rate |
| `VIDEO_MAX_FRAMES` | `60` | Safety cap for long videos |
| `MAX_UPLOAD_SIZE_MB` | `100` | Upload / download limit |
| `MODEL_DEVICE` | `cpu` | `cuda` when GPU workers available |
| `TEMP_MEDIA_DIR` | `/tmp/ai_detector_media` | Shared volume for workers |

## Project Layout

```
ai-synthetic-media-detector/
├── backend/
│   ├── app/
│   │   ├── api/          # FastAPI routes + Pydantic schemas
│   │   ├── core/         # Config, Celery factory, SSRF-safe fetch, rate limiter
│   │   ├── services/     # Analyzer (live) + Grad-CAM engine (unused scaffold, needs a trained model)
│   │   └── workers/      # Celery tasks (image / video / url)
│   ├── requirements.txt      # base - everything the live analyzer needs
│   ├── requirements-ml.txt   # torch/torchvision - only for the future trained-model path
│   ├── Dockerfile
│   └── tests/
├── mobile/
│   └── src/ScanScreen.tsx
├── docs/                 # Original handover PDF, dev status, audit reports
├── docker-compose.yml
└── README.md
```

## Important scientific status

v0.3.0 is an **experimental forensic baseline**, not a validated AI detector. Its score is a heuristic signal and must not be interpreted as a probability or proof of origin. The next accuracy milestone is a trained ensemble evaluated on held-out, cross-generator datasets with calibration, ROC/PR curves, and robustness tests. This round's audit (`docs/AUDIT_HARDENING_REPORT_v0.3.0.md`) hardened the system's *security and robustness* boundary — SSRF resistance, rate limiting, decompression-bomb protection — which is a separate concern from detection accuracy and doesn't change the scientific status above.

## Next Steps / Evolution Path

1. **Train and integrate a real ensemble** – add trained spatial + frequency detectors behind the existing analyzer interface.
2. **C2PA integration** – parse Content Credentials and distinguish present, valid, invalid, and absent provenance.
3. **S3 / object storage** – move temp media off local disk for multi-worker clusters.
4. **Web dashboard & Chrome extension** – thin clients that call `/v1/analyze/url` and poll task status.
5. **GPU workers** – set `MODEL_DEVICE=cuda` and scale Celery concurrency accordingly.
6. **File magic-byte validation** – uploads currently trust the client-supplied `Content-Type` header rather than sniffing actual file signatures (flagged in the v0.3.0 audit, not yet fixed).
7. **Redis-backed rate limiting** – the current limiter is in-memory/single-instance; needed before running more than one API replica.

## Evaluation, Strategy & Roadmap

A full evaluation of the idea, realistic use cases, open-source vs commercial paths, and a concrete multi-phase implementation plan is available here:

**→ [docs/EVALUATION_AND_ROADMAP.md](docs/EVALUATION_AND_ROADMAP.md)**

For the gate-by-gate engineering plan (matching this developer's other projects' discipline: build → test → verify → harden → promote → document), see **[docs/DEVELOPMENT_PLAN_GATES.md](docs/DEVELOPMENT_PLAN_GATES.md)**.

Key takeaways:
- Strong technical foundation with real market opportunity
- Passive detection alone is currently losing the arms race — treat every score as a *signal*
- Recommended path: open-source core + paid model updates / hosted API / vertical products
- Long-term evolution toward hybrid provenance (C2PA) + detection + human-in-the-loop authenticity infrastructure

## Contributing

This project is public and open to contributors — see
**[CONTRIBUTING.md](CONTRIBUTING.md)** for setup, ground rules, and the
current highest-priority gaps (file magic-byte validation, and — the
single most valuable contribution possible — a real evaluation harness
that turns "a heuristic signal" into a measured detector).

## License & Handover

This package is the complete technical handover, licensed under MIT (see `LICENSE`). All code contains comprehensive docstrings and type annotations so a new engineer can extend any component without reverse-engineering.

Built for maximum accuracy, explainability, and rapid adaptation to new generative models.
