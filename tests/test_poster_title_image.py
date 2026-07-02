import io
import logging
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from PIL import Image

from app.core.config import settings
from app.core.exceptions import AppError
from app.core.logging import LogEvent
from app.integrations.ai_adapters.base import ImageGenerationResult, ImageInput, TextGenerationResult
from app.integrations.image import (
    POSTER_TITLE_IMAGE_REFERENCE_ALLOWED_CONTENT_TYPES,
    POSTER_TITLE_IMAGE_REFERENCE_MAX_BYTES,
    POSTER_TITLE_IMAGE_REFERENCE_MAX_WIDTH,
    POSTER_TITLE_IMAGE_REFERENCE_POLICY,
    remove_green_background,
    transparent_title_layer_from_green_screen_bytes,
    transparent_title_layer_from_green_screen_file,
    transparent_title_layer_from_green_screen_oss_url,
    validate_image_bytes,
)
from app.integrations.object_storage import bare_sha256, sha256_digest
from app.integrations.storage import LocalObjectStorage
from app.jobs.types.poster_title_image import PosterTitleImageJob
from app.jobs.types.poster_title_image.errors import (
    POSTER_TITLE_IMAGE_DRAW_COUNT_EXCEEDS_LIMIT,
    POSTER_TITLE_IMAGE_REFERENCE_INVALID,
)
from app.models.job import Job
from app.schemas.billing import BillingEnvelope
from app.schemas.jobs import (
    CreateJobRequest,
    JobEnvelope,
    POSTER_TITLE_IMAGE_MAX_TITLE_LINES,
    PosterTitleImageParams,
    PosterTitleImageStyleProbeRuntimeFields,
)
from app.services.billing import job_cost_from_billing
from app.services.job_runtime import build_runtime_snapshot, payload_hash, write_runtime_json
from app.services.ai_capability_kernel import ModelGate


def _png_bytes(color=(0, 255, 0, 255), accent=(255, 0, 0, 255)) -> bytes:
    image = Image.new("RGBA", (40, 40), color)
    for x in range(16, 24):
        for y in range(16, 24):
            image.putpixel((x, y), accent)
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def _transparent_reference_png_bytes(size=(40, 40), accent=(255, 0, 0, 255)) -> bytes:
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    width, height = size
    for x in range(width // 2 - 4, width // 2 + 4):
        for y in range(height // 2 - 4, height // 2 + 4):
            image.putpixel((x, y), accent)
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def _jpeg_bytes() -> bytes:
    image = Image.new("RGB", (40, 40), (255, 255, 255))
    for x in range(16, 24):
        for y in range(16, 24):
            image.putpixel((x, y), (255, 0, 0))
    buf = io.BytesIO()
    image.save(buf, format="JPEG")
    return buf.getvalue()


def _webp_bytes() -> bytes:
    image = Image.new("RGB", (40, 40), (255, 255, 255))
    for x in range(16, 24):
        for y in range(16, 24):
            image.putpixel((x, y), (255, 0, 0))
    buf = io.BytesIO()
    image.save(buf, format="WEBP")
    return buf.getvalue()


def _transparent_palette_png_bytes() -> bytes:
    image = Image.new("P", (40, 40), 0)
    palette = [0, 0, 0, 255, 0, 0] + [0, 0, 0] * 254
    image.putpalette(palette)
    image.info["transparency"] = 0
    for x in range(16, 24):
        for y in range(16, 24):
            image.putpixel((x, y), 1)
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def _allowed_reference_bucket() -> str:
    return settings.job.poster_title_image_allowed_oss_buckets[0]


def _allowed_reference_region() -> str:
    return settings.job.poster_title_image_allowed_oss_regions[0]


def _url_ref(
    key: str,
    data: bytes,
    *,
    bucket: str | None = None,
    region: str | None = None,
    content_type: str = "image/png",
) -> dict:
    bucket = bucket or _allowed_reference_bucket()
    region = region or _allowed_reference_region()
    return {
        "public_url": f"https://{bucket}.oss-{region}.aliyuncs.com/{key}",
        "internal_url": f"https://{bucket}.oss-{region}-internal.aliyuncs.com/{key}",
        "content_type": content_type,
        "sha256": bare_sha256(sha256_digest(data)),
    }


def _result_image(key: str, data: bytes, *, width: int = 40, height: int = 40) -> dict:
    return {"object": _url_ref(key, data), "width": width, "height": height}


def _params(ref: dict, *, model_id: str | None = None) -> dict:
    item = {
        "item_id": "es",
        "language": "es",
        "title_text": "Cuando el amor se alejo",
        "model_options": {
            "size": "auto",
            "quality": "high",
            "draw_count": 1,
            "background": "transparent",
            "output_format": "png",
        },
        "reference_image": dict(ref),
    }
    if model_id is not None:
        item["model_id"] = model_id
    return {
        "items": [item]
    }


def _job_params_fields(params: dict) -> dict:
    return {
        "job_params_ref": {
            "storage": "db_inline",
            "type": "json",
            "name": "job_params",
            "payload": params,
        },
        "job_params_hash": payload_hash(params),
    }


def _runtime_ref(*, job_type: str, params: dict, runtime_fields: dict, output_target: dict) -> dict:
    return write_runtime_json(
        None,
        "runtime",
        build_runtime_snapshot(
            job_type=job_type,
            job_params_hash=payload_hash(params),
            runtime_fields=runtime_fields,
            output_target=output_target,
        ),
    )


def _params_for_item_count(ref: dict, count: int, *, language: str = "en") -> dict:
    base = _params(ref)["items"][0]
    return {
        "items": [
            {
                **base,
                "item_id": f"item-{index}",
                "language": language,
                "title_text": f"title {index}",
            }
            for index in range(count)
        ]
    }


def test_poster_title_image_params_apply_delivery_contract_constraints():
    ref = _url_ref("reference/title.png", b"x")
    params = PosterTitleImageParams.model_validate(_params(ref))

    assert params.items[0].model_options.background == "transparent"

    explicit = PosterTitleImageParams.model_validate(_params(ref, model_id="gpt-image-2"))
    assert explicit.items[0].model_id == "gpt-image-2"

    invalid = _params(ref)
    second = {**invalid["items"][0], "item_id": "fr", "language": "fr", "model_id": "other-image-model"}
    invalid["items"][0]["model_id"] = "gpt-image-2"
    invalid["items"].append(second)
    with pytest.raises(ValueError, match="model_id"):
        PosterTitleImageParams.model_validate(invalid)

    jpeg_ref = _url_ref("reference/title.jpg", b"x", content_type="image/jpeg")
    webp_ref = _url_ref("reference/title.webp", b"x", content_type="image/webp")
    PosterTitleImageParams.model_validate(_params(jpeg_ref))
    PosterTitleImageParams.model_validate(_params(webp_ref))

    invalid = _params(ref)
    invalid["items"][0]["reference_image"]["content_type"] = "image/gif"
    with pytest.raises(ValueError, match="content_type"):
        PosterTitleImageParams.model_validate(invalid)

    invalid = _params(ref)
    invalid["items"][0]["model_options"]["output_format"] = "jpeg"
    with pytest.raises(ValueError, match="png"):
        PosterTitleImageParams.model_validate(invalid)

    invalid = _params(ref)
    invalid["items"][0]["model_options"]["background"] = "auto"
    with pytest.raises(ValueError, match="transparent"):
        PosterTitleImageParams.model_validate(invalid)

    multiline = _params(ref)
    multiline["items"][0]["title_text"] = "AI美术封面2\nhuanghang"
    validated = PosterTitleImageParams.model_validate(multiline)
    assert validated.items[0].title_text == "AI美术封面2\nhuanghang"

    invalid = _params(ref)
    invalid["items"][0]["title_text"] = "AI美术封面2<br />huanghang"
    with pytest.raises(ValueError, match="HTML line break"):
        PosterTitleImageParams.model_validate(invalid)

    invalid = _params(ref)
    invalid["items"][0]["title_text"] = "AI美术封面2\n\nhuanghang"
    with pytest.raises(ValueError, match=rf"at most {POSTER_TITLE_IMAGE_MAX_TITLE_LINES} lines"):
        PosterTitleImageParams.model_validate(invalid)

    invalid = _params(ref)
    invalid["items"][0]["title_text"] = "AI美术封面2\n "
    with pytest.raises(ValueError, match="must not be empty"):
        PosterTitleImageParams.model_validate(invalid)

    invalid = _params(ref)
    invalid["items"][0]["title_text"] = "AI美术封面2\r\nhuanghang"
    with pytest.raises(ValueError, match="LF"):
        PosterTitleImageParams.model_validate(invalid)

    invalid = _params(ref)
    invalid["items"][0]["title_text"] = "AI美术封面2\u2028huanghang"
    with pytest.raises(ValueError, match="LF"):
        PosterTitleImageParams.model_validate(invalid)

    shared_language = _params(ref)
    shared_language["items"][0]["language"] = "en"
    PosterTitleImageParams.model_validate(shared_language)

    invalid = _params(ref)
    invalid["items"][0]["language"] = "id"
    with pytest.raises(ValueError, match="language"):
        PosterTitleImageParams.model_validate(invalid)


def test_poster_title_image_runtime_fields_preserve_system_alias():
    from app.jobs.types.poster_title_image import (
        PosterTitleImageGenerateItemJob,
        PosterTitleImageJoinJob,
        PosterTitleImageStyleProbeJob,
    )

    ref = _url_ref("reference/title.png", b"x")
    item = _params(ref)["items"][0]
    style_probe_params = {
        "style_key": "style-1",
        "reference_image": ref,
        "style_prompt": "describe style",
        "style_probe_model_id": "gpt-5.5",
        "image_adapter": "openai_responses",
    }
    generate_item_params = {
        "item": item,
        "probe_node_key": "probe.0",
        "style_probe_model_id": "gpt-5.5",
        "image_adapter": "openai_responses",
    }

    runtime_fields = [
        PosterTitleImageJob().runtime_job_fields(_params(ref)),
        PosterTitleImageStyleProbeJob().runtime_job_fields(style_probe_params),
        PosterTitleImageGenerateItemJob().runtime_job_fields(generate_item_params),
        PosterTitleImageJoinJob().runtime_job_fields({"items": [item]}),
    ]

    for fields in runtime_fields:
        assert "_system" not in fields
        assert "system" not in fields

    validated = PosterTitleImageStyleProbeRuntimeFields.model_validate(
        {
            **runtime_fields[1],
            "_system": {"trigger_request_id": "req-probe-1"},
        }
    )

    assert validated.system is not None
    assert validated.system.trigger_request_id == "req-probe-1"
    assert validated.model_dump(by_alias=True, exclude_none=True) == {
        **runtime_fields[1],
        "_system": {"trigger_request_id": "req-probe-1"},
    }


def test_poster_title_image_params_allow_duplicate_language_with_unique_item_id():
    ref = _url_ref("reference/title.png", b"x")

    params = PosterTitleImageParams.model_validate(_params_for_item_count(ref, 2, language="en"))

    assert [item.item_id for item in params.items] == ["item-0", "item-1"]
    assert [item.language for item in params.items] == ["en", "en"]


def test_poster_title_image_params_still_reject_duplicate_item_id():
    ref = _url_ref("reference/title.png", b"x")
    params = _params_for_item_count(ref, 2, language="en")
    params["items"][1]["item_id"] = "item-0"

    with pytest.raises(ValueError, match="item_id"):
        PosterTitleImageParams.model_validate(params)


def test_poster_title_image_title_prompt_allows_automatic_wrapping_without_newline():
    from app.jobs.types.poster_title_image.executor import _title_prompt

    ref = _url_ref("reference/title.png", b"x")
    item = PosterTitleImageParams.model_validate(_params(ref)).items[0]

    prompt = _title_prompt(
        item,
        language_name="Spanish",
        style_desc="heavy stone letters",
        default_prompt_blocks={
            "layout_rules": "caller layout preference",
            "additional_prompt": "additional quality rules",
        },
    )

    assert "No caller-specified hard line break is present" in prompt
    assert "Wrap the title naturally within the title area" in prompt
    assert "Render the title as exactly one line" not in prompt
    assert "Do not add line breaks or split the text" not in prompt
    assert f"Maximum lines: {POSTER_TITLE_IMAGE_MAX_TITLE_LINES}" in prompt
    assert "caller layout preference" in prompt
    assert "Caller-specified hard line breaks are present" not in prompt


def test_poster_title_image_title_prompt_uses_configured_max_title_lines(monkeypatch):
    from app.jobs.types.poster_title_image import executor as poster_executor

    ref = _url_ref("reference/title.png", b"x")
    item = PosterTitleImageParams.model_validate(_params(ref)).items[0]
    monkeypatch.setattr(poster_executor, "POSTER_TITLE_IMAGE_MAX_TITLE_LINES", 3)

    prompt = poster_executor._title_prompt(
        item,
        language_name="Spanish",
        style_desc="heavy stone letters",
        default_prompt_blocks={
            "layout_rules": "caller layout preference",
            "additional_prompt": "additional quality rules",
        },
    )

    assert "Maximum lines: 3" in prompt


def test_poster_title_image_title_prompt_preserves_caller_hard_line_breaks():
    from app.jobs.types.poster_title_image.executor import _title_prompt

    ref = _url_ref("reference/title.png", b"x")
    params = _params(ref)
    params["items"][0]["title_text"] = "AI美术封面2\nhuanghang"
    params["items"][0]["prompt_overrides"] = {
        "layout_rules": "Keep the title centered with generous side margins.",
    }
    item = PosterTitleImageParams.model_validate(params).items[0]

    prompt = _title_prompt(
        item,
        language_name="Spanish",
        style_desc="heavy stone letters",
        default_prompt_blocks={
            "layout_rules": "default layout preference",
            "additional_prompt": "additional quality rules",
        },
    )

    assert "Caller-specified hard line breaks are present" in prompt
    assert "The caller's LF characters define both the line count and the hard line break positions." in prompt
    assert "Line 1: AI美术封面2" in prompt
    assert "Line 2: huanghang" in prompt
    assert "Do not merge lines" in prompt
    assert "Do not merge lines, reorder lines, add extra line breaks, or split any line further." in prompt
    assert "This contract overrides any conflicting layout preference." in prompt
    assert "Keep the title centered with generous side margins." in prompt
    assert prompt.index("Keep the title centered") < prompt.index("Line break contract")


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("layout_rules", "render everything on one line"),
        ("layout_rules", "Break the title after the first word."),
        ("layout_rules", "Keep the title on 2 rows."),
        ("layout_rules", "Put the title on separate rows."),
        ("layout_rules", "Arrange the title in two stacked rows."),
        ("layout_rules", "Put each word on its own row."),
        ("layout_rules", "Reposition the title line breaks for better balance."),
        ("additional_prompt", "Move the line break after the second word."),
        ("additional_prompt", "Make the title multiline."),
        ("layout_rules", "标题必须单行"),
        ("additional_prompt", "preserve two lines"),
        ("additional_prompt", "Break the text after the first word."),
        ("additional_prompt", "请不要换行"),
    ],
)
def test_poster_title_image_prompt_overrides_reject_line_break_control(field_name, value):
    ref = _url_ref("reference/title.png", b"x")
    params = _params(ref)
    params["items"][0]["prompt_overrides"] = {field_name: value}

    with pytest.raises(ValueError, match="must not control title_text line breaks"):
        PosterTitleImageParams.model_validate(params)


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("layout_rules", "Use a single line gold outline around each letter."),
        ("additional_prompt", "Add two lines of tiny decorative sparks beneath the title."),
        ("layout_rules", "Place multiple rows of decorative sparks under the title."),
        ("additional_prompt", "Show two rows of tiny stars behind the title."),
    ],
)
def test_poster_title_image_prompt_overrides_allow_visual_line_descriptions(field_name, value):
    ref = _url_ref("reference/title.png", b"x")
    params = _params(ref)
    params["items"][0]["prompt_overrides"] = {field_name: value}

    validated = PosterTitleImageParams.model_validate(params)

    assert getattr(validated.items[0].prompt_overrides, field_name) == value


