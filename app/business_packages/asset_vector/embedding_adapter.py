from __future__ import annotations

import hashlib
import math
import re

from app.business_packages.asset_vector.schemas import AssetVectorAssetRef, AssetVectorTextQuery, AssetVectorUpsertItemParams

VECTOR_DIMENSIONS = 64
TOKEN_RE = re.compile(r"[0-9A-Za-z_\-\u4e00-\u9fff]+")


def _tokens(text: str) -> list[str]:
    return [token.lower() for token in TOKEN_RE.findall(text)]


def _slot(token: str) -> tuple[int, float]:
    digest = hashlib.sha256(token.encode("utf-8")).digest()
    index = int.from_bytes(digest[:4], "big") % VECTOR_DIMENSIONS
    sign = 1.0 if digest[4] % 2 == 0 else -1.0
    weight = 1.0 + (digest[5] / 255.0)
    return index, sign * weight


def _normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        return vector
    return [value / norm for value in vector]


def text_embedding(text: str) -> list[float]:
    vector = [0.0] * VECTOR_DIMENSIONS
    for token in _tokens(text):
        index, value = _slot(token)
        vector[index] += value
    if not any(vector):
        index, value = _slot(text)
        vector[index] = value
    return _normalize(vector)


def item_index_text(item: AssetVectorUpsertItemParams) -> str:
    label_parts: list[str] = []
    for label in item.labels:
        label_parts.append(label.label_name)
        if label.definition:
            label_parts.append(label.definition)
    return " ".join(
        part
        for part in (
            item.item_id,
            item.item_name,
            item.asset.public_url,
            item.asset.content_type,
            " ".join(label_parts),
        )
        if part
    )


def item_embedding(item: AssetVectorUpsertItemParams) -> list[float]:
    return text_embedding(item_index_text(item))


def asset_query_embedding(asset: AssetVectorAssetRef) -> list[float]:
    return text_embedding(f"{asset.public_url} {asset.content_type}")


def text_query_embedding(text: AssetVectorTextQuery) -> list[float]:
    return text_embedding(text.query)


def average_embeddings(vectors: list[list[float]]) -> list[float]:
    if not vectors:
        raise ValueError("vectors must not be empty")
    dimensions = len(vectors[0])
    if any(len(vector) != dimensions for vector in vectors):
        raise ValueError("vectors must have same dimensions")
    return _normalize([sum(vector[index] for vector in vectors) / len(vectors) for index in range(dimensions)])


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        raise ValueError("vectors must have same dimensions")
    return sum(left_value * right_value for left_value, right_value in zip(left, right, strict=True))
