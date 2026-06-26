import uuid
from collections import defaultdict
from datetime import datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import AppError, NotFoundAppError
from app.core.pricing_registry import MONEY_QUANT, default_currency
from app.models.ai_call_log import AiCallLog
from app.repositories.ai_call_log_repo import AiCallLogRepo
from app.repositories.job_repo import JobRepo
from app.schemas.billing import BillingEnvelope
from app.schemas.jobs import JobCost

ZERO_AMOUNT = "0.00000000"


def _amount_text(value: Decimal) -> str:
    return str(value.quantize(MONEY_QUANT))


def _usage_units(rows: list[AiCallLog]) -> dict[str, int]:
    totals: defaultdict[str, int] = defaultdict(int)
    for row in rows:
        if not isinstance(row.usage_units, dict):
            continue
        for key, value in row.usage_units.items():
            if isinstance(key, str) and isinstance(value, int) and value >= 0:
                totals[key] += value
    return dict(sorted(totals.items()))


def _finalized_at(rows: list[AiCallLog]) -> datetime | None:
    completed = [row.completed_at for row in rows if row.completed_at is not None]
    return max(completed) if completed else None


def build_scope_billing_envelope(*, scope_type: str, scope_id: str, rows: list[AiCallLog]) -> BillingEnvelope:
    if not rows:
        return BillingEnvelope(
            scope_type=scope_type,
            scope_id=scope_id,
            status="not_billable",
            currency=default_currency(),
            total_cost_amount=ZERO_AMOUNT,
            usage_units={},
            pricing_refs=[],
            ai_call_count=0,
            billable_call_count=0,
            unbillable_call_count=0,
            failed_call_count=0,
            diagnostic_reason=None,
            finalized_at=None,
        )

    ai_call_count = len(rows)
    billable_call_count = sum(1 for row in rows if row.billable_status == "billable")
    unbillable_call_count = sum(1 for row in rows if row.billable_status == "not_billable")
    failed_call_count = sum(1 for row in rows if row.status == "failed")
    pricing_refs = sorted({row.pricing_ref for row in rows if row.pricing_ref})
    incomplete = any(row.status == "pending" or row.billable_status in {"pending", "unknown"} for row in rows)
    cost_failed = any(row.cost_calculation_status == "failed" for row in rows)

    billable_rows = [row for row in rows if row.billable_status == "billable"]
    currencies = sorted({row.currency for row in billable_rows if row.currency})

    status = "estimated"
    diagnostic_reason = None
    total = Decimal("0")
    currency: str | None = currencies[0] if len(currencies) == 1 else default_currency()

    if cost_failed:
        status = "failed"
        diagnostic_reason = "cost_calculation_failed"
    elif incomplete:
        status = "incomplete"
        diagnostic_reason = "contains_pending_or_unknown_ai_call"
    elif len(currencies) > 1:
        status = "failed"
        diagnostic_reason = "multiple_currencies"
        currency = None
    elif billable_rows and any(row.currency is None for row in billable_rows):
        status = "failed"
        diagnostic_reason = "billable_call_missing_currency"
        currency = None
    elif not billable_rows:
        status = "not_billable"
        currency = currencies[0] if currencies else default_currency()
    else:
        for row in billable_rows:
            if row.cost_amount is None:
                status = "failed"
                diagnostic_reason = "billable_call_missing_cost"
                total = Decimal("0")
                break
            total += Decimal(row.cost_amount)

    return BillingEnvelope(
        scope_type=scope_type,
        scope_id=scope_id,
        status=status,
        currency=currency,
        total_cost_amount=_amount_text(total),
        usage_units=_usage_units(rows),
        pricing_refs=pricing_refs,
        ai_call_count=ai_call_count,
        billable_call_count=billable_call_count,
        unbillable_call_count=unbillable_call_count,
        failed_call_count=failed_call_count,
        diagnostic_reason=diagnostic_reason,
        finalized_at=_finalized_at(rows),
    )


async def get_scope_billing(
    db: AsyncSession,
    *,
    scope_type: str,
    scope_id: str,
    caller_id: str | None = None,
) -> BillingEnvelope:
    rows = await AiCallLogRepo.list_for_scope(db, scope_type=scope_type, scope_id=scope_id, caller_id=caller_id)
    return build_scope_billing_envelope(scope_type=scope_type, scope_id=scope_id, rows=rows)


def job_cost_from_billing(billing: BillingEnvelope) -> JobCost | None:
    if billing.status not in {"estimated", "not_billable"}:
        return None
    if not billing.currency:
        return None
    return JobCost(currency=billing.currency, amount=billing.total_cost_amount, final=True)


async def get_job_billing(
    db: AsyncSession,
    job_id: uuid.UUID,
    caller_id: str,
    *,
    request_id: str = "-",
) -> BillingEnvelope:
    if not settings.billing.enabled:
        raise AppError(
            "BILLING_DISABLED",
            "billing query is disabled",
            details={"request_id": request_id},
        )
    job = await JobRepo.get_for_caller(db, job_id, caller_id)
    if not job:
        raise NotFoundAppError("JOB_NOT_FOUND", f"job_id 不存在: {job_id}")
    if job.status not in {"succeeded", "failed"}:
        raise AppError(
            "BILLING_SCOPE_NOT_TERMINAL",
            "job billing is only available after the job reaches a terminal status",
            details={"job_id": str(job_id), "job_status": job.status},
        )
    return await get_scope_billing(db, scope_type="job", scope_id=str(job_id), caller_id=caller_id)
