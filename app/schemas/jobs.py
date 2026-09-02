from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import ConfigDict, Field, field_validator, model_validator

from app.schemas.common import StrictBaseModel
from app.schemas.errors import CallbackErrorDetail, JobErrorDetail

HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

JobStatus = Literal["queued", "running", "succeeded", "failed"]
ProgressStage = Literal[
    "accepted",
    "fetching_input",
    "planning",
    "calling_model",
    "merging",
    "writing_result",
    "completed",
    "failed",
]
CallbackDeliveryStatus = Literal["not_configured", "pending", "delivering", "delivered", "retrying", "failed"]


class CallbackConfig(StrictBaseModel):
    url: str = Field(min_length=1)
    events: list[Literal["job.succeeded", "job.failed"]] | None = None

    @model_validator(mode="after")
    def default_events(self) -> "CallbackConfig":
        if self.events == []:
            raise ValueError("callback.events must not be empty")
        if self.events is None:
            self.events = ["job.failed", "job.succeeded"]
        else:
            self.events = sorted(set(self.events))
        return self


class JobOptions(StrictBaseModel):
    priority: Literal["low", "normal"] = "normal"
    idempotency_mode: Literal["reject_duplicate", "return_existing"] = "reject_duplicate"


class CreateJobRequest(StrictBaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "client_request_id": "swagger-arithmetic-demo",
                    "job_type": "arithmetic",
                    "job_params": {
                        "a": 9,
                        "b": 3,
                    },
                    "metadata": {
                        "source": "swagger-ui",
                    },
                    "options": {
                        "priority": "normal",
                        "idempotency_mode": "return_existing",
                    },
                }
            ]
        }
    )

    client_request_id: str = Field(min_length=1, max_length=255)
    job_type: str = Field(min_length=1)
    job_params: dict[str, Any] = Field(default_factory=dict)
    callback: CallbackConfig | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    options: JobOptions | None = None


class JobProgress(StrictBaseModel):
    percent: int = Field(ge=0, le=100)
    stage: str | None = None
    message: str | None = None


class CallbackState(StrictBaseModel):
    status: CallbackDeliveryStatus
    attempt: int = Field(ge=0)
    last_error: CallbackErrorDetail | None = None
    next_retry_at: datetime | None = None


class JobCost(StrictBaseModel):
    currency: str = Field(min_length=1, max_length=8)
    amount: str = Field(min_length=1)
    final: bool


class JobUsage(StrictBaseModel):
    ai_call_count: int = Field(ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    final: bool


class JobEnvelope(StrictBaseModel):
    job_id: UUID
    client_request_id: str | None = None
    job_type: str
    job_status: JobStatus
    job_progress: JobProgress
    job_result: dict[str, Any] | None = None
    job_error: JobErrorDetail | None = None
    cost: JobCost | None = None
    usage: JobUsage | None = None
    callback: CallbackState
    status_url: str
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None = None

    @model_validator(mode="after")
    def validate_terminal_payload(self) -> "JobEnvelope":
        if self.job_status == "queued":
            if self.job_result is not None:
                raise ValueError("job_result must be null while job is not terminal")
            if self.job_error is not None:
                raise ValueError("job_error must be null while job is not terminal")
            if self.cost is not None:
                raise ValueError("cost must be null while job is not terminal")
            if self.usage is not None:
                raise ValueError("usage must be null while job is not terminal")
        elif self.job_status == "running":
            if self.job_error is not None:
                raise ValueError("job_error must be null while job is not terminal")
            if self.cost is not None:
                raise ValueError("cost must be null while job is not terminal")
            if self.usage is not None:
                raise ValueError("usage must be null while job is not terminal")
        elif self.job_status == "succeeded":
            if self.job_error is not None:
                raise ValueError("job_error must be null when job succeeded")
        elif self.job_status == "failed":
            if self.job_error is None:
                raise ValueError("job_error is required when job failed")
        return self


class JobResponseData(StrictBaseModel):
    job: JobEnvelope


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

    @field_validator("content_hash")
    @classmethod
    def validate_hash(cls, value: str | None) -> str | None:
        if value is not None and not HASH_RE.fullmatch(value):
            raise ValueError("content_hash must match sha256:<64 lowercase hex>")
        return value


class JobResult(StrictBaseModel):
    artifacts: list[Artifact | dict[str, Any]] = Field(default_factory=list)
    signals: dict[str, Any] = Field(default_factory=dict)

    @field_validator("artifacts", mode="before")
    @classmethod
    def validate_artifacts(cls, value: Any) -> list[Artifact | dict[str, Any]]:
        if value is None:
            return []
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


class RuntimeSystemFields(StrictBaseModel):
    trigger_request_id: str | None = Field(default=None, min_length=1, max_length=128)


class RuntimeFieldsBase(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    # Workflow orchestration injects internal request metadata into persisted runtime fields.
    system: RuntimeSystemFields | None = Field(default=None, alias="_system")


CreateJobResponse = JobEnvelope
JobStatusResponse = JobEnvelope
