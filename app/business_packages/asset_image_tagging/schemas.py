from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from app.core.language_catalog import supported_language_codes
from app.schemas.common import StrictBaseModel
from app.schemas.jobs import RuntimeFieldsBase

ASSET_IMAGE_TAGGING_SCHEMA_MAX_ITEMS = 100
ASSET_IMAGE_TAGGING_MAX_LABEL_GROUPS = 500
ASSET_IMAGE_TAGGING_MAX_ITEM_ID_LENGTH = 255
ASSET_IMAGE_TAGGING_MAX_ITEM_NAME_LENGTH = 512
ASSET_IMAGE_TAGGING_MAX_CATEGORY_ID_LENGTH = 255
ASSET_IMAGE_TAGGING_MAX_CATEGORY_NAME_LENGTH = 255
ASSET_IMAGE_TAGGING_MAX_LABEL_ID_LENGTH = 255
ASSET_IMAGE_TAGGING_MAX_LABEL_NAME_LENGTH = 255
ASSET_IMAGE_TAGGING_MAX_LABEL_DEFINITION_LENGTH = 4096
ASSET_IMAGE_TAGGING_MAX_DESCRIPTION_LENGTH = 4096
ASSET_IMAGE_TAGGING_MAX_REASON_LENGTH = 1024
ASSET_IMAGE_TAGGING_MAX_MESSAGE_LENGTH = 1024
ASSET_IMAGE_TAGGING_MAX_URL_LENGTH = 2048
ASSET_IMAGE_TAGGING_ALLOWED_CONTENT_TYPES = frozenset({"image/png", "image/jpeg", "image/webp"})
BARE_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

SelectionMode = Literal["single", "multiple"]
AssetImageTaggingItemStatus = Literal["succeeded", "partial_success", "failed"]


class AssetImageTaggingAssetRef(StrictBaseModel):
    public_url: str = Field(min_length=1, max_length=ASSET_IMAGE_TAGGING_MAX_URL_LENGTH)
    content_type: str = Field(min_length=1, max_length=128)
    internal_url: str | None = Field(default=None, min_length=1, max_length=ASSET_IMAGE_TAGGING_MAX_URL_LENGTH)
    sha256: str | None = Field(default=None, min_length=64, max_length=64)

    @field_validator("public_url")
    @classmethod
    def validate_public_url(cls, value: str) -> str:
        if not value.startswith("https://"):
            raise ValueError("public_url must start with https://")
        return value

    @field_validator("content_type")
    @classmethod
    def validate_content_type(cls, value: str) -> str:
        if value not in ASSET_IMAGE_TAGGING_ALLOWED_CONTENT_TYPES:
            raise ValueError("content_type is not supported by asset_image_tagging")
        return value

    @field_validator("sha256")
    @classmethod
    def validate_sha256(cls, value: str | None) -> str | None:
        if value is not None and not BARE_SHA256_RE.fullmatch(value):
            raise ValueError("sha256 must be 64 lowercase hex characters")
        return value


class AssetImageTaggingItemParams(StrictBaseModel):
    item_id: str = Field(min_length=1, max_length=ASSET_IMAGE_TAGGING_MAX_ITEM_ID_LENGTH)
    item_name: str = Field(min_length=1, max_length=ASSET_IMAGE_TAGGING_MAX_ITEM_NAME_LENGTH)
    category_id: str = Field(min_length=1, max_length=ASSET_IMAGE_TAGGING_MAX_CATEGORY_ID_LENGTH)
    category_name: str = Field(min_length=1, max_length=ASSET_IMAGE_TAGGING_MAX_CATEGORY_NAME_LENGTH)
    asset: AssetImageTaggingAssetRef
    metadata: dict[str, Any] | None = None


class AssetImageTaggingCandidateLabel(StrictBaseModel):
    label_id: str = Field(min_length=1, max_length=ASSET_IMAGE_TAGGING_MAX_LABEL_ID_LENGTH)
    label_name: str = Field(min_length=1, max_length=ASSET_IMAGE_TAGGING_MAX_LABEL_NAME_LENGTH)
    definition: str = Field(min_length=1, max_length=ASSET_IMAGE_TAGGING_MAX_LABEL_DEFINITION_LENGTH)
    metadata: dict[str, Any] | None = None


