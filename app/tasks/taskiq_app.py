from __future__ import annotations

from taskiq.events import TaskiqEvents
from taskiq_redis import ListQueueBroker, RedisStreamBroker

from app.core.config import settings
from app.core.database import close_db_engine, init_db_engine


def _build_broker():
    if settings.broker.kind == "redis_stream":
        return RedisStreamBroker(settings.broker.redis_url)
    if settings.broker.kind == "redis_list":
        return ListQueueBroker(settings.broker.redis_url)
    raise RuntimeError(f"unsupported TASKIQ_BROKER_KIND: {settings.broker.kind}")


broker = _build_broker()


@broker.on_event(TaskiqEvents.WORKER_STARTUP)
async def _worker_startup(_state) -> None:
    init_db_engine()


@broker.on_event(TaskiqEvents.WORKER_SHUTDOWN)
async def _worker_shutdown(_state) -> None:
    await close_db_engine()

# Import task modules after broker creation so decorators register on this broker.
from app.tasks import jobs as _jobs  # noqa: E402,F401
