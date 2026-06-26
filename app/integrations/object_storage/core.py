from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from app.core.exceptions import AppError

_BARE_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_PREFIXED_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


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
            object.__setattr__(self, "content_hash", normalize_content_hash(self.content_hash))


@dataclass(frozen=True)
class ObjectWriteResult:
    provider: str
    bucket: str
    region: str
    key: str
    content_type: str
    content_hash: str
    content_size_bytes: int

    def to_legacy_dict(self) -> dict[str, object]:
        return {
            "oss_bucket": self.bucket,
            "oss_key": self.key,
            "oss_region": self.region,
            "content_hash": self.content_hash,
            "content_size_bytes": self.content_size_bytes,
        }


def sha256_digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def bare_sha256(value: str) -> str:
    if not isinstance(value, str):
        raise AppError("INVALID_INPUT", "sha256 must be a string")
    candidate = value.strip()
    if _PREFIXED_SHA256_RE.fullmatch(candidate):
        return candidate.removeprefix("sha256:")
    if _BARE_SHA256_RE.fullmatch(candidate):
        return candidate
    raise AppError("INVALID_INPUT", "sha256 must be 64 lowercase hex characters")


def normalize_content_hash(value: str) -> str:
    return "sha256:" + bare_sha256(value)