@pytest.mark.parametrize("item_id", ["has space", "has=equals", "path/segment", "-leading-dash", ".leading-dot"])
def test_poster_title_image_params_rejects_log_and_path_unsafe_item_id(item_id):
    ref = _url_ref("reference/title.png", b"x")
    params = _params(ref)
    params["items"][0]["item_id"] = item_id

    with pytest.raises(ValueError, match="item_id"):
        PosterTitleImageParams.model_validate(params)


def test_poster_title_image_rejects_items_above_config(monkeypatch):
    monkeypatch.setattr(
        "app.jobs.types.poster_title_image.executor.settings",
        SimpleNamespace(
            job=SimpleNamespace(
                poster_title_image_max_items=1,
                poster_title_image_max_draw_count=4,
                poster_title_image_allowed_oss_buckets=("local-dev",),
                poster_title_image_allowed_oss_regions=("local",),
            ),
            registry=SimpleNamespace(
                poster_title_image_style_probe_model_id="gpt-5.5",
                poster_title_image_generation_default_model_id="gpt-image-2",
                poster_title_image_generation_allowed_model_ids=("gpt-image-2",),
            ),
            storage=SimpleNamespace(oss_public_endpoint=""),
        ),
    )
    params = _params_for_item_count(_url_ref("reference/title.png", b"x"), 2, language="en")

    handler = PosterTitleImageJob()
    normalized = handler.normalize_job_params(params)

    with pytest.raises(AppError) as exc:
        handler.validate_normalized_job_params(normalized)

    assert exc.value.code == "INVALID_INPUT"
    assert exc.value.details == {
        "field": "job_params.items",
        "max_items": 1,
        "item_count": 2,
    }


def test_poster_title_image_rejects_draw_count_above_config(monkeypatch):
    monkeypatch.setattr(
        "app.jobs.types.poster_title_image.executor.settings",
        SimpleNamespace(
            job=SimpleNamespace(
                poster_title_image_max_items=50,
                poster_title_image_max_draw_count=1,
                poster_title_image_allowed_oss_buckets=("local-dev",),
                poster_title_image_allowed_oss_regions=("local",),
            ),
            registry=SimpleNamespace(
                poster_title_image_style_probe_model_id="gpt-5.5",
                poster_title_image_generation_default_model_id="gpt-image-2",
                poster_title_image_generation_allowed_model_ids=("gpt-image-2",),
            ),
            storage=SimpleNamespace(oss_public_endpoint=""),
        ),
    )
    params = _params(_url_ref("reference/title.png", b"x"))
    params["items"][0]["model_options"]["draw_count"] = 2

    handler = PosterTitleImageJob()
    normalized = handler.normalize_job_params(params)

    with pytest.raises(AppError) as exc:
        handler.validate_normalized_job_params(normalized)

    assert exc.value.code == POSTER_TITLE_IMAGE_DRAW_COUNT_EXCEEDS_LIMIT
    assert exc.value.details == {"max_draw_count": 1, "draw_count": 2}


