import uuid
from datetime import UTC, datetime

import pytest

from app.core.exceptions import AppError
from app.jobs.runner import execute_job
from app.business_packages.job_real_llm_double_echo import JobRealLlmDoubleEchoJob
from app.business_packages.job_real_llm_echo import JobRealLlmEchoJob
from app.business_packages.register import register_all_business_packages
from app.models.job import Job, JobAttempt
from app.schemas.jobs import CreateJobRequest, JobRealLlmDoubleEchoParams, JobRealLlmEchoParams, JobResult
from app.services.job_runtime import payload_hash
from app.services.jobs import validate_create_contract

ROUTE_HASH = "sha256:" + "a" * 64


class _FakeDB:
    def __init__(self):
        self.commits = 0
        self.refreshed = []

    async def commit(self):
        self.commits += 1

    async def refresh(self, obj):
        self.refreshed.append(obj)


def _business_attempt(job: Job, *, lease_token: uuid.UUID | None = None) -> tuple[JobAttempt, uuid.UUID]:
    token = lease_token or uuid.uuid4()
    attempt = JobAttempt(
        id=job.active_attempt_id or uuid.uuid4(),
        job_id=job.id,
        purpose="business_execution",
        purpose_attempt_no=1,
        retry_chain_id=job.active_attempt_id or uuid.uuid4(),
        created_reason="initial",
        status="running",
        lease_token=token,
        timeout_seconds=180,
        policy_max_attempts=1,
        policy_backoff_kind="none",
        policy_retryable_error_codes=[],
        retry_policy_snapshot={
            "max_attempts": 1,
            "retry_delay_seconds": None,
            "backoff_kind": "none",
            "retryable_error_codes": [],
        },
    )
    return attempt, token


def test_job_real_llm_echo_builds_model_runtime_fields(monkeypatch):
    handler = JobRealLlmEchoJob()
    monkeypatch.setattr("app.business_packages.job_real_llm_echo.resolve_route_config_hash", lambda **_kwargs: ROUTE_HASH)

    runtime_fields = handler.runtime_job_fields(
        {
            "model_id": "gpt-5.4-mini",
            "instruction": "reply once",
            "source": {"inline": {"text": "hello"}},
        }
    )

    assert runtime_fields["model_id"] == "gpt-5.4-mini"
    assert runtime_fields["model_route_config_hash"] == ROUTE_HASH
    assert runtime_fields["prompt_payload"]["blocks"][0]["key"] == "user"
    assert "只输出一个 JSON object" in runtime_fields["prompt_payload"]["blocks"][0]["content"]


def test_job_real_llm_echo_rejects_large_inline_input():
    with pytest.raises(ValueError) as exc:
        JobRealLlmEchoParams.model_validate(
            {
                "model_id": "gpt-5.4-mini",
                "source": {"inline": {"text": "x" * 4097}},
            }
        )

    assert "source.inline.text must be at most 4096 bytes" in str(exc.value)


def test_job_real_llm_double_echo_rejects_large_inline_input():
    with pytest.raises(ValueError) as exc:
        JobRealLlmDoubleEchoParams.model_validate(
            {
                "model_id": "gpt-5.4-mini",
                "source": {"inline": {"text": "x" * 4097}},
            }
        )

    assert "source.inline.text must be at most 4096 bytes" in str(exc.value)


def test_job_real_llm_double_echo_builds_runtime_fields(monkeypatch):
    handler = JobRealLlmDoubleEchoJob()
    monkeypatch.setattr("app.business_packages.job_real_llm_double_echo.resolve_route_config_hash", lambda **_kwargs: ROUTE_HASH)

    runtime_fields = handler.runtime_job_fields(
        {
            "model_id": "gpt-5.4-mini",
            "first_instruction": "first",
            "second_instruction": "second",
            "source": {"inline": {"text": "hello"}},
        }
    )

    assert runtime_fields["model_id"] == "gpt-5.4-mini"
    assert runtime_fields["model_route_config_hash"] == ROUTE_HASH
    assert runtime_fields["first_prompt_payload"]["blocks"][0]["content"] == "first"
    assert runtime_fields["second_prompt_payload"]["blocks"][0]["content"] == "second"


def test_job_real_llm_echo_parse_output_accepts_job_result_json():
    handler = JobRealLlmEchoJob()

    result = handler.parse_output('{"artifacts":[],"signals":{"message":"ok"}}')

    assert result.model_dump() == {"artifacts": [], "signals": {"message": "ok"}}


def test_job_real_llm_echo_parse_output_rejects_non_json():
    handler = JobRealLlmEchoJob()

    with pytest.raises(AppError) as exc:
        handler.parse_output("ok")

    assert exc.value.code == "MODEL_OUTPUT_INVALID"


