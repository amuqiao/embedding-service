from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from typing import Any

from app.core.database import get_session_factory
from app.core.exceptions import AppError
from app.ai.adapters.base import ImageGenerationResult, ImageInput, TextGenerationResult
from app.ai import kernel


MODEL_GATE = kernel.ModelGate()
IMAGE_MODEL_GATE = kernel.ImageModelGate()
PROVIDER_GATEWAY = kernel.ProviderGateway()
USAGE_NORMALIZER = kernel.UsageNormalizer()
TYPED_PRICING_RESOLVER = kernel.TypedPricingResolver()
USAGE_LEDGER_WRITER = kernel.UsageLedgerWriter()
_TRANSIENT_PROVIDER_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})
_PROVIDER_STATUS_RE = re.compile(r"(?:Error code|status)[=:]?\s*(\d{3})", re.IGNORECASE)


def _ledger_session_factory(ledger_session_factory: Callable[[], Any] | None) -> Callable[[], Any]:
    return ledger_session_factory if ledger_session_factory is not None else get_session_factory()


def _provider_status_code(exc: Exception) -> int | None:
    raw_status = getattr(exc, "status_code", None)
    if raw_status is None:
        response = getattr(exc, "response", None)
        raw_status = getattr(response, "status_code", None)
    try:
        if raw_status is not None:
            return int(raw_status)
    except (TypeError, ValueError):
        pass

    match = _PROVIDER_STATUS_RE.search(str(exc))
    if match is None:
        return None
    return int(match.group(1))


def _provider_failure_error(exc: Exception) -> AppError:
    status_code = _provider_status_code(exc)
    if status_code is not None and status_code in _TRANSIENT_PROVIDER_STATUS_CODES:
        return AppError(
            "AI_PROVIDER_FAILED",
            "ai provider transient failure",
            details={"provider_status_code": status_code},
        )
    return AppError("MODEL_CALL_FAILED", "ai provider failed")


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
    scope_job_id: uuid.UUID | None = None,
    attempt_id: uuid.UUID | None = None,
    job_type: str | None = None,
    ledger_session_factory: Callable[[], Any] | None = None,
) -> TextGenerationResult:
    ledger_session_factory = _ledger_session_factory(ledger_session_factory)
    kernel.validate_ai_call_context(
        caller_id=caller_id,
        scope_type=scope_type,
        scope_id=scope_id,
        operation=operation,
        job_id=job_id,
        scope_job_id=scope_job_id,
        attempt_id=attempt_id,
        job_type=job_type,
    )
    gate_result = MODEL_GATE.resolve(model_id)
    model = gate_result.model
    resolved_model = gate_result.resolved_model
    price = TYPED_PRICING_RESOLVER.require_rule(resolved_model.pricing_ref)
    request_hash, input_size_bytes = kernel.hash_payload(
        {
            "model_id": model_id,
            "capability": resolved_model.capability,
            "provider": resolved_model.provider,
            "adapter": resolved_model.adapter,
            "provider_model": resolved_model.provider_model,
            "adapter_model": resolved_model.adapter_model,
            "route_config_hash": resolved_model.route_config_hash,
            "messages": messages,
        }
    )
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
        error = _provider_failure_error(exc)
        await USAGE_LEDGER_WRITER.mark_failed(
            ledger_session_factory,
            call_id,
            failure_phase="provider",
            error_code=error.code,
            error_message=str(exc) or type(exc).__name__,
            billable_status="unknown",
        )
        raise error from exc

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


