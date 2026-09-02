from __future__ import annotations

import math
from typing import Literal, TypeAlias

from pydantic import Field, StrictFloat, StrictInt, field_validator

from app.schemas.common import StrictBaseModel

NumberValue: TypeAlias = StrictInt | StrictFloat


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


SCHEMAS = (ArithmeticParams, ArithmeticRuntimeFields, ArithmeticResult)