def test_poster_title_image_accepts_configured_reference_oss_allowlist(monkeypatch):
    monkeypatch.setattr(
        "app.jobs.types.poster_title_image.executor.settings",
        SimpleNamespace(
            job=SimpleNamespace(
                poster_title_image_max_items=50,
                poster_title_image_max_draw_count=4,
                poster_title_image_allowed_oss_buckets=("cpp-rs-dev",),
                poster_title_image_allowed_oss_regions=("ap-southeast-1",),
            ),
            registry=SimpleNamespace(
                poster_title_image_style_probe_model_id="gpt-5.5",
                poster_title_image_generation_default_model_id="gpt-image-2",
                poster_title_image_generation_allowed_model_ids=("gpt-image-2",),
            ),
            storage=SimpleNamespace(oss_public_endpoint=""),
        ),
    )
    params = _params(
        _url_ref(
            "reference/title.png",
            b"x",
            bucket="cpp-rs-dev",
            region="ap-southeast-1",
        )
    )

    handler = PosterTitleImageJob()
    handler.validate_normalized_job_params(handler.normalize_job_params(params))


@pytest.mark.parametrize(
    "ref",
    [
        _url_ref("reference/title.png", b"x", bucket="not-allowed", region=_allowed_reference_region()),
        _url_ref("reference/title.png", b"x", bucket=_allowed_reference_bucket(), region="not-allowed"),
    ],
)
def test_poster_title_image_rejects_reference_oss_outside_allowlist(ref):
    params = _params(ref)
    validated = PosterTitleImageParams.model_validate(params)

    assert validated.items[0].reference_image.public_url == ref["public_url"]

    handler = PosterTitleImageJob()
    normalized = handler.normalize_job_params(params)

    with pytest.raises(AppError) as exc:
        handler.validate_normalized_job_params(normalized)

    assert exc.value.code == POSTER_TITLE_IMAGE_REFERENCE_INVALID
    assert exc.value.details["source_reason"] == "INVALID_INPUT"


def test_poster_title_image_create_request_does_not_require_runtime_prompt_payload():
    from app.jobs.types.register import register_all_job_types
    from app.services.jobs import _validate_create_request

    register_all_job_types()
    handler, job_params, runtime_fields = _validate_create_request(
        CreateJobRequest(
            client_request_id="poster-1",
            job_type="poster_title_image",
            job_params=_params(_url_ref("reference/title.png", b"x")),
        )
    )

    assert handler.name == "poster_title_image"
    assert job_params["items"][0]["model_id"] == "gpt-image-2"
    assert runtime_fields == {
        "operation": "poster_title_image",
        "style_probe_model_id": "gpt-5.5",
        "generation_model_id": "gpt-image-2",
        "image_adapter": "openai_images",
    }


def test_poster_title_image_model_options_accept_catalog_sizes():
    ref = _url_ref("reference/title.png", b"x")
    allowed_sizes = (
        "1024x1024",
        "1536x1024",
        "1024x1536",
        "auto",
    )

    for size in allowed_sizes:
        params = _params(ref)
        params["items"][0]["model_options"]["size"] = size

        validated = PosterTitleImageParams.model_validate(params)

        assert validated.items[0].model_options.size == size


def test_poster_title_image_create_request_preserves_title_text_line_break():
    from app.jobs.types.register import register_all_job_types
    from app.services.jobs import _validate_create_request

    register_all_job_types()
    params = _params(_url_ref("reference/title.png", b"x"))
    params["items"][0]["title_text"] = "AI美术封面2\nhuanghang"

    _handler, job_params, _runtime_fields = _validate_create_request(
        CreateJobRequest(
            client_request_id="poster-multiline-1",
            job_type="poster_title_image",
            job_params=params,
        )
    )

    assert job_params["items"][0]["title_text"] == "AI美术封面2\nhuanghang"


def test_poster_title_image_create_request_rejects_html_line_break():
    from app.jobs.types.register import register_all_job_types
    from app.services.jobs import _validate_create_request

    register_all_job_types()
    params = _params(_url_ref("reference/title.png", b"x"))
    params["items"][0]["title_text"] = "AI美术封面2<br />huanghang"

    with pytest.raises(AppError) as exc:
        _validate_create_request(
            CreateJobRequest(
                client_request_id="poster-html-break-1",
                job_type="poster_title_image",
                job_params=params,
            )
        )

    assert exc.value.code == "INVALID_INPUT"
    assert exc.value.message == "job_params does not match job_type schema"


def test_poster_title_image_create_request_rejects_unicode_line_separator():
    from app.jobs.types.register import register_all_job_types
    from app.services.jobs import _validate_create_request

    register_all_job_types()
    params = _params(_url_ref("reference/title.png", b"x"))
    params["items"][0]["title_text"] = "AI美术封面2\u2028huanghang"

    with pytest.raises(AppError) as exc:
        _validate_create_request(
            CreateJobRequest(
                client_request_id="poster-unicode-break-1",
                job_type="poster_title_image",
                job_params=params,
            )
        )

    assert exc.value.code == "INVALID_INPUT"
    assert exc.value.message == "job_params does not match job_type schema"


@pytest.mark.parametrize(
    "title_text",
    [
        "AI美术封面2\r\nhuanghang",
        "AI美术封面2\n ",
        "AI美术封面2\nhuanghang\nsubtitle",
    ],
)
def test_poster_title_image_create_request_rejects_invalid_title_text_line_breaks(title_text):
    from app.jobs.types.register import register_all_job_types
    from app.services.jobs import _validate_create_request

    register_all_job_types()
    params = _params(_url_ref("reference/title.png", b"x"))
    params["items"][0]["title_text"] = title_text

    with pytest.raises(AppError) as exc:
        _validate_create_request(
            CreateJobRequest(
                client_request_id="poster-invalid-line-break-1",
                job_type="poster_title_image",
                job_params=params,
            )
        )

    assert exc.value.code == "INVALID_INPUT"
    assert exc.value.message == "job_params does not match job_type schema"


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("layout_rules", "render everything on one line"),
        ("additional_prompt", "请不要换行"),
    ],
)
def test_poster_title_image_create_request_rejects_prompt_override_line_break_control(field_name, value):
    from app.jobs.types.register import register_all_job_types
    from app.services.jobs import _validate_create_request

    register_all_job_types()
    params = _params(_url_ref("reference/title.png", b"x"))
    params["items"][0]["prompt_overrides"] = {field_name: value}

    with pytest.raises(AppError) as exc:
        _validate_create_request(
            CreateJobRequest(
                client_request_id="poster-invalid-prompt-line-break-1",
                job_type="poster_title_image",
                job_params=params,
            )
        )

    assert exc.value.code == "INVALID_INPUT"
    assert exc.value.message == "job_params does not match job_type schema"


def test_poster_title_image_create_request_accepts_shared_language_outside_legacy_subset():
    from app.jobs.types.register import register_all_job_types
    from app.services.jobs import _validate_create_request

    register_all_job_types()
    params = _params(_url_ref("reference/title.png", b"x"))
    params["items"][0]["language"] = "en"
    handler, job_params, runtime_fields = _validate_create_request(
        CreateJobRequest(
            client_request_id="poster-en-1",
            job_type="poster_title_image",
            job_params=params,
        )
    )

    assert handler.name == "poster_title_image"
    assert job_params["items"][0]["language"] == "en"
    assert runtime_fields["operation"] == "poster_title_image"


def test_poster_title_image_create_request_accepts_allowed_caller_model_id():
    from app.jobs.types.register import register_all_job_types
    from app.services.jobs import _validate_create_request

    register_all_job_types()
    handler, job_params, runtime_fields = _validate_create_request(
        CreateJobRequest(
            client_request_id="poster-1",
            job_type="poster_title_image",
            job_params=_params(_url_ref("reference/title.png", b"x"), model_id="gpt-image-2"),
        )
    )

    assert handler.name == "poster_title_image"
    assert job_params["items"][0]["model_id"] == "gpt-image-2"
    assert runtime_fields["generation_model_id"] == "gpt-image-2"


def test_poster_title_image_create_request_rejects_model_outside_allowlist():
    from app.jobs.types.register import register_all_job_types
    from app.services.jobs import _validate_create_request

    register_all_job_types()
    with pytest.raises(AppError) as exc:
        _validate_create_request(
            CreateJobRequest(
                client_request_id="poster-1",
                job_type="poster_title_image",
                job_params=_params(_url_ref("reference/title.png", b"x"), model_id="gpt-image-custom"),
            )
        )

    assert exc.value.code == "INVALID_INPUT"
    assert exc.value.details["allowed_model_ids"] == ["gpt-image-2"]


