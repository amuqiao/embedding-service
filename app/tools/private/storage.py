from __future__ import annotations

from pathlib import Path
from typing import Protocol

from app.core.config import settings
from app.core.exceptions import AppError
from app.core.oss_endpoint import normalize_oss_endpoint
from app.object_storage import (
    AliyunOSSConfig,
    AliyunOSSRepository,
    LocalObjectStorageRepository,
    LocalStorageConfig,
    ObjectRef,
    ObjectStorageBackendError,
    ObjectStorageConfigError,
    ObjectStorageNotFoundError,
    ObjectStorageValidationError,
    PutObjectResult,
)
from app.tools.private.object_storage_refs import sha256_digest


class ObjectStorage(Protocol):
    def read_bytes(self, *, bucket: str, key: str, region: str) -> bytes: ...

    def write_bytes(
        self,
        *,
        bucket: str,
        key: str,
        region: str,
        data: bytes,
        content_type: str = "application/octet-stream",
        content_disposition: str | None = None,
    ) -> dict: ...

    def read_text(self, *, bucket: str, key: str, region: str) -> str: ...

    def write_text(self, *, bucket: str, key: str, region: str, content: str) -> dict: ...


class LocalObjectStorage:
    def __init__(self, root: Path):
        self.root = root

    def _repository(self, *, bucket: str, region: str) -> LocalObjectStorageRepository:
        return LocalObjectStorageRepository(
            LocalStorageConfig(
                root=self.root,
                bucket=bucket,
                region=region,
            )
        )

    def read_bytes(self, *, bucket: str, key: str, region: str) -> bytes:
        try:
            repository = self._repository(bucket=bucket, region=region)
            ref = ObjectRef(provider=repository.provider, bucket=bucket, region=region, key=key)
            return repository.get_bytes(ref)
        except ObjectStorageNotFoundError as exc:
            raise AppError(
                "OSS_OBJECT_NOT_FOUND",
                f"OSS object not found: {bucket}/{key}",
                details={"oss_bucket": bucket, "oss_key": key, "oss_region": region},
            ) from exc
        except ObjectStorageValidationError as exc:
            raise AppError("INVALID_INPUT", str(exc)) from exc
        except ObjectStorageBackendError as exc:
            raise AppError("OSS_FETCH_FAILED", "Failed to read OSS object") from exc

    def write_bytes(
        self,
        *,
        bucket: str,
        key: str,
        region: str,
        data: bytes,
        content_type: str = "application/octet-stream",
        content_disposition: str | None = None,
    ) -> dict:
        try:
            repository = self._repository(bucket=bucket, region=region)
            written = repository.put_bytes(
                key,
                data,
                content_type=content_type,
                content_disposition=content_disposition,
            )
        except ObjectStorageValidationError as exc:
            raise AppError("INVALID_INPUT", str(exc)) from exc
        except ObjectStorageBackendError as exc:
            raise AppError("OSS_WRITE_FAILED", "Failed to write OSS object") from exc
        return _legacy_write_result(written)

    def read_text(self, *, bucket: str, key: str, region: str) -> str:
        try:
            return self.read_bytes(bucket=bucket, key=key, region=region).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise AppError("INVALID_INPUT", "OSS object must be UTF-8 text") from exc

    def write_text(self, *, bucket: str, key: str, region: str, content: str) -> dict:
        return self.write_bytes(
            bucket=bucket,
            key=key,
            region=region,
            data=content.encode("utf-8"),
            content_type="text/plain; charset=utf-8",
        )


