import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.core.exceptions import AppError
from app.core.error_registry import get_error_spec
from app.ai.adapters.base import ImageGenerationResult, ImageInput, TextGenerationResult
from app.ai.pricing.registry import CallPrice, ImagePrice, ImageTokenPrice, TokenPrice
from app.services import ai_capability_kernel
from app.services import ai_gateway_facade


class _FakeDB:
    def __init__(self):
        self.commits = 0

    async def commit(self):
        self.commits += 1


class _FakeSessionFactory:
    def __init__(self):
        self.sessions: list[_FakeDB] = []

    def __call__(self):
        session = _FakeDB()
        self.sessions.append(session)
        return _FakeSessionContext(session)


class _FakeSessionContext:
    def __init__(self, session: _FakeDB):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *_args):
        return False


def _model() -> SimpleNamespace:
    return SimpleNamespace(
        id="custom-model",
        model_type="text",
        adapter="litellm",
        provider="openai",
        provider_model="custom-model",
        adapter_model="openai/custom-model",
        pricing_ref="openai:custom-model@2026-06-23",
        capabilities=("text_generation",),
        temperature=0.2,
        num_retries=1,
        drop_params=False,
    )


def _image_model() -> SimpleNamespace:
    return SimpleNamespace(
        id="custom-image-model",
        model_type="image",
        adapter="openai_images",
        provider="openai",
        provider_model="custom-image-model",
        adapter_model="openai/custom-image-model",
        pricing_ref="openai:custom-image-model@2026-06-23",
        capabilities=("image_generation", "image_edit"),
        input_media_types=("text/plain", "image/png"),
        output_media_types=("image/png",),
    )


def _price() -> TokenPrice:
    return TokenPrice(
        ref="openai:custom-model@2026-06-23",
        model_id="custom-model",
        provider="openai",
        provider_model="custom-model",
        pricing_type="per_token",
        version="2026-06-23",
        currency="USD",
        input_per_1m=Decimal("1.00"),
        cached_input_per_1m=Decimal("0.10"),
        output_per_1m=Decimal("2.00"),
    )


def test_facade_does_not_reexport_kernel_helper_classes():
    assert not hasattr(ai_gateway_facade, "ModelGate")
    assert not hasattr(ai_gateway_facade, "ProviderGateway")
    assert not hasattr(ai_gateway_facade, "UsageNormalizer")
    assert not hasattr(ai_gateway_facade, "TypedPricingResolver")
    assert not hasattr(ai_gateway_facade, "UsageLedgerWriter")


def test_model_gate_resolves_model_to_internal_capability(monkeypatch):
    monkeypatch.setattr(ai_capability_kernel, "require_enabled_text_model", lambda _model_id: _model())

    result = ai_capability_kernel.ModelGate().resolve("custom-model")

    assert result.model.id == "custom-model"
    assert result.resolved_model.model_id == "custom-model"
    assert result.resolved_model.provider == "openai"
    assert result.resolved_model.provider_model == "custom-model"
    assert result.resolved_model.adapter_model == "openai/custom-model"
    assert result.resolved_model.pricing_ref == "openai:custom-model@2026-06-23"


def test_model_gate_rejects_model_without_text_generation_capability(monkeypatch):
    model = SimpleNamespace(**{**_model().__dict__, "capabilities": ("image_generation",)})
    monkeypatch.setattr(ai_capability_kernel, "require_enabled_text_model", lambda _model_id: model)

    with pytest.raises(AppError) as exc:
        ai_capability_kernel.ModelGate().resolve("custom-model")

    assert exc.value.code == "MODEL_NOT_AVAILABLE"


def test_model_gate_rejects_multimodal_text_without_supported_media_type(monkeypatch):
    model = SimpleNamespace(
        **{
            **_model().__dict__,
            "capabilities": ("text_generation", "multimodal_text_generation"),
            "input_media_types": ("text/plain",),
        }
    )
    monkeypatch.setattr(ai_capability_kernel, "get_enabled_model", lambda _model_id: model)

    with pytest.raises(AppError) as exc:
        ai_capability_kernel.ModelGate().resolve_multimodal_text(
            "custom-model",
            required_media_types={"image/png"},
        )

    assert exc.value.code == "MODEL_NOT_AVAILABLE"
    assert "image/png" in exc.value.message


def test_text_model_gate_rejects_missing_model_type(monkeypatch):
    model = SimpleNamespace(**{key: value for key, value in _model().__dict__.items() if key != "model_type"})
    monkeypatch.setattr(ai_capability_kernel, "get_enabled_model", lambda _model_id: model)

    with pytest.raises(RuntimeError, match="requires model_type"):
        ai_capability_kernel.require_enabled_text_model("custom-model")


@pytest.mark.asyncio
async def test_provider_gateway_builds_text_generation_request_from_model(monkeypatch):
    recorded: dict = {}

    async def fake_generate_text(request):
        recorded["request"] = request
        return TextGenerationResult(text="ok", prompt_tokens=1, completion_tokens=1, usage={})

    def fake_require_text_generation_adapter(adapter_name):
        recorded["adapter_name"] = adapter_name
        return SimpleNamespace(generate_text=fake_generate_text)

    monkeypatch.setattr(ai_capability_kernel, "require_text_generation_adapter", fake_require_text_generation_adapter)
    monkeypatch.setattr(
        ai_capability_kernel,
        "settings",
        SimpleNamespace(
            ai_provider=SimpleNamespace(
                model_call_timeout_seconds=45,
                openai_api_key_value="test-key",
                openai_base_url="https://example.test/v1",
            )
        ),
    )

    await ai_capability_kernel.ProviderGateway().generate_text(_model(), [{"role": "user", "content": "hello"}])

    assert recorded["request"]
    assert recorded["adapter_name"] == "litellm"
    request = recorded["request"]
    assert request.adapter_model == "openai/custom-model"
    assert request.messages == [{"role": "user", "content": "hello"}]
    assert request.temperature == 0.2
    assert request.timeout_seconds == 45
    assert request.api_key == "test-key"
    assert request.api_base == "https://example.test/v1"
    assert request.num_retries == 1
    assert request.drop_params is False


