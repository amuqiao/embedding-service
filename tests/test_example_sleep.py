from __future__ import annotations

import uuid

import pytest

from app.jobs.types import examples
from app.jobs.types.examples import ExampleSleepJob
from app.models.job import Job
from app.services.job_runtime import payload_hash, write_runtime_json


@pytest.mark.asyncio
async def test_example_sleep_can_simulate_execution_delay(monkeypatch):
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(examples.asyncio, "sleep", fake_sleep)
    params = {"message": "load", "repeat": 2, "sleep_seconds": 15}
    job = Job(id=uuid.uuid4(), job_type="example_sleep", job_params_hash=payload_hash(params))
    job.job_params_ref = write_runtime_json(job, "job_params.json", params)

    result = await ExampleSleepJob()._execute(job, None)

    assert slept == [15]
    assert result == {"message": "load", "repeated": ["load", "load"], "count": 2}
