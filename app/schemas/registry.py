from __future__ import annotations

from pydantic import BaseModel

from app.schemas.billing import BillingEnvelope, JobBillingResponseData, ScopeBillingResponseData
from app.schemas.jobs import (
    ArithmeticParams,
    ArithmeticResult,
    ArithmeticRuntimeFields,
    CreateJobRequest,
    JobResponseData,
    JobTestAddParams,
    JobTestAddResult,
    JobTestAddRuntimeFields,
    JobTestEchoParams,
    JobTestEchoResult,
    JobTestEchoRuntimeFields,
    JobRealLlmEchoParams,
    JobRealLlmEchoResult,
    JobRealLlmEchoRuntimeFields,
    JobRealLlmDoubleEchoParams,
    JobRealLlmDoubleEchoResult,
    JobRealLlmDoubleEchoRuntimeFields,
)
from app.schemas.meta import ModelsResponse, PromptTemplatesResponse

_SCHEMAS: dict[str, type[BaseModel]] = {
    schema.__name__: schema
    for schema in (
        CreateJobRequest,
        JobResponseData,
        BillingEnvelope,
        JobBillingResponseData,
        ScopeBillingResponseData,
        ArithmeticParams,
        ArithmeticRuntimeFields,
        ArithmeticResult,
        JobTestAddParams,
        JobTestAddRuntimeFields,
        JobTestAddResult,
        JobTestEchoParams,
        JobTestEchoRuntimeFields,
        JobTestEchoResult,
        JobRealLlmEchoParams,
        JobRealLlmEchoRuntimeFields,
        JobRealLlmEchoResult,
        JobRealLlmDoubleEchoParams,
        JobRealLlmDoubleEchoRuntimeFields,
        JobRealLlmDoubleEchoResult,
        ModelsResponse,
        PromptTemplatesResponse,
    )
}


def get_schema(name: str) -> type[BaseModel]:
    return _SCHEMAS[name]


def all_schema_names() -> set[str]:
    return set(_SCHEMAS)
