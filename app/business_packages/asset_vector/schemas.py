from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from app.core.language_catalog import supported_language_codes
from app.schemas.common import StrictBaseModel
from app.schemas.jobs import RuntimeFieldsBase

ASSET_VECTOR_MAX_ITEMS = 500
ASSET_VECTOR_MAX_ITEM_ID_LENGTH = 255
ASSET_VECTOR_MAX_ITEM_NAME_LENGTH = 512
ASSET_VECTOR_MAX_LABELS = 200
ASSET_VECTOR_MAX_LABEL_ID_LENGTH = 255
ASSET_VECTOR_MAX_LABEL_NAME_LENGTH = 255
ASSET_VECTOR_MAX_LABEL_DEFINITION_LENGTH = 4096
ASSET_VECTOR_MAX_URL_LENGTH = 2048
ASSET_VECTOR_MAX_QUERY_LENGTH = 2048
ASSET_VECTOR_DEFAULT_TOP_K = 20
ASSET_VECTOR_MAX_TOP_K = 100
BARE_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

SearchMode = Literal["text", "image", "item_ids", "hybrid"]
AssetVectorItemStatus = Literal["succeeded"]
AssetVectorDeleteItemStatus = Literal["deleted"]


class AssetVectorAssetRef(StrictBaseModel):
    public_url: str = Field(min_length=1, max_length=ASSET_VECTOR_MAX_URL_LENGTH)
    content_type: str = Field(min_length=1, max_length=128)
    internal_url: str | None = Field(default=None, min_length=1, max_length=ASSET_VECTOR_MAX_URL_LENGTH)
    sha256: str | None = Field(default=None, min_length=64, max_length=64)

    @field_validator("public_url")
    @classmethod
    def validate_public_url(cls, value: str) -> str:
        if not value.startswith("https://"):
            raise ValueError("public_url must start with https://")
        return value

    @field_validator("sha256")
    @classmethod
    def validate_sha256(cls, value: str | None) -> str | None:
        if value is not None and not BARE_SHA256_RE.fullmatch(value):
            raise ValueError("sha256 must be 64 lowercase hex characters")
        return value


class AssetVectorLabel(StrictBaseModel):
    label_id: str = Field(min_length=1, max_length=ASSET_VECTOR_MAX_LABEL_ID_LENGTH)
    language: str = Field(min_length=1, max_length=16)
    label_name: str = Field(min_length=1, max_length=ASSET_VECTOR_MAX_LABEL_NAME_LENGTH)
    definition: str | None = Field(default=None, min_length=1, max_length=ASSET_VECTOR_MAX_LABEL_DEFINITION_LENGTH)

    @field_validator("language")
    @classmethod
    def validate_language(cls, value: str) -> str:
        if value not in supported_language_codes():
            raise ValueError("language is not supported")
        return value


class AssetVectorUpsertItemParams(StrictBaseModel):
    item_id: str = Field(min_length=1, max_length=ASSET_VECTOR_MAX_ITEM_ID_LENGTH)
    item_name: str = Field(min_length=1, max_length=ASSET_VECTOR_MAX_ITEM_NAME_LENGTH)
    asset: AssetVectorAssetRef
    labels: list[AssetVectorLabel] = Field(default_factory=list, max_length=ASSET_VECTOR_MAX_LABELS)
    metadata: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_labels(self) -> "AssetVectorUpsertItemParams":
        label_keys = [(label.label_id, label.language) for label in self.labels]
        if len(label_keys) != len(set(label_keys)):
            raise ValueError("labels[].label_id + language must be unique within item")
        return self


class AssetVectorBatchUpsertParams(StrictBaseModel):
    items: list[AssetVectorUpsertItemParams] = Field(min_length=1, max_length=ASSET_VECTOR_MAX_ITEMS)

    @model_validator(mode="after")
    def validate_items(self) -> "AssetVectorBatchUpsertParams":
        item_ids = [item.item_id for item in self.items]
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("items[].item_id must be unique")
        return self


class AssetVectorBatchDeleteParams(StrictBaseModel):
    item_ids: list[str] = Field(min_length=1, max_length=ASSET_VECTOR_MAX_ITEMS)

    @model_validator(mode="after")
    def validate_item_ids(self) -> "AssetVectorBatchDeleteParams":
        if any(not item_id for item_id in self.item_ids):
            raise ValueError("item_ids[] must not be empty")
        if len(self.item_ids) != len(set(self.item_ids)):
            raise ValueError("item_ids[] must be unique")
        return self


