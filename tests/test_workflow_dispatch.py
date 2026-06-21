import celery
import pytest
import uuid

from app.models.job import AIJob, AIJobWorkItem
from app.services.job_planner import JobPlan, PlannedWorkItem
from app.services.job_runtime import payload_hash
from app.services.job_workflow import build_canvas, execute_work_item, fail_job, finalize_job, plan_job
from app.tasks.jobs import fanout_after_mapping_task
from app.workflows.register import register_all_workflows


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
    assert calls["body"].args == ("job-1", 1)


@pytest.mark.asyncio
async def test_plan_job_reuses_existing_execution_plan(monkeypatch):
    job_id = uuid.uuid4()
    plan = JobPlan(
        execution_mode="single",
        chunk_count=1,
        chunk_registry=[{"chunk_index": 1, "input": {"value": 1}}],
        work_items=[
            PlannedWorkItem(
                name="test.custom.whole",
                kind="whole",
                chunk_index=0,
                input_data={"value": 1},
            )
        ],
    )
    job = AIJob(
        id=job_id,
        job_type="test.custom",
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

    async def fake_list_work_items(_db, _job_id, **_kwargs):
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
        job_type="test.custom",
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
                name="test.custom.whole",
                kind="whole",
                chunk_index=0,
                input_data={"value": 1},
            )
        ],
    )
    created_item = AIJobWorkItem(
        id=uuid.uuid4(),
        job_id=job_id,
        name="test.custom.whole",
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

    async def fake_list_work_items(_db, _job_id, **_kwargs):
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
async def test_plan_job_ignores_old_generation_work_items(monkeypatch):
    job_id = uuid.uuid4()
    job = AIJob(
        id=job_id,
        job_type="test.custom",
        status="running",
        progress_percent=5,
        celery_task_id="root-task",
        execution_generation=2,
        execution_plan={
            "execution_mode": "single",
            "chunk_count": 1,
            "chunk_registry": [],
            "work_items": [{"name": "test.custom.whole", "kind": "whole", "chunk_index": 0}],
            "execution_generation": 1,
        },
    )
    old_item = AIJobWorkItem(
        id=uuid.uuid4(),
        job_id=job_id,
        execution_generation=1,
        name="test.custom.whole",
        kind="whole",
        chunk_index=0,
        status="succeeded",
    )
    new_item = AIJobWorkItem(
        id=uuid.uuid4(),
        job_id=job_id,
        execution_generation=2,
        name="test.custom.whole",
        kind="whole",
        chunk_index=0,
        status="queued",
    )
    plan = JobPlan(
        execution_mode="single",
        chunk_count=1,
        chunk_registry=[{"chunk_index": 1, "input": {"value": 2}}],
        work_items=[
            PlannedWorkItem(
                name="test.custom.whole",
                kind="whole",
                chunk_index=0,
                input_data={"value": 2},
            )
        ],
    )
    stored_plan: dict = {}

    class CustomHandler:
        large_artifact_keys = frozenset()

        def build_execution_plan(self, received_job):
            assert received_job.execution_generation == 2
            return plan

    async def fake_get_job_or_404(_db, _job_id):
        return job

    async def fake_mark_running(*_args, **_kwargs):
        return True

    async def fake_list_work_items(_db, _job_id, **_kwargs):
        return [old_item]

    async def fake_create_work_item(*_args, **kwargs):
        assert kwargs["execution_generation"] == 2
        return new_item

    async def fake_set_execution_plan(_db, _job_id, *, execution_plan, **_kwargs):
        stored_plan.update(execution_plan)
        job.execution_plan = execution_plan

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
    assert item_ids == {"whole:0": new_item.id}
    assert stored_plan["execution_generation"] == 2


@pytest.mark.asyncio
async def test_finalize_waits_for_pending_work_items(monkeypatch):
    job_id = uuid.uuid4()
    job = AIJob(
        id=job_id,
        job_type="test.custom",
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

    async def fake_list_work_items(_db, _job_id, **_kwargs):
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
async def test_finalize_ignores_failed_work_items_from_older_generation(monkeypatch):
    job_id = uuid.uuid4()
    job = AIJob(
        id=job_id,
        job_type="test.custom",
        status="running",
        progress_percent=80,
        celery_task_id="root-task",
        execution_generation=2,
        execution_plan={"execution_mode": "single", "execution_generation": 2},
    )
    old_failed = AIJobWorkItem(
        id=uuid.uuid4(),
        job_id=job_id,
        execution_generation=1,
        name="whole",
        kind="whole",
        chunk_index=0,
        status="failed",
        error={"code": "OLD_FAILURE"},
    )
    current_whole = AIJobWorkItem(
        id=uuid.uuid4(),
        job_id=job_id,
        execution_generation=2,
        name="whole",
        kind="whole",
        chunk_index=0,
        status="succeeded",
        result={"artifacts": [], "signals": {"ok": True}},
    )
    delivered: list[str] = []

    class Handler:
        large_artifact_keys = frozenset()

        def validate_canonical_result(self, result):
            return result

        def public_result(self, result):
            return result

        async def run_success_side_effect(self, _job, _canonical_result, _db):
            return None

    async def fake_get_job_or_404(_db, _job_id):
        return job

    async def fake_list_work_items(_db, _job_id, **_kwargs):
        return [old_failed, current_whole]

    async def fake_mark_work_item_succeeded(_db, _item_id, result):
        current_whole.result = result

    async def fake_update_progress(*_args, **_kwargs):
        pass

    async def fake_mark_succeeded(_db, _job_id, *, celery_task_id, result, canonical_result, canonical_result_ref=None):
        assert celery_task_id == "root-task"
        assert canonical_result["signals"] == {"ok": True}
        job.status = "succeeded"
        job.result = result
        return True

    async def fake_deliver_callback(_job_id):
        delivered.append(str(_job_id))
        return True

    monkeypatch.setattr("app.services.job_workflow.get_job_or_404", fake_get_job_or_404)
    monkeypatch.setattr("app.services.job_workflow.JobRepo.list_work_items", fake_list_work_items)
    monkeypatch.setattr("app.services.job_workflow.JobRepo.mark_work_item_succeeded", fake_mark_work_item_succeeded)
    monkeypatch.setattr("app.services.job_workflow.JobRepo.update_progress", fake_update_progress)
    monkeypatch.setattr("app.services.job_workflow.JobRepo.mark_succeeded", fake_mark_succeeded)
    monkeypatch.setattr("app.core.workflow_registry.get", lambda _job_type: Handler())
    monkeypatch.setattr("app.services.job_workflow._persist_large_artifacts", lambda _job, result: result.model_dump())
    monkeypatch.setattr("app.tasks.jobs.deliver_callback_for_job", fake_deliver_callback)

    result = await finalize_job(FakeDB(), job_id)

    assert result == {"job_id": str(job_id), "status": "succeeded"}
    assert delivered == [str(job_id)]


@pytest.mark.asyncio
async def test_finalize_skips_when_canvas_generation_is_stale(monkeypatch):
    job_id = uuid.uuid4()
    job = AIJob(
        id=job_id,
        job_type="test.custom",
        status="running",
        progress_percent=80,
        celery_task_id="new-root-task",
        execution_generation=2,
        execution_plan={"execution_mode": "single", "execution_generation": 2},
    )

    async def fake_get_job_or_404(_db, _job_id):
        return job

    async def fail_list_work_items(*_args, **_kwargs):
        raise AssertionError("stale finalize must not inspect current generation work items")

    monkeypatch.setattr("app.services.job_workflow.get_job_or_404", fake_get_job_or_404)
    monkeypatch.setattr("app.services.job_workflow.JobRepo.list_work_items", fail_list_work_items)

    result = await finalize_job(FakeDB(), job_id, execution_generation=1)

    assert result == {
        "job_id": str(job_id),
        "status": "skipped",
        "reason": "stale_execution_generation",
        "expected_execution_generation": 1,
        "current_execution_generation": 2,
    }


@pytest.mark.asyncio
async def test_execute_work_item_allows_custom_runtime_without_model(monkeypatch):
    job_id = uuid.uuid4()
    item_id = uuid.uuid4()
    job = AIJob(
        id=job_id,
        job_type="test.custom",
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


@pytest.mark.asyncio
async def test_arithmetic_single_canvas_plan_execute_finalize_flow(monkeypatch):
    register_all_workflows()
    job_id = uuid.uuid4()
    params = {"a": 9, "b": 3}
    job = AIJob(
        id=job_id,
        job_type="arithmetic",
        status="running",
        progress_percent=5,
        celery_task_id="root-task",
        execution_generation=1,
        job_params_ref={
            "storage": "db_inline",
            "type": "json",
            "name": "job_params",
            "payload": params,
        },
        job_params_hash=payload_hash(params),
    )
    work_items: list[AIJobWorkItem] = []
    delivered: list[uuid.UUID] = []

    async def fake_get_job_or_404(_db, _job_id):
        return job

    async def fake_mark_running(*_args, **_kwargs):
        return True

    async def fake_list_work_items(_db, _job_id, **_kwargs):
        return list(work_items)

    async def fake_create_work_item(_db, *, job_id, execution_generation, name, kind, chunk_index, input_ref):
        item = AIJobWorkItem(
            id=uuid.uuid4(),
            job_id=job_id,
            execution_generation=execution_generation,
            name=name,
            kind=kind,
            chunk_index=chunk_index,
            status="queued",
            input_ref=input_ref,
        )
        work_items.append(item)
        return item

    async def fake_set_execution_plan(_db, _job_id, *, execution_plan, **_kwargs):
        job.execution_plan = execution_plan
        return True

    async def fake_update_progress(
        _db,
        _job_id,
        *,
        progress_percent,
        progress_text,
        progress_stage=None,
        **_kwargs,
    ):
        job.progress_percent = progress_percent
        job.progress_text = progress_text
        job.progress_stage = progress_stage
        return True

    async def fake_get_work_item(_db, item_id):
        return next((item for item in work_items if item.id == item_id), None)

    async def fake_claim_work_item(_db, item_id, *, celery_task_id):
        item = next(item for item in work_items if item.id == item_id)
        item.status = "running"
        item.celery_task_id = celery_task_id
        return True

    async def fake_mark_work_item_succeeded(_db, item_id, result):
        item = next(item for item in work_items if item.id == item_id)
        item.status = "succeeded"
        item.result = result

    async def fake_mark_succeeded(_db, _job_id, *, celery_task_id, result, canonical_result, canonical_result_ref=None):
        assert celery_task_id == "root-task"
        assert canonical_result == {
            "a": 9,
            "b": 3,
            "addition": 12,
            "subtraction": 6,
            "multiplication": 27,
            "division": 3.0,
        }
        job.status = "succeeded"
        job.result = result
        job.canonical_result = canonical_result
        job.canonical_result_ref = canonical_result_ref
        return True

    async def fake_deliver_callback_for_job(delivered_job_id):
        delivered.append(delivered_job_id)
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
    monkeypatch.setattr("app.services.job_workflow._persist_large_artifacts", lambda _job, result: result.model_dump())
    monkeypatch.setattr("app.tasks.jobs.deliver_callback_for_job", fake_deliver_callback_for_job)

    planned_job, plan, item_ids = await plan_job(FakeDB(), job_id)

    assert planned_job is job
    assert plan.execution_mode == "single"
    assert item_ids == {"whole:0": work_items[0].id}
    canvas = build_canvas(job.id, job.job_type, plan, item_ids, execution_generation=job.execution_generation)
    assert [task.task for task in canvas.tasks] == ["jobs.execute_work_item", "jobs.finalize_job"]
    assert canvas.tasks[0].args == (str(job_id), str(work_items[0].id))
    assert canvas.tasks[1].args == (str(job_id), 1)

    executed = await execute_work_item(
        FakeDB(),
        job_id=job_id,
        item_id=work_items[0].id,
        celery_task_id="work-task",
    )
    finalized = await finalize_job(FakeDB(), job_id, execution_generation=1)

    assert executed == {"work_item_id": str(work_items[0].id), "kind": "whole", "chunk_index": 0}
    assert finalized == {"job_id": str(job_id), "status": "succeeded"}
    assert job.result == {
        "a": 9,
        "b": 3,
        "addition": 12,
        "subtraction": 6,
        "multiplication": 27,
        "division": 3.0,
    }
    assert delivered == [job_id]


@pytest.mark.asyncio
async def test_execute_work_item_skips_stale_generation_item(monkeypatch):
    job_id = uuid.uuid4()
    item_id = uuid.uuid4()
    job = AIJob(
        id=job_id,
        job_type="test.custom",
        status="running",
        execution_generation=2,
        celery_task_id="root-task",
    )
    item = AIJobWorkItem(
        id=item_id,
        job_id=job_id,
        execution_generation=1,
        name="whole",
        kind="whole",
        chunk_index=0,
        status="queued",
    )

    async def fake_get_job_or_404(_db, _job_id):
        return job

    async def fake_get_work_item(_db, _item_id):
        return item

    async def fail_claim(*_args, **_kwargs):
        raise AssertionError("stale generation item must not be claimed")

    monkeypatch.setattr("app.services.job_workflow.get_job_or_404", fake_get_job_or_404)
    monkeypatch.setattr("app.services.job_workflow.JobRepo.get_work_item", fake_get_work_item)
    monkeypatch.setattr("app.services.job_workflow.JobRepo.claim_work_item_for_execution", fail_claim)

    result = await execute_work_item(FakeDB(), job_id=job_id, item_id=item_id, celery_task_id="task-1")

    assert result == {
        "work_item_id": str(item_id),
        "kind": "whole",
        "chunk_index": 0,
        "status": "skipped",
        "reason": "stale_execution_generation",
    }


@pytest.mark.asyncio
async def test_fail_job_ignores_stale_generation_item(monkeypatch):
    job_id = uuid.uuid4()
    item_id = uuid.uuid4()
    job = AIJob(
        id=job_id,
        job_type="test.custom",
        status="running",
        execution_generation=2,
        celery_task_id="root-task",
    )
    item = AIJobWorkItem(
        id=item_id,
        job_id=job_id,
        execution_generation=1,
        name="whole",
        kind="whole",
        chunk_index=0,
        status="running",
    )

    async def fake_get_job_or_404(_db, _job_id):
        return job

    async def fake_get_work_item(_db, _item_id):
        return item

    async def fail_mark_item(*_args, **_kwargs):
        raise AssertionError("stale generation item must not be marked failed")

    async def fail_mark_job(*_args, **_kwargs):
        raise AssertionError("stale generation item failure must not fail current job")

    monkeypatch.setattr("app.services.job_workflow.get_job_or_404", fake_get_job_or_404)
    monkeypatch.setattr("app.services.job_workflow.JobRepo.get_work_item", fake_get_work_item)
    monkeypatch.setattr("app.services.job_workflow.JobRepo.mark_work_item_failed", fail_mark_item)
    monkeypatch.setattr("app.services.job_workflow.JobRepo.mark_failed", fail_mark_job)

    await fail_job(
        FakeDB(),
        job_id=job_id,
        item_id=item_id,
        error={"code": "WORK_ITEM_FAILED", "message": "old generation", "details": {}},
    )
