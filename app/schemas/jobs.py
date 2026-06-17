import re
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.schemas.common import StrictBaseModel

HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
JobStatus = Literal["queued", "running", "succeeded", "failed"]


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


class JobSourceInline(StrictBaseModel):
    text: str = Field(min_length=1)


class JobSource(StrictBaseModel):
    oss: OSSReference | None = None
    inline: JobSourceInline | None = None

    @model_validator(mode="after")
    def validate_source(self):
        if self.oss is None and self.inline is None:
            raise ValueError("JobSource must have either 'oss' or 'inline'")
        if self.oss is not None and self.inline is not None:
            raise ValueError("JobSource cannot have both 'oss' and 'inline'")
        return self


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
    role: Literal["user"]
    content: str = ""


class PromptConfig(StrictBaseModel):
    blocks: list[PromptBlock] = Field(min_length=1)


class JobOptions(StrictBaseModel):
    priority: Literal["low", "normal", "high"] = "normal"
    timeout_seconds: int | None = Field(default=None, gt=0)


class CreateJobRequest(StrictBaseModel):
    client_request_id: str | None = Field(default=None, max_length=255)
    job_type: str = Field(min_length=1)
    job_params: dict[str, Any] = Field(default_factory=dict)
    callback: CallbackConfig | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    options: JobOptions | None = None


class NovelLocalizationJobParams(StrictBaseModel):
    model_id: str = Field(min_length=1)
    source: JobSource
    prompt: PromptConfig
    extra: dict[str, Any] | None = None


class CreateJobResponse(StrictBaseModel):
    job_id: UUID
    client_request_id: str | None = None
    job_type: str
    status: JobStatus
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
    artifacts: list[Artifact | dict[str, Any]]
    signals: dict[str, Any] = {}

    @field_validator("artifacts", mode="before")
    @classmethod
    def validate_artifacts(cls, value):
        if not isinstance(value, list):
            raise ValueError("artifacts must be a list")
        artifacts: list[Artifact | dict[str, Any]] = []
        for item in value:
            if isinstance(item, Artifact):
                artifacts.append(item)
                continue
            if not isinstance(item, dict):
                raise ValueError("artifact must be an object")
            if "key" in item:
                artifacts.append(Artifact.model_validate(item))
            else:
                artifacts.append(item)
        return artifacts


class JobError(StrictBaseModel):
    code: str
    message: str
    details: dict[str, Any] = {}


class JobProgress(StrictBaseModel):
    percent: int = Field(ge=0, le=100)
    message: str | None = None
    stage: str | None = None


class CallbackDeliveryView(StrictBaseModel):
    status: str
    attempts: int
    next_retry_at: datetime | None = None
    last_error: dict[str, Any] | None = None


class JobView(StrictBaseModel):
    job_id: UUID
    client_request_id: str | None = None
    job_type: str
    status: JobStatus
    progress: JobProgress
    result: dict[str, Any] | None = None
    error: JobError | None = None
    callback: CallbackDeliveryView
    metadata: dict[str, Any] = {}
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None

    @model_validator(mode="after")
    def validate_status_payload(self):
        if self.status in {"queued", "running"}:
            if self.result is not None:
                raise ValueError("result must be null while job is not terminal")
            if self.error is not None:
                raise ValueError("error must be null while job is not terminal")
        elif self.status == "succeeded":
            if self.error is not None:
                raise ValueError("error must be null when job succeeded")
        elif self.status == "failed":
            if self.result is not None:
                raise ValueError("result must be null when job failed")
            if self.error is None:
                raise ValueError("error is required when job failed")
        return self


class JobStatusResponse(JobView):
    pass


class CallbackEnvelope(StrictBaseModel):
    event: Literal["job.succeeded", "job.failed"]
    event_id: UUID
    attempt: int
    sent_at: datetime
    job: JobView

    @model_validator(mode="after")
    def validate_event_matches_job(self):
        if self.job.status not in {"succeeded", "failed"}:
            raise ValueError("callback job must be terminal")
        expected = "job.succeeded" if self.job.status == "succeeded" else "job.failed"
        if self.event != expected:
            raise ValueError("callback event must match job status")
        return self
