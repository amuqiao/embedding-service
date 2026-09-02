from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, unquote, urlsplit, urlunsplit

from app.core.exceptions import AppError
from app.object_storage.aliyun_url import AliyunOSSObjectLocation, parse_aliyun_oss_url
from app.object_storage import bare_sha256


@dataclass(frozen=True)
class CanonicalObjectRef:
    provider: str
    bucket: str
    region: str
    key: str
    content_type: str | None = None
    content_hash: str | None = None

    def __post_init__(self) -> None:
        for field_name in ("provider", "bucket", "region", "key"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise AppError("INVALID_INPUT", f"object ref requires {field_name}")
        if self.content_type is not None and not self.content_type.strip():
            raise AppError("INVALID_INPUT", "object ref content_type must not be empty")
        if self.content_hash is not None:
            object.__setattr__(self, "content_hash", f"sha256:{bare_sha256(self.content_hash)}")


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


def _parse_public_url_ref(url: str) -> AliyunOSSObjectLocation:
    parsed = urlsplit(url.strip())
    if parsed.query or parsed.fragment:
        raise AppError("INVALID_INPUT", "OSS URL must not contain query string or fragment")
    unsigned_url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    return parse_aliyun_oss_url(unsigned_url)


def _parse_public_endpoint_key(url: str, *, public_endpoint: str) -> str:
    parsed = urlsplit(url.strip())
    if parsed.scheme != "https":
        raise AppError("INVALID_INPUT", "OSS URL must use https")
    if parsed.query or parsed.fragment:
        raise AppError("INVALID_INPUT", "OSS URL must not contain query string or fragment")
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
