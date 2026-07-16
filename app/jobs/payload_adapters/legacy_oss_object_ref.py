from __future__ import annotations

import re
from collections.abc import Collection, Mapping
from typing import Any

from app.core.exceptions import AppError
from app.integrations.object_storage import CanonicalObjectRef, ObjectWriteResult, normalize_content_hash

_PREFIXED_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_LEGACY_TEXT_CONTENT_TYPE = "text/plain; charset=utf-8"
_APPLY_MODES = {"replace", "append"}


def canonical_ref_from_legacy_source_oss(
    payload: Mapping[str, Any],
    *,
    bucket: str,
    region: str,
    provider: str = "aliyun_oss",
    allowed_content_types: Collection[str] | None = None,
) -> CanonicalObjectRef:
    """Project cms-novel-localize source.oss payload into the canonical object ref."""
    _required_str(payload, "oss_url")
    key = _legacy_oss_key(_required_str(payload, "oss_key"))
    content_type = _legacy_text_content_type(_required_str(payload, "content_type"))
    _validate_allowed("content_type", content_type, allowed_content_types)

    return CanonicalObjectRef(
        provider=_required_setting("provider", provider),
        bucket=_required_setting("bucket", bucket),
        region=_required_setting("region", region),
        key=key,
        content_type=content_type,
        content_hash=_optional_prefixed_sha256(payload.get("content_hash")),
    )


def legacy_oss_artifact_from_output_object(
    output: ObjectWriteResult | Mapping[str, Any],
    *,
    key: str,
    type: str,
    label: str,
    apply_mode: str | None = None,
) -> dict[str, Any]:
    """Project a written object result into cms-novel-localize oss_object artifact shape."""
    output_payload = output.to_legacy_dict() if isinstance(output, ObjectWriteResult) else output
    artifact: dict[str, Any] = {
        "key": _required_str({"key": key}, "key"),
        "type": _required_str({"type": type}, "type"),
        "label": _required_str({"label": label}, "label"),
        "storage": "oss_object",
        "oss_bucket": _required_str(output_payload, "oss_bucket"),
        "oss_key": _legacy_oss_key(_required_str(output_payload, "oss_key")),
        "oss_region": _required_str(output_payload, "oss_region"),
        "content_hash": _required_prefixed_sha256(output_payload.get("content_hash")),
        "content_size_bytes": _required_non_negative_int(output_payload, "content_size_bytes"),
    }
    if apply_mode is not None:
        artifact["apply_mode"] = _legacy_apply_mode(apply_mode)
    return artifact


def _legacy_oss_key(value: str) -> str:
    key = value.strip().strip("/")
    if not key:
        raise AppError("INVALID_INPUT", "oss_key must be a non-empty OSS object key")
    return key


def _legacy_apply_mode(value: str) -> str:
    candidate = _required_str({"apply_mode": value}, "apply_mode")
    if candidate not in _APPLY_MODES:
        raise AppError("INVALID_INPUT", "apply_mode must be replace or append")
    return candidate


def _legacy_text_content_type(value: str) -> str:
    normalized = value.strip().lower()
    parts = [part.strip() for part in normalized.split(";")]
    if parts[0] != "text/plain":
        raise AppError("INVALID_INPUT", "content_type must be text/plain; charset=utf-8")
    params = {part for part in parts[1:] if part}
    if "charset=utf-8" not in params:
        raise AppError("INVALID_INPUT", "content_type must be text/plain; charset=utf-8")
    return _LEGACY_TEXT_CONTENT_TYPE


def _optional_prefixed_sha256(value: Any) -> str | None:
    if value is None:
        return None
    return _required_prefixed_sha256(value)


def _required_prefixed_sha256(value: Any) -> str:
    if not isinstance(value, str) or not _PREFIXED_SHA256_RE.fullmatch(value.strip()):
        raise AppError("INVALID_INPUT", "content_hash must match sha256:<64 lowercase hex>")
    return normalize_content_hash(value)


def _required_non_negative_int(payload: Mapping[str, Any], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise AppError("INVALID_INPUT", f"{key} must be a non-negative integer")
    return value


def _required_setting(label: str, value: str | None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AppError("INVALID_INPUT", f"{label} is required")
    return value.strip()


def _required_str(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise AppError("INVALID_INPUT", f"{key} is required")
    return value.strip()


def _validate_allowed(label: str, value: str, allowed: Collection[str] | None) -> None:
    if allowed is None:
        return
    if value not in set(allowed):
        raise AppError("INVALID_INPUT", f"{label} is not allowed")
