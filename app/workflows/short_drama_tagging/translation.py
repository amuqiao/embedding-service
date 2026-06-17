from __future__ import annotations

import copy
import json
from typing import Any

from app.core.exceptions import AppError
from app.services.job_runtime import payload_hash
from app.workflows.short_drama_tagging.prompts import parse_model_json

_TRANSLATABLE_CATEGORY_KEYS = {"name"}
_TRANSLATABLE_LABEL_KEYS = {"name", "definition"}


def translation_messages(params: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {
            "role": "user",
            "content": f"""You are a tag schema translation system.

Translate category names, label names, and label definitions from source_language={params['source_language']} to target_languages={params['target_languages']}.
Preserve category_id, required, min_items, max_items, label_id, label_key, label counts, category order, and label order.
Do not translate source_mutual_exclusion_rules.
Return JSON only with one key: translated_schemas. The value must be an array whose order matches target_languages.

Source schema:
{json.dumps(params['source_schema'], ensure_ascii=False, indent=2)}
""",
        }
    ]


def validate_translated_schemas(params: dict[str, Any], model_output: dict[str, Any]) -> list[dict[str, Any]]:
    translated_schemas = model_output.get("translated_schemas")
    if not isinstance(translated_schemas, list):
        raise AppError("TRANSLATION_FAILED", "model output missing translated_schemas array", status_code=502)
    if len(translated_schemas) != len(params["target_languages"]):
        raise AppError(
            "TRANSLATION_FAILED",
            "translated_schemas length does not match target_languages",
            status_code=502,
            details={"expected": len(params["target_languages"]), "actual": len(translated_schemas)},
        )
    source_categories = params["source_schema"]["categories"]
    for schema_index, translated_schema in enumerate(translated_schemas):
        if not isinstance(translated_schema, dict):
            raise AppError("TRANSLATION_FAILED", "translated schema must be an object", status_code=502)
        target_language = params["target_languages"][schema_index]
        unexpected_top_keys = set(translated_schema) - {"language", "categories"}
        if unexpected_top_keys:
            raise AppError(
                "TRANSLATION_FAILED",
                "translated schema contains unexpected top-level keys",
                status_code=502,
                details={"keys": sorted(unexpected_top_keys)},
            )
        if "language" in translated_schema and translated_schema["language"] != target_language:
            raise AppError("TRANSLATION_FAILED", "translated schema language does not match target language", status_code=502)
        categories = translated_schema.get("categories")
        if not isinstance(categories, list) or len(categories) != len(source_categories):
            raise AppError("TRANSLATION_FAILED", "translated schema category shape mismatch", status_code=502)
        for category_index, source_category in enumerate(source_categories):
            category = categories[category_index]
            if not isinstance(category, dict):
                raise AppError("TRANSLATION_FAILED", "translated category must be an object", status_code=502)
            if set(category) != set(source_category):
                raise AppError("TRANSLATION_FAILED", "translated category keys changed", status_code=502)
            if category.get("category_id") != source_category["category_id"]:
                raise AppError("TRANSLATION_FAILED", "translated category_id changed", status_code=502)
            for key, value in source_category.items():
                if key in _TRANSLATABLE_CATEGORY_KEYS or key == "labels":
                    continue
                if category.get(key) != value:
                    raise AppError("TRANSLATION_FAILED", f"translated category field changed: {key}", status_code=502)
            labels = category.get("labels")
            if not isinstance(labels, list) or len(labels) != len(source_category["labels"]):
                raise AppError("TRANSLATION_FAILED", "translated label shape mismatch", status_code=502)
            for label_index, source_label in enumerate(source_category["labels"]):
                label = labels[label_index]
                if not isinstance(label, dict):
                    raise AppError("TRANSLATION_FAILED", "translated label must be an object", status_code=502)
                if set(label) != set(source_label):
                    raise AppError("TRANSLATION_FAILED", "translated label keys changed", status_code=502)
                if label.get("label_id") != source_label["label_id"]:
                    raise AppError("TRANSLATION_FAILED", "translated label_id changed", status_code=502)
                for key, value in source_label.items():
                    if key in _TRANSLATABLE_LABEL_KEYS:
                        continue
                    if label.get(key) != value:
                        raise AppError("TRANSLATION_FAILED", f"translated label field changed: {key}", status_code=502)
    return translated_schemas


def build_translation_result(params: dict[str, Any], translated_schemas: list[dict[str, Any]]) -> dict[str, Any]:
    mutual_rules = copy.deepcopy(params["source_mutual_exclusion_rules"])
    return {
        "artifacts": [
            {
                "key": "translated_schemas",
                "type": "json",
                "label": "翻译后的标签结构体",
                "content": translated_schemas,
            },
            {
                "key": "mutual_exclusion_rules",
                "type": "json",
                "label": "互斥标签结构体",
                "content": mutual_rules,
            },
        ],
        "signals": {
            "source_schema_hash": payload_hash(params["source_schema"]),
            "translated_schemas_hash": payload_hash({"translated_schemas": translated_schemas}),
        },
    }


def parse_translation_output(text: str, params: dict[str, Any]) -> dict[str, Any]:
    model_output = parse_model_json(text, "tag_schema_translation")
    translated_schemas = validate_translated_schemas(params, model_output)
    return build_translation_result(params, translated_schemas)
