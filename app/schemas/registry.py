from __future__ import annotations

from pydantic import BaseModel

from app.business_packages.register import business_package_schemas
from app.schemas.billing import BillingEnvelope, JobBillingResponseData, ScopeBillingResponseData
from app.schemas.jobs import (
    CreateJobRequest,
    JobResponseData,
    RuntimeSystemFields,
)
from app.schemas.meta import LanguagesResponse, ModelsResponse, PromptTemplateResponseData
from app.tools.private.audio_contracts import SCHEMAS as AUDIO_TOOL_SCHEMAS

COMMON_SCHEMAS: tuple[type[BaseModel], ...] = (
    CreateJobRequest,
    JobResponseData,
    BillingEnvelope,
    JobBillingResponseData,
    ScopeBillingResponseData,
    RuntimeSystemFields,
    ModelsResponse,
    LanguagesResponse,
    PromptTemplateResponseData,
)


def _schema_registry() -> dict[str, type[BaseModel]]:
    schemas: dict[str, type[BaseModel]] = {}
    for schema in COMMON_SCHEMAS + AUDIO_TOOL_SCHEMAS + business_package_schemas():
        existing = schemas.get(schema.__name__)
        if existing is not None and existing is not schema:
            raise ValueError(f"duplicate schema name: {schema.__name__}")
        schemas[schema.__name__] = schema
    return schemas


_SCHEMAS: dict[str, type[BaseModel]] = _schema_registry()


def get_schema(name: str) -> type[BaseModel]:
    return _SCHEMAS[name]


def all_schema_names() -> set[str]:
    return set(_SCHEMAS)