class AssetImageTaggingLabelSnapshotGroup(StrictBaseModel):
    category_id: str = Field(min_length=1, max_length=ASSET_IMAGE_TAGGING_MAX_CATEGORY_ID_LENGTH)
    category_name: str = Field(min_length=1, max_length=ASSET_IMAGE_TAGGING_MAX_CATEGORY_NAME_LENGTH)
    selection_mode: SelectionMode
    labels: list[AssetImageTaggingCandidateLabel] = Field(min_length=1)
    metadata: dict[str, Any] | None = None


class AssetImageTaggingParams(StrictBaseModel):
    tagging_language: str = Field(min_length=1, max_length=16)
    items: list[AssetImageTaggingItemParams] = Field(
        min_length=1,
        max_length=ASSET_IMAGE_TAGGING_SCHEMA_MAX_ITEMS,
    )
    label_snapshot: list[AssetImageTaggingLabelSnapshotGroup] = Field(
        min_length=1,
        max_length=ASSET_IMAGE_TAGGING_MAX_LABEL_GROUPS,
    )

    @field_validator("tagging_language")
    @classmethod
    def validate_language(cls, value: str) -> str:
        if value not in supported_language_codes():
            raise ValueError("language is not supported")
        return value

    @model_validator(mode="after")
    def validate_unique_ids(self) -> "AssetImageTaggingParams":
        item_ids = [item.item_id for item in self.items]
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("items[].item_id must be unique")

        label_ids = [label.label_id for group in self.label_snapshot for label in group.labels]
        if len(label_ids) != len(set(label_ids)):
            raise ValueError("label_snapshot[].labels[].label_id must be globally unique")
        snapshot_category_ids = {group.category_id for group in self.label_snapshot}
        missing_category_ids = sorted(
            {item.category_id for item in self.items}
            - snapshot_category_ids
        )
        if missing_category_ids:
            raise ValueError("items[].category_id must exist in label_snapshot")
        return self


class AssetImageTaggingRuntimeFields(RuntimeFieldsBase):
    operation: Literal["asset_image_tagging"] = "asset_image_tagging"
    tagging_language: str = Field(min_length=1, max_length=16)
    item_count: int = Field(ge=1, le=ASSET_IMAGE_TAGGING_SCHEMA_MAX_ITEMS)
    label_group_count: int = Field(ge=1, le=ASSET_IMAGE_TAGGING_MAX_LABEL_GROUPS)
    category_ids: list[str] = Field(min_length=1)

    @field_validator("tagging_language")
    @classmethod
    def validate_language(cls, value: str) -> str:
        if value not in supported_language_codes():
            raise ValueError("language is not supported")
        return value

    @model_validator(mode="after")
    def validate_category_ids(self) -> "AssetImageTaggingRuntimeFields":
        if len(self.category_ids) != len(set(self.category_ids)):
            raise ValueError("category_ids must be unique")
        return self


class AssetImageTaggingItemJobParams(StrictBaseModel):
    tagging_language: str = Field(min_length=1, max_length=16)
    item: AssetImageTaggingItemParams
    label_snapshot: list[AssetImageTaggingLabelSnapshotGroup] = Field(min_length=1, max_length=ASSET_IMAGE_TAGGING_MAX_LABEL_GROUPS)
    label_snapshot_indexes: list[int] = Field(min_length=1, max_length=ASSET_IMAGE_TAGGING_MAX_LABEL_GROUPS)

    @field_validator("tagging_language")
    @classmethod
    def validate_language(cls, value: str) -> str:
        if value not in supported_language_codes():
            raise ValueError("language is not supported")
        return value

    @model_validator(mode="after")
    def validate_item_label_snapshot(self) -> "AssetImageTaggingItemJobParams":
        if len(self.label_snapshot) != len(self.label_snapshot_indexes):
            raise ValueError("label_snapshot_indexes length must match label_snapshot")
        if len(self.label_snapshot_indexes) != len(set(self.label_snapshot_indexes)):
            raise ValueError("label_snapshot_indexes must be unique")
        if any(index < 0 for index in self.label_snapshot_indexes):
            raise ValueError("label_snapshot_indexes must be >= 0")
        if not any(group.category_id == self.item.category_id for group in self.label_snapshot):
            raise ValueError("item.category_id must exist in label_snapshot")
        return self