class AliyunObjectStorage:
    def __init__(self, repository: AliyunOSSRepository):
        self.repository = repository

    def _assert_target(self, *, bucket: str, region: str) -> None:
        if bucket != self.repository.config.bucket:
            raise AppError(
                "OSS_BUCKET_NOT_CONFIGURED",
                "OSS bucket does not match configured Aliyun OSS bucket",
                details={"oss_bucket": bucket, "configured_bucket": self.repository.config.bucket},
            )
        if region != self.repository.config.region:
            raise AppError(
                "OSS_REGION_NOT_CONFIGURED",
                "OSS region does not match configured Aliyun OSS region",
                details={"oss_region": region, "configured_region": self.repository.config.region},
            )

    def read_bytes(self, *, bucket: str, key: str, region: str) -> bytes:
        self._assert_target(bucket=bucket, region=region)
        try:
            return self.repository.get_bytes(
                ObjectRef(provider=self.repository.provider, bucket=bucket, region=region, key=key)
            )
        except ObjectStorageNotFoundError as exc:
            raise AppError(
                "OSS_OBJECT_NOT_FOUND",
                "OSS object not found",
                details={"oss_bucket": bucket, "oss_key": key, "oss_region": region},
            ) from exc
        except ObjectStorageValidationError as exc:
            raise AppError("INVALID_INPUT", str(exc)) from exc
        except ObjectStorageBackendError as exc:
            raise AppError(
                "OSS_FETCH_FAILED",
                "Failed to read OSS object",
                details={"oss_bucket": bucket, "oss_key": key, "oss_region": region},
            ) from exc

    def write_bytes(
        self,
        *,
        bucket: str,
        key: str,
        region: str,
        data: bytes,
        content_type: str = "application/octet-stream",
        content_disposition: str | None = None,
    ) -> dict:
        self._assert_target(bucket=bucket, region=region)
        try:
            written = self.repository.put_bytes(
                key,
                data,
                content_type=content_type,
                content_disposition=content_disposition,
            )
        except ObjectStorageValidationError as exc:
            raise AppError("INVALID_INPUT", str(exc)) from exc
        except ObjectStorageBackendError as exc:
            raise AppError(
                "OSS_WRITE_FAILED",
                "Failed to write OSS object",
                details={"oss_bucket": bucket, "oss_key": key, "oss_region": region},
            ) from exc
        return _legacy_write_result(written)

    def read_text(self, *, bucket: str, key: str, region: str) -> str:
        data = self.read_bytes(bucket=bucket, key=key, region=region)
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise AppError("INVALID_INPUT", "OSS object must be UTF-8 text") from exc

    def write_text(self, *, bucket: str, key: str, region: str, content: str) -> dict:
        return self.write_bytes(
            bucket=bucket,
            key=key,
            region=region,
            data=content.encode("utf-8"),
            content_type="text/plain; charset=utf-8",
        )


def _legacy_write_result(written: PutObjectResult) -> dict[str, object]:
    return {
        "oss_bucket": written.bucket,
        "oss_key": written.key,
        "oss_region": written.region,
        "content_hash": f"sha256:{written.sha256}",
        "content_size_bytes": written.size_bytes,
    }


def _public_base_url(value: str) -> str:
    endpoint = normalize_oss_endpoint(value)
    if not endpoint:
        return ""
    return f"https://{endpoint}"


def _build_storage() -> ObjectStorage:
    if settings.storage.backend == "local":
        return LocalObjectStorage(settings.storage.local_object_storage_path)
    required = {
        "OSS_BUCKET": settings.storage.oss_bucket,
        "OSS_REGION": settings.storage.oss_region,
        "OSS_ACCESS_KEY_ID": settings.storage.oss_access_key_id,
        "OSS_ACCESS_KEY_SECRET": settings.storage.oss_access_key_secret_value,
    }
    missing = [key for key, value in required.items() if not value]
    if missing:
        raise RuntimeError(f"missing Aliyun OSS config: {', '.join(missing)}")
    try:
        repository = AliyunOSSRepository(
            AliyunOSSConfig(
                bucket=settings.storage.oss_bucket,
                region=settings.storage.oss_region,
                access_key_id=settings.storage.oss_access_key_id,
                access_key_secret=settings.storage.oss_access_key_secret_value,
                key_prefix=settings.storage.oss_project_root,
                endpoint=settings.storage.oss_endpoint,
                endpoint_style=settings.storage.oss_endpoint_style,
                public_base_url=_public_base_url(settings.storage.oss_public_endpoint),
                scheme=settings.storage.oss_scheme,
            )
        )
    except (ObjectStorageConfigError, ObjectStorageValidationError) as exc:
        raise RuntimeError(f"invalid Aliyun OSS config: {exc}") from exc
    return AliyunObjectStorage(repository)


storage = _build_storage()
