import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Numeric, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AiCallLog(Base):
    __tablename__ = "ai_call_logs"
    __table_args__ = (
        Index("ix_ai_call_logs_scope_created", "scope_type", "scope_id", "created_at"),
        Index("ix_ai_call_logs_caller_created", "caller_id", "created_at"),
        Index("ix_ai_call_logs_operation_created", "operation", "created_at"),
        Index("ix_ai_call_logs_job_created", "job_id", "created_at"),
        Index("ix_ai_call_logs_attempt_created", "attempt_id", "created_at"),
        Index("ix_ai_call_logs_model_created", "model_id", "created_at"),
        Index("ix_ai_call_logs_provider_model_created", "provider", "provider_model", "created_at"),
        Index("ix_ai_call_logs_status_created", "status", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    caller_id: Mapped[str] = mapped_column(String(64), nullable=False)
    scope_type: Mapped[str] = mapped_column(String(32), nullable=False)
    scope_id: Mapped[str] = mapped_column(String(128), nullable=False)
    operation: Mapped[str] = mapped_column(String(128), nullable=False)
    step_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("jobs.id"), nullable=True)
    attempt_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("job_attempts.id"), nullable=True)
    job_type: Mapped[str | None] = mapped_column(String(96), nullable=True)

    model_id: Mapped[str] = mapped_column(String(128), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_model: Mapped[str] = mapped_column(String(255), nullable=False)
    litellm_model: Mapped[str | None] = mapped_column(String(255), nullable=True)

    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    failure_phase: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(96), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(512), nullable=True)

    request_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    response_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    input_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)

    usage_detail: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    usage_units: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    cost_amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    pricing_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    pricing_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    cost_calculation_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    billable_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