async def generate_text_with_images_with_ledger(
    *,
    caller_id: str,
    scope_type: str,
    scope_id: str,
    operation: str,
    model_id: str,
    prompt: str,
    reference_images: list[ImageInput],
    step_name: str | None = None,
    request_id: str | None = None,
    trace_id: str | None = None,
    job_id: uuid.UUID | None = None,
    scope_job_id: uuid.UUID | None = None,
    attempt_id: uuid.UUID | None = None,
    job_type: str | None = None,
    ledger_session_factory: Callable[[], Any] | None = None,
) -> TextGenerationResult:
    ledger_session_factory = _ledger_session_factory(ledger_session_factory)
    kernel.validate_ai_call_context(
        caller_id=caller_id,
        scope_type=scope_type,
        scope_id=scope_id,
        operation=operation,
        job_id=job_id,
        scope_job_id=scope_job_id,
        attempt_id=attempt_id,
        job_type=job_type,
    )
    gate_result = MODEL_GATE.resolve_multimodal_text(
        model_id,
        required_media_types={image.content_type for image in reference_images},
    )
    model = gate_result.model
    resolved_model = gate_result.resolved_model
    price = TYPED_PRICING_RESOLVER.require_rule(resolved_model.pricing_ref)
    request_hash, input_size_bytes = kernel.hash_payload(
        {
            "model_id": model_id,
            "capability": resolved_model.capability,
            "provider": resolved_model.provider,
            "adapter": resolved_model.adapter,
            "provider_model": resolved_model.provider_model,
            "adapter_model": resolved_model.adapter_model,
            "route_config_hash": resolved_model.route_config_hash,
            "prompt": prompt,
            "reference_images": [
                {
                    "content_type": image.content_type,
                    "content_hash": kernel.hash_bytes(image.data)[0],
                    "detail": image.detail,
                }
                for image in reference_images
            ],
        }
    )
    input_size_bytes += sum(len(image.data) for image in reference_images)
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
        result = await PROVIDER_GATEWAY.generate_text_with_images(
            model,
            prompt=prompt,
            reference_images=reference_images,
        )
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
        error = _provider_failure_error(exc)
        await USAGE_LEDGER_WRITER.mark_failed(
            ledger_session_factory,
            call_id,
            failure_phase="provider",
            error_code=error.code,
            error_message=str(exc) or type(exc).__name__,
            billable_status="unknown",
        )
        raise error from exc

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


async def generate_image_with_ledger(
    *,
    caller_id: str,
    scope_type: str,
    scope_id: str,
    operation: str,
    model_id: str,
    image_adapter: str | None = None,
    response_model: str,
    prompt: str,
    reference_images: list[ImageInput],
    size: str,
    quality: str,
    background: str,
    output_format: str,
    step_name: str | None = None,
    request_id: str | None = None,
    trace_id: str | None = None,
    job_id: uuid.UUID | None = None,
    scope_job_id: uuid.UUID | None = None,
    attempt_id: uuid.UUID | None = None,
    job_type: str | None = None,
    ledger_session_factory: Callable[[], Any] | None = None,
) -> ImageGenerationResult:
    ledger_session_factory = _ledger_session_factory(ledger_session_factory)
    kernel.validate_ai_call_context(
        caller_id=caller_id,
        scope_type=scope_type,
        scope_id=scope_id,
        operation=operation,
        job_id=job_id,
        scope_job_id=scope_job_id,
        attempt_id=attempt_id,
        job_type=job_type,
    )
    gate_result = IMAGE_MODEL_GATE.resolve(model_id, require_edit=bool(reference_images))
    model = gate_result.model
    resolved_model = gate_result.resolved_model
    if image_adapter is not None and image_adapter != resolved_model.adapter:
        raise AppError(
            "MODEL_NOT_AVAILABLE",
            "image adapter does not match model route",
            details={
                "model_id": resolved_model.model_id,
                "capability": resolved_model.capability,
                "image_adapter": image_adapter,
                "route_adapter": resolved_model.adapter,
            },
        )
    price = TYPED_PRICING_RESOLVER.require_rule(resolved_model.pricing_ref)
    request_hash, input_size_bytes = kernel.hash_payload(
        {
            "model_id": model_id,
            "capability": resolved_model.capability,
            "provider": resolved_model.provider,
            "adapter": resolved_model.adapter,
            "provider_model": resolved_model.provider_model,
            "adapter_model": resolved_model.adapter_model,
            "route_config_hash": resolved_model.route_config_hash,
            "response_model": response_model,
            "prompt": prompt,
            "reference_images": [
                {
                    "content_type": image.content_type,
                    "content_hash": kernel.hash_bytes(image.data)[0],
                    "detail": image.detail,
                }
                for image in reference_images
            ],
            "model_options": {
                "size": size,
                "quality": quality,
                "background": background,
                "output_format": output_format,
            },
        }
    )
    input_size_bytes += sum(len(image.data) for image in reference_images)
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
        result = await PROVIDER_GATEWAY.generate_image(
            model,
            image_adapter=resolved_model.adapter,
            response_model=response_model,
            prompt=prompt,
            reference_images=reference_images,
            size=size,
            quality=quality,
            background=background,
            output_format=output_format,
        )
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
        error = _provider_failure_error(exc)
        await USAGE_LEDGER_WRITER.mark_failed(
            ledger_session_factory,
            call_id,
            failure_phase="provider",
            error_code=error.code,
            error_message=str(exc) or type(exc).__name__,
            billable_status="unknown",
        )
        raise error from exc

    try:
        usage_record = USAGE_NORMALIZER.normalize_image(result)
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

    response_hash, output_size_bytes = kernel.hash_bytes_list(result.images)
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
