import celery
import pytest
import uuid

from app.core import workflow_registry
from app.models.job import AIJob, AIJobWorkItem
from app.services.job_planner import JobPlan, PlannedWorkItem, build_job_plan
from app.services.job_workflow import execute_work_item, finalize_job, plan_job
from app.tasks.jobs import _ensure_workflows_registered, fanout_after_mapping_task
from scripts.verify.e2e_backend_call import Config, api_path


class FakeDB:
    async def commit(self):
        pass

    async def refresh(self, _obj):
        pass


def test_task_entrypoint_registration_restores_short_drama_translation_handler():
    previous = dict(workflow_registry._registry)
    try:
        workflow_registry._registry.clear()
        with pytest.raises(KeyError):
            workflow_registry.get("short_drama.tag_schema.translation")

        _ensure_workflows_registered()

        handler = workflow_registry.get("short_drama.tag_schema.translation")
        assert handler.job_type == "short_drama.tag_schema.translation"
    finally:
        workflow_registry._registry.clear()
        workflow_registry._registry.update(previous)


def test_memory_fanout_dispatches_finalize_chord(monkeypatch):
    calls = {}

    def fake_group(signatures):
        calls["group"] = list(signatures)
        return calls["group"]

    def fake_chord(header, body):
        calls["header"] = header
        calls["body"] = body

        class FakeChord:
            def apply_async(self):
                calls["applied"] = True

        return FakeChord()

    monkeypatch.setattr(celery, "group", fake_group)
    monkeypatch.setattr(celery, "chord", fake_chord)

    result = fanout_after_mapping_task.run({}, "job-1", ["chunk-1", "chunk-2"])

    assert result == {"job_id": "job-1", "dispatched_chunks": 2}
    assert calls["applied"] is True
    assert len(calls["group"]) == 2
    assert calls["body"].task == "jobs.finalize_job"
    assert calls["body"].args == ("job-1",)


def test_e2e_api_path_uses_configured_prefix(tmp_path):
    config = Config(
        base_url="http://127.0.0.1:8100",
        api_prefix="/api/v1/custom-ai",
        service_api_key="token",
        input_file=tmp_path / "input.txt",
        model_id=None,
        output_bucket="local-dev",
        output_prefix="ai-jobs/test",
        output_region="local",
        poll_interval=1.0,
        timeout_seconds=10,
        storage_dir=tmp_path,
        repeat_input=1,
        dry_run=False,
        contract_only=False,
        contract_check=True,
        callback_port=0,
        callback_wait_seconds=1,
        callback_signing_secret="secret",
    )

    assert api_path(config, "/jobs") == "/api/v1/custom-ai/jobs"


@pytest.mark.asyncio
async def test_plan_job_reuses_existing_execution_plan(monkeypatch):
    job_id = uuid.uuid4()
    plan = build_job_plan("novel_localization.step1_localize", "短文本")
    job = AIJob(
        id=job_id,
        job_type="novel_localization.step1_localize",
        status="running",
        progress_percent=10,
        celery_task_id="root-task",
        execution_plan=plan.model_dump(),
    )
    existing_items = [
        AIJobWorkItem(
            id=uuid.uuid4(),
            job_id=job_id,
            name=item.name,
            kind=item.kind,
            chunk_index=item.chunk_index,
            input_ref={"oss_bucket": "bucket", "oss_key": f"runtime/{item.kind}.json", "oss_region": "region"},
        )
        for item in plan.work_items
    ]

    async def fake_get_job_or_404(_db, _job_id):
        return job

    async def fake_mark_running(*_args, **_kwargs):
        return True

    async def fake_list_work_items(_db, _job_id):
        return existing_items

    async def fail_create_work_item(*_args, **_kwargs):
        raise AssertionError("plan_job should reuse existing work items")

    async def fake_update_progress(*_args, **_kwargs):
        pass

    monkeypatch.setattr("app.services.job_workflow.get_job_or_404", fake_get_job_or_404)
    monkeypatch.setattr("app.services.job_workflow.JobRepo.mark_running", fake_mark_running)
    monkeypatch.setattr("app.services.job_workflow.JobRepo.list_work_items", fake_list_work_items)
    monkeypatch.setattr("app.services.job_workflow.JobRepo.create_work_item", fail_create_work_item)
    monkeypatch.setattr("app.services.job_workflow.JobRepo.update_progress", fake_update_progress)
    monkeypatch.setattr(
        "app.services.job_workflow._load_input_text",
        lambda _job: (_ for _ in ()).throw(AssertionError("input should not be reloaded")),
    )

    _job, reused_plan, item_ids = await plan_job(FakeDB(), job_id)

    assert reused_plan.model_dump() == plan.model_dump()
    assert item_ids == {f"{item.kind}:{item.chunk_index}": item.id for item in existing_items}


