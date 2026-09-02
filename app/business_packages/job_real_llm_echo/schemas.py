from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator

from app.schemas.common import StrictBaseModel
from app.schemas.jobs import Artifact, HASH_RE, RuntimeFieldsBase

REAL_LLM_ECHO_INLINE_MAX_BYTES = 4096


class JobRealLlmEchoParams(StrictBaseModel):
    model_id: str = Field(min_length=1, max_length=128)
    instruction: str = Field(default="用一句话确认真实 LLM 计费链路可用。", min_length=1, max_length=1000)
    source: dict[str, Any]

    @field_validator("source")
    @classmethod
    def validate_inline_source(cls, value: dict[str, Any]) -> dict[str, Any]:
        inline = value.get("inline")
        if not isinstance(inline, dict):
            raise ValueError("source.inline is required")
        text = inline.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("source.inline.text is required")
        if len(text.encode("utf-8")) > REAL_LLM_ECHO_INLINE_MAX_BYTES:
            raise ValueError(f"source.inline.text must be at most {REAL_LLM_ECHO_INLINE_MAX_BYTES} bytes")
        return value


class JobRealLlmEchoRuntimeFields(RuntimeFieldsBase):
    model_id: str
    model_route_config_hash: str = Field(min_length=71, max_length=71, pattern=HASH_RE.pattern)
    prompt_payload: dict[str, Any]


class JobRealLlmEchoResult(StrictBaseModel):
    artifacts: list[Artifact | dict[str, Any]] = Field(default_factory=list)
    signals: dict[str, Any] = Field(default_factory=dict)


SCHEMAS = (JobRealLlmEchoParams, JobRealLlmEchoRuntimeFields, JobRealLlmEchoResult)
