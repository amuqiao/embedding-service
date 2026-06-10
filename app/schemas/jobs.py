import re
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.schemas.common import StrictBaseModel

HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class OSSReference(StrictBaseModel):
    oss_key: str = Field(min_length=1)
    oss_url: str = Field(min_length=1)
    content_hash: str | None = None
    content_type: str = Field(min_length=1)

    @field_validator("content_hash")
    @classmethod
    def validate_hash(cls, value: str | None) -> str | None:
        if value is not None and not HASH_RE.fullmatch(value):
            raise ValueError("content_hash must match sha256:<64 lowercase hex>")
        return value

    @field_validator("content_type")
    @classmethod
    def validate_content_type(cls, value: str) -> str:
        normalized = value.strip().lower()
        parts = [part.strip() for part in normalized.split(";")]
        if parts[0] != "text/plain":
            raise ValueError("content_type must be text/plain; charset=utf-8")
        params = {part for part in parts[1:] if part}
        if "charset=utf-8" not in params:
            raise ValueError("content_type must be text/plain; charset=utf-8")
        return "text/plain; charset=utf-8"


class JobSource(StrictBaseModel):
    oss: OSSReference


class CallbackConfig(StrictBaseModel):
    url: str = Field(min_length=1)
    events: list[Literal["job.succeeded", "job.failed"]] | None = None

    @model_validator(mode="after")
    def default_events(self):
        if not self.events:
            self.events = ["job.succeeded", "job.failed"]
        return self


class PromptBlock(StrictBaseModel):
    key: str = Field(min_length=1)
    role: Literal["system", "user"]
    content: str


class PromptConfig(StrictBaseModel):
    blocks: list[PromptBlock] = Field(min_length=1)


class CreateJobRequest(StrictBaseModel):
    client_request_id: str | None = Field(default=None, max_length=255)
    job_type: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    source: JobSource
    callback: CallbackConfig
    prompt: PromptConfig


class CreateJobResponse(StrictBaseModel):
    job_id: UUID
    status: str
    status_url: str
    created_at: datetime


class Artifact(StrictBaseModel):
    key: str
    type: str
    label: str
    apply_mode: Literal["replace", "append"] | None = None
    storage: Literal["oss_object"] | None = None
    oss_bucket: str | None = None
    oss_key: str | None = None
    oss_region: str | None = None
    content_hash: str | None = None
    content_size_bytes: int | None = None
    content: Any | None = None


class JobResult(StrictBaseModel):
    artifacts: list[Artifact]
    signals: dict[str, Any] = {}


class JobError(StrictBaseModel):
    code: str
    message: str
    details: dict[str, Any] = {}


class JobStatusResponse(StrictBaseModel):
    job_id: UUID
    job_type: str
    status: str
    progress_percent: int
    progress_text: str | None = None
    result: JobResult | None = None
    error: JobError | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
