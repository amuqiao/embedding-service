from __future__ import annotations

import base64
import io
from typing import Any

from openai import AsyncOpenAI

from app.integrations.ai_adapters.base import ImageGenerationRequest, ImageGenerationResult


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


def _extension_for_content_type(content_type: str) -> str:
    if content_type == "image/png":
        return "png"
    if content_type == "image/jpeg":
        return "jpg"
    if content_type == "image/webp":
        return "webp"
    raise RuntimeError(f"unsupported image content type for OpenAI Images API: {content_type}")


def _image_file_tuple(index: int, data: bytes, content_type: str) -> tuple[str, io.BytesIO, str]:
    stream = io.BytesIO(data)
    return f"reference-{index}.{_extension_for_content_type(content_type)}", stream, content_type


def _image_bytes_from_response(response: Any) -> tuple[list[bytes], str | None]:
    data = getattr(response, "data", None)
    if not isinstance(data, list):
        raise RuntimeError("OpenAI Images response data must be a list")

    images: list[bytes] = []
    revised_prompt: str | None = None
    for item in data:
        encoded = getattr(item, "b64_json", None)
        if not isinstance(encoded, str) or not encoded:
            raise RuntimeError("OpenAI Images response item is missing b64_json")
        images.append(base64.b64decode(encoded))
        if revised_prompt is None:
            value = getattr(item, "revised_prompt", None)
            revised_prompt = value if isinstance(value, str) and value else None
    return images, revised_prompt


class OpenAIImagesAdapter:
    async def generate_image(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        client = _client(
            api_key=request.api_key,
            timeout_seconds=request.timeout_seconds,
            api_base=request.api_base,
        )
        common: dict[str, Any] = {
            "model": request.provider_model,
            "prompt": request.prompt,
            "size": request.size,
            "quality": request.quality,
            "background": request.background,
            "output_format": request.output_format,
            "n": 1,
        }

        if request.reference_images:
            image_files = [
                _image_file_tuple(index, image.data, image.content_type)
                for index, image in enumerate(request.reference_images, start=1)
            ]
            response = await client.images.edit(
                **common,
                image=image_files[0] if len(image_files) == 1 else image_files,
            )
        else:
            response = await client.images.generate(**common)

        images, revised_prompt = _image_bytes_from_response(response)
        return ImageGenerationResult(
            images=images,
            revised_prompt=revised_prompt,
            usage={"image_count": len(images), "api": "images"},
        )