@pytest.mark.asyncio
async def test_provider_gateway_builds_multimodal_text_request_from_model(monkeypatch):
    recorded: dict = {}

    async def fake_generate_text_with_images(request):
        recorded["request"] = request
        return TextGenerationResult(text="ok", prompt_tokens=1, completion_tokens=1, usage={})

    def fake_require_multimodal_text_generation_adapter(adapter_name):
        recorded["adapter_name"] = adapter_name
        return SimpleNamespace(generate_text_with_images=fake_generate_text_with_images)

    monkeypatch.setattr(
        ai_capability_kernel,
        "require_multimodal_text_generation_adapter",
        fake_require_multimodal_text_generation_adapter,
    )
    monkeypatch.setattr(
        ai_capability_kernel,
        "settings",
        SimpleNamespace(
            ai_provider=SimpleNamespace(
                model_call_timeout_seconds=45,
                openai_api_key_value="test-key",
                openai_base_url="https://example.test/v1",
            )
        ),
    )

    image = ImageInput(data=b"image", content_type="image/png", detail="low")
    await ai_capability_kernel.ProviderGateway().generate_text_with_images(
        _model(),
        prompt="describe",
        reference_images=[image],
    )

    assert recorded["request"]
    assert recorded["adapter_name"] == "litellm"
    request = recorded["request"]
    assert request.adapter_model == "openai/custom-model"
    assert request.provider_model == "custom-model"
    assert request.prompt == "describe"
    assert request.reference_images == [image]
    assert request.timeout_seconds == 45
    assert request.api_key == "test-key"
    assert request.api_base == "https://example.test/v1"


@pytest.mark.asyncio
async def test_provider_gateway_builds_image_generation_request_from_model(monkeypatch):
    recorded: dict = {}

    async def fake_generate_image(request):
        recorded["request"] = request
        return ImageGenerationResult(images=[b"png"], usage={"image_generation_call_count": 1})

    def fake_require_image_generation_adapter(adapter_name):
        recorded["adapter_name"] = adapter_name
        return SimpleNamespace(generate_image=fake_generate_image)

    monkeypatch.setattr(ai_capability_kernel, "require_image_generation_adapter", fake_require_image_generation_adapter)
    monkeypatch.setattr(
        ai_capability_kernel,
        "settings",
        SimpleNamespace(
            ai_provider=SimpleNamespace(
                model_call_timeout_seconds=45,
                openai_api_key_value="test-key",
                openai_base_url="https://example.test/v1",
            )
        ),
    )

    image = ImageInput(data=b"image", content_type="image/png", detail="low")
    await ai_capability_kernel.ProviderGateway().generate_image(
        _image_model(),
        image_adapter="openai_images",
        response_model="gpt-4o",
        prompt="draw",
        reference_images=[image],
        size="auto",
        quality="high",
        background="auto",
        output_format="png",
    )

    assert recorded["request"]
    assert recorded["adapter_name"] == "openai_images"
    request = recorded["request"]
    assert request.adapter_model == "openai/custom-image-model"
    assert request.provider_model == "custom-image-model"
    assert request.response_model == "gpt-4o"
    assert request.prompt == "draw"
    assert request.reference_images == [image]
    assert request.size == "auto"
    assert request.quality == "high"
    assert request.background == "auto"
    assert request.output_format == "png"
    assert request.timeout_seconds == 45


@pytest.mark.asyncio
async def test_generate_image_with_ledger_uses_catalog_route_adapter_in_hash_and_provider_call(monkeypatch):
    session_factory = _FakeSessionFactory()
    call_id = uuid.uuid4()
    pending_calls: list[dict] = []
    provider_calls: list[str | None] = []
    price = ImagePrice(
        ref="openai:custom-image-model@2026-06-23",
        model_id="custom-image-model",
        provider="openai",
        provider_model="custom-image-model",
        pricing_type="per_image",
        version="2026-06-23",
        currency="USD",
        amount_per_image=Decimal("0.04000000"),
    )

    async def fake_create_pending(*_args, **kwargs):
        pending_calls.append(kwargs)
        return SimpleNamespace(id=call_id)

    async def fake_generate_image(_model, *, image_adapter=None, **_kwargs):
        provider_calls.append(image_adapter)
        return ImageGenerationResult(images=[b"png"], usage={"image_count": 1})

    async def fake_mark_succeeded(*_args, **_kwargs):
        return True

    monkeypatch.setattr(ai_capability_kernel, "require_enabled_image_model", lambda _model_id: _image_model())
    monkeypatch.setattr(ai_capability_kernel, "require_price", lambda _pricing_ref: price)
    monkeypatch.setattr(ai_gateway_facade.PROVIDER_GATEWAY, "generate_image", fake_generate_image)
    monkeypatch.setattr(ai_capability_kernel.AiCallLogRepo, "create_pending", fake_create_pending)
    monkeypatch.setattr(ai_capability_kernel.AiCallLogRepo, "mark_succeeded", fake_mark_succeeded)

    common_kwargs = dict(
        caller_id="caller-1",
        scope_type="job",
        scope_id="00000000-0000-0000-0000-000000000001",
        operation="poster_title_image.generate_title",
        model_id="custom-image-model",
        response_model="gpt-5.5",
        prompt="draw",
        reference_images=[ImageInput(data=b"image", content_type="image/png", detail="low")],
        size="auto",
        quality="high",
        background="auto",
        output_format="png",
        job_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        attempt_id=uuid.UUID("00000000-0000-0000-0000-000000000002"),
        job_type="poster_title_image_generate_item",
        ledger_session_factory=session_factory,
    )
    await ai_gateway_facade.generate_image_with_ledger(**common_kwargs, image_adapter="openai_images")

    assert provider_calls == ["openai_images"]
    assert pending_calls[0]["model_id"] == "custom-image-model"
    assert pending_calls[0]["provider"] == "openai"
    assert pending_calls[0]["provider_model"] == "custom-image-model"
    assert pending_calls[0]["request_hash"].startswith("sha256:")