def test_poster_title_image_create_request_rejects_unavailable_configured_generation_model(monkeypatch):
    from app.jobs.types.register import register_all_job_types
    from app.services.jobs import _validate_create_request

    monkeypatch.setattr(
        "app.jobs.types.poster_title_image.executor.settings",
        SimpleNamespace(
            job=SimpleNamespace(
                poster_title_image_max_items=50,
                poster_title_image_max_draw_count=4,
                poster_title_image_allowed_oss_buckets=("local-dev",),
                poster_title_image_allowed_oss_regions=("local",),
            ),
            registry=SimpleNamespace(
                poster_title_image_style_probe_model_id="gpt-5.5",
                poster_title_image_generation_default_model_id="not-an-image-model",
                poster_title_image_generation_allowed_model_ids=("not-an-image-model",),
            ),
        ),
    )
    monkeypatch.setattr(
        "app.jobs.types.poster_title_image.executor.poster_title_image_generation_default_model_id",
        lambda: "not-an-image-model",
    )
    monkeypatch.setattr(
        "app.jobs.types.poster_title_image.executor.poster_title_image_generation_allowed_model_ids",
        lambda: ("not-an-image-model",),
    )
    register_all_job_types()
    with pytest.raises(Exception, match="模型不可用"):
        _validate_create_request(
            CreateJobRequest(
                client_request_id="poster-1",
                job_type="poster_title_image",
                job_params=_params(_url_ref("reference/title.png", b"x")),
            )
        )


def test_style_probe_response_model_supports_reference_image_input():
    from app.jobs.model_selection import poster_title_image_style_probe_model_id

    model_id = poster_title_image_style_probe_model_id()
    result = ModelGate().resolve_multimodal_text(
        model_id,
        required_media_types=POSTER_TITLE_IMAGE_REFERENCE_ALLOWED_CONTENT_TYPES,
    )

    assert result.resolved_model.model_id == model_id
    assert result.resolved_model.provider_model == "gpt-5.5"
    assert result.model.features["supports_image_generation_tool"] is True


def test_poster_title_image_response_model_requires_image_generation_tool(monkeypatch):
    from app.jobs.types.poster_title_image.executor import _validate_style_probe_model

    monkeypatch.setattr(
        "app.jobs.types.poster_title_image.executor.poster_title_image_style_probe_model_id",
        lambda: "gpt-4o",
    )
    monkeypatch.setattr(
        "app.jobs.types.poster_title_image.executor.poster_title_image_generation_image_adapter",
        lambda: "openai_responses",
    )

    with pytest.raises(AppError, match="image_generation tool"):
        _validate_style_probe_model(required_media_types={"image/png"})


def test_poster_title_image_images_adapter_does_not_require_image_generation_tool(monkeypatch):
    from app.jobs.types.poster_title_image.executor import _validate_style_probe_model

    monkeypatch.setattr(
        "app.jobs.types.poster_title_image.executor.poster_title_image_style_probe_model_id",
        lambda: "gpt-4o",
    )
    monkeypatch.setattr(
        "app.jobs.types.poster_title_image.executor.poster_title_image_generation_image_adapter",
        lambda: "openai_images",
    )

    _validate_style_probe_model(required_media_types={"image/png"})


def test_remove_green_background_matches_poc_chroma_key_strategy():
    processed = remove_green_background(_png_bytes())
    result = Image.open(io.BytesIO(processed.data)).convert("RGBA")

    assert processed.width == 40
    assert processed.height == 40
    assert result.size == (processed.width, processed.height)
    assert result.getpixel((0, 0))[3] == 0
    assert result.getpixel((20, 20))[3] == 255


def test_transparent_title_layer_postprocess_supports_bytes_file_and_oss_url(tmp_path):
    data = _png_bytes()
    local_path = tmp_path / "title.png"
    local_path.write_bytes(data)
    local_storage = LocalObjectStorage(tmp_path)
    local_storage.write_bytes(
        bucket="local-dev",
        region="local",
        key="generated/title.png",
        data=data,
        content_type="image/png",
    )

    from_bytes = transparent_title_layer_from_green_screen_bytes(data)
    from_file = transparent_title_layer_from_green_screen_file(local_path)
    from_oss_url = transparent_title_layer_from_green_screen_oss_url(
        "https://local-dev.oss-local-internal.aliyuncs.com/generated/title.png",
        object_storage=local_storage,
    )

    for output in [from_bytes, from_file, from_oss_url]:
        assert output.width == 40
        assert output.height == 40
        result = Image.open(io.BytesIO(output.data)).convert("RGBA")
        assert result.size == (output.width, output.height)
        assert result.getpixel((0, 0))[3] == 0
        assert result.getpixel((20, 20))[3] == 255


def test_validate_image_bytes_requires_real_format_and_matching_content_type():
    data = _png_bytes()

    result = validate_image_bytes(data, content_type="image/png", policy=POSTER_TITLE_IMAGE_REFERENCE_POLICY)

    assert result.width == 40
    assert result.height == 40
    assert result.content_type == "image/png"

    with pytest.raises(AppError, match="content_type"):
        validate_image_bytes(data, content_type="image/webp", policy=POSTER_TITLE_IMAGE_REFERENCE_POLICY)

    with pytest.raises(AppError, match="decodable"):
        validate_image_bytes(b"not an image", content_type="image/png", policy=POSTER_TITLE_IMAGE_REFERENCE_POLICY)


def test_validate_image_bytes_accepts_png_jpeg_and_webp():
    palette_png = validate_image_bytes(
        _transparent_palette_png_bytes(),
        content_type="image/png",
        policy=POSTER_TITLE_IMAGE_REFERENCE_POLICY,
    )
    jpeg = validate_image_bytes(_jpeg_bytes(), content_type="image/jpeg", policy=POSTER_TITLE_IMAGE_REFERENCE_POLICY)
    webp = validate_image_bytes(_webp_bytes(), content_type="image/webp", policy=POSTER_TITLE_IMAGE_REFERENCE_POLICY)

    assert [palette_png.content_type, jpeg.content_type, webp.content_type] == [
        "image/png",
        "image/jpeg",
        "image/webp",
    ]


def test_poster_title_image_reference_image_validation_uses_business_error(monkeypatch):
    from app.jobs.types.poster_title_image.executor import _load_reference_image_from_ref

    data = b"not an image"
    monkeypatch.setattr("app.jobs.types.poster_title_image.executor.read_http_url_bytes", lambda *_args, **_kwargs: data)

    with pytest.raises(AppError) as exc:
        _load_reference_image_from_ref(_url_ref("reference/title.png", data))

    assert exc.value.code == POSTER_TITLE_IMAGE_REFERENCE_INVALID
    assert exc.value.details["source_reason"] == "INVALID_INPUT"


def test_poster_title_image_reference_read_uses_public_url_not_output_storage(monkeypatch):
    from app.jobs.types.poster_title_image.executor import _load_reference_image_from_ref

    data = _transparent_reference_png_bytes()
    ref = _url_ref("reference/title.png", data, bucket="cpp-rs-dev", region="ap-southeast-1")
    calls = []

    class NoReadStorage:
        def read_bytes(self, **_kwargs):
            raise AssertionError("reference image must not be read through output storage")

    def fake_read_http_url_bytes(url, **kwargs):
        calls.append((url, kwargs))
        return data

    monkeypatch.setattr(
        "app.jobs.types.poster_title_image.executor.settings",
        SimpleNamespace(
            job=SimpleNamespace(
                poster_title_image_allowed_oss_buckets=("cpp-rs-dev",),
                poster_title_image_allowed_oss_regions=("ap-southeast-1",),
            ),
            storage=SimpleNamespace(oss_public_endpoint=""),
        ),
    )
    monkeypatch.setattr("app.jobs.types.poster_title_image.executor.storage", NoReadStorage())
    monkeypatch.setattr("app.jobs.types.poster_title_image.executor.read_http_url_bytes", fake_read_http_url_bytes)

    result = _load_reference_image_from_ref(ref)

    assert result.data == data
    assert result.content_type == "image/png"
    assert calls == [(ref["public_url"], {"max_bytes": POSTER_TITLE_IMAGE_REFERENCE_MAX_BYTES})]


def test_poster_title_image_reference_accepts_configured_cdn_public_url(monkeypatch):
    from app.jobs.types.poster_title_image.executor import _load_reference_image_from_ref

    data = _transparent_reference_png_bytes()
    ref = _url_ref("reference/title.png", data, bucket="cpp-rs-dev", region="ap-southeast-1")
    ref["public_url"] = "https://aigc-datas.epubgame.com/reference/title.png"
    calls = []

    def fake_read_http_url_bytes(url, **kwargs):
        calls.append((url, kwargs))
        return data

    monkeypatch.setattr(
        "app.jobs.types.poster_title_image.executor.settings",
        SimpleNamespace(
            job=SimpleNamespace(
                poster_title_image_allowed_oss_buckets=("cpp-rs-dev",),
                poster_title_image_allowed_oss_regions=("ap-southeast-1",),
            ),
            storage=SimpleNamespace(oss_public_endpoint="aigc-datas.epubgame.com"),
        ),
    )
    monkeypatch.setattr("app.jobs.types.poster_title_image.executor.read_http_url_bytes", fake_read_http_url_bytes)

    result = _load_reference_image_from_ref(ref)

    assert result.data == data
    assert calls == [(ref["public_url"], {"max_bytes": POSTER_TITLE_IMAGE_REFERENCE_MAX_BYTES})]


def test_validate_image_bytes_rejects_oversized_dimensions():
    policy = replace(POSTER_TITLE_IMAGE_REFERENCE_POLICY, max_width=32)

    with pytest.raises(AppError) as exc:
        validate_image_bytes(_transparent_reference_png_bytes(), content_type="image/png", policy=policy)

    assert exc.value.code == "INPUT_TOO_LARGE"
    assert exc.value.details["max_width"] == 32


