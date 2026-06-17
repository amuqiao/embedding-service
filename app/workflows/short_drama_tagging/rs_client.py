from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urljoin

import httpx

from app.core.config import ROOT_DIR, settings
from app.core.exceptions import AppError

logger = logging.getLogger(__name__)

RS_COMPAT_TAG_SCHEMA_VERSION = "v1.1"


class TagSchemaProvider(Protocol):
    async def fetch(self, language: str) -> dict[str, Any]: ...


class TaggingResultWriter(Protocol):
    async def write(self, payload: dict[str, Any]) -> dict[str, Any]: ...


def rs_runtime_fields_from_settings() -> dict[str, Any]:
    return {
        "rs_schema_mock_enabled": settings.SHORT_DRAMA_RS_SCHEMA_MOCK_ENABLED,
        "rs_result_mock_enabled": settings.SHORT_DRAMA_RS_RESULT_MOCK_ENABLED,
        "rs_base_url": settings.SHORT_DRAMA_RS_BASE_URL,
        "rs_timeout_seconds": settings.SHORT_DRAMA_RS_TIMEOUT_SECONDS,
        "rs_schema_mock_path": settings.SHORT_DRAMA_RS_SCHEMA_MOCK_PATH,
        "rs_result_response_mock_path": settings.SHORT_DRAMA_RS_RESULT_RESPONSE_MOCK_PATH,
        "rs_tag_schema_version": RS_COMPAT_TAG_SCHEMA_VERSION,
    }


def _runtime_bool(runtime_fields: dict[str, Any], key: str) -> bool:
    value = runtime_fields.get(key)
    if not isinstance(value, bool):
        raise AppError("RUNTIME_REF_INVALID", f"runtime field {key} must be boolean", status_code=500)
    return value


def _runtime_str(runtime_fields: dict[str, Any], key: str) -> str:
    value = runtime_fields.get(key)
    if not isinstance(value, str) or not value.strip():
        raise AppError("RUNTIME_REF_INVALID", f"runtime field {key} must be a non-empty string", status_code=500)
    return value.strip()


def _runtime_optional_str(runtime_fields: dict[str, Any], key: str) -> str:
    value = runtime_fields.get(key, "")
    if not isinstance(value, str):
        raise AppError("RUNTIME_REF_INVALID", f"runtime field {key} must be a string", status_code=500)
    return value.strip()


def _runtime_positive_int(runtime_fields: dict[str, Any], key: str) -> int:
    value = runtime_fields.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise AppError("RUNTIME_REF_INVALID", f"runtime field {key} must be a positive integer", status_code=500)
    return value


def tag_schema_version_from_runtime(runtime_fields: dict[str, Any]) -> str:
    return _runtime_str(runtime_fields, "rs_tag_schema_version")


