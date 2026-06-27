import io
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from PIL import Image

from app.integrations.ai_adapters.base import ImageGenerationResult, ImageInput, TextGenerationResult
from app.core.config import settings
from app.core.exceptions import AppError
from app.integrations.image import (
    TRANSPARENT_REFERENCE_MAX_BYTES,
    TRANSPARENT_REFERENCE_MAX_WIDTH,
    remove_green_background,
    transparent_title_layer_from_green_screen_bytes,
    transparent_title_layer_from_green_screen_file,
    transparent_title_layer_from_green_screen_oss_url,
    validate_transparent_reference_image,
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
from app.schemas.jobs import CreateJobRequest, JobEnvelope, PosterTitleImageParams
from app.services.billing import job_cost_from_billing
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


def _url_ref(key: str, data: bytes, *, bucket: str | None = None, region: str | None = None) -> dict:
    bucket = bucket or _allowed_reference_bucket()
    region = region or _allowed_reference_region()
    return {
        "public_url": f"https://{bucket}.oss-{region}.aliyuncs.com/{key}",
        "internal_url": f"https://{bucket}.oss-{region}-internal.aliyuncs.com/{key}",
        "content_type": "image/png",
        "sha256": bare_sha256(sha256_digest(data)),
    }


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


def test_poster_title_image_params_apply_delivery_contract_constraints():
    ref = _url_ref("reference/title.png", b"x")
    params = PosterTitleImageParams.model_validate(_params(ref))

    assert params.items[0].model_id == "gpt-image-2"
    assert params.items[0].model_options.background == "transparent"

    invalid = _params(ref)
    invalid["items"][0]["reference_image"]["content_type"] = "image/jpeg"
    with pytest.raises(ValueError, match="image/png"):
        PosterTitleImageParams.model_validate(invalid)

    invalid = _params(ref)
    invalid["items"][0]["model_options"]["output_format"] = "jpeg"
    with pytest.raises(ValueError, match="png"):
        PosterTitleImageParams.model_validate(invalid)

    invalid = _params(ref)
    invalid["items"][0]["model_options"]["background"] = "auto"
    with pytest.raises(ValueError, match="transparent"):
        PosterTitleImageParams.model_validate(invalid)

    invalid = _params(ref)
    invalid["items"][0]["language"] = "en"
    with pytest.raises(ValueError, match="language"):
        PosterTitleImageParams.model_validate(invalid)

    invalid = _params(ref)
    second = {**invalid["items"][0], "item_id": "fr", "language": "fr", "model_id": "other-image-model"}
    invalid["items"].append(second)
    with pytest.raises(ValueError, match="model_id"):
        PosterTitleImageParams.model_validate(invalid)


def test_poster_title_image_rejects_draw_count_above_config(monkeypatch):
    monkeypatch.setattr(
        "app.jobs.types.poster_title_image.executor.settings",
        SimpleNamespace(
            job=SimpleNamespace(
                poster_title_image_max_draw_count=1,
                poster_title_image_allowed_oss_buckets=("local-dev",),
                poster_title_image_allowed_oss_regions=("local",),
            ),
            registry=SimpleNamespace(poster_title_image_response_model_id="gpt-5.5"),
        ),
    )
    params = _params(_url_ref("reference/title.png", b"x"))
    params["items"][0]["model_options"]["draw_count"] = 2

    with pytest.raises(AppError) as exc:
        PosterTitleImageJob().validate_normalized_job_params(params)

    assert exc.value.code == POSTER_TITLE_IMAGE_DRAW_COUNT_EXCEEDS_LIMIT
    assert exc.value.details == {"max_draw_count": 1, "draw_count": 2}


def test_poster_title_image_accepts_configured_reference_oss_allowlist(monkeypatch):
    monkeypatch.setattr(
        "app.jobs.types.poster_title_image.executor.settings",
        SimpleNamespace(
            job=SimpleNamespace(
                poster_title_image_max_draw_count=4,
                poster_title_image_allowed_oss_buckets=("cpp-rs-dev",),
                poster_title_image_allowed_oss_regions=("ap-southeast-1",),
            ),
            registry=SimpleNamespace(poster_title_image_response_model_id="gpt-5.5"),
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

    PosterTitleImageJob().validate_normalized_job_params(params)


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

    with pytest.raises(AppError) as exc:
        PosterTitleImageJob().validate_normalized_job_params(params)

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
    assert runtime_fields == {"model_id": "gpt-image-2", "operation": "poster_title_image"}


def test_poster_title_image_create_request_accepts_custom_image_model_id(monkeypatch):
    from app.jobs.types.register import register_all_job_types
    from app.services.jobs import _validate_create_request

    monkeypatch.setattr(
        "app.jobs.types.poster_title_image.executor.IMAGE_MODEL_GATE.resolve",
        lambda model_id, *, require_edit: object(),
    )
    register_all_job_types()
    handler, job_params, runtime_fields = _validate_create_request(
        CreateJobRequest(
            client_request_id="poster-1",
            job_type="poster_title_image",
            job_params=_params(_url_ref("reference/title.png", b"x"), model_id="gpt-image-custom"),
        )
    )

    assert handler.name == "poster_title_image"
    assert job_params["items"][0]["model_id"] == "gpt-image-custom"
    assert runtime_fields == {"model_id": "gpt-image-custom", "operation": "poster_title_image"}


def test_poster_title_image_create_request_rejects_unknown_image_model_id():
    from app.jobs.types.register import register_all_job_types
    from app.services.jobs import _validate_create_request

    register_all_job_types()
    with pytest.raises(Exception, match="模型不可用"):
        _validate_create_request(
            CreateJobRequest(
                client_request_id="poster-1",
                job_type="poster_title_image",
                job_params=_params(_url_ref("reference/title.png", b"x"), model_id="not-an-image-model"),
            )
        )


def test_style_probe_response_model_supports_reference_image_input():
    result = ModelGate().resolve_multimodal_text(
        settings.registry.poster_title_image_response_model_id,
        required_media_types={"image/png"},
    )

    assert result.resolved_model.model_id == settings.registry.poster_title_image_response_model_id
    assert result.resolved_model.provider_model == "gpt-5.5"
    assert result.model.features["supports_image_generation_tool"] is True


def test_poster_title_image_response_model_requires_image_generation_tool(monkeypatch):
    from app.jobs.types.poster_title_image.executor import _validate_response_model

    monkeypatch.setattr(
        "app.jobs.types.poster_title_image.executor.settings",
        SimpleNamespace(registry=SimpleNamespace(poster_title_image_response_model_id="gpt-4o")),
    )

    with pytest.raises(AppError, match="image_generation tool"):
        _validate_response_model(required_media_types={"image/png"})


def test_remove_green_background_matches_poc_chroma_key_strategy():
    data = remove_green_background(_png_bytes())
    result = Image.open(io.BytesIO(data)).convert("RGBA")

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
        result = Image.open(io.BytesIO(output)).convert("RGBA")
        assert result.getpixel((0, 0))[3] == 0
        assert result.getpixel((20, 20))[3] == 255


def test_validate_transparent_reference_image_requires_real_format_and_transparent_background():
    data = _transparent_reference_png_bytes()

    result = validate_transparent_reference_image(data, content_type="image/png")

    assert result.width == 40
    assert result.height == 40
    assert result.content_type == "image/png"

    with pytest.raises(AppError, match="content_type"):
        validate_transparent_reference_image(data, content_type="image/webp")

    with pytest.raises(AppError, match="transparent"):
        validate_transparent_reference_image(_png_bytes(), content_type="image/png")


def test_validate_transparent_reference_image_accepts_palette_png_and_rejects_webp_content_type():
    palette_png = validate_transparent_reference_image(
        _transparent_palette_png_bytes(),
        content_type="image/png",
    )

    assert palette_png.content_type == "image/png"
    with pytest.raises(AppError, match="image/png"):
        validate_transparent_reference_image(_transparent_reference_png_bytes(), content_type="image/webp")


def test_poster_title_image_reference_validation_uses_business_error(monkeypatch, tmp_path):
    from app.jobs.types.poster_title_image.executor import _load_reference_image_from_ref

    data = _png_bytes()
    local_storage = LocalObjectStorage(tmp_path)
    local_storage.write_bytes(
        bucket=_allowed_reference_bucket(),
        region=_allowed_reference_region(),
        key="reference/title.png",
        data=data,
        content_type="image/png",
    )
    monkeypatch.setattr("app.jobs.types.poster_title_image.executor.storage", local_storage)

    with pytest.raises(AppError) as exc:
        _load_reference_image_from_ref(_url_ref("reference/title.png", data))

    assert exc.value.code == POSTER_TITLE_IMAGE_REFERENCE_INVALID
    assert exc.value.details["source_reason"] == "INVALID_INPUT"


def test_validate_transparent_reference_image_rejects_oversized_dimensions(monkeypatch):
    monkeypatch.setattr(
        "app.integrations.image.reference_validation.TRANSPARENT_REFERENCE_MAX_WIDTH",
        32,
    )

    with pytest.raises(AppError) as exc:
        validate_transparent_reference_image(_transparent_reference_png_bytes(), content_type="image/png")

    assert exc.value.code == "INPUT_TOO_LARGE"
    assert exc.value.details["max_width"] == 32


def test_validate_transparent_reference_image_rejects_oversized_bytes(monkeypatch):
    monkeypatch.setattr(
        "app.integrations.image.reference_validation.TRANSPARENT_REFERENCE_MAX_BYTES",
        1,
    )

    with pytest.raises(AppError) as exc:
        validate_transparent_reference_image(_transparent_reference_png_bytes(), content_type="image/png")

    assert exc.value.code == "INPUT_TOO_LARGE"
    assert exc.value.details["max_bytes"] == 1
    assert TRANSPARENT_REFERENCE_MAX_BYTES == 20 * 1024 * 1024
    assert TRANSPARENT_REFERENCE_MAX_WIDTH == 4096


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
async def test_poster_title_image_generate_item_leaf_generates_transparent_title_layer(monkeypatch, tmp_path):
    from app.jobs.types.poster_title_image import PosterTitleImageGenerateItemJob

    local_storage = LocalObjectStorage(tmp_path)
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

    probe_child = SimpleNamespace(
        workflow_node_key="probe.0",
        status="succeeded",
        result={"style_desc": "heavy cracked stone letterforms"},
    )

    async def fake_workflow_children(_job, _db):
        return [probe_child]

    monkeypatch.setattr("app.jobs.types.poster_title_image.executor.storage", local_storage)
    monkeypatch.setattr("app.jobs.types.poster_title_image.executor.output_target_from_job", lambda _job: output_target)
    monkeypatch.setattr("app.jobs.types.poster_title_image.executor._response_provider_model", lambda: "gpt-5.5")
    monkeypatch.setattr("app.jobs.types.poster_title_image.executor._workflow_children", fake_workflow_children)
    monkeypatch.setattr(
        "app.jobs.types.poster_title_image.executor.generate_image_with_ledger",
        fake_generate_image_with_ledger,
    )

    root_id = uuid.uuid4()
    job_id = uuid.uuid4()
    job = Job(
        id=job_id,
        caller_id="caller-1",
        client_request_id=None,
        job_type="poster_title_image_generate_item",
        status="running",
        active_attempt_id=uuid.uuid4(),
        root_job_id=root_id,
        parent_job_id=root_id,
        workflow_node_key="item.es",
        is_internal=True,
        job_params={
            "item": _params(_url_ref("reference/title.png", reference))["items"][0],
            "probe_node_key": "probe.0",
        },
        created_at=datetime.now(timezone.utc),
    )

    result = await PosterTitleImageGenerateItemJob()._execute(job, object())

    item = result["item"]
    assert item["status"] == "succeeded"
    obj = item["images"][0]["object"]
    assert obj["public_url"].startswith(f"https://{reference_bucket}.oss-{reference_region}.aliyuncs.com/")
    assert obj["content_type"] == "image/png"
    assert len(recorded) == 1
    assert recorded[0]["model_id"] == "gpt-image-2"
    assert recorded[0]["response_model"] == "gpt-5.5"
    assert recorded[0]["background"] == "auto"
    assert recorded[0]["output_format"] == "png"
    assert recorded[0]["scope_id"] == str(root_id)
    assert recorded[0]["scope_job_id"] == root_id
    assert GREEN_BACKGROUND_TEXT in recorded[0]["prompt"]
    assert "poster-title layer" in recorded[0]["prompt"]
    assert "poster title text only" in recorded[0]["prompt"]

    output_key = "ai-jobs/job-1/poster-title/{}/es/title-layer.png".format(root_id)
    written = local_storage.read_bytes(bucket=reference_bucket, region=reference_region, key=output_key)
    output_image = Image.open(io.BytesIO(written)).convert("RGBA")
    assert output_image.getpixel((0, 0))[3] == 0
    assert output_image.getpixel((20, 20))[3] == 255


@pytest.mark.asyncio
async def test_poster_title_image_generate_item_leaf_generates_two_draws(monkeypatch, tmp_path):
    from app.jobs.types.poster_title_image import PosterTitleImageGenerateItemJob

    local_storage = LocalObjectStorage(tmp_path)
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
    monkeypatch.setattr("app.jobs.types.poster_title_image.executor.output_target_from_job", lambda _job: output_target)
    monkeypatch.setattr("app.jobs.types.poster_title_image.executor._response_provider_model", lambda: "gpt-5.5")
    monkeypatch.setattr("app.jobs.types.poster_title_image.executor._workflow_children", fake_workflow_children)
    monkeypatch.setattr(
        "app.jobs.types.poster_title_image.executor.generate_image_with_ledger",
        fake_generate_image_with_ledger,
    )

    params = _params(_url_ref("reference/title.png", reference))
    params["items"][0]["model_options"]["draw_count"] = 2
    root_id = uuid.uuid4()
    job = Job(
        id=uuid.uuid4(),
        caller_id="caller-1",
        client_request_id=None,
        job_type="poster_title_image_generate_item",
        status="running",
        active_attempt_id=uuid.uuid4(),
        root_job_id=root_id,
        parent_job_id=root_id,
        workflow_node_key="item.es",
        is_internal=True,
        job_params={"item": params["items"][0], "probe_node_key": "probe.0"},
        created_at=datetime.now(timezone.utc),
    )

    result = await PosterTitleImageGenerateItemJob()._execute(job, object())

    assert len(recorded) == 2
    assert len(result["item"]["images"]) == 2
    keys = [
        "ai-jobs/job-1/poster-title/{}/es/title-layer.png".format(root_id),
        "ai-jobs/job-1/poster-title/{}/es/title-layer-2.png".format(root_id),
    ]
    for key in keys:
        written = local_storage.read_bytes(bucket=reference_bucket, region=reference_region, key=key)
        output_image = Image.open(io.BytesIO(written)).convert("RGBA")
        assert output_image.getpixel((0, 0))[3] == 0


@pytest.mark.asyncio
async def test_poster_title_image_join_leaf_preserves_request_item_order(monkeypatch):
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
        "images": [{"object": _url_ref("out/es.png", b"es")}],
    }
    second_result = {
        "item_id": "fr",
        "language": "fr",
        "status": "succeeded",
        "images": [{"object": _url_ref("out/fr.png", b"fr")}],
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
        parent_job_id=root_id,
        workflow_node_key="join",
        is_internal=True,
        job_params={"items": params["items"]},
        created_at=datetime.now(timezone.utc),
    )

    result = await PosterTitleImageJoinJob()._execute(job, object())

    assert result["batch_summary"] == {"total": 2, "succeeded": 2, "failed": 0, "running": 0, "pending": 0}
    assert [item["item_id"] for item in result["items"]] == ["es", "fr"]
    assert result["duration_ms"]["ai_model"] == 12
    assert result["duration_ms"]["total"] == 12


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
        "images": [{"object": _url_ref("out/es.png", b"es")}],
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
        job_params=params,
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
        job_params=_params(ref),
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
        "images": [{"object": _url_ref("out/es.png", b"es")}],
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
        job_params=params,
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
        "images": [{"object": _url_ref("out/es.png", b"es")}],
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
        callback_status="pending",
        callback_attempts=0,
        job_params=params,
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
        "images": [{"object": _url_ref("out/es.png", b"es")}],
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
        callback_status="pending",
        callback_attempts=0,
        job_params=params,
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
        job_params={},
        created_at=datetime.now(timezone.utc),
    )
    reference_image = ImageInput(data=b"png", content_type="image/png")

    text = await _probe_style(
        reference_image,
        "describe style",
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
        total_cost_amount="0.04000000",
        usage_units={"image_count": 1},
        pricing_refs=["openai:gpt-image-2@2026-06-23"],
        ai_call_count=1,
        billable_call_count=1,
        unbillable_call_count=0,
        failed_call_count=0,
    )

    cost = job_cost_from_billing(billing)

    assert cost is not None
    assert cost.model_dump() == {"currency": "USD", "amount": "0.04000000", "final": True}


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
                "cost": {"currency": "USD", "amount": "0.04000000", "final": True},
                "callback": {"status": "not_configured", "attempt": 0},
                "status_url": "/api/v1/ai-jobs/jobs/test",
                "created_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
                "finished_at": None,
            }
        )


GREEN_BACKGROUND_TEXT = "#00FF00"
