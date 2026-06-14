import celery
import pytest
import uuid

from app.models.job import AIJob, AIJobWorkItem
from app.services.job_planner import build_job_plan
from app.services.job_workflow import finalize_job, plan_job
from app.tasks.jobs import fanout_after_mapping_task
from scripts.e2e_backend_call import Config, api_path


class FakeDB:
    async def commit(self):
        pass

    async def refresh(self, _obj):
        pass


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
        model_id="gpt-4.1",
        status="running",
        progress_percent=10,
        celery_task_id="root-task",
        execution_plan=plan.model_dump(),
        input_payload={"source": {"inline": {"text": "短文本"}}},
        output_payload={},
        callback_payload={},
        prompt_payload={},
    )
    existing_items = [
        AIJobWorkItem(
            id=uuid.uuid4(),
            job_id=job_id,
            name=item.name,
            kind=item.kind,
            chunk_index=item.chunk_index,
            input_payload=item.input_payload,
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
async def test_finalize_waits_for_pending_work_items(monkeypatch):
    job_id = uuid.uuid4()
    job = AIJob(
        id=job_id,
        job_type="novel_localization.step1_localize",
        model_id="gpt-4.1",
        status="running",
        progress_percent=50,
        celery_task_id="root-task",
        execution_mode="chunked",
        input_payload={},
        output_payload={},
        callback_payload={},
        prompt_payload={},
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
