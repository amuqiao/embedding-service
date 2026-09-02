from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any

import httpx

from app.business_packages.asset_vector.schemas import (
    AssetVectorAssetRef,
    AssetVectorTextQuery,
    AssetVectorUpsertItemParams,
)
from app.core.config import settings
from app.core.exceptions import AppError

MULTIMODAL_EMBEDDING_PATH = "/services/embeddings/multimodal-embedding/multimodal-embedding"


@dataclass(frozen=True)
class AssetVectorEmbeddingConfig:
    api_key: str
    base_url: str
    model_id: str
    dimension: int
    timeout_seconds: int

    @property
    def endpoint(self) -> str:
        return f"{self.base_url.rstrip('/')}{MULTIMODAL_EMBEDDING_PATH}"


def normalize_dashscope_native_base_url(base_url: str) -> str:
    value = base_url.strip().rstrip("/")
    if not value:
        raise ValueError("DashScope base_url must not be empty")
    if value.endswith("/compatible-mode/v1"):
        return value[: -len("/compatible-mode/v1")] + "/api/v1"
    if value.endswith("/api/v1"):
        return value
    if "/services/embeddings/" in value:
        raise ValueError("DashScope base_url must not include concrete embedding service path")
    raise ValueError("DashScope base_url must end with /api/v1 or /compatible-mode/v1")


def asset_vector_embedding_config_from_settings() -> AssetVectorEmbeddingConfig:
    api_key = settings.job.asset_vector.dashscope_api_key_value or settings.ai_provider.dashscope_api_key_value
    base_url = settings.job.asset_vector.dashscope_base_url or settings.ai_provider.dashscope_base_url
    if not api_key:
        raise AppError(
            "MODEL_CALL_FAILED",
            "asset_vector DashScope API key is not configured",
            details={"job_type": "asset_vector", "provider": "dashscope"},
        )
    try:
        native_base_url = normalize_dashscope_native_base_url(base_url)
    except ValueError as exc:
        raise AppError(
            "MODEL_CALL_FAILED",
            "asset_vector DashScope base_url is invalid",
            details={"base_url": base_url, "message": str(exc)},
        ) from exc
    return AssetVectorEmbeddingConfig(
        api_key=api_key,
        base_url=native_base_url,
        model_id=settings.job.asset_vector.embedding_model,
        dimension=settings.job.asset_vector.embedding_dimension,
        timeout_seconds=settings.ai_provider.model_call_timeout_seconds,
    )


class DashScopeMultimodalEmbeddingAdapter:
    def __init__(self, config: AssetVectorEmbeddingConfig | None = None) -> None:
        self.config = config or asset_vector_embedding_config_from_settings()

    async def embed(self, content: dict[str, str]) -> list[float]:
        payload = {
            "model": self.config.model_id,
            "input": {"contents": [content]},
            "parameters": {"dimension": self.config.dimension},
        }
        try:
            async with httpx.AsyncClient(timeout=self.config.timeout_seconds) as client:
                response = await client.post(
                    self.config.endpoint,
                    headers={
                        "Authorization": f"Bearer {self.config.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
        except httpx.TimeoutException as exc:
            raise AppError(
                "MODEL_CALL_TIMEOUT",
                "asset_vector DashScope embedding timed out",
                details={
                    "base_url": self.config.base_url,
                    "model_id": self.config.model_id,
                    "timeout_seconds": self.config.timeout_seconds,
                },
            ) from exc
        except httpx.HTTPError as exc:
            raise AppError(
                "MODEL_CALL_FAILED",
                "asset_vector DashScope embedding request failed",
                details={
                    "base_url": self.config.base_url,
                    "model_id": self.config.model_id,
                    "provider_message": str(exc),
                },
            ) from exc
        if response.status_code >= 400:
            raise AppError(
                "MODEL_CALL_FAILED",
                "asset_vector DashScope embedding failed",
                details=_provider_error_details(
                    response,
                    base_url=self.config.base_url,
                    model_id=self.config.model_id,
                ),
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise AppError(
                "MODEL_OUTPUT_INVALID",
                "asset_vector DashScope response is not valid JSON",
                details={"base_url": self.config.base_url, "model_id": self.config.model_id},
            ) from exc
        if not isinstance(data, dict):
            raise AppError(
                "MODEL_OUTPUT_INVALID",
                "asset_vector DashScope response must be a JSON object",
                details={"base_url": self.config.base_url, "model_id": self.config.model_id},
            )
        return extract_embedding(data, expected_dimension=self.config.dimension)


def _provider_error_details(response: httpx.Response, *, base_url: str, model_id: str) -> dict[str, Any]:
    details: dict[str, Any] = {
        "base_url": base_url,
        "model_id": model_id,
        "provider_status_code": response.status_code,
    }
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        error_payload = payload.get("error")
        error_object = error_payload if isinstance(error_payload, dict) else {}
        code = payload.get("code") or error_object.get("code")
        message = payload.get("message") or error_object.get("message")
        if code is not None:
            details["provider_code"] = str(code)
        if message is not None:
            details["provider_message"] = str(message)
    else:
        details["provider_message"] = response.text[:500]
    return details


def extract_embedding(data: dict[str, Any], *, expected_dimension: int) -> list[float]:
    output = data.get("output")
    embeddings = output.get("embeddings") if isinstance(output, dict) else None
    if not isinstance(embeddings, list) or not embeddings:
        raise AppError("MODEL_OUTPUT_INVALID", "asset_vector DashScope response missing output.embeddings")
    first = embeddings[0]
    embedding = first.get("embedding") if isinstance(first, dict) else None
    if not isinstance(embedding, list) or not embedding:
        raise AppError("MODEL_OUTPUT_INVALID", "asset_vector DashScope response missing embedding list")
    try:
        vector = [float(value) for value in embedding]
    except (TypeError, ValueError) as exc:
        raise AppError("MODEL_OUTPUT_INVALID", "asset_vector embedding contains non-numeric values") from exc
    if len(vector) != expected_dimension:
        raise AppError(
            "MODEL_OUTPUT_INVALID",
            "asset_vector embedding dimension mismatch",
            details={"expected_dimension": expected_dimension, "actual_dimension": len(vector)},
        )
    return vector


def item_index_text(item: AssetVectorUpsertItemParams) -> str:
    label_parts: list[str] = []
    for label in item.labels:
        label_parts.append(f"{label.language}: {label.label_name}")
        if label.definition:
            label_parts.append(f"{label.language}: {label.definition}")
    return " ".join(
        part
        for part in (
            item.item_name,
            item.asset.content_type,
            " ".join(label_parts),
        )
        if part
    )


def item_embedding_content(item: AssetVectorUpsertItemParams) -> dict[str, str]:
    return {"image": item.asset.public_url, "text": item_index_text(item)}


def asset_query_content(asset: AssetVectorAssetRef) -> dict[str, str]:
    return {"image": asset.public_url}


def text_query_content(text: AssetVectorTextQuery) -> dict[str, str]:
    return {"text": text.query}


def input_sha256(content: dict[str, str]) -> str:
    payload = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def normalize_embedding(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        raise ValueError("embedding vector norm must not be zero")
    return [value / norm for value in vector]


def average_embeddings(vectors: list[list[float]]) -> list[float]:
    if not vectors:
        raise ValueError("vectors must not be empty")
    dimensions = len(vectors[0])
    if any(len(vector) != dimensions for vector in vectors):
        raise ValueError("vectors must have same dimensions")
    return normalize_embedding([sum(vector[index] for vector in vectors) / len(vectors) for index in range(dimensions)])
