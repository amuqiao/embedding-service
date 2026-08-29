from __future__ import annotations

from typing import Any

from openai import AsyncOpenAI

from app.ai.adapters.base import EmbeddingRequest, EmbeddingResult


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
    raise RuntimeError("OpenAI-compatible embeddings usage payload must be an object")


class OpenAICompatibleEmbeddingsAdapter:
    async def embed(self, request: EmbeddingRequest) -> EmbeddingResult:
        client = _client(
            api_key=request.api_key,
            timeout_seconds=request.timeout_seconds,
            api_base=request.api_base,
        )
        kwargs: dict[str, Any] = {
            "model": request.adapter_model,
            "input": request.input_texts,
        }
        if request.dimensions is not None:
            kwargs["dimensions"] = request.dimensions
        response = await client.embeddings.create(**kwargs)
        data = getattr(response, "data", None)
        if not isinstance(data, list):
            raise RuntimeError("OpenAI-compatible embeddings response data must be a list")
        vectors: list[list[float]] = []
        for item in data:
            vector = getattr(item, "embedding", None)
            if not isinstance(vector, list):
                raise RuntimeError("OpenAI-compatible embeddings response item is missing embedding")
            vectors.append([float(value) for value in vector])
        return EmbeddingResult(vectors=vectors, usage=_usage_dict(response))
