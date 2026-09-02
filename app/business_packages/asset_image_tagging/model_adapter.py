from __future__ import annotations

import hashlib
from typing import Any

from app.business_packages.asset_image_tagging.prompt_builder import (
    build_item_prompt_context,
    matching_label_groups,
)
from app.business_packages.asset_image_tagging.schemas import (
    AssetImageTaggingAssetDescription,
    AssetImageTaggingBatchSummary,
    AssetImageTaggingCandidateLabel,
    AssetImageTaggingItemError,
    AssetImageTaggingItemParams,
    AssetImageTaggingLabelGroupSelection,
    AssetImageTaggingLabelSnapshotGroup,
    AssetImageTaggingParams,
    AssetImageTaggingResultItem,
    AssetImageTaggingSelectedLabel,
    AssetImageTaggingValidationIssue,
)

ITEM_LABEL_SNAPSHOT_NOT_FOUND = "LABEL_SNAPSHOT_NOT_FOUND"
ITEM_NO_LABEL_SELECTED = "NO_LABEL_SELECTED"
ISSUE_LABEL_GROUP_EMPTY = "label_group_empty"


def _stable_int(*parts: object) -> int:
    digest = hashlib.sha256(":".join(str(part) for part in parts).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big", signed=False)


def _multiple_label_count(
    item: AssetImageTaggingItemParams,
    label_snapshot_index: int,
    total_labels: int,
) -> int:
    if total_labels <= 3:
        return total_labels
    return 1 + (_stable_int(item.item_id, label_snapshot_index, "multiple") % 3)


def _label_weight(
    item: AssetImageTaggingItemParams,
    label_snapshot_index: int,
    label: AssetImageTaggingCandidateLabel,
) -> float:
    raw = _stable_int(item.item_id, label_snapshot_index, label.label_id) % 4500
    return round(0.55 + (raw / 10_000), 4)


def _label_reason(
    item: AssetImageTaggingItemParams,
    group: AssetImageTaggingLabelSnapshotGroup,
    label: AssetImageTaggingCandidateLabel,
) -> str:
    return f"deterministic stub selected {label.label_name} for {item.item_name} in {group.category_name}"


def _selected_labels(
    item: AssetImageTaggingItemParams,
    group: AssetImageTaggingLabelSnapshotGroup,
    label_snapshot_index: int,
) -> list[AssetImageTaggingSelectedLabel]:
    if not group.labels:
        return []

    if group.selection_mode == "single":
        selected = group.labels[:1]
    else:
        selected = group.labels[: _multiple_label_count(item, label_snapshot_index, len(group.labels))]

    return [
        AssetImageTaggingSelectedLabel(
            label_id=label.label_id,
            label_name=label.label_name,
            definition=label.definition,
            weight=_label_weight(item, label_snapshot_index, label),
            reason=_label_reason(item, group, label),
        )
        for label in selected
    ]


def _label_group_selection(
    item: AssetImageTaggingItemParams,
    group: AssetImageTaggingLabelSnapshotGroup,
    label_snapshot_index: int,
) -> tuple[AssetImageTaggingLabelGroupSelection, list[AssetImageTaggingValidationIssue]]:
    selected_labels = _selected_labels(item, group, label_snapshot_index)
    issues: list[AssetImageTaggingValidationIssue] = []
    if not selected_labels:
        issues.append(
            AssetImageTaggingValidationIssue(
                issue=ISSUE_LABEL_GROUP_EMPTY,
                label_snapshot_index=label_snapshot_index,
                message="label_snapshot group did not provide any selectable labels",
                details={
                    "item_id": item.item_id,
                    "category_id": item.category_id,
                    "selection_mode": group.selection_mode,
                },
            )
        )
    return (
        AssetImageTaggingLabelGroupSelection(
            label_snapshot_index=label_snapshot_index,
            category_id=group.category_id,
            category_name=group.category_name,
            selection_mode=group.selection_mode,
            labels=selected_labels,
        ),
        issues,
    )


def _asset_description(
    language: str,
    item: AssetImageTaggingItemParams,
    label_names: list[str],
) -> AssetImageTaggingAssetDescription:
    joined_labels = ", ".join(label_names)
    if language == "zh":
        text = f"{item.item_name}，分类为{item.category_name}，候选标签：{joined_labels}。"
    else:
        text = f"{item.item_name} in category {item.category_name}; selected labels: {joined_labels}."
    return AssetImageTaggingAssetDescription(language=language, text=text)


def _item_error(code: str, message: str, details: dict[str, Any]) -> AssetImageTaggingItemError:
    return AssetImageTaggingItemError(code=code, message=message, details=details)


def _failed_item(
    item: AssetImageTaggingItemParams,
    *,
    code: str,
    message: str,
    details: dict[str, Any],
    validation_issues: list[AssetImageTaggingValidationIssue] | None = None,
) -> AssetImageTaggingResultItem:
    return AssetImageTaggingResultItem(
        item_id=item.item_id,
        item_name=item.item_name,
        category_id=item.category_id,
        category_name=item.category_name,
        asset=item.asset,
        status="failed",
        label_group_selections=[],
        asset_description=None,
        validation_issues=validation_issues or [],
        error=_item_error(code, message, details),
    )


def build_result_item(
    *,
    item: AssetImageTaggingItemParams,
    tagging_language: str,
    label_snapshot: list[AssetImageTaggingLabelSnapshotGroup],
) -> AssetImageTaggingResultItem:
    prompt_context = build_item_prompt_context(
        item=item,
        tagging_language=tagging_language,
        label_snapshot=label_snapshot,
    )
    matching_groups = matching_label_groups(item, label_snapshot)
    if not matching_groups:
        return _failed_item(
            item,
            code=ITEM_LABEL_SNAPSHOT_NOT_FOUND,
            message="no matching label_snapshot group for item category",
            details={
                "item_id": prompt_context["item"]["item_id"],
                "category_id": prompt_context["item"]["category_id"],
            },
        )

    selections: list[AssetImageTaggingLabelGroupSelection] = []
    validation_issues: list[AssetImageTaggingValidationIssue] = []
    for label_snapshot_index, group in matching_groups:
        selection, issues = _label_group_selection(item, group, label_snapshot_index)
        selections.append(selection)
        validation_issues.extend(issues)

    selected_labels = [label for selection in selections for label in selection.labels]
    if not selected_labels:
        return _failed_item(
            item,
            code=ITEM_NO_LABEL_SELECTED,
            message="no labels were selected for item",
            details={
                "item_id": item.item_id,
                "category_id": item.category_id,
                "matching_group_count": len(matching_groups),
            },
            validation_issues=validation_issues,
        )

    status = "partial_success" if validation_issues else "succeeded"
    return AssetImageTaggingResultItem(
        item_id=item.item_id,
        item_name=item.item_name,
        category_id=item.category_id,
        category_name=item.category_name,
        asset=item.asset,
        status=status,
        label_group_selections=selections,
        asset_description=_asset_description(
            tagging_language,
            item,
            [label.label_name for label in selected_labels],
        ),
        validation_issues=validation_issues,
        error=None,
    )


def build_batch_summary(items: list[AssetImageTaggingResultItem]) -> AssetImageTaggingBatchSummary:
    return AssetImageTaggingBatchSummary(
        total=len(items),
        succeeded=sum(1 for item in items if item.status == "succeeded"),
        partial_success=sum(1 for item in items if item.status == "partial_success"),
        failed=sum(1 for item in items if item.status == "failed"),
    )


class DeterministicAssetImageTaggingModelAdapter:
    async def tag(self, params: AssetImageTaggingParams) -> list[AssetImageTaggingResultItem]:
        return [
            build_result_item(
                item=item,
                tagging_language=params.tagging_language,
                label_snapshot=params.label_snapshot,
            )
            for item in params.items
        ]
