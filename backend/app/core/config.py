"""
Application configuration using environment variables with sensible defaults.
All settings are overridable via env vars for production, staging, and local dev.
"""
from functools import lru_cache
from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    """Central configuration for the AI Synthetic Media Detector backend."""

    # API
    API_V1_PREFIX: str = "/v1"
    PROJECT_NAME: str = "AI Synthetic Media Detector"
    VERSION: str = "0.3.0"
    DEBUG: bool = False

    # Redis / Celery
    REDIS_URL: str = "redis://localhost:6379/0"
    CELERY_BROKER_URL: Optional[str] = None
    CELERY_RESULT_BACKEND: Optional[str] = None

    # Media processing
    MAX_UPLOAD_SIZE_MB: int = 100
    VIDEO_SAMPLE_FPS: float = 5.0
    VIDEO_MAX_FRAMES: int = 60          # hard cap for long videos
    TEMP_MEDIA_DIR: str = "/tmp/ai_detector_media"
    ALLOWED_IMAGE_TYPES: set = {"image/jpeg", "image/png", "image/webp", "image/gif"}
    ALLOWED_VIDEO_TYPES: set = {"video/mp4", "video/webm", "video/quicktime", "video/x-msvideo"}

    # Model / inference (placeholder paths – replace with real weights)
    MODEL_DEVICE: str = "cpu"           # "cuda" when GPU workers available
    MODEL_WEIGHTS_PATH: Optional[str] = None
    GRADCAM_TARGET_LAYER: str = "layer4"  # typical ResNet / EfficientNet final block

    # External fetch safety
    URL_FETCH_TIMEOUT_SEC: int = 30
    URL_MAX_REDIRECTS: int = 5
    URL_ALLOWED_SCHEMES: set = {"http", "https"}

    class Config:
        env_file = ".env"
        case_sensitive = True

    def get_celery_broker(self) -> str:
        return self.CELERY_BROKER_URL or self.REDIS_URL

    def get_celery_backend(self) -> str:
        return self.CELERY_RESULT_BACKEND or self.REDIS_URL


@lru_cache()
def get_settings() -> Settings:
    return Settings()
