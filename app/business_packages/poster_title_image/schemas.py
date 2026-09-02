from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from app.core.language_catalog import supported_language_codes
from app.schemas.common import StrictBaseModel
from app.schemas.jobs import HASH_RE, RuntimeFieldsBase

BARE_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
POSTER_TITLE_IMAGE_MAX_TITLE_LINES = 2
POSTER_TITLE_IMAGE_MAX_HARD_LINE_BREAKS = POSTER_TITLE_IMAGE_MAX_TITLE_LINES - 1
POSTER_TITLE_IMAGE_DISALLOWED_LINE_BREAKS = frozenset(
    {
        "\r",
        "\v",
        "\f",
        "\x1c",
        "\x1d",
        "\x1e",
        "\x85",
        "\u2028",
        "\u2029",
    }
)
POSTER_TITLE_IMAGE_LINE_CONTROL_OVERRIDE_PATTERN = re.compile(
    r"\\n|\b(?:line[- ]?breaks?|newlines?|new\s+lines?|hard\s+breaks?|lf)\b|"
    r"\b(?:render|show|display|draw|format|set|make|force|keep|preserve|put|place)\b.{0,40}"
    r"\b(?:single|one|two|three|\d+|separate|multiple)\s+lines?\b|"
    r"\b(?:title|text|words?)\b.{0,40}\b(?:single|one|two|three|\d+|separate|multiple)\s+lines?\b|"
    r"\b(?:title|text|words?)\b.{0,40}\b(?:rows?|stacked|multiline)\b|"
    r"\b(?:arrange|put|place|keep|make|set|format|render|display|show)\b.{0,20}"
    r"\b(?:title|text|words?)\b.{0,40}\b(?:rows?|stacked|multiline)\b|"
    r"\b(?:each|every)\s+word\b.{0,40}\b(?:own|separate)\s+rows?\b|"
    r"\bmultiline\b|"
    r"\b(?:break|wrap|split)\b.{0,40}\b(?:title|text|words?)\b|"
    r"\b(?:title|text|words?)\b.{0,40}\b(?:break|wrap|split)\b|"
    r"\b(?:split|merge|preserve|add|remove|reorder|reposition|move)\s+(?:any\s+)?lines?\b|"
    r"换行|分行|断行|单行|多行|合并行|拆行",
    re.IGNORECASE,
)


class OssUrlRef(StrictBaseModel):
    public_url: str = Field(min_length=1)
    internal_url: str = Field(min_length=1)
    content_type: Literal["image/png", "image/jpeg", "image/webp"]
    sha256: str

    @field_validator("sha256")
    @classmethod
    def validate_bare_sha256(cls, value: str) -> str:
        if not BARE_HASH_RE.fullmatch(value):
            raise ValueError("sha256 must be 64 lowercase hex characters")
        return value


class PosterTitleImageReferenceImage(StrictBaseModel):
    public_url: str = Field(min_length=1)
    internal_url: str = Field(min_length=1)
    content_type: Literal["image/png", "image/jpeg", "image/webp"]
    sha256: str

    @field_validator("sha256")
    @classmethod
    def validate_bare_sha256(cls, value: str) -> str:
        if not BARE_HASH_RE.fullmatch(value):
            raise ValueError("sha256 must be 64 lowercase hex characters")
        return value


class PosterTitleImageModelOptions(StrictBaseModel):
    size: Literal["1024x1024", "1536x1024", "1024x1536", "auto"] = "auto"
    quality: Literal["low", "medium", "high", "auto"] = "high"
    draw_count: int = Field(default=1, ge=1, le=4)
    # Public delivery contract: callers request a transparent title layer.
    # The current gpt-image-2 provider call still uses background=auto and
    # local green-screen post-processing to produce that transparent output.
    background: Literal["transparent"] = "transparent"
    output_format: Literal["png"] = "png"

    @model_validator(mode="after")
    def validate_transparent_output(self) -> "PosterTitleImageModelOptions":
        if self.background == "transparent" and self.output_format != "png":
            raise ValueError("background=transparent requires output_format=png")
        return self


