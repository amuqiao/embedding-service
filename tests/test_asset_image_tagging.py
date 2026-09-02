from __future__ import annotations

import pytest

from app.business_packages.asset_image_tagging.model_adapter import build_result_item
from app.business_packages.asset_image_tagging.prompt_builder import build_batch_prompt_payload
from app.business_packages.asset_image_tagging.schemas import (
    AssetImageTaggingAssetRef,
    AssetImageTaggingItemParams,
    AssetImageTaggingLabelSnapshotGroup,
    AssetImageTaggingParams,
)


def _item(category_id: str = "hair") -> AssetImageTaggingItemParams:
    return AssetImageTaggingItemParams(
        item_id="asset_001",
        item_name="棕色中长卷发",
        category_id=category_id,
        category_name="发型",
        asset=AssetImageTaggingAssetRef(
            public_url="https://example.com/assets/hair_001.png",
            content_type="image/png",
        ),
    )


def _label_group(selection_mode: str = "single") -> AssetImageTaggingLabelSnapshotGroup:
    return AssetImageTaggingLabelSnapshotGroup(
        category_id="hair",
        category_name="发型",
        selection_mode=selection_mode,
        labels=[
            {
                "label_id": "hair_color_brown",
                "label_name": "棕色",
                "definition": "头发主体颜色为棕色或棕褐色",
            },
            {
                "label_id": "hair_color_black",
                "label_name": "黑色",
                "definition": "头发主体颜色为黑色或深黑色",
            },
        ],
    )


def test_asset_image_tagging_selects_from_matching_category_only():
    item = build_result_item(
        item=_item(),
        tagging_language="zh",
        label_snapshot=[_label_group()],
    )

    assert item.status == "succeeded"
    assert item.category_id == "hair"
    assert item.label_group_selections[0].category_id == "hair"
    assert item.label_group_selections[0].labels[0].label_id == "hair_color_brown"


def test_asset_image_tagging_fails_item_without_matching_category():
    item = build_result_item(
        item=_item(category_id="clothes"),
        tagging_language="zh",
        label_snapshot=[_label_group()],
    )

    assert item.status == "failed"
    assert item.error is not None
    assert item.error.code == "LABEL_SNAPSHOT_NOT_FOUND"


def test_asset_image_tagging_params_reject_missing_category_snapshot():
    with pytest.raises(ValueError, match="items\\[\\]\\.category_id"):
        AssetImageTaggingParams(
            tagging_language="zh",
            items=[_item(category_id="clothes")],
            label_snapshot=[_label_group()],
        )


def test_asset_image_tagging_rejects_non_image_asset():
    with pytest.raises(ValueError, match="content_type"):
        AssetImageTaggingAssetRef(
            public_url="https://example.com/assets/hair_001.txt",
            content_type="text/plain",
        )


def test_asset_image_tagging_prompt_payload_keeps_item_category_scope():
    payload = build_batch_prompt_payload(
        AssetImageTaggingParams(
            tagging_language="zh",
            items=[_item()],
            label_snapshot=[_label_group()],
        )
    )

    assert payload["tagging_language"] == "zh"
    assert payload["items"][0]["item"]["category_id"] == "hair"
    assert payload["items"][0]["label_groups"][0]["selection_mode"] == "single"
