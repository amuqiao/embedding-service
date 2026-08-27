from __future__ import annotations

from collections.abc import Collection, Mapping
from typing import Any
from urllib.parse import quote, unquote, urlsplit

from ..aliyun_oss import normalize_public_base_url, parse_aliyun_oss_url
from ..contract import bare_sha256, validate_object_key
from ..refs import CanonicalObjectRef


def canonical_ref_from_oss_url_ref(
    payload: Mapping[str, Any],
    *,
    allowed_buckets: Collection[str] | None = None,
    allowed_regions: Collection[str] | None = None,
    allowed_content_types: Collection[str] | None = None,
    public_base_url: str | None = None,
    public_base_url_bucket: str | None = None,
    public_base_url_region: str | None = None,
) -> CanonicalObjectRef:
    public_url = _required_str(payload, "public_url")
    internal_url = _required_str(payload, "internal_url")
    content_type = _required_str(payload, "content_type")
    digest = _required_bare_sha256(payload)

    normalized_public_base_url = normalize_public_base_url(public_base_url)
    if normalized_public_base_url and _url_host(public_url) == _url_host(normalized_public_base_url):
        key = _parse_public_base_url_key(public_url, public_base_url=normalized_public_base_url)
        bucket = _required_setting("OSS bucket", public_base_url_bucket)
        region = _required_setting("OSS region", public_base_url_region)
        internal_location = parse_aliyun_oss_url(internal_url)
        if not internal_location.internal:
            raise ValueError("internal_url must use an internal OSS endpoint")
        if internal_location.object_identity != (bucket, region, key):
            raise ValueError("OSS URLs must reference the same object")
    else:
        location = parse_aliyun_oss_url(public_url)
        if location.internal:
            raise ValueError("public_url must use a public OSS endpoint")
        internal_location = parse_aliyun_oss_url(internal_url)
        if not internal_location.internal:
            raise ValueError("internal_url must use an internal OSS endpoint")
        if internal_location.object_identity != location.object_identity:
            raise ValueError("OSS URLs must reference the same object")
        bucket = location.bucket
        region = location.region
        key = location.key

    _validate_allowed("OSS bucket", bucket, allowed_buckets)
    _validate_allowed("OSS region", region, allowed_regions)
    _validate_allowed("content_type", content_type, allowed_content_types)
    return CanonicalObjectRef(
        provider="aliyun_oss",
        bucket=bucket,
        region=region,
        key=key,
        content_type=content_type,
        content_hash=f"sha256:{digest}",
    )


def oss_url_ref_from_output_object(
    *,
    bucket: str,
    region: str,
    key: str,
    content_type: str,
    content_hash: str,
    public_base_url: str | None = None,
) -> dict[str, str]:
    normalized_public_base_url = normalize_public_base_url(public_base_url)
    encoded_key = quote(key.lstrip("/"), safe="/")
    public_url = (
        f"{normalized_public_base_url}/{encoded_key}"
        if normalized_public_base_url
        else f"https://{bucket}.oss-{region}.aliyuncs.com/{encoded_key}"
    )
    return {
        "public_url": public_url,
        "internal_url": f"https://{bucket}.oss-{region}-internal.aliyuncs.com/{encoded_key}",
        "content_type": content_type,
        "sha256": bare_sha256(content_hash),
    }


def _parse_public_base_url_key(url: str, *, public_base_url: str) -> str:
    parsed = urlsplit(url.strip())
    if parsed.scheme != "https":
        raise ValueError("OSS URL must use https")
    if parsed.query or parsed.fragment:
        raise ValueError("OSS URL must not contain query string or fragment")
    if parsed.username or parsed.password or parsed.port is not None:
        raise ValueError("OSS URL must not contain credentials or port")
    base = urlsplit(public_base_url)
    if (parsed.hostname or "").lower() != (base.hostname or "").lower():
        raise ValueError("public_url host does not match configured public_base_url")
    key = unquote(parsed.path.lstrip("/"))
    base_prefix = unquote(base.path.strip("/"))
    if base_prefix:
        if key == base_prefix:
            raise ValueError("OSS URL object key is missing")
        if not key.startswith(f"{base_prefix}/"):
            raise ValueError("public_url path does not start with configured public_base_url path")
        key = key.removeprefix(f"{base_prefix}/")
    if not key:
        raise ValueError("OSS URL object key is missing")
    try:
        validate_object_key(key, field="OSS URL object key")
    except ValueError as exc:
        raise ValueError(str(exc)) from exc
    return key


def _required_setting(label: str, value: str | None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} is required for public endpoint refs")
    return value.strip()


def _url_host(url: str) -> str:
    parsed = urlsplit(url.strip())
    if parsed.username or parsed.password or parsed.port is not None:
        raise ValueError("OSS URL must not contain credentials or port")
    return (parsed.hostname or "").lower()


def _required_str(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} is required")
    return value.strip()


def _required_bare_sha256(payload: Mapping[str, Any]) -> str:
    value = _required_str(payload, "sha256")
    if value.startswith("sha256:"):
        raise ValueError("sha256 must be 64 lowercase hex characters")
    return bare_sha256(value)


def _validate_allowed(label: str, value: str, allowed: Collection[str] | None) -> None:
    if allowed is not None and value not in set(allowed):
        raise ValueError(f"{label} is not allowed")
