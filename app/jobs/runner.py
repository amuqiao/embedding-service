from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import AppError
from app.jobs.factory import get_job_executor
from app.models.job import Job
from app.repositories.job_repo import JobRepo
from app.services.executor import run_ai_job
from app.services.job_lifecycle import SUCCESS_SIDE_EFFECT_DONE_STAGE, SUCCESS_SIDE_EFFECT_STAGE
from app.services.job_runtime import (
    ai_billing_scope_id_from_job,
    model_id_from_job,
    prompt_payload_from_job,
    workflow_plan_from_job,
)
from app.services.jobs import _load_input_text, _persist_large_artifacts, get_job_or_404, trigger_request_id_from_job
from app.workflows.orchestrator import advance_workflow_after_child_terminal, create_ready_child_jobs

logger = logging.getLogger(__name__)


def _execution_generation(job: Job) -> int:
    return int(getattr(job, "execution_generation", None) or 1)


async def _update_current_progress(
    db: AsyncSession,
    job: Job,
    *,
    progress_percent: int,
    progress_text: str,
    progress_stage: str | None = None,
) -> bool:
    updated = await JobRepo.update_progress(
        db,
        job.id,
        progress_percent=progress_percent,
        progress_text=progress_text,
        progress_stage=progress_stage,
        execution_token=job.execution_token,
        execution_generation=_execution_generation(job),
    )
    return updated is not False


def _job_error_from_exception(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, AppError):
        return {"code": exc.code, "message": exc.message, "details": exc.details}
    return {
        "code": "WORKFLOW_AFTER_SUCCESS_FAILED",
        "message": str(exc)[:500],
        "details": {"type": type(exc).__name__},
    }


