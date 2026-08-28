from __future__ import annotations

import json
import uuid

import pytest

from app.core.exceptions import AppError
from app.jobs.types.example_lifecycle_probe import executor as lifecycle_probe_executor
from app.jobs.types.example_lifecycle_probe import ExampleLifecycleProbeJob
from app.jobs.types.example_lifecycle_probe.errors import EXAMPLE_LIFECYCLE_PROBE_FORCED_FAILURE
from app.models.job import Job
from app.schemas.jobs import CreateJobRequest, ExampleLifecycleProbeParams
from app.services.job_runtime import payload_hash, write_runtime_json
from app.services.jobs import validate_create_contract
from app.jobs.types.register import register_all_job_types


@pytest.mark.asyncio
async def test_lifecycle_probe_can_simulate_execution_delay(monkeypatch):
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(lifecycle_probe_executor.asyncio, "sleep", fake_sleep)
    params = {"probe_id": "probe-1", "message": "ready", "sleep_seconds": 3, "result_payload": "ok"}
    job = Job(id=uuid.uuid4(), job_type="example_lifecycle_probe", job_params_hash=payload_hash(params))
    job.job_params_ref = write_runtime_json(job, "job_params.json", params)

    result = await ExampleLifecycleProbeJob()._execute(job, None)

    assert slept == [3]
    assert result["probe_id"] == "probe-1"
    assert result["message"] == "ready"
    assert result["requested_sleep_seconds"] == 3
    assert result["fail"] is False
    assert result["result_payload"] == "ok"
    assert isinstance(result["elapsed_ms"], int)
    assert isinstance(result["worker_observed_at"], str)
    json.dumps(result)


@pytest.mark.asyncio
async def test_lifecycle_probe_can_force_failure(monkeypatch):
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(lifecycle_probe_executor.asyncio, "sleep", fake_sleep)
    params = {"probe_id": "probe-fail", "fail": True, "fail_after_seconds": 2}
    job = Job(id=uuid.uuid4(), job_type="example_lifecycle_probe", job_params_hash=payload_hash(params))
    job.job_params_ref = write_runtime_json(job, "job_params.json", params)

    with pytest.raises(AppError) as exc:
        await ExampleLifecycleProbeJob()._execute(job, None)

    assert slept == [2]
    assert exc.value.code == EXAMPLE_LIFECYCLE_PROBE_FORCED_FAILURE
    assert exc.value.details["job_type"] == "example_lifecycle_probe"


def test_lifecycle_probe_rejects_ambiguous_result_payload_config():
    with pytest.raises(ValueError, match="mutually exclusive"):
        ExampleLifecycleProbeParams.model_validate(
            {
                "result_payload": "ok",
                "result_size_bytes": 1,
            }
        )


def test_lifecycle_probe_rejects_fail_after_without_failure():
    with pytest.raises(ValueError, match="fail_after_seconds requires fail=true"):
        ExampleLifecycleProbeParams.model_validate({"fail": False, "fail_after_seconds": 1})


def test_lifecycle_probe_rejects_total_simulated_wait_over_limit():
    with pytest.raises(ValueError, match=r"sleep_seconds \+ fail_after_seconds must be <= 600"):
        ExampleLifecycleProbeParams.model_validate({"fail": True, "sleep_seconds": 500, "fail_after_seconds": 101})


def test_lifecycle_probe_allows_callback_at_create_time():
    register_all_job_types()
    payload = CreateJobRequest.model_validate(
        {
            "client_request_id": "example-lifecycle-callback",
            "job_type": "example_lifecycle_probe",
            "job_params": {"probe_id": "callback", "message": "callback"},
            "callback": {"url": "https://example.com/callback"},
        }
    )

    handler, params = validate_create_contract(payload)

    assert handler.job_type == "example_lifecycle_probe"
    assert params["probe_id"] == "callback"