@pytest.mark.asyncio
async def test_generate_image_with_ledger_rejects_image_adapter_mismatch(monkeypatch):
    session_factory = _FakeSessionFactory()

    monkeypatch.setattr(ai_capability_kernel, "require_enabled_image_model", lambda _model_id: _image_model())

    with pytest.raises(AppError) as exc:
        await ai_gateway_facade.generate_image_with_ledger(
            caller_id="caller-1",
            scope_type="job",
            scope_id="00000000-0000-0000-0000-000000000001",
            operation="poster_title_image.generate_title",
            model_id="custom-image-model",
            image_adapter="openai_responses",
            response_model="gpt-5.5",
            prompt="draw",
            reference_images=[ImageInput(data=b"image", content_type="image/png", detail="low")],
            size="auto",
            quality="high",
            background="auto",
            output_format="png",
            job_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
            attempt_id=uuid.UUID("00000000-0000-0000-0000-000000000002"),
            job_type="poster_title_image_generate_item",
            ledger_session_factory=session_factory,
        )

    assert exc.value.code == "MODEL_NOT_AVAILABLE"


@pytest.mark.asyncio
async def test_generate_image_with_ledger_freezes_openai_images_token_usage_and_cost(monkeypatch):
    session_factory = _FakeSessionFactory()
    call_id = uuid.uuid4()
    recorded: dict = {}
    price = ImageTokenPrice(
        ref="openai:gpt-image-2@2026-07-02",
        model_id="custom-image-model",
        provider="openai",
        provider_model="custom-image-model",
        pricing_type="per_image_token",
        version="2026-07-02",
        currency="USD",
        text_input_per_1m=Decimal("5.00"),
        cached_text_input_per_1m=Decimal("1.25"),
        image_input_per_1m=Decimal("8.00"),
        cached_image_input_per_1m=Decimal("2.00"),
        image_output_per_1m=Decimal("30.00"),
    )

    async def fake_create_pending(*_args, **_kwargs):
        return SimpleNamespace(id=call_id)

    async def fake_generate_image(*_args, **_kwargs):
        return ImageGenerationResult(
            images=[b"png"],
            usage={
                "image_count": 1,
                "api": "images",
                "provider_usage": {
                    "input_tokens": 17,
                    "input_tokens_details": {
                        "image_tokens": 0,
                        "text_tokens": 17,
                    },
                    "output_tokens": 196,
                    "output_tokens_details": {
                        "image_tokens": 196,
                        "text_tokens": 0,
                    },
                    "total_tokens": 213,
                },
            },
        )

    async def fake_mark_succeeded(_db, received_call_id, **kwargs):
        recorded["call_id"] = received_call_id
        recorded.update(kwargs)
        return True

    monkeypatch.setattr(ai_capability_kernel, "require_enabled_image_model", lambda _model_id: _image_model())
    monkeypatch.setattr(ai_capability_kernel, "require_price", lambda _pricing_ref: price)
    monkeypatch.setattr(ai_gateway_facade.PROVIDER_GATEWAY, "generate_image", fake_generate_image)
    monkeypatch.setattr(ai_capability_kernel.AiCallLogRepo, "create_pending", fake_create_pending)
    monkeypatch.setattr(ai_capability_kernel.AiCallLogRepo, "mark_succeeded", fake_mark_succeeded)

    await ai_gateway_facade.generate_image_with_ledger(
        caller_id="caller-1",
        scope_type="job",
        scope_id="00000000-0000-0000-0000-000000000001",
        operation="poster_title_image.generate_title",
        model_id="custom-image-model",
        response_model="gpt-5.5",
        prompt="draw",
        reference_images=[],
        size="1024x1024",
        quality="low",
        background="auto",
        output_format="png",
        job_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        attempt_id=uuid.UUID("00000000-0000-0000-0000-000000000002"),
        job_type="poster_title_image_generate_item",
        ledger_session_factory=session_factory,
        image_adapter="openai_images",
    )

    assert recorded["call_id"] == call_id
    assert recorded["usage_units"] == {
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
    }
    assert recorded["cost_amount"] == Decimal("0.00596500")
    assert recorded["currency"] == "USD"


@pytest.mark.asyncio
async def test_generate_image_with_ledger_fails_token_pricing_without_provider_usage(monkeypatch):
    session_factory = _FakeSessionFactory()
    call_id = uuid.uuid4()
    recorded: dict = {}
    price = ImageTokenPrice(
        ref="openai:gpt-image-2@2026-07-02",
        model_id="custom-image-model",
        provider="openai",
        provider_model="custom-image-model",
        pricing_type="per_image_token",
        version="2026-07-02",
        currency="USD",
        text_input_per_1m=Decimal("5.00"),
        cached_text_input_per_1m=Decimal("1.25"),
        image_input_per_1m=Decimal("8.00"),
        cached_image_input_per_1m=Decimal("2.00"),
        image_output_per_1m=Decimal("30.00"),
    )

    async def fake_create_pending(*_args, **_kwargs):
        return SimpleNamespace(id=call_id)

    async def fake_generate_image(*_args, **_kwargs):
        return ImageGenerationResult(
            images=[b"png"],
            usage={
                "image_count": 1,
                "api": "images",
                "provider_usage": {
                    "input_tokens": 17,
                    "output_tokens": 196,
                    "total_tokens": 213,
                },
            },
        )

    async def fake_mark_failed(_db, received_call_id, **kwargs):
        recorded["call_id"] = received_call_id
        recorded.update(kwargs)
        return True

    async def fail_mark_succeeded(*_args, **_kwargs):
        raise AssertionError("missing image token usage must not be marked succeeded")

    monkeypatch.setattr(ai_capability_kernel, "require_enabled_image_model", lambda _model_id: _image_model())
    monkeypatch.setattr(ai_capability_kernel, "require_price", lambda _pricing_ref: price)
    monkeypatch.setattr(ai_gateway_facade.PROVIDER_GATEWAY, "generate_image", fake_generate_image)
    monkeypatch.setattr(ai_capability_kernel.AiCallLogRepo, "create_pending", fake_create_pending)
    monkeypatch.setattr(ai_capability_kernel.AiCallLogRepo, "mark_failed", fake_mark_failed)
    monkeypatch.setattr(ai_capability_kernel.AiCallLogRepo, "mark_succeeded", fail_mark_succeeded)

    with pytest.raises(AppError) as exc:
        await ai_gateway_facade.generate_image_with_ledger(
            caller_id="caller-1",
            scope_type="job",
            scope_id="00000000-0000-0000-0000-000000000001",
            operation="poster_title_image.generate_title",
            model_id="custom-image-model",
            response_model="gpt-5.5",
            prompt="draw",
            reference_images=[],
            size="1024x1024",
            quality="low",
            background="auto",
            output_format="png",
            job_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
            attempt_id=uuid.UUID("00000000-0000-0000-0000-000000000002"),
            job_type="poster_title_image_generate_item",
            ledger_session_factory=session_factory,
            image_adapter="openai_images",
        )

    assert exc.value.code == "MODEL_COST_CALCULATION_FAILED"
    assert recorded["call_id"] == call_id
    assert recorded["failure_phase"] == "pricing"
    assert recorded["billable_status"] == "unknown"
    assert recorded["cost_calculation_status"] == "failed"


