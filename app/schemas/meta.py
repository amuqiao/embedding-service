from typing import Literal

from pydantic import BaseModel


ModelParameterType = Literal["string", "integer", "number", "boolean", "select"]
ModelParameterValue = str | int | float | bool
ModelType = Literal["text", "image", "audio", "video"]
ModelMetadataValue = str | int | float | bool


class ModelParameterOut(BaseModel):
    name: str
    label: str
    type: ModelParameterType
    required: bool
    default: ModelParameterValue
    options: list[ModelParameterValue] | None = None
    min: int | float | None = None
    max: int | float | None = None


class ModelOut(BaseModel):
    id: str
    name: str
    model_type: ModelType
    provider: str
    enabled: bool
    capabilities: list[str]
    input_media_types: list[str]
    output_media_types: list[str]
    limits: dict[str, ModelMetadataValue]
    features: dict[str, ModelMetadataValue]
    parameters: list[ModelParameterOut]
    notes: str = ""


class ModelsResponse(BaseModel):
    default_model_id: str
    models: list[ModelOut]
    billing_enabled: bool | None = None
    cost_estimate_available: bool | None = None


class LanguageOut(BaseModel):
    language: str
    display_name: str
    native_name: str


class LanguagesResponse(BaseModel):
    languages: list[LanguageOut]


class PromptBlockTemplate(BaseModel):
    key: str
    role: str
    label: str
    default_content: str


class PromptTemplateResponseData(BaseModel):
    version: str
    job_type: str
    name: str
    description: str
    prompt_blocks: list[PromptBlockTemplate]
