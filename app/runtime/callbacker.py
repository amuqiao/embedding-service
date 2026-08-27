from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.repositories.job_repo import JobRepo
from app.runtime.common import run_once_with_session, run_role_cli
from app.tasks.jobs import deliver_callback_for_job


async def run_callbacker_once() -> dict[str, Any]:
    return await run_once_with_session(_run_callbacker_once)


async def _run_callbacker_once(db: AsyncSession) -> dict[str, Any]:
    due_jobs = await JobRepo.find_due_callbacks(
        db,
        now=datetime.now(timezone.utc),
        max_attempts=settings.callback.max_delivery_attempts,
        limit=settings.job.recovery_callback_batch_size,
    )
    job_ids = tuple(dict.fromkeys(job.id for job in due_jobs))
    await db.commit()

    delivered = 0
    for job_id in job_ids:
        if await deliver_callback_for_job(job_id):
            delivered += 1

    return {
        "due": len(due_jobs),
        "jobs": len(job_ids),
        "delivered": delivered,
        "pending": len(job_ids) - delivered,
    }


def main() -> None:
    run_role_cli(
        role="callbacker",
        run_once=run_callbacker_once,
        default_interval_seconds=5,
    )


if __name__ == "__main__":
    main()
