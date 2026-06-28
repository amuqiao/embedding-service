import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Job(Base):
    __tablename__ = "job_aggregates"
    __table_args__ = (
        CheckConstraint("status IN ('queued', 'running', 'succeeded', 'failed')", name="ck_job_aggregates_status"),
        CheckConstraint("priority IN ('low', 'normal')", name="ck_job_aggregates_priority"),
        CheckConstraint(
            "progress_percent >= 0 AND progress_percent <= 100",
            name="ck_job_aggregates_progress_percent_range",
        ),
        CheckConstraint(
            """
            (
                root_job_id IS NULL
                AND workflow_node_key IS NULL
                AND client_request_id IS NOT NULL
            )
            OR
            (
                root_job_id IS NOT NULL
                AND workflow_node_key IS NOT NULL
                AND client_request_id IS NULL
            )
            """,
            name="ck_job_aggregates_root_child_shape",
        ),
        CheckConstraint(
            "root_job_id IS NULL OR (callback_url IS NULL AND callback_events IS NULL)",
            name="ck_job_aggregates_child_no_callback",
        ),
        CheckConstraint(
            "status NOT IN ('succeeded', 'failed') OR active_attempt_id IS NULL",
            name="ck_job_aggregates_terminal_no_active_attempt",
        ),
        Index("ix_job_aggregates_root_job_id", "root_job_id"),
        Index("ix_job_aggregates_root_status", "root_job_id", "status"),
        Index(
            "uq_job_aggregates_root_workflow_node_key",
            "root_job_id",
            "workflow_node_key",
            unique=True,
            postgresql_where=text("workflow_node_key IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    root_job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "job_aggregates.id",
            name="fk_job_aggregates_root_job_id_job_aggregates",
            use_alter=True,
        ),
        nullable=True,
    )
    workflow_node_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    caller_id: Mapped[str] = mapped_column(String(64), nullable=False, default="default")
    client_request_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    job_type: Mapped[str] = mapped_column(String(96), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued", index=True)
    progress_percent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    progress_text: Mapped[str | None] = mapped_column(String(255), nullable=True)
    progress_stage: Mapped[str | None] = mapped_column(String(64), nullable=True)
    queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    priority: Mapped[str] = mapped_column(String(16), nullable=False, default="normal")
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    job_params_ref: Mapped[dict] = mapped_column(JSONB, nullable=False)
    job_params_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    runtime_ref: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    callback_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    callback_events: Mapped[list | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    canonical_result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    active_attempt_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "job_execution_attempts.id",
            name="fk_job_aggregates_active_attempt_id_job_execution_attempts",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delete_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    deleted_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc) + timedelta(hours=24),
        index=True,
    )


class JobSubmissionKey(Base):
    __tablename__ = "job_submission_keys"
    __table_args__ = (
        UniqueConstraint("caller_id", "key_kind", "key_value", name="uq_job_submission_keys_caller_kind_value"),
        Index("ix_job_submission_keys_job_id", "job_id"),
        Index("ix_job_submission_keys_expires_at", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    caller_id: Mapped[str] = mapped_column(String(64), nullable=False)
    key_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    key_value: Mapped[str] = mapped_column(String(255), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("job_aggregates.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


class JobAttempt(Base):
    __tablename__ = "job_execution_attempts"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed')",
            name="ck_job_execution_attempts_status",
        ),
        CheckConstraint(
            "purpose IN ('workflow_orchestration', 'business_execution')",
            name="ck_job_execution_attempts_purpose",
        ),
        CheckConstraint("purpose_attempt_no >= 1", name="ck_job_execution_attempts_purpose_attempt_no_positive"),
        CheckConstraint("policy_max_attempts >= 1", name="ck_job_execution_attempts_policy_max_attempts_positive"),
        CheckConstraint(
            "policy_retry_delay_seconds IS NULL OR policy_retry_delay_seconds >= 0",
            name="ck_job_execution_attempts_policy_retry_delay_non_negative",
        ),
        CheckConstraint(
            "policy_backoff_kind IN ('none', 'fixed', 'exponential')",
            name="ck_job_execution_attempts_policy_backoff_kind",
        ),
        CheckConstraint(
            "retry_decision IS NULL OR retry_decision IN ('not_decided', 'retry', 'do_not_retry')",
            name="ck_job_execution_attempts_retry_decision",
        ),
        UniqueConstraint("job_id", "purpose", "purpose_attempt_no", name="uq_job_execution_attempts_job_purpose_no"),
        Index("ix_job_execution_attempts_running_lease", "status", "lease_expires_at"),
        Index("ix_job_execution_attempts_retry_chain_id", "retry_chain_id"),
        Index(
            "uq_job_execution_attempts_previous_attempt_id",
            "previous_attempt_id",
            unique=True,
            postgresql_where=text("previous_attempt_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("job_aggregates.id"), nullable=False, index=True
    )
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    purpose_attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    retry_chain_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, default=uuid.uuid4)
    previous_attempt_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("job_execution_attempts.id"), nullable=True
    )
    created_reason: Mapped[str] = mapped_column(String(64), nullable=False, default="initial")
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending", index=True)
    worker_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    lease_token: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    leased_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    error: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    failure_phase: Mapped[str | None] = mapped_column(String(32), nullable=True)
    policy_max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    policy_retry_delay_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    policy_backoff_kind: Mapped[str] = mapped_column(String(32), nullable=False, default="none")
    policy_retryable_error_codes: Mapped[list | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    retry_policy_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    retry_eligible: Mapped[bool | None] = mapped_column(nullable=True)
    retry_decision: Mapped[str | None] = mapped_column(String(32), nullable=True)
    retry_decision_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    retry_decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_attempt_scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class DispatchOutbox(Base):
    __tablename__ = "dispatch_outbox"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'leased', 'published', 'retrying', 'dead_letter')",
            name="ck_dispatch_outbox_status",
        ),
        CheckConstraint("publish_attempts >= 0", name="ck_dispatch_outbox_publish_attempts_non_negative"),
        CheckConstraint("max_publish_attempts >= 1", name="ck_dispatch_outbox_max_publish_attempts_positive"),
        CheckConstraint("orphan_timeout_seconds >= 1", name="ck_dispatch_outbox_orphan_timeout_seconds_positive"),
        CheckConstraint(
            "publish_retry_delay_seconds >= 0",
            name="ck_dispatch_outbox_publish_retry_delay_seconds_non_negative",
        ),
        CheckConstraint(
            "publish_backoff_kind IN ('none', 'fixed', 'exponential')",
            name="ck_dispatch_outbox_publish_backoff_kind",
        ),
        UniqueConstraint("event_id", name="uq_dispatch_outbox_event_id"),
        UniqueConstraint("attempt_id", "task_name", name="uq_dispatch_outbox_attempt_task"),
        Index("ix_dispatch_outbox_attempt_id", "attempt_id"),
        Index("ix_dispatch_outbox_due", "status", "next_attempt_at", "created_at"),
        Index("ix_dispatch_outbox_lease", "status", "lease_expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    attempt_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("job_execution_attempts.id"), nullable=False, index=True
    )
    task_name: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending", index=True)
    publish_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_publish_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=12)
    orphan_timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=300)
    publish_retry_delay_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    publish_backoff_kind: Mapped[str] = mapped_column(String(32), nullable=False, default="fixed")
    publish_retry_policy_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    lease_token: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    last_error: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    leased_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dead_lettered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class CallbackOutbox(Base):
    __tablename__ = "callback_outbox"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'leased', 'delivered', 'retrying', 'skipped', 'dead_letter')",
            name="ck_callback_outbox_status",
        ),
        CheckConstraint("delivery_attempts >= 0", name="ck_callback_outbox_delivery_attempts_non_negative"),
        CheckConstraint("max_delivery_attempts >= 1", name="ck_callback_outbox_max_delivery_attempts_positive"),
        CheckConstraint("request_timeout_seconds >= 1", name="ck_callback_outbox_request_timeout_seconds_positive"),
        CheckConstraint("retry_delay_seconds >= 0", name="ck_callback_outbox_retry_delay_seconds_non_negative"),
        UniqueConstraint("job_id", "event_type", name="uq_callback_outbox_job_event_type"),
        UniqueConstraint("event_id", name="uq_callback_outbox_event_id"),
        Index("ix_callback_outbox_due", "status", "next_attempt_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("job_aggregates.id"), nullable=False, index=True
    )
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, default=uuid.uuid4)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    callback_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    signature_version: Mapped[str] = mapped_column(String(64), nullable=False, default="hmac-sha256:v1")
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending", index=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    delivery_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_delivery_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=6)
    request_timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    retry_delay_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    delivery_retry_policy_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    lease_token: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    last_http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_response: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    last_error: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    leased_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    first_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dead_lettered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class JobEvent(Base):
    __tablename__ = "job_audit_events"
    __table_args__ = (
        Index("ix_job_audit_events_job_created", "job_id", "created_at"),
        Index("ix_job_audit_events_attempt_created", "attempt_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("job_aggregates.id"), nullable=False, index=True
    )
    attempt_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("job_execution_attempts.id"), nullable=True, index=True
    )
    callback_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("callback_outbox.id"), nullable=True, index=True
    )
    event_type: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    from_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    to_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(128), nullable=True)
    payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