def test_validate_image_bytes_rejects_oversized_bytes():
    policy = replace(POSTER_TITLE_IMAGE_REFERENCE_POLICY, max_bytes=1)

    with pytest.raises(AppError) as exc:
        validate_image_bytes(_transparent_reference_png_bytes(), content_type="image/png", policy=policy)

    assert exc.value.code == "INPUT_TOO_LARGE"
    assert exc.value.details["max_bytes"] == 1
    assert POSTER_TITLE_IMAGE_REFERENCE_MAX_BYTES == 20 * 1024 * 1024
    assert POSTER_TITLE_IMAGE_REFERENCE_MAX_WIDTH == 4096


def test_poster_title_image_missing_default_prompt_is_runtime_config_error(monkeypatch):
    from app.jobs.types.poster_title_image.executor import _default_prompt_blocks

    def fake_get_prompt_block_default(job_type, block_key):
        raise RuntimeError(f"missing prompt block: {block_key}")

    monkeypatch.setattr(
        "app.jobs.types.poster_title_image.executor.get_prompt_block_default",
        fake_get_prompt_block_default,
    )

    with pytest.raises(AppError) as exc:
        _default_prompt_blocks()

    assert exc.value.code == "RUNTIME_CONFIG_MISSING"


@pytest.mark.asyncio
async def test_poster_title_image_generate_item_leaf_generates_transparent_title_layer(monkeypatch, tmp_path, caplog):
    from app.jobs.types.poster_title_image import PosterTitleImageGenerateItemJob

    class RecordingLocalObjectStorage(LocalObjectStorage):
        def __init__(self, root):
            super().__init__(root)
            self.write_calls = []

        def write_bytes(
            self,
            *,
            bucket,
            key,
            region,
            data,
            content_type="application/octet-stream",
            content_disposition=None,
        ):
            self.write_calls.append(
                {
                    "bucket": bucket,
                    "key": key,
                    "region": region,
                    "content_type": content_type,
                    "content_disposition": content_disposition,
                }
            )
            return super().write_bytes(
                bucket=bucket,
                key=key,
                region=region,
                data=data,
                content_type=content_type,
                content_disposition=content_disposition,
            )

    local_storage = RecordingLocalObjectStorage(tmp_path)
    reference = _transparent_reference_png_bytes(accent=(0, 0, 255, 255))
    reference_bucket = _allowed_reference_bucket()
    reference_region = _allowed_reference_region()
    output_bucket = "service-output"
    output_region = "service-region"
    output_target = {
        "type": "oss_prefix",
        "oss_bucket": output_bucket,
        "oss_region": output_region,
        "oss_prefix": "ai-jobs/job-1/",
    }
    generated_green = _png_bytes()
    recorded = []
    opened_urls = []

    class _ReferenceResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, size=None):
            return reference if size is None else reference[:size]

    class _ReferenceOpener:
        def open(self, request, *, timeout):
            opened_urls.append(request.full_url)
            return _ReferenceResponse()

    async def fake_generate_image_with_ledger(**kwargs):
        recorded.append(kwargs)
        return ImageGenerationResult(images=[generated_green], usage={"image_count": 1})

    probe_child = SimpleNamespace(
        workflow_node_key="probe.0",
        status="succeeded",
        result={"style_desc": "heavy cracked stone letterforms"},
    )

    async def fake_workflow_children(_job, _db):
        return [probe_child]

    monkeypatch.setattr("app.jobs.types.poster_title_image.executor.storage", local_storage)
    monkeypatch.setattr(
        "app.jobs.adapters.http_url_input.urllib.request.build_opener",
        lambda *_args: _ReferenceOpener(),
    )
    monkeypatch.setattr("app.jobs.types.poster_title_image.executor.output_target_from_job", lambda _job: output_target)
    monkeypatch.setattr("app.jobs.types.poster_title_image.executor._style_probe_provider_model", lambda _model_id: "gpt-5.5")
    monkeypatch.setattr(
        "app.jobs.types.poster_title_image.executor.poster_title_image_generation_image_adapter",
        lambda: "openai_images",
    )
    monkeypatch.setattr("app.jobs.types.poster_title_image.executor._workflow_children", fake_workflow_children)
    monkeypatch.setattr(
        "app.jobs.types.poster_title_image.executor.settings",
        SimpleNamespace(
            job=settings.job,
            storage=SimpleNamespace(oss_public_endpoint="aigc-datas.epubgame.com"),
        ),
    )
    monkeypatch.setattr(
        "app.jobs.types.poster_title_image.executor.generate_image_with_ledger",
        fake_generate_image_with_ledger,
    )

    root_id = uuid.uuid4()
    job_id = uuid.uuid4()
    reference_ref = _url_ref("reference/title.png", reference, bucket=reference_bucket, region=reference_region)
    child_params = {
        "item": _params(reference_ref)["items"][0],
        "probe_node_key": "probe.0",
        "style_probe_model_id": "gpt-5.5",
        "image_adapter": "openai_images",
    }
    job = Job(
        id=job_id,
        caller_id="caller-1",
        client_request_id=None,
        job_type="poster_title_image_generate_item",
        status="running",
        active_attempt_id=uuid.uuid4(),
        root_job_id=root_id,
        workflow_node_key="item.es",
        **_job_params_fields(child_params),
        runtime_ref=_runtime_ref(
            job_type="poster_title_image_generate_item",
            params=child_params,
            runtime_fields={
                "operation": "poster_title_image_generate_item",
                "generation_model_id": "gpt-image-2",
                "style_probe_model_id": "gpt-5.5",
                "image_adapter": "openai_images",
                "_system": {"trigger_request_id": "req-generate-1"},
            },
            output_target=output_target,
        ),
        created_at=datetime.now(timezone.utc),
    )

    caplog.set_level(logging.INFO, logger="app.jobs.types.poster_title_image.executor")
    result = await PosterTitleImageGenerateItemJob()._execute(job, object())

    item = result["item"]
    assert item["status"] == "succeeded"
    image = item["images"][0]
    obj = image["object"]
    assert opened_urls == [reference_ref["public_url"]]
    assert obj["public_url"].startswith("https://aigc-datas.epubgame.com/")
    assert obj["internal_url"].startswith(f"https://{output_bucket}.oss-{output_region}-internal.aliyuncs.com/")
    assert obj["content_type"] == "image/png"
    assert image["width"] == 40
    assert image["height"] == 40
    assert len(recorded) == 1
    assert recorded[0]["model_id"] == "gpt-image-2"
    assert recorded[0]["image_adapter"] == "openai_images"
    assert recorded[0]["response_model"] == "gpt-5.5"
    assert recorded[0]["background"] == "auto"
    assert recorded[0]["output_format"] == "png"
    assert recorded[0]["scope_id"] == str(root_id)
    assert recorded[0]["scope_job_id"] == root_id
    assert recorded[0]["request_id"] == "req-generate-1"
    assert GREEN_BACKGROUND_TEXT in recorded[0]["prompt"]
    assert "poster-title layer" in recorded[0]["prompt"]
    assert "poster title text only" in recorded[0]["prompt"]

    output_key = "ai-jobs/job-1/poster-title/{}/es/title-layer.png".format(root_id)
    assert local_storage.write_calls == [
        {
            "bucket": output_bucket,
            "key": output_key,
            "region": output_region,
            "content_type": "image/png",
            "content_disposition": f'attachment; filename="poster-title-{root_id}-es.png"',
        }
    ]
    written = local_storage.read_bytes(bucket=output_bucket, region=output_region, key=output_key)
    output_image = Image.open(io.BytesIO(written)).convert("RGBA")
    assert output_image.getpixel((0, 0))[3] == 0
    assert output_image.getpixel((20, 20))[3] == 255
    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert f"event={LogEvent.POSTER_TITLE_IMAGE_OBJECT_STORED}" in messages
    assert f"event={LogEvent.POSTER_TITLE_IMAGE_ITEM_COMPLETED}" in messages
    assert f"job_id={job_id}" in messages
    assert f"root_job_id={root_id}" in messages
    assert "trigger_request_id=req-generate-1" in messages
    assert "caller_id=caller-1" in messages
    assert "job_type=poster_title_image_generate_item" in messages
    assert "item_id=es" in messages
    assert "language=es" in messages
    assert "image_index=1" in messages
    assert f"oss_key={output_key}" in messages
    assert "content_type=image/png" in messages
    assert "content_hash=" in messages
    assert "image_count=1" in messages
    assert "operation=poster_title_image.generate_title" in messages
    assert "model_id=gpt-image-2" in messages
    assert "public_url" not in messages
    assert "internal_url" not in messages
    assert "heavy cracked stone letterforms" not in messages
    assert "poster-title layer" not in messages


