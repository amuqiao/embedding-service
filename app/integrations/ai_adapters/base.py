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


class TextGenerationAdapter(Protocol):
    async def generate_text(self, request: TextGenerationRequest) -> TextGenerationResult:
        """Generate text for a normalized provider request."""
