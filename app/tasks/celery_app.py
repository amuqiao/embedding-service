import logging
import threading
import time

from celery import Celery
from celery.signals import worker_ready

from app.infrastructure.config import settings

logger = logging.getLogger(__name__)
_recovery_loop_started = False

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
    task_soft_time_limit=settings.celery_soft_time_limit,
    task_time_limit=settings.celery_time_limit,
    task_max_retries=settings.CELERY_MAX_RETRIES,
    task_default_retry_delay=settings.CELERY_RETRY_DELAY,
    result_expires=settings.CELERY_RESULT_EXPIRES,
)


def _run_recovery_once() -> None:
    from app.tasks.recovery import run_recovery
    try:
        result = run_recovery()
        logger.info("recovery completed: %s", result)
    except Exception:
        logger.exception("recovery failed, continuing")


def _recovery_loop() -> None:
    while True:
        time.sleep(settings.JOB_RECOVERY_INTERVAL_SECONDS)
        _run_recovery_once()


@worker_ready.connect
def _on_worker_ready(sender, **kwargs):
    global _recovery_loop_started
    logger.info("worker_ready: running startup recovery scan")
    _run_recovery_once()
    if not _recovery_loop_started:
        thread = threading.Thread(target=_recovery_loop, name="job-recovery-loop", daemon=True)
        thread.start()
        _recovery_loop_started = True
        logger.info("worker_ready: recovery loop started interval_seconds=%d", settings.JOB_RECOVERY_INTERVAL_SECONDS)
