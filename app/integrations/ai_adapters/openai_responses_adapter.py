from __future__ import annotations

import base64
from typing import Any

from openai import AsyncOpenAI

from app.integrations.ai_adapters.base import (
    ImageGenerationRequest,
    ImageGenerationResult,
    MultimodalTextGenerationRequest,
    TextGenerationResult,
)


def _client(
    *,
    api_key: str | None,
    timeout_seconds: int,
    api_base: str | None,
) -> AsyncOpenAI:
    kwargs: dict[str, Any] = {
        "api_key": api_key,
        "timeout": timeout_seconds,
        "max_retries": 0,
    }
    if api_base:
        kwargs["base_url"] = api_base
    return AsyncOpenAI(**kwargs)


def _image_data_url(data: bytes, content_type: str) -> str:
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{content_type};base64,{encoded}"


def _usage_dict(response: Any) -> dict[str, Any] | None:
    usage = getattr(response, "usage", None)
    if usage is None:
        return None
    if hasattr(usage, "model_dump"):
        dumped = usage.model_dump()
        if isinstance(dumped, dict):
            return dumped
    if isinstance(usage, dict):
        return usage
    raise RuntimeError("OpenAI Responses usage payload must be an object")


def _usage_int(usage: dict[str, Any] | None, key: str) -> int | None:
    if usage is None:
        return None
    value = usage.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return int(value)


def _text_from_response(response: Any) -> str:
    texts: list[str] = []
    for item in response.output:
        if getattr(item, "type", None) != "message":
            continue
        for content in getattr(item, "content", []):
            if getattr(content, "type", None) == "output_text":
                texts.append(str(content.text))
    return "\n".join(texts).strip()


class OpenAIResponsesAdapter:
    async def generate_text_with_images(self, request: MultimodalTextGenerationRequest) -> TextGenerationResult:
        content: list[dict[str, Any]] = []
        for image in request.reference_images:
            content.append(
                {
                    "type": "input_image",
                    "image_url": _image_data_url(image.data, image.content_type),
                    "detail": image.detail,
                }
            )
        content.append({"type": "input_text", "text": request.prompt})

        response = await _client(
            api_key=request.api_key,
            timeout_seconds=request.timeout_seconds,
            api_base=request.api_base,
        ).responses.create(
            model=request.provider_model,
            input=[{"role": "user", "content": content}],
        )
        usage = _usage_dict(response)
        return TextGenerationResult(
            text=_text_from_response(response),
            prompt_tokens=_usage_int(usage, "input_tokens"),
            completion_tokens=_usage_int(usage, "output_tokens"),
            usage=usage,
        )

    async def generate_image(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        content: list[dict[str, Any]] = [{"type": "input_text", "text": request.prompt}]
        for image in request.reference_images:
            content.append(
                {
                    "type": "input_image",
                    "image_url": _image_data_url(image.data, image.content_type),
                    "detail": image.detail,
                }
            )

        response = await _client(
            api_key=request.api_key,
            timeout_seconds=request.timeout_seconds,
            api_base=request.api_base,
        ).responses.create(
            model=request.response_model,
            input=[{"role": "user", "content": content}],
            tools=[
                {
                    "type": "image_generation",
                    "model": request.provider_model,
                    "action": "edit" if request.reference_images else "generate",
                    "size": request.size,
                    "quality": request.quality,
                    "background": request.background,
                    "output_format": request.output_format,
                }
            ],
            tool_choice={"type": "image_generation"},
        )

        image_calls = [item for item in response.output if getattr(item, "type", None) == "image_generation_call"]
        images: list[bytes] = []
        revised_prompt: str | None = None
        for image_call in image_calls:
            result = getattr(image_call, "result", None)
            if isinstance(result, str) and result:
                images.append(base64.b64decode(result))
            if revised_prompt is None:
                value = getattr(image_call, "revised_prompt", None)
                revised_prompt = value if isinstance(value, str) and value else None

        usage: dict[str, Any] = {"image_generation_call_count": len(image_calls)}
        provider_usage = _usage_dict(response)
        if provider_usage is not None:
            usage["provider_usage"] = provider_usage
        return ImageGenerationResult(
            images=images,
            revised_prompt=revised_prompt,
            usage=usage,
        )
