from __future__ import annotations

import hashlib
import logging
import time
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import AppError
from app.core.language_catalog import require_supported_language
from app.core.logging import LogEvent, log_event
from app.ai.capabilities import IMAGE_EDIT, MULTIMODAL_TEXT_GENERATION
from app.ai.resolver import resolve_model, resolve_route_config_hash
from app.core.prompt_templates import get_prompt_block_default
from app.ai.adapters.base import ImageInput
from app.integrations.image import (
    transparent_title_layer_from_green_screen_bytes,
)
from app.jobs.base import ExecutionRetryPolicy, JobExecutor, JobRetryPolicy
from app.ai.policy.job_models import (
    poster_title_image_generation_allowed_model_ids,
    poster_title_image_generation_default_model_id,
    poster_title_image_style_probe_model_id,
)
from app.jobs.registry import register_job_type
from app.jobs.types.poster_title_image.errors import (
    POSTER_TITLE_IMAGE_ALL_ITEMS_FAILED,
    POSTER_TITLE_IMAGE_DRAW_COUNT_EXCEEDS_LIMIT,
    POSTER_TITLE_IMAGE_REFERENCE_INVALID,
)
from app.jobs.types.poster_title_image.storage_adapter import PosterTitleImageStorageAdapter
from app.models.job import Job
from app.schemas.jobs import (
    POSTER_TITLE_IMAGE_MAX_TITLE_LINES,
    PosterTitleImageBatchSummary,
    PosterTitleImageDurationMs,
    PosterTitleImageGenerateItemParams,
    PosterTitleImageGenerateItemResult,
    PosterTitleImageGenerateItemRuntimeFields,
    PosterTitleImageImage,
    PosterTitleImageItemParams,
    PosterTitleImageJoinParams,
    PosterTitleImageJoinRuntimeFields,
    PosterTitleImageObject,
    PosterTitleImageParams,
    PosterTitleImageResult,
    PosterTitleImageResultItem,
    PosterTitleImageRuntimeFields,
    PosterTitleImageStyleProbeParams,
    PosterTitleImageStyleProbeResult,
    PosterTitleImageStyleProbeRuntimeFields,
)
from app.ai.kernel import ImageModelGate, ModelGate
from app.ai.gateway import generate_image_with_ledger, generate_text_with_images_with_ledger
from app.services.job_runtime import (
    ai_billing_scope_id_from_job,
    job_params_from_job,
    runtime_fields_from_job,
)
from app.services.jobs import trigger_request_id_from_job
from app.workflows import WorkflowDefinition, chord, group, register as register_workflow, task

POSTER_TITLE_IMAGE_JOB_TYPE = "poster_title_image"
POSTER_TITLE_IMAGE_STYLE_PROBE_JOB_TYPE = "poster_title_image_style_probe"
POSTER_TITLE_IMAGE_GENERATE_ITEM_JOB_TYPE = "poster_title_image_generate_item"
POSTER_TITLE_IMAGE_JOIN_JOB_TYPE = "poster_title_image_join"
POSTER_TITLE_IMAGE_BUSINESS_RETRY_POLICY = JobRetryPolicy(
    business_execution=ExecutionRetryPolicy(
        domain="business_execution",
        max_attempts=3,
        retry_delay_seconds=15,
        backoff_kind="fixed",
        retryable_error_codes=frozenset(
            {
                "AI_PROVIDER_FAILED",
                "MODEL_CALL_TIMEOUT",
                "OSS_FETCH_FAILED",
                "OSS_WRITE_FAILED",
                "JOB_TIMEOUT",
            }
        ),
    )
)
POSTER_TITLE_IMAGE_JOIN_NODE_KEY = "join"
POSTER_TITLE_IMAGE_PROMPT_BLOCKS = ("style_probe", "additional_prompt", "layout_rules")
GREEN_SCREEN = "#00FF00"
POSTER_TITLE_IMAGE_PROVIDER_BACKGROUND = "auto"
IMAGE_MODEL_GATE = ImageModelGate()
STYLE_PROBE_MODEL_GATE = ModelGate()
_WORKFLOW_DEFINITION: WorkflowDefinition | None = None
POSTER_TITLE_IMAGE_LOG_EVENTS = (
    LogEvent.POSTER_TITLE_IMAGE_STYLE_PROBE_COMPLETED,
    LogEvent.POSTER_TITLE_IMAGE_OBJECT_STORED,
    LogEvent.POSTER_TITLE_IMAGE_ITEM_COMPLETED,
    LogEvent.POSTER_TITLE_IMAGE_JOIN_COMPLETED,
)
POSTER_TITLE_IMAGE_STYLE_PROBE_LOG_EVENTS = (LogEvent.POSTER_TITLE_IMAGE_STYLE_PROBE_COMPLETED,)
POSTER_TITLE_IMAGE_GENERATE_ITEM_LOG_EVENTS = (
    LogEvent.POSTER_TITLE_IMAGE_OBJECT_STORED,
    LogEvent.POSTER_TITLE_IMAGE_ITEM_COMPLETED,
)
POSTER_TITLE_IMAGE_JOIN_LOG_EVENTS = (LogEvent.POSTER_TITLE_IMAGE_JOIN_COMPLETED,)
logger = logging.getLogger(__name__)

