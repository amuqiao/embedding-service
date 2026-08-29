from __future__ import annotations

from dataclasses import dataclass

from app.ai.adapters.base import (
    EmbeddingAdapter,
    ImageGenerationAdapter,
    MultimodalTextGenerationAdapter,
    TextGenerationAdapter,
)
from app.ai.adapters.litellm_adapter import LiteLLMAdapter
from app.ai.adapters.openai_compatible_embeddings_adapter import OpenAICompatibleEmbeddingsAdapter
from app.ai.adapters.openai_images_adapter import OpenAIImagesAdapter
from app.ai.adapters.openai_responses_adapter import OpenAIResponsesAdapter


@dataclass(frozen=True)
class AdapterRegistration:
    name: str
    text_generation_adapter: TextGenerationAdapter | None = None
    multimodal_text_generation_adapter: MultimodalTextGenerationAdapter | None = None
    image_generation_adapter: ImageGenerationAdapter | None = None
    embedding_adapter: EmbeddingAdapter | None = None


_OPENAI_RESPONSES_ADAPTER = OpenAIResponsesAdapter()
_OPENAI_IMAGES_ADAPTER = OpenAIImagesAdapter()
_OPENAI_COMPATIBLE_EMBEDDINGS_ADAPTER = OpenAICompatibleEmbeddingsAdapter()

_ADAPTERS: dict[str, AdapterRegistration] = {
    "litellm": AdapterRegistration(
        name="litellm",
        text_generation_adapter=LiteLLMAdapter(),
        multimodal_text_generation_adapter=_OPENAI_RESPONSES_ADAPTER,
        image_generation_adapter=_OPENAI_RESPONSES_ADAPTER,
    ),
    "openai_responses": AdapterRegistration(
        name="openai_responses",
        multimodal_text_generation_adapter=_OPENAI_RESPONSES_ADAPTER,
        image_generation_adapter=_OPENAI_RESPONSES_ADAPTER,
    ),
    "openai_images": AdapterRegistration(
        name="openai_images",
        image_generation_adapter=_OPENAI_IMAGES_ADAPTER,
    ),
    "openai_compatible_embeddings": AdapterRegistration(
        name="openai_compatible_embeddings",
        embedding_adapter=_OPENAI_COMPATIBLE_EMBEDDINGS_ADAPTER,
    ),
}


def get_text_generation_adapter(adapter_name: str) -> TextGenerationAdapter | None:
    registration = _ADAPTERS.get(adapter_name)
    if registration is None:
        return None
    return registration.text_generation_adapter


def require_text_generation_adapter(adapter_name: str) -> TextGenerationAdapter:
    adapter = get_text_generation_adapter(adapter_name)
    if adapter is None:
        raise RuntimeError(f"text generation adapter not found: {adapter_name}")
    return adapter


def get_multimodal_text_generation_adapter(adapter_name: str) -> MultimodalTextGenerationAdapter | None:
    registration = _ADAPTERS.get(adapter_name)
    if registration is None:
        return None
    return registration.multimodal_text_generation_adapter


def require_multimodal_text_generation_adapter(adapter_name: str) -> MultimodalTextGenerationAdapter:
    adapter = get_multimodal_text_generation_adapter(adapter_name)
    if adapter is None:
        raise RuntimeError(f"multimodal text generation adapter not found: {adapter_name}")
    return adapter


def get_image_generation_adapter(adapter_name: str) -> ImageGenerationAdapter | None:
    registration = _ADAPTERS.get(adapter_name)
    if registration is None:
        return None
    return registration.image_generation_adapter


def require_image_generation_adapter(adapter_name: str) -> ImageGenerationAdapter:
    adapter = get_image_generation_adapter(adapter_name)
    if adapter is None:
        raise RuntimeError(f"image generation adapter not found: {adapter_name}")
    return adapter


def get_embedding_adapter(adapter_name: str) -> EmbeddingAdapter | None:
    registration = _ADAPTERS.get(adapter_name)
    if registration is None:
        return None
    return registration.embedding_adapter


def require_embedding_adapter(adapter_name: str) -> EmbeddingAdapter:
    adapter = get_embedding_adapter(adapter_name)
    if adapter is None:
        raise RuntimeError(f"embedding adapter not found: {adapter_name}")
    return adapter


def validate_model_adapter(adapter_name: str) -> None:
    if adapter_name not in _ADAPTERS:
        raise RuntimeError(f"model adapter not found: {adapter_name}")


def validate_text_generation_adapter(adapter_name: str) -> None:
    validate_model_adapter(adapter_name)
    if get_text_generation_adapter(adapter_name) is None:
        raise RuntimeError(f"text generation adapter not found: {adapter_name}")


def validate_image_generation_adapter(adapter_name: str) -> None:
    validate_model_adapter(adapter_name)
    if get_image_generation_adapter(adapter_name) is None:
        raise RuntimeError(f"image generation adapter not found: {adapter_name}")


def validate_multimodal_text_generation_adapter(adapter_name: str) -> None:
    validate_model_adapter(adapter_name)
    if get_multimodal_text_generation_adapter(adapter_name) is None:
        raise RuntimeError(f"multimodal text generation adapter not found: {adapter_name}")


def validate_embedding_adapter(adapter_name: str) -> None:
    validate_model_adapter(adapter_name)
    if get_embedding_adapter(adapter_name) is None:
        raise RuntimeError(f"embedding adapter not found: {adapter_name}")
