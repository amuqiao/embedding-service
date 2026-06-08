import re
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.schemas.common import StrictBaseModel

HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class TextInput(StrictBaseModel):
    type: Literal["text"]
    content: str
    content_hash: str | None = None

    @field_validator("content_hash")
    @classmethod
    def validate_hash(cls, value: str | None) -> str | None:
        if value is not None and not HASH_RE.fullmatch(value):
            raise ValueError("content_hash must match sha256:<64 lowercase hex>")
        return value


class OSSObjectInput(StrictBaseModel):
    type: Literal["oss_object"]
    oss_bucket: str = Field(min_length=1)
    oss_key: str = Field(min_length=1)
    oss_region: str = Field(min_length=1)
    content_hash: str | None = None

    @field_validator("content_hash")
    @classmethod
    def validate_hash(cls, value: str | None) -> str | None:
        if value is not None and not HASH_RE.fullmatch(value):
            raise ValueError("content_hash must match sha256:<64 lowercase hex>")
        return value


class OutputConfig(StrictBaseModel):
    type: Literal["oss_prefix"]
    oss_bucket: str = Field(min_length=1)
    oss_prefix: str = Field(min_length=1)
    oss_region: str = Field(min_length=1)


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
    input: TextInput | OSSObjectInput = Field(discriminator="type")
    output: OutputConfig
    callback: CallbackConfig
    prompt: PromptConfig


class CreateJobResponse(StrictBaseModel):
    job_id: UUID
    status: str
    status_url: str
    created_at: datetime


class ArtifactTarget(StrictBaseModel):
    job_type: str
    prompt_block_key: str
    default_mode: str


class Artifact(StrictBaseModel):
    key: str
    type: str
    label: str
    storage: Literal["oss_object"] | None = None
    oss_bucket: str | None = None
    oss_key: str | None = None
    oss_region: str | None = None
    content_hash: str | None = None
    content_size_bytes: int | None = None
    content: Any | None = None
    target: ArtifactTarget | None = None


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
