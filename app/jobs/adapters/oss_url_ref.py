from __future__ import annotations

from collections.abc import Collection, Mapping
from typing import Any
from urllib.parse import quote, unquote, urlsplit

from app.core.exceptions import AppError
from app.integrations.object_storage import CanonicalObjectRef, bare_sha256
from app.jobs.adapters.cpp_oss_url_ref import (
    canonical_ref_from_cpp_oss_url_ref,
    cpp_oss_url_ref_from_output_object,
)


def canonical_ref_from_oss_url_ref(
    payload: Mapping[str, Any],
    *,
    allowed_buckets: Collection[str] | None = None,
    allowed_regions: Collection[str] | None = None,
    allowed_content_types: Collection[str] | None = None,
    public_endpoint: str | None = None,
    public_endpoint_bucket: str | None = None,
    public_endpoint_region: str | None = None,
) -> CanonicalObjectRef:
    normalized_public_endpoint = normalize_public_endpoint(public_endpoint)
    if normalized_public_endpoint and _url_host(_required_str(payload, "public_url")) == normalized_public_endpoint:
        return _canonical_ref_from_public_endpoint_url_ref(
            payload,
            public_endpoint=normalized_public_endpoint,
            bucket=public_endpoint_bucket,
            region=public_endpoint_region,
            allowed_buckets=allowed_buckets,
            allowed_regions=allowed_regions,
            allowed_content_types=allowed_content_types,
        )
    return canonical_ref_from_cpp_oss_url_ref(
        payload,
        allowed_buckets=allowed_buckets,
        allowed_regions=allowed_regions,
        allowed_content_types=allowed_content_types,
    )


def oss_url_ref_from_output_object(
    *,
    bucket: str,
    region: str,
    key: str,
    content_type: str,
    content_hash: str,
    public_endpoint: str | None = None,
) -> dict[str, str]:
    normalized_public_endpoint = normalize_public_endpoint(public_endpoint)
    if not normalized_public_endpoint:
        return cpp_oss_url_ref_from_output_object(
            bucket=bucket,
            region=region,
            key=key,
            content_type=content_type,
            content_hash=content_hash,
        )

    encoded_key = quote(key.lstrip("/"), safe="/")
    return {
        "public_url": f"https://{normalized_public_endpoint}/{encoded_key}",
        "internal_url": f"https://{bucket}.oss-{region}-internal.aliyuncs.com/{encoded_key}",
        "content_type": content_type,
        "sha256": bare_sha256(content_hash),
    }


def normalize_public_endpoint(value: str | None) -> str:
    return (value or "").strip().removeprefix("https://").removeprefix("http://").strip("/").lower()


def _canonical_ref_from_public_endpoint_url_ref(
    payload: Mapping[str, Any],
    *,
    public_endpoint: str,
    bucket: str | None,
    region: str | None,
    allowed_buckets: Collection[str] | None,
    allowed_regions: Collection[str] | None,
    allowed_content_types: Collection[str] | None,
) -> CanonicalObjectRef:
    public_url = _required_str(payload, "public_url")
    _required_str(payload, "internal_url")
    content_type = _required_str(payload, "content_type")
    sha256 = _required_bare_sha256(payload)

    public_key = _parse_public_endpoint_key(public_url, public_endpoint=public_endpoint)
    bucket = _required_setting("OSS bucket", bucket)
    region = _required_setting("OSS region", region)
    _validate_allowed("OSS bucket", bucket, allowed_buckets)
    _validate_allowed("OSS region", region, allowed_regions)
    _validate_allowed("content_type", content_type, allowed_content_types)

    return CanonicalObjectRef(
        provider="aliyun_oss",
        bucket=bucket,
        region=region,
        key=public_key,
        content_type=content_type,
        content_hash=f"sha256:{sha256}",
    )


def _parse_public_endpoint_key(url: str, *, public_endpoint: str) -> str:
    parsed = urlsplit(url.strip())
    if parsed.scheme != "https":
        raise AppError("INVALID_INPUT", "OSS URL must use https")
    if parsed.fragment:
        raise AppError("INVALID_INPUT", "OSS URL must not contain fragment")
    if parsed.username or parsed.password or parsed.port is not None:
        raise AppError("INVALID_INPUT", "OSS URL must not contain credentials or port")
    if (parsed.hostname or "").lower() != public_endpoint:
        raise AppError("INVALID_INPUT", "public_url host does not match OSS public endpoint")
    key = unquote(parsed.path.lstrip("/"))
    if not key:
        raise AppError("INVALID_INPUT", "OSS URL object key is missing")
    if any(part == ".." for part in key.split("/")):
        raise AppError("INVALID_INPUT", "OSS URL object key contains illegal path traversal")
    return key


def _required_setting(label: str, value: str | None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AppError("INVALID_INPUT", f"{label} is required for public endpoint refs")
    return value.strip()


def _url_host(url: str) -> str:
    parsed = urlsplit(url.strip())
    if parsed.username or parsed.password or parsed.port is not None:
        raise AppError("INVALID_INPUT", "OSS URL must not contain credentials or port")
    return (parsed.hostname or "").lower()


def _required_str(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise AppError("INVALID_INPUT", f"{key} is required")
    return value.strip()


def _required_bare_sha256(payload: Mapping[str, Any]) -> str:
    value = _required_str(payload, "sha256")
    if value.startswith("sha256:"):
        raise AppError("INVALID_INPUT", "sha256 must be 64 lowercase hex characters")
    return bare_sha256(value)


def _validate_allowed(label: str, value: str, allowed: Collection[str] | None) -> None:
    if allowed is None:
        return
    if value not in set(allowed):
        raise AppError("INVALID_INPUT", f"{label} is not allowed")