class AssetImageTaggingItemRuntimeFields(RuntimeFieldsBase):
    operation: Literal["asset_image_tagging_item"] = "asset_image_tagging_item"
    tagging_language: str = Field(min_length=1, max_length=16)
    item_id: str = Field(min_length=1, max_length=ASSET_IMAGE_TAGGING_MAX_ITEM_ID_LENGTH)
    category_id: str = Field(min_length=1, max_length=ASSET_IMAGE_TAGGING_MAX_CATEGORY_ID_LENGTH)
    label_group_count: int = Field(ge=1, le=ASSET_IMAGE_TAGGING_MAX_LABEL_GROUPS)

    @field_validator("tagging_language")
    @classmethod
    def validate_language(cls, value: str) -> str:
        if value not in supported_language_codes():
            raise ValueError("language is not supported")
        return value


class AssetImageTaggingSelectedLabel(StrictBaseModel):
    label_id: str = Field(min_length=1, max_length=ASSET_IMAGE_TAGGING_MAX_LABEL_ID_LENGTH)
    label_name: str = Field(min_length=1, max_length=ASSET_IMAGE_TAGGING_MAX_LABEL_NAME_LENGTH)
    definition: str = Field(min_length=1, max_length=ASSET_IMAGE_TAGGING_MAX_LABEL_DEFINITION_LENGTH)
    weight: float = Field(gt=0, le=1)
    reason: str | None = Field(default=None, min_length=1, max_length=ASSET_IMAGE_TAGGING_MAX_REASON_LENGTH)


class AssetImageTaggingLabelGroupSelection(StrictBaseModel):
    label_snapshot_index: int = Field(ge=0)
    category_id: str = Field(min_length=1, max_length=ASSET_IMAGE_TAGGING_MAX_CATEGORY_ID_LENGTH)
    category_name: str = Field(min_length=1, max_length=ASSET_IMAGE_TAGGING_MAX_CATEGORY_NAME_LENGTH)
    selection_mode: SelectionMode
    labels: list[AssetImageTaggingSelectedLabel] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_selection_mode(self) -> "AssetImageTaggingLabelGroupSelection":
        if self.selection_mode == "single" and len(self.labels) > 1:
            raise ValueError("single selection_mode must return at most one label")
        return self


class AssetImageTaggingAssetDescription(StrictBaseModel):
    language: str = Field(min_length=1, max_length=16)
    text: str = Field(min_length=1, max_length=ASSET_IMAGE_TAGGING_MAX_DESCRIPTION_LENGTH)

    @field_validator("language")
    @classmethod
    def validate_language(cls, value: str) -> str:
        if value not in supported_language_codes():
            raise ValueError("language is not supported")
        return value


class AssetImageTaggingValidationIssue(StrictBaseModel):
    issue: str = Field(min_length=1, max_length=128)
    label_snapshot_index: int | None = Field(default=None, ge=0)
    label_id: str | None = Field(default=None, min_length=1, max_length=ASSET_IMAGE_TAGGING_MAX_LABEL_ID_LENGTH)
    message: str = Field(min_length=1, max_length=ASSET_IMAGE_TAGGING_MAX_MESSAGE_LENGTH)
    details: dict[str, Any] = Field(default_factory=dict)


class AssetImageTaggingItemError(StrictBaseModel):
    code: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=ASSET_IMAGE_TAGGING_MAX_MESSAGE_LENGTH)
    details: dict[str, Any] = Field(default_factory=dict)


class AssetImageTaggingResultItem(StrictBaseModel):
    item_id: str = Field(min_length=1, max_length=ASSET_IMAGE_TAGGING_MAX_ITEM_ID_LENGTH)
    item_name: str = Field(min_length=1, max_length=ASSET_IMAGE_TAGGING_MAX_ITEM_NAME_LENGTH)
    category_id: str = Field(min_length=1, max_length=ASSET_IMAGE_TAGGING_MAX_CATEGORY_ID_LENGTH)
    category_name: str = Field(min_length=1, max_length=ASSET_IMAGE_TAGGING_MAX_CATEGORY_NAME_LENGTH)
    asset: AssetImageTaggingAssetRef
    status: AssetImageTaggingItemStatus
    label_group_selections: list[AssetImageTaggingLabelGroupSelection] = Field(default_factory=list)
    asset_description: AssetImageTaggingAssetDescription | None = None
    validation_issues: list[AssetImageTaggingValidationIssue] = Field(default_factory=list)
    error: AssetImageTaggingItemError | None = None

    @model_validator(mode="after")
    def validate_status_shape(self) -> "AssetImageTaggingResultItem":
        if self.status == "succeeded":
            if not self.label_group_selections:
                raise ValueError("succeeded item requires label_group_selections")
            if self.validation_issues:
                raise ValueError("succeeded item must not contain validation_issues")
            if self.error is not None:
                raise ValueError("succeeded item requires null error")
            return self
        if self.status == "partial_success":
            if not self.label_group_selections:
                raise ValueError("partial_success item requires label_group_selections")
            if not self.validation_issues:
                raise ValueError("partial_success item requires validation_issues")
            if self.error is not None:
                raise ValueError("partial_success item requires null error")
            return self
        if self.label_group_selections:
            raise ValueError("failed item requires empty label_group_selections")
        if self.error is None:
            raise ValueError("failed item requires non-null error")
        if self.asset_description is not None:
            raise ValueError("failed item requires null asset_description")
        return self