PROMPT_TEMPLATE = """\
Generate a high-resolution standalone title graphic. {bg_header}

== STYLE (letterforms only — ignore any background, glow, haze, or shadow behind text) ==
{locked_style_desc}

== TEXT ==
Exact text to render: {target_text}
Language: {language}
Reproduce every character exactly — do not add, omit, alter, or paraphrase any character.
For non-Latin scripts (Japanese, Korean, Thai, Arabic, Cyrillic, etc.): preserve native glyph structure but apply the same stroke weight, surface texture, distress level, and cinematic drama as the style description above.

== LAYOUT ==
Placement context (lower priority than the line break contract below): {layout_rules}
Line break contract (highest priority within layout; overrides any conflicting placement context or prompt override): {line_break_rules}
Scale: Render the title LARGE — main-title proportions. The text block must fill 85-95% of the frame width. Do not render small, compact, or center-pinned text.

== TECHNICAL REQUIREMENTS ==
{bg_requirement}
- Letterform weight: HEAVY and BOLD — thick main strokes with substantial visual mass, matching the reference weight
- No drop shadow, outer glow, halo, blur, or soft edge of any kind — letter edges must be hard and crisp
- No backing plate, brush stroke, banner, badge, ink splash, or any non-text carrier element
- Centered composition, complete and uncropped
- Output ready as an isolated title layer for poster compositing
{additional_prompt}\
"""


def _style_probe_provider_model(model_id: str) -> str:
    return resolve_model(
        capability=MULTIMODAL_TEXT_GENERATION,
        requested_model_id=model_id,
    ).resolved_model.provider_model


def _style_probe_route_config_hash(model_id: str) -> str:
    return resolve_route_config_hash(
        capability=MULTIMODAL_TEXT_GENERATION,
        requested_model_id=model_id,
    )


def _generation_route_config_hash(model_id: str) -> str:
    return resolve_route_config_hash(
        capability=IMAGE_EDIT,
        requested_model_id=model_id,
    )


def _style_probe_model_id_for_reference(reference_image: ImageInput, *, model_id: str, image_adapter: str) -> str:
    _validate_style_probe_model(
        required_media_types={reference_image.content_type},
        model_id=model_id,
        image_adapter=image_adapter,
    )
    return model_id


def _validate_style_probe_model(
    *,
    required_media_types: set[str],
    model_id: str | None = None,
    image_adapter: str | None = None,
) -> None:
    model_id = model_id or _style_probe_model_id()
    result = STYLE_PROBE_MODEL_GATE.resolve_multimodal_text(model_id, required_media_types=required_media_types)
    if (image_adapter or _image_adapter()) == "openai_responses" and result.model.features.get("supports_image_generation_tool") is not True:
        raise AppError("MODEL_NOT_AVAILABLE", f"模型不支持 image_generation tool 调用: {model_id}")


def _storage_adapter() -> PosterTitleImageStorageAdapter:
    return PosterTitleImageStorageAdapter.from_settings(settings)


def _validate_reference_ref_payload(reference_image: Any) -> None:
    _storage_adapter().validate_reference_ref_payload(reference_image)


def _load_reference_image_from_ref(reference_image: Any) -> ImageInput:
    return _storage_adapter().load_reference_image_from_ref(reference_image)


def _load_reference_image(item: PosterTitleImageItemParams) -> ImageInput:
    return _load_reference_image_from_ref(item.reference_image)


def _log_trigger_request_id(job: Job) -> str:
    return trigger_request_id_from_job(job) or "-"


def _effective_style_prompt(
    item: PosterTitleImageItemParams,
    *,
    default_prompt_blocks: dict[str, str],
) -> str:
    return (
        item.prompt_overrides.style_probe
        if item.prompt_overrides and item.prompt_overrides.style_probe
        else default_prompt_blocks["style_probe"]
    )


def _style_key(item: PosterTitleImageItemParams, *, style_prompt: str) -> str:
    return f"{item.reference_image.sha256}:{style_prompt}"


def _style_probe_model_id() -> str:
    return poster_title_image_style_probe_model_id()


def _generation_default_model_id() -> str:
    return poster_title_image_generation_default_model_id()


def _generation_allowed_model_ids() -> tuple[str, ...]:
    return poster_title_image_generation_allowed_model_ids()


def _image_adapter(model_id: str | None = None) -> str:
    generation_model_id = model_id or _generation_default_model_id()
    return resolve_model(
        capability=IMAGE_EDIT,
        requested_model_id=generation_model_id,
    ).resolved_model.adapter


def _max_workflow_nodes() -> int:
    return settings.job.poster_title_image.max_items * 2 + 1


def _generation_model_id_from_params(params: PosterTitleImageParams) -> str:
    return params.items[0].model_id or _generation_default_model_id()


