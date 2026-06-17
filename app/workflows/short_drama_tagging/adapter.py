from __future__ import annotations

from typing import Any

from app.core.exceptions import AppError


def _require_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AppError("MODEL_OUTPUT_INVALID", f"{label} must be an object", status_code=502)
    return value


def _selected_tags_from_model_output(final_result: dict[str, Any]) -> dict[str, Any]:
    if "selected_tags" in final_result:
        return _require_object(final_result["selected_tags"], "finalize.selected_tags")
    final_tags = _require_object(final_result.get("final_tags"), "finalize.final_tags")
    return _require_object(final_tags.get("tags"), "finalize.final_tags.tags")


def _schema_indexes(tag_schema: dict[str, Any]) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    categories = {category["category_id"]: category for category in tag_schema["categories"]}
    labels_by_name_by_category = {
        category_id: {label["name"]: label for label in category["labels"]}
        for category_id, category in categories.items()
    }
    return categories, labels_by_name_by_category


def _selected_label_name(item: dict[str, Any]) -> str:
    label_name = item.get("标签名", item.get("label_name", item.get("name")))
    if not isinstance(label_name, str) or not label_name.strip():
        raise AppError("MODEL_OUTPUT_INVALID", "selected tag missing label name", status_code=502)
    return label_name


def _selected_weight(item: dict[str, Any]) -> float:
    weight = item.get("权重", item.get("weight"))
    if isinstance(weight, bool) or not isinstance(weight, int | float) or weight <= 0 or weight > 1:
        raise AppError("MODEL_OUTPUT_INVALID", f"selected tag has invalid weight: {weight}", status_code=502)
    return float(weight)


def _selected_reason(item: dict[str, Any]) -> str:
    reason = item.get("打标原因", item.get("reason"))
    if not isinstance(reason, str) or not reason.strip():
        raise AppError("MODEL_OUTPUT_INVALID", "selected tag missing reason", status_code=502)
    return reason


def _validate_mutual_exclusions(
    *,
    selected_labels: list[dict[str, Any]],
    mutual_exclusion_rules: list[dict[str, Any]],
) -> None:
    selected_by_id = {label["label_id"]: label for label in selected_labels}
    conflicts: list[dict[str, Any]] = []
    seen_pairs: set[tuple[str, str]] = set()
    for rule_index, rule in enumerate(mutual_exclusion_rules):
        rule_obj = _require_object(rule, f"mutual_exclusion_rules[{rule_index}]")
        label_id = rule_obj.get("label_id")
        mutex_label_ids = rule_obj.get("mutex_label_ids")
        if not isinstance(label_id, str) or not label_id.strip():
            raise AppError("TAG_SCHEMA_INVALID", "mutual exclusion rule missing label_id", status_code=502)
        if not isinstance(mutex_label_ids, list):
            raise AppError("TAG_SCHEMA_INVALID", "mutual exclusion rule mutex_label_ids must be an array", status_code=502)
        if label_id not in selected_by_id:
            continue
        for mutex_label_id in mutex_label_ids:
            if not isinstance(mutex_label_id, str) or not mutex_label_id.strip():
                raise AppError("TAG_SCHEMA_INVALID", "mutual exclusion rule contains invalid mutex_label_id", status_code=502)
            if mutex_label_id not in selected_by_id:
                continue
            pair = tuple(sorted((label_id, mutex_label_id)))
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            label = selected_by_id[label_id]
            mutex_label = selected_by_id[mutex_label_id]
            conflicts.append(
                {
                    "label_id": label_id,
                    "label_name": label["name"],
                    "category_id": label["category_id"],
                    "category_name": label["category_name"],
                    "mutex_label_id": mutex_label_id,
                    "mutex_label_name": mutex_label["name"],
                    "mutex_category_id": mutex_label["category_id"],
                    "mutex_category_name": mutex_label["category_name"],
                }
            )
    if conflicts:
        raise AppError(
            "MODEL_OUTPUT_INVALID",
            "selected tags violate mutual exclusion rules",
            status_code=502,
            details={"conflicts": conflicts},
        )