class PosterTitleImagePromptOverrides(StrictBaseModel):
    style_probe: str | None = Field(default=None, min_length=1, max_length=4000)
    additional_prompt: str | None = Field(
        default=None,
        min_length=1,
        max_length=4000,
        description=(
            "Additional visual or style preference only. It must not define, add, remove, merge, split, reorder, "
            "or reposition title_text line breaks."
        ),
    )
    layout_rules: str | None = Field(
        default=None,
        min_length=1,
        max_length=4000,
        description=(
            "Visual layout preference only. It must not define, add, remove, merge, split, reorder, or reposition "
            "title_text line breaks."
        ),
    )

    @field_validator("additional_prompt", "layout_rules")
    @classmethod
    def validate_no_line_break_control(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if POSTER_TITLE_IMAGE_LINE_CONTROL_OVERRIDE_PATTERN.search(value):
            raise ValueError(
                "prompt_overrides.additional_prompt and prompt_overrides.layout_rules must not control title_text line breaks"
            )
        return value


class PosterTitleImageItemParams(StrictBaseModel):
    item_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
    language: str = Field(min_length=1, max_length=16)
    title_text: str = Field(
        min_length=1,
        max_length=200,
        description=(
            "Only source of caller-specified hard line breaks. No LF means no caller-specified hard line break; "
            "The service may wrap when needed for fit and balance. "
            "Each LF position determines a hard line break position. "
            f"LF separates caller-specified lines, up to {POSTER_TITLE_IMAGE_MAX_TITLE_LINES} lines."
        ),
    )
    model_id: str | None = Field(default=None, min_length=1, max_length=128)
    model_options: PosterTitleImageModelOptions = Field(default_factory=PosterTitleImageModelOptions)
    reference_image: PosterTitleImageReferenceImage
    prompt_overrides: PosterTitleImagePromptOverrides | None = None

    @field_validator("language")
    @classmethod
    def validate_language_subset(cls, value: str) -> str:
        if value not in supported_language_codes():
            raise ValueError("language is not supported by poster_title_image")
        return value

    @field_validator("title_text")
    @classmethod
    def validate_title_text_line_breaks(cls, value: str) -> str:
        if any(separator in value for separator in POSTER_TITLE_IMAGE_DISALLOWED_LINE_BREAKS):
            raise ValueError("title_text line breaks must use LF \\n only")
        if "<br" in value.lower():
            raise ValueError("title_text does not support HTML line break tags; use LF \\n")
        if value.count("\n") > POSTER_TITLE_IMAGE_MAX_HARD_LINE_BREAKS:
            raise ValueError(f"title_text must use at most {POSTER_TITLE_IMAGE_MAX_TITLE_LINES} lines")
        lines = value.split("\n")
        if any(not line.strip() for line in lines):
            raise ValueError("title_text lines must not be empty")
        return value


class PosterTitleImageParams(StrictBaseModel):
    items: list[PosterTitleImageItemParams] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_items(self) -> "PosterTitleImageParams":
        item_ids = [item.item_id for item in self.items]
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("items[].item_id must be unique")
        model_ids = {item.model_id for item in self.items if item.model_id is not None}
        if len(model_ids) > 1:
            raise ValueError("items[].model_id must be the same within one poster_title_image job")
        return self


class PosterTitleImageRuntimeFields(RuntimeFieldsBase):
    operation: Literal["poster_title_image"] = "poster_title_image"
    style_probe_model_id: str = Field(min_length=1, max_length=128)
    style_probe_route_config_hash: str = Field(min_length=71, max_length=71, pattern=HASH_RE.pattern)
    generation_model_id: str = Field(min_length=1, max_length=128)
    generation_route_config_hash: str = Field(min_length=71, max_length=71, pattern=HASH_RE.pattern)
    image_adapter: str = Field(min_length=1, max_length=128)


class PosterTitleImageStyleProbeParams(StrictBaseModel):
    style_key: str = Field(min_length=1)
    reference_image: PosterTitleImageReferenceImage
    style_prompt: str = Field(min_length=1, max_length=8000)
    style_probe_model_id: str = Field(min_length=1, max_length=128)
    image_adapter: str = Field(min_length=1, max_length=128)


class PosterTitleImageStyleProbeRuntimeFields(RuntimeFieldsBase):
    operation: Literal["poster_title_image_style_probe"] = "poster_title_image_style_probe"
    style_probe_model_id: str = Field(min_length=1, max_length=128)
    style_probe_route_config_hash: str = Field(min_length=71, max_length=71, pattern=HASH_RE.pattern)
    image_adapter: str = Field(min_length=1, max_length=128)


class PosterTitleImageDurationMs(StrictBaseModel):
    ai_model: int = Field(ge=0)
    total: int = Field(ge=0)


class PosterTitleImageStyleProbeResult(StrictBaseModel):
    style_key: str = Field(min_length=1)
    style_desc: str = Field(min_length=1)
    duration_ms: PosterTitleImageDurationMs


class PosterTitleImageGenerateItemParams(StrictBaseModel):
    item: PosterTitleImageItemParams
    probe_node_key: str = Field(min_length=1, max_length=128)
    style_probe_model_id: str = Field(min_length=1, max_length=128)
    image_adapter: str = Field(min_length=1, max_length=128)


class PosterTitleImageGenerateItemRuntimeFields(RuntimeFieldsBase):
    operation: Literal["poster_title_image_generate_item"] = "poster_title_image_generate_item"
    generation_model_id: str = Field(min_length=1, max_length=128)
    generation_route_config_hash: str = Field(min_length=71, max_length=71, pattern=HASH_RE.pattern)
    style_probe_model_id: str = Field(min_length=1, max_length=128)
    style_probe_route_config_hash: str = Field(min_length=71, max_length=71, pattern=HASH_RE.pattern)
    image_adapter: str = Field(min_length=1, max_length=128)


PosterTitleImageItemStatus = Literal["pending", "running", "succeeded", "failed"]


class PosterTitleImageObject(StrictBaseModel):
    public_url: str
    internal_url: str
    content_type: Literal["image/png", "image/jpeg", "image/webp"]
    sha256: str

    @field_validator("sha256")
    @classmethod
    def validate_output_sha256(cls, value: str) -> str:
        if not BARE_HASH_RE.fullmatch(value):
            raise ValueError("sha256 must be 64 lowercase hex characters")
        return value


class PosterTitleImageImage(StrictBaseModel):
    object: PosterTitleImageObject
    width: int = Field(ge=1)
    height: int = Field(ge=1)


class PosterTitleImageError(StrictBaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class PosterTitleImageResultItem(StrictBaseModel):
    item_id: str
    language: str
    status: PosterTitleImageItemStatus
    images: list[PosterTitleImageImage] = Field(default_factory=list)
    error: PosterTitleImageError | None = None


class PosterTitleImageBatchSummary(StrictBaseModel):
    total: int = Field(ge=1)
    succeeded: int = Field(ge=0)
    failed: int = Field(ge=0)
    running: int = Field(ge=0)
    pending: int = Field(ge=0)


class PosterTitleImageGenerateItemResult(StrictBaseModel):
    item: PosterTitleImageResultItem
    duration_ms: PosterTitleImageDurationMs


class PosterTitleImageJoinParams(StrictBaseModel):
    items: list[PosterTitleImageItemParams] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_items_contract(self) -> "PosterTitleImageJoinParams":
        PosterTitleImageParams.model_validate({"items": [item.model_dump() for item in self.items]})
        return self


class PosterTitleImageJoinRuntimeFields(RuntimeFieldsBase):
    operation: Literal["poster_title_image_join"] = "poster_title_image_join"


class PosterTitleImageResult(StrictBaseModel):
    schema_version: Literal["default"] = "default"
    job_type: Literal["poster_title_image"] = "poster_title_image"
    batch_summary: PosterTitleImageBatchSummary
    items: list[PosterTitleImageResultItem] = Field(min_length=1)
    duration_ms: PosterTitleImageDurationMs

    @model_validator(mode="after")
    def validate_summary(self) -> "PosterTitleImageResult":
        counts = {
            "succeeded": sum(1 for item in self.items if item.status == "succeeded"),
            "failed": sum(1 for item in self.items if item.status == "failed"),
            "running": sum(1 for item in self.items if item.status == "running"),
            "pending": sum(1 for item in self.items if item.status == "pending"),
        }
        if self.batch_summary.total != len(self.items):
            raise ValueError("batch_summary.total must equal items count")
        for key, value in counts.items():
            if getattr(self.batch_summary, key) != value:
                raise ValueError(f"batch_summary.{key} must match items")
        for item in self.items:
            if item.status == "succeeded" and (not item.images or item.error is not None):
                raise ValueError("succeeded item requires images and null error")
            if item.status == "failed" and (item.images or item.error is None):
                raise ValueError("failed item requires empty images and non-null error")
            if item.status in {"pending", "running"} and (item.images or item.error is not None):
                raise ValueError("pending/running item requires empty images and null error")
        return self


SCHEMAS = (
    OssUrlRef,
    PosterTitleImageReferenceImage,
    PosterTitleImageModelOptions,
    PosterTitleImagePromptOverrides,
    PosterTitleImageItemParams,
    PosterTitleImageParams,
    PosterTitleImageRuntimeFields,
    PosterTitleImageStyleProbeParams,
    PosterTitleImageStyleProbeRuntimeFields,
    PosterTitleImageStyleProbeResult,
    PosterTitleImageGenerateItemParams,
    PosterTitleImageGenerateItemRuntimeFields,
    PosterTitleImageObject,
    PosterTitleImageImage,
    PosterTitleImageError,
    PosterTitleImageResultItem,
    PosterTitleImageBatchSummary,
    PosterTitleImageDurationMs,
    PosterTitleImageGenerateItemResult,
    PosterTitleImageJoinParams,
    PosterTitleImageJoinRuntimeFields,
    PosterTitleImageResult,
)