def _normalize_generation_model_ids(job_params: dict[str, Any]) -> dict[str, Any]:
    params = PosterTitleImageParams.model_validate(job_params)
    default_model_id = _generation_default_model_id()
    normalized_items = []
    for item in params.items:
        item_data = item.model_dump()
        if item_data.get("model_id") is None:
            item_data["model_id"] = default_model_id
        normalized_items.append(item_data)
    return PosterTitleImageParams.model_validate({"items": normalized_items}).model_dump()


def _safe_node_suffix(value: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in {"-", "_", "."} else "-" for char in value)
    cleaned = cleaned.strip("-_.")
    return cleaned[:64] or "node"


async def _probe_style(
    reference_image: ImageInput,
    prompt: str,
    *,
    model_id: str,
    image_adapter: str,
    caller_id: str,
    scope_id: str,
    scope_job_id: Any,
    request_id: str,
    job: Job,
    attempt_id: Any,
) -> str:
    result = await generate_text_with_images_with_ledger(
        caller_id=caller_id,
        scope_type="job",
        scope_id=scope_id,
        operation="poster_title_image.probe_style",
        step_name="style_probe",
        request_id=request_id,
        job_id=job.id,
        scope_job_id=scope_job_id,
        attempt_id=attempt_id,
        job_type=job.job_type,
        model_id=_style_probe_model_id_for_reference(reference_image, model_id=model_id, image_adapter=image_adapter),
        prompt=prompt,
        reference_images=[reference_image],
    )
    text = result.text.strip()
    if text:
        return text
    raise AppError("MODEL_OUTPUT_INVALID", "style probe did not return text")


def _default_prompt_blocks() -> dict[str, str]:
    try:
        return {
            block_key: get_prompt_block_default(POSTER_TITLE_IMAGE_JOB_TYPE, block_key)
            for block_key in POSTER_TITLE_IMAGE_PROMPT_BLOCKS
        }
    except RuntimeError as exc:
        raise AppError("RUNTIME_CONFIG_MISSING", str(exc)) from exc


def _title_prompt(
    item: PosterTitleImageItemParams,
    *,
    language_name: str,
    style_desc: str,
    default_prompt_blocks: dict[str, str],
) -> str:
    overrides = item.prompt_overrides
    layout_rules = (
        overrides.layout_rules if overrides and overrides.layout_rules else default_prompt_blocks["layout_rules"]
    )
    additional_prompt = (
        overrides.additional_prompt
        if overrides and overrides.additional_prompt
        else default_prompt_blocks["additional_prompt"]
    )
    return PROMPT_TEMPLATE.format(
        bg_header=f"Output as PNG with a perfectly solid flat background color {GREEN_SCREEN}. No transparency.",
        bg_requirement=(
            f"- Background: solid flat uniform {GREEN_SCREEN}. Completely uniform — absolutely no gradients, "
            "no vignette, no noise, no texture. The background must be a single pure color."
        ),
        locked_style_desc=style_desc,
        target_text=item.title_text,
        language=f"{item.language} ({language_name})",
        line_break_rules=_line_break_rules(item.title_text),
        layout_rules=layout_rules,
        additional_prompt=additional_prompt,
    )


def _line_break_rules(title_text: str) -> str:
    lines = title_text.split("\n")
    if len(lines) == 1:
        return (
            "No caller-specified hard line break is present. Wrap the title naturally within the title area "
            "when needed for fit and balance. "
            f"Maximum lines: {POSTER_TITLE_IMAGE_MAX_TITLE_LINES}. "
            "This contract overrides any conflicting layout preference."
        )
    numbered_lines = " ".join(f"Line {index}: {line}" for index, line in enumerate(lines, start=1))
    return (
        "Caller-specified hard line breaks are present. "
        "The caller's LF characters define both the line count and the hard line break positions. "
        f"Render exactly these {len(lines)} lines in this order: {numbered_lines}. "
        "Preserve the line breaks exactly. Do not merge lines, reorder lines, add extra line breaks, "
        "or split any line further. "
        f"Maximum lines: {POSTER_TITLE_IMAGE_MAX_TITLE_LINES}. "
        "This contract overrides any conflicting layout preference."
    )


def _download_filename_for_item_id(job: Job, item_id: str, image_index: int) -> str:
    image_suffix = "" if image_index == 1 else f"-{image_index}"
    return f"poster-title-{job.root_job_id or job.id}-{item_id}{image_suffix}.png"


def _attachment_content_disposition(filename: str) -> str:
    return f'attachment; filename="{filename}"'


def _extract_join_result(result: dict[str, Any]) -> dict[str, Any]:
    if result.get("job_type") == POSTER_TITLE_IMAGE_JOB_TYPE and "batch_summary" in result:
        return PosterTitleImageResult.model_validate(result).model_dump()
    workflow = result.get("workflow") if isinstance(result, dict) else None
    nodes = workflow.get("nodes") if isinstance(workflow, dict) else None
    if isinstance(nodes, list):
        for node in nodes:
            if (
                isinstance(node, dict)
                and node.get("node_key") == POSTER_TITLE_IMAGE_JOIN_NODE_KEY
                and isinstance(node.get("result"), dict)
            ):
                return PosterTitleImageResult.model_validate(node["result"]).model_dump()
    raise ValueError("poster_title_image workflow result is missing join result")


