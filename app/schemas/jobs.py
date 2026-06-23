from __future__ import annotations

import math
import re
from datetime import datetime
from typing import Any, Literal, TypeAlias
from uuid import UUID

from pydantic import ConfigDict, Field, StrictFloat, StrictInt, field_validator, model_validator

from app.schemas.common import StrictBaseModel
from app.schemas.errors import CallbackErrorDetail, JobErrorDetail

HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
REAL_LLM_ECHO_INLINE_MAX_BYTES = 4096

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
NumberValue: TypeAlias = StrictInt | StrictFloat


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
    stage: ProgressStage
    percent: int = Field(ge=0, le=100)
    message: str | None = None


class CallbackState(StrictBaseModel):
    status: CallbackDeliveryStatus
    attempt: int = Field(ge=0)
    last_error: CallbackErrorDetail | None = None
    next_retry_at: datetime | None = None


class JobEnvelope(StrictBaseModel):
    job_id: UUID
    client_request_id: str | None = None
    job_type: str
    job_status: JobStatus
    job_progress: JobProgress
    job_result: dict[str, Any] | None = None
    job_error: JobErrorDetail | None = None
    callback: CallbackState
    status_url: str
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None = None

    @model_validator(mode="after")
    def validate_terminal_payload(self) -> "JobEnvelope":
        if self.job_status in {"queued", "running"}:
            if self.job_result is not None:
                raise ValueError("job_result must be null while job is not terminal")
            if self.job_error is not None:
                raise ValueError("job_error must be null while job is not terminal")
        elif self.job_status == "succeeded":
            if self.job_error is not None:
                raise ValueError("job_error must be null when job succeeded")
        elif self.job_status == "failed":
            if self.job_result is not None:
                raise ValueError("job_result must be null when job failed")
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


class JobTestAddParams(StrictBaseModel):
    a: NumberValue
    b: NumberValue

    @field_validator("a", "b")
    @classmethod
    def validate_number(cls, value: NumberValue) -> NumberValue:
        if isinstance(value, bool):
            raise ValueError("value must be a number")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("value must be finite")
        return value


class JobTestAddRuntimeFields(StrictBaseModel):
    operation: Literal["add"]


class JobTestAddResult(StrictBaseModel):
    a: NumberValue
    b: NumberValue
    result: NumberValue

    @field_validator("a", "b", "result")
    @classmethod
    def validate_number(cls, value: NumberValue) -> NumberValue:
        if isinstance(value, bool):
            raise ValueError("value must be a number")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("value must be finite")
        return value


class JobTestEchoParams(StrictBaseModel):
    message: str = Field(min_length=1, max_length=512)
    repeat: int = Field(default=1, ge=1, le=5)


class JobTestEchoRuntimeFields(StrictBaseModel):
    operation: Literal["echo"]


class JobTestEchoResult(StrictBaseModel):
    message: str
    repeated: list[str]
    count: int = Field(ge=1, le=5)


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


class JobRealLlmEchoRuntimeFields(StrictBaseModel):
    model_id: str
    prompt_payload: dict[str, Any]


class JobRealLlmEchoResult(StrictBaseModel):
    artifacts: list[Artifact | dict[str, Any]] = Field(default_factory=list)
    signals: dict[str, Any] = Field(default_factory=dict)


class JobRealLlmDoubleEchoParams(JobRealLlmEchoParams):
    first_instruction: str = Field(default="第一次调用：用一句话确认真实 LLM 计费链路可用。", min_length=1, max_length=1000)
    second_instruction: str = Field(default="第二次调用：用另一句话确认同一 Job 的多次 LLM 计费可汇总。", min_length=1, max_length=1000)


class JobRealLlmDoubleEchoRuntimeFields(StrictBaseModel):
    model_id: str
    first_prompt_payload: dict[str, Any]
    second_prompt_payload: dict[str, Any]


class JobRealLlmDoubleEchoResult(StrictBaseModel):
    artifacts: list[Artifact | dict[str, Any]] = Field(default_factory=list)
    signals: dict[str, Any]


class ArithmeticParams(StrictBaseModel):
    a: NumberValue
    b: NumberValue

    @field_validator("a", "b")
    @classmethod
    def validate_nonzero_number(cls, value: NumberValue) -> NumberValue:
        if isinstance(value, bool):
            raise ValueError("value must be a number")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("value must be finite")
        if value == 0:
            raise ValueError("value must be non-zero")
        return value


class ArithmeticRuntimeFields(StrictBaseModel):
    operation: Literal["add_subtract_multiply_divide"]


class ArithmeticResult(StrictBaseModel):
    a: NumberValue
    b: NumberValue
    addition: NumberValue
    subtraction: NumberValue
    multiplication: NumberValue
    division: StrictFloat

    @field_validator("a", "b", "addition", "subtraction", "multiplication")
    @classmethod
    def validate_number(cls, value: NumberValue) -> NumberValue:
        if isinstance(value, bool):
            raise ValueError("value must be a number")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("value must be finite")
        return value

    @field_validator("division")
    @classmethod
    def validate_division(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("division must be finite")
        return value


CreateJobResponse = JobEnvelope
JobStatusResponse = JobEnvelope


def __getattr__(name: str) -> Any:
    if name in {"CallbackEnvelope", "CallbackResponseEnvelope"}:
        from app.schemas import callbacks as callback_schemas

        return getattr(callback_schemas, name)
    raise AttributeError(name)
