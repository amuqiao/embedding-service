from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .aliyun_oss import direct_public_url
from .contract import (
    bare_sha256,
    validate_non_empty_str,
    validate_object_key,
    validate_positive_int,
    validate_provider,
)


@dataclass(frozen=True, slots=True)
class CanonicalObjectRef:
    provider: str
    bucket: str
    region: str
    key: str
    content_type: str
    content_hash: str


def build_storage_object(
    *,
    provider: str = "aliyun_oss",
    bucket: str,
    region: str,
    key: str,
    content_type: str,
    size_bytes: int | None = None,
    sha256: str | None = None,
    public_url: str | None = None,
) -> dict[str, Any]:
    validate_provider(provider)
    validate_non_empty_str(bucket, field="bucket")
    validate_non_empty_str(region, field="region")
    validate_object_key(key, field="key")
    validate_non_empty_str(content_type, field="content_type")
    obj: dict[str, Any] = {
        "provider": provider,
        "bucket": bucket,
        "region": region,
        "key": key,
        "content_type": content_type,
    }
    if size_bytes is not None:
        obj["size_bytes"] = validate_positive_int(size_bytes, field="size_bytes")
    if sha256 is not None:
        obj["sha256"] = bare_sha256(sha256)
    if public_url:
        obj["public_url"] = public_url
    return obj


def build_access_ref(*, presigned_url: str) -> dict[str, str]:
    if not isinstance(presigned_url, str) or not presigned_url.strip():
        raise ValueError("presigned_url must be a non-empty string")
    return {"presigned_url": presigned_url.strip()}


def build_object_ref(*, obj: dict[str, Any], presigned_url: str) -> dict[str, Any]:
    return {
        "object": obj,
        "access": build_access_ref(presigned_url=presigned_url),
    }


def build_output_object_spec(
    *,
    bucket: str,
    region: str,
    key: str,
    content_type: str,
    presigned_url: str,
    public_base_url: str | None = None,
) -> dict[str, Any]:
    return build_object_ref(
        obj=build_storage_object(
            bucket=bucket,
            region=region,
            key=key,
            content_type=content_type,
            public_url=direct_public_url(
                bucket=bucket,
                region=region,
                key=key,
                public_base_url=public_base_url,
            ),
        ),
        presigned_url=presigned_url,
    )
