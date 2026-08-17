from __future__ import annotations

from app.jobs.base import JobExecutor
from app.jobs.registry import get, get_enabled


def get_job_executor(job_type: str) -> JobExecutor:
    return get(job_type)


def get_enabled_job_executor(job_type: str) -> JobExecutor:
    return get_enabled(job_type)
