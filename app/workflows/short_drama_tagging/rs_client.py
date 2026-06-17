from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urljoin

import httpx

from app.core.config import ROOT_DIR, settings
from app.core.exceptions import AppError


class TagSchemaProvider(Protocol):
    async def fetch(self, language: str) -> dict[str, Any]: ...


class TaggingResultWriter(Protocol):
    async def write(self, payload: dict[str, Any]) -> dict[str, Any]: ...


def _resolve_path(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = ROOT_DIR / path
    return path


def _load_json_file(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise AppError("TAG_SCHEMA_UNAVAILABLE", f"mock fixture not found: {path}", status_code=500) from exc
    except json.JSONDecodeError as exc:
        raise AppError("TAG_SCHEMA_INVALID", f"mock fixture is not valid JSON: {path}", status_code=500) from exc


def _fixture_path_matches_language(path: Path, language: str) -> bool:
    return path.name == f"{language}.json" or f".{language}." in path.name


def _resolve_fixture_path(path_template: str, language: str) -> Path:
    if "{lang}" in path_template:
        return _resolve_path(path_template.replace("{lang}", language))
    path = _resolve_path(path_template)
    if not _fixture_path_matches_language(path, language):
        raise AppError(
            "TAG_SCHEMA_UNAVAILABLE",
            "mock tag schema fixture path does not match requested language",
            status_code=500,
            details={"requested_language": language, "path": str(path), "expected_token": "{lang}"},
        )
    return path


def schema_fixture_path_for_language(path_template: str, language: str) -> Path:
    return _resolve_fixture_path(path_template, language)


def assert_schema_fixture_available(path_template: str, language: str) -> Path:
    path = schema_fixture_path_for_language(path_template, language)
    if not path.is_file():
        raise AppError(
            "TAG_SCHEMA_UNAVAILABLE",
            "mock tag schema fixture not found for requested language",
            status_code=500,
            details={"requested_language": language, "path": str(path)},
        )
    return path


def assert_rs_write_accepted(response: dict[str, Any]) -> None:
    if not isinstance(response, dict):
        raise AppError("RS_RESULT_WRITE_FAILED", "RS write response must be an object", status_code=502)
    if response.get("code") not in (0, "0"):
        raise AppError(
            "RS_RESULT_WRITE_FAILED",
            "RS rejected AI tagging result",
            status_code=502,
            details={"rs_response": response},
        )
    if not isinstance(response.get("msg"), str):
        raise AppError("RS_RESULT_WRITE_FAILED", "RS write response missing msg", status_code=502)
    if not isinstance(response.get("data"), dict):
        raise AppError("RS_RESULT_WRITE_FAILED", "RS write response missing data object", status_code=502)


def _require_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AppError("TAG_SCHEMA_INVALID", f"{label} must be an object", status_code=422)
    return value


def _require_array(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise AppError("TAG_SCHEMA_INVALID", f"{label} must be an array", status_code=422)
    return value


def normalize_tag_schema_response(payload: dict[str, Any]) -> dict[str, Any]:
    payload = _require_object(payload, "tag schema response")
    categories = _require_array(payload.get("categories"), "tag schema categories")
    mutual_exclusion_rules = _require_array(
        payload.get("mutual_exclusion_rules", []),
        "tag schema mutual_exclusion_rules",
    )
    tag_schema_snapshot = {
        key: value
        for key, value in payload.items()
        if key != "mutual_exclusion_rules"
    }
    tag_schema_snapshot["categories"] = categories
    bundle = {
        "tag_schema_snapshot": tag_schema_snapshot,
        "mutual_exclusion_rules": mutual_exclusion_rules,
    }
    validate_tag_schema_bundle(bundle)
    return bundle


def validate_tag_schema_bundle(bundle: dict[str, Any]) -> None:
    schema = _require_object(bundle.get("tag_schema_snapshot"), "tag_schema_snapshot")
    categories = _require_array(schema.get("categories"), "tag_schema_snapshot.categories")
    seen_label_ids: set[str] = set()
    for category_index, category in enumerate(categories):
        category_obj = _require_object(category, f"categories[{category_index}]")
        for key in ("category_id", "name", "required", "min_items", "max_items", "labels"):
            if key not in category_obj:
                raise AppError("TAG_SCHEMA_INVALID", f"category missing {key}", status_code=422)
        labels = _require_array(category_obj["labels"], f"categories[{category_index}].labels")
        for label_index, label in enumerate(labels):
            label_obj = _require_object(label, f"categories[{category_index}].labels[{label_index}]")
            for key in ("label_id", "name", "definition"):
                if not isinstance(label_obj.get(key), str) or not label_obj[key].strip():
                    raise AppError("TAG_SCHEMA_INVALID", f"label missing {key}", status_code=422)
            label_id = label_obj["label_id"]
            if label_id in seen_label_ids:
                raise AppError("TAG_SCHEMA_INVALID", f"duplicate label_id: {label_id}", status_code=422)
            seen_label_ids.add(label_id)
    for rule_index, rule in enumerate(_require_array(bundle.get("mutual_exclusion_rules"), "mutual_exclusion_rules")):
        rule_obj = _require_object(rule, f"mutual_exclusion_rules[{rule_index}]")
        if not isinstance(rule_obj.get("label_id"), str) or not rule_obj["label_id"].strip():
            raise AppError("TAG_SCHEMA_INVALID", "mutex rule missing label_id", status_code=422)
        if rule_obj["label_id"] not in seen_label_ids:
            raise AppError("TAG_SCHEMA_INVALID", "mutex rule references unknown label_id", status_code=422)
        for mutex_label_id in _require_array(
            rule_obj.get("mutex_label_ids"),
            f"mutual_exclusion_rules[{rule_index}].mutex_label_ids",
        ):
            if not isinstance(mutex_label_id, str) or not mutex_label_id.strip():
                raise AppError("TAG_SCHEMA_INVALID", "mutex rule contains invalid mutex_label_id", status_code=422)
            if mutex_label_id not in seen_label_ids:
                raise AppError("TAG_SCHEMA_INVALID", "mutex rule references unknown mutex_label_id", status_code=422)


class FixtureTagSchemaProvider:
    def __init__(self, path_template: str):
        self.path_template = path_template

    async def fetch(self, language: str) -> dict[str, Any]:
        path = assert_schema_fixture_available(self.path_template, language)
        payload = _load_json_file(path)
        bundle = normalize_tag_schema_response(payload)
        bundle["source"] = {
            "type": "fixture",
            "path": str(path),
            "requested_language": language,
        }
        return bundle


class HttpTagSchemaProvider:
    def __init__(self, base_url: str, api_key: str, timeout_seconds: int):
        self.base_url = base_url.rstrip("/") + "/"
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    async def fetch(self, language: str) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "X-AI-Service-Caller-ID": "ai",
        }
        url = urljoin(self.base_url, "api/v1/tag-schemas/default")
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            try:
                response = await client.get(url, params={"lang": language}, headers=headers)
            except httpx.HTTPError as exc:
                raise AppError("TAG_SCHEMA_UNAVAILABLE", "RS tag schema request failed", status_code=502) from exc
        if response.status_code >= 400:
            raise AppError(
                "TAG_SCHEMA_UNAVAILABLE",
                "RS tag schema request returned an error",
                status_code=502,
                details={"status_code": response.status_code, "body": response.text[:500]},
            )
        try:
            body = response.json()
        except json.JSONDecodeError as exc:
            raise AppError("TAG_SCHEMA_INVALID", "RS tag schema response is not valid JSON", status_code=502) from exc
        bundle = normalize_tag_schema_response(body)
        bundle["source"] = {"type": "http", "requested_language": language}
        return bundle


class FixtureTaggingResultWriter:
    def __init__(self, response_path: str):
        self.response_path = _resolve_path(response_path)

    async def write(self, payload: dict[str, Any]) -> dict[str, Any]:
        response = _load_json_file(self.response_path)
        if not isinstance(response, dict):
            raise AppError("RS_RESULT_WRITE_FAILED", "mock RS write response must be an object", status_code=500)
        assert_rs_write_accepted(response)
        return response


class HttpTaggingResultWriter:
    def __init__(self, base_url: str, api_key: str, timeout_seconds: int):
        self.base_url = base_url.rstrip("/") + "/"
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    async def write(self, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "X-AI-Service-Caller-ID": "ai",
            "Content-Type": "application/json",
        }
        url = urljoin(self.base_url, "api/v1/ai-tag-results")
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            try:
                response = await client.post(url, json=payload, headers=headers)
            except httpx.HTTPError as exc:
                raise AppError("RS_RESULT_WRITE_FAILED", "RS result write request failed", status_code=502) from exc
        if response.status_code >= 400:
            raise AppError(
                "RS_RESULT_WRITE_FAILED",
                "RS result write returned an HTTP error",
                status_code=502,
                details={"status_code": response.status_code, "body": response.text[:500]},
            )
        try:
            body = response.json()
        except json.JSONDecodeError as exc:
            raise AppError("RS_RESULT_WRITE_FAILED", "RS write response is not valid JSON", status_code=502) from exc
        if not isinstance(body, dict):
            raise AppError("RS_RESULT_WRITE_FAILED", "RS write response must be an object", status_code=502)
        assert_rs_write_accepted(body)
        return body


def get_tag_schema_provider() -> TagSchemaProvider:
    if settings.SHORT_DRAMA_RS_SCHEMA_SOURCE == "fixture":
        return FixtureTagSchemaProvider(settings.SHORT_DRAMA_RS_SCHEMA_FIXTURE_PATH)
    return HttpTagSchemaProvider(
        settings.SHORT_DRAMA_RS_BASE_URL,
        settings.SHORT_DRAMA_RS_API_KEY,
        settings.SHORT_DRAMA_RS_TIMEOUT_SECONDS,
    )


def get_tagging_result_writer() -> TaggingResultWriter:
    if settings.SHORT_DRAMA_RS_RESULT_SINK == "fixture":
        return FixtureTaggingResultWriter(settings.SHORT_DRAMA_RS_RESULT_RESPONSE_FIXTURE_PATH)
    return HttpTaggingResultWriter(
        settings.SHORT_DRAMA_RS_BASE_URL,
        settings.SHORT_DRAMA_RS_API_KEY,
        settings.SHORT_DRAMA_RS_TIMEOUT_SECONDS,
    )