def _child_by_node_key(children: list[Job], node_key: str) -> Job:
    for child in children:
        if child.workflow_node_key == node_key:
            return child
    raise AppError("RUNTIME_REF_MISSING", f"workflow child not found: {node_key}")


async def _workflow_children(job: Job, db: AsyncSession) -> list[Job]:
    if job.root_job_id is None:
        raise AppError("JOB_RUNTIME_NOT_SUPPORTED", "poster_title_image leaf requires root_job_id")
    from app.repositories.job_repo import JobRepo

    return await JobRepo.list_internal_children(db, root_job_id=job.root_job_id)


def _style_probe_node_key(index: int) -> str:
    return f"probe.{index}"


def _item_node_key(item_id: str) -> str:
    suffix = _safe_node_suffix(item_id)[:40]
    digest = hashlib.sha256(item_id.encode("utf-8")).hexdigest()[:16]
    return f"item.{suffix}.{digest}"


def _duration_totals_from_child(child: Job) -> tuple[int, int]:
    result = child.result if isinstance(child.result, dict) else {}
    duration = result.get("duration_ms")
    if not isinstance(duration, dict):
        return 0, 0
    return int(duration.get("ai_model") or 0), int(duration.get("total") or 0)


async def _build_result_snapshot(job: Job, db: AsyncSession) -> dict[str, Any] | None:
    params = PosterTitleImageParams.model_validate(job_params_from_job(job))
    from app.repositories.job_repo import JobRepo

    children = await JobRepo.list_internal_children(db, root_job_id=job.id)
    children_by_key = {child.workflow_node_key: child for child in children}
    result_items: list[PosterTitleImageResultItem] = []
    ai_model_ms = 0
    total_ms = 0
    for child in children:
        if child.job_type == POSTER_TITLE_IMAGE_STYLE_PROBE_JOB_TYPE and child.status == "succeeded":
            child_ai_ms, child_total_ms = _duration_totals_from_child(child)
            ai_model_ms += child_ai_ms
            total_ms += child_total_ms

    for item in params.items:
        node_key = _item_node_key(item.item_id)
        child = children_by_key.get(node_key)
        if child is None or child.status != "succeeded":
            continue
        if not isinstance(child.result, dict):
            raise AppError("RUNTIME_REF_INVALID", f"item child result is not available: {node_key}")
        result_item = PosterTitleImageResultItem.model_validate(child.result.get("item"))
        if result_item.item_id != item.item_id or result_item.language != item.language:
            raise AppError("MODEL_OUTPUT_INVALID", "item child returned mismatched item identity")
        if len(result_item.images) != item.model_options.draw_count:
            raise AppError("MODEL_OUTPUT_INVALID", "item child returned unexpected image count")
        result_items.append(result_item)
        child_ai_ms, child_total_ms = _duration_totals_from_child(child)
        ai_model_ms += child_ai_ms
        total_ms += child_total_ms

    if not result_items:
        return None
    result = PosterTitleImageResult(
        batch_summary=PosterTitleImageBatchSummary(
            total=len(result_items),
            succeeded=len(result_items),
            failed=0,
            running=0,
            pending=0,
        ),
        items=result_items,
        duration_ms=PosterTitleImageDurationMs(ai_model=ai_model_ms, total=total_ms),
    )
    return result.model_dump()


