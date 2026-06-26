import io
import uuid
from datetime import datetime, timezone

import pytest
from PIL import Image

from app.integrations.ai_adapters.base import ImageGenerationResult, ImageInput, TextGenerationResult
from app.integrations.image import remove_green_background
from app.integrations.object_storage import bare_sha256, sha256_digest
from app.integrations.storage import LocalObjectStorage
from app.jobs.types.poster_title_image import POSTER_TITLE_IMAGE_RESPONSE_MODEL_ID, PosterTitleImageJob
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


def _url_ref(key: str, data: bytes) -> dict:
    return {
        "public_url": f"https://local-dev.oss-local.aliyuncs.com/{key}",
        "internal_url": f"https://local-dev.oss-local-internal.aliyuncs.com/{key}",
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
        "reference_image": ref,
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
        "app.jobs.types.poster_title_image.IMAGE_MODEL_GATE.resolve",
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
        POSTER_TITLE_IMAGE_RESPONSE_MODEL_ID,
        required_media_types={"image/png"},
    )

    assert result.resolved_model.model_id == POSTER_TITLE_IMAGE_RESPONSE_MODEL_ID
    assert result.resolved_model.provider_model == "gpt-4o"


def test_remove_green_background_matches_poc_chroma_key_strategy():
    data = remove_green_background(_png_bytes())
    result = Image.open(io.BytesIO(data)).convert("RGBA")

    assert result.getpixel((0, 0))[3] == 0
    assert result.getpixel((20, 20))[3] == 255


@pytest.mark.asyncio
async def test_poster_title_image_executor_generates_transparent_title_layer(monkeypatch, tmp_path):
    local_storage = LocalObjectStorage(tmp_path)
    reference = _png_bytes(accent=(0, 0, 255, 255))
    local_storage.write_bytes(
        bucket="local-dev",
        region="local",
        key="reference/title.png",
        data=reference,
        content_type="image/png",
    )
    output_target = {
        "type": "oss_prefix",
        "oss_bucket": "local-dev",
        "oss_region": "local",
        "oss_prefix": "ai-jobs/job-1/",
    }
    generated_green = _png_bytes()
    recorded = {}

    async def fake_generate_image_with_ledger(**kwargs):
        recorded["kwargs"] = kwargs
        return ImageGenerationResult(images=[generated_green], usage={"image_count": 1})

    async def fake_probe_style(reference_image, prompt, **kwargs):
        assert reference_image.content_type == "image/png"
        assert "LETTERFORMS ONLY" in prompt
        assert kwargs["caller_id"] == "caller-1"
        return "heavy cracked stone letterforms"

    monkeypatch.setattr("app.jobs.types.poster_title_image.storage", local_storage)
    monkeypatch.setattr("app.jobs.types.poster_title_image.output_target_from_job", lambda _job: output_target)
    monkeypatch.setattr("app.jobs.types.poster_title_image._response_provider_model", lambda: "gpt-4o")
    monkeypatch.setattr("app.jobs.types.poster_title_image._probe_style", fake_probe_style)
    monkeypatch.setattr("app.jobs.types.poster_title_image.generate_image_with_ledger", fake_generate_image_with_ledger)

    job_id = uuid.uuid4()
    job = Job(
        id=job_id,
        caller_id="caller-1",
        client_request_id="poster-1",
        job_type="poster_title_image",
        status="running",
        active_attempt_id=uuid.uuid4(),
        job_params=_params(_url_ref("reference/title.png", reference)),
        created_at=datetime.now(timezone.utc),
    )

    result = await PosterTitleImageJob()._execute(job, object())

    assert result["job_type"] == "poster_title_image"
    assert result["batch_summary"] == {"total": 1, "succeeded": 1, "failed": 0, "running": 0, "pending": 0}
    item = result["items"][0]
    assert item["status"] == "succeeded"
    obj = item["images"][0]["object"]
    assert obj["public_url"].startswith("https://local-dev.oss-local.aliyuncs.com/")
    assert obj["internal_url"].startswith("https://local-dev.oss-local-internal.aliyuncs.com/")
    assert obj["content_type"] == "image/png"
    assert recorded["kwargs"]["model_id"] == "gpt-image-2"
    assert recorded["kwargs"]["response_model"] == "gpt-4o"
    assert GREEN_BACKGROUND_TEXT not in recorded["kwargs"]["prompt"]

    output_key = "ai-jobs/job-1/poster-title/{}/es/title-layer.png".format(job_id)
    written = local_storage.read_bytes(bucket="local-dev", region="local", key=output_key)
    output_image = Image.open(io.BytesIO(written)).convert("RGBA")
    assert output_image.getpixel((0, 0))[3] == 0
    assert output_image.getpixel((20, 20))[3] == 255


@pytest.mark.asyncio
async def test_style_probe_uses_ai_ledger(monkeypatch):
    from app.jobs.types.poster_title_image import _probe_style

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
        "app.jobs.types.poster_title_image.generate_text_with_images_with_ledger",
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
        request_id="request-1",
        job=job,
        attempt_id=job.active_attempt_id,
    )

    assert text == "bold stone title letters"
    assert recorded["operation"] == "poster_title_image.probe_style"
    assert recorded["model_id"] == "gpt-4o"
    assert recorded["reference_images"] == [reference_image]


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


GREEN_BACKGROUND_TEXT = "Output as a transparent PNG"