def build_rs_tagging_payload(
    *,
    t_book_id: str,
    job_id: str,
    tag_schema: dict[str, Any],
    mutual_exclusion_rules: list[dict[str, Any]],
    final_result: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    selected_tags = _selected_tags_from_model_output(final_result)
    tagging_detail = _require_object(final_result.get("tagging_detail"), "finalize.tagging_detail")
    categories, labels_by_name_by_category = _schema_indexes(tag_schema)
    unknown_category_ids = sorted(set(selected_tags) - set(categories))
    if unknown_category_ids:
        raise AppError(
            "MODEL_OUTPUT_INVALID",
            "selected_tags contains unknown category_id",
            status_code=502,
            details={"category_ids": unknown_category_ids},
        )

    tags: dict[str, list[dict[str, Any]]] = {}
    validation_issues: list[dict[str, Any]] = []
    selected_labels: list[dict[str, Any]] = []
    for category_id, category in categories.items():
        items = selected_tags.get(category_id)
        if items is None:
            if category.get("required"):
                tags[category_id] = []
                validation_issues.append(
                    {
                        "category_id": category_id,
                        "category_name": category["name"],
                        "issue": "missing_required_category",
                        "min_items": category.get("min_items"),
                        "max_items": category.get("max_items"),
                        "actual_items": 0,
                        "message": f"{category['name']} is required but no tag was selected.",
                    }
                )
            continue
        if not isinstance(items, list):
            raise AppError("MODEL_OUTPUT_INVALID", f"selected_tags.{category_id} must be an array", status_code=502)
        min_items = category.get("min_items")
        max_items = category.get("max_items")
        if isinstance(min_items, int) and len(items) < min_items:
            validation_issues.append(
                {
                    "category_id": category_id,
                    "category_name": category["name"],
                    "issue": "below_min_items",
                    "min_items": min_items,
                    "max_items": max_items,
                    "actual_items": len(items),
                    "message": f"{category['name']} selected {len(items)} tags, below min_items {min_items}.",
                }
            )
        if isinstance(max_items, int) and len(items) > max_items:
            validation_issues.append(
                {
                    "category_id": category_id,
                    "category_name": category["name"],
                    "issue": "above_max_items",
                    "min_items": min_items,
                    "max_items": max_items,
                    "actual_items": len(items),
                    "message": f"{category['name']} selected {len(items)} tags, above max_items {max_items}.",
                }
            )
        labels_by_name = labels_by_name_by_category[category_id]
        tags[category_id] = []
        for item in items:
            item_obj = _require_object(item, f"selected_tags.{category_id}[]")
            label_name = _selected_label_name(item_obj)
            label = labels_by_name.get(label_name)
            if label is None:
                raise AppError(
                    "MODEL_OUTPUT_INVALID",
                    "selected tag label name is not in schema",
                    status_code=502,
                    details={"category_id": category_id, "label_name": label_name},
                )
            selected_labels.append(
                {
                    "label_id": label["label_id"],
                    "name": label["name"],
                    "category_id": category_id,
                    "category_name": category["name"],
                }
            )
            tags[category_id].append(
                {
                    "label_id": label["label_id"],
                    "name": label["name"],
                    "weight": _selected_weight(item_obj),
                    "reason": _selected_reason(item_obj),
                    "definition": label["definition"],
                }
            )

    _validate_mutual_exclusions(
        selected_labels=selected_labels,
        mutual_exclusion_rules=mutual_exclusion_rules,
    )
    result_status = "partial_success" if validation_issues else "success"
    tagging_detail["result_status"] = result_status
    tagging_detail["validation_issues"] = validation_issues
    return (
        {
            "t_book_id": t_book_id,
            "job_id": job_id,
            "tags": tags,
        },
        tagging_detail,
    )