class AssetImageTaggingBatchSummary(StrictBaseModel):
    total: int = Field(ge=1)
    succeeded: int = Field(ge=0)
    partial_success: int = Field(ge=0)
    failed: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_counts(self) -> "AssetImageTaggingBatchSummary":
        if self.succeeded + self.partial_success + self.failed != self.total:
            raise ValueError("batch_summary counts must add up to total")
        return self


class AssetImageTaggingJoinParams(StrictBaseModel):
    tagging_language: str = Field(min_length=1, max_length=16)
    item_ids: list[str] = Field(min_length=1, max_length=ASSET_IMAGE_TAGGING_SCHEMA_MAX_ITEMS)

    @field_validator("tagging_language")
    @classmethod
    def validate_language(cls, value: str) -> str:
        if value not in supported_language_codes():
            raise ValueError("language is not supported")
        return value

    @model_validator(mode="after")
    def validate_item_ids(self) -> "AssetImageTaggingJoinParams":
        if len(self.item_ids) != len(set(self.item_ids)):
            raise ValueError("item_ids must be unique")
        return self


class AssetImageTaggingJoinRuntimeFields(RuntimeFieldsBase):
    operation: Literal["asset_image_tagging_join"] = "asset_image_tagging_join"
    tagging_language: str = Field(min_length=1, max_length=16)
    item_count: int = Field(ge=1, le=ASSET_IMAGE_TAGGING_SCHEMA_MAX_ITEMS)

    @field_validator("tagging_language")
    @classmethod
    def validate_language(cls, value: str) -> str:
        if value not in supported_language_codes():
            raise ValueError("language is not supported")
        return value


class AssetImageTaggingResult(StrictBaseModel):
    schema_version: Literal["default"] = "default"
    job_type: Literal["asset_image_tagging"] = "asset_image_tagging"
    tagging_language: str = Field(min_length=1, max_length=16)
    batch_summary: AssetImageTaggingBatchSummary
    items: list[AssetImageTaggingResultItem] = Field(
        min_length=1,
        max_length=ASSET_IMAGE_TAGGING_SCHEMA_MAX_ITEMS,
    )

    @field_validator("tagging_language")
    @classmethod
    def validate_language(cls, value: str) -> str:
        if value not in supported_language_codes():
            raise ValueError("language is not supported")
        return value

    @model_validator(mode="after")
    def validate_summary(self) -> "AssetImageTaggingResult":
        counts = {
            "succeeded": sum(1 for item in self.items if item.status == "succeeded"),
            "partial_success": sum(1 for item in self.items if item.status == "partial_success"),
            "failed": sum(1 for item in self.items if item.status == "failed"),
        }
        if self.batch_summary.total != len(self.items):
            raise ValueError("batch_summary.total must equal items count")
        for key, value in counts.items():
            if getattr(self.batch_summary, key) != value:
                raise ValueError(f"batch_summary.{key} must match items")
        return self


SCHEMAS = (
    AssetImageTaggingAssetRef,
    AssetImageTaggingItemParams,
    AssetImageTaggingCandidateLabel,
    AssetImageTaggingLabelSnapshotGroup,
    AssetImageTaggingParams,
    AssetImageTaggingRuntimeFields,
    AssetImageTaggingItemJobParams,
    AssetImageTaggingItemRuntimeFields,
    AssetImageTaggingSelectedLabel,
    AssetImageTaggingLabelGroupSelection,
    AssetImageTaggingAssetDescription,
    AssetImageTaggingValidationIssue,
    AssetImageTaggingItemError,
    AssetImageTaggingResultItem,
    AssetImageTaggingBatchSummary,
    AssetImageTaggingJoinParams,
    AssetImageTaggingJoinRuntimeFields,
    AssetImageTaggingResult,
)
