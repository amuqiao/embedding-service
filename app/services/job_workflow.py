import uuid
from typing import Any

from celery import chain, chord, group
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError
from app.models.job import AIJob, AIJobWorkItem
from app.repositories.job_repo import JobRepo
from app.schemas.jobs import JobResult
from app.services.job_context import (
    append_context_to_prompt,
    build_chunk_context,
    project_memory_from_job,
)
from app.services.executor import run_ai_job
from app.services.job_planner import JobPlan, build_job_plan, job_plan_from_payload
from app.services.job_runtime import model_id_from_job, prompt_payload_from_job, work_item_payload, write_runtime_json
from app.services.jobs import _load_input_text, _persist_large_artifacts, _persist_work_item_artifacts, get_job_or_404


def _job_execution_mode(job: AIJob) -> str | None:
    return (job.execution_plan or {}).get("execution_mode")



def _first_result_value(items: list[AIJobWorkItem], kind: str, key: str) -> Any:
    for item in sorted(items, key=lambda item: item.chunk_index):
        if item.kind == kind and item.result:
            return item.result.get(key)
    return None


def _artifact_identifier(artifact: Any) -> str | None:
    if isinstance(artifact, dict):
        value = artifact.get("key") or artifact.get("label_id")
        return value if isinstance(value, str) else None
    value = getattr(artifact, "key", None)
    return value if isinstance(value, str) else None



def merge_work_items(job: AIJob, items: list[AIJobWorkItem]) -> JobResult:
    if _job_execution_mode(job) == "single":
        whole = next(item for item in items if item.kind == "whole")
        return JobResult.model_validate(whole.result)
    from app.core import workflow_registry
    handler = workflow_registry.get(job.job_type)
    # Pass all non-administrative items so the handler can use both chunks and scan results.
    payload_items = [item for item in items if item.kind not in ("whole", "merge", "memory")]
    return handler.merge_chunks(payload_items)



async def create_work_items(db: AsyncSession, job: AIJob, plan: JobPlan) -> dict[str, uuid.UUID]:
    item_ids: dict[str, uuid.UUID] = {}
    for item in plan.work_items:
        input_ref = item.input_ref
        if input_ref is None and item.input_data is not None:
            input_ref = write_runtime_json(job, f"work-items/{item.kind}-{item.chunk_index}", item.input_data)
        created = await JobRepo.create_work_item(
            db,
            job_id=job.id,
            name=item.name,
            kind=item.kind,
            chunk_index=item.chunk_index,
            input_ref=input_ref,
        )
        item_ids[f"{item.kind}:{item.chunk_index}"] = created.id
    return item_ids


async def plan_job(db: AsyncSession, job_id: uuid.UUID) -> tuple[AIJob, JobPlan, dict[str, uuid.UUID]]:
    job = await get_job_or_404(db, job_id)
    if job.celery_task_id:
        await JobRepo.mark_running(db, job_id, celery_task_id=job.celery_task_id, progress_text="正在规划执行策略")
    existing_items = await JobRepo.list_work_items(db, job_id)
    if job.execution_plan and existing_items:
        plan = job_plan_from_payload(job.execution_plan)
        item_ids = {f"{item.kind}:{item.chunk_index}": item.id for item in existing_items}
        expected_keys = {f"{item.kind}:{item.chunk_index}" for item in plan.work_items}
        missing_keys = sorted(expected_keys - set(item_ids))
        if missing_keys:
            raise RuntimeError(f"execution plan missing work items: {missing_keys}")
        await JobRepo.update_progress(
            db,
            job_id,
            progress_percent=max(job.progress_percent or 0, 10),
            progress_text=f"复用 {plan.execution_mode} 执行计划",
        )
        await db.commit()
        await db.refresh(job)
        return job, plan, item_ids
    from app.core import workflow_registry
    handler = workflow_registry.get(job.job_type)
    plan = handler.build_execution_plan(job)
    if plan is None:
        input_text = _load_input_text(job)
        plan = build_job_plan(job.job_type, input_text)
    item_ids = await create_work_items(db, job, plan)
    await JobRepo.set_execution_plan(
        db,
        job_id,
        execution_plan=plan.model_dump(),
    )
    await JobRepo.update_progress(
        db,
        job_id,
        progress_percent=10,
        progress_text=f"已生成 {plan.execution_mode} 执行计划",
    )
    await db.commit()
    await db.refresh(job)
    return job, plan, item_ids


async def execute_work_item(
    db: AsyncSession,
    *,
    job_id: uuid.UUID,
    item_id: uuid.UUID,
    celery_task_id: str | None,
) -> dict[str, Any]:
    job = await get_job_or_404(db, job_id)
    item = await JobRepo.get_work_item(db, item_id)
    if not item:
        raise RuntimeError(f"work item not found: {item_id}")
    claimed = await JobRepo.claim_work_item_for_execution(db, item_id, celery_task_id=celery_task_id)
    if not claimed:
        return {
            "work_item_id": str(item_id),
            "kind": item.kind,
            "chunk_index": item.chunk_index,
            "status": "skipped",
        }
    await JobRepo.update_progress(db, job_id, progress_percent=30, progress_text=f"正在执行 {item.kind}")
    await db.commit()

    from app.core import workflow_registry
    handler = workflow_registry.get(job.job_type)
    if item.kind in ("memory", "scan"):
        item_result = await handler.execute_special_item(item, job, db)
    else:
        custom_result = await handler.execute_standard_item(item, job, db)
        if custom_result is not None:
            item_result = custom_result
        else:
            model_id = model_id_from_job(job)
            if not model_id:
                raise AppError(
                    "JOB_RUNTIME_NOT_SUPPORTED",
                    "job_type 未配置可执行运行时",
                    status_code=500,
                    details={"job_type": job.job_type},
                )
            prompt_payload = prompt_payload_from_job(job)
            item_payload = work_item_payload(item)
            input_text = item_payload.get("text") or ""
            if item.kind == "chunk":
                memory = project_memory_from_job(job)
                if handler.canvas_pattern == "memory_fanout":
                    memory = (
                        _first_result_value(await JobRepo.list_work_items(db, job_id), "memory", "project_memory")
                        or memory
                    )
                context_text = build_chunk_context(
                    memory=memory,
                    chunk_index=item.chunk_index,
                    chunk_count=(job.execution_plan or {}).get("chunk_count") or 1,
                )
                prompt_payload = append_context_to_prompt(job, context_text)
            result = await run_ai_job(job.job_type, model_id, prompt_payload, input_text)
            item_result = result.model_dump()

    if _job_execution_mode(job) != "single":
        item_result = _persist_work_item_artifacts(job, kind=item.kind, chunk_index=item.chunk_index, result=item_result)
    await JobRepo.mark_work_item_succeeded(db, item_id, item_result)
    await db.commit()
    return {"work_item_id": str(item_id), "kind": item.kind, "chunk_index": item.chunk_index}