class AssetVectorBatchUpsertRuntimeFields(RuntimeFieldsBase):
    operation: Literal["asset_vector_batch_upsert"] = "asset_vector_batch_upsert"
    item_count: int = Field(ge=1, le=ASSET_VECTOR_MAX_ITEMS)


class AssetVectorBatchDeleteRuntimeFields(RuntimeFieldsBase):
    operation: Literal["asset_vector_batch_delete"] = "asset_vector_batch_delete"
    item_count: int = Field(ge=1, le=ASSET_VECTOR_MAX_ITEMS)


class AssetVectorEmbedItemParams(StrictBaseModel):
    item: AssetVectorUpsertItemParams


class AssetVectorEmbedItemRuntimeFields(RuntimeFieldsBase):
    operation: Literal["asset_vector_embed_item"] = "asset_vector_embed_item"
    item_id: str = Field(min_length=1, max_length=ASSET_VECTOR_MAX_ITEM_ID_LENGTH)


class AssetVectorUpsertJoinParams(StrictBaseModel):
    item_ids: list[str] = Field(min_length=1, max_length=ASSET_VECTOR_MAX_ITEMS)

    @model_validator(mode="after")
    def validate_item_ids(self) -> "AssetVectorUpsertJoinParams":
        if any(not item_id for item_id in self.item_ids):
            raise ValueError("item_ids[] must not be empty")
        if len(self.item_ids) != len(set(self.item_ids)):
            raise ValueError("item_ids[] must be unique")
        return self


class AssetVectorUpsertJoinRuntimeFields(RuntimeFieldsBase):
    operation: Literal["asset_vector_upsert_join"] = "asset_vector_upsert_join"
    item_count: int = Field(ge=1, le=ASSET_VECTOR_MAX_ITEMS)


class AssetVectorEmbeddedItemResult(StrictBaseModel):
    item: AssetVectorUpsertItemParams
    embedding: list[float] = Field(min_length=1)
    embedding_text: str = Field(min_length=1)
    model_id: str = Field(min_length=1, max_length=255)
    dimension: int = Field(gt=0)
    input_sha256: str = Field(min_length=64, max_length=64)


class AssetVectorIndexedInfo(StrictBaseModel):
    indexed_at: str = Field(min_length=1)


class AssetVectorUpsertResultItem(StrictBaseModel):
    item_id: str = Field(min_length=1, max_length=ASSET_VECTOR_MAX_ITEM_ID_LENGTH)
    status: AssetVectorItemStatus
    indexed: AssetVectorIndexedInfo


class AssetVectorDeleteResultItem(StrictBaseModel):
    item_id: str = Field(min_length=1, max_length=ASSET_VECTOR_MAX_ITEM_ID_LENGTH)
    status: AssetVectorDeleteItemStatus


class AssetVectorUpsertBatchSummary(StrictBaseModel):
    total: int = Field(ge=1)
    succeeded: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_counts(self) -> "AssetVectorUpsertBatchSummary":
        if self.succeeded != self.total:
            raise ValueError("batch_summary counts must add up to total")
        return self


class AssetVectorDeleteBatchSummary(StrictBaseModel):
    total: int = Field(ge=1)
    deleted: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_counts(self) -> "AssetVectorDeleteBatchSummary":
        if self.deleted != self.total:
            raise ValueError("batch_summary counts must add up to total")
        return self


class AssetVectorBatchUpsertResult(StrictBaseModel):
    schema_version: Literal["default"] = "default"
    job_type: Literal["asset_vector_batch_upsert"] = "asset_vector_batch_upsert"
    batch_summary: AssetVectorUpsertBatchSummary
    items: list[AssetVectorUpsertResultItem] = Field(min_length=1, max_length=ASSET_VECTOR_MAX_ITEMS)


class AssetVectorBatchDeleteResult(StrictBaseModel):
    schema_version: Literal["default"] = "default"
    job_type: Literal["asset_vector_batch_delete"] = "asset_vector_batch_delete"
    batch_summary: AssetVectorDeleteBatchSummary
    items: list[AssetVectorDeleteResultItem] = Field(min_length=1, max_length=ASSET_VECTOR_MAX_ITEMS)


