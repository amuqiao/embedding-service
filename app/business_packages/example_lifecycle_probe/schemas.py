from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from app.schemas.common import StrictBaseModel


class ExampleLifecycleProbeParams(StrictBaseModel):
    probe_id: str = Field(default="default", min_length=1, max_length=128)
    message: str = Field(default="lifecycle probe", min_length=1, max_length=512)
    sleep_seconds: float = Field(default=0, ge=0, le=600)
    fail: bool = False
    fail_after_seconds: float = Field(default=0, ge=0, le=600)
    result_payload: str | None = Field(default=None, min_length=1, max_length=4096)
    result_size_bytes: int = Field(default=0, ge=0, le=65_536)

    @model_validator(mode="after")
    def validate_payload_source(self) -> "ExampleLifecycleProbeParams":
        if self.result_payload is not None and self.result_size_bytes:
            raise ValueError("result_payload and result_size_bytes are mutually exclusive")
        if not self.fail and self.fail_after_seconds:
            raise ValueError("fail_after_seconds requires fail=true")
        if self.sleep_seconds + self.fail_after_seconds > 600:
            raise ValueError("sleep_seconds + fail_after_seconds must be <= 600")
        return self


class ExampleLifecycleProbeRuntimeFields(StrictBaseModel):
    operation: Literal["lifecycle_probe"]
    probe_id: str
    sleep_seconds: float
    fail: bool
    fail_after_seconds: float


class ExampleLifecycleProbeResult(StrictBaseModel):
    probe_id: str
    message: str
    requested_sleep_seconds: float = Field(ge=0, le=600)
    fail: bool
    result_payload: str | None = Field(default=None, max_length=65_536)
    elapsed_ms: int = Field(ge=0)
    worker_observed_at: str = Field(min_length=1)


SCHEMAS = (
    ExampleLifecycleProbeParams,
    ExampleLifecycleProbeRuntimeFields,
    ExampleLifecycleProbeResult,
)
