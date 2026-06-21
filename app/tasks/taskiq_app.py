from __future__ import annotations

from taskiq_redis import ListQueueBroker

from app.core.config import settings

broker = ListQueueBroker(settings.REDIS_URL)

# Import task modules after broker creation so decorators register on this broker.
from app.tasks import jobs as _jobs  # noqa: E402,F401
