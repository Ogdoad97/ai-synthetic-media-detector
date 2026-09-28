"""
FastAPI application entry-point for the AI Synthetic Media Detector.
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import router
from app.core.config import get_settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting %s v%s", settings.PROJECT_NAME, settings.VERSION)
    # Ensure temp dir exists
    from pathlib import Path
    Path(settings.TEMP_MEDIA_DIR).mkdir(parents=True, exist_ok=True)
    yield
    logger.info("Shutting down")


app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description=(
        "Cloud-first hybrid AI Synthetic Media Authenticity Detector. "
        "Supports image & video analysis with Grad-CAM visual heatmaps, "
        "async Celery workers, and remote URL inspection for browser extensions."
    ),
    lifespan=lifespan,
    # Previously settings.DEBUG existed but was never passed here, so it
    # had no effect. Wired explicitly; defaults to False.
    debug=settings.DEBUG,
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """
    Last-resort safety net (Gate D2): no unhandled exception may leak an
    internal message, stack trace, or filesystem path to a client. Full
    detail is logged server-side; the client always gets a fixed message.
    """
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error."})

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.get("/")
async def root():
    return {
        "service": settings.PROJECT_NAME,
        "version": settings.VERSION,
        "docs": "/docs",
        "health": f"{settings.API_V1_PREFIX}/health",
    }
