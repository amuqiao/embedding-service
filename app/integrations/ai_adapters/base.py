from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from typing import Protocol


@dataclass(frozen=True)
class TextGenerationRequest:
    adapter_model: str
    messages: list[dict[str, str]]
    temperature: float
    timeout_seconds: int
    api_key: str | None
    api_base: str | None
    num_retries: int
    drop_params: bool


@dataclass
class TextGenerationResult:
    text: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    usage: dict[str, Any] | None = None


@dataclass(frozen=True)
class ImageInput:
    data: bytes
    content_type: str
    detail: str = "high"


@dataclass(frozen=True)
class MultimodalTextGenerationRequest:
    adapter_model: str
    provider_model: str
    prompt: str
    reference_images: list[ImageInput]
    timeout_seconds: int
    api_key: str | None
    api_base: str | None


@dataclass(frozen=True)
class ImageGenerationRequest:
    adapter_model: str
    provider_model: str
    response_model: str
    prompt: str
    reference_images: list[ImageInput]
    size: str
    quality: str
    background: str
    output_format: str
    timeout_seconds: int
    api_key: str | None
    api_base: str | None


@dataclass
class ImageGenerationResult:
    images: list[bytes]
    revised_prompt: str | None = None
    usage: dict[str, Any] | None = None


class TextGenerationAdapter(Protocol):
    async def generate_text(self, request: TextGenerationRequest) -> TextGenerationResult:
        """Generate text for a normalized provider request."""


class MultimodalTextGenerationAdapter(Protocol):
    async def generate_text_with_images(self, request: MultimodalTextGenerationRequest) -> TextGenerationResult:
        """Generate text from a prompt plus image inputs for a normalized provider request."""


class ImageGenerationAdapter(Protocol):
    async def generate_image(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        """Generate or edit an image for a normalized provider request."""
