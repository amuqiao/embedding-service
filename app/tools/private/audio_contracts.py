from __future__ import annotations

import re
from typing import Literal

from pydantic import Field, field_validator

from app.schemas.common import StrictBaseModel

# Audio input pipeline contracts shared by audio tools and audio business
# packages. Keep provider calls and business executor logic out of this module.
BARE_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
AudioInputContentType = Literal["audio/wav", "audio/x-wav", "audio/mpeg", "audio/mp3"]


class CanonicalObjectRefSnapshot(StrictBaseModel):
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


class MediaFetchSpec(StrictBaseModel):
    read_mode: Literal["object_storage"] = "object_storage"
    endpoint_key: Literal["canonical_object_ref"] = "canonical_object_ref"
    max_bytes: int = Field(gt=0)
    redirect_policy: Literal["forbid"] = "forbid"


class AudioDecodeNormalizeSpec(StrictBaseModel):
    source_content_type: AudioInputContentType
    target_sample_rate: Literal[44100] = 44100
    target_channels: Literal[2] = 2


class AudioDecodeNormalizeRequest(StrictBaseModel):
    data: bytes
    decode: AudioDecodeNormalizeSpec
    max_duration_seconds: float | None = Field(default=None, gt=0, le=3600)


class AudioInputPlanSnapshot(StrictBaseModel):
    tool_refs: tuple[Literal["object_storage_read:1"], Literal["audio_decode_normalize:1"]] = (
        "object_storage_read:1",
        "audio_decode_normalize:1",
    )
    source: CanonicalObjectRefSnapshot
    fetch: MediaFetchSpec
    decode: AudioDecodeNormalizeSpec
    max_duration_seconds: float | None = Field(default=None, gt=0, le=3600)


class PreparedAudioInputMetadata(StrictBaseModel):
    sample_rate: Literal[44100] = 44100
    channels: Literal[2] = 2
    duration_seconds: float = Field(gt=0)


SCHEMAS = (
    CanonicalObjectRefSnapshot,
    MediaFetchSpec,
    AudioDecodeNormalizeSpec,
    AudioDecodeNormalizeRequest,
    AudioInputPlanSnapshot,
    PreparedAudioInputMetadata,
)
