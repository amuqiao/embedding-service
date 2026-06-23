from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from typing import Any

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.exceptions import AppError, ValidationAppError
from app.core.model_registry import TextModel, get_enabled_model
from app.core.pricing_registry import calculate_token_cost, require_price, validate_price_matches_model
from app.integrations.ai_gateway import TextGenerationRequest, TextGenerationResult, generate_text
from app.repositories.ai_call_log_repo import AiCallLogRepo

KNOWN_SCOPE_TYPES = {"job", "sync_api", "internal", "batch"}


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def _hash_payload(value: Any) -> tuple[str, int]:
    encoded = _json_bytes(value)
    return "sha256:" + hashlib.sha256(encoded).hexdigest(), len(encoded)


def _hash_text(value: str) -> tuple[str, int]:
    encoded = value.encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest(), len(encoded)


def _nested_int(data: dict[str, Any], path: tuple[str, ...]) -> int:
    current: Any = data
    for key in path:
        if not isinstance(current, dict):
            return 0
        current = current.get(key)
    if isinstance(current, bool) or not isinstance(current, int | float):
        return 0
    return int(current)


def _usage_units(result: TextGenerationResult) -> dict[str, int]:
    if result.usage is None or result.prompt_tokens is None or result.completion_tokens is None:
        raise AppError(
            "MODEL_USAGE_MISSING",
            "provider response did not include token usage",
            status_code=502,
        )
    input_tokens = int(result.prompt_tokens)
    output_tokens = int(result.completion_tokens)
    cached_input_tokens = max(
        _nested_int(result.usage, ("prompt_tokens_details", "cached_tokens")),
        _nested_int(result.usage, ("input_token_details", "cached_tokens")),
        _nested_int(result.usage, ("cache_read_input_tokens",)),
    )
    return {
        "input_tokens": input_tokens,
        "cached_input_tokens": cached_input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
    }


def _validate_context(
    *,
    caller_id: str,
    scope_type: str,
    scope_id: str,
    operation: str,
    job_id: uuid.UUID | None,
    attempt_id: uuid.UUID | None,
    job_type: str | None,
) -> None:
    if not caller_id.strip():
        raise ValidationAppError("INVALID_INPUT", "caller_id is required")
    if scope_type not in KNOWN_SCOPE_TYPES:
        raise ValidationAppError("INVALID_INPUT", f"unsupported scope_type: {scope_type}")
    if not scope_id.strip():
        raise ValidationAppError("INVALID_INPUT", "scope_id is required")
    if not operation.strip():
        raise ValidationAppError("INVALID_INPUT", "operation is required")
    if scope_type == "job":
        if job_id is None or attempt_id is None or not job_type:
            raise ValidationAppError("INVALID_INPUT", "job scope requires job_id, attempt_id, and job_type")
        if scope_id != str(job_id):
            raise ValidationAppError("INVALID_INPUT", "job scope_id must equal job_id")


def _require_model(model_id: str) -> TextModel:
    model = get_enabled_model(model_id)
    if model is None:
        raise ValidationAppError("MODEL_NOT_AVAILABLE", f"模型不可用: {model_id}")
    validate_price_matches_model(
        pricing_ref=model.pricing_ref,
        model_id=model.id,
        provider=model.provider,
        provider_model=model.provider_model,
    )
    return model


async def _mark_failed_and_commit(
    session_factory: Callable[[], Any],
    call_id: uuid.UUID,
    *,
    failure_phase: str,
    error_code: str,
    error_message: str,
    billable_status: str,
    cost_calculation_status: str = "not_applicable",
) -> None:
    async with session_factory() as db:
        marked = await AiCallLogRepo.mark_failed(
            db,
            call_id,
            failure_phase=failure_phase,
            error_code=error_code,
            error_message=error_message,
            billable_status=billable_status,
            cost_calculation_status=cost_calculation_status,
        )
        if not marked:
            raise AppError(
                "AI_LEDGER_UPDATE_FAILED",
                "pending ai call ledger row could not be marked failed",
                status_code=500,
                details={"ai_call_log_id": str(call_id), "failure_phase": failure_phase},
            )
        await db.commit()


