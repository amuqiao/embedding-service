from __future__ import annotations

from taskiq_redis import ListQueueBroker, RedisStreamBroker

from app.core.config import settings


def _build_broker():
    if settings.broker.kind == "redis_stream":
        return RedisStreamBroker(settings.broker.redis_url)
    if settings.broker.kind == "redis_list":
        return ListQueueBroker(settings.broker.redis_url)
    raise RuntimeError(f"unsupported TASKIQ_BROKER_KIND: {settings.broker.kind}")


broker = _build_broker()

# Import task modules after broker creation so decorators register on this broker.
from app.tasks import jobs as _jobs  # noqa: E402,F401
