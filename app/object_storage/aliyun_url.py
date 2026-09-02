from __future__ import annotations

import urllib.parse
from dataclasses import dataclass

from app.core.exceptions import AppError


@dataclass(frozen=True)
class AliyunOSSObjectLocation:
    bucket: str
    region: str
    key: str
    internal: bool
    endpoint: str

    @property
    def object_identity(self) -> tuple[str, str, str]:
        return self.bucket, self.region, self.key


def parse_aliyun_oss_url(url: str) -> AliyunOSSObjectLocation:
    if not isinstance(url, str) or not url.strip():
        raise AppError("INVALID_INPUT", "OSS URL must be a non-empty string")
    parsed = urllib.parse.urlsplit(url.strip())
    if parsed.scheme != "https":
        raise AppError("INVALID_INPUT", "OSS URL must use https")
    if parsed.query or parsed.fragment:
        raise AppError("INVALID_INPUT", "OSS URL must not contain query string or fragment")
    if parsed.username or parsed.password or parsed.port is not None:
        raise AppError("INVALID_INPUT", "OSS URL must not contain credentials or port")
    host = (parsed.hostname or "").lower()
    suffix = ".aliyuncs.com"
    marker = ".oss-"
    if not host.endswith(suffix) or marker not in host:
        raise AppError("INVALID_INPUT", "OSS URL host is not an Aliyun OSS virtual-host endpoint")
    bucket, endpoint_part = host.split(marker, 1)
    if not bucket or not endpoint_part.endswith(suffix.removeprefix(".")):
        raise AppError("INVALID_INPUT", "OSS URL host is invalid")
    region_part = endpoint_part.removesuffix(suffix.removeprefix(".")).rstrip(".")
    internal = False
    if region_part.endswith("-internal"):
        internal = True
        region_part = region_part.removesuffix("-internal")
    if not region_part:
        raise AppError("INVALID_INPUT", "OSS URL region is missing")
    key = urllib.parse.unquote(parsed.path.lstrip("/"))
    if not key:
        raise AppError("INVALID_INPUT", "OSS URL object key is missing")
    if any(part == ".." for part in key.split("/")):
        raise AppError("INVALID_INPUT", "OSS URL object key contains illegal path traversal")
    return AliyunOSSObjectLocation(
        bucket=bucket,
        region=region_part,
        key=key,
        internal=internal,
        endpoint=f"oss-{region_part}{'-internal' if internal else ''}.aliyuncs.com",
    )
