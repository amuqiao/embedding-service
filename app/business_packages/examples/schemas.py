from __future__ import annotations

import math
from typing import Any, Literal, TypeAlias

from pydantic import Field, StrictFloat, StrictInt, field_validator

from app.schemas.common import StrictBaseModel

NumberValue: TypeAlias = StrictInt | StrictFloat


class ExamplePairParams(StrictBaseModel):
    a: NumberValue
    b: NumberValue
    sleep_seconds: float = Field(default=0, ge=0, le=600)
    fail: bool = False
    fail_after_seconds: float = Field(default=0, ge=0, le=600)

    @field_validator("a", "b")
    @classmethod
    def validate_number(cls, value: NumberValue) -> NumberValue:
        if isinstance(value, bool):
            raise ValueError("value must be a number")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("value must be finite")
        return value


class ExamplePairRuntimeFields(StrictBaseModel):
    operation: Literal["pair"]


class ExamplePairResult(StrictBaseModel):
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


class ExampleSleepParams(StrictBaseModel):
    message: str = Field(min_length=1, max_length=512)
    repeat: int = Field(default=1, ge=1, le=5)
    sleep_seconds: float = Field(default=0, ge=0, le=600)
    fail: bool = False
    fail_after_seconds: float = Field(default=0, ge=0, le=600)
    result_size_bytes: int = Field(default=0, ge=0, le=65_536)


class ExampleSleepRuntimeFields(StrictBaseModel):
    operation: Literal["sleep"]


class ExampleSleepResult(StrictBaseModel):
    message: str
    repeated: list[str]
    count: int = Field(ge=1, le=5)
    payload: str = Field(default="", max_length=65_536)


class ExampleCollectParams(StrictBaseModel):
    items: list[str] = Field(min_length=1, max_length=10)
    sleep_seconds: float = Field(default=0, ge=0, le=600)
    fail: bool = False
    fail_after_seconds: float = Field(default=0, ge=0, le=600)


class ExampleCollectRuntimeFields(StrictBaseModel):
    operation: Literal["collect"]


class ExampleCollectResult(StrictBaseModel):
    items: list[str]
    count: int = Field(ge=1, le=10)


class ExampleWorkflowParams(StrictBaseModel):
    mode: Literal["single", "chain", "group", "chord", "map", "starmap", "chunks"]
    label: str = Field(default="workflow-smoke", min_length=1, max_length=64)
    sleep_seconds: float = Field(default=0, ge=0, le=600)
    fail_node_key: str | None = Field(default=None, min_length=1, max_length=64)
    fail_after_seconds: float = Field(default=0, ge=0, le=600)
    result_size_bytes: int = Field(default=0, ge=0, le=65_536)


class ExampleWorkflowRuntimeFields(StrictBaseModel):
    operation: Literal["workflow_root"]


class ExampleWorkflowResult(StrictBaseModel):
    schema_version: int
    job_type: str
    workflow: dict[str, Any]


SCHEMAS = (
    ExamplePairParams,
    ExamplePairRuntimeFields,
    ExamplePairResult,
    ExampleCollectParams,
    ExampleCollectRuntimeFields,
    ExampleCollectResult,
    ExampleSleepParams,
    ExampleSleepRuntimeFields,
    ExampleSleepResult,
    ExampleWorkflowParams,
    ExampleWorkflowRuntimeFields,
    ExampleWorkflowResult,
)
