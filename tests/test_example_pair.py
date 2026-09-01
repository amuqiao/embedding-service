import uuid
from datetime import UTC, datetime

import pytest

from app.jobs import registry as job_registry
from app.models.job import Job
from app.services.job_runtime import payload_hash
from app.business_packages.register import register_all_business_packages


def _example_pair_handler():
    register_all_business_packages()
    return job_registry.get("example_pair")


def test_example_pair_is_registered_and_validates_params():
    handler = _example_pair_handler()

    assert handler.job_type == "example_pair"
    assert handler.normalize_job_params({"a": 2, "b": 3}) == {"a": 2, "b": 3}
    assert handler.normalize_job_params({"a": 2, "b": 3, "sleep_seconds": 0}) == {
        "a": 2,
        "b": 3,
        "sleep_seconds": 0,
    }

    invalid_payloads = [
        {"a": 2},
        {"b": 3},
        {"a": 2, "b": 3, "extra": 4},
        {"a": "2", "b": 3},
    ]
    for payload in invalid_payloads:
        with pytest.raises(Exception):
            handler.normalize_job_params(payload)


@pytest.mark.asyncio
async def test_example_pair_executes_addition_and_returns_sum():
    handler = _example_pair_handler()
    job_id = uuid.uuid4()
    job = Job(
        id=job_id,
        caller_id="caller-1",
        client_request_id="client-add-1",
        job_type="example_pair",
        status="running",
        job_params_ref={
            "storage": "db_inline",
            "type": "json",
            "name": "job_params",
            "payload": {"a": 2, "b": 3},
        },
        job_params_hash=payload_hash({"a": 2, "b": 3}),
        created_at=datetime(2026, 6, 20, 10, 0, tzinfo=UTC),
        updated_at=datetime(2026, 6, 20, 10, 0, tzinfo=UTC),
    )

    job_result = await handler.execute(job, object())
    assert job_result == {"a": 2, "b": 3, "result": 5}

    canonical_result = handler.validate_canonical_result(job_result)
    public_result = handler.public_result(canonical_result)

    assert canonical_result == {"a": 2, "b": 3, "result": 5}
    assert public_result == {"a": 2, "b": 3, "result": 5}
    assert public_result["result"] == public_result["a"] + public_result["b"]