def test_job_real_llm_echo_rejects_callback_at_create_time():
    register_all_business_packages()
    payload = CreateJobRequest.model_validate(
        {
            "client_request_id": "real-llm-callback",
            "job_type": "job_real_llm_echo",
            "job_params": {
                "model_id": "gpt-5.4-mini",
                "source": {"inline": {"text": "hello"}},
            },
            "callback": {"url": "https://example.com/callback"},
        }
    )

    with pytest.raises(AppError) as exc:
        validate_create_contract(payload)

    assert exc.value.code == "INVALID_INPUT"
    assert "callback is not supported" in exc.value.message


def _running_real_llm_job() -> Job:
    job_id = uuid.uuid4()
    attempt_id = uuid.uuid4()
    params = {
        "model_id": "gpt-5.4-mini",
        "source": {"inline": {"text": "hello"}},
    }
    return Job(
        id=job_id,
        caller_id="caller-1",
        client_request_id="client-real-1",
        job_type="job_real_llm_echo",
        status="running",
        progress_percent=5,
        progress_stage="running",
        active_attempt_id=attempt_id,
        job_params_ref={
            "storage": "db_inline",
            "type": "json",
            "name": "job_params",
            "payload": params,
        },
        job_params_hash=payload_hash(params),
        runtime_ref={
            "storage": "db_inline",
            "type": "json",
            "name": "runtime",
            "payload": {
                "schema_version": 1,
                "job_type": "job_real_llm_echo",
                "job_params_hash": payload_hash(params),
                "runtime_fields": {
                    "model_id": "gpt-5.4-mini",
                    "model_route_config_hash": ROUTE_HASH,
                    "prompt_payload": {"blocks": [{"key": "user", "role": "user", "content": "reply"}]},
                    "_system": {"trigger_request_id": "req-real-1"},
                },
                "output_target": {
                    "type": "oss_prefix",
                    "oss_bucket": "local-dev",
                    "oss_prefix": f"{job_id}/",
                    "oss_region": "local",
                },
            },
        },
        created_at=datetime(2026, 6, 23, 10, 0, tzinfo=UTC),
        updated_at=datetime(2026, 6, 23, 10, 0, tzinfo=UTC),
    )


def _running_real_llm_double_job() -> Job:
    job = _running_real_llm_job()
    params = {
        "model_id": "gpt-5.4-mini",
        "source": {"inline": {"text": "hello"}},
        "first_instruction": "first",
        "second_instruction": "second",
    }
    job.job_type = "job_real_llm_double_echo"
    job.client_request_id = "client-real-double-1"
    job.job_params_ref = {
        "storage": "db_inline",
        "type": "json",
        "name": "job_params",
        "payload": params,
    }
    job.job_params_hash = payload_hash(params)
    job.runtime_ref["payload"]["job_type"] = "job_real_llm_double_echo"
    job.runtime_ref["payload"]["job_params_hash"] = payload_hash(params)
    job.runtime_ref["payload"]["runtime_fields"] = {
        "model_id": "gpt-5.4-mini",
        "model_route_config_hash": ROUTE_HASH,
        "first_prompt_payload": {"blocks": [{"key": "user", "role": "user", "content": "first"}]},
        "second_prompt_payload": {"blocks": [{"key": "user", "role": "user", "content": "second"}]},
        "_system": {"trigger_request_id": "req-real-double-1"},
    }
    return job


@pytest.mark.asyncio
async def test_job_real_llm_echo_uses_shared_llm_runtime(monkeypatch):
    register_all_business_packages()
    job = _running_real_llm_job()
    attempt, lease_token = _business_attempt(job)
    captured = {}

    async def fake_get_job_or_404(_db, _job_id):
        return job

    async def fake_get_attempt(_db, attempt_id):
        assert attempt_id == attempt.id
        return attempt

    async def fake_update_progress(_db, _job_id, *, progress_percent, progress_text, progress_stage, **_kwargs):
        job.progress_percent = progress_percent
        job.progress_stage = progress_stage
        return True

    async def fake_run_ai_job(**kwargs):
        captured.update(kwargs)
        return JobResult(artifacts=[], signals={"message": "ok"})

    async def fake_mark_succeeded(_db, _job_id, *, result, canonical_result, **_kwargs):
        captured["result"] = result
        captured["canonical_result"] = canonical_result
        job.status = "succeeded"
        return True

    monkeypatch.setattr("app.jobs.runner.get_job_or_404", fake_get_job_or_404)
    monkeypatch.setattr("app.jobs.runner.JobRepo.get_attempt", fake_get_attempt)
    monkeypatch.setattr("app.jobs.runner.JobRepo.update_progress", fake_update_progress)
    monkeypatch.setattr("app.jobs.runner.run_ai_job", fake_run_ai_job)
    monkeypatch.setattr("app.jobs.runner.JobRepo.mark_succeeded", fake_mark_succeeded)

    result = await execute_job(_FakeDB(), job.id, attempt_id=attempt.id, lease_token=lease_token)

    assert result == {"job_id": str(job.id), "status": "succeeded"}
    assert captured["job_type"] == "job_real_llm_echo"
    assert captured["model_id"] == "gpt-5.4-mini"
    assert captured["caller_id"] == "caller-1"
    assert captured["job_id"] == job.id
    assert captured["ai_scope_id"] == job.id
    assert captured["attempt_id"] == job.active_attempt_id
    assert captured["request_id"] == "req-real-1"
    assert captured["input_text"] == "hello"
    assert captured["canonical_result"] == {"artifacts": [], "signals": {"message": "ok"}}
    assert "callback_checked" not in captured


