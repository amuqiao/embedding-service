from __future__ import annotations

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
)


def _public_base_url(value: str) -> str:
    endpoint = normalize_oss_endpoint(value)
    if not endpoint:
        return ""
    return f"https://{endpoint}"


def validate_configuration() -> None:
    if settings.storage.backend == "local":
        return
    required = {
        "OSS_BUCKET": settings.storage.oss_bucket,
        "OSS_REGION": settings.storage.oss_region,
        "OSS_ACCESS_KEY_ID": settings.storage.oss_access_key_id,
        "OSS_ACCESS_KEY_SECRET": settings.storage.oss_access_key_secret_value,
    }
    missing = [key for key, value in required.items() if not value]
    if missing:
        raise ObjectStorageConfigError(f"missing Aliyun OSS config: {', '.join(missing)}")
    try:
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
    except (ObjectStorageConfigError, ObjectStorageValidationError) as exc:
        raise ObjectStorageConfigError(f"invalid Aliyun OSS config: {exc}") from exc


def _read_bytes(*, bucket: str, region: str, key: str) -> bytes:
    try:
        if settings.storage.backend == "local":
            repository = LocalObjectStorageRepository(
                LocalStorageConfig(
                    root=settings.storage.local_object_storage_path,
                    bucket=bucket,
                    region=region,
                )
            )
            return repository.get_bytes(ObjectRef(provider=repository.provider, bucket=bucket, region=region, key=key))

        validate_configuration()
        if bucket != settings.storage.oss_bucket:
            raise AppError(
                "OSS_BUCKET_NOT_CONFIGURED",
                "OSS bucket does not match configured Aliyun OSS bucket",
                details={"oss_bucket": bucket, "configured_bucket": settings.storage.oss_bucket},
            )
        if region != settings.storage.oss_region:
            raise AppError(
                "OSS_REGION_NOT_CONFIGURED",
                "OSS region does not match configured Aliyun OSS region",
                details={"oss_region": region, "configured_region": settings.storage.oss_region},
            )
        repository_config = AliyunOSSConfig(
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
        repository = AliyunOSSRepository(repository_config)
        return repository.get_bytes(ObjectRef(provider=repository.provider, bucket=bucket, region=region, key=key))
    except ObjectStorageConfigError as exc:
        raise AppError(
            "OSS_FETCH_FAILED",
            "Failed to configure OSS object reader",
            details={"oss_bucket": bucket, "oss_key": key, "oss_region": region},
        ) from exc
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


def read_object_bytes(*, bucket: str, region: str, key: str, max_bytes: int | None = None) -> bytes:
    data = _read_bytes(bucket=bucket, region=region, key=key)
    if max_bytes is not None and len(data) > max_bytes:
        raise AppError(
            "INPUT_TOO_LARGE",
            "object input exceeds service limit",
            details={"max_bytes": max_bytes, "size_bytes": len(data)},
        )
    return data
