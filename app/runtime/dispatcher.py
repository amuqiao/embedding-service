from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.repositories.job_repo import JobRepo
from app.runtime.common import run_once_with_session, run_role_cli
from app.tasks.jobs import TaskiqPublishDeferredError, publish_job_attempt

logger = logging.getLogger(__name__)


async def run_dispatcher_once() -> dict[str, Any]:
    return await run_once_with_session(_run_dispatcher_once)


async def _run_dispatcher_once(db: AsyncSession) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    due_dispatches = await JobRepo.find_due_dispatches(
        db,
        now,
        limit=settings.job.recovery_batch_size,
    )
    attempt_ids = tuple(dict.fromkeys(dispatch.attempt_id for dispatch in due_dispatches))
    await db.commit()

    published = 0
    deferred = 0
    for attempt_id in attempt_ids:
        try:
            await publish_job_attempt(attempt_id)
            published += 1
        except TaskiqPublishDeferredError:
            deferred += 1
            logger.exception("dispatcher_publish_deferred attempt_id=%s", attempt_id)

    return {
        "due": len(due_dispatches),
        "attempts": len(attempt_ids),
        "published": published,
        "deferred": deferred,
    }


def main() -> None:
    run_role_cli(
        role="dispatcher",
        run_once=run_dispatcher_once,
        default_interval_seconds=1,
    )


if __name__ == "__main__":
    main()
