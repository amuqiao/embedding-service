from pydantic import BaseModel


class ModelOut(BaseModel):
    id: str
    name: str
    provider: str
    enabled: bool
    capabilities: list[str]
    input_media_types: list[str]
    output_media_types: list[str]
    context_window: int
    supports_json_output: bool = False
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


class JobTypeTemplate(BaseModel):
    job_type: str
    name: str
    description: str
    prompt_blocks: list[PromptBlockTemplate]


class PromptTemplatesResponse(BaseModel):
    version: str
    job_types: list[JobTypeTemplate]
