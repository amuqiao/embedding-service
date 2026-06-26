from __future__ import annotations

import time
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError
from app.core.language_catalog import require_supported_language
from app.core.model_registry import get_enabled_model
from app.integrations.ai_adapters.base import ImageInput
from app.integrations.image import remove_green_background
from app.integrations.object_storage import sha256_digest
from app.integrations.storage import storage
from app.jobs.adapters.cpp_oss_url_ref import canonical_ref_from_cpp_oss_url_ref, cpp_oss_url_ref_from_output_object
from app.jobs.base import JobExecutor
from app.jobs.registry import register_job_type
from app.models.job import Job
from app.schemas.jobs import (
    PosterTitleImageBatchSummary,
    PosterTitleImageDurationMs,
    PosterTitleImageError,
    PosterTitleImageImage,
    PosterTitleImageItemParams,
    PosterTitleImageObject,
    PosterTitleImageParams,
    PosterTitleImageResult,
    PosterTitleImageResultItem,
    PosterTitleImageRuntimeFields,
)
from app.services.ai_capability_kernel import ImageModelGate
from app.services.ai_gateway_facade import generate_image_with_ledger, generate_text_with_images_with_ledger
from app.services.job_runtime import ai_billing_scope_id_from_job, output_target_from_job
from app.services.jobs import trigger_request_id_from_job

POSTER_TITLE_IMAGE_RESPONSE_MODEL_ID = "gpt-4o"
POSTER_TITLE_IMAGE_MAX_INPUT_BYTES = 20 * 1024 * 1024
GREEN_SCREEN = "#00FF00"
IMAGE_MODEL_GATE = ImageModelGate()

STYLE_PROBE_PROMPT = """\
Analyze this title image and describe the visual design style of the LETTERFORMS ONLY.
Ignore any background, board, plaque, panel, canvas, atmospheric glow, haze, fog, or shadow behind or around the text — describe only what belongs to the letters themselves.

Output a single dense English paragraph suitable for use in an image generation prompt. Be precise and generative.

Cover only the following aspects of the letterforms themselves:
- Stroke weight and overall letter mass: is the typeface HEAVY/BOLD or light/thin? Describe the visual weight of the main strokes — thick slab, medium, or hairline-dominant.
- Letter dimensionality: flat / subtly embossed / 3D beveled / carved / extruded
- Material and surface texture: what the letter surfaces look like (metal, stone, glass, painted, printed); texture details inside the strokes
- Lighting on the letterforms: highlight direction and placement, color temperature, how light interacts with the letter surfaces and edges
- Color palette of the letters: fill color, stroke or outline color, any gradients within the letterforms — describe the letter color only, not any background color
- Special effects ON or immediately around the letters: cracks, distress, wear, abrasion, debris particles or fragments adjacent to the letters — note their presence and style
- Typography character: serif style, stroke weight contrast, condensed or expanded proportions, decorative details, overall weight and mood
- Composition scale: how large the text fills the frame — e.g. "letters fill nearly the full frame width" or "compact centered cluster"
- Overall cinematic / genre mood

Do NOT mention or describe: background color, background texture, atmospheric glow or haze behind the text, the plaque or board silhouette, shadows cast behind the text, or any element that is not part of the letterforms themselves.

Output ONLY the style description paragraph — no headers, no preamble, no explanation.\
"""

DEFAULT_LAYOUT_RULES = (
    "The title is a horizontal poster-title layer. Render the text large, filling 85-95% of the frame width. "
    "Prefer one line when the specific language and text width fit comfortably. For longer Latin or Cyrillic text, "
    "allow one natural grammatical line break only; never split inside a word. Use at most two lines."
)