@register_job_type
class PosterTitleImageJob(JobExecutor):
    name = "poster_title_image"
    visibility = "public"
    role = "root"
    params_schema = PosterTitleImageParams
    runtime_fields_schema_name = "PosterTitleImageRuntimeFields"
    canonical_result_schema = PosterTitleImageResult
    public_result_schema = PosterTitleImageResult
    result_snapshot_statuses = frozenset({"running", "failed"})
    prompt_template_required_blocks = frozenset(POSTER_TITLE_IMAGE_PROMPT_BLOCKS)
    log_events = POSTER_TITLE_IMAGE_LOG_EVENTS
    allow_callback = True
    timeout_seconds = 600
    allowed_error_codes = JobExecutor.allowed_error_codes | frozenset(
        {
            "INPUT_HASH_MISMATCH",
            "INPUT_TOO_LARGE",
            "MODEL_NOT_AVAILABLE",
            "OSS_FETCH_FAILED",
            "OSS_OBJECT_NOT_FOUND",
            "OSS_WRITE_FAILED",
            POSTER_TITLE_IMAGE_ALL_ITEMS_FAILED,
            POSTER_TITLE_IMAGE_DRAW_COUNT_EXCEEDS_LIMIT,
            POSTER_TITLE_IMAGE_REFERENCE_INVALID,
            "RUNTIME_CONFIG_MISSING",
            "RUNTIME_REF_INVALID",
            "RUNTIME_REF_MISSING",
            "WORKFLOW_CHILD_FAILED",
        }
    )

    def normalize_job_params(self, job_params: dict[str, Any]) -> dict[str, Any]:
        return _normalize_generation_model_ids(job_params)

    def validate_canonical_result(self, result: dict[str, Any]) -> dict[str, Any]:
        if isinstance(result, dict) and isinstance(result.get("workflow"), dict):
            return result
        return super().validate_canonical_result(result)

    def public_result(self, canonical_result: dict[str, Any]) -> dict[str, Any] | None:
        return self.validate_public_result(canonical_result)

    def validate_public_result(self, result: dict[str, Any] | None) -> dict[str, Any] | None:
        if result is None:
            raise ValueError("poster_title_image succeeded result is required")
        return _extract_join_result(result)

    def runtime_job_fields(self, job_params: dict[str, Any]) -> dict[str, Any]:
        params = PosterTitleImageParams.model_validate(job_params)
        style_probe_model_id = _style_probe_model_id()
        generation_model_id = _generation_model_id_from_params(params)
        image_adapter = _image_adapter(generation_model_id)
        return PosterTitleImageRuntimeFields(
            style_probe_model_id=style_probe_model_id,
            style_probe_route_config_hash=_style_probe_route_config_hash(style_probe_model_id),
            generation_model_id=generation_model_id,
            generation_route_config_hash=_generation_route_config_hash(generation_model_id),
            image_adapter=image_adapter,
        ).model_dump(by_alias=True, exclude_none=True)

    async def build_result_snapshot(self, status: str, job: Job, db: AsyncSession) -> dict[str, Any] | None:
        return await _build_result_snapshot(job, db)

    def validate_normalized_job_params(self, job_params: dict[str, Any]) -> None:
        params = PosterTitleImageParams.model_validate(job_params)
        max_items = settings.job.poster_title_image.max_items
        if len(params.items) > max_items:
            raise AppError(
                "INVALID_INPUT",
                "poster_title_image items exceeds configured limit",
                details={
                    "field": "job_params.items",
                    "max_items": max_items,
                    "item_count": len(params.items),
                },
            )
        generation_model_id = _generation_model_id_from_params(params)
        allowed_model_ids = _generation_allowed_model_ids()
        if generation_model_id not in allowed_model_ids:
            raise AppError(
                "INVALID_INPUT",
                "poster_title_image model_id is not supported",
                details={
                    "field": "job_params.items[].model_id",
                    "model_id": generation_model_id,
                    "allowed_model_ids": list(allowed_model_ids),
                },
            )
        image_adapter = _image_adapter(generation_model_id)
        IMAGE_MODEL_GATE.resolve(generation_model_id, require_edit=True)
        for item in params.items:
            require_supported_language(item.language)
            if item.model_options.draw_count > settings.job.poster_title_image.max_draw_count:
                raise AppError(
                    POSTER_TITLE_IMAGE_DRAW_COUNT_EXCEEDS_LIMIT,
                    "draw_count exceeds configured poster_title_image limit",
                    details={
                        "max_draw_count": settings.job.poster_title_image.max_draw_count,
                        "draw_count": item.model_options.draw_count,
                    },
                )
            _validate_reference_ref_payload(item.reference_image)
            _validate_style_probe_model(required_media_types={item.reference_image.content_type}, image_adapter=image_adapter)

    async def _execute(self, job: Job, db: AsyncSession) -> dict[str, Any] | None:
        raise AppError(
            "JOB_RUNTIME_NOT_SUPPORTED",
            "poster_title_image root must be executed by workflow orchestration",
            details={"job_id": str(job.id), "job_type": job.job_type},
        )


