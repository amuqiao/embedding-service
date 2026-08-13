from __future__ import annotations

import json
import re
from typing import Any

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import AppError, ValidationAppError
from app.core.language_catalog import supported_language_codes
from app.jobs.base import JobExecutor
from app.jobs.registry import register_job_type
from app.jobs.types.tagged_text_translation.prompt import build_translation_messages
from app.models.job import Job
from app.schemas.jobs import (
    TaggedTextTranslationCharCount,
    TaggedTextTranslationItemParams,
    TaggedTextTranslationParams,
    TaggedTextTranslationResult,
    TaggedTextTranslationResultItem,
    TaggedTextTranslationRuntimeFields,
)
from app.services.ai_gateway_facade import generate_text_with_ledger
from app.services.job_runtime import ai_billing_scope_id_from_job, job_params_from_job, runtime_fields_from_job
from app.services.jobs import trigger_request_id_from_job


HTML_TAG_RE = re.compile(r"</?[A-Za-z][^>]*>")
DOUBLE_PLACEHOLDER_RE = re.compile(r"\{\{[^{}]+\}\}")
SINGLE_PLACEHOLDER_RE = re.compile(r"(?<!\{)\{[^{}]+\}(?!\})")
TEXT_SLOT = "<text>"


def _validate_configured_input_limits(params: TaggedTextTranslationParams) -> None:
    max_items = settings.job.tagged_text_translation.max_items
    if len(params.items) > max_items:
        raise ValidationAppError(
            "INVALID_JOB_PARAMS",
            "tagged_text_translation items exceeds configured limit",
            {
                "field": "job_params.items",
                "max_items": max_items,
                "item_count": len(params.items),
            },
        )

    max_text_length = settings.job.tagged_text_translation.max_text_length
    for index, item in enumerate(params.items):
        text_length = len(item.text)
        if text_length > max_text_length:
            raise ValidationAppError(
                "INVALID_JOB_PARAMS",
                "tagged_text_translation item text exceeds configured limit",
                {
                    "field": f"job_params.items[{index}].text",
                    "max_text_length": max_text_length,
                    "text_length": text_length,
                },
            )

    total_text_length = sum(len(item.text) for item in params.items)
    max_total_text_length = settings.job.tagged_text_translation.max_total_text_length
    if total_text_length > max_total_text_length:
        raise ValidationAppError(
            "INVALID_JOB_PARAMS",
            "tagged_text_translation total text length exceeds configured limit",
            {
                "field": "job_params.items[].text",
                "max_total_text_length": max_total_text_length,
                "total_text_length": total_text_length,
            },
        )


def _model_output_invalid(message: str, details: dict[str, Any] | None = None) -> AppError:
    return AppError("MODEL_OUTPUT_INVALID", message, details=details)


def _protected_segments(text: str) -> list[tuple[int, int, str]]:
    matches: list[tuple[int, int, str]] = []
    for pattern in (HTML_TAG_RE, DOUBLE_PLACEHOLDER_RE, SINGLE_PLACEHOLDER_RE):
        matches.extend((match.start(), match.end(), match.group(0)) for match in pattern.finditer(text))
    segments: list[tuple[int, int, str]] = []
    cursor = 0
    for start, end, token in sorted(matches, key=lambda item: (item[0], -(item[1] - item[0]))):
        if start < cursor:
            continue
        segments.append((start, end, token))
        cursor = end
    return segments


def _extract_protected_tokens(text: str) -> list[str]:
    return [token for _, _, token in _protected_segments(text)]


def _protected_structure(text: str) -> list[str]:
    structure: list[str] = []
    cursor = 0
    for start, end, token in _protected_segments(text):
        if text[cursor:start].strip():
            structure.append(TEXT_SLOT)
        structure.append(token)
        cursor = end
    if text[cursor:].strip():
        structure.append(TEXT_SLOT)
    return structure


def _visible_char_count(text: str) -> int:
    visible = HTML_TAG_RE.sub("", text)
    visible = DOUBLE_PLACEHOLDER_RE.sub("", visible)
    visible = SINGLE_PLACEHOLDER_RE.sub("", visible)
    return len(visible)


def _validate_preserved_tokens(source_text: str, translated_text: str, *, item_id: str) -> None:
    source_tokens = _extract_protected_tokens(source_text)
    translated_tokens = _extract_protected_tokens(translated_text)
    if translated_tokens != source_tokens:
        raise _model_output_invalid(
            "model output did not preserve required tags or placeholders",
            details={
                "item_id": item_id,
                "expected_tokens": source_tokens,
                "actual_tokens": translated_tokens,
            },
        )
    source_structure = _protected_structure(source_text)
    translated_structure = _protected_structure(translated_text)
    if translated_structure != source_structure:
        raise _model_output_invalid(
            "model output changed protected tag or placeholder structure",
            details={
                "item_id": item_id,
                "expected_structure": source_structure,
                "actual_structure": translated_structure,
            },
        )


def _parse_model_json(text: str) -> dict[str, Any]:
    try:
        payload = json.loads(text.strip())
    except json.JSONDecodeError as exc:
        raise _model_output_invalid("tagged_text_translation output must be JSON") from exc
    if not isinstance(payload, dict):
        raise _model_output_invalid("tagged_text_translation output must be a JSON object")
    return payload


def _result_source_language(params: TaggedTextTranslationParams, payload: dict[str, Any]) -> str | None:
    if params.source_language is not None:
        return params.source_language
    value = payload.get("source_language")
    if value is None:
        return None
    if not isinstance(value, str):
        raise _model_output_invalid("source_language must be a string or null")
    if value not in supported_language_codes():
        raise _model_output_invalid("source_language is not supported", details={"source_language": value})
    return value


