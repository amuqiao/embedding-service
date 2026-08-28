from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from app.jobs.base import JobExecutor


RegisterExecutor = Callable[[JobExecutor], JobExecutor]


@dataclass(frozen=True)
class JobTypePackage:
    name: str
    register: Callable[[RegisterExecutor], None]


def register_executor_classes(
    register: RegisterExecutor,
    executor_classes: Sequence[type[JobExecutor]],
) -> None:
    for executor_cls in executor_classes:
        register(executor_cls())
