from __future__ import annotations

import json
from typing import Any

from app.core.exceptions import AppError
from app.services.job_runtime import payload_hash
from app.workflows.short_drama_tagging.prompts import parse_model_json
from app.workflows.short_drama_tagging.schemas import TagSchemaTranslationResult


def translation_messages(params: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {
            "role": "user",
            "content": f"""You are a short-drama tag label translation system.

Translate each label's display_name and definition from its source_language to its target_languages.
Preserve label_id and label order. Do not add, remove, merge, split, or rewrite label_id.
Return JSON only with exactly one key: artifacts.
artifacts must be an array whose order matches input labels.
Each artifact must be:
{{
  "label_id": "...",
  "langs": {{
    "<target_language>": {{"name": "...", "definition": "..."}}
  }}
}}
The langs object must contain exactly the requested target_languages for that label.

Labels:
{json.dumps(params['labels'], ensure_ascii=False, indent=2)}
""",
        }
    ]


def _require_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AppError("TRANSLATION_FAILED", f"{label} must be an object", status_code=502)
    return value


def _require_non_empty_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AppError("TRANSLATION_FAILED", f"{label} must be a non-empty string", status_code=502)
    return value


def validate_translation_artifacts(params: dict[str, Any], model_output: dict[str, Any]) -> list[dict[str, Any]]:
    artifacts = model_output.get("artifacts")
    if not isinstance(artifacts, list):
        raise AppError("TRANSLATION_FAILED", "model output missing artifacts array", status_code=502)
    labels = params["labels"]
    if len(artifacts) != len(labels):
        raise AppError(
            "TRANSLATION_FAILED",
            "artifacts length does not match labels",
            status_code=502,
            details={"expected": len(labels), "actual": len(artifacts)},
        )
    validated: list[dict[str, Any]] = []
    for index, source_label in enumerate(labels):
        artifact = _require_object(artifacts[index], f"artifacts[{index}]")
        if set(artifact) != {"label_id", "langs"}:
            raise AppError(
                "TRANSLATION_FAILED",
                "translation artifact keys changed",
                status_code=502,
                details={"index": index, "keys": sorted(artifact)},
            )
        if artifact["label_id"] != source_label["label_id"]:
            raise AppError(
                "TRANSLATION_FAILED",
                "translated label_id changed",
                status_code=502,
                details={"index": index, "expected": source_label["label_id"], "actual": artifact["label_id"]},
            )
        langs = _require_object(artifact["langs"], f"artifacts[{index}].langs")
        expected_languages = source_label["target_languages"]
        actual_languages = list(langs)
        if set(actual_languages) != set(expected_languages):
            raise AppError(
                "TRANSLATION_FAILED",
                "translation languages do not match target_languages",
                status_code=502,
                details={
                    "label_id": source_label["label_id"],
                    "expected": expected_languages,
                    "actual": actual_languages,
                },
            )
        validated_langs: dict[str, dict[str, str]] = {}
        for language in expected_languages:
            value = _require_object(langs[language], f"artifacts[{index}].langs.{language}")
            if set(value) != {"name", "definition"}:
                raise AppError(
                    "TRANSLATION_FAILED",
                    "translation language entry keys changed",
                    status_code=502,
                    details={"label_id": source_label["label_id"], "language": language, "keys": sorted(value)},
                )
            validated_langs[language] = {
                "name": _require_non_empty_string(value["name"], f"artifacts[{index}].langs.{language}.name"),
                "definition": _require_non_empty_string(
                    value["definition"],
                    f"artifacts[{index}].langs.{language}.definition",
                ),
            }
        validated.append({"label_id": source_label["label_id"], "langs": validated_langs})
    return validated


def build_translation_result(params: dict[str, Any], artifacts: list[dict[str, Any]]) -> dict[str, Any]:
    return TagSchemaTranslationResult.model_validate({
        "artifacts": artifacts,
        "signals": {
            "source_schema_hash": payload_hash({"labels": params["labels"]}),
            "translated_schemas_hash": payload_hash({"artifacts": artifacts}),
        },
    }).model_dump()


def parse_translation_output(text: str, params: dict[str, Any]) -> dict[str, Any]:
    model_output = parse_model_json(text, "tag_schema_translation")
    artifacts = validate_translation_artifacts(params, model_output)
    return build_translation_result(params, artifacts)
