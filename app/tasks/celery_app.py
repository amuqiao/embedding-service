import logging

from celery import Celery
from celery.schedules import crontab
from celery.signals import worker_ready

from app.infrastructure.config import settings

logger = logging.getLogger(__name__)

celery_app = Celery(
    "cms_novel_localize",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=["app.tasks.jobs"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_soft_time_limit=settings.CELERY_SOFT_TIME_LIMIT,
    task_time_limit=settings.CELERY_TIME_LIMIT,
    task_max_retries=settings.CELERY_MAX_RETRIES,
    task_default_retry_delay=settings.CELERY_RETRY_DELAY,
    result_expires=settings.CELERY_RESULT_EXPIRES,
    beat_schedule={
        "cleanup-expired-jobs": {
            "task": "jobs.cleanup_expired",
            "schedule": crontab(hour=2, minute=0),
        }
    },
)


@worker_ready.connect
def _on_worker_ready(sender, **kwargs):
    from app.tasks.recovery import run_recovery
    logger.info("worker_ready: running startup recovery scan")
    try:
        result = run_recovery()
        logger.info("startup recovery completed: %s", result)
    except Exception:
        logger.exception("startup recovery failed, continuing")
