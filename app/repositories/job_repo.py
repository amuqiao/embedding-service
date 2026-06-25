import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, delete, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.error_registry import get_error_spec
from app.models.job import CallbackOutbox, DispatchOutbox, Job, JobAttempt, JobEvent, JobSubmissionKey

CALLBACK_EVENT_NAMESPACE = "ai-job-callback"
DISPATCH_TASK_NAME = "jobs.run_attempt"
SUBMISSION_KEY_KIND_CLIENT_REQUEST_ID = "client_request_id"


class JobRepo:
    @staticmethod
    async def advisory_lock_for_client_request(db: AsyncSession, caller_id: str, client_request_id: str) -> None:
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
            {"lock_key": f"{caller_id}:{client_request_id}"},
        )

    @staticmethod
    async def get_submission_by_client_request(
        db: AsyncSession,
        *,
        caller_id: str,
        client_request_id: str,
    ) -> tuple[Job, JobSubmissionKey] | None:
        query_result = await db.execute(
            select(Job, JobSubmissionKey)
            .join(JobSubmissionKey, JobSubmissionKey.job_id == Job.id)
            .where(
                JobSubmissionKey.caller_id == caller_id,
                JobSubmissionKey.key_kind == SUBMISSION_KEY_KIND_CLIENT_REQUEST_ID,
                JobSubmissionKey.key_value == client_request_id,
                Job.deleted_at.is_(None),
                Job.is_internal.is_(False),
            )
            .order_by(JobSubmissionKey.created_at.asc())
            .limit(1)
        )
        return query_result.one_or_none()

    @staticmethod
    async def create(
        db: AsyncSession,
        *,
        caller_id: str,
        client_request_id: str | None,
        job_type: str,
        metadata: dict[str, Any] | None = None,
        priority: str = "normal",
        timeout_seconds: int | None = None,
        job_params: dict[str, Any] | None = None,
        max_attempts: int = 1,
        job_params_ref: dict[str, Any] | None = None,
        job_params_hash: str | None = None,
        callback_url: str | None = None,
        callback_events: list[str] | None = None,
        root_job_id: uuid.UUID | None = None,
        parent_job_id: uuid.UUID | None = None,
        is_internal: bool = False,
        workflow_node_key: str | None = None,
    ) -> Job:
        if is_internal and root_job_id is None:
            raise ValueError("internal job must include root_job_id")
        if is_internal and client_request_id is not None:
            raise ValueError("internal job must not include client_request_id")
        if is_internal and (callback_url is not None or callback_events is not None):
            raise ValueError("internal job must not include callback")
        if not is_internal and parent_job_id is not None:
            raise ValueError("parent_job_id requires internal job")
        if not is_internal and workflow_node_key is not None:
            raise ValueError("workflow_node_key requires internal job")
        if workflow_node_key is not None and root_job_id is None:
            raise ValueError("workflow_node_key requires root_job_id")
        job = Job(
            caller_id=caller_id,
            client_request_id=client_request_id,
            root_job_id=root_job_id,
            parent_job_id=parent_job_id,
            is_internal=is_internal,
            workflow_node_key=workflow_node_key,
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
            callback_status="pending" if callback_url else "not_configured",
        )
        db.add(job)
        await db.flush()
        await db.refresh(job)
        return job

    @staticmethod
    async def create_submission_key(
        db: AsyncSession,
        *,
        caller_id: str,
        client_request_id: str,
        request_fingerprint: str,
        job: Job,
    ) -> JobSubmissionKey:
        key = JobSubmissionKey(
            caller_id=caller_id,
            key_kind=SUBMISSION_KEY_KIND_CLIENT_REQUEST_ID,
            key_value=client_request_id,
            request_fingerprint=request_fingerprint,
            job_id=job.id,
            expires_at=job.expires_at,
        )
        db.add(key)
        await db.flush()
        return key

    @staticmethod
    def _callback_events(job: Job) -> list[str]:
        if job.callback_events is None:
            return ["job.succeeded", "job.failed"]
        return list(job.callback_events)

    @staticmethod
    def _terminal_callback_event_type(job: Job) -> str | None:
        if job.status == "succeeded":
            return "job.succeeded"
        if job.status == "failed":
            return "job.failed"
        return None

    @staticmethod
    def _job_error_detail(error: dict[str, Any] | None) -> dict[str, Any] | None:
        if error is None:
            return None
        reason = str(error.get("code") or "INTERNAL_ERROR")
        details = dict(error.get("details")) if isinstance(error.get("details"), dict) else {}
        for key, value in error.items():
            if key not in {"code", "reason", "message", "details"}:
                details[key] = value
        return {
            "reason": reason,
            "details": details,
            "retryable": get_error_spec(reason).retryable,
        }

    @staticmethod
    def _trigger_request_id(job: Job) -> str | None:
        runtime_ref = job.runtime_ref if isinstance(job.runtime_ref, dict) else {}
        payload = runtime_ref.get("payload")
        if not isinstance(payload, dict):
            return None
        runtime_fields = payload.get("runtime_fields")
        if not isinstance(runtime_fields, dict):
            return None
        system_fields = runtime_fields.get("_system")
        if not isinstance(system_fields, dict):
            return None
        value = system_fields.get("trigger_request_id")
        return value if isinstance(value, str) and value else None

    @staticmethod
    def _terminal_callback_payload(job: Job, *, event_id: uuid.UUID, event_type: str, now: datetime) -> dict[str, Any]:
        progress_stage = "completed" if job.status == "succeeded" else "failed"
        callback_status = "pending" if event_type in set(JobRepo._callback_events(job)) else "skipped"
        return {
            "event": event_type,
            "event_id": str(event_id),
            "attempt": 1,
            "sent_at": now.isoformat(),
            "trigger_request_id": JobRepo._trigger_request_id(job),
            "caller_id": job.caller_id,
            "job": {
                "job_id": str(job.id),
                "client_request_id": job.client_request_id,
                "job_type": job.job_type,
                "job_status": job.status,
                "job_progress": {
                    "percent": job.progress_percent or 0,
                    "message": job.progress_text or progress_stage,
                    "stage": progress_stage,
                },
                "job_result": job.result,
                "job_error": JobRepo._job_error_detail(job.error),
                "callback": {
                    "status": callback_status,
                    "attempt": 0,
                    "last_error": None,
                    "next_retry_at": now.isoformat() if callback_status == "pending" else None,
                },
                "status_url": f"{settings.service.api_prefix}/jobs/{job.id}",
                "created_at": (job.created_at or now).isoformat(),
                "updated_at": (job.updated_at or now).isoformat(),
                "finished_at": (job.finished_at or now).isoformat(),
            },
        }

    @staticmethod
    async def ensure_terminal_callback_outbox(db: AsyncSession, job: Job, *, now: datetime) -> CallbackOutbox | None:
        event_type = JobRepo._terminal_callback_event_type(job)
        if not job.callback_url or event_type is None:
            job.callback_status = "not_configured"
            job.callback_attempts = 0
            job.callback_next_retry_at = None
            job.callback_last_error = None
            return None

        result = await db.execute(
            select(CallbackOutbox)
            .where(CallbackOutbox.job_id == job.id, CallbackOutbox.event_type == event_type)
            .with_for_update(skip_locked=True)
        )
        existing = result.scalar_one_or_none()
        if existing is not None:
            job.callback_status = "failed" if existing.status == "dead_letter" else existing.status
            job.callback_attempts = existing.delivery_attempts or 0
            job.callback_next_retry_at = existing.next_attempt_at
            job.callback_last_error = existing.last_error
            return existing

        subscribed = event_type in set(JobRepo._callback_events(job))
        outbox_status = "pending" if subscribed else "skipped"
        event_id = uuid.uuid5(uuid.NAMESPACE_URL, f"{CALLBACK_EVENT_NAMESPACE}:{job.id}:{event_type}")
        outbox = CallbackOutbox(
            job_id=job.id,
            event_id=event_id,
            event_type=event_type,
            callback_url=job.callback_url,
            signature_version="hmac-sha256:v1",
            status=outbox_status,
            payload=JobRepo._terminal_callback_payload(job, event_id=event_id, event_type=event_type, now=now),
            next_attempt_at=now if subscribed else None,
        )
        db.add(outbox)
        await db.flush()
        job.callback_status = "pending" if subscribed else "skipped"
        job.callback_attempts = 0
        job.callback_next_retry_at = now if subscribed else None
        job.callback_last_error = None
        db.add(
            JobEvent(
                job_id=job.id,
                callback_id=outbox.id,
                event_type="callback.created" if subscribed else "callback.skipped",
                to_status=outbox_status,
                payload={"event_type": event_type, "event_id": str(event_id)},
            )
        )
        await db.flush()
        return outbox

    @staticmethod
    async def create_dispatch_outbox(
        db: AsyncSession,
        *,
        job_id: uuid.UUID,
        attempt_id: uuid.UUID,
        next_attempt_at: datetime | None,
        dispatch_reason: str,
    ) -> DispatchOutbox:
        outbox = DispatchOutbox(
            event_id=f"job_attempt:{attempt_id}:dispatch",
            job_id=job_id,
            attempt_id=attempt_id,
            task_name=DISPATCH_TASK_NAME,
            payload={"attempt_id": str(attempt_id)},
            status="pending",
            next_attempt_at=next_attempt_at,
        )
        db.add(outbox)
        db.add(
            JobEvent(
                job_id=job_id,
                attempt_id=attempt_id,
                event_type="dispatch.created",
                to_status="pending",
                payload={"task_name": DISPATCH_TASK_NAME, "dispatch_reason": dispatch_reason},
            )
        )
        await db.flush()
        return outbox

    @staticmethod
    async def create_initial_attempt(db: AsyncSession, job: Job, *, timeout_seconds: int) -> JobAttempt:
        attempt_id = uuid.uuid4()
        attempt = JobAttempt(
            id=attempt_id,
            job_id=job.id,
            attempt_no=1,
            status="pending",
            timeout_seconds=timeout_seconds,
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
                to_status="pending",
            )
        )
        await db.flush()
        await JobRepo.create_dispatch_outbox(
            db,
            job_id=job.id,
            attempt_id=attempt_id,
            next_attempt_at=datetime.now(timezone.utc),
            dispatch_reason="initial",
        )
        await db.flush()
        return attempt

    @staticmethod
    async def get_attempt(db: AsyncSession, attempt_id: uuid.UUID) -> JobAttempt | None:
        result = await db.execute(select(JobAttempt).where(JobAttempt.id == attempt_id))
        return result.scalar_one_or_none()

    @staticmethod
    async def lease_dispatch_for_publish(
        db: AsyncSession,
        attempt_id: uuid.UUID,
        *,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> tuple[DispatchOutbox, uuid.UUID] | None:
        now = now or datetime.now(timezone.utc)
        result = await db.execute(
            select(DispatchOutbox, Job, JobAttempt)
            .join(Job, Job.id == DispatchOutbox.job_id)
            .join(JobAttempt, JobAttempt.id == DispatchOutbox.attempt_id)
            .where(
                DispatchOutbox.attempt_id == attempt_id,
                DispatchOutbox.task_name == DISPATCH_TASK_NAME,
                Job.status == "queued",
                Job.active_attempt_id == JobAttempt.id,
                Job.deleted_at.is_(None),
                JobAttempt.status == "pending",
                or_(
                    and_(
                        DispatchOutbox.status.in_(["pending", "retrying"]),
                        or_(DispatchOutbox.next_attempt_at.is_(None), DispatchOutbox.next_attempt_at <= now),
                    ),
                    and_(DispatchOutbox.status == "leased", DispatchOutbox.lease_expires_at <= now),
                    and_(DispatchOutbox.status == "published", DispatchOutbox.next_attempt_at <= now),
                ),
            )
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        row = result.one_or_none()
        if row is None:
            return None
        dispatch, _job, _attempt = row
        previous = dispatch.status
        lease_token = uuid.uuid4()
        dispatch.status = "leased"
        dispatch.lease_token = lease_token
        dispatch.lease_expires_at = now + timedelta(seconds=lease_seconds)
        dispatch.leased_at = now
        dispatch.updated_at = now
        db.add(
            JobEvent(
                job_id=dispatch.job_id,
                attempt_id=dispatch.attempt_id,
                event_type="dispatch.leased",
                from_status=previous,
                to_status="leased",
                payload={"dispatch_id": str(dispatch.id), "task_name": dispatch.task_name},
            )
        )
        await db.flush()
        return dispatch, lease_token

    @staticmethod
    async def mark_dispatch_published(
        db: AsyncSession,
        dispatch_id: uuid.UUID,
        *,
        lease_token: uuid.UUID,
        next_attempt_at: datetime,
    ) -> bool:
        now = datetime.now(timezone.utc)
        result = await db.execute(
            select(DispatchOutbox)
            .where(
                DispatchOutbox.id == dispatch_id,
                DispatchOutbox.status == "leased",
                DispatchOutbox.lease_token == lease_token,
            )
            .with_for_update(skip_locked=True)
        )
        dispatch = result.scalar_one_or_none()
        if dispatch is None:
            return False
        dispatch.status = "published"
        dispatch.publish_attempts = (dispatch.publish_attempts or 0) + 1
        dispatch.next_attempt_at = next_attempt_at
        dispatch.lease_token = None
        dispatch.lease_expires_at = None
        dispatch.last_error = None
        dispatch.published_at = now
        dispatch.updated_at = now
        db.add(
            JobEvent(
                job_id=dispatch.job_id,
                attempt_id=dispatch.attempt_id,
                event_type="dispatch.published",
                from_status="leased",
                to_status="published",
                payload={"dispatch_id": str(dispatch.id), "task_name": dispatch.task_name},
            )
        )
        await db.flush()
        return True

    @staticmethod
    async def mark_dispatch_publish_failed(
        db: AsyncSession,
        dispatch_id: uuid.UUID,
        *,
        lease_token: uuid.UUID,
        error: dict[str, Any],
        next_attempt_at: datetime,
        max_publish_attempts: int,
    ) -> bool:
        now = datetime.now(timezone.utc)
        result = await db.execute(
            select(DispatchOutbox)
            .where(
                DispatchOutbox.id == dispatch_id,
                DispatchOutbox.status == "leased",
                DispatchOutbox.lease_token == lease_token,
            )
            .with_for_update(skip_locked=True)
        )
        dispatch = result.scalar_one_or_none()
        if dispatch is None:
            return False
        previous = dispatch.status
        publish_attempts = (dispatch.publish_attempts or 0) + 1
        dispatch.publish_attempts = publish_attempts
        dispatch.lease_token = None
        dispatch.lease_expires_at = None
        dispatch.last_error = error
        dispatch.updated_at = now
        if publish_attempts >= max_publish_attempts:
            dispatch.status = "dead_letter"
            dispatch.next_attempt_at = None
            dispatch.dead_lettered_at = now
        else:
            dispatch.status = "retrying"
            dispatch.next_attempt_at = next_attempt_at
        db.add(
            JobEvent(
                job_id=dispatch.job_id,
                attempt_id=dispatch.attempt_id,
                event_type="dispatch.dead_lettered"
                if dispatch.status == "dead_letter"
                else "dispatch.publish_failed",
                from_status=previous,
                to_status=dispatch.status,
                payload={**error, "dispatch_id": str(dispatch.id), "publish_attempts": publish_attempts},
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
                JobAttempt.status == "pending",
            )
            .with_for_update()
        )
        row = result.one_or_none()
        if row is None:
            return None
        job, attempt = row
        now = datetime.now(timezone.utc)
        previous_attempt_status = attempt.status
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
                from_status=previous_attempt_status,
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
        next_attempt_at: datetime | None = None,
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
                status="pending",
                timeout_seconds=job.timeout_seconds or attempt.timeout_seconds,
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
                    to_status="pending",
                    payload={
                        "reason": "retry",
                        "previous_attempt_id": str(attempt.id),
                        "previous_error": error,
                    },
                )
            )
            await JobRepo.create_dispatch_outbox(
                db,
                job_id=job.id,
                attempt_id=next_attempt_id,
                next_attempt_at=next_attempt_at or now,
                dispatch_reason="retry",
            )
        elif job.status != "failed":
            job.status = "failed"
            job.progress_text = "处理失败"
            job.progress_stage = "failed"
            job.result = None
            job.error = error
            job.finished_at = now
            await JobRepo.ensure_terminal_callback_outbox(db, job, now=now)
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
    async def mark_workflow_orchestration_attempt_succeeded(
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
                Job.status == "running",
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
        job.active_attempt_id = None
        job.execution_token = None
        job.progress_percent = max(job.progress_percent or 0, 20)
        job.progress_text = "等待子任务完成"
        job.progress_stage = "planning"
        job.last_heartbeat_at = now
        job.updated_at = now
        db.add(
            JobEvent(
                job_id=job.id,
                attempt_id=attempt.id,
                event_type="attempt.succeeded",
                from_status="running",
                to_status="succeeded",
                payload={"reason": "workflow_orchestration"},
            )
        )
        await db.flush()
        return True

    @staticmethod
    async def find_due_dispatches(db: AsyncSession, now: datetime, *, limit: int) -> list[DispatchOutbox]:
        result = await db.execute(
            select(DispatchOutbox)
            .join(Job, Job.id == DispatchOutbox.job_id)
            .join(JobAttempt, JobAttempt.id == DispatchOutbox.attempt_id)
            .where(
                Job.status == "queued",
                Job.active_attempt_id == JobAttempt.id,
                Job.deleted_at.is_(None),
                JobAttempt.status == "pending",
                or_(
                    and_(
                        DispatchOutbox.status.in_(["pending", "retrying"]),
                        or_(DispatchOutbox.next_attempt_at.is_(None), DispatchOutbox.next_attempt_at <= now),
                    ),
                    and_(DispatchOutbox.status == "leased", DispatchOutbox.lease_expires_at <= now),
                    and_(DispatchOutbox.status == "published", DispatchOutbox.next_attempt_at <= now),
                ),
            )
            .order_by(DispatchOutbox.created_at.asc())
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
    async def find_workflow_roots_for_reconciliation(db: AsyncSession, *, limit: int) -> list[Job]:
        result = await db.execute(
            select(Job)
            .where(
                Job.status == "running",
                Job.active_attempt_id.is_(None),
                Job.is_internal.is_(False),
                Job.deleted_at.is_(None),
                Job.runtime_ref["payload"].op("?")("workflow_plan"),
            )
            .order_by(Job.updated_at.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    @staticmethod
    async def find_active_pending_attempts_missing_dispatch(db: AsyncSession, *, limit: int) -> list[JobAttempt]:
        dispatch_exists = (
            select(DispatchOutbox.id)
            .where(
                DispatchOutbox.attempt_id == JobAttempt.id,
                DispatchOutbox.task_name == DISPATCH_TASK_NAME,
            )
            .exists()
        )
        result = await db.execute(
            select(JobAttempt)
            .join(Job, Job.id == JobAttempt.job_id)
            .where(
                Job.status == "queued",
                Job.active_attempt_id == JobAttempt.id,
                Job.deleted_at.is_(None),
                JobAttempt.status == "pending",
                ~dispatch_exists,
            )
            .order_by(JobAttempt.created_at.asc())
            .with_for_update(skip_locked=True)
            .limit(limit)
        )
        return list(result.scalars().all())

    @staticmethod
    async def find_terminal_root_jobs_missing_callback_outbox(db: AsyncSession, *, limit: int) -> list[Job]:
        succeeded_callback_exists = (
            select(CallbackOutbox.id)
            .where(
                CallbackOutbox.job_id == Job.id,
                CallbackOutbox.event_type == "job.succeeded",
            )
            .exists()
        )
        failed_callback_exists = (
            select(CallbackOutbox.id)
            .where(
                CallbackOutbox.job_id == Job.id,
                CallbackOutbox.event_type == "job.failed",
            )
            .exists()
        )
        result = await db.execute(
            select(Job)
            .where(
                Job.status.in_(["succeeded", "failed"]),
                Job.is_internal.is_(False),
                Job.callback_url.is_not(None),
                Job.deleted_at.is_(None),
                or_(
                    and_(Job.status == "succeeded", ~succeeded_callback_exists),
                    and_(Job.status == "failed", ~failed_callback_exists),
                ),
            )
            .order_by(Job.updated_at.asc())
            .with_for_update(skip_locked=True)
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
            select(Job).where(
                Job.id == job_id,
                Job.caller_id == caller_id,
                Job.deleted_at.is_(None),
                Job.is_internal.is_(False),
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_internal_child_by_node_key(
        db: AsyncSession,
        *,
        root_job_id: uuid.UUID,
        workflow_node_key: str,
    ) -> Job | None:
        result = await db.execute(
            select(Job).where(
                Job.root_job_id == root_job_id,
                Job.workflow_node_key == workflow_node_key,
                Job.is_internal.is_(True),
                Job.deleted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_internal_children(
        db: AsyncSession,
        *,
        root_job_id: uuid.UUID,
        statuses: list[str] | None = None,
    ) -> list[Job]:
        conditions = [
            Job.root_job_id == root_job_id,
            Job.is_internal.is_(True),
            Job.deleted_at.is_(None),
        ]
        if statuses is not None:
            conditions.append(Job.status.in_(statuses))
        result = await db.execute(select(Job).where(*conditions).order_by(Job.created_at.asc()))
        return list(result.scalars().all())

    @staticmethod
    async def get_workflow_root_for_update(db: AsyncSession, root_job_id: uuid.UUID) -> Job | None:
        result = await db.execute(
            select(Job)
            .where(
                Job.id == root_job_id,
                Job.is_internal.is_(False),
                Job.deleted_at.is_(None),
            )
            .with_for_update()
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
        await JobRepo.ensure_terminal_callback_outbox(db, job, now=now)
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
        await JobRepo.ensure_terminal_callback_outbox(db, job, now=now)
        await db.flush()
        return True

    @staticmethod
    async def mark_workflow_root_succeeded(
        db: AsyncSession,
        root_job_id: uuid.UUID,
        *,
        result: dict[str, Any],
        canonical_result: dict[str, Any],
    ) -> bool:
        query_result = await db.execute(
            select(Job)
            .where(
                Job.id == root_job_id,
                Job.status == "running",
                Job.active_attempt_id.is_(None),
                Job.is_internal.is_(False),
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
        job.canonical_result_ref = None
        job.error = None
        job.finished_at = now
        job.last_heartbeat_at = now
        job.updated_at = now
        await JobRepo.ensure_terminal_callback_outbox(db, job, now=now)
        db.add(
            JobEvent(
                job_id=job.id,
                event_type="workflow.root.succeeded",
                from_status="running",
                to_status="succeeded",
                payload={"reason": "workflow_finalize"},
            )
        )
        await db.flush()
        return True

    @staticmethod
    async def mark_workflow_root_failed(
        db: AsyncSession,
        root_job_id: uuid.UUID,
        *,
        error: dict[str, Any],
    ) -> bool:
        query_result = await db.execute(
            select(Job)
            .where(
                Job.id == root_job_id,
                Job.status == "running",
                Job.active_attempt_id.is_(None),
                Job.is_internal.is_(False),
                Job.deleted_at.is_(None),
            )
            .with_for_update(skip_locked=True)
        )
        job = query_result.scalar_one_or_none()
        if not job:
            return False
        now = datetime.now(timezone.utc)
        job.status = "failed"
        job.progress_text = "处理失败"
        job.progress_stage = "failed"
        job.result = None
        job.canonical_result = None
        job.canonical_result_ref = None
        job.error = error
        job.finished_at = now
        job.last_heartbeat_at = now
        job.updated_at = now
        await JobRepo.ensure_terminal_callback_outbox(db, job, now=now)
        db.add(
            JobEvent(
                job_id=job.id,
                event_type="workflow.root.failed",
                from_status="running",
                to_status="failed",
                payload={**error, "reason": "workflow_finalize"},
            )
        )
        await db.flush()
        return True

    @staticmethod
    async def mark_callback_delivering(
        db: AsyncSession,
        job_id: uuid.UUID,
        *,
        now: datetime,
        max_attempts: int,
        next_retry_at: datetime,
    ) -> tuple[Job, CallbackOutbox] | None:
        result = await db.execute(
            select(Job, CallbackOutbox)
            .join(CallbackOutbox, CallbackOutbox.job_id == Job.id)
            .where(
                Job.id == job_id,
                Job.status.in_(["succeeded", "failed"]),
                Job.deleted_at.is_(None),
                CallbackOutbox.status.in_(["pending", "retrying", "leased"]),
                CallbackOutbox.delivery_attempts < max_attempts,
                or_(
                    CallbackOutbox.next_attempt_at.is_(None),
                    CallbackOutbox.next_attempt_at <= now,
                    and_(CallbackOutbox.status == "leased", CallbackOutbox.lease_expires_at <= now),
                ),
            )
            .order_by(CallbackOutbox.next_attempt_at.asc().nullsfirst(), CallbackOutbox.created_at.asc())
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        row = result.one_or_none()
        if row is None:
            return None
        job, outbox = row
        if outbox.status == "leased" and outbox.lease_expires_at and outbox.lease_expires_at > now:
            return None
        previous_status = outbox.status
        outbox.status = "leased"
        outbox.lease_token = uuid.uuid4()
        outbox.lease_expires_at = next_retry_at
        outbox.leased_at = now
        outbox.updated_at = now
        job.callback_status = "delivering"
        job.callback_attempts = outbox.delivery_attempts or 0
        job.callback_next_retry_at = next_retry_at
        job.updated_at = now
        db.add(
            JobEvent(
                job_id=job.id,
                callback_id=outbox.id,
                event_type="callback.leased",
                from_status=previous_status,
                to_status="leased",
                payload={"event_type": outbox.event_type, "delivery_attempts": outbox.delivery_attempts or 0},
            )
        )
        await db.flush()
        return job, outbox

    @staticmethod
    async def mark_callback_result(
        db: AsyncSession,
        job_id: uuid.UUID,
        *,
        status: str,
        last_error: dict[str, Any] | None,
        next_retry_at: datetime | None,
        max_attempts: int,
        delivery_attempts: int = 1,
        last_response: dict[str, Any] | None = None,
        callback_id: uuid.UUID | None = None,
        lease_token: uuid.UUID | None = None,
    ) -> None:
        conditions = [Job.id == job_id, CallbackOutbox.job_id == Job.id]
        if callback_id is not None:
            conditions.append(CallbackOutbox.id == callback_id)
        else:
            conditions.append(CallbackOutbox.status == "leased")
        if lease_token is not None:
            conditions.extend([CallbackOutbox.status == "leased", CallbackOutbox.lease_token == lease_token])
        result = await db.execute(select(Job, CallbackOutbox).where(*conditions).with_for_update(skip_locked=True))
        row = result.one_or_none()
        if row is None:
            return

        job, outbox = row
        now = datetime.now(timezone.utc)
        previous_status = outbox.status
        attempted_count = max(0, delivery_attempts)
        if attempted_count:
            outbox.delivery_attempts = (outbox.delivery_attempts or 0) + attempted_count
            outbox.first_attempt_at = outbox.first_attempt_at or now
            outbox.last_attempt_at = now
        outbox.lease_token = None
        outbox.lease_expires_at = None
        outbox.last_error = last_error
        outbox.last_response = last_response
        outbox.updated_at = now
        if status == "delivered":
            outbox.status = "delivered"
            outbox.next_attempt_at = None
            outbox.delivered_at = now
            job.callback_status = "delivered"
            job.callback_delivered_at = now
            job.callback_next_retry_at = None
            job.callback_last_error = None
        elif status == "skipped":
            outbox.status = "skipped"
            outbox.next_attempt_at = None
            job.callback_status = "skipped"
            job.callback_next_retry_at = None
            job.callback_last_error = last_error
        elif status == "failed" and (outbox.delivery_attempts or 0) >= max_attempts:
            outbox.status = "dead_letter"
            outbox.next_attempt_at = None
            outbox.dead_lettered_at = now
            job.callback_status = "failed"
            job.callback_failed_at = now
            job.callback_next_retry_at = None
            job.callback_last_error = last_error
        elif status == "failed":
            outbox.status = "retrying"
            outbox.next_attempt_at = next_retry_at
            job.callback_status = "retrying"
            job.callback_next_retry_at = next_retry_at
            job.callback_last_error = last_error
        else:
            raise ValueError(f"unsupported callback result status: {status}")

        job.callback_attempts = outbox.delivery_attempts or 0
        job.callback_first_attempt_at = job.callback_first_attempt_at or outbox.first_attempt_at or now
        job.callback_last_attempt_at = outbox.last_attempt_at or now
        job.updated_at = now
        db.add(
            JobEvent(
                job_id=job.id,
                callback_id=outbox.id,
                event_type=f"callback.{outbox.status}",
                from_status=previous_status,
                to_status=outbox.status,
                payload={
                    "event_type": outbox.event_type,
                    "delivery_attempts": outbox.delivery_attempts,
                    "last_error": last_error,
                },
            )
        )
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
            .join(CallbackOutbox, CallbackOutbox.job_id == Job.id)
            .where(
                Job.status.in_(["succeeded", "failed"]),
                Job.deleted_at.is_(None),
                CallbackOutbox.status.in_(["pending", "retrying", "leased"]),
                CallbackOutbox.delivery_attempts < max_attempts,
                or_(
                    CallbackOutbox.next_attempt_at.is_(None),
                    CallbackOutbox.next_attempt_at <= now,
                    and_(CallbackOutbox.status == "leased", CallbackOutbox.lease_expires_at <= now),
                ),
            )
            .order_by(CallbackOutbox.next_attempt_at.asc().nullsfirst(), CallbackOutbox.created_at.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def count_active_jobs(db: AsyncSession) -> int:
        result = await db.execute(
            select(func.count())
            .select_from(Job)
            .where(
                or_(
                    Job.status == "queued",
                    and_(Job.status == "running", Job.active_attempt_id.is_not(None)),
                ),
                Job.deleted_at.is_(None),
            )
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
        ).subquery()
        await db.execute(
            delete(JobSubmissionKey).where(JobSubmissionKey.job_id.in_(select(expired_settled_jobs.c.id)))
        )
        result = await db.execute(
            update(Job)
            .where(Job.id.in_(select(expired_settled_jobs.c.id)))
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
