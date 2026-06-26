from __future__ import annotations

from dataclasses import dataclass

from app.integrations.ai_adapters.base import TextGenerationAdapter
from app.integrations.ai_adapters.litellm_adapter import LiteLLMAdapter


@dataclass(frozen=True)
class AdapterRegistration:
    name: str
    text_generation_adapter: TextGenerationAdapter | None = None


_ADAPTERS: dict[str, AdapterRegistration] = {
    "litellm": AdapterRegistration(name="litellm", text_generation_adapter=LiteLLMAdapter()),
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


def validate_model_adapter(adapter_name: str) -> None:
    if adapter_name not in _ADAPTERS:
        raise RuntimeError(f"model adapter not found: {adapter_name}")


def validate_text_generation_adapter(adapter_name: str) -> None:
    validate_model_adapter(adapter_name)
    if get_text_generation_adapter(adapter_name) is None:
        raise RuntimeError(f"text generation adapter not found: {adapter_name}")