@pytest.mark.asyncio
async def test_poster_title_image_generate_item_leaf_generates_two_draws(monkeypatch, tmp_path):
    from app.jobs.types.poster_title_image import PosterTitleImageGenerateItemJob

    class RecordingLocalObjectStorage(LocalObjectStorage):
        def __init__(self, root):
            super().__init__(root)
            self.write_calls = []

        def write_bytes(
            self,
            *,
            bucket,
            key,
            region,
            data,
            content_type="application/octet-stream",
            content_disposition=None,
        ):
            self.write_calls.append(
                {
                    "bucket": bucket,
                    "key": key,
                    "region": region,
                    "content_disposition": content_disposition,
                }
            )
            return super().write_bytes(
                bucket=bucket,
                key=key,
                region=region,
                data=data,
                content_type=content_type,
                content_disposition=content_disposition,
            )

    local_storage = RecordingLocalObjectStorage(tmp_path)
    reference = _transparent_reference_png_bytes(accent=(0, 0, 255, 255))
    reference_bucket = _allowed_reference_bucket()
    reference_region = _allowed_reference_region()
    local_storage.write_bytes(
        bucket=reference_bucket,
        region=reference_region,
        key="reference/title.png",
        data=reference,
        content_type="image/png",
    )
    output_target = {
        "type": "oss_prefix",
        "oss_bucket": reference_bucket,
        "oss_region": reference_region,
        "oss_prefix": "ai-jobs/job-1/",
    }
    generated_green = _png_bytes()
    recorded = []

    async def fake_generate_image_with_ledger(**kwargs):
        recorded.append(kwargs)
        return ImageGenerationResult(images=[generated_green], usage={"image_count": 1})

    async def fake_workflow_children(_job, _db):
        return [
            SimpleNamespace(
                workflow_node_key="probe.0",
                status="succeeded",
                result={"style_desc": "heavy cracked stone letterforms"},
            )
        ]

    monkeypatch.setattr("app.jobs.types.poster_title_image.executor.storage", local_storage)
    monkeypatch.setattr(
        "app.jobs.types.poster_title_image.executor.read_http_url_bytes",
        lambda *_args, **_kwargs: reference,
    )
    monkeypatch.setattr("app.jobs.types.poster_title_image.executor.output_target_from_job", lambda _job: output_target)
    monkeypatch.setattr("app.jobs.types.poster_title_image.executor._style_probe_provider_model", lambda _model_id: "gpt-5.5")
    monkeypatch.setattr("app.jobs.types.poster_title_image.executor._workflow_children", fake_workflow_children)
    monkeypatch.setattr(
        "app.jobs.types.poster_title_image.executor.generate_image_with_ledger",
        fake_generate_image_with_ledger,
    )

    params = _params(_url_ref("reference/title.png", reference))
    params["items"][0]["model_options"]["draw_count"] = 2
    child_params = {
        "item": params["items"][0],
        "probe_node_key": "probe.0",
        "style_probe_model_id": "gpt-5.5",
        "image_adapter": "openai_responses",
    }
    root_id = uuid.uuid4()
    job = Job(
        id=uuid.uuid4(),
        caller_id="caller-1",
        client_request_id=None,
        job_type="poster_title_image_generate_item",
        status="running",
        active_attempt_id=uuid.uuid4(),
        root_job_id=root_id,
        workflow_node_key="item.es",
        **_job_params_fields(child_params),
        runtime_ref=_runtime_ref(
            job_type="poster_title_image_generate_item",
            params=child_params,
            runtime_fields={
                "operation": "poster_title_image_generate_item",
                "generation_model_id": "gpt-image-2",
                "style_probe_model_id": "gpt-5.5",
                "image_adapter": "openai_responses",
            },
            output_target=output_target,
        ),
        created_at=datetime.now(timezone.utc),
    )

    result = await PosterTitleImageGenerateItemJob()._execute(job, object())

    assert len(recorded) == 2
    assert len(result["item"]["images"]) == 2
    assert [(image["width"], image["height"]) for image in result["item"]["images"]] == [(40, 40), (40, 40)]
    keys = [
        "ai-jobs/job-1/poster-title/{}/es/title-layer.png".format(root_id),
        "ai-jobs/job-1/poster-title/{}/es/title-layer-2.png".format(root_id),
    ]
    output_write_calls = [call for call in local_storage.write_calls if call["content_disposition"] is not None]
    assert output_write_calls == [
        {
            "bucket": reference_bucket,
            "key": keys[0],
            "region": reference_region,
            "content_disposition": f'attachment; filename="poster-title-{root_id}-es.png"',
        },
        {
            "bucket": reference_bucket,
            "key": keys[1],
            "region": reference_region,
            "content_disposition": f'attachment; filename="poster-title-{root_id}-es-2.png"',
        },
    ]
    for key in keys:
        written = local_storage.read_bytes(bucket=reference_bucket, region=reference_region, key=key)
        output_image = Image.open(io.BytesIO(written)).convert("RGBA")
        assert output_image.getpixel((0, 0))[3] == 0


@pytest.mark.asyncio
async def test_poster_title_image_join_leaf_preserves_request_item_order(monkeypatch, caplog):
    from app.jobs.types.poster_title_image import PosterTitleImageJoinJob
    from app.jobs.types.poster_title_image.executor import _item_node_key

    ref = _url_ref("reference/title.png", _transparent_reference_png_bytes())
    params = _params(ref)
    second = {**params["items"][0], "item_id": "fr", "language": "fr", "title_text": "Quand l'amour s'eloigne"}
    params["items"].append(second)
    first_result = {
        "item_id": "es",
        "language": "es",
        "status": "succeeded",
        "images": [_result_image("out/es.png", b"es")],
    }
    second_result = {
        "item_id": "fr",
        "language": "fr",
        "status": "succeeded",
        "images": [_result_image("out/fr.png", b"fr")],
    }

    async def fake_workflow_children(_job, _db):
        return [
            SimpleNamespace(
                workflow_node_key=_item_node_key("fr"),
                job_type="poster_title_image_generate_item",
                status="succeeded",
                result={"item": second_result, "duration_ms": {"ai_model": 7, "total": 7}},
            ),
            SimpleNamespace(
                workflow_node_key=_item_node_key("es"),
                job_type="poster_title_image_generate_item",
                status="succeeded",
                result={"item": first_result, "duration_ms": {"ai_model": 5, "total": 5}},
            ),
        ]

    monkeypatch.setattr("app.jobs.types.poster_title_image.executor._workflow_children", fake_workflow_children)
    root_id = uuid.uuid4()
    job = Job(
        id=uuid.uuid4(),
        caller_id="caller-1",
        client_request_id=None,
        job_type="poster_title_image_join",
        status="running",
        active_attempt_id=uuid.uuid4(),
        root_job_id=root_id,
        workflow_node_key="join",
        **_job_params_fields({"items": params["items"]}),
        created_at=datetime.now(timezone.utc),
    )

    caplog.set_level(logging.INFO, logger="app.jobs.types.poster_title_image.executor")
    result = await PosterTitleImageJoinJob()._execute(job, object())

    assert result["batch_summary"] == {"total": 2, "succeeded": 2, "failed": 0, "running": 0, "pending": 0}
    assert [item["item_id"] for item in result["items"]] == ["es", "fr"]
    assert result["duration_ms"]["ai_model"] == 12
    assert result["duration_ms"]["total"] == 12
    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert f"event={LogEvent.POSTER_TITLE_IMAGE_JOIN_COMPLETED}" in messages
    assert f"root_job_id={root_id}" in messages
    assert "trigger_request_id=" in messages
    assert "caller_id=caller-1" in messages
    assert "job_type=poster_title_image_join" in messages
    assert "workflow_node_key=join" in messages
    assert "total=2" in messages
    assert "succeeded=2" in messages
    assert "ai_model_ms=12" in messages
    assert "total_ms=12" in messages


@pytest.mark.asyncio
async def test_poster_title_image_running_result_contains_only_succeeded_items(monkeypatch):
    from app.jobs.types.poster_title_image import PosterTitleImageJob
    from app.jobs.types.poster_title_image.executor import _item_node_key

    ref = _url_ref("reference/title.png", _transparent_reference_png_bytes())
    params = _params(ref)
    params["items"].append(
        {
            **params["items"][0],
            "item_id": "fr",
            "language": "fr",
            "title_text": "Quand l'amour s'eloigne",
        }
    )
    succeeded_item = {
        "item_id": "es",
        "language": "es",
        "status": "succeeded",
        "images": [_result_image("out/es.png", b"es")],
        "error": None,
    }
    root_id = uuid.uuid4()
    children = [
        SimpleNamespace(
            workflow_node_key="probe.0",
            job_type="poster_title_image_style_probe",
            status="succeeded",
            result={"duration_ms": {"ai_model": 3, "total": 4}},
        ),
        SimpleNamespace(
            workflow_node_key=_item_node_key("es"),
            job_type="poster_title_image_generate_item",
            status="succeeded",
            result={"item": succeeded_item, "duration_ms": {"ai_model": 5, "total": 6}},
        ),
        SimpleNamespace(
            workflow_node_key=_item_node_key("fr"),
            job_type="poster_title_image_generate_item",
            status="running",
            result=None,
        ),
    ]

    async def fake_list_internal_children(_db, *, root_job_id, statuses=None):
        assert root_job_id == root_id
        assert statuses is None
        return children

    monkeypatch.setattr("app.repositories.job_repo.JobRepo.list_internal_children", fake_list_internal_children)
    job = Job(
        id=root_id,
        caller_id="caller-1",
        client_request_id="poster-1",
        job_type="poster_title_image",
        status="running",
        progress_percent=55,
        **_job_params_fields(params),
        created_at=datetime.now(timezone.utc),
    )

    result = await PosterTitleImageJob().build_result_snapshot("running", job, object())

    assert result is not None
    assert result["batch_summary"] == {"total": 1, "succeeded": 1, "failed": 0, "running": 0, "pending": 0}
    assert [item["item_id"] for item in result["items"]] == ["es"]
    assert result["items"][0]["images"] == succeeded_item["images"]
    assert result["duration_ms"] == {"ai_model": 8, "total": 10}


