from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from app.core.usage_records import AudioUsageRecord, ImageUsageRecord, TextUsageRecord, VideoUsageRecord


@dataclass(frozen=True)
class ResolvedModel:
    model_id: str
    provider: str
    provider_model: str
    litellm_model: str
    pricing_ref: str


@dataclass(frozen=True)
class ModelGateDecision:
    allowed: bool
    model_id: str
    reason: str | None = None


UsageRecord: TypeAlias = TextUsageRecord | ImageUsageRecord | AudioUsageRecord | VideoUsageRecord
