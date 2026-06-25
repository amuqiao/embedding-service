from __future__ import annotations

import uuid

import pytest

from app.jobs.types import job_test_echo
from app.jobs.types.job_test_echo import JobTestEchoJob
from app.models.job import Job
from app.services.job_runtime import payload_hash, write_runtime_json


@pytest.mark.asyncio
async def test_job_test_echo_can_simulate_execution_delay(monkeypatch):
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(job_test_echo.asyncio, "sleep", fake_sleep)
    params = {"message": "load", "repeat": 2, "sleep_seconds": 15}
    job = Job(id=uuid.uuid4(), job_type="job_test_echo", job_params_hash=payload_hash(params))
    job.job_params_ref = write_runtime_json(job, "job_params.json", params)

    result = await JobTestEchoJob()._execute(job, None)

    assert slept == [15]
    assert result == {"message": "load", "repeated": ["load", "load"], "count": 2}
