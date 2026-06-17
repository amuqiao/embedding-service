import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AIJob(Base):
    __tablename__ = "ai_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    caller_id: Mapped[str] = mapped_column(String(64), nullable=False, default="default")
    client_request_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    request_fingerprint: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    job_type: Mapped[str] = mapped_column(String(96), nullable=False)
    model_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued", index=True)
    progress_percent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    progress_text: Mapped[str | None] = mapped_column(String(255), nullable=True)
    progress_stage: Mapped[str | None] = mapped_column(String(64), nullable=True)
    queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    priority: Mapped[str] = mapped_column(String(16), nullable=False, default="normal")
    timeout_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    public_metadata: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    options_payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    input_ref: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    runtime_ref: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    prompt_ref: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    output_oss_bucket: Mapped[str | None] = mapped_column(String(255), nullable=True)
    output_oss_prefix: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    output_oss_region: Mapped[str | None] = mapped_column(String(64), nullable=True)
    input_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    output_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    callback_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    callback_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    callback_events: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    prompt_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    execution_mode: Mapped[str | None] = mapped_column(String(24), nullable=True)
    execution_plan: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    public_result_payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    canonical_result_ref: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    result_payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error_payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    celery_task_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    celery_published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dispatch_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    first_published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    execution_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_execution_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_execution_error: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    callback_status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending", index=True)
    callback_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    callback_first_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    callback_last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    callback_next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    callback_delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    callback_failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    callback_last_error: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
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


class AIJobWorkItem(Base):
    __tablename__ = "ai_job_work_items"
    __table_args__ = (
        UniqueConstraint("job_id", "name", "chunk_index", name="uq_ai_job_work_items_job_name_chunk"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ai_jobs.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(96), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued", index=True)
    celery_task_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    input_ref: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    input_payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    result_ref: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    result_payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error_payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    deleted_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc) + timedelta(hours=24),
        index=True,
    )