@register_job_type
class PosterTitleImageStyleProbeJob(JobExecutor):
    name = POSTER_TITLE_IMAGE_STYLE_PROBE_JOB_TYPE
    visibility = "internal"
    role = "leaf"
    params_schema = PosterTitleImageStyleProbeParams
    runtime_fields_schema_name = "PosterTitleImageStyleProbeRuntimeFields"
    canonical_result_schema = PosterTitleImageStyleProbeResult
    public_result_schema = PosterTitleImageStyleProbeResult
    allow_callback = False
    timeout_seconds = 300
    retry_policy = POSTER_TITLE_IMAGE_BUSINESS_RETRY_POLICY
    allowed_error_codes = PosterTitleImageJob.allowed_error_codes
    log_events = POSTER_TITLE_IMAGE_STYLE_PROBE_LOG_EVENTS

    def normalize_job_params(self, job_params: dict[str, Any]) -> dict[str, Any]:
        return PosterTitleImageStyleProbeParams.model_validate(job_params).model_dump()

    def runtime_job_fields(self, job_params: dict[str, Any]) -> dict[str, Any]:
        params = PosterTitleImageStyleProbeParams.model_validate(job_params)
        return PosterTitleImageStyleProbeRuntimeFields(
            style_probe_model_id=params.style_probe_model_id,
            style_probe_route_config_hash=_style_probe_route_config_hash(params.style_probe_model_id),
            image_adapter=params.image_adapter,
        ).model_dump(by_alias=True, exclude_none=True)

    async def _execute(self, job: Job, db: AsyncSession) -> dict[str, Any] | None:
        params = PosterTitleImageStyleProbeParams.model_validate(job_params_from_job(job))
        runtime_fields = PosterTitleImageStyleProbeRuntimeFields.model_validate(runtime_fields_from_job(job))
        attempt_id = job.active_attempt_id
        if attempt_id is None:
            raise AppError("JOB_RUNTIME_NOT_SUPPORTED", "poster_title_image style probe requires active_attempt_id")
        request_id = trigger_request_id_from_job(job)
        log_request_id = request_id or "-"
        ai_scope_id = ai_billing_scope_id_from_job(job)
        reference_image = _load_reference_image_from_ref(params.reference_image)
        started = time.monotonic()
        style_desc = await _probe_style(
            reference_image,
            params.style_prompt,
            model_id=runtime_fields.style_probe_model_id,
            image_adapter=runtime_fields.image_adapter,
            caller_id=job.caller_id,
            scope_id=str(ai_scope_id),
            scope_job_id=ai_scope_id,
            request_id=request_id,
            job=job,
            attempt_id=attempt_id,
        )
        elapsed_ms = int((time.monotonic() - started) * 1000)
        log_event(
            logger,
            logging.INFO,
            LogEvent.POSTER_TITLE_IMAGE_STYLE_PROBE_COMPLETED,
            job_id=job.id,
            root_job_id=job.root_job_id,
            attempt_id=attempt_id,
            trigger_request_id=log_request_id,
            caller_id=job.caller_id,
            job_type=job.job_type,
            workflow_node_key=job.workflow_node_key,
            operation="poster_title_image.probe_style",
            model_id=runtime_fields.style_probe_model_id,
            duration_ms=elapsed_ms,
        )
        return PosterTitleImageStyleProbeResult(
            style_key=params.style_key,
            style_desc=style_desc,
            duration_ms=PosterTitleImageDurationMs(ai_model=elapsed_ms, total=elapsed_ms),
        ).model_dump()