@pytest.mark.asyncio
async def test_poster_title_image_running_result_is_null_before_first_succeeded_item(monkeypatch):
    from app.jobs.types.poster_title_image import PosterTitleImageJob

    ref = _url_ref("reference/title.png", _transparent_reference_png_bytes())
    root_id = uuid.uuid4()

    async def fake_list_internal_children(_db, *, root_job_id, statuses=None):
        assert root_job_id == root_id
        return [
            SimpleNamespace(
                workflow_node_key="probe.0",
                job_type="poster_title_image_style_probe",
                status="succeeded",
                result={"duration_ms": {"ai_model": 3, "total": 4}},
            )
        ]

    monkeypatch.setattr("app.repositories.job_repo.JobRepo.list_internal_children", fake_list_internal_children)
    job = Job(
        id=root_id,
        caller_id="caller-1",
        client_request_id="poster-1",
        job_type="poster_title_image",
        status="running",
        progress_percent=30,
        **_job_params_fields(_params(ref)),
        created_at=datetime.now(timezone.utc),
    )

    assert await PosterTitleImageJob().build_result_snapshot("running", job, object()) is None


@pytest.mark.asyncio
async def test_poster_title_image_failed_result_reuses_succeeded_item_subset(monkeypatch):
    from app.jobs.types.poster_title_image import PosterTitleImageJob
    from app.jobs.types.poster_title_image.executor import _item_node_key

    ref = _url_ref("reference/title.png", _transparent_reference_png_bytes())
    params = _params(ref)
    params["items"].append(
        {
            **params["items"][0],
            "item_id": "fr",
            "language": "fr",
            "title_text": "Quand l'amour s'eloigne",
        }
    )
    succeeded_item = {
        "item_id": "es",
        "language": "es",
        "status": "succeeded",
        "images": [_result_image("out/es.png", b"es")],
        "error": None,
    }
    root_id = uuid.uuid4()

    async def fake_list_internal_children(_db, *, root_job_id, statuses=None):
        assert root_job_id == root_id
        return [
            SimpleNamespace(
                workflow_node_key=_item_node_key("es"),
                job_type="poster_title_image_generate_item",
                status="succeeded",
                result={"item": succeeded_item, "duration_ms": {"ai_model": 5, "total": 6}},
            ),
            SimpleNamespace(
                workflow_node_key=_item_node_key("fr"),
                job_type="poster_title_image_generate_item",
                status="failed",
                result=None,
            ),
        ]

    monkeypatch.setattr("app.repositories.job_repo.JobRepo.list_internal_children", fake_list_internal_children)
    job = Job(
        id=root_id,
        caller_id="caller-1",
        client_request_id="poster-1",
        job_type="poster_title_image",
        status="failed",
        progress_percent=100,
        progress_stage="failed",
        **_job_params_fields(params),
        error={"code": "WORKFLOW_CHILD_FAILED", "message": "workflow child job failed"},
        created_at=datetime.now(timezone.utc),
    )

    result = await PosterTitleImageJob().build_result_snapshot("failed", job, object())

    assert result is not None
    assert result["batch_summary"] == {"total": 1, "succeeded": 1, "failed": 0, "running": 0, "pending": 0}
    assert [item["item_id"] for item in result["items"]] == ["es"]


@pytest.mark.asyncio
async def test_get_job_response_projects_poster_title_image_running_result(monkeypatch):
    from app.jobs.types.poster_title_image import PosterTitleImageJob
    from app.jobs.types.poster_title_image.executor import _item_node_key
    from app.services.jobs import get_job_response

    ref = _url_ref("reference/title.png", _transparent_reference_png_bytes())
    params = _params(ref)
    root_id = uuid.uuid4()
    succeeded_item = {
        "item_id": "es",
        "language": "es",
        "status": "succeeded",
        "images": [_result_image("out/es.png", b"es")],
        "error": None,
    }
    job = Job(
        id=root_id,
        caller_id="caller-1",
        client_request_id="poster-1",
        job_type="poster_title_image",
        status="running",
        progress_percent=55,
        progress_text="正在生成标题图",
        progress_stage="calling_model",
        **_job_params_fields(params),
        created_at=datetime.now(timezone.utc),
    )

    async def fake_get_for_caller(_db, job_id, caller_id):
        assert job_id == root_id
        assert caller_id == "caller-1"
        return job

    async def fake_list_internal_children(_db, *, root_job_id, statuses=None):
        assert root_job_id == root_id
        return [
            SimpleNamespace(
                workflow_node_key=_item_node_key("es"),
                job_type="poster_title_image_generate_item",
                status="succeeded",
                result={"item": succeeded_item, "duration_ms": {"ai_model": 5, "total": 6}},
            )
        ]

    monkeypatch.setattr("app.services.jobs.JobRepo.get_for_caller", fake_get_for_caller)
    monkeypatch.setattr("app.repositories.job_repo.JobRepo.list_internal_children", fake_list_internal_children)
    monkeypatch.setattr("app.jobs.factory.get_job_executor", lambda _job_type: PosterTitleImageJob())

    response = await get_job_response(object(), root_id, "caller-1")

    assert response.job_status == "running"
    assert response.cost is None
    assert response.job_result is not None
    assert response.job_result["batch_summary"] == {
        "total": 1,
        "succeeded": 1,
        "failed": 0,
        "running": 0,
        "pending": 0,
    }
    assert [item["item_id"] for item in response.job_result["items"]] == ["es"]


@pytest.mark.asyncio
async def test_get_job_response_preserves_succeeded_items_when_poster_title_image_failed(monkeypatch):
    from app.jobs.types.poster_title_image import PosterTitleImageJob
    from app.jobs.types.poster_title_image.executor import _item_node_key
    from app.services.jobs import get_job_response

    ref = _url_ref("reference/title.png", _transparent_reference_png_bytes())
    params = _params(ref)
    params["items"].append(
        {
            **params["items"][0],
            "item_id": "fr",
            "language": "fr",
            "title_text": "Quand l'amour s'eloigne",
        }
    )
    root_id = uuid.uuid4()
    succeeded_item = {
        "item_id": "es",
        "language": "es",
        "status": "succeeded",
        "images": [_result_image("out/es.png", b"es")],
        "error": None,
    }
    job = Job(
        id=root_id,
        caller_id="caller-1",
        client_request_id="poster-1",
        job_type="poster_title_image",
        status="failed",
        progress_percent=100,
        progress_text="failed",
        progress_stage="failed",
        **_job_params_fields(params),
        error={"code": "WORKFLOW_CHILD_FAILED", "message": "workflow child job failed"},
        created_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
    )

    async def fake_get_for_caller(_db, job_id, caller_id):
        assert job_id == root_id
        assert caller_id == "caller-1"
        return job

    async def fake_list_internal_children(_db, *, root_job_id, statuses=None):
        assert root_job_id == root_id
        return [
            SimpleNamespace(
                workflow_node_key=_item_node_key("es"),
                job_type="poster_title_image_generate_item",
                status="succeeded",
                result={"item": succeeded_item, "duration_ms": {"ai_model": 5, "total": 6}},
            ),
            SimpleNamespace(
                workflow_node_key=_item_node_key("fr"),
                job_type="poster_title_image_generate_item",
                status="failed",
                result=None,
            ),
        ]

    async def fake_get_scope_billing(*_args, **_kwargs):
        return BillingEnvelope(
            scope_type="job",
            scope_id=str(root_id),
            status="incomplete",
            currency="USD",
            total_cost_amount="0",
            usage_units={},
            pricing_refs=[],
            ai_call_count=0,
            billable_call_count=0,
            unbillable_call_count=0,
            failed_call_count=0,
        )

    monkeypatch.setattr("app.services.jobs.JobRepo.get_for_caller", fake_get_for_caller)
    monkeypatch.setattr("app.repositories.job_repo.JobRepo.list_internal_children", fake_list_internal_children)
    monkeypatch.setattr("app.services.jobs.get_scope_billing", fake_get_scope_billing)
    monkeypatch.setattr("app.jobs.factory.get_job_executor", lambda _job_type: PosterTitleImageJob())

    response = await get_job_response(object(), root_id, "caller-1")

    assert response.job_status == "failed"
    assert response.job_error is not None
    assert response.job_result is not None
    assert response.job_result["batch_summary"] == {
        "total": 1,
        "succeeded": 1,
        "failed": 0,
        "running": 0,
        "pending": 0,
    }
    assert [item["item_id"] for item in response.job_result["items"]] == ["es"]


def test_callback_body_preserves_poster_title_image_dimensions():
    from app.services.callbacks import build_callback_body

    root_id = uuid.uuid4()
    result = {
        "schema_version": "default",
        "job_type": "poster_title_image",
        "batch_summary": {"total": 1, "succeeded": 1, "failed": 0, "running": 0, "pending": 0},
        "items": [
            {
                "item_id": "es",
                "language": "es",
                "status": "succeeded",
                "images": [_result_image("out/es.png", b"es", width=96, height=48)],
                "error": None,
            }
        ],
        "duration_ms": {"ai_model": 5, "total": 6},
    }
    job = Job(
        id=root_id,
        caller_id="caller-1",
        client_request_id="poster-1",
        job_type="poster_title_image",
        status="succeeded",
        progress_percent=100,
        **_job_params_fields(_params(_url_ref("reference/title.png", _transparent_reference_png_bytes()))),
        result=result,
        created_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
    )

    body = build_callback_body(job)

    image = body["job"]["job_result"]["items"][0]["images"][0]
    assert image["width"] == 96
    assert image["height"] == 48