@pytest.mark.parametrize(
    ("usage", "expected_cached"),
    [
        ({"prompt_tokens_details": {"cached_tokens": 7}}, 7),
        ({"input_token_details": {"cached_tokens": 8}}, 8),
        ({"cache_read_input_tokens": 9}, 9),
    ],
)
def test_usage_normalizer_reads_provider_cached_token_variants(usage, expected_cached):
    result = TextGenerationResult(
        text="ok",
        prompt_tokens=100,
        completion_tokens=50,
        usage=usage,
    )

    usage_record = ai_capability_kernel.UsageNormalizer().normalize_text(result)

    assert usage_record.kind == "text"
    assert usage_record.usage_units() == {
        "input_tokens": 100,
        "cached_input_tokens": expected_cached,
        "output_tokens": 50,
        "total_tokens": 150,
    }


def test_typed_pricing_resolver_keeps_token_cost_patchable(monkeypatch):
    calls = {}

    def fake_calculate_cost(price, usage_record):
        calls["price"] = price
        calls["usage_record"] = usage_record
        return Decimal("1.23000000")

    monkeypatch.setattr(ai_capability_kernel, "calculate_usage_cost", fake_calculate_cost)

    usage_record = ai_capability_kernel.normalize_text_usage(
        prompt_tokens=1,
        cached_input_tokens=0,
        completion_tokens=1,
        raw_usage={"prompt_tokens": 1, "completion_tokens": 1},
    )
    amount = ai_capability_kernel.TypedPricingResolver().calculate_cost(
        _price(),
        usage_record,
    )

    assert amount == Decimal("1.23000000")
    assert calls["price"].ref == "openai:custom-model@2026-06-23"
    assert calls["usage_record"].usage_units()["total_tokens"] == 2


async def _call(session_factory: _FakeSessionFactory):
    return await ai_gateway_facade.generate_text_with_ledger(
        caller_id="caller-1",
        scope_type="job",
        scope_id="00000000-0000-0000-0000-000000000001",
        operation="job_type.execute",
        model_id="custom-model",
        messages=[{"role": "user", "content": "hello"}],
        job_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        attempt_id=uuid.UUID("00000000-0000-0000-0000-000000000002"),
        job_type="job_type",
        ledger_session_factory=session_factory,
    )


@pytest.mark.asyncio
async def test_gateway_resolves_default_ledger_session_factory_at_runtime(monkeypatch):
    session_factory = _FakeSessionFactory()
    call_id = uuid.uuid4()

    async def fake_create_pending(*_args, **_kwargs):
        return SimpleNamespace(id=call_id)

    async def fake_generate_text(*_args, **_kwargs):
        return TextGenerationResult(
            text="ok",
            prompt_tokens=1,
            completion_tokens=1,
            usage={"prompt_tokens": 1, "completion_tokens": 1},
        )

    async def fake_mark_succeeded(*_args, **_kwargs):
        return True

    monkeypatch.setattr(ai_gateway_facade, "get_session_factory", lambda: session_factory)
    monkeypatch.setattr(ai_capability_kernel, "require_enabled_text_model", lambda _model_id: _model())
    monkeypatch.setattr(ai_capability_kernel, "require_price", lambda _pricing_ref: _price())
    monkeypatch.setattr(ai_capability_kernel.AiCallLogRepo, "create_pending", fake_create_pending)
    monkeypatch.setattr(ai_capability_kernel.AiCallLogRepo, "mark_succeeded", fake_mark_succeeded)
    monkeypatch.setattr(
        ai_capability_kernel,
        "require_text_generation_adapter",
        lambda adapter_name: SimpleNamespace(generate_text=fake_generate_text),
    )

    result = await ai_gateway_facade.generate_text_with_ledger(
        caller_id="caller-1",
        scope_type="job",
        scope_id="00000000-0000-0000-0000-000000000001",
        operation="job_type.execute",
        model_id="custom-model",
        messages=[{"role": "user", "content": "hello"}],
        job_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        attempt_id=uuid.UUID("00000000-0000-0000-0000-000000000002"),
        job_type="job_type",
    )

    assert result.text == "ok"
    assert [session.commits for session in session_factory.sessions] == [1, 1]


def test_job_scope_context_allows_explicit_root_scope():
    child_job_id = uuid.UUID("00000000-0000-0000-0000-000000000011")
    root_job_id = uuid.UUID("00000000-0000-0000-0000-000000000012")

    ai_capability_kernel.validate_ai_call_context(
        caller_id="caller-1",
        scope_type="job",
        scope_id=str(root_job_id),
        operation="job_type.execute",
        job_id=child_job_id,
        scope_job_id=root_job_id,
        attempt_id=uuid.UUID("00000000-0000-0000-0000-000000000013"),
        job_type="job_type",
    )


def test_job_scope_context_rejects_unowned_scope_id():
    child_job_id = uuid.UUID("00000000-0000-0000-0000-000000000011")
    root_job_id = uuid.UUID("00000000-0000-0000-0000-000000000012")

    with pytest.raises(AppError, match="job scope_id must equal scope_job_id"):
        ai_capability_kernel.validate_ai_call_context(
            caller_id="caller-1",
            scope_type="job",
            scope_id=str(root_job_id),
            operation="job_type.execute",
            job_id=child_job_id,
            attempt_id=uuid.UUID("00000000-0000-0000-0000-000000000013"),
            job_type="job_type",
        )