DEFAULT_ADDITIONAL_PROMPT = (
    "High resolution, standalone title text only. The letterforms must be heavy and bold, with crisp hard edges. "
    "No drop shadow, outer glow, halo, blur, backing plate, brush stroke, banner, badge, ink splash, or non-text carrier element. "
    "Centered composition, complete and uncropped, ready as an isolated title layer for poster compositing."
)

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
    model = get_enabled_model(POSTER_TITLE_IMAGE_RESPONSE_MODEL_ID)
    if model is None:
        raise AppError("MODEL_NOT_AVAILABLE", f"模型不可用: {POSTER_TITLE_IMAGE_RESPONSE_MODEL_ID}")
    return model.provider_model


def _load_reference_image(item: PosterTitleImageItemParams) -> ImageInput:
    ref = canonical_ref_from_cpp_oss_url_ref(
        item.reference_image.model_dump(),
        allowed_content_types={"image/png", "image/jpeg", "image/webp"},
    )
    data = storage.read_bytes(bucket=ref.bucket, key=ref.key, region=ref.region)
    if len(data) > POSTER_TITLE_IMAGE_MAX_INPUT_BYTES:
        raise AppError("INPUT_TOO_LARGE", "reference image exceeds poster_title_image limit")
    if sha256_digest(data) != ref.content_hash:
        raise AppError("INPUT_HASH_MISMATCH", "reference image sha256 mismatch")
    if ref.content_type is None:
        raise AppError("INVALID_INPUT", "reference image content_type is required")
    return ImageInput(data=data, content_type=ref.content_type, detail="high")


async def _probe_style(
    reference_image: ImageInput,
    prompt: str,
    *,
    caller_id: str,
    scope_id: str,
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
        attempt_id=attempt_id,
        job_type=job.job_type,
        model_id=POSTER_TITLE_IMAGE_RESPONSE_MODEL_ID,
        prompt=prompt,
        reference_images=[reference_image],
    )
    text = result.text.strip()
    if text:
        return text
    raise AppError("MODEL_OUTPUT_INVALID", "style probe did not return text")