@pytest.mark.asyncio
async def test_job_real_llm_double_echo_calls_ledger_twice(monkeypatch):
    job = _running_real_llm_double_job()
    calls = []

    async def fake_generate_text_with_ledger(**kwargs):
        calls.append(kwargs)
        index = len(calls)
        return type("Result", (), {"text": f"message-{index}"})()

    monkeypatch.setattr(
        "app.business_packages.job_real_llm_double_echo.generate_text_with_ledger",
        fake_generate_text_with_ledger,
    )

    result = await JobRealLlmDoubleEchoJob()._execute(job, _FakeDB())

    assert result == {
        "artifacts": [],
        "signals": {
            "first_message": "message-1",
            "second_message": "message-2",
            "llm_call_count": 2,
        },
    }
    assert [call["operation"] for call in calls] == [
        "job_real_llm_double_echo.first",
        "job_real_llm_double_echo.second",
    ]
    assert calls[0]["scope_id"] == str(job.id)
    assert calls[0]["attempt_id"] == job.active_attempt_id
    assert calls[1]["request_id"] == "req-real-double-1"


@pytest.mark.asyncio
async def test_internal_real_llm_echo_bills_root_scope(monkeypatch):
    register_all_business_packages()
    job = _running_real_llm_job()
    attempt, lease_token = _business_attempt(job)
    root_id = uuid.uuid4()
    job.root_job_id = root_id
    job.workflow_node_key = "first"
    captured = {}

    async def fake_get_job_or_404(_db, _job_id):
        return job

    async def fake_get_attempt(_db, attempt_id):
        assert attempt_id == attempt.id
        return attempt

    async def fake_update_progress(_db, _job_id, *, progress_percent, progress_text, progress_stage, **_kwargs):
        job.progress_percent = progress_percent
        job.progress_stage = progress_stage
        return True

    async def fake_run_ai_job(**kwargs):
        captured.update(kwargs)
        return JobResult(artifacts=[], signals={"message": "ok"})

    async def fake_mark_succeeded(_db, _job_id, *, result, canonical_result, **_kwargs):
        job.status = "succeeded"
        return True

    async def fake_advance_workflow_after_child_terminal(_db, *, child_job):
        captured["advanced_child_id"] = child_job.id
        return {"advanced": True}

    async def fake_handle_workflow_advance_result(result):
        captured["workflow_advance"] = result

    async def fake_deliver_callback_for_job(_job_id):
        captured["callback_checked"] = True
        return False

    monkeypatch.setattr("app.jobs.runner.get_job_or_404", fake_get_job_or_404)
    monkeypatch.setattr("app.jobs.runner.JobRepo.get_attempt", fake_get_attempt)
    monkeypatch.setattr("app.jobs.runner.JobRepo.update_progress", fake_update_progress)
    monkeypatch.setattr("app.jobs.runner.run_ai_job", fake_run_ai_job)
    monkeypatch.setattr("app.jobs.runner.JobRepo.mark_succeeded", fake_mark_succeeded)
    monkeypatch.setattr(
        "app.jobs.runner.advance_workflow_after_child_terminal",
        fake_advance_workflow_after_child_terminal,
    )
    monkeypatch.setattr("app.tasks.jobs.handle_workflow_advance_result", fake_handle_workflow_advance_result)
    monkeypatch.setattr("app.tasks.jobs.deliver_callback_for_job", fake_deliver_callback_for_job)

    result = await execute_job(_FakeDB(), job.id, attempt_id=attempt.id, lease_token=lease_token)

    assert result == {"job_id": str(job.id), "status": "succeeded"}
    assert captured["job_id"] == job.id
    assert captured["ai_scope_id"] == root_id
    assert captured["advanced_child_id"] == job.id
    assert captured["workflow_advance"] == {"advanced": True}
    assert "callback_checked" not in captured


@pytest.mark.asyncio
async def test_internal_real_llm_double_echo_bills_root_scope(monkeypatch):
    job = _running_real_llm_double_job()
    root_id = uuid.uuid4()
    job.root_job_id = root_id
    job.workflow_node_key = "double"
    calls = []

    async def fake_generate_text_with_ledger(**kwargs):
        calls.append(kwargs)
        index = len(calls)
        return type("Result", (), {"text": f"message-{index}"})()

    monkeypatch.setattr(
        "app.business_packages.job_real_llm_double_echo.generate_text_with_ledger",
        fake_generate_text_with_ledger,
    )

    await JobRealLlmDoubleEchoJob()._execute(job, _FakeDB())

    assert [call["scope_id"] for call in calls] == [str(root_id), str(root_id)]
    assert {call["job_id"] for call in calls} == {job.id}
    assert {call["attempt_id"] for call in calls} == {job.active_attempt_id}
