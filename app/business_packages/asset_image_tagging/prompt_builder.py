from __future__ import annotations

from typing import Any

from app.business_packages.asset_image_tagging.schemas import (
    AssetImageTaggingItemParams,
    AssetImageTaggingLabelSnapshotGroup,
    AssetImageTaggingParams,
)


def matching_label_groups(
    item: AssetImageTaggingItemParams,
    label_snapshot: list[AssetImageTaggingLabelSnapshotGroup],
) -> list[tuple[int, AssetImageTaggingLabelSnapshotGroup]]:
    return [
        (index, group)
        for index, group in enumerate(label_snapshot)
        if group.category_id == item.category_id
    ]


def build_item_prompt_context(
    *,
    item: AssetImageTaggingItemParams,
    tagging_language: str,
    label_snapshot: list[AssetImageTaggingLabelSnapshotGroup],
) -> dict[str, Any]:
    return {
        "tagging_language": tagging_language,
        "item": {
            "item_id": item.item_id,
            "item_name": item.item_name,
            "category_id": item.category_id,
            "category_name": item.category_name,
            "asset": item.asset.model_dump(exclude_none=True),
        },
        "label_groups": [
            {
                "label_snapshot_index": index,
                "category_id": group.category_id,
                "category_name": group.category_name,
                "selection_mode": group.selection_mode,
                "labels": [label.model_dump(exclude_none=True) for label in group.labels],
            }
            for index, group in matching_label_groups(item, label_snapshot)
        ],
    }


def build_batch_prompt_payload(params: AssetImageTaggingParams) -> dict[str, Any]:
    return {
        "tagging_language": params.tagging_language,
        "items": [
            build_item_prompt_context(
                item=item,
                tagging_language=params.tagging_language,
                label_snapshot=params.label_snapshot,
            )
            for item in params.items
        ],
    }
