from __future__ import annotations

from collections.abc import Collection, Mapping
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

from app.core.exceptions import AppError
from app.integrations.object_storage.aliyun_url import AliyunOSSObjectLocation, parse_aliyun_oss_url
from app.integrations.object_storage import CanonicalObjectRef, bare_sha256


def canonical_ref_from_cpp_oss_url_ref(
    payload: Mapping[str, Any],
    *,
    allowed_buckets: Collection[str] | None = None,
    allowed_regions: Collection[str] | None = None,
    allowed_content_types: Collection[str] | None = None,
) -> CanonicalObjectRef:
    public_url = _required_str(payload, "public_url")
    _required_str(payload, "internal_url")
    content_type = _required_str(payload, "content_type")
    sha256 = _required_bare_sha256(payload)

    public_location = _parse_public_url_ref(public_url)
    if public_location.internal:
        raise AppError("INVALID_INPUT", "public_url must use a public OSS endpoint")
    _validate_allowed("OSS bucket", public_location.bucket, allowed_buckets)
    _validate_allowed("OSS region", public_location.region, allowed_regions)
    _validate_allowed("content_type", content_type, allowed_content_types)

    return CanonicalObjectRef(
        provider="aliyun_oss",
        bucket=public_location.bucket,
        region=public_location.region,
        key=public_location.key,
        content_type=content_type,
        content_hash=f"sha256:{sha256}",
    )


def cpp_oss_url_ref_from_canonical(
    ref: CanonicalObjectRef,
    *,
    public_url: str,
    internal_url: str,
) -> dict[str, str]:
    if ref.provider != "aliyun_oss":
        raise AppError("INVALID_INPUT", "canonical object ref provider must be aliyun_oss")
    public_location = parse_aliyun_oss_url(public_url)
    internal_location = parse_aliyun_oss_url(internal_url)
    expected_identity = (ref.bucket, ref.region, ref.key)
    if public_location.internal:
        raise AppError("INVALID_INPUT", "public_url must use a public OSS endpoint")
    if not internal_location.internal:
        raise AppError("INVALID_INPUT", "internal_url must use an internal OSS endpoint")
    if public_location.object_identity != expected_identity or internal_location.object_identity != expected_identity:
        raise AppError("INVALID_INPUT", "OSS URLs must reference the canonical object")
    if ref.content_type is None:
        raise AppError("INVALID_INPUT", "canonical object ref requires content_type for CPP projection")
    if ref.content_hash is None:
        raise AppError("INVALID_INPUT", "canonical object ref requires content_hash for CPP projection")
    return {
        "public_url": public_url,
        "internal_url": internal_url,
        "content_type": ref.content_type,
        "sha256": bare_sha256(ref.content_hash),
    }


def cpp_oss_url_ref_from_output_object(
    *,
    bucket: str,
    region: str,
    key: str,
    content_type: str,
    content_hash: str,
) -> dict[str, str]:
    ref = CanonicalObjectRef(
        provider="aliyun_oss",
        bucket=bucket,
        region=region,
        key=key,
        content_type=content_type,
        content_hash=content_hash,
    )
    encoded_key = quote(key.lstrip("/"), safe="/")
    public_endpoint = f"{bucket}.oss-{region}.aliyuncs.com"
    internal_endpoint = f"{bucket}.oss-{region}-internal.aliyuncs.com"
    return cpp_oss_url_ref_from_canonical(
        ref,
        public_url=f"https://{public_endpoint}/{encoded_key}",
        internal_url=f"https://{internal_endpoint}/{encoded_key}",
    )


def _required_str(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise AppError("INVALID_INPUT", f"{key} is required")
    return value.strip()


def _parse_public_url_ref(url: str) -> AliyunOSSObjectLocation:
    parsed = urlsplit(url.strip())
    if parsed.fragment:
        raise AppError("INVALID_INPUT", "OSS URL must not contain fragment")
    unsigned_url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    return parse_aliyun_oss_url(unsigned_url)


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
