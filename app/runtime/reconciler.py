from __future__ import annotations

from app.core.config import settings
from app.runtime.common import run_role_cli
from app.tasks.recovery import run_recovery_once


async def run_reconciler_once() -> dict:
    return await run_recovery_once()


def main() -> None:
    run_role_cli(
        role="reconciler",
        run_once=run_reconciler_once,
        default_interval_seconds=settings.job.recovery_interval_seconds,
    )


if __name__ == "__main__":
    main()
