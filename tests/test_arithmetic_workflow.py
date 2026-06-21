import uuid
from datetime import UTC, datetime

import pytest

from app.core import workflow_registry
from app.models.job import AIJob, AIJobWorkItem
from app.schemas.jobs import ArithmeticResult
from app.services.job_runtime import payload_hash
from app.workflows.register import register_all_workflows


def _arithmetic_handler():
    register_all_workflows()
    return workflow_registry.get("arithmetic")


def test_arithmetic_is_registered_and_validates_nonzero_params():
    handler = _arithmetic_handler()

    assert handler.job_type == "arithmetic"
    assert handler.normalize_job_params({"a": 8, "b": 2}) == {"a": 8, "b": 2}

    invalid_payloads = [
        {"a": 8},
        {"b": 2},
        {"a": 8, "b": 2, "extra": 4},
        {"a": "8", "b": 2},
        {"a": 0, "b": 2},
        {"a": 8, "b": 0},
    ]
    for payload in invalid_payloads:
        with pytest.raises(Exception):
            handler.normalize_job_params(payload)


@pytest.mark.parametrize(
    "payload",
    [
        {"a": True, "b": 2},
        {"a": 8, "b": True},
        {"a": float("nan"), "b": 2},
        {"a": 8, "b": float("inf")},
        {"a": -0.0, "b": 2},
    ],
)
def test_arithmetic_rejects_strict_number_boundary_values(payload):
    handler = _arithmetic_handler()

    with pytest.raises(Exception):
        handler.normalize_job_params(payload)


@pytest.mark.parametrize(
    "result",
    [
        {"a": True, "b": 2, "addition": 3, "subtraction": -1, "multiplication": 2, "division": 0.5},
        {"a": 1, "b": 2, "addition": float("nan"), "subtraction": -1, "multiplication": 2, "division": 0.5},
        {"a": 1, "b": 2, "addition": 3, "subtraction": -1, "multiplication": 2, "division": float("inf")},
    ],
)
def test_arithmetic_result_rejects_strict_number_boundary_values(result):
    with pytest.raises(Exception):
        ArithmeticResult.model_validate(result)


def test_arithmetic_builds_single_canvas_plan_from_job_params():
    handler = _arithmetic_handler()
    job_id = uuid.uuid4()
    params = {"a": 8, "b": 2}
    job = AIJob(
        id=job_id,
        caller_id="caller-1",
        client_request_id="client-arithmetic-plan-1",
        job_type="arithmetic",
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

    plan = handler.build_execution_plan(job)

    assert plan.execution_mode == "single"
    assert plan.chunk_count == 1
    assert plan.chunk_registry == [{"chunk_index": 1, "kind": "whole"}]
    assert len(plan.work_items) == 1
    assert plan.work_items[0].name == "arithmetic.whole"
    assert plan.work_items[0].kind == "whole"
    assert plan.work_items[0].input_data == params


@pytest.mark.asyncio
async def test_arithmetic_executes_four_operations_and_returns_public_result():
    handler = _arithmetic_handler()
    job_id = uuid.uuid4()
    job = AIJob(
        id=job_id,
        caller_id="caller-1",
        client_request_id="client-arithmetic-1",
        job_type="arithmetic",
        status="running",
        created_at=datetime(2026, 6, 20, 10, 0, tzinfo=UTC),
        updated_at=datetime(2026, 6, 20, 10, 0, tzinfo=UTC),
    )
    item = AIJobWorkItem(
        id=uuid.uuid4(),
        job_id=job_id,
        name="arithmetic.whole",
        kind="whole",
        chunk_index=0,
        input_ref={
            "storage": "db_inline",
            "type": "json",
            "name": "job_params",
            "payload": {"a": 8, "b": 2},
        },
    )

    work_item_result = await handler.execute_standard_item(item, job, object())
    assert work_item_result == {
        "artifacts": [],
        "signals": {
            "a": 8,
            "b": 2,
            "addition": 10,
            "subtraction": 6,
            "multiplication": 16,
            "division": 4.0,
        },
    }

    canonical_result = handler.validate_canonical_result(work_item_result)
    public_result = handler.public_result(canonical_result)

    assert canonical_result == {
        "a": 8,
        "b": 2,
        "addition": 10,
        "subtraction": 6,
        "multiplication": 16,
        "division": 4.0,
    }
    assert public_result == canonical_result