@pytest.mark.asyncio
async def test_plan_job_allows_custom_plan_without_text_source(monkeypatch):
    job_id = uuid.uuid4()
    job = AIJob(
        id=job_id,
        job_type="generic.custom",
        status="running",
        progress_percent=5,
        celery_task_id="root-task",
        job_params_ref={"oss_bucket": "bucket", "oss_key": "runtime/job_params.json", "oss_region": "region"},
    )
    plan = JobPlan(
        execution_mode="single",
        chunk_count=1,
        chunk_registry=[{"chunk_index": 1, "input": {"value": 1}}],
        work_items=[
            PlannedWorkItem(
                name="generic.custom.whole",
                kind="whole",
                chunk_index=0,
                input_data={"value": 1},
            )
        ],
    )
    created_item = AIJobWorkItem(
        id=uuid.uuid4(),
        job_id=job_id,
        name="generic.custom.whole",
        kind="whole",
        chunk_index=0,
        input_ref={"oss_bucket": "bucket", "oss_key": "runtime/whole.json", "oss_region": "region"},
    )

    class CustomHandler:
        large_artifact_keys = frozenset()

        def build_execution_plan(self, received_job):
            assert received_job.id == job_id
            return plan

    async def fake_get_job_or_404(_db, _job_id):
        return job

    async def fake_mark_running(*_args, **_kwargs):
        return True

    async def fake_list_work_items(_db, _job_id):
        return []

    async def fake_create_work_item(*_args, **_kwargs):
        return created_item

    async def fake_set_execution_plan(*_args, **_kwargs):
        pass

    async def fake_update_progress(*_args, **_kwargs):
        pass

    monkeypatch.setattr("app.services.job_workflow.get_job_or_404", fake_get_job_or_404)
    monkeypatch.setattr("app.services.job_workflow.JobRepo.mark_running", fake_mark_running)
    monkeypatch.setattr("app.services.job_workflow.JobRepo.list_work_items", fake_list_work_items)
    monkeypatch.setattr("app.services.job_workflow.JobRepo.create_work_item", fake_create_work_item)
    monkeypatch.setattr("app.services.job_workflow.JobRepo.set_execution_plan", fake_set_execution_plan)
    monkeypatch.setattr("app.services.job_workflow.JobRepo.update_progress", fake_update_progress)
    monkeypatch.setattr("app.core.workflow_registry.get", lambda job_type: CustomHandler())
    monkeypatch.setattr(
        "app.services.job_workflow._load_input_text",
        lambda _job: (_ for _ in ()).throw(AssertionError("custom plan should not load text")),
    )

    _job, planned, item_ids = await plan_job(FakeDB(), job_id)

    assert planned == plan
    assert item_ids == {"whole:0": created_item.id}