def _resolve_path(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = ROOT_DIR / path
    return path


def _load_json_file(
    path: Path,
    *,
    unavailable_code: str = "TAG_SCHEMA_UNAVAILABLE",
    invalid_code: str = "TAG_SCHEMA_INVALID",
    missing_message: str = "mock JSON file not found",
    invalid_message: str = "mock JSON file is not valid JSON",
) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise AppError(unavailable_code, f"{missing_message}: {path}", status_code=500) from exc
    except json.JSONDecodeError as exc:
        raise AppError(invalid_code, f"{invalid_message}: {path}", status_code=500) from exc


def _mock_schema_path_matches_language(path: Path, language: str) -> bool:
    return path.name == f"{language}.json" or f".{language}." in path.name


def _resolve_mock_schema_path(path_template: str, language: str) -> Path:
    if "{lang}" in path_template:
        return _resolve_path(path_template.replace("{lang}", language))
    path = _resolve_path(path_template)
    if not _mock_schema_path_matches_language(path, language):
        raise AppError(
            "TAG_SCHEMA_UNAVAILABLE",
            "mock tag schema path does not match requested language",
            status_code=500,
            details={"requested_language": language, "path": str(path), "expected_token": "{lang}"},
        )
    return path


def schema_mock_path_for_language(path_template: str, language: str) -> Path:
    return _resolve_mock_schema_path(path_template, language)


def assert_schema_mock_available(path_template: str, language: str) -> Path:
    path = schema_mock_path_for_language(path_template, language)
    if not path.is_file():
        raise AppError(
            "TAG_SCHEMA_UNAVAILABLE",
            "mock tag schema not found for requested language",
            status_code=500,
            details={"requested_language": language, "path": str(path)},
        )
    return path


def schema_fixture_path_for_language(path_template: str, language: str) -> Path:
    return schema_mock_path_for_language(path_template, language)


def assert_schema_fixture_available(path_template: str, language: str) -> Path:
    return assert_schema_mock_available(path_template, language)


def assert_result_response_mock_available(path: str) -> Path:
    resolved = _resolve_path(path)
    if not resolved.is_file():
        raise AppError(
            "RS_RESULT_WRITE_FAILED",
            "mock RS write response not found",
            status_code=500,
            details={"path": str(resolved)},
        )
    return resolved


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


class MockTagSchemaProvider:
    def __init__(self, path_template: str):
        self.path_template = path_template

    async def fetch(self, language: str) -> dict[str, Any]:
        path = assert_schema_mock_available(self.path_template, language)
        payload = _load_json_file(path)
        bundle = normalize_tag_schema_response(payload)
        bundle["source"] = {
            "type": "mock",
            "path": str(path),
            "requested_language": language,
        }
        logger.info("rs_tag_schema_mock_loaded language=%s path=%s", language, path)
        return bundle


FixtureTagSchemaProvider = MockTagSchemaProvider


class HttpTagSchemaProvider:
    def __init__(self, base_url: str, timeout_seconds: int):
        self.base_url = base_url.rstrip("/") + "/"
        self.timeout_seconds = timeout_seconds

    async def fetch(self, language: str) -> dict[str, Any]:
        headers = {
            "Accept": "application/json",
            "X-AI-Service-Caller-ID": "ai",
        }
        url = urljoin(self.base_url, "api/v1/tag-schemas/default")
        logger.info("rs_tag_schema_request method=GET url=%s language=%s", url, language)
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
        logger.info("rs_tag_schema_response status_code=%d language=%s", response.status_code, language)
        return bundle


class MockTaggingResultWriter:
    def __init__(self, response_path: str):
        self.response_path = _resolve_path(response_path)

    async def write(self, payload: dict[str, Any]) -> dict[str, Any]:
        response = _load_json_file(
            self.response_path,
            unavailable_code="RS_RESULT_WRITE_FAILED",
            invalid_code="RS_RESULT_WRITE_FAILED",
            missing_message="mock RS write response not found",
            invalid_message="mock RS write response is not valid JSON",
        )
        if not isinstance(response, dict):
            raise AppError("RS_RESULT_WRITE_FAILED", "mock RS write response must be an object", status_code=500)
        assert_rs_write_accepted(response)
        logger.info(
            "rs_result_write_mocked t_book_id=%s job_id=%s status=%s msg=%s response_code=%s response_msg=%s",
            payload.get("t_book_id"),
            payload.get("job_id"),
            payload.get("status"),
            payload.get("msg"),
            response.get("code"),
            response.get("msg"),
        )
        return response


FixtureTaggingResultWriter = MockTaggingResultWriter


class HttpTaggingResultWriter:
    def __init__(self, base_url: str, timeout_seconds: int):
        self.base_url = base_url.rstrip("/") + "/"
        self.timeout_seconds = timeout_seconds

    async def write(self, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {
            "X-AI-Service-Caller-ID": "ai",
            "Content-Type": "application/json",
        }
        url = urljoin(self.base_url, "api/v1/ai-tag-results")
        logger.info(
            "rs_result_write_request method=POST url=%s t_book_id=%s job_id=%s status=%s msg=%s",
            url,
            payload.get("t_book_id"),
            payload.get("job_id"),
            payload.get("status"),
            payload.get("msg"),
        )
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
        logger.info(
            "rs_result_write_response status_code=%d t_book_id=%s job_id=%s response_code=%s response_msg=%s",
            response.status_code,
            payload.get("t_book_id"),
            payload.get("job_id"),
            body.get("code"),
            body.get("msg"),
        )
        return body


def get_tag_schema_provider(runtime_fields: dict[str, Any] | None = None) -> TagSchemaProvider:
    fields = runtime_fields or rs_runtime_fields_from_settings()
    if _runtime_bool(fields, "rs_schema_mock_enabled"):
        return MockTagSchemaProvider(_runtime_str(fields, "rs_schema_mock_path"))
    base_url = _runtime_optional_str(fields, "rs_base_url")
    if not base_url:
        raise AppError("RUNTIME_REF_INVALID", "runtime field rs_base_url is required when schema mock is disabled", status_code=500)
    return HttpTagSchemaProvider(
        base_url,
        _runtime_positive_int(fields, "rs_timeout_seconds"),
    )


def get_tagging_result_writer(runtime_fields: dict[str, Any] | None = None) -> TaggingResultWriter:
    fields = runtime_fields or rs_runtime_fields_from_settings()
    if _runtime_bool(fields, "rs_result_mock_enabled"):
        return MockTaggingResultWriter(_runtime_str(fields, "rs_result_response_mock_path"))
    base_url = _runtime_optional_str(fields, "rs_base_url")
    if not base_url:
        raise AppError("RUNTIME_REF_INVALID", "runtime field rs_base_url is required when result mock is disabled", status_code=500)
    return HttpTaggingResultWriter(
        base_url,
        _runtime_positive_int(fields, "rs_timeout_seconds"),
    )
