from __future__ import annotations

from dataclasses import dataclass
from os import PathLike
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

from ..exceptions import (
    ObjectStorageBackendError,
    ObjectStorageNotFoundError,
    ObjectStorageValidationError,
)
from ..models import ObjectMeta, ObjectRef, PutObjectResult, normalize_name, normalize_object_key, sha256_digest
from ..repository import ObjectStorageRepository


@dataclass(frozen=True)
class LocalStorageConfig:
    root: Path
    bucket: str = "local"
    region: str = "local"
    public_base_url: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "root", _root_path(self.root))
        object.__setattr__(self, "bucket", normalize_name(self.bucket, "bucket"))
        object.__setattr__(self, "region", normalize_name(self.region, "region"))
        if self.public_base_url:
            _validate_public_base_url(self.public_base_url)


class LocalObjectStorageRepository(ObjectStorageRepository):
    provider = "local"

    def __init__(self, config: LocalStorageConfig):
        self.config = config

    def get_bytes(self, ref: ObjectRef) -> bytes:
        self._assert_ref(ref)
        path = self._path(ref.key)
        if not path.exists():
            raise ObjectStorageNotFoundError(f"object not found: {ref.bucket}/{ref.key}")
        try:
            return path.read_bytes()
        except OSError as exc:
            raise ObjectStorageBackendError("failed to read object") from exc

    def put_bytes(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str,
        content_disposition: str | None = None,
    ) -> PutObjectResult:
        object_key = normalize_object_key(key)
        path = self._path(object_key)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        except OSError as exc:
            raise ObjectStorageBackendError("failed to write object") from exc
        return PutObjectResult(
            provider=self.provider,
            bucket=self.config.bucket,
            region=self.config.region,
            key=object_key,
            content_type=content_type,
            size_bytes=len(data),
            sha256=sha256_digest(data),
            public_url=self._public_url(object_key),
        )

    def head(self, ref: ObjectRef) -> ObjectMeta:
        self._assert_ref(ref)
        path = self._path(ref.key)
        if not path.exists():
            raise ObjectStorageNotFoundError(f"object not found: {ref.bucket}/{ref.key}")
        try:
            stat = path.stat()
        except OSError as exc:
            raise ObjectStorageBackendError("failed to stat object") from exc
        return ObjectMeta(
            provider=self.provider,
            bucket=self.config.bucket,
            region=self.config.region,
            key=ref.key,
            size_bytes=stat.st_size,
        )

    def delete(self, ref: ObjectRef) -> None:
        self._assert_ref(ref)
        path = self._path(ref.key)
        try:
            path.unlink()
        except FileNotFoundError as exc:
            raise ObjectStorageNotFoundError(f"object not found: {ref.bucket}/{ref.key}") from exc
        except OSError as exc:
            raise ObjectStorageBackendError("failed to delete object") from exc

    def _path(self, key: str) -> Path:
        bucket_root = (self.config.root / self.config.bucket).resolve()
        path = (bucket_root / normalize_object_key(key)).resolve()
        if path != bucket_root and bucket_root not in path.parents:
            raise ObjectStorageValidationError("key contains illegal path traversal")
        return path

    def _assert_ref(self, ref: ObjectRef) -> None:
        if ref.provider != self.provider:
            raise ObjectStorageValidationError("object ref provider does not match repository")
        if ref.bucket != self.config.bucket:
            raise ObjectStorageValidationError("object ref bucket does not match repository")
        if ref.region != self.config.region:
            raise ObjectStorageValidationError("object ref region does not match repository")

    def _public_url(self, key: str) -> str | None:
        base = self.config.public_base_url.strip().rstrip("/")
        if not base:
            return None
        return f"{base}/{quote(normalize_object_key(key), safe='/')}"


def _validate_public_base_url(value: str) -> None:
    parsed = urlsplit(value.strip().rstrip("/"))
    if parsed.scheme != "https" or not parsed.hostname:
        raise ObjectStorageValidationError("public_base_url must be an absolute HTTPS URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ObjectStorageValidationError("public_base_url must not contain credentials, query, or fragment")


def _root_path(value: Any) -> Path:
    if not isinstance(value, str | PathLike):
        raise ObjectStorageValidationError("root must be a path")
    if isinstance(value, str) and not value.strip():
        raise ObjectStorageValidationError("root must be a non-empty path")
    return Path(value)
