from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from app.schemas.common import StrictBaseModel
from app.schemas.jobs import HASH_RE
from app.workflows.short_drama_tagging.languages import validate_business_language
from app.workflows.short_drama_tagging.languages import SUPPORTED_BUSINESS_LANGUAGES


class ShortDramaWorkContext(StrictBaseModel):
    title: str = Field(min_length=1)
    synopsis: str = ""
    subtitle_language: str
    series_structure: Literal["continuous_series", "unit_series"]
    content_type: str | None = None
    episode_count: int | None = Field(default=None, ge=0)
    audio_language: str | None = None

    @field_validator("subtitle_language")
    @classmethod
    def validate_subtitle_language(cls, value: str) -> str:
        return validate_business_language(value)

    @field_validator("audio_language")
    @classmethod
    def validate_audio_language(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return validate_business_language(value)


class ShortDramaAsset(StrictBaseModel):
    asset_type: str = Field(min_length=1)
    episode_no: int | None = Field(default=None, ge=0)
    format: str = Field(min_length=1)
    uri: str | None = None
    text: str | None = None
    content_hash: str | None = None
    language: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("language")
    @classmethod
    def validate_language(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return validate_business_language(value)

    @model_validator(mode="after")
    def validate_asset(self):
        if self.asset_type == "subtitle_srt":
            if self.format != "srt":
                raise ValueError("subtitle_srt asset format must be srt")
            if not (self.text and self.text.strip()) and not (self.uri and self.uri.strip()):
                raise ValueError("subtitle_srt asset must include text or uri")
        return self


class ShortDramaTaggingParams(StrictBaseModel):
    t_book_id: str = Field(min_length=1)
    work_context: ShortDramaWorkContext
    assets: list[ShortDramaAsset] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_materials(self):
        if not any(asset.asset_type == "subtitle_srt" for asset in self.assets):
            raise ValueError("assets must include at least one subtitle_srt")
        return self


class TagSchemaLabel(StrictBaseModel):
    label_id: str = Field(min_length=1)
    label_key: str | None = None
    name: str = Field(min_length=1)
    definition: str = Field(min_length=1)


class TagSchemaCategory(StrictBaseModel):
    category_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    required: bool
    min_items: int = Field(ge=0)
    max_items: int | None = Field(default=None, ge=0)
    labels: list[TagSchemaLabel] = Field(min_length=1)


class TagSchemaSnapshot(StrictBaseModel):
    categories: list[TagSchemaCategory] = Field(min_length=1)


class MutualExclusionRule(StrictBaseModel):
    label_id: str = Field(min_length=1)
    mutex_label_ids: list[str]


class TagSchemaTranslationLabel(StrictBaseModel):
    label_id: str = Field(min_length=1)
    source_language: str
    target_languages: list[str] = Field(min_length=1)
    display_name: str = Field(min_length=1)
    definition: str = Field(min_length=1)

    @field_validator("source_language")
    @classmethod
    def validate_source_language(cls, value: str) -> str:
        return validate_business_language(value)

    @field_validator("target_languages")
    @classmethod
    def validate_target_languages(cls, value: list[str]) -> list[str]:
        for language in value:
            validate_business_language(language)
        if len(value) != len(set(value)):
            raise ValueError("target_languages must not contain duplicates")
        expected = sorted(value, key=SUPPORTED_BUSINESS_LANGUAGES.index)
        if value != expected:
            raise ValueError("target_languages must follow business language order")
        return value


class TagSchemaTranslationParams(StrictBaseModel):
    labels: list[TagSchemaTranslationLabel] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_labels(self):
        label_ids: set[str] = set()
        for label in self.labels:
            if label.label_id in label_ids:
                raise ValueError(f"duplicate label_id: {label.label_id}")
            label_ids.add(label.label_id)
        return self


class TagSchemaTranslatedText(StrictBaseModel):
    name: str = Field(min_length=1)
    definition: str = Field(min_length=1)


class TagSchemaTranslationArtifact(StrictBaseModel):
    label_id: str = Field(min_length=1)
    langs: dict[str, TagSchemaTranslatedText] = Field(min_length=1)

    @field_validator("langs")
    @classmethod
    def validate_lang_keys(cls, value: dict[str, TagSchemaTranslatedText]) -> dict[str, TagSchemaTranslatedText]:
        for language in value:
            validate_business_language(language)
        return value


class TagSchemaTranslationSignals(StrictBaseModel):
    source_schema_hash: str
    translated_schemas_hash: str

    @field_validator("source_schema_hash", "translated_schemas_hash")
    @classmethod
    def validate_hash(cls, value: str) -> str:
        if not HASH_RE.fullmatch(value):
            raise ValueError("hash must match sha256:<64 lowercase hex>")
        return value


class TagSchemaTranslationResult(StrictBaseModel):
    artifacts: list[TagSchemaTranslationArtifact] = Field(min_length=1)
    signals: TagSchemaTranslationSignals