async def execute_job(
    db: AsyncSession,
    job_id: uuid.UUID,
    *,
    execution_generation: int | None = None,
    attempt_id: uuid.UUID | None = None,
    lease_token: uuid.UUID | None = None,
) -> dict[str, Any]:
    job = await get_job_or_404(db, job_id)
    if execution_generation is not None and _execution_generation(job) != execution_generation:
        return {
            "job_id": str(job_id),
            "status": "skipped",
            "reason": "stale_execution_generation",
            "expected_execution_generation": execution_generation,
            "current_execution_generation": _execution_generation(job),
        }
    if job.status in ("succeeded", "failed"):
        return {"job_id": str(job_id), "status": "skipped", "job_status": job.status}
    if job.status != "running":
        return {"job_id": str(job_id), "status": "skipped", "job_status": job.status}
    if not job.execution_token:
        raise RuntimeError(f"job has no execution_token: {job_id}")

    workflow_plan = workflow_plan_from_job(job)
    if workflow_plan is not None:
        if attempt_id is None or lease_token is None:
            raise AppError(
                "JOB_RUNTIME_NOT_SUPPORTED",
                "workflow root orchestration requires an active attempt lease",
                details={"job_id": str(job.id), "job_type": job.job_type},
            )
        claimed_orchestration = await _update_current_progress(
            db,
            job,
            progress_percent=max(job.progress_percent or 0, 15),
            progress_text="正在编排子任务",
            progress_stage="planning",
        )
        await db.commit()
        if not claimed_orchestration:
            return {"job_id": str(job_id), "status": "skipped", "reason": "stale_execution_generation"}
        extended = await JobRepo.heartbeat_attempt(
            db,
            attempt_id,
            lease_token=lease_token,
            lease_seconds=settings.job_stale_running_seconds,
        )
        if not extended:
            raise AppError(
                "JOB_STATE_TRANSITION_CONFLICT",
                "workflow root orchestration attempt lease could not be extended",
                details={"job_id": str(job_id), "attempt_id": str(attempt_id)},
            )
        orchestration = await create_ready_child_jobs(db, root_job=job, workflow_plan=workflow_plan)
        marked_attempt = await JobRepo.mark_workflow_orchestration_attempt_succeeded(
            db,
            attempt_id,
            lease_token=lease_token,
        )
        if not marked_attempt:
            raise AppError(
                "JOB_STATE_TRANSITION_CONFLICT",
                "workflow root orchestration attempt could not be marked succeeded",
                details={"job_id": str(job_id), "attempt_id": str(attempt_id)},
            )
        await db.commit()
        from app.tasks.jobs import TaskiqPublishDeferredError, publish_job_attempt

        for child_attempt_id in orchestration.created_attempt_ids:
            try:
                await publish_job_attempt(child_attempt_id)
            except TaskiqPublishDeferredError:
                logger.exception(
                    "workflow_child_attempt_publish_deferred root_job_id=%s child_attempt_id=%s",
                    job.id,
                    child_attempt_id,
                )
        return {
            "job_id": str(job_id),
            "status": "succeeded",
            "workflow_status": "orchestrated",
            "created_child_jobs": len(orchestration.created_child_job_ids),
        }

    try:
        executor = get_job_executor(job.job_type)
    except KeyError as exc:
        raise AppError(
            "INVALID_JOB_TYPE",
            "stored job references an unregistered job_type",
            details={"job_id": str(job.id), "job_type": job.job_type},
        ) from exc
    claimed_execute = await _update_current_progress(
        db,
        job,
        progress_percent=max(job.progress_percent or 0, 30),
        progress_text="正在执行 Job",
        progress_stage="calling_model",
    )
    await db.commit()
    if not claimed_execute:
        return {"job_id": str(job_id), "status": "skipped", "reason": "stale_execution_generation"}

    custom_result = await executor.execute(job, db)
    if custom_result is None:
        model_id = model_id_from_job(job)
        if not model_id:
            raise AppError(
                "JOB_RUNTIME_NOT_SUPPORTED",
                "job_type 未配置可执行运行时",
                details={"job_type": job.job_type},
            )
        model_attempt_id = attempt_id or job.active_attempt_id
        if model_attempt_id is None:
            raise AppError(
                "JOB_RUNTIME_NOT_SUPPORTED",
                "job scope model execution requires an active attempt",
                details={"job_id": str(job.id), "job_type": job.job_type},
            )
        result = await run_ai_job(
            job_type=job.job_type,
            model_id=model_id,
            prompt_payload=prompt_payload_from_job(job),
            input_text=_load_input_text(job),
            caller_id=job.caller_id,
            job_id=job.id,
            ai_scope_id=ai_billing_scope_id_from_job(job),
            attempt_id=model_attempt_id,
            request_id=trigger_request_id_from_job(job),
        )
        result_data = result.model_dump()
    else:
        result_data = custom_result

    claimed_result = await _update_current_progress(
        db,
        job,
        progress_percent=max(job.progress_percent or 0, 85),
        progress_text="正在整理执行结果",
        progress_stage="writing_result",
    )
    await db.commit()
    if not claimed_result:
        return {"job_id": str(job_id), "status": "skipped", "reason": "stale_execution_generation"}

    canonical_result = executor.validate_canonical_result(_persist_large_artifacts(job, result_data))
    public_result = executor.public_result(canonical_result)
    if job.progress_stage != SUCCESS_SIDE_EFFECT_DONE_STAGE:
        claimed_side_effect = await _update_current_progress(
            db,
            job,
            progress_percent=90,
            progress_text="正在执行成功前副作用",
            progress_stage=SUCCESS_SIDE_EFFECT_STAGE,
        )
        await db.commit()
        if not claimed_side_effect:
            return {"job_id": str(job_id), "status": "skipped", "reason": "stale_execution_generation"}
        try:
            await executor.run_success_side_effect(job, canonical_result, db)
        except Exception as exc:
            await JobRepo.mark_failed(
                db,
                job_id,
                _job_error_from_exception(exc),
                execution_token=job.execution_token,
            )
            await db.commit()
            from app.tasks.jobs import deliver_callback_for_job

            await deliver_callback_for_job(job_id)
            return {"job_id": str(job_id), "status": "failed"}
        marked_side_effect_done = await _update_current_progress(
            db,
            job,
            progress_percent=95,
            progress_text="成功前副作用已完成",
            progress_stage=SUCCESS_SIDE_EFFECT_DONE_STAGE,
        )
        await db.commit()
        if not marked_side_effect_done:
            return {"job_id": str(job_id), "status": "skipped", "reason": "stale_execution_generation"}
    succeeded = await JobRepo.mark_succeeded(
        db,
        job_id,
        execution_token=job.execution_token,
        result=public_result,
        canonical_result=canonical_result,
    )
    if not succeeded:
        raise AppError(
            "JOB_STATE_TRANSITION_CONFLICT",
            "job could not be marked succeeded after success side effect",
            details={"job_id": str(job_id), "execution_token": job.execution_token},
        )
    if attempt_id is not None and lease_token is not None:
        attempt_succeeded = await JobRepo.mark_attempt_succeeded(db, attempt_id, lease_token=lease_token)
        if not attempt_succeeded:
            raise AppError(
                "JOB_STATE_TRANSITION_CONFLICT",
                "attempt could not be marked succeeded with job result",
                details={"job_id": str(job_id), "attempt_id": str(attempt_id)},
            )
    workflow_advance = None
    if job.is_internal:
        workflow_advance = await advance_workflow_after_child_terminal(db, child_job=job)
    await db.commit()
    await db.refresh(job)
    from app.tasks.jobs import deliver_callback_for_job, handle_workflow_advance_result

    if workflow_advance is not None:
        await handle_workflow_advance_result(workflow_advance)
    else:
        await deliver_callback_for_job(job_id)
    return {"job_id": str(job_id), "status": "succeeded"}


async def fail_job(
    db: AsyncSession,
    *,
    job_id: uuid.UUID,
    error: dict[str, Any],
) -> None:
    job = await get_job_or_404(db, job_id)
    if job.active_attempt_id is not None:
        raise AppError(
            "JOB_STATE_TRANSITION_CONFLICT",
            "fail_job cannot bypass an active attempt",
            details={"job_id": str(job.id), "active_attempt_id": str(job.active_attempt_id)},
        )
    await JobRepo.mark_failed(db, job_id, error, execution_token=job.execution_token)
    await db.commit()
    from app.tasks.jobs import deliver_callback_for_job

    await deliver_callback_for_job(job_id)
