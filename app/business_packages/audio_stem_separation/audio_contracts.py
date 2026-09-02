from __future__ import annotations

import re
from typing import Literal

from pydantic import Field, field_validator

from app.schemas.common import StrictBaseModel
from app.tools.private.media_audio import AudioDecodeNormalizeSpec, AudioInputContentType

BARE_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


class AudioInputObjectSnapshot(StrictBaseModel):
    provider: Literal["aliyun_oss", "local"]
    bucket: str = Field(min_length=1)
    region: str = Field(min_length=1)
    key: str = Field(min_length=1)
    content_type: AudioInputContentType
    content_hash: str

    @field_validator("content_hash")
    @classmethod
    def validate_content_hash(cls, value: str) -> str:
        if not value.startswith("sha256:"):
            raise ValueError("content_hash must use sha256: prefix")
        raw = value.removeprefix("sha256:")
        if not BARE_HASH_RE.fullmatch(raw):
            raise ValueError("content_hash must be sha256-prefixed lowercase hex")
        return value


class AudioInputFetchSpec(StrictBaseModel):
    max_bytes: int = Field(gt=0)


class AudioInputPlanSnapshot(StrictBaseModel):
    source: AudioInputObjectSnapshot
    fetch: AudioInputFetchSpec
    decode: AudioDecodeNormalizeSpec
    max_duration_seconds: float | None = Field(default=None, gt=0, le=3600)


SCHEMAS = (
    AudioInputObjectSnapshot,
    AudioInputFetchSpec,
    AudioInputPlanSnapshot,
)
