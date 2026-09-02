import uuid
from datetime import UTC, datetime

import pytest

from app.jobs import registry as job_registry
from app.business_packages.register import register_all_business_packages
from app.models.job import Job
from app.services.job_runtime import payload_hash


def _example_collect_handler():
    register_all_business_packages()
    return job_registry.get("example_collect")


@pytest.mark.asyncio
async def test_example_collect_executes_configured_sleep(monkeypatch):
    handler = _example_collect_handler()
    params = {"items": ["a", "b"], "sleep_seconds": 2}
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr("app.business_packages.examples.asyncio.sleep", fake_sleep)
    job = Job(
        id=uuid.uuid4(),
        caller_id="caller-1",
        job_type="example_collect",
        status="running",
        job_params_ref={
            "storage": "db_inline",
            "type": "json",
            "name": "job_params",
            "payload": params,
        },
        job_params_hash=payload_hash(params),
        created_at=datetime(2026, 6, 20, 10, 0, tzinfo=UTC),
        updated_at=datetime(2026, 6, 20, 10, 0, tzinfo=UTC),
    )

    result = await handler.execute(job, object())

    assert slept == [2]
    assert result == {"items": ["a", "b"], "count": 2}
