from __future__ import annotations

from app.jobs.base import JobExecutor
from app.jobs.registry import get


def get_job_executor(job_type: str) -> JobExecutor:
    return get(job_type)