@register_job_type
class PosterTitleImageGenerateItemJob(JobExecutor):
    name = POSTER_TITLE_IMAGE_GENERATE_ITEM_JOB_TYPE
    visibility = "internal"
    role = "leaf"
    params_schema = PosterTitleImageGenerateItemParams
    runtime_fields_schema_name = "PosterTitleImageGenerateItemRuntimeFields"
    canonical_result_schema = PosterTitleImageGenerateItemResult
    public_result_schema = PosterTitleImageGenerateItemResult
    allow_callback = False
    timeout_seconds = 600
    retry_policy = POSTER_TITLE_IMAGE_BUSINESS_RETRY_POLICY
    allowed_error_codes = PosterTitleImageJob.allowed_error_codes
    log_events = POSTER_TITLE_IMAGE_GENERATE_ITEM_LOG_EVENTS

    def normalize_job_params(self, job_params: dict[str, Any]) -> dict[str, Any]:
        return PosterTitleImageGenerateItemParams.model_validate(job_params).model_dump()

    def runtime_job_fields(self, job_params: dict[str, Any]) -> dict[str, Any]:
        params = PosterTitleImageGenerateItemParams.model_validate(job_params)
        generation_model_id = params.item.model_id or _generation_default_model_id()
        image_adapter = _image_adapter(generation_model_id)
        return PosterTitleImageGenerateItemRuntimeFields(
            generation_model_id=generation_model_id,
            generation_route_config_hash=_generation_route_config_hash(generation_model_id),
            style_probe_model_id=params.style_probe_model_id,
            style_probe_route_config_hash=_style_probe_route_config_hash(params.style_probe_model_id),
            image_adapter=image_adapter,
        ).model_dump(by_alias=True, exclude_none=True)

    async def _execute(self, job: Job, db: AsyncSession) -> dict[str, Any] | None:
        params = PosterTitleImageGenerateItemParams.model_validate(job_params_from_job(job))
        item = params.item
        attempt_id = job.active_attempt_id
        if attempt_id is None:
            raise AppError("JOB_RUNTIME_NOT_SUPPORTED", "poster_title_image item requires active_attempt_id")
        request_id = trigger_request_id_from_job(job)
        log_request_id = request_id or "-"
        ai_scope_id = ai_billing_scope_id_from_job(job)
        runtime_fields = PosterTitleImageGenerateItemRuntimeFields.model_validate(runtime_fields_from_job(job))
        generation_model_id = runtime_fields.generation_model_id
        image_adapter = runtime_fields.image_adapter
        response_model = _style_probe_provider_model(runtime_fields.style_probe_model_id)
        default_prompt_blocks = _default_prompt_blocks()
        children = await _workflow_children(job, db)
        probe_child = _child_by_node_key(children, params.probe_node_key)
        if probe_child.status != "succeeded" or not isinstance(probe_child.result, dict):
            raise AppError("RUNTIME_REF_INVALID", "style probe child result is not available")
        style_desc = str(probe_child.result.get("style_desc") or "").strip()
        if not style_desc:
            raise AppError("MODEL_OUTPUT_INVALID", "style probe did not return text")

        started = time.monotonic()
        language_name = require_supported_language(item.language).display_name
        reference_image = _load_reference_image(item)
        prompt = _title_prompt(
            item,
            language_name=language_name,
            style_desc=style_desc,
            default_prompt_blocks=default_prompt_blocks,
        )
        images: list[PosterTitleImageImage] = []
        for image_index in range(1, item.model_options.draw_count + 1):
            generated = await generate_image_with_ledger(
                caller_id=job.caller_id,
                scope_type="job",
                scope_id=str(ai_scope_id),
                operation="poster_title_image.generate_title",
                step_name="image_generation",
                request_id=request_id,
                job_id=job.id,
                scope_job_id=ai_scope_id,
                attempt_id=attempt_id,
                job_type=job.job_type,
                model_id=generation_model_id,
                image_adapter=image_adapter,
                response_model=response_model,
                prompt=prompt,
                reference_images=[reference_image],
                size=item.model_options.size,
                quality=item.model_options.quality,
                background=POSTER_TITLE_IMAGE_PROVIDER_BACKGROUND,
                output_format="png",
            )
            if len(generated.images) != 1:
                raise AppError("MODEL_OUTPUT_INVALID", "image provider returned unexpected image count")
            title_layer = transparent_title_layer_from_green_screen_bytes(generated.images[0])
            stored = _storage_adapter().write_title_layer(
                job=job,
                item_id=item.item_id,
                image_index=image_index,
                data=title_layer.data,
                content_disposition=_attachment_content_disposition(
                    _download_filename_for_item_id(job, item.item_id, image_index)
                ),
            )
            written = stored["written"]
            log_event(
                logger,
                logging.INFO,
                LogEvent.POSTER_TITLE_IMAGE_OBJECT_STORED,
                job_id=job.id,
                root_job_id=job.root_job_id,
                attempt_id=attempt_id,
                trigger_request_id=log_request_id,
                caller_id=job.caller_id,
                job_type=job.job_type,
                workflow_node_key=job.workflow_node_key,
                item_id=item.item_id,
                language=item.language,
                image_index=image_index,
                oss_bucket=written["oss_bucket"],
                oss_region=written["oss_region"],
                oss_key=written["oss_key"],
                content_type="image/png",
                content_hash=written["content_hash"],
                bytes=len(title_layer.data),
            )
            obj = stored["object"]
            images.append(
                PosterTitleImageImage(
                    object=PosterTitleImageObject.model_validate(obj),
                    width=title_layer.width,
                    height=title_layer.height,
                )
            )
        elapsed_ms = int((time.monotonic() - started) * 1000)
        log_event(
            logger,
            logging.INFO,
            LogEvent.POSTER_TITLE_IMAGE_ITEM_COMPLETED,
            job_id=job.id,
            root_job_id=job.root_job_id,
            attempt_id=attempt_id,
            trigger_request_id=log_request_id,
            caller_id=job.caller_id,
            job_type=job.job_type,
            workflow_node_key=job.workflow_node_key,
            item_id=item.item_id,
            language=item.language,
            operation="poster_title_image.generate_title",
            model_id=generation_model_id,
            image_count=len(images),
            duration_ms=elapsed_ms,
        )
        return PosterTitleImageGenerateItemResult(
            item=PosterTitleImageResultItem(
                item_id=item.item_id,
                language=item.language,
                status="succeeded",
                images=images,
                error=None,
            ),
            duration_ms=PosterTitleImageDurationMs(ai_model=elapsed_ms, total=elapsed_ms),
        ).model_dump()


