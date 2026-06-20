import uuid
from datetime import UTC, datetime

import pytest

from app.core import workflow_registry
from app.models.job import AIJob, AIJobWorkItem
from app.workflows.register import register_all_workflows


def _job_test_add_handler():
    register_all_workflows()
    return workflow_registry.get("job_test_add")


def test_job_test_add_is_registered_and_validates_params():
    handler = _job_test_add_handler()

    assert handler.job_type == "job_test_add"
    assert handler.normalize_job_params({"a": 2, "b": 3}) == {"a": 2, "b": 3}

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
async def test_job_test_add_executes_addition_and_returns_sum():
    handler = _job_test_add_handler()
    job_id = uuid.uuid4()
    job = AIJob(
        id=job_id,
        caller_id="caller-1",
        client_request_id="client-add-1",
        job_type="job_test_add",
        status="running",
        created_at=datetime(2026, 6, 20, 10, 0, tzinfo=UTC),
        updated_at=datetime(2026, 6, 20, 10, 0, tzinfo=UTC),
    )
    item = AIJobWorkItem(
        id=uuid.uuid4(),
        job_id=job_id,
        name="job_test_add.whole",
        kind="whole",
        chunk_index=0,
        input_ref={
            "storage": "db_inline",
            "type": "json",
            "name": "job_params",
            "payload": {"a": 2, "b": 3},
        },
    )

    work_item_result = await handler.execute_standard_item(item, job, object())
    assert work_item_result == {"artifacts": [], "signals": {"a": 2, "b": 3, "result": 5}}

    canonical_result = handler.validate_canonical_result(work_item_result)
    public_result = handler.public_result(canonical_result)

    assert canonical_result == {"a": 2, "b": 3, "result": 5}
    assert public_result == {"a": 2, "b": 3, "result": 5}
    assert public_result["result"] == public_result["a"] + public_result["b"]
