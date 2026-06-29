import logging
import sys
from contextvars import ContextVar
from typing import Any

from app.core.config import settings

_request_id_var: ContextVar[str] = ContextVar("request_id", default="-")


class LogEvent:
    REQUEST_COMPLETED = "request_completed"
    REQUEST_FAILED = "request_failed"
    JOB_CREATED = "job_created"
    JOB_PUBLISH_REQUESTED = "job_publish_requested"
    JOB_PUBLISHED = "job_published"
    JOB_STARTED = "job_started"
    JOB_PROGRESSED = "job_progressed"
    JOB_SUCCEEDED = "job_succeeded"
    JOB_FAILED = "job_failed"
    JOB_RECOVERED = "job_recovered"
    POSTER_TITLE_IMAGE_STYLE_PROBE_COMPLETED = "poster_title_image_style_probe_completed"
    POSTER_TITLE_IMAGE_OBJECT_STORED = "poster_title_image_object_stored"
    POSTER_TITLE_IMAGE_ITEM_COMPLETED = "poster_title_image_item_completed"
    POSTER_TITLE_IMAGE_JOIN_COMPLETED = "poster_title_image_join_completed"
    CALLBACK_SCHEDULED = "callback_scheduled"
    CALLBACK_DELIVERED = "callback_delivered"
    CALLBACK_FAILED = "callback_failed"


_LOG_EVENTS = frozenset(
    {
        LogEvent.REQUEST_COMPLETED,
        LogEvent.REQUEST_FAILED,
        LogEvent.JOB_CREATED,
        LogEvent.JOB_PUBLISH_REQUESTED,
        LogEvent.JOB_PUBLISHED,
        LogEvent.JOB_STARTED,
        LogEvent.JOB_PROGRESSED,
        LogEvent.JOB_SUCCEEDED,
        LogEvent.JOB_FAILED,
        LogEvent.JOB_RECOVERED,
        LogEvent.POSTER_TITLE_IMAGE_STYLE_PROBE_COMPLETED,
        LogEvent.POSTER_TITLE_IMAGE_OBJECT_STORED,
        LogEvent.POSTER_TITLE_IMAGE_ITEM_COMPLETED,
        LogEvent.POSTER_TITLE_IMAGE_JOIN_COMPLETED,
        LogEvent.CALLBACK_SCHEDULED,
        LogEvent.CALLBACK_DELIVERED,
        LogEvent.CALLBACK_FAILED,
    }
)


def set_request_id(request_id: str) -> None:
    _request_id_var.set(request_id)


def all_log_events() -> set[str]:
    return set(_LOG_EVENTS)


def format_log_fields(**fields: Any) -> str:
    parts: list[str] = []
    for key, value in fields.items():
        if value is None:
            continue
        text = str(value).replace("\n", "\\n")
        parts.append(f"{key}={text}")
    return " ".join(parts)


def log_event(logger: logging.Logger, level: int, event: str, **fields: Any) -> None:
    if event not in _LOG_EVENTS:
        raise ValueError(f"unknown log event: {event}")
    logger.log(level, "%s", format_log_fields(event=event, **fields))


class _RequestIDFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id_var.get()
        return True


def configure_logging() -> None:
    level = getattr(logging, settings.observability.log_level.upper(), logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)
    handler.addFilter(_RequestIDFilter())
    handler.setFormatter(logging.Formatter(
        "%(asctime)s level=%(levelname)s logger=%(name)s request_id=%(request_id)s %(message)s"
    ))
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(handler)