@pytest.mark.asyncio
async def test_callback_body_for_failed_poster_title_image_snapshot_preserves_dimensions(monkeypatch):
    from app.jobs.types.poster_title_image import PosterTitleImageJob
    from app.jobs.types.poster_title_image.executor import _item_node_key
    from app.services.callbacks import build_callback_body_for_job

    ref = _url_ref("reference/title.png", _transparent_reference_png_bytes())
    params = _params(ref)
    params["items"].append(
        {
            **params["items"][0],
            "item_id": "fr",
            "language": "fr",
            "title_text": "Quand l'amour s'eloigne",
        }
    )
    root_id = uuid.uuid4()
    succeeded_item = {
        "item_id": "es",
        "language": "es",
        "status": "succeeded",
        "images": [_result_image("out/es.png", b"es", width=96, height=48)],
        "error": None,
    }
    job = Job(
        id=root_id,
        caller_id="caller-1",
        client_request_id="poster-1",
        job_type="poster_title_image",
        status="failed",
        progress_percent=100,
        progress_stage="failed",
        **_job_params_fields(params),
        error={"code": "WORKFLOW_CHILD_FAILED", "message": "workflow child job failed"},
        created_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
    )

    async def fake_list_internal_children(_db, *, root_job_id, statuses=None):
        assert root_job_id == root_id
        return [
            SimpleNamespace(
                workflow_node_key=_item_node_key("es"),
                job_type="poster_title_image_generate_item",
                status="succeeded",
                result={"item": succeeded_item, "duration_ms": {"ai_model": 5, "total": 6}},
            ),
            SimpleNamespace(
                workflow_node_key=_item_node_key("fr"),
                job_type="poster_title_image_generate_item",
                status="failed",
                result=None,
            ),
        ]

    monkeypatch.setattr("app.repositories.job_repo.JobRepo.list_internal_children", fake_list_internal_children)
    monkeypatch.setattr("app.jobs.factory.get_job_executor", lambda _job_type: PosterTitleImageJob())

    body = await build_callback_body_for_job(job, object())

    image = body["job"]["job_result"]["items"][0]["images"][0]
    assert image["width"] == 96
    assert image["height"] == 48


@pytest.mark.asyncio
async def test_poster_title_image_style_probe_leaf_logs_completion(monkeypatch, tmp_path, caplog):
    from app.jobs.types.poster_title_image import PosterTitleImageStyleProbeJob

    local_storage = LocalObjectStorage(tmp_path)
    reference = _transparent_reference_png_bytes()
    reference_bucket = _allowed_reference_bucket()
    reference_region = _allowed_reference_region()
    local_storage.write_bytes(
        bucket=reference_bucket,
        region=reference_region,
        key="reference/title.png",
        data=reference,
        content_type="image/png",
    )

    recorded_probe_kwargs: dict = {}

    async def fake_probe_style(*_args, **kwargs):
        recorded_probe_kwargs.update(kwargs)
        return "bold stone title letters"

    monkeypatch.setattr("app.jobs.types.poster_title_image.executor.storage", local_storage)
    monkeypatch.setattr(
        "app.jobs.types.poster_title_image.executor.read_http_url_bytes",
        lambda *_args, **_kwargs: reference,
    )
    monkeypatch.setattr("app.jobs.types.poster_title_image.executor._probe_style", fake_probe_style)
    root_id = uuid.uuid4()
    attempt_id = uuid.uuid4()
    job_id = uuid.uuid4()
    job = Job(
        id=job_id,
        caller_id="caller-1",
        client_request_id="poster-1",
        job_type="poster_title_image_style_probe",
        status="running",
        active_attempt_id=attempt_id,
        root_job_id=root_id,
        workflow_node_key="probe.0",
        **_job_params_fields(
            {
                "style_key": "style-1",
                "reference_image": _url_ref("reference/title.png", reference),
                "style_prompt": "describe style",
                "style_probe_model_id": "gpt-5.5",
                "image_adapter": "openai_responses",
            }
        ),
        runtime_ref=_runtime_ref(
            job_type="poster_title_image_style_probe",
            params={
                "style_key": "style-1",
                "reference_image": _url_ref("reference/title.png", reference),
                "style_prompt": "describe style",
                "style_probe_model_id": "gpt-5.5",
                "image_adapter": "openai_responses",
            },
            runtime_fields={
                "operation": "poster_title_image_style_probe",
                "style_probe_model_id": "gpt-5.5",
                "image_adapter": "openai_responses",
                "_system": {"trigger_request_id": "req-probe-1"},
            },
            output_target={"type": "oss_prefix", "oss_bucket": "local-dev", "oss_region": "local", "oss_prefix": "ai-jobs/"},
        ),
        created_at=datetime.now(timezone.utc),
    )

    caplog.set_level(logging.INFO, logger="app.jobs.types.poster_title_image.executor")
    result = await PosterTitleImageStyleProbeJob()._execute(job, object())

    assert result["style_key"] == "style-1"
    assert result["style_desc"] == "bold stone title letters"
    assert recorded_probe_kwargs["model_id"] == "gpt-5.5"
    assert recorded_probe_kwargs["image_adapter"] == "openai_responses"
    assert recorded_probe_kwargs["request_id"] == "req-probe-1"
    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert f"event={LogEvent.POSTER_TITLE_IMAGE_STYLE_PROBE_COMPLETED}" in messages
    assert f"job_id={job_id}" in messages
    assert f"root_job_id={root_id}" in messages
    assert f"attempt_id={attempt_id}" in messages
    assert "trigger_request_id=req-probe-1" in messages
    assert "caller_id=caller-1" in messages
    assert "job_type=poster_title_image_style_probe" in messages
    assert "workflow_node_key=probe.0" in messages
    assert "operation=poster_title_image.probe_style" in messages
    assert "model_id=gpt-5.5" in messages
    assert "bold stone title letters" not in messages
    assert "describe style" not in messages
    assert "public_url" not in messages


@pytest.mark.asyncio
async def test_style_probe_uses_ai_ledger(monkeypatch):
    from app.jobs.types.poster_title_image.executor import _probe_style

    recorded = {}

    async def fake_generate_text_with_images_with_ledger(**kwargs):
        recorded.update(kwargs)
        return TextGenerationResult(
            text="bold stone title letters",
            prompt_tokens=10,
            completion_tokens=4,
            usage={"input_tokens": 10, "output_tokens": 4},
        )

    monkeypatch.setattr(
        "app.jobs.types.poster_title_image.executor.generate_text_with_images_with_ledger",
        fake_generate_text_with_images_with_ledger,
    )
    job = Job(
        id=uuid.uuid4(),
        caller_id="caller-1",
        client_request_id="poster-1",
        job_type="poster_title_image",
        status="running",
        active_attempt_id=uuid.uuid4(),
        **_job_params_fields({}),
        created_at=datetime.now(timezone.utc),
    )
    reference_image = ImageInput(data=b"png", content_type="image/png")

    text = await _probe_style(
        reference_image,
        "describe style",
        model_id="gpt-5.5",
        image_adapter="openai_responses",
        caller_id="caller-1",
        scope_id=str(job.id),
        scope_job_id=job.id,
        request_id="request-1",
        job=job,
        attempt_id=job.active_attempt_id,
    )

    assert text == "bold stone title letters"
    assert recorded["operation"] == "poster_title_image.probe_style"
    assert recorded["model_id"] == "gpt-5.5"
    assert recorded["reference_images"] == [reference_image]
    assert recorded["scope_job_id"] == job.id


def test_job_cost_maps_terminal_billing_projection():
    billing = BillingEnvelope(
        scope_type="job",
        scope_id="job-1",
        status="estimated",
        currency="USD",
        total_cost_amount="0.00596500",
        usage_units={
            "image_count": 1,
            "input_tokens": 17,
            "cached_input_tokens": 0,
            "output_tokens": 196,
            "total_tokens": 213,
            "text_input_tokens": 17,
            "cached_text_input_tokens": 0,
            "image_input_tokens": 0,
            "cached_image_input_tokens": 0,
            "image_output_tokens": 196,
        },
        pricing_refs=["openai:gpt-image-2@2026-07-02"],
        ai_call_count=1,
        billable_call_count=1,
        unbillable_call_count=0,
        failed_call_count=0,
    )

    cost = job_cost_from_billing(billing)

    assert cost is not None
    assert cost.model_dump() == {"currency": "USD", "amount": "0.00596500", "final": True}


def test_job_envelope_rejects_non_terminal_cost():
    with pytest.raises(ValueError, match="cost must be null"):
        JobEnvelope.model_validate(
            {
                "job_id": uuid.uuid4(),
                "client_request_id": "poster-1",
                "job_type": "poster_title_image",
                "job_status": "running",
                "job_progress": {"stage": "calling_model", "percent": 50},
                "job_result": None,
                "job_error": None,
                "cost": {"currency": "USD", "amount": "0.00596500", "final": True},
                "callback": {"status": "not_configured", "attempt": 0},
                "status_url": "/api/v1/ai-jobs/jobs/test",
                "created_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
                "finished_at": None,
            }
        )


def test_job_envelope_rejects_non_terminal_usage():
    with pytest.raises(ValueError, match="usage must be null"):
        JobEnvelope.model_validate(
            {
                "job_id": uuid.uuid4(),
                "client_request_id": "poster-1",
                "job_type": "poster_title_image",
                "job_status": "running",
                "job_progress": {"stage": "calling_model", "percent": 50},
                "job_result": None,
                "job_error": None,
                "cost": None,
                "usage": {
                    "ai_call_count": 1,
                    "total_tokens": None,
                    "final": True,
                },
                "callback": {"status": "not_configured", "attempt": 0},
                "status_url": "/api/v1/ai-jobs/jobs/test",
                "created_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
                "finished_at": None,
            }
        )


GREEN_BACKGROUND_TEXT = "#00FF00"
