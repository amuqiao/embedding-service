from __future__ import annotations

from typing import Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

USAGE_SCHEMA_VERSION = "1"


def _strict_non_negative_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be a non-negative integer")
    if value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")
    return value


class UsageRecordBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: str
    schema_version: Literal["1"] = USAGE_SCHEMA_VERSION
    raw_usage: dict[str, Any] = Field(default_factory=dict)


class TextUsageRecord(UsageRecordBase):
    kind: Literal["text"] = "text"
    input_tokens: int
    cached_input_tokens: int = 0
    output_tokens: int
    total_tokens: int

    @field_validator("input_tokens", "cached_input_tokens", "output_tokens", "total_tokens", mode="before")
    @classmethod
    def _validate_token_count(cls, value: Any, info: ValidationInfo) -> int:
        return _strict_non_negative_int(value, info.field_name)

    @model_validator(mode="after")
    def _validate_token_relationships(self) -> TextUsageRecord:
        if self.cached_input_tokens > self.input_tokens:
            raise ValueError("cached_input_tokens must not exceed input_tokens")
        expected_total = self.input_tokens + self.output_tokens
        if self.total_tokens != expected_total:
            raise ValueError("total_tokens must equal input_tokens + output_tokens")
        return self

    def usage_units(self) -> dict[str, int]:
        return {
            "input_tokens": self.input_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
        }


class ImageUsageRecord(UsageRecordBase):
    kind: Literal["image"] = "image"
    image_count: int

    @field_validator("image_count", mode="before")
    @classmethod
    def _validate_image_count(cls, value: Any, info: ValidationInfo) -> int:
        return _strict_non_negative_int(value, info.field_name)

    def usage_units(self) -> dict[str, int]:
        return {"image_count": self.image_count}


class AudioUsageRecord(UsageRecordBase):
    kind: Literal["audio"] = "audio"
    duration_ms: int

    @field_validator("duration_ms", mode="before")
    @classmethod
    def _validate_duration_ms(cls, value: Any, info: ValidationInfo) -> int:
        return _strict_non_negative_int(value, info.field_name)

    def usage_units(self) -> dict[str, int]:
        return {"duration_ms": self.duration_ms}


class VideoUsageRecord(UsageRecordBase):
    kind: Literal["video"] = "video"
    duration_ms: int

    @field_validator("duration_ms", mode="before")
    @classmethod
    def _validate_duration_ms(cls, value: Any, info: ValidationInfo) -> int:
        return _strict_non_negative_int(value, info.field_name)

    def usage_units(self) -> dict[str, int]:
        return {"duration_ms": self.duration_ms}


UsageRecord: TypeAlias = TextUsageRecord | ImageUsageRecord | AudioUsageRecord | VideoUsageRecord


def normalize_text_usage(
    *,
    prompt_tokens: int,
    completion_tokens: int,
    cached_input_tokens: int = 0,
    raw_usage: dict[str, Any] | None = None,
) -> TextUsageRecord:
    return TextUsageRecord(
        input_tokens=prompt_tokens,
        cached_input_tokens=cached_input_tokens,
        output_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
        raw_usage=raw_usage or {},
    )
