import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, func, or_, select, text, update
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
                JobSubmissionKey.deleted_at.is_(None),
                Job.deleted_at.is_(None),
                Job.root_job_id.is_(None),
                Job.workflow_node_key.is_(None),
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
        job_params_ref: dict[str, Any],
        job_params_hash: str,
        metadata: dict[str, Any] | None = None,
        priority: str = "normal",
        job_id: uuid.UUID | None = None,
        runtime_ref: dict[str, Any] | None = None,
        callback_url: str | None = None,
        callback_events: list[str] | None = None,
        root_job_id: uuid.UUID | None = None,
        workflow_node_key: str | None = None,
    ) -> Job:
        is_child = root_job_id is not None
        if is_child and workflow_node_key is None:
            raise ValueError("child job must include workflow_node_key")
        if is_child and client_request_id is not None:
            raise ValueError("child job must not include client_request_id")
        if is_child and (callback_url is not None or callback_events is not None):
            raise ValueError("child job must not include callback")
        if not is_child and workflow_node_key is not None:
            raise ValueError("workflow_node_key requires root_job_id")
        if not is_child and client_request_id is None:
            raise ValueError("public root job must include client_request_id")
        stored_callback_url = None if is_child else callback_url
        stored_callback_events = None if is_child else callback_events
        job = Job(
            id=job_id or uuid.uuid4(),
            caller_id=caller_id,
            client_request_id=client_request_id,
            root_job_id=root_job_id,
            workflow_node_key=workflow_node_key,
            job_type=job_type,
            status="queued",
            progress_percent=0,
            progress_text="已排队",
            queued_at=datetime.now(timezone.utc),
            priority=priority,
            metadata_=metadata or {},
            job_params_ref=job_params_ref,
            job_params_hash=job_params_hash,
            runtime_ref=runtime_ref,
            callback_url=stored_callback_url,
            callback_events=stored_callback_events,
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
    def _terminal_callback_payload(
        job: Job,
        *,
        event_id: uuid.UUID,
        event_type: str,
        now: datetime,
        job_result: dict[str, Any] | None,
        delivery_attempts: int = 0,
        next_retry_at: datetime | None = None,
        last_error: dict[str, Any] | None = None,
        cost: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
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
                "job_result": job_result,
                "job_error": JobRepo._job_error_detail(job.error),
                "cost": cost,
                "callback": {
                    "status": callback_status,
                    "attempt": delivery_attempts,
                    "last_error": last_error,
                    "next_retry_at": (next_retry_at or now).isoformat() if callback_status == "pending" else None,
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
            return None

        result = await db.execute(
            select(CallbackOutbox)
            .where(CallbackOutbox.job_id == job.id, CallbackOutbox.event_type == event_type)
            .with_for_update(skip_locked=True)
        )
        existing = result.scalar_one_or_none()
        if existing is not None:
            return existing

        subscribed = event_type in set(JobRepo._callback_events(job))
        outbox_status = "pending" if subscribed else "skipped"
        event_id = uuid.uuid5(uuid.NAMESPACE_URL, f"{CALLBACK_EVENT_NAMESPACE}:{job.id}:{event_type}")
        from app.services.billing import get_scope_billing, job_cost_from_billing

        billing = await get_scope_billing(db, scope_type="job", scope_id=str(job.id), caller_id=job.caller_id)
        mapped_cost = job_cost_from_billing(billing)
        cost = mapped_cost.model_dump() if mapped_cost is not None else None
        projected_result = job.result
        if job.status == "failed":
            from app.jobs.factory import get_job_executor

            handler = get_job_executor(job.job_type)
            projected_result = None
            if handler.supports_result_snapshot(job.status):
                projected_result = await handler.build_result_snapshot(job.status, job, db)
            projected_result = handler.validate_result_snapshot(job.status, projected_result)
        outbox = CallbackOutbox(
            job_id=job.id,
            event_id=event_id,
            event_type=event_type,
            callback_url=job.callback_url,
            signature_version="hmac-sha256:v1",
            status=outbox_status,
            payload=JobRepo._terminal_callback_payload(
                job,
                event_id=event_id,
                event_type=event_type,
                now=now,
                job_result=projected_result,
                cost=cost,
            ),
            max_delivery_attempts=settings.callback.max_delivery_attempts,
            request_timeout_seconds=settings.callback.delivery_timeout_seconds,
            retry_delay_seconds=settings.callback.retry_delay_seconds,
            delivery_retry_policy_snapshot={
                "max_delivery_attempts": settings.callback.max_delivery_attempts,
                "request_timeout_seconds": settings.callback.delivery_timeout_seconds,
                "retry_delay_seconds": settings.callback.retry_delay_seconds,
                "backoff_kind": "fixed",
            },
            next_attempt_at=now if subscribed else None,
        )
        db.add(outbox)
        await db.flush()
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
    async def get_terminal_callback_outbox(db: AsyncSession, job: Job) -> CallbackOutbox | None:
        event_type = JobRepo._terminal_callback_event_type(job)
        if event_type is None:
            return None
        result = await db.execute(
            select(CallbackOutbox)
            .where(CallbackOutbox.job_id == job.id, CallbackOutbox.event_type == event_type)
            .order_by(CallbackOutbox.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def create_dispatch_outbox(
        db: AsyncSession,
        *,
        event_job_id: uuid.UUID,
        attempt_id: uuid.UUID,
        next_attempt_at: datetime,
        dispatch_reason: str,
    ) -> DispatchOutbox:
        publish_retry_policy_snapshot = {
            "max_publish_attempts": settings.job.dispatch_max_publish_attempts,
            "orphan_timeout_seconds": settings.job.orphan_timeout_seconds,
            "publish_retry_delay_seconds": 5,
            "backoff_kind": "fixed",
        }
        outbox = DispatchOutbox(
            event_id=f"job_attempt:{attempt_id}:dispatch",
            attempt_id=attempt_id,
            task_name=DISPATCH_TASK_NAME,
            payload={"attempt_id": str(attempt_id)},
            status="pending",
            max_publish_attempts=settings.job.dispatch_max_publish_attempts,
            orphan_timeout_seconds=settings.job.orphan_timeout_seconds,
            publish_retry_delay_seconds=5,
            publish_backoff_kind="fixed",
            publish_retry_policy_snapshot=publish_retry_policy_snapshot,
            next_attempt_at=next_attempt_at,
        )
        db.add(outbox)
        db.add(
            JobEvent(
                job_id=event_job_id,
                attempt_id=attempt_id,
                event_type="dispatch.created",
                to_status="pending",
                payload={
                    "task_name": DISPATCH_TASK_NAME,
                    "dispatch_reason": dispatch_reason,
                    "publish_retry_policy": publish_retry_policy_snapshot,
                },
            )
        )
        await db.flush()
        return outbox

    @staticmethod
    async def create_initial_attempt(
        db: AsyncSession,
        job: Job,
        *,
        timeout_seconds: int,
        purpose: str,
        retry_policy: Any,
        created_reason: str = "initial",
    ) -> JobAttempt:
        attempt_id = uuid.uuid4()
        retry_snapshot = retry_policy.snapshot() if hasattr(retry_policy, "snapshot") else dict(retry_policy)
        attempt = JobAttempt(
            id=attempt_id,
            job_id=job.id,
            purpose=purpose,
            purpose_attempt_no=1,
            retry_chain_id=attempt_id,
            created_reason=created_reason,
            status="pending",
            timeout_seconds=timeout_seconds,
            policy_max_attempts=int(retry_snapshot["max_attempts"]),
            policy_retry_delay_seconds=retry_snapshot.get("retry_delay_seconds"),
            policy_backoff_kind=str(retry_snapshot["backoff_kind"]),
            policy_retryable_error_codes=list(retry_snapshot.get("retryable_error_codes") or []),
            retry_policy_snapshot=retry_snapshot,
        )
        db.add(attempt)
        job.active_attempt_id = attempt_id
        job.updated_at = datetime.now(timezone.utc)
        db.add(
            JobEvent(
                job_id=job.id,
                attempt_id=attempt_id,
                event_type="attempt.created",
                to_status="pending",
                payload={"purpose": purpose, "created_reason": created_reason, "retry_policy": retry_snapshot},
            )
        )
        await db.flush()
        await JobRepo.create_dispatch_outbox(
            db,
            event_job_id=job.id,
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
            .join(JobAttempt, JobAttempt.id == DispatchOutbox.attempt_id)
            .join(Job, Job.id == JobAttempt.job_id)
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
        dispatch, _job, attempt = row
        previous = dispatch.status
        lease_token = uuid.uuid4()
        dispatch.status = "leased"
        dispatch.lease_token = lease_token
        dispatch.lease_expires_at = now + timedelta(seconds=lease_seconds)
        dispatch.leased_at = now
        dispatch.updated_at = now
        db.add(
            JobEvent(
                job_id=attempt.job_id,
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
            select(DispatchOutbox, JobAttempt)
            .join(JobAttempt, JobAttempt.id == DispatchOutbox.attempt_id)
            .where(
                DispatchOutbox.id == dispatch_id,
                DispatchOutbox.status == "leased",
                DispatchOutbox.lease_token == lease_token,
            )
            .with_for_update(skip_locked=True)
        )
        row = result.one_or_none()
        if row is None:
            return False
        dispatch, attempt = row
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
                job_id=attempt.job_id,
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
            select(DispatchOutbox, JobAttempt)
            .join(JobAttempt, JobAttempt.id == DispatchOutbox.attempt_id)
            .where(
                DispatchOutbox.id == dispatch_id,
                DispatchOutbox.status == "leased",
                DispatchOutbox.lease_token == lease_token,
            )
            .with_for_update(skip_locked=True)
        )
        row = result.one_or_none()
        if row is None:
            return False
        dispatch, attempt = row
        previous = dispatch.status
        publish_attempts = (dispatch.publish_attempts or 0) + 1
        dispatch.publish_attempts = publish_attempts
        dispatch.lease_token = None
        dispatch.lease_expires_at = None
        dispatch.last_error = error
        dispatch.updated_at = now
        if publish_attempts >= min(max_publish_attempts, dispatch.max_publish_attempts):
            dispatch.status = "dead_letter"
            dispatch.next_attempt_at = None
            dispatch.dead_lettered_at = now
        else:
            dispatch.status = "retrying"
            dispatch.next_attempt_at = next_attempt_at
        db.add(
            JobEvent(
                job_id=attempt.job_id,
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
    async def find_dead_lettered_pending_dispatches(db: AsyncSession, *, limit: int) -> list[DispatchOutbox]:
        result = await db.execute(
            select(DispatchOutbox)
            .join(JobAttempt, JobAttempt.id == DispatchOutbox.attempt_id)
            .join(Job, Job.id == JobAttempt.job_id)
            .where(
                Job.status == "queued",
                Job.active_attempt_id == JobAttempt.id,
                Job.deleted_at.is_(None),
                JobAttempt.status == "pending",
                DispatchOutbox.task_name == DISPATCH_TASK_NAME,
                DispatchOutbox.status == "dead_letter",
            )
            .order_by(DispatchOutbox.dead_lettered_at.asc().nullsfirst(), DispatchOutbox.updated_at.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    @staticmethod
    async def mark_dead_lettered_dispatch_attempt_failed(
        db: AsyncSession,
        dispatch_id: uuid.UUID,
        *,
        error: dict[str, Any],
    ) -> Job | None:
        result = await db.execute(
            select(Job, JobAttempt, DispatchOutbox)
            .join(JobAttempt, JobAttempt.job_id == Job.id)
            .join(DispatchOutbox, DispatchOutbox.attempt_id == JobAttempt.id)
            .where(
                DispatchOutbox.id == dispatch_id,
                DispatchOutbox.task_name == DISPATCH_TASK_NAME,
                DispatchOutbox.status == "dead_letter",
                Job.status == "queued",
                Job.active_attempt_id == JobAttempt.id,
                Job.deleted_at.is_(None),
                JobAttempt.status == "pending",
            )
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        row = result.one_or_none()
        if row is None:
            return None
        job, attempt, dispatch = row
        now = datetime.now(timezone.utc)

        attempt.status = "failed"
        attempt.finished_at = now
        attempt.error = error
        attempt.error_kind = "dispatch_error"
        attempt.failure_phase = "dispatch"
        attempt.retry_eligible = False
        attempt.retry_decision = "do_not_retry"
        attempt.retry_decision_reason = "dispatch_publish_exhausted"
        attempt.retry_decided_at = now
        attempt.decision_source = "repository"
        attempt.lease_token = None
        attempt.lease_expires_at = None
        attempt.updated_at = now

        job.active_attempt_id = None
        job.status = "failed"
        job.progress_text = "任务发布失败"
        job.progress_stage = "failed"
        job.result = None
        job.error = error
        job.finished_at = now
        job.updated_at = now

        await JobRepo.ensure_terminal_callback_outbox(db, job, now=now)
        db.add(
            JobEvent(
                job_id=job.id,
                attempt_id=attempt.id,
                event_type="attempt.failed",
                from_status="pending",
                to_status="failed",
                payload={
                    **error,
                    "retry_eligible": False,
                    "retry_decision": "do_not_retry",
                    "retry_decision_reason": "dispatch_publish_exhausted",
                    "dispatch_id": str(dispatch.id),
                },
            )
        )
        await db.flush()
        return job

    @staticmethod
    async def get_dead_lettered_dispatch_replay_candidate(
        db: AsyncSession,
        job_id: uuid.UUID,
    ) -> tuple[Job, JobAttempt, DispatchOutbox] | None:
        result = await db.execute(
            select(Job, JobAttempt, DispatchOutbox)
            .join(JobAttempt, JobAttempt.job_id == Job.id)
            .join(DispatchOutbox, DispatchOutbox.attempt_id == JobAttempt.id)
            .where(
                Job.id == job_id,
                Job.status == "queued",
                Job.active_attempt_id == JobAttempt.id,
                Job.deleted_at.is_(None),
                JobAttempt.status == "pending",
                DispatchOutbox.task_name == DISPATCH_TASK_NAME,
                DispatchOutbox.status == "dead_letter",
            )
            .limit(1)
        )
        return result.one_or_none()

    @staticmethod
    async def replay_dead_lettered_dispatch(
        db: AsyncSession,
        job_id: uuid.UUID,
        *,
        reason: str,
        operator: str | None,
    ) -> tuple[Job, JobAttempt, DispatchOutbox] | None:
        result = await db.execute(
            select(Job, JobAttempt, DispatchOutbox)
            .join(JobAttempt, JobAttempt.job_id == Job.id)
            .join(DispatchOutbox, DispatchOutbox.attempt_id == JobAttempt.id)
            .where(
                Job.id == job_id,
                Job.status == "queued",
                Job.active_attempt_id == JobAttempt.id,
                Job.deleted_at.is_(None),
                JobAttempt.status == "pending",
                DispatchOutbox.task_name == DISPATCH_TASK_NAME,
                DispatchOutbox.status == "dead_letter",
            )
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        row = result.one_or_none()
        if row is None:
            return None
        job, attempt, dispatch = row
        now = datetime.now(timezone.utc)
        previous_status = dispatch.status
        previous_error = dispatch.last_error
        previous_publish_attempts = dispatch.publish_attempts or 0

        dispatch.status = "retrying"
        dispatch.publish_attempts = 0
        dispatch.next_attempt_at = now
        dispatch.lease_token = None
        dispatch.lease_expires_at = None
        dispatch.leased_at = None
        dispatch.published_at = None
        dispatch.dead_lettered_at = None
        dispatch.last_error = None
        dispatch.updated_at = now
        job.updated_at = now

        db.add(
            JobEvent(
                job_id=job.id,
                attempt_id=attempt.id,
                event_type="dispatch.replayed",
                from_status=previous_status,
                to_status="retrying",
                reason=reason,
                payload={
                    "dispatch_id": str(dispatch.id),
                    "operator": operator,
                    "previous_error": previous_error,
                    "previous_publish_attempts": previous_publish_attempts,
                },
            )
        )
        await db.flush()
        return job, attempt, dispatch

    @staticmethod
    async def claim_attempt_for_execution(
        db: AsyncSession,
        attempt_id: uuid.UUID,
        *,
        worker_id: str,
        lease_seconds: int,
    ) -> tuple[Job, JobAttempt, uuid.UUID] | None:
        job_result = await db.execute(
            select(Job)
            .where(
                Job.active_attempt_id == attempt_id,
                Job.status == "queued",
                Job.deleted_at.is_(None),
            )
            .with_for_update(skip_locked=True)
        )
        job = job_result.scalar_one_or_none()
        if job is None:
            return None

        attempt_result = await db.execute(
            select(JobAttempt)
            .where(
                JobAttempt.id == attempt_id,
                JobAttempt.job_id == job.id,
                JobAttempt.status == "pending",
            )
            .with_for_update(skip_locked=True)
        )
        attempt = attempt_result.scalar_one_or_none()
        if attempt is None:
            return None

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
        job.updated_at = now
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
        job.updated_at = now
        await db.flush()
        return True

    @staticmethod
    def _next_retry_scheduled_at(attempt: JobAttempt, now: datetime) -> datetime:
        delay_seconds = attempt.policy_retry_delay_seconds
        if attempt.policy_backoff_kind == "none":
            return now
        if delay_seconds is None:
            raise ValueError(f"retry backoff kind {attempt.policy_backoff_kind} requires retry delay seconds")
        if attempt.policy_backoff_kind == "fixed":
            return now + timedelta(seconds=delay_seconds)
        if attempt.policy_backoff_kind == "exponential":
            multiplier = 2 ** max(attempt.purpose_attempt_no - 1, 0)
            return now + timedelta(seconds=delay_seconds * multiplier)
        raise ValueError(f"unsupported retry backoff kind: {attempt.policy_backoff_kind}")

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
        retry_created_reason: str = "retry",
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
        can_retry = retryable and job.status != "failed" and attempt.purpose_attempt_no < attempt.policy_max_attempts
        attempt.retry_eligible = retryable
        attempt.retry_decision = "retry" if can_retry else "do_not_retry"
        if not retryable:
            attempt.retry_decision_reason = "not_retry_eligible"
        elif attempt.purpose_attempt_no >= attempt.policy_max_attempts:
            attempt.retry_decision_reason = "policy_exhausted"
        else:
            attempt.retry_decision_reason = "policy_allows_retry"
        attempt.retry_decided_at = now
        scheduled_next_attempt_at = None
        if can_retry:
            scheduled_next_attempt_at = (
                next_attempt_at if next_attempt_at is not None else JobRepo._next_retry_scheduled_at(attempt, now)
            )
        attempt.next_attempt_scheduled_at = scheduled_next_attempt_at
        attempt.decision_source = "repository"
        attempt.lease_token = None
        attempt.lease_expires_at = None
        attempt.updated_at = now
        if can_retry:
            next_attempt_id = uuid.uuid4()
            next_attempt_no = attempt.purpose_attempt_no + 1
            next_attempt = JobAttempt(
                id=next_attempt_id,
                job_id=job.id,
                purpose=attempt.purpose,
                purpose_attempt_no=next_attempt_no,
                retry_chain_id=attempt.retry_chain_id,
                previous_attempt_id=attempt.id,
                created_reason=retry_created_reason,
                status="pending",
                timeout_seconds=attempt.timeout_seconds,
                policy_max_attempts=attempt.policy_max_attempts,
                policy_retry_delay_seconds=attempt.policy_retry_delay_seconds,
                policy_backoff_kind=attempt.policy_backoff_kind,
                policy_retryable_error_codes=attempt.policy_retryable_error_codes,
                retry_policy_snapshot=attempt.retry_policy_snapshot,
            )
            db.add(next_attempt)
            job.active_attempt_id = next_attempt_id
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
                        "purpose": attempt.purpose,
                        "reason": retry_created_reason,
                        "previous_attempt_id": str(attempt.id),
                        "previous_error": error,
                        "retry_policy": attempt.retry_policy_snapshot,
                    },
                )
            )
            await JobRepo.create_dispatch_outbox(
                db,
                event_job_id=job.id,
                attempt_id=next_attempt_id,
                next_attempt_at=scheduled_next_attempt_at,
                dispatch_reason=retry_created_reason,
            )
        elif job.status != "failed":
            job.active_attempt_id = None
            job.status = "failed"
            job.progress_text = "处理失败"
            job.progress_stage = "failed"
            job.result = None
            job.error = error
            job.finished_at = now
            await JobRepo.ensure_terminal_callback_outbox(db, job, now=now)
        job.updated_at = now
        db.add(
            JobEvent(
                job_id=job.id,
                attempt_id=attempt.id,
                event_type="attempt.failed",
                from_status="running",
                to_status="failed",
                payload={
                    **error,
                    "retry_eligible": retryable,
                    "retry_decision": attempt.retry_decision,
                    "retry_decision_reason": attempt.retry_decision_reason,
                },
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
        job.progress_percent = max(job.progress_percent or 0, 20)
        job.progress_text = "等待子任务完成"
        job.progress_stage = "planning"
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
            .join(JobAttempt, JobAttempt.id == DispatchOutbox.attempt_id)
            .join(Job, Job.id == JobAttempt.job_id)
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
                Job.root_job_id.is_(None),
                Job.workflow_node_key.is_(None),
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
                Job.root_job_id.is_(None),
                Job.workflow_node_key.is_(None),
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
                Job.root_job_id.is_(None),
                Job.workflow_node_key.is_(None),
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
            Job.workflow_node_key.is_not(None),
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
                Job.root_job_id.is_(None),
                Job.workflow_node_key.is_(None),
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
        attempt_id: uuid.UUID | None = None,
        lease_token: uuid.UUID | None = None,
    ) -> bool:
        conditions = [Job.id == job_id, Job.deleted_at.is_(None)]
        if attempt_id is not None:
            conditions.extend(
                [
                    Job.status == "running",
                    Job.active_attempt_id == JobAttempt.id,
                    JobAttempt.id == attempt_id,
                    JobAttempt.status == "running",
                ]
            )
            if lease_token is not None:
                conditions.append(JobAttempt.lease_token == lease_token)
            result = await db.execute(
                select(Job, JobAttempt)
                .join(JobAttempt, JobAttempt.job_id == Job.id)
                .where(*conditions)
                .with_for_update(skip_locked=True)
            )
            row = result.one_or_none()
            if row is None:
                return False
            job, attempt = row
        else:
            result = await db.execute(select(Job).where(*conditions).with_for_update(skip_locked=True))
            job = result.scalar_one_or_none()
            if not job:
                return False
            attempt = None
        if not job:
            return False
        now = datetime.now(timezone.utc)
        job.progress_percent = max(0, min(100, progress_percent))
        job.progress_text = progress_text
        if progress_stage is not None:
            job.progress_stage = progress_stage
        if attempt is not None:
            attempt.heartbeat_at = now
            attempt.updated_at = now
        job.updated_at = now
        await db.flush()
        return True

    @staticmethod
    async def mark_succeeded(
        db: AsyncSession,
        job_id: uuid.UUID,
        *,
        attempt_id: uuid.UUID,
        lease_token: uuid.UUID,
        result: dict[str, Any] | None,
        canonical_result: dict[str, Any] | None = None,
    ) -> bool:
        query_result = await db.execute(
            select(Job, JobAttempt)
            .join(JobAttempt, JobAttempt.job_id == Job.id)
            .where(
                Job.id == job_id,
                Job.status == "running",
                Job.active_attempt_id == JobAttempt.id,
                JobAttempt.id == attempt_id,
                JobAttempt.status == "running",
                JobAttempt.lease_token == lease_token,
                Job.deleted_at.is_(None),
            )
            .with_for_update(skip_locked=True)
        )
        row = query_result.one_or_none()
        if row is None:
            return False
        job, attempt = row
        now = datetime.now(timezone.utc)
        job.status = "succeeded"
        job.active_attempt_id = None
        job.progress_percent = 100
        job.progress_text = "已完成"
        job.progress_stage = "succeeded"
        job.result = result
        job.canonical_result = canonical_result
        job.error = None
        job.finished_at = now
        job.updated_at = now
        attempt.status = "succeeded"
        attempt.finished_at = now
        attempt.lease_token = None
        attempt.lease_expires_at = None
        attempt.heartbeat_at = now
        attempt.updated_at = now
        await JobRepo.ensure_terminal_callback_outbox(db, job, now=now)
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
    async def mark_failed(
        db: AsyncSession,
        job_id: uuid.UUID,
        error: dict[str, Any],
        *,
        attempt_id: uuid.UUID | None = None,
        lease_token: uuid.UUID | None = None,
    ) -> bool:
        conditions = [Job.id == job_id, Job.status.in_(["queued", "running"]), Job.deleted_at.is_(None)]
        if attempt_id is not None:
            conditions.extend(
                [
                    Job.active_attempt_id == JobAttempt.id,
                    JobAttempt.id == attempt_id,
                    JobAttempt.status.in_(["pending", "running"]),
                ]
            )
            if lease_token is not None:
                conditions.append(JobAttempt.lease_token == lease_token)
            result = await db.execute(
                select(Job, JobAttempt)
                .join(JobAttempt, JobAttempt.job_id == Job.id)
                .where(*conditions)
                .with_for_update(skip_locked=True)
            )
            row = result.one_or_none()
            if row is None:
                return False
            job, attempt = row
        else:
            result = await db.execute(select(Job).where(*conditions).with_for_update(skip_locked=True))
            job = result.scalar_one_or_none()
            if not job:
                return False
            attempt = None
        if not job:
            return False
        now = datetime.now(timezone.utc)
        job.status = "failed"
        job.active_attempt_id = None
        job.progress_text = "处理失败"
        job.progress_stage = "failed"
        job.result = None
        job.error = error
        job.finished_at = now
        job.updated_at = now
        if attempt is not None:
            attempt.status = "failed"
            attempt.finished_at = now
            attempt.error = error
            attempt.retry_eligible = False
            attempt.retry_decision = "do_not_retry"
            attempt.retry_decision_reason = "force_mark_failed"
            attempt.retry_decided_at = now
            attempt.decision_source = "repository"
            attempt.lease_token = None
            attempt.lease_expires_at = None
            attempt.updated_at = now
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
                Job.root_job_id.is_(None),
                Job.workflow_node_key.is_(None),
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
        job.error = None
        job.finished_at = now
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
                Job.root_job_id.is_(None),
                Job.workflow_node_key.is_(None),
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
        job.error = error
        job.finished_at = now
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
                CallbackOutbox.delivery_attempts < CallbackOutbox.max_delivery_attempts,
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
        last_http_status: int | None = None,
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
        attempted_count = max(0, delivery_attempts)
        current_delivery_attempts = outbox.delivery_attempts or 0
        max_allowed_attempts = min(max_attempts, outbox.max_delivery_attempts)
        if (
            status == "failed"
            and current_delivery_attempts + attempted_count < max_allowed_attempts
            and next_retry_at is None
        ):
            raise ValueError("callback retrying status requires next_retry_at")

        now = datetime.now(timezone.utc)
        previous_status = outbox.status
        if attempted_count:
            outbox.delivery_attempts = current_delivery_attempts + attempted_count
            outbox.first_attempt_at = outbox.first_attempt_at or now
            outbox.last_attempt_at = now
        outbox.lease_token = None
        outbox.lease_expires_at = None
        outbox.last_http_status = last_http_status
        outbox.last_error = last_error
        outbox.last_response = last_response
        outbox.updated_at = now
        if status == "delivered":
            outbox.status = "delivered"
            outbox.next_attempt_at = None
            outbox.delivered_at = now
        elif status == "skipped":
            outbox.status = "skipped"
            outbox.next_attempt_at = None
        elif status == "failed" and (outbox.delivery_attempts or 0) >= max_allowed_attempts:
            outbox.status = "dead_letter"
            outbox.next_attempt_at = None
            outbox.dead_lettered_at = now
        elif status == "failed":
            outbox.status = "retrying"
            outbox.next_attempt_at = next_retry_at
        else:
            raise ValueError(f"unsupported callback result status: {status}")

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
                CallbackOutbox.delivery_attempts < CallbackOutbox.max_delivery_attempts,
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
    async def soft_delete_root_family(
        db: AsyncSession,
        root_job_id: uuid.UUID,
        *,
        reason: str,
        now: datetime | None = None,
    ) -> int:
        """Soft-delete a settled public root Job and its internal child Jobs."""
        now = now or datetime.now(timezone.utc)
        async with db.begin_nested():
            settled_callback_exists = (
                select(CallbackOutbox.id)
                .where(
                    CallbackOutbox.job_id == Job.id,
                    or_(
                        and_(Job.status == "succeeded", CallbackOutbox.event_type == "job.succeeded"),
                        and_(Job.status == "failed", CallbackOutbox.event_type == "job.failed"),
                    ),
                    CallbackOutbox.status.in_(["delivered", "skipped", "dead_letter"]),
                )
                .exists()
            )
            active_submission_key_exists = (
                select(JobSubmissionKey.id)
                .where(
                    JobSubmissionKey.job_id == root_job_id,
                    JobSubmissionKey.deleted_at.is_(None),
                )
                .exists()
            )
            unsettled_child_exists = (
                select(Job.id)
                .where(
                    Job.root_job_id == root_job_id,
                    Job.deleted_at.is_(None),
                    or_(
                        Job.status.not_in(["succeeded", "failed"]),
                        Job.active_attempt_id.is_not(None),
                    ),
                )
                .exists()
            )
            root_result = await db.execute(
                select(Job)
                .where(
                    Job.id == root_job_id,
                    Job.root_job_id.is_(None),
                    Job.workflow_node_key.is_(None),
                    Job.client_request_id.is_not(None),
                    Job.deleted_at.is_(None),
                    Job.status.in_(["succeeded", "failed"]),
                    Job.active_attempt_id.is_(None),
                    or_(Job.callback_url.is_(None), settled_callback_exists),
                    active_submission_key_exists,
                    ~unsettled_child_exists,
                )
                .with_for_update(skip_locked=True)
            )
            root = root_result.scalar_one_or_none()
            if root is None:
                return 0

            key_result = await db.execute(
                update(JobSubmissionKey)
                .where(
                    JobSubmissionKey.job_id == root_job_id,
                    JobSubmissionKey.deleted_at.is_(None),
                )
                .values(deleted_at=now, deleted_reason=reason)
            )
            if key_result.rowcount != 1:
                raise ValueError("cannot soft-delete root job family: active submission key is missing")

            family_result = await db.execute(
                update(Job)
                .where(
                    or_(Job.id == root_job_id, Job.root_job_id == root_job_id),
                    Job.deleted_at.is_(None),
                )
                .values(
                    delete_requested_at=now,
                    deleted_at=now,
                    deleted_reason=reason,
                    updated_at=now,
                )
            )
            if family_result.rowcount < 1:
                raise ValueError("cannot soft-delete root job family: family update affected no jobs")
            await db.flush()
            return family_result.rowcount

    @staticmethod
    async def restore_root_family(db: AsyncSession, root_job_id: uuid.UUID) -> int:
        """Restore a soft-deleted public root Job family if its submission key is free."""
        async with db.begin_nested():
            root_result = await db.execute(
                select(Job)
                .where(
                    Job.id == root_job_id,
                    Job.root_job_id.is_(None),
                    Job.workflow_node_key.is_(None),
                    Job.client_request_id.is_not(None),
                    Job.deleted_at.is_not(None),
                )
                .with_for_update(skip_locked=True)
            )
            root = root_result.scalar_one_or_none()
            if root is None:
                return 0

            active_family_member_result = await db.execute(
                select(Job.id)
                .where(
                    or_(Job.id == root_job_id, Job.root_job_id == root_job_id),
                    Job.deleted_at.is_(None),
                )
                .limit(1)
            )
            if active_family_member_result.scalar_one_or_none() is not None:
                raise ValueError("cannot restore root job family: family is only partially soft-deleted")

            deleted_keys_result = await db.execute(
                select(JobSubmissionKey)
                .where(
                    JobSubmissionKey.job_id == root_job_id,
                    JobSubmissionKey.deleted_at.is_not(None),
                )
                .with_for_update(skip_locked=True)
            )
            deleted_keys = list(deleted_keys_result.scalars().all())
            if not deleted_keys:
                raise ValueError("cannot restore root job family: deleted submission key is missing")
            for key in deleted_keys:
                if key.key_kind == SUBMISSION_KEY_KIND_CLIENT_REQUEST_ID:
                    await JobRepo.advisory_lock_for_client_request(db, key.caller_id, key.key_value)
                conflict_result = await db.execute(
                    select(JobSubmissionKey.id)
                    .where(
                        JobSubmissionKey.caller_id == key.caller_id,
                        JobSubmissionKey.key_kind == key.key_kind,
                        JobSubmissionKey.key_value == key.key_value,
                        JobSubmissionKey.deleted_at.is_(None),
                        JobSubmissionKey.id != key.id,
                    )
                    .limit(1)
                )
                if conflict_result.scalar_one_or_none() is not None:
                    raise ValueError(
                        "cannot restore root job family: submission key is already used by an active job"
                    )

            family_result = await db.execute(
                update(Job)
                .where(
                    or_(Job.id == root_job_id, Job.root_job_id == root_job_id),
                    Job.deleted_at.is_not(None),
                )
                .values(
                    delete_requested_at=None,
                    deleted_at=None,
                    deleted_reason=None,
                    updated_at=datetime.now(timezone.utc),
                )
            )
            if family_result.rowcount < 1:
                raise ValueError("cannot restore root job family: family update affected no jobs")
            key_result = await db.execute(
                update(JobSubmissionKey)
                .where(JobSubmissionKey.job_id == root_job_id, JobSubmissionKey.deleted_at.is_not(None))
                .values(deleted_at=None, deleted_reason=None)
            )
            if key_result.rowcount != len(deleted_keys):
                raise ValueError("cannot restore root job family: deleted submission key restore count mismatch")
            await db.flush()
            return family_result.rowcount

    @staticmethod
    async def cleanup_expired_jobs(db: AsyncSession) -> int:
        """软删除过期且生命周期已收敛的 root Job family（expires_at <= now）。"""
        async with db.begin_nested():
            settled_callback_exists = (
                select(CallbackOutbox.id)
                .where(
                    CallbackOutbox.job_id == Job.id,
                    or_(
                        and_(Job.status == "succeeded", CallbackOutbox.event_type == "job.succeeded"),
                        and_(Job.status == "failed", CallbackOutbox.event_type == "job.failed"),
                    ),
                    CallbackOutbox.status.in_(["delivered", "skipped", "dead_letter"]),
                )
                .exists()
            )
            root = Job
            child = Job.__table__.alias("child")
            unsettled_child_exists = (
                select(child.c.id)
                .where(
                    child.c.root_job_id == root.id,
                    child.c.deleted_at.is_(None),
                    or_(
                        child.c.status.not_in(["succeeded", "failed"]),
                        child.c.active_attempt_id.is_not(None),
                    ),
                )
                .exists()
            )
            active_submission_key_exists = (
                select(JobSubmissionKey.id)
                .where(
                    JobSubmissionKey.job_id == root.id,
                    JobSubmissionKey.deleted_at.is_(None),
                )
                .exists()
            )
            expired_roots_result = await db.execute(
                select(root.id)
                .where(
                    root.expires_at <= func.now(),
                    root.deleted_at.is_(None),
                    root.root_job_id.is_(None),
                    root.workflow_node_key.is_(None),
                    root.client_request_id.is_not(None),
                    root.status.in_(["succeeded", "failed"]),
                    root.active_attempt_id.is_(None),
                    or_(root.callback_url.is_(None), settled_callback_exists),
                    active_submission_key_exists,
                    ~unsettled_child_exists,
                )
                .with_for_update(skip_locked=True)
            )
            expired_root_ids = list(expired_roots_result.scalars().all())
            if not expired_root_ids:
                return 0

            key_result = await db.execute(
                update(JobSubmissionKey)
                .where(
                    JobSubmissionKey.job_id.in_(expired_root_ids),
                    JobSubmissionKey.deleted_at.is_(None),
                )
                .values(deleted_at=func.now(), deleted_reason="expired")
            )
            if key_result.rowcount != len(expired_root_ids):
                raise ValueError("cannot cleanup expired jobs: active submission key count mismatch")

            result = await db.execute(
                update(Job)
                .where(
                    Job.deleted_at.is_(None),
                    or_(Job.id.in_(expired_root_ids), Job.root_job_id.in_(expired_root_ids)),
                )
                .values(
                    delete_requested_at=func.now(),
                    deleted_at=func.now(),
                    deleted_reason="expired",
                    updated_at=func.now(),
                )
            )
            if result.rowcount < len(expired_root_ids):
                raise ValueError("cannot cleanup expired jobs: family update affected fewer rows than roots")
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
        now = datetime.now(timezone.utc)
        await db.execute(
            update(CallbackOutbox)
            .where(
                CallbackOutbox.job_id == job_id,
                CallbackOutbox.status.in_(["pending", "retrying", "leased"]),
            )
            .values(
                status="delivered",
                next_attempt_at=None,
                lease_token=None,
                lease_expires_at=None,
                delivered_at=now,
                updated_at=now,
            )
        )
        await db.flush()
