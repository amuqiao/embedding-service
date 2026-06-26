from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from app.core.database import AsyncSessionLocal
from app.core.exceptions import AppError
from app.integrations.ai_adapters.base import TextGenerationResult
from app.services import ai_capability_kernel as kernel


MODEL_GATE = kernel.ModelGate()
PROVIDER_GATEWAY = kernel.ProviderGateway()
USAGE_NORMALIZER = kernel.UsageNormalizer()
TYPED_PRICING_RESOLVER = kernel.TypedPricingResolver()
USAGE_LEDGER_WRITER = kernel.UsageLedgerWriter()


async def generate_text_with_ledger(
    *,
    caller_id: str,
    scope_type: str,
    scope_id: str,
    operation: str,
    model_id: str,
    messages: list[dict[str, str]],
    step_name: str | None = None,
    request_id: str | None = None,
    trace_id: str | None = None,
    job_id: uuid.UUID | None = None,
    attempt_id: uuid.UUID | None = None,
    job_type: str | None = None,
    ledger_session_factory: Callable[[], Any] = AsyncSessionLocal,
) -> TextGenerationResult:
    kernel.validate_ai_call_context(
        caller_id=caller_id,
        scope_type=scope_type,
        scope_id=scope_id,
        operation=operation,
        job_id=job_id,
        attempt_id=attempt_id,
        job_type=job_type,
    )
    gate_result = MODEL_GATE.resolve(model_id)
    model = gate_result.model
    resolved_model = gate_result.resolved_model
    price = TYPED_PRICING_RESOLVER.require_rule(resolved_model.pricing_ref)
    request_hash, input_size_bytes = kernel.hash_payload({"model_id": model_id, "messages": messages})
    call_id = await USAGE_LEDGER_WRITER.create_pending(
        ledger_session_factory,
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
        model_id=resolved_model.model_id,
        provider=resolved_model.provider,
        provider_model=resolved_model.provider_model,
        litellm_model=resolved_model.adapter_model,
        pricing_ref=price.ref,
        pricing_version=price.version,
        request_hash=request_hash,
        input_size_bytes=input_size_bytes,
    )

    try:
        result = await PROVIDER_GATEWAY.generate_text(model, messages)
    except TimeoutError as exc:
        await USAGE_LEDGER_WRITER.mark_failed(
            ledger_session_factory,
            call_id,
            failure_phase="provider",
            error_code="MODEL_CALL_TIMEOUT",
            error_message=str(exc) or "model call timeout",
            billable_status="unknown",
        )
        raise AppError("MODEL_CALL_TIMEOUT", "model call timeout") from exc
    except Exception as exc:
        await USAGE_LEDGER_WRITER.mark_failed(
            ledger_session_factory,
            call_id,
            failure_phase="provider",
            error_code="MODEL_CALL_FAILED",
            error_message=str(exc) or type(exc).__name__,
            billable_status="unknown",
        )
        raise AppError("MODEL_CALL_FAILED", "ai provider failed") from exc

    response_hash, output_size_bytes = kernel.hash_text(result.text)
    try:
        usage_record = USAGE_NORMALIZER.normalize_text(result)
    except AppError as exc:
        await USAGE_LEDGER_WRITER.mark_failed(
            ledger_session_factory,
            call_id,
            failure_phase="usage",
            error_code=exc.code,
            error_message=exc.message,
            billable_status="unknown",
            cost_calculation_status="failed",
        )
        raise
    usage_units = usage_record.usage_units()
    try:
        cost_amount = TYPED_PRICING_RESOLVER.calculate_cost(price, usage_record)
    except Exception as exc:
        await USAGE_LEDGER_WRITER.mark_failed(
            ledger_session_factory,
            call_id,
            failure_phase="pricing",
            error_code="MODEL_COST_CALCULATION_FAILED",
            error_message=str(exc) or type(exc).__name__,
            billable_status="unknown",
            cost_calculation_status="failed",
        )
        raise AppError("MODEL_COST_CALCULATION_FAILED", "model cost calculation failed") from exc

    await USAGE_LEDGER_WRITER.mark_succeeded(
        ledger_session_factory,
        call_id,
        usage_detail=result.usage or {},
        usage_units=usage_units,
        cost_amount=cost_amount,
        currency=price.currency,
        response_hash=response_hash,
        output_size_bytes=output_size_bytes,
    )
    return result