@pytest.mark.asyncio
async def test_gateway_does_not_call_provider_when_pending_ledger_write_fails(monkeypatch):
    session_factory = _FakeSessionFactory()

    async def fail_create_pending(*_args, **_kwargs):
        raise RuntimeError("ledger unavailable")

    async def fail_generate_text(*_args, **_kwargs):
        raise AssertionError("provider must not be called before pending ledger row exists")

    monkeypatch.setattr(ai_capability_kernel, "require_enabled_text_model", lambda _model_id: _model())
    monkeypatch.setattr(ai_capability_kernel, "require_price", lambda _pricing_ref: _price())
    monkeypatch.setattr(ai_capability_kernel.AiCallLogRepo, "create_pending", fail_create_pending)
    monkeypatch.setattr(ai_capability_kernel, "require_text_generation_adapter", lambda adapter_name: SimpleNamespace(generate_text=fail_generate_text))

    with pytest.raises(RuntimeError, match="ledger unavailable"):
        await _call(session_factory)

    assert [session.commits for session in session_factory.sessions] == [0]


@pytest.mark.asyncio
async def test_gateway_marks_usage_missing_as_failed_unknown_without_zero_cost(monkeypatch):
    session_factory = _FakeSessionFactory()
    call_id = uuid.uuid4()
    recorded: dict = {}

    async def fake_create_pending(*_args, **_kwargs):
        return SimpleNamespace(id=call_id)

    async def fake_generate_text(*_args, **_kwargs):
        return TextGenerationResult(text="ok", prompt_tokens=None, completion_tokens=None, usage=None)

    async def fake_mark_failed(_db, received_call_id, **kwargs):
        recorded["call_id"] = received_call_id
        recorded.update(kwargs)
        return True

    async def fail_mark_succeeded(*_args, **_kwargs):
        raise AssertionError("usage missing must not be marked succeeded")

    monkeypatch.setattr(ai_capability_kernel, "require_enabled_text_model", lambda _model_id: _model())
    monkeypatch.setattr(ai_capability_kernel, "require_price", lambda _pricing_ref: _price())
    monkeypatch.setattr(ai_capability_kernel.AiCallLogRepo, "create_pending", fake_create_pending)
    monkeypatch.setattr(ai_capability_kernel.AiCallLogRepo, "mark_failed", fake_mark_failed)
    monkeypatch.setattr(ai_capability_kernel.AiCallLogRepo, "mark_succeeded", fail_mark_succeeded)
    monkeypatch.setattr(ai_capability_kernel, "require_text_generation_adapter", lambda adapter_name: SimpleNamespace(generate_text=fake_generate_text))

    with pytest.raises(AppError) as exc:
        await _call(session_factory)

    assert exc.value.code == "MODEL_USAGE_MISSING"
    assert recorded["call_id"] == call_id
    assert recorded["failure_phase"] == "usage"
    assert recorded["billable_status"] == "unknown"
    assert recorded["cost_calculation_status"] == "failed"
    assert [session.commits for session in session_factory.sessions] == [1, 1]


@pytest.mark.asyncio
async def test_gateway_freezes_usage_and_cost_on_success(monkeypatch):
    session_factory = _FakeSessionFactory()
    call_id = uuid.uuid4()
    recorded: dict = {}

    async def fake_create_pending(*_args, **_kwargs):
        return SimpleNamespace(id=call_id)

    async def fake_generate_text(*_args, **_kwargs):
        return TextGenerationResult(
            text=" ok ",
            prompt_tokens=1000,
            completion_tokens=500,
            usage={"prompt_tokens": 1000, "completion_tokens": 500},
        )

    async def fake_mark_succeeded(_db, received_call_id, **kwargs):
        recorded["call_id"] = received_call_id
        recorded.update(kwargs)
        return True

    monkeypatch.setattr(ai_capability_kernel, "require_enabled_text_model", lambda _model_id: _model())
    monkeypatch.setattr(ai_capability_kernel, "require_price", lambda _pricing_ref: _price())
    monkeypatch.setattr(ai_capability_kernel.AiCallLogRepo, "create_pending", fake_create_pending)
    monkeypatch.setattr(ai_capability_kernel.AiCallLogRepo, "mark_succeeded", fake_mark_succeeded)
    monkeypatch.setattr(ai_capability_kernel, "require_text_generation_adapter", lambda adapter_name: SimpleNamespace(generate_text=fake_generate_text))

    result = await _call(session_factory)

    assert result.text == " ok "
    assert recorded["call_id"] == call_id
    assert recorded["usage_units"] == {
        "input_tokens": 1000,
        "cached_input_tokens": 0,
        "output_tokens": 500,
        "total_tokens": 1500,
    }
    assert recorded["cost_amount"] == Decimal("0.00200000")
    assert recorded["currency"] == "USD"
    assert [session.commits for session in session_factory.sessions] == [1, 1]


@pytest.mark.asyncio
async def test_gateway_marks_provider_timeout_as_failed_unknown(monkeypatch):
    session_factory = _FakeSessionFactory()
    call_id = uuid.uuid4()
    recorded: dict = {}

    async def fake_create_pending(*_args, **_kwargs):
        return SimpleNamespace(id=call_id)

    async def fake_generate_text(*_args, **_kwargs):
        raise TimeoutError("provider timed out")

    async def fake_mark_failed(_db, received_call_id, **kwargs):
        recorded["call_id"] = received_call_id
        recorded.update(kwargs)
        return True

    async def fail_mark_succeeded(*_args, **_kwargs):
        raise AssertionError("provider timeout must not be marked succeeded")

    monkeypatch.setattr(ai_capability_kernel, "require_enabled_text_model", lambda _model_id: _model())
    monkeypatch.setattr(ai_capability_kernel, "require_price", lambda _pricing_ref: _price())
    monkeypatch.setattr(ai_capability_kernel.AiCallLogRepo, "create_pending", fake_create_pending)
    monkeypatch.setattr(ai_capability_kernel.AiCallLogRepo, "mark_failed", fake_mark_failed)
    monkeypatch.setattr(ai_capability_kernel.AiCallLogRepo, "mark_succeeded", fail_mark_succeeded)
    monkeypatch.setattr(ai_capability_kernel, "require_text_generation_adapter", lambda adapter_name: SimpleNamespace(generate_text=fake_generate_text))

    with pytest.raises(AppError) as exc:
        await _call(session_factory)

    assert exc.value.code == "MODEL_CALL_TIMEOUT"
    assert exc.value.status_code == 504
    assert recorded["call_id"] == call_id
    assert recorded["failure_phase"] == "provider"
    assert recorded["error_code"] == "MODEL_CALL_TIMEOUT"
    assert recorded["error_message"] == "provider timed out"
    assert recorded["billable_status"] == "unknown"
    assert [session.commits for session in session_factory.sessions] == [1, 1]


