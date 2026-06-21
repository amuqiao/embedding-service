import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.job import Job, JobAttempt, JobEvent


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
    ) -> Job | None:
        since = datetime.now(timezone.utc) - timedelta(hours=24)
        query_result = await db.execute(
            select(Job)
            .where(
                Job.caller_id == caller_id,
                Job.client_request_id == client_request_id,
                Job.created_at >= since,
                Job.deleted_at.is_(None),
            )
            .order_by(Job.created_at.asc())
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
        job_params: dict[str, Any] | None = None,
        max_attempts: int = 1,
        job_params_ref: dict[str, Any] | None = None,
        job_params_hash: str | None = None,
        callback_url: str | None = None,
        callback_events: list[str] | None = None,
    ) -> Job:
        job = Job(
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
            job_params=job_params or {},
            max_attempts=max_attempts,
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
    async def create_initial_attempt(db: AsyncSession, job: Job, *, timeout_seconds: int) -> JobAttempt:
        attempt_id = uuid.uuid4()
        attempt = JobAttempt(
            id=attempt_id,
            job_id=job.id,
            attempt_no=1,
            status="queued",
            timeout_seconds=timeout_seconds,
            next_dispatch_at=datetime.now(timezone.utc),
        )
        db.add(attempt)
        job.active_attempt_id = attempt_id
        job.attempt_count = 1
        job.timeout_seconds = timeout_seconds
        job.updated_at = datetime.now(timezone.utc)
        db.add(
            JobEvent(
                job_id=job.id,
                attempt_id=attempt_id,
                event_type="attempt.created",
                to_status="queued",
            )
        )
        await db.flush()
        return attempt

    @staticmethod
    async def get_attempt(db: AsyncSession, attempt_id: uuid.UUID) -> JobAttempt | None:
        result = await db.execute(select(JobAttempt).where(JobAttempt.id == attempt_id))
        return result.scalar_one_or_none()

    @staticmethod
    async def mark_attempt_published(
        db: AsyncSession,
        attempt_id: uuid.UUID,
        *,
        next_dispatch_at: datetime,
    ) -> bool:
        now = datetime.now(timezone.utc)
        result = await db.execute(select(JobAttempt).where(JobAttempt.id == attempt_id).with_for_update(skip_locked=True))
        attempt = result.scalar_one_or_none()
        if not attempt or attempt.status not in {"queued", "published"}:
            return False
        previous = attempt.status
        attempt.status = "published"
        attempt.published_at = now
        attempt.dispatch_attempts = (attempt.dispatch_attempts or 0) + 1
        attempt.next_dispatch_at = next_dispatch_at
        attempt.last_dispatch_error = None
        attempt.updated_at = now
        db.add(
            JobEvent(
                job_id=attempt.job_id,
                attempt_id=attempt.id,
                event_type="attempt.published",
                from_status=previous,
                to_status="published",
            )
        )
        await db.flush()
        return True

    @staticmethod
    async def mark_attempt_publish_failed(
        db: AsyncSession,
        attempt_id: uuid.UUID,
        *,
        error: dict[str, Any],
        next_dispatch_at: datetime,
    ) -> bool:
        now = datetime.now(timezone.utc)
        result = await db.execute(select(JobAttempt).where(JobAttempt.id == attempt_id).with_for_update(skip_locked=True))
        attempt = result.scalar_one_or_none()
        if not attempt or attempt.status not in {"queued", "published"}:
            return False
        attempt.dispatch_attempts = (attempt.dispatch_attempts or 0) + 1
        attempt.last_dispatch_error = error
        attempt.next_dispatch_at = next_dispatch_at
        attempt.updated_at = now
        db.add(
            JobEvent(
                job_id=attempt.job_id,
                attempt_id=attempt.id,
                event_type="attempt.publish_failed",
                from_status=attempt.status,
                to_status=attempt.status,
                payload=error,
            )
        )
        await db.flush()
        return True

    @staticmethod
    async def claim_attempt_for_execution(
        db: AsyncSession,
        attempt_id: uuid.UUID,
        *,
        worker_id: str,
        lease_seconds: int,
    ) -> tuple[Job, JobAttempt, uuid.UUID] | None:
        result = await db.execute(
            select(Job, JobAttempt)
            .join(JobAttempt, JobAttempt.job_id == Job.id)
            .where(
                JobAttempt.id == attempt_id,
                Job.active_attempt_id == JobAttempt.id,
                Job.status == "queued",
                Job.deleted_at.is_(None),
                JobAttempt.status.in_(["queued", "published"]),
            )
            .with_for_update()
        )
        row = result.one_or_none()
        if row is None:
            return None
        job, attempt = row
        now = datetime.now(timezone.utc)
        lease_token = uuid.uuid4()
        attempt.status = "running"
        attempt.worker_id = worker_id
        attempt.lease_token = lease_token
        attempt.leased_at = now
        attempt.lease_expires_at = now + timedelta(seconds=lease_seconds)
        attempt.heartbeat_at = now
        attempt.started_at = now
        attempt.updated_at = now
        job.status = "running"
        job.progress_percent = max(job.progress_percent or 0, 5)
        job.progress_text = "正在执行"
        job.progress_stage = "running"
        job.started_at = job.started_at or now
        job.last_execution_at = now
        job.last_heartbeat_at = now
        job.execution_attempts = (job.execution_attempts or 0) + 1
        job.updated_at = now
        # Shared runner CAS token for progress/result state transitions.
        job.execution_token = str(attempt_id)
        db.add(
            JobEvent(
                job_id=job.id,
                attempt_id=attempt.id,
                event_type="attempt.claimed",
                from_status="published",
                to_status="running",
                payload={"worker_id": worker_id},
            )
        )
        await db.flush()
        return job, attempt, lease_token

    @staticmethod
    async def heartbeat_attempt(
        db: AsyncSession,
        attempt_id: uuid.UUID,
        *,
        lease_token: uuid.UUID,
        lease_seconds: int,
    ) -> bool:
        result = await db.execute(
            select(Job, JobAttempt)
            .join(JobAttempt, JobAttempt.job_id == Job.id)
            .where(
                JobAttempt.id == attempt_id,
                JobAttempt.status == "running",
                JobAttempt.lease_token == lease_token,
                Job.active_attempt_id == JobAttempt.id,
                Job.status == "running",
                Job.deleted_at.is_(None),
            )
            .with_for_update(skip_locked=True)
        )
        row = result.one_or_none()
        if row is None:
            return False
        job, attempt = row
        now = datetime.now(timezone.utc)
        attempt.heartbeat_at = now
        attempt.lease_expires_at = now + timedelta(seconds=lease_seconds)
        attempt.updated_at = now
        job.last_heartbeat_at = now
        job.updated_at = now
        await db.flush()
        return True

    @staticmethod
    async def mark_attempt_failed(
        db: AsyncSession,
        attempt_id: uuid.UUID,
        *,
        lease_token: uuid.UUID | None,
        error: dict[str, Any],
        error_kind: str = "worker_error",
        failure_phase: str = "execute",
        retryable: bool = False,
        next_dispatch_at: datetime | None = None,
    ) -> bool:
        conditions = [JobAttempt.id == attempt_id, JobAttempt.status == "running"]
        if lease_token is not None:
            conditions.append(JobAttempt.lease_token == lease_token)
        result = await db.execute(
            select(Job, JobAttempt)
            .join(JobAttempt, JobAttempt.job_id == Job.id)
            .where(*conditions, Job.active_attempt_id == JobAttempt.id, Job.status.in_(["queued", "running", "failed"]))
            .with_for_update(skip_locked=True)
        )
        row = result.one_or_none()
        if row is None:
            return False
        job, attempt = row
        now = datetime.now(timezone.utc)
        attempt.status = "failed"
        attempt.finished_at = now
        attempt.error = error
        attempt.error_kind = error_kind
        attempt.failure_phase = failure_phase
        can_retry = retryable and job.status != "failed" and job.attempt_count < job.max_attempts
        attempt.retryable = can_retry
        attempt.lease_token = None
        attempt.lease_expires_at = None
        attempt.updated_at = now
        if can_retry:
            next_attempt_id = uuid.uuid4()
            next_attempt_no = (job.attempt_count or 0) + 1
            next_attempt = JobAttempt(
                id=next_attempt_id,
                job_id=job.id,
                attempt_no=next_attempt_no,
                status="queued",
                timeout_seconds=job.timeout_seconds or attempt.timeout_seconds,
                next_dispatch_at=next_dispatch_at or now,
            )
            db.add(next_attempt)
            job.active_attempt_id = next_attempt_id
            job.attempt_count = next_attempt_no
            job.execution_generation = (job.execution_generation or 1) + 1
            job.execution_token = None
            job.status = "queued"
            job.progress_percent = 0
            job.progress_text = "已排队"
            job.progress_stage = "accepted"
            job.error = None
            job.finished_at = None
            db.add(
                JobEvent(
                    job_id=job.id,
                    attempt_id=next_attempt_id,
                    event_type="attempt.created",
                    to_status="queued",
                    payload={
                        "reason": "retry",
                        "previous_attempt_id": str(attempt.id),
                        "previous_error": error,
                    },
                )
            )
        elif job.status != "failed":
            job.status = "failed"
            job.progress_text = "处理失败"
            job.progress_stage = "failed"
            job.result = None
            job.error = error
            job.finished_at = now
            job.callback_status = "pending" if job.callback_url else "not_configured"
        job.last_heartbeat_at = now
        job.updated_at = now
        db.add(
            JobEvent(
                job_id=job.id,
                attempt_id=attempt.id,
                event_type="attempt.failed",
                from_status="running",
                to_status="failed",
                payload={**error, "retryable": can_retry},
            )
        )
        await db.flush()
        return True

    @staticmethod
    async def mark_attempt_succeeded(
        db: AsyncSession,
        attempt_id: uuid.UUID,
        *,
        lease_token: uuid.UUID,
    ) -> bool:
        result = await db.execute(
            select(Job, JobAttempt)
            .join(JobAttempt, JobAttempt.job_id == Job.id)
            .where(
                JobAttempt.id == attempt_id,
                JobAttempt.status == "running",
                JobAttempt.lease_token == lease_token,
                Job.active_attempt_id == JobAttempt.id,
                Job.status == "succeeded",
            )
            .with_for_update(skip_locked=True)
        )
        row = result.one_or_none()
        if row is None:
            return False
        job, attempt = row
        now = datetime.now(timezone.utc)
        attempt.status = "succeeded"
        attempt.finished_at = now
        attempt.lease_token = None
        attempt.lease_expires_at = None
        attempt.heartbeat_at = now
        attempt.updated_at = now
        db.add(
            JobEvent(
                job_id=job.id,
                attempt_id=attempt.id,
                event_type="attempt.succeeded",
                from_status="running",
                to_status="succeeded",
            )
        )
        await db.flush()
        return True

    @staticmethod
    async def find_dispatch_due_attempts(db: AsyncSession, now: datetime, *, limit: int) -> list[JobAttempt]:
        result = await db.execute(
            select(JobAttempt)
            .join(Job, Job.id == JobAttempt.job_id)
            .where(
                Job.status == "queued",
                Job.active_attempt_id == JobAttempt.id,
                Job.deleted_at.is_(None),
                or_(
                    and_(
                        JobAttempt.status == "queued",
                        or_(JobAttempt.next_dispatch_at.is_(None), JobAttempt.next_dispatch_at <= now),
                    ),
                    and_(JobAttempt.status == "published", JobAttempt.next_dispatch_at <= now),
                ),
            )
            .order_by(JobAttempt.created_at.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    @staticmethod
    async def find_stale_running_attempts(db: AsyncSession, now: datetime, *, limit: int) -> list[JobAttempt]:
        result = await db.execute(
            select(JobAttempt)
            .join(Job, Job.id == JobAttempt.job_id)
            .where(
                Job.status == "running",
                Job.active_attempt_id == JobAttempt.id,
                Job.deleted_at.is_(None),
                JobAttempt.status == "running",
                JobAttempt.lease_expires_at <= now,
            )
            .order_by(JobAttempt.lease_expires_at.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    @staticmethod
    async def get(db: AsyncSession, job_id: uuid.UUID) -> Job | None:
        result = await db.execute(select(Job).where(Job.id == job_id, Job.deleted_at.is_(None)))
        return result.scalar_one_or_none()

    @staticmethod
    async def get_for_caller(db: AsyncSession, job_id: uuid.UUID, caller_id: str) -> Job | None:
        result = await db.execute(
            select(Job).where(Job.id == job_id, Job.caller_id == caller_id, Job.deleted_at.is_(None))
        )
        return result.scalar_one_or_none()

    async def update_progress(
        db: AsyncSession,
        job_id: uuid.UUID,
        *,
        progress_percent: int,
        progress_text: str,
        progress_stage: str | None = None,
        execution_token: str | None = None,
        execution_generation: int | None = None,
    ) -> bool:
        conditions = [Job.id == job_id, Job.deleted_at.is_(None)]
        if execution_token is not None:
            conditions.extend([Job.status == "running", Job.execution_token == execution_token])
        if execution_generation is not None:
            conditions.append(Job.execution_generation == execution_generation)
        result = await db.execute(select(Job).where(*conditions).with_for_update(skip_locked=True))
        job = result.scalar_one_or_none()
        if not job:
            return False
        now = datetime.now(timezone.utc)
        job.progress_percent = max(0, min(100, progress_percent))
        job.progress_text = progress_text
        if progress_stage is not None:
            job.progress_stage = progress_stage
        job.last_heartbeat_at = now
        job.updated_at = now
        await db.flush()
        return True

    @staticmethod
    async def mark_succeeded(
        db: AsyncSession,
        job_id: uuid.UUID,
        *,
        execution_token: str,
        result: dict[str, Any] | None,
        canonical_result: dict[str, Any] | None = None,
        canonical_result_ref: dict[str, Any] | None = None,
    ) -> bool:
        query_result = await db.execute(
            select(Job)
            .where(
                Job.id == job_id,
                Job.status == "running",
                Job.execution_token == execution_token,
                Job.deleted_at.is_(None),
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
        job.callback_status = "pending" if job.callback_url else "not_configured"
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
        execution_token: str | None = None,
    ) -> bool:
        conditions = [Job.id == job_id, Job.status.in_(["queued", "running"]), Job.deleted_at.is_(None)]
        if execution_token:
            conditions.append(Job.execution_token == execution_token)
        result = await db.execute(
            select(Job)
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
        job.callback_status = "pending" if job.callback_url else "not_configured"
        job.callback_attempts = 0
        job.callback_next_retry_at = None
        job.callback_last_error = None
        await db.flush()
        return True

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
                "UPDATE jobs "
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
    ) -> list[Job]:
        result = await db.execute(
            select(Job)
            .where(
                Job.status.in_(["succeeded", "failed"]),
                Job.callback_status.in_(["pending", "failed", "delivering"]),
                Job.callback_attempts < max_attempts,
                or_(Job.callback_next_retry_at.is_(None), Job.callback_next_retry_at <= now),
                Job.deleted_at.is_(None),
            )
            .order_by(Job.finished_at.asc(), Job.created_at.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def count_active_jobs(db: AsyncSession) -> int:
        result = await db.execute(
            select(func.count())
            .select_from(Job)
            .where(Job.status.in_(["queued", "running"]), Job.deleted_at.is_(None))
        )
        return result.scalar_one()

    @staticmethod
    async def cleanup_expired_jobs(db: AsyncSession) -> int:
        """软删除过期且生命周期已收敛的 Job 记录（expires_at <= now）。"""
        expired_settled_jobs = select(Job.id).where(
            Job.expires_at <= func.now(),
            Job.deleted_at.is_(None),
            Job.status.in_(["succeeded", "failed"]),
            Job.callback_status.in_(["delivered", "skipped", "not_configured"]),
        )
        result = await db.execute(
            update(Job)
            .where(Job.id.in_(expired_settled_jobs))
            .values(
                delete_requested_at=func.now(),
                deleted_at=func.now(),
                deleted_reason="expired",
                updated_at=func.now(),
            )
        )
        await db.flush()
        return result.rowcount

    @staticmethod
    async def list_jobs_before(
        db: AsyncSession,
        expires_before: datetime,
    ) -> list[Job]:
        """查询在指定时间前过期的 Job（用于清理前的日志或备份）"""
        result = await db.execute(
            select(Job)
            .where(Job.expires_at <= expires_before, Job.deleted_at.is_(None))
            .order_by(Job.created_at.asc())
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
