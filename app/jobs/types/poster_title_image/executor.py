from __future__ import annotations

import time
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import AppError
from app.core.language_catalog import require_supported_language
from app.core.model_registry import get_enabled_model
from app.core.prompt_templates import get_prompt_block_default
from app.integrations.ai_adapters.base import ImageInput
from app.integrations.image import (
    TRANSPARENT_REFERENCE_ALLOWED_CONTENT_TYPES,
    transparent_title_layer_from_green_screen_bytes,
    validate_transparent_reference_image,
)
from app.integrations.object_storage import sha256_digest
from app.integrations.storage import storage
from app.jobs.adapters.cpp_oss_url_ref import canonical_ref_from_cpp_oss_url_ref, cpp_oss_url_ref_from_output_object
from app.jobs.base import JobExecutor
from app.jobs.registry import register_job_type
from app.models.job import Job
from app.schemas.jobs import (
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
from app.services.ai_capability_kernel import ImageModelGate, ModelGate
from app.services.ai_gateway_facade import generate_image_with_ledger, generate_text_with_images_with_ledger
from app.services.job_runtime import ai_billing_scope_id_from_job, output_target_from_job
from app.services.jobs import trigger_request_id_from_job
from app.workflows import WorkflowDefinition, chord, group, register as register_workflow, task

POSTER_TITLE_IMAGE_JOB_TYPE = "poster_title_image"
POSTER_TITLE_IMAGE_STYLE_PROBE_JOB_TYPE = "poster_title_image_style_probe"
POSTER_TITLE_IMAGE_GENERATE_ITEM_JOB_TYPE = "poster_title_image_generate_item"
POSTER_TITLE_IMAGE_JOIN_JOB_TYPE = "poster_title_image_join"
POSTER_TITLE_IMAGE_JOIN_NODE_KEY = "join"
POSTER_TITLE_IMAGE_PROMPT_BLOCKS = ("style_probe", "additional_prompt", "layout_rules")
GREEN_SCREEN = "#00FF00"
POSTER_TITLE_IMAGE_PROVIDER_BACKGROUND = "auto"
IMAGE_MODEL_GATE = ImageModelGate()
RESPONSE_MODEL_GATE = ModelGate()
_WORKFLOW_DEFINITION: WorkflowDefinition | None = None

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
Placement context: {layout_rules}
Line breaks: Assess the visual width of the rendered text for this specific language and script. If it fits comfortably on one line, use a SINGLE line. If it is too long, break at ONE natural grammatical boundary only.
Maximum lines: 2
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


def _response_provider_model() -> str:
    model_id = settings.registry.poster_title_image_response_model_id
    model = get_enabled_model(model_id)
    if model is None:
        raise AppError("MODEL_NOT_AVAILABLE", f"模型不可用: {model_id}")
    return model.provider_model


def _response_model_id_for_reference(reference_image: ImageInput) -> str:
    model_id = settings.registry.poster_title_image_response_model_id
    _validate_response_model(required_media_types={reference_image.content_type})
    return model_id


def _validate_response_model(*, required_media_types: set[str]) -> None:
    model_id = settings.registry.poster_title_image_response_model_id
    result = RESPONSE_MODEL_GATE.resolve_multimodal_text(model_id, required_media_types=required_media_types)
    if result.model.features.get("supports_image_generation_tool") is not True:
        raise AppError("MODEL_NOT_AVAILABLE", f"模型不支持 image_generation tool 调用: {model_id}")


def _load_reference_image_from_ref(reference_image: Any) -> ImageInput:
    ref = canonical_ref_from_cpp_oss_url_ref(
        reference_image.model_dump() if hasattr(reference_image, "model_dump") else reference_image,
        allowed_content_types=TRANSPARENT_REFERENCE_ALLOWED_CONTENT_TYPES,
    )
    data = storage.read_bytes(bucket=ref.bucket, key=ref.key, region=ref.region)
    if sha256_digest(data) != ref.content_hash:
        raise AppError("INPUT_HASH_MISMATCH", "reference image sha256 mismatch")
    if ref.content_type is None:
        raise AppError("INVALID_INPUT", "reference image content_type is required")
    validate_transparent_reference_image(data, content_type=ref.content_type)
    return ImageInput(data=data, content_type=ref.content_type, detail="high")


def _load_reference_image(item: PosterTitleImageItemParams) -> ImageInput:
    return _load_reference_image_from_ref(item.reference_image)


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


def _safe_node_suffix(value: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in {"-", "_", "."} else "-" for char in value)
    cleaned = cleaned.strip("-_.")
    return cleaned[:64] or "node"


async def _probe_style(
    reference_image: ImageInput,
    prompt: str,
    *,
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
        model_id=_response_model_id_for_reference(reference_image),
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
        layout_rules=layout_rules,
        additional_prompt=additional_prompt,
    )


def _output_key_for_item_id(job: Job, item_id: str, image_index: int) -> str:
    output_target = output_target_from_job(job)
    prefix = output_target["oss_prefix"].strip("/")
    filename = "title-layer.png" if image_index == 1 else f"title-layer-{image_index}.png"
    key = f"poster-title/{job.root_job_id or job.id}/{item_id}/{filename}"
    return f"{prefix}/{key}" if prefix else key


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
    digest = sha256_digest(item_id.encode("utf-8"))[:16]
    return f"item.{suffix}.{digest}"


@register_job_type
class PosterTitleImageJob(JobExecutor):
    name = "poster_title_image"
    visibility = "public"
    role = "root"
    params_schema = PosterTitleImageParams
    runtime_fields_schema_name = "PosterTitleImageRuntimeFields"
    canonical_result_schema = PosterTitleImageResult
    public_result_schema = PosterTitleImageResult
    prompt_template_required_blocks = frozenset(POSTER_TITLE_IMAGE_PROMPT_BLOCKS)
    allow_callback = True
    max_attempts = 1
    timeout_seconds = 600
    allowed_error_codes = JobExecutor.allowed_error_codes | frozenset(
        {
            "ALL_ITEMS_FAILED",
            "INPUT_HASH_MISMATCH",
            "INPUT_TOO_LARGE",
            "OSS_FETCH_FAILED",
            "OSS_OBJECT_NOT_FOUND",
            "OSS_WRITE_FAILED",
            "RUNTIME_CONFIG_MISSING",
            "RUNTIME_REF_INVALID",
            "RUNTIME_REF_MISSING",
            "WORKFLOW_CHILD_FAILED",
        }
    )

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
        model_id = params.items[0].model_id if params.items else "gpt-image-2"
        return PosterTitleImageRuntimeFields(model_id=model_id).model_dump()

    def validate_normalized_job_params(self, job_params: dict[str, Any]) -> None:
        params = PosterTitleImageParams.model_validate(job_params)
        IMAGE_MODEL_GATE.resolve(params.items[0].model_id, require_edit=True)
        for item in params.items:
            require_supported_language(item.language)
            if item.model_options.draw_count > settings.job.poster_title_image_max_draw_count:
                raise AppError(
                    "INVALID_INPUT",
                    "draw_count exceeds configured poster_title_image limit",
                    details={
                        "max_draw_count": settings.job.poster_title_image_max_draw_count,
                        "draw_count": item.model_options.draw_count,
                    },
                )
            canonical_ref_from_cpp_oss_url_ref(
                item.reference_image.model_dump(),
                allowed_content_types=TRANSPARENT_REFERENCE_ALLOWED_CONTENT_TYPES,
            )
            _validate_response_model(required_media_types={item.reference_image.content_type})

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
    max_attempts = 1
    timeout_seconds = 300
    allowed_error_codes = PosterTitleImageJob.allowed_error_codes

    def normalize_job_params(self, job_params: dict[str, Any]) -> dict[str, Any]:
        return PosterTitleImageStyleProbeParams.model_validate(job_params).model_dump()

    def runtime_job_fields(self, job_params: dict[str, Any]) -> dict[str, Any]:
        return PosterTitleImageStyleProbeRuntimeFields(model_id=_response_provider_model()).model_dump()

    async def _execute(self, job: Job, db: AsyncSession) -> dict[str, Any] | None:
        params = PosterTitleImageStyleProbeParams.model_validate(job.job_params)
        attempt_id = job.active_attempt_id
        if attempt_id is None:
            raise AppError("JOB_RUNTIME_NOT_SUPPORTED", "poster_title_image style probe requires active_attempt_id")
        request_id = trigger_request_id_from_job(job)
        ai_scope_id = ai_billing_scope_id_from_job(job)
        reference_image = _load_reference_image_from_ref(params.reference_image)
        started = time.monotonic()
        style_desc = await _probe_style(
            reference_image,
            params.style_prompt,
            caller_id=job.caller_id,
            scope_id=str(ai_scope_id),
            scope_job_id=ai_scope_id,
            request_id=request_id,
            job=job,
            attempt_id=attempt_id,
        )
        elapsed_ms = int((time.monotonic() - started) * 1000)
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
    max_attempts = 1
    timeout_seconds = 600
    allowed_error_codes = PosterTitleImageJob.allowed_error_codes

    def normalize_job_params(self, job_params: dict[str, Any]) -> dict[str, Any]:
        return PosterTitleImageGenerateItemParams.model_validate(job_params).model_dump()

    def runtime_job_fields(self, job_params: dict[str, Any]) -> dict[str, Any]:
        item = PosterTitleImageGenerateItemParams.model_validate(job_params).item
        return PosterTitleImageGenerateItemRuntimeFields(model_id=item.model_id).model_dump()

    async def _execute(self, job: Job, db: AsyncSession) -> dict[str, Any] | None:
        params = PosterTitleImageGenerateItemParams.model_validate(job.job_params)
        item = params.item
        attempt_id = job.active_attempt_id
        if attempt_id is None:
            raise AppError("JOB_RUNTIME_NOT_SUPPORTED", "poster_title_image item requires active_attempt_id")
        request_id = trigger_request_id_from_job(job)
        ai_scope_id = ai_billing_scope_id_from_job(job)
        output_target = output_target_from_job(job)
        response_model = _response_provider_model()
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
                model_id=item.model_id,
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
            image_bytes = transparent_title_layer_from_green_screen_bytes(generated.images[0])
            key = _output_key_for_item_id(job, item.item_id, image_index)
            written = storage.write_bytes(
                bucket=output_target["oss_bucket"],
                region=output_target["oss_region"],
                key=key,
                data=image_bytes,
                content_type="image/png",
            )
            obj = cpp_oss_url_ref_from_output_object(
                bucket=str(written["oss_bucket"]),
                region=str(written["oss_region"]),
                key=str(written["oss_key"]),
                content_type="image/png",
                content_hash=str(written["content_hash"]),
            )
            images.append(PosterTitleImageImage(object=PosterTitleImageObject.model_validate(obj)))
        elapsed_ms = int((time.monotonic() - started) * 1000)
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
    max_attempts = 1
    timeout_seconds = 120
    allowed_error_codes = PosterTitleImageJob.allowed_error_codes

    def normalize_job_params(self, job_params: dict[str, Any]) -> dict[str, Any]:
        return PosterTitleImageJoinParams.model_validate(job_params).model_dump()

    def runtime_job_fields(self, job_params: dict[str, Any]) -> dict[str, Any]:
        return PosterTitleImageJoinRuntimeFields().model_dump()

    async def _execute(self, job: Job, db: AsyncSession) -> dict[str, Any] | None:
        params = PosterTitleImageJoinParams.model_validate(job.job_params)
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
        return result.model_dump()


def _workflow_definition() -> WorkflowDefinition:
    global _WORKFLOW_DEFINITION
    if _WORKFLOW_DEFINITION is None:
        _WORKFLOW_DEFINITION = WorkflowDefinition(
            workflow_type=POSTER_TITLE_IMAGE_JOB_TYPE,
            build=_workflow_expr,
            max_nodes=60,
        )
    return _WORKFLOW_DEFINITION


def register_poster_title_image_workflow() -> None:
    register_workflow(_workflow_definition())


def _workflow_expr(job_params: dict[str, Any]) -> Any:
    params = PosterTitleImageParams.model_validate(job_params)
    default_prompt_blocks = _default_prompt_blocks()
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