@pytest.mark.asyncio
async def test_gateway_marks_provider_failure_as_failed_unknown(monkeypatch):
    session_factory = _FakeSessionFactory()
    call_id = uuid.uuid4()
    recorded: dict = {}

    async def fake_create_pending(*_args, **_kwargs):
        return SimpleNamespace(id=call_id)

    async def fake_generate_text(*_args, **_kwargs):
        raise RuntimeError("provider exploded")

    async def fake_mark_failed(_db, received_call_id, **kwargs):
        recorded["call_id"] = received_call_id
        recorded.update(kwargs)
        return True

    async def fail_mark_succeeded(*_args, **_kwargs):
        raise AssertionError("provider failure must not be marked succeeded")

    monkeypatch.setattr(ai_capability_kernel, "require_enabled_text_model", lambda _model_id: _model())
    monkeypatch.setattr(ai_capability_kernel, "require_price", lambda _pricing_ref: _price())
    monkeypatch.setattr(ai_capability_kernel.AiCallLogRepo, "create_pending", fake_create_pending)
    monkeypatch.setattr(ai_capability_kernel.AiCallLogRepo, "mark_failed", fake_mark_failed)
    monkeypatch.setattr(ai_capability_kernel.AiCallLogRepo, "mark_succeeded", fail_mark_succeeded)
    monkeypatch.setattr(ai_capability_kernel, "require_text_generation_adapter", lambda adapter_name: SimpleNamespace(generate_text=fake_generate_text))

    with pytest.raises(AppError) as exc:
        await _call(session_factory)

    assert exc.value.code == "MODEL_CALL_FAILED"
    assert exc.value.status_code == 502
    assert recorded["call_id"] == call_id
    assert recorded["failure_phase"] == "provider"
    assert recorded["error_code"] == "MODEL_CALL_FAILED"
    assert recorded["error_message"] == "provider exploded"
    assert recorded["billable_status"] == "unknown"
    assert [session.commits for session in session_factory.sessions] == [1, 1]


@pytest.mark.asyncio
async def test_gateway_marks_transient_provider_status_as_ai_provider_failed(monkeypatch):
    session_factory = _FakeSessionFactory()
    call_id = uuid.uuid4()
    recorded: dict = {}

    class ProviderStatusError(RuntimeError):
        status_code = 502

    async def fake_create_pending(*_args, **_kwargs):
        return SimpleNamespace(id=call_id)

    async def fake_generate_text(*_args, **_kwargs):
        raise ProviderStatusError("Error code: 502 - origin_bad_gateway")

    async def fake_mark_failed(_db, received_call_id, **kwargs):
        recorded["call_id"] = received_call_id
        recorded.update(kwargs)
        return True

    async def fail_mark_succeeded(*_args, **_kwargs):
        raise AssertionError("provider failure must not be marked succeeded")

    monkeypatch.setattr(ai_capability_kernel, "require_enabled_text_model", lambda _model_id: _model())
    monkeypatch.setattr(ai_capability_kernel, "require_price", lambda _pricing_ref: _price())
    monkeypatch.setattr(ai_capability_kernel.AiCallLogRepo, "create_pending", fake_create_pending)
    monkeypatch.setattr(ai_capability_kernel.AiCallLogRepo, "mark_failed", fake_mark_failed)
    monkeypatch.setattr(ai_capability_kernel.AiCallLogRepo, "mark_succeeded", fail_mark_succeeded)
    monkeypatch.setattr(ai_capability_kernel, "require_text_generation_adapter", lambda adapter_name: SimpleNamespace(generate_text=fake_generate_text))

    with pytest.raises(AppError) as exc:
        await _call(session_factory)

    assert exc.value.code == "AI_PROVIDER_FAILED"
    assert exc.value.status_code == 502
    assert exc.value.details == {"provider_status_code": 502}
    assert recorded["call_id"] == call_id
    assert recorded["failure_phase"] == "provider"
    assert recorded["error_code"] == "AI_PROVIDER_FAILED"
    assert recorded["error_message"] == "Error code: 502 - origin_bad_gateway"
    assert recorded["billable_status"] == "unknown"
    assert [session.commits for session in session_factory.sessions] == [1, 1]


