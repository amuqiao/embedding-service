from __future__ import annotations

from typing import Any, Collection


SUPPORTED_PROVIDERS = frozenset({"aliyun_oss"})
OBJECT_BASE_FIELDS = frozenset({"provider", "bucket", "region", "key", "content_type"})
OBJECT_SIZE_FIELDS = frozenset({"size_bytes"})
OBJECT_HASH_FIELDS = frozenset({"sha256"})
OBJECT_INTEGRITY_FIELDS = OBJECT_SIZE_FIELDS | OBJECT_HASH_FIELDS
OBJECT_DISPLAY_FIELDS = frozenset({"public_url"})


class ObjectStorageContractError(ValueError):
    pass


def validate_provider(value: Any, *, field: str = "provider") -> str:
    if not isinstance(value, str) or not value:
        raise ObjectStorageContractError(f"{field} must be a non-empty string")
    if value not in SUPPORTED_PROVIDERS:
        supported = ", ".join(sorted(SUPPORTED_PROVIDERS))
        raise ObjectStorageContractError(f"{field} must be one of: {supported}")
    return value


def validate_object_key(value: Any, *, field: str = "key") -> str:
    if not isinstance(value, str) or not value:
        raise ObjectStorageContractError(f"{field} must be a non-empty string")
    if value.startswith("/") or "\\" in value:
        raise ObjectStorageContractError(f"{field} must be relative")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ObjectStorageContractError(f"{field} must be canonical")
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        raise ObjectStorageContractError(f"{field} contains an invalid character")
    return value


def is_bare_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def bare_sha256(value: str) -> str:
    digest = value.strip().removeprefix("sha256:")
    if not is_bare_sha256(digest):
        raise ObjectStorageContractError("sha256 must be 64 lowercase hex characters")
    return digest


def validate_positive_int(value: Any, *, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ObjectStorageContractError(f"{field} must be a positive integer")
    return value


def validate_non_empty_str(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ObjectStorageContractError(f"{field} must be a non-empty string")
    return value


def validate_storage_object(
    value: Any,
    *,
    field: str,
    require_integrity: bool = False,
    require_size_bytes: bool | None = None,
    require_sha256: bool | None = None,
    allow_public_url: bool = False,
    allowed_content_types: Collection[str] | None = None,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ObjectStorageContractError(f"{field} must be an object")
    if require_size_bytes is None:
        require_size_bytes = require_integrity
    if require_sha256 is None:
        require_sha256 = require_integrity
    required = set(OBJECT_BASE_FIELDS)
    if require_size_bytes:
        required |= set(OBJECT_SIZE_FIELDS)
    if require_sha256:
        required |= set(OBJECT_HASH_FIELDS)
    missing = sorted(required - set(value))
    if missing:
        raise ObjectStorageContractError(f"{field} missing required keys: {', '.join(missing)}")
    allowed = OBJECT_BASE_FIELDS | OBJECT_INTEGRITY_FIELDS
    if allow_public_url:
        allowed |= OBJECT_DISPLAY_FIELDS
    unsupported = sorted(set(value) - allowed)
    if unsupported:
        raise ObjectStorageContractError(f"{field} contains unsupported keys: {', '.join(unsupported)}")

    validate_provider(value.get("provider"), field=f"{field}.provider")
    for key in ("bucket", "region", "content_type"):
        validate_non_empty_str(value.get(key), field=f"{field}.{key}")
    validate_object_key(value.get("key"), field=f"{field}.key")

    if allowed_content_types is not None and value.get("content_type") not in set(allowed_content_types):
        supported = ", ".join(sorted(allowed_content_types))
        raise ObjectStorageContractError(f"{field}.content_type must be one of: {supported}")
    if "size_bytes" in value:
        validate_positive_int(value.get("size_bytes"), field=f"{field}.size_bytes")
    if "sha256" in value and not is_bare_sha256(value.get("sha256")):
        raise ObjectStorageContractError(f"{field}.sha256 must be 64 lowercase hex characters")
    return value