async def finalize_job(db: AsyncSession, job_id: uuid.UUID) -> dict[str, Any]:
    job = await get_job_or_404(db, job_id)
    items = await JobRepo.list_work_items(db, job_id)
    failed = [item for item in items if item.status == "failed"]
    if failed:
        raise AppError(
            "WORK_ITEM_FAILED",
            "内部执行分片失败",
            status_code=500,
            details={"failed_items": [str(item.id) for item in failed]},
        )
    pending = [item for item in items if item.kind != "merge" and item.status != "succeeded"]
    if pending:
        await JobRepo.update_progress(
            db,
            job_id,
            progress_percent=max(job.progress_percent or 0, 80),
            progress_text="等待分片执行完成",
        )
        await db.commit()
        return {
            "job_id": str(job_id),
            "status": "waiting",
            "pending_items": [str(item.id) for item in pending],
        }

    from app.core import workflow_registry
    handler = workflow_registry.get(job.job_type)
    merged_result = merge_work_items(job, items)
    canonical_result = handler.validate_canonical_result(_persist_large_artifacts(job, merged_result))
    public_result = handler.public_result(canonical_result)
    if _job_execution_mode(job) == "single":
        whole = next((item for item in items if item.kind == "whole"), None)
        if whole is not None:
            await JobRepo.mark_work_item_succeeded(db, whole.id, canonical_result)
    for item in items:
        if item.kind == "merge" and item.status in ("queued", "running"):
            await JobRepo.mark_work_item_succeeded(
                db,
                item.id,
                {
                    "merged": True,
                    "artifact_keys": [
                        key
                        for artifact in merged_result.artifacts
                        if (key := _artifact_identifier(artifact)) is not None
                    ],
                },
            )
    await JobRepo.update_progress(db, job_id, progress_percent=90, progress_text="正在写入最终结果")
    if not job.celery_task_id:
        raise RuntimeError(f"job has no celery_task_id: {job_id}")
    await JobRepo.mark_succeeded(
        db,
        job_id,
        celery_task_id=job.celery_task_id,
        result=public_result,
        canonical_result=canonical_result,
    )
    await db.commit()
    await db.refresh(job)
    from app.tasks.jobs import deliver_callback_for_job
    await deliver_callback_for_job(job_id)
    await handler.after_success_callback(job, canonical_result, db)
    return {"job_id": str(job_id), "status": "succeeded"}


async def fail_job(
    db: AsyncSession,
    *,
    job_id: uuid.UUID,
    item_id: uuid.UUID | None,
    error: dict[str, Any],
) -> None:
    if item_id:
        await JobRepo.mark_work_item_failed(db, item_id, error)
    job = await get_job_or_404(db, job_id)
    await JobRepo.mark_failed(db, job_id, error, celery_task_id=job.celery_task_id)
    await db.commit()
    from app.tasks.jobs import deliver_callback_for_job
    await deliver_callback_for_job(job_id)


def build_canvas(job_id: uuid.UUID, job_type: str, plan: JobPlan, item_ids: dict[str, uuid.UUID]):
    from app.tasks.jobs import execute_work_item_task, fanout_after_mapping_task, finalize_job_task
    from app.core import workflow_registry
    handler = workflow_registry.get(job_type)
    pattern = handler.canvas_pattern

    job_id_text = str(job_id)
    if plan.execution_mode == "single":
        whole_id = str(item_ids["whole:0"])
        return chain(
            execute_work_item_task.s(job_id_text, whole_id),
            finalize_job_task.s(job_id_text),
        )

    chunk_signatures = [
        execute_work_item_task.s(job_id_text, str(item_ids[f"chunk:{item.chunk_index}"]))
        for item in plan.work_items
        if item.kind == "chunk"
    ]

    if pattern == "memory_fanout":
        chunk_item_ids = [
            str(item_ids[f"chunk:{item.chunk_index}"])
            for item in plan.work_items
            if item.kind == "chunk"
        ]
        return chain(
            execute_work_item_task.s(job_id_text, str(item_ids["memory:0"])),
            fanout_after_mapping_task.s(job_id_text, chunk_item_ids),
        )

    if pattern == "scan_chord":
        scan_id = str(item_ids[f"scan:{plan.chunk_count + 2}"])
        return chain(
            chord(group(chunk_signatures), execute_work_item_task.si(job_id_text, scan_id)),
            finalize_job_task.s(job_id_text),
        )

    return chord(group(chunk_signatures), finalize_job_task.s(job_id_text))