async def _create_pending_and_commit(
    session_factory: Callable[[], Any],
    **kwargs: Any,
) -> uuid.UUID:
    async with session_factory() as db:
        pending = await AiCallLogRepo.create_pending(db, **kwargs)
        call_id = pending.id
        await db.commit()
        return call_id


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
    _validate_context(
        caller_id=caller_id,
        scope_type=scope_type,
        scope_id=scope_id,
        operation=operation,
        job_id=job_id,
        attempt_id=attempt_id,
        job_type=job_type,
    )
    model = _require_model(model_id)
    price = require_price(model.pricing_ref)
    request_hash, input_size_bytes = _hash_payload({"model_id": model_id, "messages": messages})
    call_id = await _create_pending_and_commit(
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
        model_id=model.id,
        provider=model.provider,
        provider_model=model.provider_model,
        litellm_model=model.litellm_model,
        pricing_ref=price.ref,
        pricing_version=price.version,
        request_hash=request_hash,
        input_size_bytes=input_size_bytes,
    )

    try:
        result = await generate_text(
            TextGenerationRequest(
                litellm_model=model.litellm_model,
                messages=messages,
                temperature=model.temperature,
                timeout_seconds=settings.ai_provider.model_call_timeout_seconds,
                api_key=settings.ai_provider.openai_api_key_value or None,
                api_base=settings.ai_provider.openai_base_url or None,
                num_retries=model.num_retries,
                drop_params=model.drop_params,
            )
        )
    except TimeoutError as exc:
        await _mark_failed_and_commit(
            ledger_session_factory,
            call_id,
            failure_phase="provider",
            error_code="MODEL_CALL_TIMEOUT",
            error_message=str(exc) or "model call timeout",
            billable_status="unknown",
        )
        raise AppError("MODEL_CALL_TIMEOUT", "model call timeout", status_code=504) from exc
    except Exception as exc:
        await _mark_failed_and_commit(
            ledger_session_factory,
            call_id,
            failure_phase="provider",
            error_code="MODEL_CALL_FAILED",
            error_message=str(exc) or type(exc).__name__,
            billable_status="unknown",
        )
        raise AppError("MODEL_CALL_FAILED", "ai provider failed", status_code=502) from exc

    response_hash, output_size_bytes = _hash_text(result.text)
    try:
        usage_units = _usage_units(result)
    except AppError as exc:
        await _mark_failed_and_commit(
            ledger_session_factory,
            call_id,
            failure_phase="usage",
            error_code=exc.code,
            error_message=exc.message,
            billable_status="unknown",
            cost_calculation_status="failed",
        )
        raise
    try:
        cost_amount = calculate_token_cost(price, usage_units)
    except Exception as exc:
        await _mark_failed_and_commit(
            ledger_session_factory,
            call_id,
            failure_phase="pricing",
            error_code="MODEL_COST_CALCULATION_FAILED",
            error_message=str(exc) or type(exc).__name__,
            billable_status="unknown",
            cost_calculation_status="failed",
        )
        raise AppError("MODEL_COST_CALCULATION_FAILED", "model cost calculation failed", status_code=502) from exc

    async with ledger_session_factory() as db:
        marked = await AiCallLogRepo.mark_succeeded(
            db,
            call_id,
            usage_detail=result.usage or {},
            usage_units=usage_units,
            cost_amount=cost_amount,
            currency=price.currency,
            response_hash=response_hash,
            output_size_bytes=output_size_bytes,
        )
        if not marked:
            raise AppError(
                "AI_LEDGER_UPDATE_FAILED",
                "pending ai call ledger row could not be marked succeeded",
                status_code=500,
                details={"ai_call_log_id": str(call_id)},
            )
        await db.commit()
    return result
