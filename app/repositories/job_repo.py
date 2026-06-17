import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.job import AIJob, AIJobWorkItem


class JobRepo:
    @staticmethod
    async def advisory_lock_for_client_request(db: AsyncSession, caller_id: str, client_request_id: str) -> None:
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
            {"lock_key": f"{caller_id}:{client_request_id}"},
        )

    @staticmethod
    async def get_recent_by_client_request(
        db: AsyncSession,
        *,
        caller_id: str,
        client_request_id: str,
    ) -> AIJob | None:
        since = datetime.now(timezone.utc) - timedelta(hours=24)
        query_result = await db.execute(
            select(AIJob)
            .where(
                AIJob.caller_id == caller_id,
                AIJob.client_request_id == client_request_id,
                AIJob.created_at >= since,
                AIJob.deleted_at.is_(None),
            )
            .order_by(AIJob.created_at.asc())
            .limit(1)
        )
        return query_result.scalar_one_or_none()

    @staticmethod
    async def create(
        db: AsyncSession,
        *,
        caller_id: str,
        client_request_id: str | None,
        job_type: str,
        request_fingerprint: str | None = None,
        metadata: dict[str, Any] | None = None,
        priority: str = "normal",
        timeout_seconds: int | None = None,
        job_params_ref: dict[str, Any] | None = None,
        job_params_hash: str | None = None,
        callback_url: str | None = None,
        callback_events: list[str] | None = None,
    ) -> AIJob:
        job = AIJob(
            caller_id=caller_id,
            client_request_id=client_request_id,
            request_fingerprint=request_fingerprint,
            job_type=job_type,
            status="queued",
            progress_percent=0,
            progress_text="已排队",
            queued_at=datetime.now(timezone.utc),
            priority=priority,
            timeout_seconds=timeout_seconds,
            metadata_=metadata or {},
            job_params_ref=job_params_ref,
            job_params_hash=job_params_hash,
            callback_url=callback_url,
            callback_events=callback_events,
        )
        db.add(job)
        await db.flush()
        await db.refresh(job)
        return job

    @staticmethod
    async def get(db: AsyncSession, job_id: uuid.UUID) -> AIJob | None:
        result = await db.execute(select(AIJob).where(AIJob.id == job_id, AIJob.deleted_at.is_(None)))
        return result.scalar_one_or_none()

    @staticmethod
    async def get_for_caller(db: AsyncSession, job_id: uuid.UUID, caller_id: str) -> AIJob | None:
        result = await db.execute(
            select(AIJob).where(AIJob.id == job_id, AIJob.caller_id == caller_id, AIJob.deleted_at.is_(None))
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def set_celery_task_id(db: AsyncSession, job_id: uuid.UUID, celery_task_id: str) -> None:
        job = await JobRepo.get(db, job_id)
        if job:
            job.celery_task_id = celery_task_id
            job.celery_published_at = None
            job.updated_at = datetime.now(timezone.utc)
            await db.flush()

    @staticmethod
    async def mark_celery_published(db: AsyncSession, job_id: uuid.UUID, celery_task_id: str) -> bool:
        result = await db.execute(
            text(
                "UPDATE ai_jobs "
                "SET celery_published_at = now(), "
                "first_published_at = COALESCE(first_published_at, now()), "
                "last_published_at = now(), "
                "dispatch_attempts = dispatch_attempts + 1, "
                "updated_at = now() "
                "WHERE id = :job_id AND celery_task_id = :task_id AND deleted_at IS NULL"
            ),
            {"job_id": str(job_id), "task_id": celery_task_id},
        )
        await db.flush()
        return result.rowcount == 1

    @staticmethod
    async def set_execution_plan(
        db: AsyncSession,
        job_id: uuid.UUID,
        *,
        execution_plan: dict[str, Any],
    ) -> None:
        job = await JobRepo.get(db, job_id)
        if job:
            job.execution_plan = execution_plan
            job.updated_at = datetime.now(timezone.utc)
            await db.flush()

    @staticmethod
    async def mark_running_if_queued(
        db: AsyncSession,
        job_id: uuid.UUID,
        *,
        celery_task_id: str,
        progress_text: str = "正在处理文本",
    ) -> bool:
        """CAS 原子转移：仅在 status='queued' 时标记 running，防止多 Worker 并发双执行。"""
        result = await db.execute(
            text(
                "UPDATE ai_jobs "
                "SET status='running', "
                "progress_percent=GREATEST(COALESCE(progress_percent, 0), 5), "
                "progress_text=:progress_text, "
                "started_at=now(), "
                "last_execution_at=now(), "
                "last_heartbeat_at=now(), "
                "execution_attempts=execution_attempts + 1, "
                "updated_at=now() "
                "WHERE id=:job_id AND status='queued' AND celery_task_id=:task_id AND deleted_at IS NULL"
            ),
            {"job_id": str(job_id), "task_id": celery_task_id, "progress_text": progress_text},
        )
        await db.flush()
        return result.rowcount == 1

    @staticmethod
    async def mark_running(
        db: AsyncSession,
        job_id: uuid.UUID,
        *,
        celery_task_id: str,
        progress_text: str = "正在处理文本",
    ) -> bool:
        query_result = await db.execute(
            select(AIJob)
            .where(
                AIJob.id == job_id,
                AIJob.status == "running",
                AIJob.celery_task_id == celery_task_id,
                AIJob.deleted_at.is_(None),
            )
            .with_for_update(skip_locked=True)
        )
        job = query_result.scalar_one_or_none()
        if not job:
            return False
        job.progress_percent = max(job.progress_percent or 0, 5)
        job.progress_text = progress_text
        job.started_at = datetime.now(timezone.utc)
        job.last_execution_at = job.started_at
        job.last_heartbeat_at = job.started_at
        job.updated_at = datetime.now(timezone.utc)
        await db.flush()
        return True

    @staticmethod
    async def update_progress(
        db: AsyncSession,
        job_id: uuid.UUID,
        *,
        progress_percent: int,
        progress_text: str,
        progress_stage: str | None = None,
    ) -> None:
        job = await JobRepo.get(db, job_id)
        if job:
            now = datetime.now(timezone.utc)
            job.progress_percent = max(0, min(100, progress_percent))
            job.progress_text = progress_text
            if progress_stage is not None:
                job.progress_stage = progress_stage
            job.last_heartbeat_at = now
            job.updated_at = now
            await db.flush()

    @staticmethod
    async def mark_succeeded(
        db: AsyncSession,
        job_id: uuid.UUID,
        *,
        celery_task_id: str,
        result: dict[str, Any] | None,
        canonical_result: dict[str, Any] | None = None,
        canonical_result_ref: dict[str, Any] | None = None,
    ) -> bool:
        query_result = await db.execute(
            select(AIJob)
            .where(
                AIJob.id == job_id,
                AIJob.status == "running",
                AIJob.celery_task_id == celery_task_id,
                AIJob.deleted_at.is_(None),
            )
            .with_for_update(skip_locked=True)
        )
        job = query_result.scalar_one_or_none()
        if not job:
            return False
        now = datetime.now(timezone.utc)
        job.status = "succeeded"
        job.progress_percent = 100
        job.progress_text = "已完成"
        job.progress_stage = "succeeded"
        job.result = result
        job.canonical_result = canonical_result
        job.canonical_result_ref = canonical_result_ref
        job.error = None
        job.finished_at = now
        job.last_heartbeat_at = now
        job.updated_at = now
        job.callback_status = "pending"
        job.callback_attempts = 0
        job.callback_next_retry_at = None
        job.callback_last_error = None
        await db.flush()
        return True

    @staticmethod
    async def mark_failed(
        db: AsyncSession,
        job_id: uuid.UUID,
        error: dict[str, Any],
        *,
        celery_task_id: str | None = None,
    ) -> bool:
        conditions = [AIJob.id == job_id, AIJob.status.in_(["queued", "running"]), AIJob.deleted_at.is_(None)]
        if celery_task_id:
            conditions.append(AIJob.celery_task_id == celery_task_id)
        result = await db.execute(
            select(AIJob)
            .where(*conditions)
            .with_for_update(skip_locked=True)
        )
        job = result.scalar_one_or_none()
        if not job:
            return False
        now = datetime.now(timezone.utc)
        job.status = "failed"
        job.progress_text = "处理失败"
        job.progress_stage = "failed"
        job.result = None
        job.error = error
        job.finished_at = now
        job.last_heartbeat_at = now
        job.updated_at = now
        job.callback_status = "pending"
        job.callback_attempts = 0
        job.callback_next_retry_at = None
        job.callback_last_error = None
        await db.flush()
        return True

    @staticmethod
    async def mark_failed_if_running(db: AsyncSession, job_id: uuid.UUID, error: dict[str, Any]) -> bool:
        """CAS 原子转移：仅在 status='running' 时标记 failed，防止并发 recovery 重复处理。"""
        result = await db.execute(
            select(AIJob)
            .where(AIJob.id == job_id, AIJob.status == "running", AIJob.deleted_at.is_(None))
            .with_for_update(skip_locked=True)
        )
        job = result.scalar_one_or_none()
        if not job:
            return False
        now = datetime.now(timezone.utc)
        job.status = "failed"
        job.progress_text = "处理失败"
        job.progress_stage = "failed"
        job.result = None
        job.error = error
        job.finished_at = now
        job.last_heartbeat_at = now
        job.callback_status = "pending"
        job.callback_attempts = 0
        job.callback_next_retry_at = None
        job.callback_last_error = None
        job.updated_at = now
        await db.flush()
        return True

    @staticmethod
    async def mark_success_side_effect_recovery_dispatched(
        db: AsyncSession,
        job_id: uuid.UUID,
        *,
        progress_stage: str,
    ) -> bool:
        result = await db.execute(
            select(AIJob)
            .where(
                AIJob.id == job_id,
                AIJob.status == "running",
                AIJob.progress_stage == progress_stage,
                AIJob.deleted_at.is_(None),
            )
            .with_for_update(skip_locked=True)
        )
        job = result.scalar_one_or_none()
        if not job:
            return False
        now = datetime.now(timezone.utc)
        job.progress_percent = max(job.progress_percent or 0, 90)
        job.progress_text = "正在恢复成功前副作用"
        job.last_heartbeat_at = now
        job.updated_at = now
        await db.flush()
        return True

    @staticmethod
    async def create_work_item(
        db: AsyncSession,
        *,
        job_id: uuid.UUID,
        name: str,
        kind: str,
        chunk_index: int = 0,
        input_ref: dict[str, Any] | None = None,
    ) -> AIJobWorkItem:
        item = AIJobWorkItem(
            job_id=job_id,
            name=name,
            kind=kind,
            chunk_index=chunk_index,
            status="queued",
            input_ref=input_ref,
        )
        db.add(item)
        await db.flush()
        await db.refresh(item)
        return item

    @staticmethod
    async def list_work_items(db: AsyncSession, job_id: uuid.UUID) -> list[AIJobWorkItem]:
        result = await db.execute(
            select(AIJobWorkItem)
            .where(AIJobWorkItem.job_id == job_id, AIJobWorkItem.deleted_at.is_(None))
            .order_by(AIJobWorkItem.chunk_index.asc(), AIJobWorkItem.created_at.asc())
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_work_item(db: AsyncSession, item_id: uuid.UUID) -> AIJobWorkItem | None:
        result = await db.execute(
            select(AIJobWorkItem).where(AIJobWorkItem.id == item_id, AIJobWorkItem.deleted_at.is_(None))
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def claim_work_item_for_execution(
        db: AsyncSession,
        item_id: uuid.UUID,
        *,
        celery_task_id: str | None,
    ) -> bool:
        result = await db.execute(
            select(AIJobWorkItem)
            .where(AIJobWorkItem.id == item_id, AIJobWorkItem.deleted_at.is_(None))
            .with_for_update(skip_locked=True)
        )
        item = result.scalar_one_or_none()
        if not item:
            return False
        if item.status == "running" and item.celery_task_id == celery_task_id:
            item.updated_at = datetime.now(timezone.utc)
            await db.flush()
            return True
        if item.status != "queued":
            return False
        now = datetime.now(timezone.utc)
        item.status = "running"
        item.started_at = now
        item.updated_at = now
        if celery_task_id:
            item.celery_task_id = celery_task_id
        await db.flush()
        return True

    @staticmethod
    async def mark_work_item_running(
        db: AsyncSession,
        item_id: uuid.UUID,
        *,
        celery_task_id: str | None = None,
    ) -> None:
        item = await JobRepo.get_work_item(db, item_id)
        if item:
            now = datetime.now(timezone.utc)
            item.status = "running"
            item.started_at = now
            item.updated_at = now
            if celery_task_id:
                item.celery_task_id = celery_task_id
            await db.flush()

    @staticmethod
    async def mark_work_item_succeeded(
        db: AsyncSession,
        item_id: uuid.UUID,
        result: dict[str, Any] | None,
    ) -> None:
        item = await JobRepo.get_work_item(db, item_id)
        if item:
            now = datetime.now(timezone.utc)
            item.status = "succeeded"
            item.result = result
            item.error = None
            item.finished_at = now
            item.updated_at = now
            await db.flush()

    @staticmethod
    async def mark_work_item_failed(
        db: AsyncSession,
        item_id: uuid.UUID,
        error: dict[str, Any],
    ) -> None:
        item = await JobRepo.get_work_item(db, item_id)
        if item:
            now = datetime.now(timezone.utc)
            item.status = "failed"
            item.error = error
            item.finished_at = now
            item.updated_at = now
            await db.flush()

    @staticmethod
    async def find_unpublished_queued_jobs(
        db: AsyncSession,
        created_before: datetime,
        *,
        limit: int,
    ) -> list[AIJob]:
        """查找已分配 task_id 但未确认 publish 的 queued Job。"""
        result = await db.execute(
            select(AIJob).where(
                AIJob.status == "queued",
                AIJob.celery_task_id.is_not(None),
                AIJob.celery_published_at.is_(None),
                AIJob.created_at < created_before,
                AIJob.deleted_at.is_(None),
            )
            .order_by(AIJob.created_at.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    @staticmethod
    async def claim_unpublished_for_dispatch(
        db: AsyncSession, job_id: uuid.UUID, old_task_id: str, new_task_id: str
    ) -> bool:
        """CAS 替换未确认发布的 task_id，防止并发补偿投递。"""
        result = await db.execute(
            text(
                "UPDATE ai_jobs SET celery_task_id = :new_task_id, updated_at = now() "
                "WHERE id = :job_id AND status = 'queued' "
                "AND celery_task_id = :old_task_id AND celery_published_at IS NULL "
                "AND deleted_at IS NULL"
            ),
            {"new_task_id": new_task_id, "job_id": str(job_id), "old_task_id": old_task_id},
        )
        await db.flush()
        return result.rowcount == 1

    @staticmethod
    async def mark_callback_delivering(
        db: AsyncSession,
        job_id: uuid.UUID,
        *,
        now: datetime,
        max_attempts: int,
        next_retry_at: datetime,
    ) -> bool:
        result = await db.execute(
            text(
                "UPDATE ai_jobs "
                "SET callback_status='delivering', "
                "callback_next_retry_at=:next_retry_at, "
                "callback_first_attempt_at=COALESCE(callback_first_attempt_at, :now), "
                "callback_last_attempt_at=:now, "
                "updated_at=now() "
                "WHERE id=:job_id AND status IN ('succeeded', 'failed') "
                "AND callback_status IN ('pending', 'failed', 'delivering') "
                "AND callback_attempts < :max_attempts "
                "AND (callback_next_retry_at IS NULL OR callback_next_retry_at <= :now) "
                "AND deleted_at IS NULL"
            ),
            {
                "job_id": str(job_id),
                "now": now,
                "max_attempts": max_attempts,
                "next_retry_at": next_retry_at,
            },
        )
        await db.flush()
        return result.rowcount == 1

    @staticmethod
    async def mark_callback_result(
        db: AsyncSession,
        job_id: uuid.UUID,
        *,
        status: str,
        attempts_increment: int,
        last_error: dict[str, Any] | None,
        next_retry_at: datetime | None,
    ) -> None:
        job = await JobRepo.get(db, job_id)
        if job:
            now = datetime.now(timezone.utc)
            job.callback_status = status
            job.callback_attempts = (job.callback_attempts or 0) + attempts_increment
            job.callback_last_error = last_error
            job.callback_next_retry_at = next_retry_at
            job.callback_first_attempt_at = job.callback_first_attempt_at or now
            job.callback_last_attempt_at = now
            if status == "delivered":
                job.callback_delivered_at = now
            elif status == "failed":
                job.callback_failed_at = now
            job.updated_at = now
            await db.flush()

    @staticmethod
    async def find_due_callbacks(
        db: AsyncSession,
        *,
        now: datetime,
        max_attempts: int,
        limit: int,
    ) -> list[AIJob]:
        result = await db.execute(
            select(AIJob)
            .where(
                AIJob.status.in_(["succeeded", "failed"]),
                AIJob.callback_status.in_(["pending", "failed", "delivering"]),
                AIJob.callback_attempts < max_attempts,
                or_(AIJob.callback_next_retry_at.is_(None), AIJob.callback_next_retry_at <= now),
                AIJob.deleted_at.is_(None),
            )
            .order_by(AIJob.finished_at.asc(), AIJob.created_at.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    @staticmethod
    async def find_orphaned_queued_jobs(
        db: AsyncSession,
        created_before: datetime,
        *,
        limit: int,
    ) -> list[AIJob]:
        result = await db.execute(
            select(AIJob).where(
                AIJob.status == "queued",
                AIJob.celery_task_id.is_(None),
                AIJob.created_at < created_before,
                AIJob.deleted_at.is_(None),
            )
            .order_by(AIJob.created_at.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    @staticmethod
    async def find_stale_running_jobs(
        db: AsyncSession,
        started_before: datetime,
        *,
        limit: int,
    ) -> list[AIJob]:
        result = await db.execute(
            select(AIJob).where(
                AIJob.status == "running",
                AIJob.started_at.is_not(None),
                or_(AIJob.last_heartbeat_at.is_(None), AIJob.last_heartbeat_at < started_before),
                AIJob.deleted_at.is_(None),
            )
            .order_by(AIJob.started_at.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    @staticmethod
    async def claim_orphan_for_dispatch(db: AsyncSession, job_id: uuid.UUID, celery_task_id: str) -> bool:
        """原子性抢占孤儿 Job 的投递权。仅在 celery_task_id 为 NULL 时成功写入，返回 True。
        多 Worker 并发时只有一个能成功，防止同一 Job 被重复 dispatch。
        """
        result = await db.execute(
            text(
                "UPDATE ai_jobs SET celery_task_id = :task_id, updated_at = now() "
                "WHERE id = :job_id AND status = 'queued' AND celery_task_id IS NULL "
                "AND deleted_at IS NULL"
            ),
            {"task_id": celery_task_id, "job_id": str(job_id)},
        )
        await db.flush()
        return result.rowcount == 1

    @staticmethod
    async def count_active_jobs(db: AsyncSession) -> int:
        result = await db.execute(
            select(func.count())
            .select_from(AIJob)
            .where(AIJob.status.in_(["queued", "running"]), AIJob.deleted_at.is_(None))
        )
        return result.scalar_one()

    @staticmethod
    async def cleanup_expired_jobs(db: AsyncSession) -> int:
        """软删除过期且生命周期已收敛的 Job 记录（expires_at <= now）。"""
        expired_settled_jobs = select(AIJob.id).where(
            AIJob.expires_at <= func.now(),
            AIJob.deleted_at.is_(None),
            AIJob.status.in_(["succeeded", "failed"]),
            AIJob.callback_status.in_(["delivered", "skipped"]),
        )
        result = await db.execute(
            update(AIJob)
            .where(AIJob.id.in_(expired_settled_jobs))
            .values(
                delete_requested_at=func.now(),
                deleted_at=func.now(),
                deleted_reason="expired",
                updated_at=func.now(),
            )
        )
        await db.execute(
            update(AIJobWorkItem)
            .where(
                AIJobWorkItem.job_id.in_(
                    select(AIJob.id).where(
                        AIJob.expires_at <= func.now(),
                        AIJob.deleted_at.is_not(None),
                        AIJob.status.in_(["succeeded", "failed"]),
                    )
                ),
                AIJobWorkItem.deleted_at.is_(None),
            )
            .values(deleted_at=func.now(), deleted_reason="parent_expired", updated_at=func.now())
        )
        await db.flush()
        return result.rowcount

    @staticmethod
    async def list_jobs_before(
        db: AsyncSession,
        expires_before: datetime,
    ) -> list[AIJob]:
        """查询在指定时间前过期的 Job（用于清理前的日志或备份）"""
        result = await db.execute(
            select(AIJob)
            .where(AIJob.expires_at <= expires_before, AIJob.deleted_at.is_(None))
            .order_by(AIJob.created_at.asc())
        )
        return list(result.scalars().all())

    @staticmethod
    async def mark_callback_delivered(
        db: AsyncSession,
        job_id: uuid.UUID,
    ) -> None:
        """标记 Callback 已成功发送（可选，用于高级重试控制）"""
        job = await JobRepo.get(db, job_id)
        if job:
            now = datetime.now(timezone.utc)
            job.callback_delivered_at = now
            job.updated_at = now
            await db.flush()
