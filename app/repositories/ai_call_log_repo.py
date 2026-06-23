import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_call_log import AiCallLog


def _duration_ms(started_at: datetime, completed_at: datetime) -> int:
    return max(0, int((completed_at - started_at).total_seconds() * 1000))


class AiCallLogRepo:
    @staticmethod
    async def create_pending(
        db: AsyncSession,
        *,
        caller_id: str,
        scope_type: str,
        scope_id: str,
        operation: str,
        model_id: str,
        provider: str,
        provider_model: str,
        litellm_model: str,
        pricing_ref: str,
        pricing_version: str,
        step_name: str | None = None,
        request_id: str | None = None,
        trace_id: str | None = None,
        job_id: uuid.UUID | None = None,
        attempt_id: uuid.UUID | None = None,
        job_type: str | None = None,
        request_hash: str | None = None,
        input_size_bytes: int | None = None,
        started_at: datetime | None = None,
    ) -> AiCallLog:
        now = started_at or datetime.now(timezone.utc)
        row = AiCallLog(
            caller_id=caller_id,
            scope_type=scope_type,
            scope_id=scope_id,
            operation=operation,
            step_name=step_name,
            request_id=request_id,
            trace_id=trace_id,
            job_id=job_id,
            attempt_id=attempt_id,
            job_type=job_type,
            model_id=model_id,
            provider=provider,
            provider_model=provider_model,
            litellm_model=litellm_model,
            status="pending",
            request_hash=request_hash,
            input_size_bytes=input_size_bytes,
            pricing_ref=pricing_ref,
            pricing_version=pricing_version,
            cost_calculation_status="pending",
            billable_status="pending",
            started_at=now,
        )
        db.add(row)
        await db.flush()
        await db.refresh(row)
        return row

    @staticmethod
    async def mark_succeeded(
        db: AsyncSession,
        call_id: uuid.UUID,
        *,
        usage_detail: dict[str, Any],
        usage_units: dict[str, int],
        cost_amount: Decimal,
        currency: str,
        response_hash: str | None,
        output_size_bytes: int | None,
    ) -> bool:
        result = await db.execute(
            select(AiCallLog).where(AiCallLog.id == call_id, AiCallLog.status == "pending").with_for_update(skip_locked=True)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return False
        completed_at = datetime.now(timezone.utc)
        row.status = "succeeded"
        row.usage_detail = usage_detail
        row.usage_units = usage_units
        row.cost_amount = cost_amount
        row.currency = currency
        row.response_hash = response_hash
        row.output_size_bytes = output_size_bytes
        row.cost_calculation_status = "estimated"
        row.billable_status = "billable"
        row.completed_at = completed_at
        row.duration_ms = _duration_ms(row.started_at, completed_at)
        row.updated_at = completed_at
        await db.flush()
        return True

    @staticmethod
    async def mark_failed(
        db: AsyncSession,
        call_id: uuid.UUID,
        *,
        failure_phase: str,
        error_code: str,
        error_message: str,
        billable_status: str,
        cost_calculation_status: str = "not_applicable",
    ) -> bool:
        result = await db.execute(
            select(AiCallLog).where(AiCallLog.id == call_id, AiCallLog.status == "pending").with_for_update(skip_locked=True)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return False
        completed_at = datetime.now(timezone.utc)
        row.status = "failed"
        row.failure_phase = failure_phase
        row.error_code = error_code[:96]
        row.error_message = error_message[:512]
        row.billable_status = billable_status
        row.cost_calculation_status = cost_calculation_status
        row.completed_at = completed_at
        row.duration_ms = _duration_ms(row.started_at, completed_at)
        row.updated_at = completed_at
        await db.flush()
        return True

    @staticmethod
    async def list_for_scope(
        db: AsyncSession,
        *,
        scope_type: str,
        scope_id: str,
        caller_id: str | None = None,
    ) -> list[AiCallLog]:
        conditions = [AiCallLog.scope_type == scope_type, AiCallLog.scope_id == scope_id]
        if caller_id is not None:
            conditions.append(AiCallLog.caller_id == caller_id)
        result = await db.execute(select(AiCallLog).where(*conditions).order_by(AiCallLog.created_at.asc()))
        return list(result.scalars().all())