def _title_prompt(item: PosterTitleImageItemParams, *, language_name: str, style_desc: str) -> str:
    overrides = item.prompt_overrides
    layout_rules = overrides.layout_rules if overrides and overrides.layout_rules else DEFAULT_LAYOUT_RULES
    additional_prompt = (
        overrides.additional_prompt if overrides and overrides.additional_prompt else DEFAULT_ADDITIONAL_PROMPT
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


def _output_key(job: Job, item: PosterTitleImageItemParams, image_index: int) -> str:
    output_target = output_target_from_job(job)
    prefix = output_target["oss_prefix"].strip("/")
    filename = "title-layer.png" if image_index == 1 else f"title-layer-{image_index}.png"
    key = f"poster-title/{job.id}/{item.item_id}/{filename}"
    return f"{prefix}/{key}" if prefix else key


def _item_error(exc: Exception) -> PosterTitleImageError:
    if isinstance(exc, AppError):
        return PosterTitleImageError(code=exc.code, message=exc.message, details=exc.details)
    return PosterTitleImageError(
        code="MODEL_CALL_FAILED",
        message="image provider failed",
        details={"type": type(exc).__name__, "failure_phase": "image_generation"},
    )


@register_job_type
class PosterTitleImageJob(JobExecutor):
    name = "poster_title_image"
    params_schema = PosterTitleImageParams
    runtime_fields_schema_name = "PosterTitleImageRuntimeFields"
    canonical_result_schema = PosterTitleImageResult
    public_result_schema = PosterTitleImageResult
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
        }
    )

    def runtime_job_fields(self, job_params: dict[str, Any]) -> dict[str, Any]:
        params = PosterTitleImageParams.model_validate(job_params)
        model_id = params.items[0].model_id if params.items else "gpt-image-2"
        return PosterTitleImageRuntimeFields(model_id=model_id).model_dump()

    def validate_normalized_job_params(self, job_params: dict[str, Any]) -> None:
        params = PosterTitleImageParams.model_validate(job_params)
        IMAGE_MODEL_GATE.resolve(params.items[0].model_id, require_edit=True)
        for item in params.items:
            require_supported_language(item.language)
            canonical_ref_from_cpp_oss_url_ref(
                item.reference_image.model_dump(),
                allowed_content_types={"image/png", "image/jpeg", "image/webp"},
            )

    async def _execute(self, job: Job, db: AsyncSession) -> dict[str, Any] | None:
        params = PosterTitleImageParams.model_validate(job.job_params)
        attempt_id = job.active_attempt_id
        if attempt_id is None:
            raise AppError("JOB_RUNTIME_NOT_SUPPORTED", "poster_title_image requires active_attempt_id")
        request_id = trigger_request_id_from_job(job)
        ai_scope_id = ai_billing_scope_id_from_job(job)
        output_target = output_target_from_job(job)
        response_model = _response_provider_model()

        started = time.monotonic()
        ai_model_ms = 0
        result_items: list[PosterTitleImageResultItem] = []
        for item in params.items:
            item_ai_started = time.monotonic()
            images: list[PosterTitleImageImage] = []
            try:
                language_name = require_supported_language(item.language).display_name
                reference_image = _load_reference_image(item)
                style_prompt = (
                    item.prompt_overrides.style_probe
                    if item.prompt_overrides and item.prompt_overrides.style_probe
                    else STYLE_PROBE_PROMPT
                )
                style_desc = await _probe_style(
                    reference_image,
                    style_prompt,
                    caller_id=job.caller_id,
                    scope_id=str(ai_scope_id),
                    request_id=request_id,
                    job=job,
                    attempt_id=attempt_id,
                )
                prompt = _title_prompt(item, language_name=language_name, style_desc=style_desc)
                for image_index in range(1, item.model_options.draw_count + 1):
                    generated = await generate_image_with_ledger(
                        caller_id=job.caller_id,
                        scope_type="job",
                        scope_id=str(ai_scope_id),
                        operation="poster_title_image.generate_title",
                        step_name="image_generation",
                        request_id=request_id,
                        job_id=job.id,
                        attempt_id=attempt_id,
                        job_type=job.job_type,
                        model_id=item.model_id,
                        response_model=response_model,
                        prompt=prompt,
                        reference_images=[reference_image],
                        size=item.model_options.size,
                        quality=item.model_options.quality,
                        background="auto",
                        output_format="png",
                    )
                    if len(generated.images) != 1:
                        raise AppError("MODEL_OUTPUT_INVALID", "image provider returned unexpected image count")
                    image_bytes = remove_green_background(generated.images[0])
                    key = _output_key(job, item, image_index)
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
                result_items.append(
                    PosterTitleImageResultItem(
                        item_id=item.item_id,
                        language=item.language,
                        status="succeeded",
                        images=images,
                        error=None,
                    )
                )
            except Exception as exc:
                result_items.append(
                    PosterTitleImageResultItem(
                        item_id=item.item_id,
                        language=item.language,
                        status="failed",
                        images=[],
                        error=_item_error(exc),
                    )
                )
            finally:
                ai_model_ms += int((time.monotonic() - item_ai_started) * 1000)

        succeeded = sum(1 for item in result_items if item.status == "succeeded")
        failed = sum(1 for item in result_items if item.status == "failed")
        result = PosterTitleImageResult(
            batch_summary=PosterTitleImageBatchSummary(
                total=len(result_items),
                succeeded=succeeded,
                failed=failed,
                running=0,
                pending=0,
            ),
            items=result_items,
            duration_ms=PosterTitleImageDurationMs(
                ai_model=ai_model_ms,
                total=int((time.monotonic() - started) * 1000),
            ),
        )
        if succeeded == 0:
            raise AppError(
                "ALL_ITEMS_FAILED",
                "all batch items failed",
                details={
                    "failure_phase": "batch_execution",
                    "items": [item.model_dump() for item in result.items],
                },
            )
        if failed:
            raise AppError(
                "JOB_EXECUTION_FAILED",
                "poster_title_image requires all items to succeed",
                details={"failure_phase": "batch_execution", "result": result.model_dump()},
            )
        return result.model_dump()
