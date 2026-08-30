from __future__ import annotations

from dataclasses import dataclass
from typing import Self

from .exceptions import ObjectStorageConfigError, ObjectStorageValidationError
from .factory import ObjectStorageConfig, build_repository
from .models import (
    ObjectMeta,
    ObjectReadSpec,
    ObjectRef,
    PublicUrlReadSpec,
    PutObjectResult,
    sha256_digest,
)
from .public_url import PublicUrlConfig, PublicUrlInputReader, PublicUrlReader
from .repository import ObjectStorageRepository


@dataclass(frozen=True)
class ObjectStorageAdapterContext:
    repository: ObjectStorageRepository

    def __post_init__(self) -> None:
        if not isinstance(self.repository, ObjectStorageRepository):
            raise ObjectStorageConfigError("repository must implement ObjectStorageRepository")

    @classmethod
    def from_config(
        cls,
        *,
        repository_config: ObjectStorageConfig,
    ) -> ObjectStorageAdapterContext:
        if not isinstance(repository_config, ObjectStorageConfig):
            raise ObjectStorageConfigError("repository_config must be ObjectStorageConfig")
        return cls(repository=build_repository(repository_config))


class BaseObjectStorageAdapter:
    def __init__(
        self,
        storage_context: ObjectStorageAdapterContext,
        *,
        public_url_reader: PublicUrlInputReader | None = None,
    ) -> None:
        if not isinstance(storage_context, ObjectStorageAdapterContext):
            raise ObjectStorageConfigError("storage_context must be ObjectStorageAdapterContext")
        if public_url_reader is not None and not isinstance(public_url_reader, PublicUrlInputReader):
            raise ObjectStorageConfigError("public_url_reader must implement PublicUrlInputReader")
        self._storage_context = storage_context
        self._public_url_reader = public_url_reader

    @classmethod
    def from_config(
        cls,
        *,
        repository_config: ObjectStorageConfig,
        public_url_config: PublicUrlConfig | None = None,
    ) -> Self:
        if not isinstance(repository_config, ObjectStorageConfig):
            raise ObjectStorageConfigError("repository_config must be ObjectStorageConfig")
        public_url_reader = PublicUrlReader(public_url_config) if public_url_config is not None else None
        return cls(
            ObjectStorageAdapterContext.from_config(repository_config=repository_config),
            public_url_reader=public_url_reader,
        )

    @property
    def storage_context(self) -> ObjectStorageAdapterContext:
        return self._storage_context

    @property
    def repository(self) -> ObjectStorageRepository:
        return self._storage_context.repository

    def read_object(self, spec: ObjectReadSpec) -> bytes:
        if not isinstance(spec, ObjectReadSpec):
            raise ObjectStorageConfigError("spec must be ObjectReadSpec")
        if spec.policy.max_bytes is not None or spec.policy.verify_size_bytes:
            _precheck_object_meta(self.repository.head(spec.ref), spec=spec)
        data = self.repository.get_bytes(spec.ref)
        _verify_read_data(data, spec=spec, source="object")
        return data

    def read_public_url(self, spec: PublicUrlReadSpec) -> bytes:
        if not isinstance(spec, PublicUrlReadSpec):
            raise ObjectStorageConfigError("spec must be PublicUrlReadSpec")
        reader = self._public_url_reader
        if reader is None:
            raise ObjectStorageConfigError("public_url_reader is not configured")
        data = reader.read_public_url(spec)
        _verify_read_data(data, spec=spec, source="public_url")
        return data

    def write_object_bytes(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str,
        content_disposition: str | None = None,
    ) -> PutObjectResult:
        return self.repository.put_bytes(
            key,
            data,
            content_type=content_type,
            content_disposition=content_disposition,
        )

    def head_object(self, ref: ObjectRef) -> ObjectMeta:
        return self.repository.head(ref)

    def delete_object(self, ref: ObjectRef) -> None:
        self.repository.delete(ref)


def _precheck_object_meta(meta: ObjectMeta, *, spec: ObjectReadSpec) -> None:
    if spec.policy.max_bytes is not None and meta.size_bytes is not None and meta.size_bytes > spec.policy.max_bytes:
        raise ObjectStorageValidationError(f"object exceeds max_bytes={spec.policy.max_bytes}")
    if spec.policy.verify_size_bytes and meta.size_bytes is not None and meta.size_bytes != spec.integrity.size_bytes:
        raise ObjectStorageValidationError(
            f"object size_bytes mismatch: expected {spec.integrity.size_bytes}, got {meta.size_bytes}"
        )


def _verify_read_data(data: bytes, *, spec: ObjectReadSpec | PublicUrlReadSpec, source: str) -> None:
    if spec.policy.max_bytes is not None and len(data) > spec.policy.max_bytes:
        raise ObjectStorageValidationError(f"{source} exceeds max_bytes={spec.policy.max_bytes}")
    if spec.policy.verify_size_bytes and len(data) != spec.integrity.size_bytes:
        raise ObjectStorageValidationError(
            f"{source} size_bytes mismatch: expected {spec.integrity.size_bytes}, got {len(data)}"
        )
    if spec.policy.verify_sha256 and sha256_digest(data) != spec.integrity.sha256:
        raise ObjectStorageValidationError(f"{source} sha256 mismatch")
