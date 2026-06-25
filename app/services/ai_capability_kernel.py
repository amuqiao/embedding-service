from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.core.ai_capabilities import ResolvedModel
from app.core.config import settings
from app.core.exceptions import AppError, ValidationAppError
from app.core.model_registry import TextModel, get_enabled_model
from app.core.pricing_registry import calculate_cost as calculate_usage_cost
from app.core.pricing_registry import TokenPrice, require_price, validate_price_matches_model
from app.core.usage_records import TextUsageRecord, UsageRecord, normalize_text_usage
from app.integrations.ai_gateway import TextGenerationRequest, TextGenerationResult, generate_text
from app.repositories.ai_call_log_repo import AiCallLogRepo

KNOWN_SCOPE_TYPES = {"job", "sync_api", "internal", "batch"}


@dataclass(frozen=True)
class ModelGateResult:
    model: TextModel
    resolved_model: ResolvedModel


def json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def hash_payload(value: Any) -> tuple[str, int]:
    encoded = json_bytes(value)
    return "sha256:" + hashlib.sha256(encoded).hexdigest(), len(encoded)


def hash_text(value: str) -> tuple[str, int]:
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


def normalize_text_result_usage(result: TextGenerationResult) -> TextUsageRecord:
    if result.usage is None or result.prompt_tokens is None or result.completion_tokens is None:
        raise AppError(
            "MODEL_USAGE_MISSING",
            "provider response did not include token usage",
        )
    cached_input_tokens = max(
        _nested_int(result.usage, ("prompt_tokens_details", "cached_tokens")),
        _nested_int(result.usage, ("input_token_details", "cached_tokens")),
        _nested_int(result.usage, ("cache_read_input_tokens",)),
    )
    return normalize_text_usage(
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
        cached_input_tokens=cached_input_tokens,
        raw_usage=result.usage,
    )


def validate_ai_call_context(
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


def require_enabled_text_model(model_id: str) -> TextModel:
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


class ModelGate:
    def resolve(self, model_id: str) -> ModelGateResult:
        model = require_enabled_text_model(model_id)
        if "text_generation" not in model.capabilities:
            raise ValidationAppError("MODEL_NOT_AVAILABLE", f"模型不支持文本生成: {model_id}")
        return ModelGateResult(
            model=model,
            resolved_model=ResolvedModel(
                model_id=model.id,
                provider=model.provider,
                provider_model=model.provider_model,
                litellm_model=model.litellm_model,
                pricing_ref=model.pricing_ref,
            ),
        )


class ProviderGateway:
    async def generate_text(self, model: TextModel, messages: list[dict[str, str]]) -> TextGenerationResult:
        return await generate_text(
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


class UsageNormalizer:
    def normalize_text(self, result: TextGenerationResult) -> TextUsageRecord:
        return normalize_text_result_usage(result)


class TypedPricingResolver:
    def require_rule(self, pricing_ref: str) -> Any:
        return require_price(pricing_ref)

    def calculate_cost(self, price: Any, usage_record: UsageRecord):
        if isinstance(usage_record, TextUsageRecord) and not isinstance(price, TokenPrice):
            raise RuntimeError("text usage requires per_token pricing")
        return calculate_usage_cost(price, usage_record)


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


class UsageLedgerWriter:
    async def create_pending(self, session_factory: Callable[[], Any], **kwargs: Any) -> uuid.UUID:
        return await _create_pending_and_commit(session_factory, **kwargs)

    async def mark_failed(
        self,
        session_factory: Callable[[], Any],
        call_id: uuid.UUID,
        *,
        failure_phase: str,
        error_code: str,
        error_message: str,
        billable_status: str,
        cost_calculation_status: str = "not_applicable",
    ) -> None:
        await _mark_failed_and_commit(
            session_factory,
            call_id,
            failure_phase=failure_phase,
            error_code=error_code,
            error_message=error_message,
            billable_status=billable_status,
            cost_calculation_status=cost_calculation_status,
        )

    async def mark_succeeded(
        self,
        session_factory: Callable[[], Any],
        call_id: uuid.UUID,
        *,
        usage_detail: dict[str, Any],
        usage_units: dict[str, int],
        cost_amount: Any,
        currency: str,
        response_hash: str | None,
        output_size_bytes: int | None,
    ) -> None:
        async with session_factory() as db:
            marked = await AiCallLogRepo.mark_succeeded(
                db,
                call_id,
                usage_detail=usage_detail,
                usage_units=usage_units,
                cost_amount=cost_amount,
                currency=currency,
                response_hash=response_hash,
                output_size_bytes=output_size_bytes,
            )
            if not marked:
                raise AppError(
                    "AI_LEDGER_UPDATE_FAILED",
                    "pending ai call ledger row could not be marked succeeded",
                    details={"ai_call_log_id": str(call_id)},
                )
            await db.commit()
