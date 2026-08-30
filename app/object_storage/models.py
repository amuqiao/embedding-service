from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from typing import Any

from .exceptions import ObjectStorageValidationError


@dataclass(frozen=True)
class ObjectRef:
    provider: str
    bucket: str
    region: str
    key: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider", _required_str(self.provider, "provider"))
        object.__setattr__(self, "bucket", _required_str(self.bucket, "bucket"))
        object.__setattr__(self, "region", _required_str(self.region, "region"))
        object.__setattr__(self, "key", normalize_object_key(self.key))

    def to_dict(self) -> dict[str, str]:
        return {
            "provider": self.provider,
            "bucket": self.bucket,
            "region": self.region,
            "key": self.key,
        }


@dataclass(frozen=True)
class ObjectMeta:
    provider: str
    bucket: str
    region: str
    key: str
    content_type: str | None = None
    size_bytes: int | None = None
    etag: str | None = None
    sha256: str | None = None

    def to_dict(self) -> dict[str, object]:
        data: dict[str, object] = {
            "provider": self.provider,
            "bucket": self.bucket,
            "region": self.region,
            "key": self.key,
        }
        if self.content_type is not None:
            data["content_type"] = self.content_type
        if self.size_bytes is not None:
            data["size_bytes"] = self.size_bytes
        if self.etag is not None:
            data["etag"] = self.etag
        if self.sha256 is not None:
            data["sha256"] = self.sha256
        return data


@dataclass(frozen=True)
class PutObjectResult:
    provider: str
    bucket: str
    region: str
    key: str
    content_type: str
    size_bytes: int
    sha256: str
    public_url: str | None = None

    def to_dict(self) -> dict[str, object]:
        data: dict[str, object] = {
            "provider": self.provider,
            "bucket": self.bucket,
            "region": self.region,
            "key": self.key,
            "content_type": self.content_type,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
        }
        if self.public_url is not None:
            data["public_url"] = self.public_url
        return data


@dataclass(frozen=True)
class ExpectedObjectIntegrity:
    size_bytes: int | None = None
    sha256: str | None = None

    def __post_init__(self) -> None:
        if self.size_bytes is not None:
            object.__setattr__(self, "size_bytes", _non_negative_int(self.size_bytes, "size_bytes"))
        if self.sha256 is not None:
            object.__setattr__(self, "sha256", bare_sha256(self.sha256))

    def to_dict(self) -> dict[str, object]:
        data: dict[str, object] = {}
        if self.size_bytes is not None:
            data["size_bytes"] = self.size_bytes
        if self.sha256 is not None:
            data["sha256"] = self.sha256
        return data


@dataclass(frozen=True)
class ObjectReadPolicy:
    verify_size_bytes: bool = False
    verify_sha256: bool = False
    max_bytes: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.verify_size_bytes, bool):
            raise ObjectStorageValidationError("verify_size_bytes must be a boolean")
        if not isinstance(self.verify_sha256, bool):
            raise ObjectStorageValidationError("verify_sha256 must be a boolean")
        if self.max_bytes is not None:
            object.__setattr__(self, "max_bytes", _positive_int(self.max_bytes, "max_bytes"))


@dataclass(frozen=True)
class ObjectReadSpec:
    ref: ObjectRef
    integrity: ExpectedObjectIntegrity = field(default_factory=ExpectedObjectIntegrity)
    policy: ObjectReadPolicy = field(default_factory=ObjectReadPolicy)

    def __post_init__(self) -> None:
        if not isinstance(self.ref, ObjectRef):
            raise ObjectStorageValidationError("ref must be ObjectRef")
        if not isinstance(self.integrity, ExpectedObjectIntegrity):
            raise ObjectStorageValidationError("integrity must be ExpectedObjectIntegrity")
        if not isinstance(self.policy, ObjectReadPolicy):
            raise ObjectStorageValidationError("policy must be ObjectReadPolicy")
        _validate_policy_has_integrity(self.integrity, self.policy)


@dataclass(frozen=True)
class PublicUrlReadSpec:
    url: str
    integrity: ExpectedObjectIntegrity = field(default_factory=ExpectedObjectIntegrity)
    policy: ObjectReadPolicy = field(default_factory=ObjectReadPolicy)

    def __post_init__(self) -> None:
        object.__setattr__(self, "url", _required_str(self.url, "url"))
        if not isinstance(self.integrity, ExpectedObjectIntegrity):
            raise ObjectStorageValidationError("integrity must be ExpectedObjectIntegrity")
        if not isinstance(self.policy, ObjectReadPolicy):
            raise ObjectStorageValidationError("policy must be ObjectReadPolicy")
        _validate_policy_has_integrity(self.integrity, self.policy)


def sha256_digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def bare_sha256(value: str) -> str:
    if not isinstance(value, str):
        raise ObjectStorageValidationError("sha256 must be a string")
    candidate = value.strip()
    if candidate.startswith("sha256:"):
        candidate = candidate.removeprefix("sha256:")
    if len(candidate) == 64 and all(char in "0123456789abcdef" for char in candidate):
        return candidate
    raise ObjectStorageValidationError("sha256 must be 64 lowercase hex characters")


def normalize_content_hash(value: str) -> str:
    return bare_sha256(value)


def normalize_object_key(value: str) -> str:
    key = _required_str(value, "key").strip("/")
    if "\\" in key:
        raise ObjectStorageValidationError("key must not contain backslash")
    parts = key.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ObjectStorageValidationError("key must be a canonical relative path")
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in key):
        raise ObjectStorageValidationError("key contains an invalid character")
    return key


def normalize_name(value: str, field: str) -> str:
    name = _required_str(value, field)
    if "/" in name or "\\" in name:
        raise ObjectStorageValidationError(f"{field} must not contain path separators")
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in name):
        raise ObjectStorageValidationError(f"{field} contains an invalid character")
    return name


def join_key(prefix: str, key: str) -> str:
    clean_key = normalize_object_key(key)
    clean_prefix = prefix.strip().strip("/")
    if not clean_prefix:
        return clean_key
    return f"{normalize_object_key(clean_prefix)}/{clean_key}"


def _required_str(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ObjectStorageValidationError(f"{field} must be a non-empty string")
    return value.strip()


def _positive_int(value: Any, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ObjectStorageValidationError(f"{field} must be a positive integer")
    return value


def _non_negative_int(value: Any, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ObjectStorageValidationError(f"{field} must be a non-negative integer")
    return value


def _validate_policy_has_integrity(integrity: ExpectedObjectIntegrity, policy: ObjectReadPolicy) -> None:
    if policy.verify_size_bytes and integrity.size_bytes is None:
        raise ObjectStorageValidationError("size_bytes is required when verify_size_bytes is true")
    if policy.verify_sha256 and integrity.sha256 is None:
        raise ObjectStorageValidationError("sha256 is required when verify_sha256 is true")
    if integrity.size_bytes is not None and policy.max_bytes is not None and integrity.size_bytes > policy.max_bytes:
        raise ObjectStorageValidationError("size_bytes must not exceed max_bytes")
