from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.core.language_catalog import supported_language_codes
from app.schemas.common import StrictBaseModel
from app.schemas.jobs import HASH_RE, RuntimeFieldsBase

TAGGED_TEXT_TRANSLATION_MAX_ITEMS = 100
TAGGED_TEXT_TRANSLATION_MAX_ITEM_ID_LENGTH = 255
TAGGED_TEXT_TRANSLATION_MAX_TEXT_LENGTH = 10_000
TAGGED_TEXT_TRANSLATION_MAX_TARGET_CHARS_HINT = 10_000


class TaggedTextTranslationItemParams(StrictBaseModel):
    id: str = Field(min_length=1, max_length=TAGGED_TEXT_TRANSLATION_MAX_ITEM_ID_LENGTH)
    text: str = Field(min_length=1, max_length=TAGGED_TEXT_TRANSLATION_MAX_TEXT_LENGTH)
    max_target_chars_hint: int | None = Field(
        default=None,
        ge=1,
        le=TAGGED_TEXT_TRANSLATION_MAX_TARGET_CHARS_HINT,
    )


class TaggedTextTranslationParams(StrictBaseModel):
    source_language: str | None = Field(default=None, min_length=1, max_length=16)
    target_language: str = Field(min_length=1, max_length=16)
    items: list[TaggedTextTranslationItemParams] = Field(
        min_length=1,
        max_length=TAGGED_TEXT_TRANSLATION_MAX_ITEMS,
    )

    @field_validator("source_language", "target_language")
    @classmethod
    def validate_language(cls, value: str | None) -> str | None:
        if value is not None and value not in supported_language_codes():
            raise ValueError("language is not supported")
        return value

    @model_validator(mode="after")
    def validate_unique_item_ids(self) -> "TaggedTextTranslationParams":
        item_ids = [item.id for item in self.items]
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("items[].id must be unique")
        return self


class TaggedTextTranslationRuntimeFields(RuntimeFieldsBase):
    operation: Literal["tagged_text_translation"] = "tagged_text_translation"
    model_id: str = Field(min_length=1, max_length=128)
    model_route_config_hash: str = Field(min_length=71, max_length=71, pattern=HASH_RE.pattern)


class TaggedTextTranslationCharCount(StrictBaseModel):
    source: int = Field(ge=0)
    target: int = Field(ge=0)
    target_limit_hint: int | None = Field(default=None, ge=1)
    within_hint: bool | None = None

    @model_validator(mode="after")
    def validate_hint_consistency(self) -> "TaggedTextTranslationCharCount":
        if self.target_limit_hint is None:
            if self.within_hint is not None:
                raise ValueError("within_hint must be null when target_limit_hint is null")
            return self
        if self.within_hint is None:
            raise ValueError("within_hint is required when target_limit_hint is present")
        return self


class TaggedTextTranslationResultItem(StrictBaseModel):
    id: str = Field(min_length=1, max_length=TAGGED_TEXT_TRANSLATION_MAX_ITEM_ID_LENGTH)
    source_text: str = Field(min_length=1, max_length=TAGGED_TEXT_TRANSLATION_MAX_TEXT_LENGTH)
    translated_text: str = Field(min_length=1, max_length=TAGGED_TEXT_TRANSLATION_MAX_TEXT_LENGTH)
    char_count: TaggedTextTranslationCharCount


class TaggedTextTranslationResult(StrictBaseModel):
    source_language: str | None = Field(default=None, min_length=1, max_length=16)
    target_language: str = Field(min_length=1, max_length=16)
    items: list[TaggedTextTranslationResultItem] = Field(min_length=1, max_length=TAGGED_TEXT_TRANSLATION_MAX_ITEMS)

    @field_validator("source_language", "target_language")
    @classmethod
    def validate_language(cls, value: str | None) -> str | None:
        if value is not None and value not in supported_language_codes():
            raise ValueError("language is not supported")
        return value


SCHEMAS = (
    TaggedTextTranslationItemParams,
    TaggedTextTranslationParams,
    TaggedTextTranslationRuntimeFields,
    TaggedTextTranslationCharCount,
    TaggedTextTranslationResultItem,
    TaggedTextTranslationResult,
)
