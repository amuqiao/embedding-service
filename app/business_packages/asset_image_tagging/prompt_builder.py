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


def model_item_ref(index: int) -> str:
    return f"I{index + 1}"


def model_group_ref(index: int) -> str:
    return f"G{index + 1}"


def model_label_ref(index: int) -> str:
    return f"L{index + 1}"


def build_item_prompt_context(
    *,
    item_index: int,
    item: AssetImageTaggingItemParams,
    tagging_language: str,
    label_snapshot: list[AssetImageTaggingLabelSnapshotGroup],
) -> dict[str, Any]:
    return {
        "tagging_language": tagging_language,
        "item": {
            "item_ref": model_item_ref(item_index),
            "item_name": item.item_name,
            "category_name": item.category_name,
        },
        "label_groups": [
            {
                "group_ref": model_group_ref(index),
                "category_name": group.category_name,
                "selection_mode": group.selection_mode,
                "labels": [
                    {
                        "label_ref": model_label_ref(label_index),
                        "label_name": label.label_name,
                        "definition": label.definition,
                    }
                    for label_index, label in enumerate(group.labels)
                ],
            }
            for index, group in matching_label_groups(item, label_snapshot)
        ],
    }


def build_batch_prompt_payload(params: AssetImageTaggingParams) -> dict[str, Any]:
    return {
        "tagging_language": params.tagging_language,
        "items": [
            build_item_prompt_context(
                item_index=index,
                item=item,
                tagging_language=params.tagging_language,
                label_snapshot=params.label_snapshot,
            )
            for index, item in enumerate(params.items)
        ],
    }