class AssetVectorTextQuery(StrictBaseModel):
    query: str = Field(min_length=1, max_length=ASSET_VECTOR_MAX_QUERY_LENGTH)


class AssetVectorSearchRequest(StrictBaseModel):
    search_mode: SearchMode
    text: AssetVectorTextQuery | None = None
    asset: AssetVectorAssetRef | None = None
    item_ids: list[str] | None = Field(default=None, min_length=1, max_length=ASSET_VECTOR_MAX_ITEMS)
    candidate_item_ids: list[str] | None = Field(default=None, max_length=ASSET_VECTOR_MAX_ITEMS)
    top_k: int | None = Field(default=None, gt=0, le=ASSET_VECTOR_MAX_TOP_K)

    @field_validator("item_ids", "candidate_item_ids")
    @classmethod
    def validate_id_list(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        if any(not item_id for item_id in value):
            raise ValueError("item ids must not be empty")
        if len(value) != len(set(value)):
            raise ValueError("item ids must be unique")
        return value

    @model_validator(mode="after")
    def validate_mode_shape(self) -> "AssetVectorSearchRequest":
        has_text = self.text is not None
        has_asset = self.asset is not None
        has_item_ids = self.item_ids is not None
        if self.search_mode == "text":
            if not has_text or has_asset or has_item_ids:
                raise ValueError("text search requires text only")
            return self
        if self.search_mode == "image":
            if has_text or not has_asset or has_item_ids:
                raise ValueError("image search requires asset only")
            self._validate_image_asset()
            return self
        if self.search_mode == "item_ids":
            if has_text or has_asset or not has_item_ids:
                raise ValueError("item_ids search requires item_ids only")
            return self
        if sum(1 for value in (has_text, has_asset, has_item_ids) if value) < 2:
            raise ValueError("hybrid search requires at least two query inputs")
        if has_asset:
            self._validate_image_asset()
        return self

    def _validate_image_asset(self) -> None:
        if self.asset is None:
            return
        if not self.asset.content_type.startswith("image/"):
            raise ValueError("image search asset.content_type must start with image/")


class AssetVectorSearchResponse(StrictBaseModel):
    item_ids: list[str] = Field(default_factory=list)


class AssetVectorExistsRequest(StrictBaseModel):
    item_ids: list[str] = Field(min_length=1, max_length=ASSET_VECTOR_MAX_ITEMS)

    @model_validator(mode="after")
    def validate_item_ids(self) -> "AssetVectorExistsRequest":
        if any(not item_id for item_id in self.item_ids):
            raise ValueError("item_ids[] must not be empty")
        if len(self.item_ids) != len(set(self.item_ids)):
            raise ValueError("item_ids[] must be unique")
        return self


class AssetVectorExistsItem(StrictBaseModel):
    item_id: str = Field(min_length=1, max_length=ASSET_VECTOR_MAX_ITEM_ID_LENGTH)
    exists: bool


class AssetVectorExistsResponse(StrictBaseModel):
    items: list[AssetVectorExistsItem] = Field(min_length=1, max_length=ASSET_VECTOR_MAX_ITEMS)


class AssetVectorIdsResponse(StrictBaseModel):
    item_ids: list[str] = Field(default_factory=list)
    next_cursor: str | None = None


SCHEMAS = (
    AssetVectorAssetRef,
    AssetVectorLabel,
    AssetVectorUpsertItemParams,
    AssetVectorBatchUpsertParams,
    AssetVectorBatchDeleteParams,
    AssetVectorBatchUpsertRuntimeFields,
    AssetVectorBatchDeleteRuntimeFields,
    AssetVectorEmbedItemParams,
    AssetVectorEmbedItemRuntimeFields,
    AssetVectorUpsertJoinParams,
    AssetVectorUpsertJoinRuntimeFields,
    AssetVectorEmbeddedItemResult,
    AssetVectorIndexedInfo,
    AssetVectorUpsertResultItem,
    AssetVectorDeleteResultItem,
    AssetVectorUpsertBatchSummary,
    AssetVectorDeleteBatchSummary,
    AssetVectorBatchUpsertResult,
    AssetVectorBatchDeleteResult,
    AssetVectorTextQuery,
    AssetVectorSearchRequest,
    AssetVectorSearchResponse,
    AssetVectorExistsRequest,
    AssetVectorExistsItem,
    AssetVectorExistsResponse,
    AssetVectorIdsResponse,
)