def _translated_items_by_id(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    raw_items = payload.get("items")
    if not isinstance(raw_items, list):
        raise _model_output_invalid("items must be a list")
    items_by_id: dict[str, dict[str, Any]] = {}
    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            raise _model_output_invalid("items[] must be an object")
        item_id = raw_item.get("id")
        if not isinstance(item_id, str) or not item_id:
            raise _model_output_invalid("items[].id must be a non-empty string")
        if item_id in items_by_id:
            raise _model_output_invalid("items[].id must be unique", details={"item_id": item_id})
        items_by_id[item_id] = raw_item
    return items_by_id


def _result_item(source_item: TaggedTextTranslationItemParams, raw_item: dict[str, Any]) -> TaggedTextTranslationResultItem:
    translated_text = raw_item.get("translated_text")
    if not isinstance(translated_text, str) or not translated_text:
        raise _model_output_invalid("items[].translated_text must be a non-empty string", details={"item_id": source_item.id})
    _validate_preserved_tokens(source_item.text, translated_text, item_id=source_item.id)
    target_count = _visible_char_count(translated_text)
    target_limit_hint = source_item.max_target_chars_hint
    return TaggedTextTranslationResultItem(
        id=source_item.id,
        source_text=source_item.text,
        translated_text=translated_text,
        char_count=TaggedTextTranslationCharCount(
            source=_visible_char_count(source_item.text),
            target=target_count,
            target_limit_hint=target_limit_hint,
            within_hint=None if target_limit_hint is None else target_count <= target_limit_hint,
        ),
    )


def _build_result(params: TaggedTextTranslationParams, payload: dict[str, Any]) -> TaggedTextTranslationResult:
    if payload.get("target_language") != params.target_language:
        raise _model_output_invalid("target_language does not match request")
    items_by_id = _translated_items_by_id(payload)
    source_ids = [item.id for item in params.items]
    if set(items_by_id) != set(source_ids):
        raise _model_output_invalid(
            "model output items do not match request items",
            details={"expected_ids": source_ids, "actual_ids": list(items_by_id)},
        )
    return TaggedTextTranslationResult(
        source_language=_result_source_language(params, payload),
        target_language=params.target_language,
        items=[_result_item(source_item, items_by_id[source_item.id]) for source_item in params.items],
    )


@register_job_type
class TaggedTextTranslationJob(JobExecutor):
    name = "tagged_text_translation"
    visibility = "public"
    role = "root"
    params_schema = TaggedTextTranslationParams
    runtime_fields_schema_name = "TaggedTextTranslationRuntimeFields"
    canonical_result_schema = TaggedTextTranslationResult
    public_result_schema = TaggedTextTranslationResult
    allow_callback = True
    requires_text_generation_model = True
    timeout_seconds = 300
    allowed_error_codes = JobExecutor.allowed_error_codes | frozenset(
        {
            "INVALID_JOB_PARAMS",
            "MODEL_NOT_AVAILABLE",
            "QUEUE_FULL",
        }
    )

    def normalize_job_params(self, job_params: dict[str, Any]) -> dict[str, Any]:
        try:
            params = TaggedTextTranslationParams.model_validate(job_params)
        except ValidationError as exc:
            raise ValidationAppError(
                "INVALID_JOB_PARAMS",
                "tagged_text_translation job_params does not match schema",
                {"errors": exc.errors(include_url=False), "job_type": self.name},
            ) from exc
        normalized = params.model_dump(exclude_none=True)
        self.validate_normalized_job_params(normalized)
        return normalized

    def validate_normalized_job_params(self, job_params: dict[str, Any]) -> None:
        params = TaggedTextTranslationParams.model_validate(job_params)
        _validate_configured_input_limits(params)

    def runtime_job_fields(self, job_params: dict[str, Any]) -> dict[str, Any]:
        TaggedTextTranslationParams.model_validate(job_params)
        return TaggedTextTranslationRuntimeFields(model_id=settings.registry.default_model_id).model_dump(
            by_alias=True,
            exclude_none=True,
        )

    def validate_canonical_result(self, result: dict[str, Any]) -> dict[str, Any]:
        return TaggedTextTranslationResult.model_validate(result).model_dump()

    def validate_public_result(self, result: dict[str, Any] | None) -> dict[str, Any] | None:
        if result is None:
            raise ValueError(f"{self.name} succeeded result is required")
        return TaggedTextTranslationResult.model_validate(result).model_dump()

    async def _execute(self, job: Job, db: AsyncSession) -> dict[str, Any] | None:
        params = TaggedTextTranslationParams.model_validate(job_params_from_job(job))
        runtime_fields = TaggedTextTranslationRuntimeFields.model_validate(runtime_fields_from_job(job))
        attempt_id = job.active_attempt_id
        if attempt_id is None:
            raise AppError(
                "JOB_RUNTIME_NOT_SUPPORTED",
                "tagged_text_translation requires an active attempt",
                details={"job_id": str(job.id), "job_type": job.job_type},
            )
        scope_id = ai_billing_scope_id_from_job(job)
        result = await generate_text_with_ledger(
            caller_id=job.caller_id,
            scope_type="job",
            scope_id=str(scope_id),
            operation="tagged_text_translation.translate",
            step_name="calling_model",
            request_id=trigger_request_id_from_job(job),
            job_id=job.id,
            scope_job_id=scope_id,
            attempt_id=attempt_id,
            job_type=job.job_type,
            model_id=runtime_fields.model_id,
            messages=build_translation_messages(params),
        )
        parsed = _parse_model_json(result.text)
        return _build_result(params, parsed).model_dump()