@register_job_type
class PosterTitleImageJoinJob(JobExecutor):
    name = POSTER_TITLE_IMAGE_JOIN_JOB_TYPE
    visibility = "internal"
    role = "leaf"
    params_schema = PosterTitleImageJoinParams
    runtime_fields_schema_name = "PosterTitleImageJoinRuntimeFields"
    canonical_result_schema = PosterTitleImageResult
    public_result_schema = PosterTitleImageResult
    allow_callback = False
    timeout_seconds = 120
    allowed_error_codes = PosterTitleImageJob.allowed_error_codes
    log_events = POSTER_TITLE_IMAGE_JOIN_LOG_EVENTS

    def normalize_job_params(self, job_params: dict[str, Any]) -> dict[str, Any]:
        return PosterTitleImageJoinParams.model_validate(job_params).model_dump()

    def runtime_job_fields(self, job_params: dict[str, Any]) -> dict[str, Any]:
        return PosterTitleImageJoinRuntimeFields().model_dump(by_alias=True, exclude_none=True)

    async def _execute(self, job: Job, db: AsyncSession) -> dict[str, Any] | None:
        params = PosterTitleImageJoinParams.model_validate(job_params_from_job(job))
        log_request_id = _log_trigger_request_id(job)
        requested = PosterTitleImageParams.model_validate({"items": [item.model_dump() for item in params.items]})
        children = await _workflow_children(job, db)
        children_by_key = {child.workflow_node_key: child for child in children}
        result_items: list[PosterTitleImageResultItem] = []
        ai_model_ms = 0
        total_ms = 0
        for child in children:
            if child.job_type != POSTER_TITLE_IMAGE_STYLE_PROBE_JOB_TYPE or child.status != "succeeded":
                continue
            duration = child.result.get("duration_ms") if isinstance(child.result, dict) else None
            if isinstance(duration, dict):
                ai_model_ms += int(duration.get("ai_model") or 0)
                total_ms += int(duration.get("total") or 0)
        for item in requested.items:
            node_key = _item_node_key(item.item_id)
            child = children_by_key.get(node_key)
            if child is None or child.status != "succeeded" or not isinstance(child.result, dict):
                raise AppError("RUNTIME_REF_INVALID", f"item child result is not available: {node_key}")
            result_item = PosterTitleImageResultItem.model_validate(child.result.get("item"))
            if len(result_item.images) != item.model_options.draw_count:
                raise AppError("MODEL_OUTPUT_INVALID", "item child returned unexpected image count")
            result_items.append(result_item)
            duration = child.result.get("duration_ms")
            if isinstance(duration, dict):
                ai_model_ms += int(duration.get("ai_model") or 0)
                total_ms += int(duration.get("total") or 0)
        result = PosterTitleImageResult(
            batch_summary=PosterTitleImageBatchSummary(
                total=len(result_items),
                succeeded=len(result_items),
                failed=0,
                running=0,
                pending=0,
            ),
            items=result_items,
            duration_ms=PosterTitleImageDurationMs(ai_model=ai_model_ms, total=total_ms),
        )
        log_event(
            logger,
            logging.INFO,
            LogEvent.POSTER_TITLE_IMAGE_JOIN_COMPLETED,
            job_id=job.id,
            root_job_id=job.root_job_id,
            attempt_id=job.active_attempt_id,
            trigger_request_id=log_request_id,
            caller_id=job.caller_id,
            job_type=job.job_type,
            workflow_node_key=job.workflow_node_key,
            total=len(result_items),
            succeeded=len(result_items),
            failed=0,
            ai_model_ms=ai_model_ms,
            total_ms=total_ms,
        )
        return result.model_dump()


def _workflow_definition() -> WorkflowDefinition:
    global _WORKFLOW_DEFINITION
    if _WORKFLOW_DEFINITION is None:
        _WORKFLOW_DEFINITION = WorkflowDefinition(
            workflow_type=POSTER_TITLE_IMAGE_JOB_TYPE,
            root_job_type=POSTER_TITLE_IMAGE_JOB_TYPE,
            build=_workflow_expr,
            max_nodes=_max_workflow_nodes(),
            runtime_job_type_dependencies=frozenset(
                {
                    POSTER_TITLE_IMAGE_STYLE_PROBE_JOB_TYPE,
                    POSTER_TITLE_IMAGE_GENERATE_ITEM_JOB_TYPE,
                    POSTER_TITLE_IMAGE_JOIN_JOB_TYPE,
                }
            ),
        )
    return _WORKFLOW_DEFINITION


def register_poster_title_image_workflow() -> None:
    register_workflow(_workflow_definition())


def _workflow_expr(job_params: dict[str, Any]) -> Any:
    params = PosterTitleImageParams.model_validate(job_params)
    default_prompt_blocks = _default_prompt_blocks()
    image_adapter = _image_adapter()
    style_probe_model_id = _style_probe_model_id()
    style_nodes: dict[str, tuple[str, PosterTitleImageItemParams, str]] = {}
    item_members = []
    for item in params.items:
        style_prompt = _effective_style_prompt(item, default_prompt_blocks=default_prompt_blocks)
        key = _style_key(item, style_prompt=style_prompt)
        if key not in style_nodes:
            node_key = _style_probe_node_key(len(style_nodes))
            style_nodes[key] = (node_key, item, style_prompt)
        probe_node_key = style_nodes[key][0]
        item_members.append(
            task(
                _item_node_key(item.item_id),
                POSTER_TITLE_IMAGE_GENERATE_ITEM_JOB_TYPE,
                {
                    "item": item.model_dump(),
                    "probe_node_key": probe_node_key,
                    "style_probe_model_id": style_probe_model_id,
                    "image_adapter": image_adapter,
                },
                depends_on=(probe_node_key,),
            )
        )
    probe_members = [
        task(
            node_key,
            POSTER_TITLE_IMAGE_STYLE_PROBE_JOB_TYPE,
            {
                "style_key": style_key,
                "reference_image": item.reference_image.model_dump(),
                "style_prompt": style_prompt,
                "style_probe_model_id": style_probe_model_id,
                "image_adapter": image_adapter,
            },
        )
        for style_key, (node_key, item, style_prompt) in style_nodes.items()
    ]
    return chord(
        group(*probe_members, *item_members),
        task(
            POSTER_TITLE_IMAGE_JOIN_NODE_KEY,
            POSTER_TITLE_IMAGE_JOIN_JOB_TYPE,
            {"items": [item.model_dump() for item in params.items]},
        ),
    )
