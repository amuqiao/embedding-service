from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from app.schemas.common import StrictBaseModel
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


class TagSchemaTranslationParams(StrictBaseModel):
    source_language: str
    target_languages: list[str] = Field(min_length=1)
    source_schema: TagSchemaSnapshot
    source_mutual_exclusion_rules: list[MutualExclusionRule]

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

    @model_validator(mode="after")
    def validate_schema_references(self):
        label_ids: set[str] = set()
        for category in self.source_schema.categories:
            for label in category.labels:
                if label.label_id in label_ids:
                    raise ValueError(f"duplicate label_id: {label.label_id}")
                label_ids.add(label.label_id)
        for rule in self.source_mutual_exclusion_rules:
            if rule.label_id not in label_ids:
                raise ValueError(f"mutual exclusion rule references unknown label_id: {rule.label_id}")
            for mutex_label_id in rule.mutex_label_ids:
                if mutex_label_id not in label_ids:
                    raise ValueError(f"mutual exclusion rule references unknown mutex label_id: {mutex_label_id}")
        return self
