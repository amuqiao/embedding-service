from __future__ import annotations

from collections.abc import Sequence

from app.business_packages.base import RegisterJobExecutor
from app.jobs.base import JobExecutor


RegisterExecutor = RegisterJobExecutor


def register_executor_classes(
    register: RegisterExecutor,
    executor_classes: Sequence[type[JobExecutor]],
) -> None:
    for executor_cls in executor_classes:
        register(executor_cls())
