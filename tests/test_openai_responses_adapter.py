from types import SimpleNamespace

import pytest

from app.integrations.ai_adapters import openai_images_adapter
from app.integrations.ai_adapters import openai_responses_adapter
from app.integrations.ai_adapters.base import ImageGenerationRequest, ImageInput, MultimodalTextGenerationRequest
from app.integrations.ai_adapters.openai_images_adapter import OpenAIImagesAdapter
from app.integrations.ai_adapters.openai_responses_adapter import OpenAIResponsesAdapter


@pytest.mark.asyncio
async def test_generate_text_with_images_uses_provider_model(monkeypatch):
    recorded: dict = {}

    class FakeResponses:
        async def create(self, **kwargs):
            recorded.update(kwargs)
            return SimpleNamespace(
                output=[
                    SimpleNamespace(
                        type="message",
                        content=[SimpleNamespace(type="output_text", text="ok")],
                    )
                ],
                usage=SimpleNamespace(model_dump=lambda: {"input_tokens": 3, "output_tokens": 4}),
            )

    monkeypatch.setattr(
        openai_responses_adapter,
        "_client",
        lambda **_kwargs: SimpleNamespace(responses=FakeResponses()),
    )

    result = await OpenAIResponsesAdapter().generate_text_with_images(
        MultimodalTextGenerationRequest(
            adapter_model="openai/gpt-4o",
            provider_model="gpt-4o",
            prompt="describe title style",
            reference_images=[ImageInput(data=b"image", content_type="image/png", detail="low")],
            timeout_seconds=30,
            api_key="test-key",
            api_base=None,
        )
    )

    assert recorded["model"] == "gpt-4o"
    assert recorded["input"][0]["role"] == "user"
    assert recorded["input"][0]["content"][0]["type"] == "input_image"
    assert recorded["input"][0]["content"][1] == {"type": "input_text", "text": "describe title style"}
    assert result.text == "ok"
    assert result.prompt_tokens == 3
    assert result.completion_tokens == 4


@pytest.mark.asyncio
async def test_generate_image_uses_response_model_and_provider_tool_model(monkeypatch):
    recorded: dict = {}

    class FakeResponses:
        async def create(self, **kwargs):
            recorded.update(kwargs)
            return SimpleNamespace(
                output=[
                    SimpleNamespace(
                        type="image_generation_call",
                        result="cG5n",
                        revised_prompt="revised",
                    )
                ],
                usage=None,
            )

    monkeypatch.setattr(
        openai_responses_adapter,
        "_client",
        lambda **_kwargs: SimpleNamespace(responses=FakeResponses()),
    )

    result = await OpenAIResponsesAdapter().generate_image(
        ImageGenerationRequest(
            adapter_model="openai/gpt-image-2",
            provider_model="gpt-image-2",
            response_model="gpt-4o",
            prompt="draw title",
            reference_images=[ImageInput(data=b"image", content_type="image/png", detail="low")],
            size="auto",
            quality="high",
            background="auto",
            output_format="png",
            timeout_seconds=30,
            api_key="test-key",
            api_base=None,
        )
    )

    assert recorded["model"] == "gpt-4o"
    assert recorded["tools"][0]["model"] == "gpt-image-2"
    assert recorded["tools"][0]["action"] == "edit"
    assert recorded["tools"][0]["background"] == "auto"
    assert recorded["tool_choice"] == {"type": "image_generation"}
    assert result.images == [b"png"]
    assert result.revised_prompt == "revised"


@pytest.mark.asyncio
async def test_openai_responses_adapter_generates_without_reference_images(monkeypatch):
    recorded: dict = {}

    class FakeResponses:
        async def create(self, **kwargs):
            recorded.update(kwargs)
            return SimpleNamespace(
                output=[
                    SimpleNamespace(
                        type="image_generation_call",
                        result="cG5n",
                        revised_prompt=None,
                    )
                ],
                usage=None,
            )

    monkeypatch.setattr(
        openai_responses_adapter,
        "_client",
        lambda **_kwargs: SimpleNamespace(responses=FakeResponses()),
    )

    result = await OpenAIResponsesAdapter().generate_image(
        ImageGenerationRequest(
            adapter_model="openai/gpt-image-2",
            provider_model="gpt-image-2",
            response_model="gpt-4o",
            prompt="draw title",
            reference_images=[],
            size="1024x1024",
            quality="medium",
            background="opaque",
            output_format="png",
            timeout_seconds=30,
            api_key="test-key",
            api_base=None,
        )
    )

    assert recorded["tools"][0]["action"] == "generate"
    assert result.images == [b"png"]


@pytest.mark.asyncio
async def test_openai_images_adapter_edits_with_provider_model(monkeypatch):
    recorded: dict = {}

    class FakeImages:
        async def edit(self, **kwargs):
            recorded.update(kwargs)
            return SimpleNamespace(data=[SimpleNamespace(b64_json="cG5n", revised_prompt="revised")])

    monkeypatch.setattr(
        openai_images_adapter,
        "_client",
        lambda **_kwargs: SimpleNamespace(images=FakeImages()),
    )

    result = await OpenAIImagesAdapter().generate_image(
        ImageGenerationRequest(
            adapter_model="openai/gpt-image-2",
            provider_model="gpt-image-2",
            response_model="gpt-4o",
            prompt="draw title",
            reference_images=[ImageInput(data=b"image", content_type="image/png", detail="low")],
            size="auto",
            quality="high",
            background="auto",
            output_format="png",
            timeout_seconds=30,
            api_key="test-key",
            api_base=None,
        )
    )

    assert recorded["model"] == "gpt-image-2"
    assert recorded["prompt"] == "draw title"
    assert recorded["size"] == "auto"
    assert recorded["quality"] == "high"
    assert recorded["background"] == "auto"
    assert recorded["output_format"] == "png"
    assert recorded["n"] == 1
    assert recorded["image"][0] == "reference-1.png"
    assert recorded["image"][1].read() == b"image"
    assert recorded["image"][2] == "image/png"
    assert result.images == [b"png"]
    assert result.revised_prompt == "revised"
    assert result.usage == {"image_count": 1, "api": "images"}


@pytest.mark.asyncio
async def test_openai_images_adapter_generates_without_reference_images(monkeypatch):
    recorded: dict = {}

    class FakeImages:
        async def generate(self, **kwargs):
            recorded.update(kwargs)
            return SimpleNamespace(data=[SimpleNamespace(b64_json="cG5n", revised_prompt=None)])

    monkeypatch.setattr(
        openai_images_adapter,
        "_client",
        lambda **_kwargs: SimpleNamespace(images=FakeImages()),
    )

    result = await OpenAIImagesAdapter().generate_image(
        ImageGenerationRequest(
            adapter_model="openai/gpt-image-2",
            provider_model="gpt-image-2",
            response_model="gpt-4o",
            prompt="draw title",
            reference_images=[],
            size="1024x1024",
            quality="medium",
            background="opaque",
            output_format="png",
            timeout_seconds=30,
            api_key="test-key",
            api_base=None,
        )
    )

    assert recorded["model"] == "gpt-image-2"
    assert "image" not in recorded
    assert result.images == [b"png"]
