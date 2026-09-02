from __future__ import annotations

import hashlib
import json
from typing import Any, Callable, Protocol

from openai import APIStatusError, APITimeoutError, AsyncOpenAI
from pydantic import ValidationError

from app.business_packages.asset_image_tagging.prompt_builder import (
    build_batch_prompt_payload,
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
from app.core.config import settings
from app.core.exceptions import AppError
from app.services.job_context import extract_json_object

ITEM_LABEL_SNAPSHOT_NOT_FOUND = "LABEL_SNAPSHOT_NOT_FOUND"
ITEM_NO_LABEL_SELECTED = "NO_LABEL_SELECTED"
ITEM_MODEL_RESPONSE_INVALID = "MODEL_RESPONSE_INVALID"
ISSUE_LABEL_GROUP_EMPTY = "label_group_empty"
ISSUE_MODEL_RESPONSE_INVALID = "model_response_invalid"
ISSUE_MODEL_LABEL_INVALID = "model_label_invalid"
ISSUE_MODEL_LABEL_WEIGHT_INVALID = "model_label_weight_invalid"
ISSUE_MODEL_SINGLE_SELECTION_INVALID = "model_single_selection_invalid"
ISSUE_MODEL_DESCRIPTION_MISSING = "model_description_missing"
_MODEL_BATCH_ITEM_COUNT = 5

_SYSTEM_PROMPT = """你是图片素材打标服务。
你只能根据图片内容、素材名称、分类和候选标签进行判断。
必须只输出 JSON object，不要输出 Markdown、解释文字或代码块。
只能选择候选标签中给出的 label_id。
selection_mode=single 的标签组必须选择 1 个最匹配标签；selection_mode=multiple 的标签组可以选择 0 到多个标签。
每个输入 label_groups 都必须在 label_group_selections 中返回一条记录；multiple 没有合适标签时返回空 labels。
weight 必须是 0 到 1 之间的小数，表示置信度。
reason 使用 tagging_language 指定语言，简短说明选择原因。
"""


def _client(
    *,
    api_key: str,
    timeout_seconds: int,
    api_base: str | None,
) -> AsyncOpenAI:
    kwargs: dict[str, Any] = {
        "api_key": api_key,
        "timeout": timeout_seconds,
        "max_retries": 0,
    }
    if api_base:
        kwargs["base_url"] = api_base
    return AsyncOpenAI(**kwargs)


def _build_user_prompt(params: AssetImageTaggingParams) -> str:
    prompt_payload = build_batch_prompt_payload(params)
    return (
        "请为下面的批量素材打标签。\n"
        "输入 JSON 中每个 item 只允许使用它自己的 label_groups。\n"
        "每个 label_group 都必须返回一条 label_group_selections 记录。\n"
        "selection_mode=single 必须选择 1 个最匹配标签；selection_mode=multiple 可以返回空 labels。\n"
        "请按以下 JSON 结构返回：\n"
        "{\n"
        '  "items": [\n'
        "    {\n"
        '      "item_id": "原 item_id",\n'
        '      "asset_description": "素材描述",\n'
        '      "label_group_selections": [\n'
        "        {\n"
        '          "label_snapshot_index": 0,\n'
        '          "labels": [\n'
        '            {"label_id": "候选 label_id", "weight": 0.9, "reason": "选择原因"}\n'
        "          ]\n"
        "        }\n"
        "      ]\n"
        "    }\n"
        "  ]\n"
        "}\n\n"
        f"输入 JSON：\n{json.dumps(prompt_payload, ensure_ascii=False, separators=(',', ':'))}"
    )


def _build_responses_content(params: AssetImageTaggingParams) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = [{"type": "input_text", "text": _build_user_prompt(params)}]
    for index, item in enumerate(params.items, start=1):
        content.append(
            {
                "type": "input_text",
                "text": (
                    f"图片 {index}: item_id={item.item_id}, item_name={item.item_name}, "
                    f"category_id={item.category_id}, category_name={item.category_name}"
                ),
            }
        )
        content.append({"type": "input_image", "image_url": item.asset.public_url})
    return content


def _provider_error_details(
    exc: APIStatusError,
    *,
    model_adapter: str,
    model_id: str,
    base_url: str,
    item_ids: list[str],
) -> dict[str, Any]:
    details: dict[str, Any] = {
        "model_adapter": model_adapter,
        "model_id": model_id,
        "base_url": base_url,
        "item_ids": item_ids,
        "provider_status_code": exc.status_code,
    }
    try:
        payload = exc.response.json()
    except Exception:
        payload = None
    if isinstance(payload, dict):
        error_payload = payload.get("error")
        error_object = error_payload if isinstance(error_payload, dict) else {}
        code = payload.get("code") or error_object.get("code")
        message = payload.get("message") or error_object.get("message")
        if code is not None:
            details["provider_code"] = str(code)
        if message is not None:
            details["provider_message"] = str(message)
    else:
        details["provider_message"] = exc.message
    return details


def _model_batch_params(
    *,
    params: AssetImageTaggingParams,
    items: list[AssetImageTaggingItemParams],
) -> AssetImageTaggingParams:
    return AssetImageTaggingParams(
        tagging_language=params.tagging_language,
        items=items,
        label_snapshot=params.label_snapshot,
    )


def _model_output_text(response: Any) -> str:
    output_text = getattr(response, "output_text", None)
    if isinstance(output_text, str) and output_text.strip():
        return output_text

    output = getattr(response, "output", None)
    if isinstance(output, list):
        texts: list[str] = []
        for item in output:
            if getattr(item, "type", None) != "message":
                continue
            for content in getattr(item, "content", []):
                if getattr(content, "type", None) == "output_text":
                    texts.append(str(content.text))
        text = "\n".join(texts).strip()
        if text:
            return text

    choices = getattr(response, "choices", None)
    if not isinstance(choices, list) or not choices:
        raise AppError("MODEL_OUTPUT_INVALID", "asset_image_tagging model response missing text content")
    message = getattr(choices[0], "message", None)
    content = getattr(message, "content", None)
    if isinstance(content, str) and content.strip():
        return content
    raise AppError("MODEL_OUTPUT_INVALID", "asset_image_tagging model response missing text content")


def _invalid_output(message: str, details: dict[str, Any] | None = None) -> AppError:
    return AppError("MODEL_OUTPUT_INVALID", message, details=details or {})


def _index_model_items(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    raw_items = payload.get("items")
    if not isinstance(raw_items, list):
        raise _invalid_output("asset_image_tagging model output must contain items list")

    model_items: dict[str, dict[str, Any]] = {}
    for index, raw_item in enumerate(raw_items):
        if not isinstance(raw_item, dict):
            raise _invalid_output(
                "asset_image_tagging model output item must be an object",
                {"index": index},
            )
        item_id = raw_item.get("item_id")
        if not isinstance(item_id, str) or not item_id:
            raise _invalid_output(
                "asset_image_tagging model output item_id must be a non-empty string",
                {"index": index},
            )
        if item_id in model_items:
            raise _invalid_output(
                "asset_image_tagging model output item_id must be unique",
                {"item_id": item_id},
            )
        model_items[item_id] = raw_item
    return model_items


def _selection_payload_by_index(
    *,
    item: AssetImageTaggingItemParams,
    model_item: dict[str, Any],
) -> tuple[dict[int, dict[str, Any]], list[AssetImageTaggingValidationIssue]]:
    raw_selections = model_item.get("label_group_selections")
    if not isinstance(raw_selections, list):
        return {}, [
            AssetImageTaggingValidationIssue(
                issue=ISSUE_MODEL_RESPONSE_INVALID,
                message="model item must contain label_group_selections list",
                details={"item_id": item.item_id},
            )
        ]

    selections: dict[int, dict[str, Any]] = {}
    issues: list[AssetImageTaggingValidationIssue] = []
    for raw_selection in raw_selections:
        if not isinstance(raw_selection, dict):
            issues.append(
                AssetImageTaggingValidationIssue(
                    issue=ISSUE_MODEL_RESPONSE_INVALID,
                    message="model label_group_selection must be an object",
                    details={"item_id": item.item_id},
                )
            )
            continue
        label_snapshot_index = raw_selection.get("label_snapshot_index")
        if isinstance(label_snapshot_index, bool) or not isinstance(label_snapshot_index, int):
            issues.append(
                AssetImageTaggingValidationIssue(
                    issue=ISSUE_MODEL_RESPONSE_INVALID,
                    message="model label_snapshot_index must be an integer",
                    details={"item_id": item.item_id},
                )
            )
            continue
        if label_snapshot_index in selections:
            issues.append(
                AssetImageTaggingValidationIssue(
                    issue=ISSUE_MODEL_RESPONSE_INVALID,
                    label_snapshot_index=label_snapshot_index,
                    message="model returned duplicate label_snapshot_index for item",
                    details={"item_id": item.item_id},
                )
            )
            continue
        selections[label_snapshot_index] = raw_selection
    return selections, issues


def _description_from_model(
    *,
    item: AssetImageTaggingItemParams,
    tagging_language: str,
    model_item: dict[str, Any],
) -> tuple[AssetImageTaggingAssetDescription | None, list[AssetImageTaggingValidationIssue]]:
    raw_description = model_item.get("asset_description")
    if isinstance(raw_description, dict):
        raw_description = raw_description.get("text")
    if isinstance(raw_description, str) and raw_description.strip():
        return AssetImageTaggingAssetDescription(language=tagging_language, text=raw_description.strip()), []
    return None, [
        AssetImageTaggingValidationIssue(
            issue=ISSUE_MODEL_DESCRIPTION_MISSING,
            message="model item must contain non-empty asset_description",
            details={"item_id": item.item_id},
        )
    ]


def _selected_labels_from_model(
    *,
    item: AssetImageTaggingItemParams,
    group: AssetImageTaggingLabelSnapshotGroup,
    label_snapshot_index: int,
    selection_payload: dict[str, Any],
) -> tuple[list[AssetImageTaggingSelectedLabel], list[AssetImageTaggingValidationIssue]]:
    raw_labels = selection_payload.get("labels")
    if not isinstance(raw_labels, list):
        return [], [
            AssetImageTaggingValidationIssue(
                issue=ISSUE_MODEL_RESPONSE_INVALID,
                label_snapshot_index=label_snapshot_index,
                message="model selection must contain labels list",
                details={"item_id": item.item_id},
            )
        ]

    label_by_id = {label.label_id: label for label in group.labels}
    selected: list[AssetImageTaggingSelectedLabel] = []
    issues: list[AssetImageTaggingValidationIssue] = []
    for raw_label in raw_labels:
        if not isinstance(raw_label, dict):
            issues.append(
                AssetImageTaggingValidationIssue(
                    issue=ISSUE_MODEL_RESPONSE_INVALID,
                    label_snapshot_index=label_snapshot_index,
                    message="model selected label must be an object",
                    details={"item_id": item.item_id},
                )
            )
            continue
        label_id = raw_label.get("label_id")
        if not isinstance(label_id, str) or label_id not in label_by_id:
            issues.append(
                AssetImageTaggingValidationIssue(
                    issue=ISSUE_MODEL_LABEL_INVALID,
                    label_snapshot_index=label_snapshot_index,
                    label_id=str(label_id) if label_id is not None else None,
                    message="model selected label_id is not in this label group",
                    details={"item_id": item.item_id},
                )
            )
            continue
        weight = raw_label.get("weight")
        if isinstance(weight, bool) or not isinstance(weight, int | float) or weight <= 0 or weight > 1:
            issues.append(
                AssetImageTaggingValidationIssue(
                    issue=ISSUE_MODEL_LABEL_WEIGHT_INVALID,
                    label_snapshot_index=label_snapshot_index,
                    label_id=label_id,
                    message="model selected label weight must be a number in (0, 1]",
                    details={"item_id": item.item_id},
                )
            )
            continue
        reason = raw_label.get("reason")
        selected_label = label_by_id[label_id]
        selected.append(
            AssetImageTaggingSelectedLabel(
                label_id=selected_label.label_id,
                label_name=selected_label.label_name,
                definition=selected_label.definition,
                weight=float(weight),
                reason=reason.strip() if isinstance(reason, str) and reason.strip() else None,
            )
        )

    if group.selection_mode == "single" and len(selected) > 1:
        return [], issues + [
            AssetImageTaggingValidationIssue(
                issue=ISSUE_MODEL_SINGLE_SELECTION_INVALID,
                label_snapshot_index=label_snapshot_index,
                message="model selected more than one label for single selection group",
                details={"item_id": item.item_id, "label_count": len(selected)},
            )
        ]
    if group.selection_mode == "single" and len(selected) != 1:
        return [], issues + [
            AssetImageTaggingValidationIssue(
                issue=ISSUE_MODEL_SINGLE_SELECTION_INVALID,
                label_snapshot_index=label_snapshot_index,
                message="model must select exactly one label for single selection group",
                details={"item_id": item.item_id, "label_count": len(selected)},
            )
        ]
    return selected, issues


def _result_item_from_model(
    *,
    item: AssetImageTaggingItemParams,
    tagging_language: str,
    label_snapshot: list[AssetImageTaggingLabelSnapshotGroup],
    model_item: dict[str, Any],
) -> AssetImageTaggingResultItem:
    matching_groups = matching_label_groups(item, label_snapshot)
    if not matching_groups:
        return _failed_item(
            item,
            code=ITEM_LABEL_SNAPSHOT_NOT_FOUND,
            message="no matching label_snapshot group for item category",
            details={"item_id": item.item_id, "category_id": item.category_id},
        )

    selection_payloads, validation_issues = _selection_payload_by_index(item=item, model_item=model_item)
    allowed_label_snapshot_indexes = {label_snapshot_index for label_snapshot_index, _group in matching_groups}
    extra_label_snapshot_indexes = sorted(set(selection_payloads) - allowed_label_snapshot_indexes)
    for label_snapshot_index in extra_label_snapshot_indexes:
        validation_issues.append(
            AssetImageTaggingValidationIssue(
                issue=ISSUE_MODEL_RESPONSE_INVALID,
                label_snapshot_index=label_snapshot_index,
                message="model returned label group outside item category",
                details={"item_id": item.item_id, "category_id": item.category_id},
            )
        )
    asset_description, description_issues = _description_from_model(
        item=item,
        tagging_language=tagging_language,
        model_item=model_item,
    )
    validation_issues.extend(description_issues)

    selections: list[AssetImageTaggingLabelGroupSelection] = []
    for label_snapshot_index, group in matching_groups:
        selection_payload = selection_payloads.get(label_snapshot_index)
        if selection_payload is None:
            validation_issues.append(
                AssetImageTaggingValidationIssue(
                    issue=ISSUE_MODEL_RESPONSE_INVALID,
                    label_snapshot_index=label_snapshot_index,
                    message="model omitted matching label group",
                    details={"item_id": item.item_id},
                )
            )
            continue
        selected_labels, issues = _selected_labels_from_model(
            item=item,
            group=group,
            label_snapshot_index=label_snapshot_index,
            selection_payload=selection_payload,
        )
        validation_issues.extend(issues)
        if selected_labels:
            selections.append(
                AssetImageTaggingLabelGroupSelection(
                    label_snapshot_index=label_snapshot_index,
                    category_id=group.category_id,
                    category_name=group.category_name,
                    selection_mode=group.selection_mode,
                    labels=selected_labels,
                )
            )

    if not any(selection.labels for selection in selections):
        return _failed_item(
            item,
            code=ITEM_NO_LABEL_SELECTED,
            message="no labels were selected for item",
            details={"item_id": item.item_id, "category_id": item.category_id},
            validation_issues=validation_issues,
        )

    return AssetImageTaggingResultItem(
        item_id=item.item_id,
        item_name=item.item_name,
        category_id=item.category_id,
        category_name=item.category_name,
        asset=item.asset,
        status="partial_success" if validation_issues else "succeeded",
        label_group_selections=selections,
        asset_description=asset_description,
        validation_issues=validation_issues,
        error=None,
    )


def build_result_items_from_model_payload(
    *,
    params: AssetImageTaggingParams,
    payload: dict[str, Any],
) -> list[AssetImageTaggingResultItem]:
    model_items = _index_model_items(payload)
    expected_item_ids = {item.item_id for item in params.items}
    extra_item_ids = sorted(set(model_items) - expected_item_ids)
    if extra_item_ids:
        raise _invalid_output(
            "asset_image_tagging model output returned unknown item_id",
            {"item_ids": extra_item_ids},
        )
    result_items: list[AssetImageTaggingResultItem] = []
    for item in params.items:
        model_item = model_items.get(item.item_id)
        if model_item is None:
            result_items.append(
                _failed_item(
                    item,
                    code=ITEM_MODEL_RESPONSE_INVALID,
                    message="model output did not contain this item_id",
                    details={"item_id": item.item_id},
                )
            )
            continue
        try:
            result_items.append(
                _result_item_from_model(
                    item=item,
                    tagging_language=params.tagging_language,
                    label_snapshot=params.label_snapshot,
                    model_item=model_item,
                )
            )
        except ValidationError as exc:
            raise _invalid_output(
                "asset_image_tagging model output does not match result schema",
                {"item_id": item.item_id, "errors": exc.errors(include_url=False)},
            ) from exc
    return result_items


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


class AssetImageTaggingModelAdapter(Protocol):
    async def tag(self, params: AssetImageTaggingParams) -> list[AssetImageTaggingResultItem]:
        """Return model-backed labels for a batch of assets."""


class OpenAIResponsesAssetImageTaggingModelAdapter:
    adapter_name = "openai_responses"

    def __init__(self, *, api_key: str, base_url: str | None, model_id: str, timeout_seconds: int) -> None:
        self.api_key = api_key
        self.base_url = base_url or None
        self.model_id = model_id
        self.timeout_seconds = timeout_seconds

    @classmethod
    def from_settings(cls) -> "OpenAIResponsesAssetImageTaggingModelAdapter":
        return cls(
            api_key=settings.ai_provider.openai_api_key_value,
            base_url=settings.ai_provider.openai_base_url,
            model_id=settings.job.asset_image_tagging.model_id,
            timeout_seconds=settings.ai_provider.model_call_timeout_seconds,
        )

    async def tag(self, params: AssetImageTaggingParams) -> list[AssetImageTaggingResultItem]:
        if not self.api_key:
            raise AppError(
                "MODEL_CALL_FAILED",
                "OPENAI_API_KEY is required for asset_image_tagging",
                details={"model_adapter": self.adapter_name, "model_id": self.model_id},
            )

        result_items: list[AssetImageTaggingResultItem] = []
        for start in range(0, len(params.items), _MODEL_BATCH_ITEM_COUNT):
            batch_items = params.items[start : start + _MODEL_BATCH_ITEM_COUNT]
            result_items.extend(await self._tag_batch(_model_batch_params(params=params, items=batch_items)))
        return result_items

    async def _tag_batch(self, params: AssetImageTaggingParams) -> list[AssetImageTaggingResultItem]:
        item_ids = [item.item_id for item in params.items]
        try:
            response = await _client(
                api_key=self.api_key,
                timeout_seconds=self.timeout_seconds,
                api_base=self.base_url,
            ).responses.create(
                model=self.model_id,
                instructions=_SYSTEM_PROMPT,
                input=[{"role": "user", "content": _build_responses_content(params)}],
            )
        except APITimeoutError as exc:
            raise AppError(
                "MODEL_CALL_TIMEOUT",
                "asset_image_tagging OpenAI model call timeout",
                details={
                    "model_adapter": self.adapter_name,
                    "model_id": self.model_id,
                    "base_url": self.base_url,
                    "item_ids": item_ids,
                },
            ) from exc
        except APIStatusError as exc:
            raise AppError(
                "MODEL_CALL_FAILED",
                "asset_image_tagging OpenAI model call failed",
                details=_provider_error_details(
                    exc,
                    model_adapter=self.adapter_name,
                    model_id=self.model_id,
                    base_url=self.base_url or "",
                    item_ids=item_ids,
                ),
            ) from exc
        except Exception as exc:
            raise AppError(
                "MODEL_CALL_FAILED",
                "asset_image_tagging OpenAI model call failed",
                details={
                    "model_adapter": self.adapter_name,
                    "model_id": self.model_id,
                    "base_url": self.base_url,
                    "item_ids": item_ids,
                    "error_type": exc.__class__.__name__,
                    "error_message": str(exc)[:300],
                },
            ) from exc

        parsed = extract_json_object(_model_output_text(response))
        if parsed is None:
            raise _invalid_output("asset_image_tagging model output must be a JSON object")
        return build_result_items_from_model_payload(params=params, payload=parsed)


_MODEL_ADAPTER_FACTORIES: dict[str, Callable[[], AssetImageTaggingModelAdapter]] = {
    OpenAIResponsesAssetImageTaggingModelAdapter.adapter_name: OpenAIResponsesAssetImageTaggingModelAdapter.from_settings,
}


def asset_image_tagging_model_adapter_from_settings() -> AssetImageTaggingModelAdapter:
    adapter_name = settings.job.asset_image_tagging.model_adapter
    factory = _MODEL_ADAPTER_FACTORIES.get(adapter_name)
    if factory is None:
        raise AppError(
            "MODEL_NOT_AVAILABLE",
            "asset_image_tagging model adapter is not supported",
            details={
                "model_adapter": adapter_name,
                "supported_model_adapters": sorted(_MODEL_ADAPTER_FACTORIES),
            },
        )
    return factory()
