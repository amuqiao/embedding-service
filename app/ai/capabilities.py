from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from app.ai.usage.records import AudioUsageRecord, ImageUsageRecord, TextUsageRecord, VideoUsageRecord

TEXT_GENERATION = "text_generation"
MULTIMODAL_TEXT_GENERATION = "multimodal_text_generation"
IMAGE_GENERATION = "image_generation"
IMAGE_EDIT = "image_edit"
EMBEDDINGS = "embeddings"
RERANK = "rerank"
TTS = "tts"
VIDEO_GENERATION = "video_generation"

KNOWN_MODEL_TYPES = frozenset({"text", "image", "embedding", "audio", "video"})
KNOWN_MODEL_CAPABILITIES = frozenset(
    {
        TEXT_GENERATION,
        MULTIMODAL_TEXT_GENERATION,
        IMAGE_GENERATION,
        IMAGE_EDIT,
        EMBEDDINGS,
        RERANK,
        TTS,
        VIDEO_GENERATION,
    }
)


@dataclass(frozen=True)
class ResolvedModel:
    model_id: str
    capability: str
    provider: str
    adapter: str
    provider_model: str
    adapter_model: str
    pricing_ref: str
    route_config_hash: str


@dataclass(frozen=True)
class ModelGateDecision:
    allowed: bool
    model_id: str
    reason: str | None = None


UsageRecord: TypeAlias = TextUsageRecord | ImageUsageRecord | AudioUsageRecord | VideoUsageRecord