@pytest.mark.asyncio
async def test_generate_image_with_ledger_marks_provider_429_as_ai_provider_failed(monkeypatch):
    session_factory = _FakeSessionFactory()
    call_id = uuid.uuid4()
    recorded: dict = {}
    price = ImageTokenPrice(
        ref="openai:gpt-image-2@2026-07-02",
        model_id="custom-image-model",
        provider="openai",
        provider_model="custom-image-model",
        pricing_type="per_image_token",
        version="2026-07-02",
        currency="USD",
        text_input_per_1m=Decimal("5.00"),
        cached_text_input_per_1m=Decimal("1.25"),
        image_input_per_1m=Decimal("8.00"),
        cached_image_input_per_1m=Decimal("2.00"),
        image_output_per_1m=Decimal("30.00"),
    )

    class ProviderRateLimitError(RuntimeError):
        status_code = 429

    async def fake_create_pending(*_args, **_kwargs):
        return SimpleNamespace(id=call_id)

    async def fake_generate_image(*_args, **_kwargs):
        raise ProviderRateLimitError("Error code: 429 - rate limited")

    async def fake_mark_failed(_db, received_call_id, **kwargs):
        recorded["call_id"] = received_call_id
        recorded.update(kwargs)
        return True

    monkeypatch.setattr(ai_capability_kernel, "require_enabled_image_model", lambda _model_id: _image_model())
    monkeypatch.setattr(ai_capability_kernel, "require_price", lambda _pricing_ref: price)
    monkeypatch.setattr(ai_gateway_facade.PROVIDER_GATEWAY, "generate_image", fake_generate_image)
    monkeypatch.setattr(ai_capability_kernel.AiCallLogRepo, "create_pending", fake_create_pending)
    monkeypatch.setattr(ai_capability_kernel.AiCallLogRepo, "mark_failed", fake_mark_failed)

    with pytest.raises(AppError) as exc:
        await ai_gateway_facade.generate_image_with_ledger(
            caller_id="caller-1",
            scope_type="job",
            scope_id="00000000-0000-0000-0000-000000000001",
            operation="poster_title_image.generate_title",
            model_id="custom-image-model",
            response_model="gpt-5.5",
            prompt="draw",
            reference_images=[],
            size="1024x1024",
            quality="low",
            background="auto",
            output_format="png",
            job_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
            attempt_id=uuid.UUID("00000000-0000-0000-0000-000000000002"),
            job_type="poster_title_image_generate_item",
            ledger_session_factory=session_factory,
            image_adapter="openai_images",
        )

    assert exc.value.code == "AI_PROVIDER_FAILED"
    assert exc.value.details == {"provider_status_code": 429}
    assert recorded["call_id"] == call_id
    assert recorded["failure_phase"] == "provider"
    assert recorded["error_code"] == "AI_PROVIDER_FAILED"
    assert recorded["error_message"] == "Error code: 429 - rate limited"
    assert recorded["billable_status"] == "unknown"
    assert [session.commits for session in session_factory.sessions] == [1, 1]


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [409, 501])
async def test_gateway_keeps_non_transient_provider_status_as_model_call_failed(monkeypatch, status_code):
    session_factory = _FakeSessionFactory()
    call_id = uuid.uuid4()
    recorded: dict = {}

    class ProviderStatusError(RuntimeError):
        pass

    ProviderStatusError.status_code = status_code

    async def fake_create_pending(*_args, **_kwargs):
        return SimpleNamespace(id=call_id)

    async def fake_generate_text(*_args, **_kwargs):
        raise ProviderStatusError(f"Error code: {status_code} - not retryable")

    async def fake_mark_failed(_db, received_call_id, **kwargs):
        recorded["call_id"] = received_call_id
        recorded.update(kwargs)
        return True

    async def fail_mark_succeeded(*_args, **_kwargs):
        raise AssertionError("provider failure must not be marked succeeded")

    monkeypatch.setattr(ai_capability_kernel, "require_enabled_text_model", lambda _model_id: _model())
    monkeypatch.setattr(ai_capability_kernel, "require_price", lambda _pricing_ref: _price())
    monkeypatch.setattr(ai_capability_kernel.AiCallLogRepo, "create_pending", fake_create_pending)
    monkeypatch.setattr(ai_capability_kernel.AiCallLogRepo, "mark_failed", fake_mark_failed)
    monkeypatch.setattr(ai_capability_kernel.AiCallLogRepo, "mark_succeeded", fail_mark_succeeded)
    monkeypatch.setattr(
        ai_capability_kernel,
        "require_text_generation_adapter",
        lambda adapter_name: SimpleNamespace(generate_text=fake_generate_text),
    )

    with pytest.raises(AppError) as exc:
        await _call(session_factory)

    assert exc.value.code == "MODEL_CALL_FAILED"
    assert recorded["call_id"] == call_id
    assert recorded["failure_phase"] == "provider"
    assert recorded["error_code"] == "MODEL_CALL_FAILED"
    assert recorded["error_message"] == f"Error code: {status_code} - not retryable"
    assert recorded["billable_status"] == "unknown"


@pytest.mark.asyncio
async def test_gateway_does_not_replay_provider_when_terminal_ledger_update_fails(monkeypatch):
    session_factory = _FakeSessionFactory()
    call_id = uuid.uuid4()
    provider_calls = 0

    async def fake_create_pending(*_args, **_kwargs):
        return SimpleNamespace(id=call_id)

    async def fake_generate_text(*_args, **_kwargs):
        nonlocal provider_calls
        provider_calls += 1
        return TextGenerationResult(
            text="ok",
            prompt_tokens=100,
            completion_tokens=50,
            usage={"prompt_tokens": 100, "completion_tokens": 50},
        )

    async def fake_mark_succeeded(_db, received_call_id, **_kwargs):
        assert received_call_id == call_id
        return False

    monkeypatch.setattr(ai_capability_kernel, "require_enabled_text_model", lambda _model_id: _model())
    monkeypatch.setattr(ai_capability_kernel, "require_price", lambda _pricing_ref: _price())
    monkeypatch.setattr(ai_capability_kernel.AiCallLogRepo, "create_pending", fake_create_pending)
    monkeypatch.setattr(ai_capability_kernel.AiCallLogRepo, "mark_succeeded", fake_mark_succeeded)
    monkeypatch.setattr(ai_capability_kernel, "require_text_generation_adapter", lambda adapter_name: SimpleNamespace(generate_text=fake_generate_text))

    with pytest.raises(AppError) as exc:
        await _call(session_factory)

    assert exc.value.code == "AI_LEDGER_UPDATE_FAILED"
    assert exc.value.details == {"ai_call_log_id": str(call_id)}
    assert provider_calls == 1
    assert [session.commits for session in session_factory.sessions] == [1, 0]
    assert get_error_spec("AI_LEDGER_UPDATE_FAILED").retryable is False


