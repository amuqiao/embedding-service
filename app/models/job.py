import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database import Base


class AIJob(Base):
    __tablename__ = "ai_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    caller_id: Mapped[str] = mapped_column(String(64), nullable=False, default="default")
    client_request_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    job_type: Mapped[str] = mapped_column(String(96), nullable=False)
    model_id: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued", index=True)
    progress_percent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    progress_text: Mapped[str | None] = mapped_column(String(255), nullable=True)
    input_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    output_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    callback_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    prompt_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    execution_mode: Mapped[str | None] = mapped_column(String(24), nullable=True)
    execution_plan: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    result_payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error_payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    celery_task_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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
        UUID(as_uuid=True), ForeignKey("ai_jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(96), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued", index=True)
    celery_task_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    input_payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    result_payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error_payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc) + timedelta(hours=24),
        index=True,
    )
