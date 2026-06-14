import uuid

import pytest

from app.models.job import AIJob, AIJobWorkItem
from app.schemas.jobs import CreateJobRequest
from app.services.jobs import _validate_create_request
from app.services.job_workflow import execute_work_item, finalize_job, plan_job


class FakeDB:
    async def commit(self):
        pass

    async def refresh(self, _obj):
        pass


def test_generic_echo_create_validation_uses_job_params_only(monkeypatch):
    payload = CreateJobRequest.model_validate(
        {
            "job_type": "generic.echo",
            "job_params": {"value": {"hello": "world"}, "label": "Echo"},
            "metadata": {"caller_task_id": "echo-1"},
        }
    )
    monkeypatch.setattr(
        "app.services.jobs.get_enabled_model",
        lambda model_id: (_ for _ in ()).throw(AssertionError("model registry should not be called")),
    )

    _handler, job_params, runtime_fields = _validate_create_request(payload)

    assert job_params == {"value": {"hello": "world"}, "label": "Echo"}
    assert runtime_fields == {}


def test_generic_echo_rejects_missing_value():
    payload = CreateJobRequest.model_validate(
        {
            "job_type": "generic.echo",
            "job_params": {"label": "Echo"},
        }
    )

    with pytest.raises(Exception, match="job_params does not match job_type schema"):
        _validate_create_request(payload)


@pytest.mark.asyncio
async def test_generic_echo_workflow_runs_without_llm_or_text_source(monkeypatch):
    job_id = uuid.uuid4()
    item_id = uuid.uuid4()
    job = AIJob(
        id=job_id,
        job_type="generic.echo",
        model_id=None,
        status="running",
        progress_percent=5,
        celery_task_id="root-task",
        input_payload={
            "job_params": {"value": {"hello": "world"}, "label": "Echo"},
            "metadata": {"caller_task_id": "echo-1"},
        },
        output_payload={},
        callback_payload={},
        prompt_payload={},
    )
    created_item = AIJobWorkItem(
        id=item_id,
        job_id=job_id,
        name="generic.echo.whole",
        kind="whole",
        chunk_index=0,
        status="queued",
        input_payload={"value": {"hello": "world"}, "label": "Echo"},
    )
    marked = {}

    async def fake_get_job_or_404(_db, _job_id):
        return job

    async def fake_mark_running(*_args, **_kwargs):
        return True

    async def fake_list_work_items(_db, _job_id):
        return [created_item] if marked.get("planned") else []

    async def fake_create_work_item(*_args, **kwargs):
        marked["planned"] = True
        assert kwargs["input_payload"] == {"value": {"hello": "world"}, "label": "Echo"}
        return created_item

    async def fake_set_execution_plan(_db, _job_id, *, execution_mode, execution_plan):
        job.execution_mode = execution_mode
        job.execution_plan = execution_plan

    async def fake_update_progress(*_args, **_kwargs):
        pass

    async def fake_get_work_item(_db, _item_id):
        return created_item

    async def fake_claim_work_item(*_args, **_kwargs):
        return True

    async def fake_mark_work_item_succeeded(_db, _item_id, result_payload):
        created_item.status = "succeeded"
        created_item.result_payload = result_payload

    async def fake_mark_succeeded(_db, _job_id, *, celery_task_id, result_payload):
        job.status = "succeeded"
        job.result_payload = result_payload
        return True

    async def fake_deliver_callback(_job_id):
        return True

    monkeypatch.setattr("app.services.job_workflow.get_job_or_404", fake_get_job_or_404)
    monkeypatch.setattr("app.services.job_workflow.JobRepo.mark_running", fake_mark_running)
    monkeypatch.setattr("app.services.job_workflow.JobRepo.list_work_items", fake_list_work_items)
    monkeypatch.setattr("app.services.job_workflow.JobRepo.create_work_item", fake_create_work_item)
    monkeypatch.setattr("app.services.job_workflow.JobRepo.set_execution_plan", fake_set_execution_plan)
    monkeypatch.setattr("app.services.job_workflow.JobRepo.update_progress", fake_update_progress)
    monkeypatch.setattr("app.services.job_workflow.JobRepo.get_work_item", fake_get_work_item)
    monkeypatch.setattr("app.services.job_workflow.JobRepo.claim_work_item_for_execution", fake_claim_work_item)
    monkeypatch.setattr("app.services.job_workflow.JobRepo.mark_work_item_succeeded", fake_mark_work_item_succeeded)
    monkeypatch.setattr("app.services.job_workflow.JobRepo.mark_succeeded", fake_mark_succeeded)
    monkeypatch.setattr(
        "app.services.job_workflow._load_input_text",
        lambda _job: (_ for _ in ()).throw(AssertionError("generic.echo should not load text")),
    )
    monkeypatch.setattr(
        "app.services.job_workflow.run_ai_job",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("generic.echo should not call LLM")),
    )
    monkeypatch.setattr("app.tasks.jobs.deliver_callback_for_job", fake_deliver_callback)

    _job, plan, item_ids = await plan_job(FakeDB(), job_id)
    executed = await execute_work_item(FakeDB(), job_id=job_id, item_id=item_id, celery_task_id="task-1")
    finalized = await finalize_job(FakeDB(), job_id)

    assert plan.execution_mode == "single"
    assert item_ids == {"whole:0": item_id}
    assert executed == {"work_item_id": str(item_id), "kind": "whole", "chunk_index": 0}
    assert finalized == {"job_id": str(job_id), "status": "succeeded"}
    assert job.result_payload["artifacts"][0]["content"] == {"hello": "world"}
    assert job.result_payload["artifacts"][0]["label"] == "Echo"
    assert job.result_payload["signals"] == {"echoed": True}
