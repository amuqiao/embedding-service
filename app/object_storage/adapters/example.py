"""Minimal project adapter example.

Copy this file to a project-specific name, then replace the field mapping with
the shape used by that project. The common package does not import adapters.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..refs import build_object_ref, build_storage_object


def input_ref_from_business_payload(
    payload: Mapping[str, Any],
    *,
    presigned_url: str,
) -> dict[str, Any]:
    """Convert one business object payload into the common input ref shape."""
    oss = _required_mapping(payload, "oss")
    size_bytes = payload.get("size_bytes")
    sha256 = _optional_str(payload.get("sha256"))
    return build_object_ref(
        obj=build_storage_object(
            provider=_required_str(oss, "provider"),
            bucket=_required_str(oss, "bucket"),
            region=_required_str(oss, "region"),
            key=_required_str(oss, "key"),
            content_type=_required_str(payload, "content_type"),
            size_bytes=int(size_bytes) if size_bytes is not None else None,
            sha256=sha256,
            public_url=_optional_str(payload.get("download_url")),
        ),
        presigned_url=presigned_url,
    )


def business_payload_from_result_ref(result_ref: Mapping[str, Any]) -> dict[str, Any]:
    """Convert a written storage object result back to a project response shape."""
    obj = _required_mapping(result_ref, "object")
    return {
        "download_url": _optional_str(obj.get("public_url")),
        "oss": {
            "provider": _required_str(obj, "provider"),
            "bucket": _required_str(obj, "bucket"),
            "region": _required_str(obj, "region"),
            "key": _required_str(obj, "key"),
        },
        "content_type": _required_str(obj, "content_type"),
        "size_bytes": int(obj["size_bytes"]),
        "sha256": _required_str(obj, "sha256"),
    }


def _required_mapping(payload: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = payload.get(key)
    if not isinstance(value, Mapping):
        raise ValueError(f"{key} must be an object")
    return value


def _required_str(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value.strip()


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError("optional string field must be non-empty when provided")
    return value.strip()
