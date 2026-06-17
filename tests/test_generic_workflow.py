import json
import uuid

import pytest

from app.models.job import AIJob, AIJobWorkItem
from app.core.workflow_registry import WorkflowHandler
from app.schemas.jobs import CreateJobRequest, JobResult
from app.services.job_runtime import payload_hash
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
    job_params = {"value": {"hello": "world"}, "label": "Echo"}
    job_params_hash = payload_hash(job_params)
    job = AIJob(
        id=job_id,
        job_type="generic.echo",
        status="running",
        progress_percent=5,
        celery_task_id="root-task",
        job_params_ref={"oss_bucket": "bucket", "oss_key": "runtime/job_params.json", "oss_region": "region"},
        job_params_hash=job_params_hash,
        runtime_ref={"oss_bucket": "bucket", "oss_key": "runtime/runtime.json", "oss_region": "region"},
        metadata_={"caller_task_id": "echo-1"},
    )
    created_item = AIJobWorkItem(
        id=item_id,
        job_id=job_id,
        name="generic.echo.whole",
        kind="whole",
        chunk_index=0,
        status="queued",
    )
    marked = {}
    runtime_objects = {
        "runtime/job_params.json": job_params,
        "runtime/runtime.json": {
            "schema_version": 1,
            "job_type": "generic.echo",
            "job_params_hash": job_params_hash,
            "runtime_fields": {},
            "output_target": {
                "type": "oss_prefix",
                "oss_bucket": "bucket",
                "oss_prefix": "runtime-output/",
                "oss_region": "region",
            },
        },
    }

    async def fake_get_job_or_404(_db, _job_id):
        return job

    async def fake_mark_running(*_args, **_kwargs):
        return True

    async def fake_list_work_items(_db, _job_id):
        return [created_item] if marked.get("planned") else []

    async def fake_create_work_item(*_args, **kwargs):
        marked["planned"] = True
        assert "input_payload" not in kwargs
        assert kwargs["input_ref"]["oss_key"] == "runtime/work-items/whole-0.json"
        created_item.input_ref = kwargs["input_ref"]
        return created_item

    async def fake_set_execution_plan(_db, _job_id, *, execution_plan):
        assert "input_data" not in execution_plan["work_items"][0]
        job.execution_plan = execution_plan

    async def fake_update_progress(*_args, **_kwargs):
        pass

    async def fake_get_work_item(_db, _item_id):
        return created_item

    async def fake_claim_work_item(*_args, **_kwargs):
        return True

    async def fake_mark_work_item_succeeded(_db, _item_id, result):
        created_item.status = "succeeded"
        created_item.result = result

    async def fake_mark_succeeded(_db, _job_id, *, celery_task_id, result, canonical_result, canonical_result_ref=None):
        job.status = "succeeded"
        job.result = result
        job.canonical_result = canonical_result
        return True

    async def fake_deliver_callback(_job_id):
        return True

    def fake_write_runtime_json(_job, _name, payload):
        key = f"runtime/{_name}.json"
        runtime_objects[key] = payload
        return {"storage": "oss_object", "type": "json", "oss_bucket": "bucket", "oss_key": key, "oss_region": "region"}

    def fake_read_text(*, bucket, key, region):
        assert bucket == "bucket"
        assert region == "region"
        return json.dumps(runtime_objects[key], ensure_ascii=False)

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
    monkeypatch.setattr("app.services.job_workflow.write_runtime_json", fake_write_runtime_json)
    monkeypatch.setattr("app.services.job_runtime.storage.read_text", fake_read_text)
    monkeypatch.setattr("app.tasks.jobs.deliver_callback_for_job", fake_deliver_callback)

    _job, plan, item_ids = await plan_job(FakeDB(), job_id)
    executed = await execute_work_item(FakeDB(), job_id=job_id, item_id=item_id, celery_task_id="task-1")
    finalized = await finalize_job(FakeDB(), job_id)

    assert plan.execution_mode == "single"
    assert item_ids == {"whole:0": item_id}
    assert executed == {"work_item_id": str(item_id), "kind": "whole", "chunk_index": 0}
    assert finalized == {"job_id": str(job_id), "status": "succeeded"}
    assert job.result["artifacts"][0]["content"] == {"hello": "world"}
    assert job.result["artifacts"][0]["label"] == "Echo"
    assert job.result["signals"] == {"echoed": True}
    assert job.canonical_result == job.result


@pytest.mark.asyncio
async def test_finalize_runs_handler_hook_after_success_callback(monkeypatch):
    job_id = uuid.uuid4()
    job = AIJob(
        id=job_id,
        job_type="generic.echo",
        status="running",
        celery_task_id="root-task",
        execution_plan={"execution_mode": "single"},
    )
    whole = AIJobWorkItem(
        id=uuid.uuid4(),
        job_id=job_id,
        name="generic.echo.whole",
        kind="whole",
        chunk_index=0,
        status="succeeded",
        result={
            "artifacts": [{"key": "echo", "type": "json", "label": "Echo", "content": {"ok": True}}],
            "signals": {"echoed": True},
        },
    )
    calls: list[str] = []

    class Handler(WorkflowHandler):
        job_type = "generic.echo"
        canonical_result_schema = JobResult
        public_result_schema = JobResult
        large_artifact_keys = frozenset()

        async def after_success_callback(self, received_job, canonical_result, _db):
            assert received_job.id == job_id
            assert canonical_result["signals"] == {"echoed": True}
            calls.append("after_success_callback")

    async def fake_get_job_or_404(_db, _job_id):
        return job

    async def fake_list_work_items(_db, _job_id):
        return [whole]

    async def fake_mark_work_item_succeeded(_db, _item_id, result):
        whole.result = result

    async def fake_update_progress(*_args, **_kwargs):
        pass

    async def fake_mark_succeeded(_db, _job_id, *, celery_task_id, result, canonical_result, canonical_result_ref=None):
        job.status = "succeeded"
        job.result = result
        job.canonical_result = canonical_result
        return True

    async def fake_deliver_callback(_job_id):
        calls.append("callback")
        return True

    monkeypatch.setattr("app.services.job_workflow.get_job_or_404", fake_get_job_or_404)
    monkeypatch.setattr("app.services.job_workflow.JobRepo.list_work_items", fake_list_work_items)
    monkeypatch.setattr("app.services.job_workflow.JobRepo.mark_work_item_succeeded", fake_mark_work_item_succeeded)
    monkeypatch.setattr("app.services.job_workflow.JobRepo.update_progress", fake_update_progress)
    monkeypatch.setattr("app.services.job_workflow.JobRepo.mark_succeeded", fake_mark_succeeded)
    monkeypatch.setattr("app.core.workflow_registry.get", lambda _job_type: Handler())
    monkeypatch.setattr("app.tasks.jobs.deliver_callback_for_job", fake_deliver_callback)

    finalized = await finalize_job(FakeDB(), job_id)

    assert finalized == {"job_id": str(job_id), "status": "succeeded"}
    assert calls == ["callback", "after_success_callback"]
