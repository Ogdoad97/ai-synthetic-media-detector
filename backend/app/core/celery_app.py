"""
Celery application factory.
Uses Redis as both broker and result backend (proven simple + high-performance pattern).
"""
from celery import Celery
from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "ai_media_detector",
    broker=settings.get_celery_broker(),
    backend=settings.get_celery_backend(),
    include=["app.workers.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,          # one heavy media task at a time per worker
    task_time_limit=600,                   # 10 min hard limit
    task_soft_time_limit=540,
    result_expires=3600 * 24,              # keep results 24 h
    worker_max_tasks_per_child=50,         # recycle workers to avoid memory leaks
)

# Optional named queues for future scaling (image vs video)
celery_app.conf.task_routes = {
    "app.workers.tasks.process_image_task": {"queue": "media"},
    "app.workers.tasks.process_video_task": {"queue": "media"},
    "app.workers.tasks.process_url_task": {"queue": "media"},
}