@pytest.mark.asyncio
async def test_gateway_raises_ai_ledger_update_failed_when_failed_terminal_update_cannot_claim_row(monkeypatch):
    session_factory = _FakeSessionFactory()
    call_id = uuid.uuid4()

    async def fake_create_pending(*_args, **_kwargs):
        return SimpleNamespace(id=call_id)

    async def fake_generate_text(*_args, **_kwargs):
        return TextGenerationResult(text="ok", prompt_tokens=None, completion_tokens=None, usage=None)

    async def fake_mark_failed(_db, received_call_id, **kwargs):
        assert received_call_id == call_id
        assert kwargs["failure_phase"] == "usage"
        assert kwargs["error_code"] == "MODEL_USAGE_MISSING"
        return False

    async def fail_mark_succeeded(*_args, **_kwargs):
        raise AssertionError("usage missing must not be marked succeeded")

    monkeypatch.setattr(ai_capability_kernel, "require_enabled_text_model", lambda _model_id: _model())
    monkeypatch.setattr(ai_capability_kernel, "require_price", lambda _pricing_ref: _price())
    monkeypatch.setattr(ai_capability_kernel.AiCallLogRepo, "create_pending", fake_create_pending)
    monkeypatch.setattr(ai_capability_kernel.AiCallLogRepo, "mark_failed", fake_mark_failed)
    monkeypatch.setattr(ai_capability_kernel.AiCallLogRepo, "mark_succeeded", fail_mark_succeeded)
    monkeypatch.setattr(ai_capability_kernel, "require_text_generation_adapter", lambda adapter_name: SimpleNamespace(generate_text=fake_generate_text))

    with pytest.raises(AppError) as exc:
        await _call(session_factory)

    assert exc.value.code == "AI_LEDGER_UPDATE_FAILED"
    assert exc.value.details == {"ai_call_log_id": str(call_id), "failure_phase": "usage"}
    assert [session.commits for session in session_factory.sessions] == [1, 0]


@pytest.mark.asyncio
async def test_gateway_marks_cost_calculation_failed_and_raises_app_error_when_pricing_fails(monkeypatch):
    session_factory = _FakeSessionFactory()
    call_id = uuid.uuid4()
    recorded: dict = {}

    async def fake_create_pending(*_args, **_kwargs):
        return SimpleNamespace(id=call_id)

    async def fake_generate_text(*_args, **_kwargs):
        return TextGenerationResult(
            text="ok",
            prompt_tokens=100,
            completion_tokens=50,
            usage={"prompt_tokens": 100, "completion_tokens": 50},
        )

    def fail_calculate_cost(*_args, **_kwargs):
        raise RuntimeError("price table broken")

    async def fake_mark_failed(_db, received_call_id, **kwargs):
        recorded["call_id"] = received_call_id
        recorded.update(kwargs)
        return True

    async def fail_mark_succeeded(*_args, **_kwargs):
        raise AssertionError("pricing failure must not be marked succeeded")

    monkeypatch.setattr(ai_capability_kernel, "require_enabled_text_model", lambda _model_id: _model())
    monkeypatch.setattr(ai_capability_kernel, "require_price", lambda _pricing_ref: _price())
    monkeypatch.setattr(ai_capability_kernel, "calculate_usage_cost", fail_calculate_cost)
    monkeypatch.setattr(ai_capability_kernel.AiCallLogRepo, "create_pending", fake_create_pending)
    monkeypatch.setattr(ai_capability_kernel.AiCallLogRepo, "mark_failed", fake_mark_failed)
    monkeypatch.setattr(ai_capability_kernel.AiCallLogRepo, "mark_succeeded", fail_mark_succeeded)
    monkeypatch.setattr(ai_capability_kernel, "require_text_generation_adapter", lambda adapter_name: SimpleNamespace(generate_text=fake_generate_text))

    with pytest.raises(AppError) as exc:
        await _call(session_factory)

    assert exc.value.code == "MODEL_COST_CALCULATION_FAILED"
    assert recorded["call_id"] == call_id
    assert recorded["failure_phase"] == "pricing"
    assert recorded["error_code"] == "MODEL_COST_CALCULATION_FAILED"
    assert recorded["billable_status"] == "unknown"
    assert recorded["cost_calculation_status"] == "failed"
    assert [session.commits for session in session_factory.sessions] == [1, 1]


@pytest.mark.asyncio
async def test_gateway_rejects_non_token_pricing_for_text_usage(monkeypatch):
    session_factory = _FakeSessionFactory()
    call_id = uuid.uuid4()
    recorded: dict = {}
    call_price = CallPrice(
        ref="openai:custom-model@2026-06-23",
        model_id="custom-model",
        provider="openai",
        provider_model="custom-model",
        pricing_type="per_call",
        version="2026-06-23",
        currency="USD",
        amount_per_call=Decimal("0.01000000"),
    )

    async def fake_create_pending(*_args, **_kwargs):
        return SimpleNamespace(id=call_id)

    async def fake_generate_text(*_args, **_kwargs):
        return TextGenerationResult(
            text="ok",
            prompt_tokens=100,
            completion_tokens=50,
            usage={"prompt_tokens": 100, "completion_tokens": 50},
        )

    async def fake_mark_failed(_db, received_call_id, **kwargs):
        recorded["call_id"] = received_call_id
        recorded.update(kwargs)
        return True

    async def fail_mark_succeeded(*_args, **_kwargs):
        raise AssertionError("text usage with non-token pricing must not be marked succeeded")

    monkeypatch.setattr(ai_capability_kernel, "require_enabled_text_model", lambda _model_id: _model())
    monkeypatch.setattr(ai_capability_kernel, "require_price", lambda _pricing_ref: call_price)
    monkeypatch.setattr(ai_capability_kernel.AiCallLogRepo, "create_pending", fake_create_pending)
    monkeypatch.setattr(ai_capability_kernel.AiCallLogRepo, "mark_failed", fake_mark_failed)
    monkeypatch.setattr(ai_capability_kernel.AiCallLogRepo, "mark_succeeded", fail_mark_succeeded)
    monkeypatch.setattr(ai_capability_kernel, "require_text_generation_adapter", lambda adapter_name: SimpleNamespace(generate_text=fake_generate_text))

    with pytest.raises(AppError) as exc:
        await _call(session_factory)

    assert exc.value.code == "MODEL_COST_CALCULATION_FAILED"
    assert recorded["call_id"] == call_id
    assert recorded["failure_phase"] == "pricing"
    assert recorded["error_code"] == "MODEL_COST_CALCULATION_FAILED"
    assert recorded["billable_status"] == "unknown"
    assert recorded["cost_calculation_status"] == "failed"
    assert [session.commits for session in session_factory.sessions] == [1, 1]