@pytest.mark.asyncio
async def test_finalize_waits_for_pending_work_items(monkeypatch):
    job_id = uuid.uuid4()
    job = AIJob(
        id=job_id,
        job_type="novel_localization.step1_localize",
        status="running",
        progress_percent=50,
        celery_task_id="root-task",
        execution_plan={"execution_mode": "chunked"},
    )
    chunk = AIJobWorkItem(
        id=uuid.uuid4(),
        job_id=job_id,
        name="chunk-1",
        kind="chunk",
        chunk_index=1,
        status="running",
    )
    merge = AIJobWorkItem(
        id=uuid.uuid4(),
        job_id=job_id,
        name="merge",
        kind="merge",
        chunk_index=2,
        status="queued",
    )

    async def fake_get_job_or_404(_db, _job_id):
        return job

    async def fake_list_work_items(_db, _job_id):
        return [chunk, merge]

    async def fake_update_progress(*_args, **_kwargs):
        pass

    monkeypatch.setattr("app.services.job_workflow.get_job_or_404", fake_get_job_or_404)
    monkeypatch.setattr("app.services.job_workflow.JobRepo.list_work_items", fake_list_work_items)
    monkeypatch.setattr("app.services.job_workflow.JobRepo.update_progress", fake_update_progress)
    monkeypatch.setattr(
        "app.services.job_workflow._persist_large_artifacts",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("pending items must not merge")),
    )

    result = await finalize_job(FakeDB(), job_id)

    assert result["status"] == "waiting"
    assert result["pending_items"] == [str(chunk.id)]


@pytest.mark.asyncio
async def test_execute_work_item_allows_custom_runtime_without_model(monkeypatch):
    job_id = uuid.uuid4()
    item_id = uuid.uuid4()
    job = AIJob(
        id=job_id,
        job_type="generic.custom",
        status="running",
        progress_percent=10,
        celery_task_id="root-task",
        job_params_ref={"oss_bucket": "bucket", "oss_key": "runtime/job_params.json", "oss_region": "region"},
    )
    item = AIJobWorkItem(
        id=item_id,
        job_id=job_id,
        name="whole",
        kind="whole",
        chunk_index=0,
        status="queued",
        input_ref={"oss_bucket": "bucket", "oss_key": "runtime/whole.json", "oss_region": "region"},
    )
    succeeded = {}

    class CustomHandler:
        canvas_pattern = "single"
        large_artifact_keys = frozenset()

        async def execute_standard_item(self, received_item, received_job, _db):
            assert received_item.id == item_id
            assert received_job.id == job_id
            return {"artifacts": [], "signals": {"custom": True}}

    async def fake_get_job_or_404(_db, _job_id):
        return job

    async def fake_get_work_item(_db, _item_id):
        return item

    async def fake_claim(*_args, **_kwargs):
        return True

    async def fake_update_progress(*_args, **_kwargs):
        pass

    async def fake_mark_succeeded(_db, _item_id, result):
        succeeded["result"] = result

    monkeypatch.setattr("app.services.job_workflow.get_job_or_404", fake_get_job_or_404)
    monkeypatch.setattr("app.services.job_workflow.JobRepo.get_work_item", fake_get_work_item)
    monkeypatch.setattr("app.services.job_workflow.JobRepo.claim_work_item_for_execution", fake_claim)
    monkeypatch.setattr("app.services.job_workflow.JobRepo.update_progress", fake_update_progress)
    monkeypatch.setattr("app.services.job_workflow.JobRepo.mark_work_item_succeeded", fake_mark_succeeded)
    monkeypatch.setattr("app.core.workflow_registry.get", lambda job_type: CustomHandler())
    monkeypatch.setattr(
        "app.services.job_workflow.run_ai_job",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("LLM runtime should not be called")),
    )

    result = await execute_work_item(FakeDB(), job_id=job_id, item_id=item_id, celery_task_id="task-1")

    assert result == {"work_item_id": str(item_id), "kind": "whole", "chunk_index": 0}
    assert succeeded["result"] == {"artifacts": [], "signals": {"custom": True}}
