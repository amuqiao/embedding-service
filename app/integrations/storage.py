from pathlib import Path
from typing import Protocol

from app.core.exceptions import AppError
from app.integrations.aliyun_oss import AliyunOSSClient, AliyunOSSConfig, AliyunOSSError
from app.core.config import settings
from app.integrations.object_storage import ObjectWriteResult, sha256_digest


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
    ) -> dict: ...

    def read_text(self, *, bucket: str, key: str, region: str) -> str: ...

    def write_text(self, *, bucket: str, key: str, region: str, content: str) -> dict: ...


class LocalObjectStorage:
    def __init__(self, root: Path):
        self.root = root

    def _path(self, bucket: str, key: str) -> Path:
        clean_key = key.lstrip("/")
        root_resolved = self.root.resolve()
        bucket_root = (self.root / bucket).resolve()
        if bucket_root != root_resolved and root_resolved not in bucket_root.parents:
            raise AppError("INVALID_INPUT", "OSS bucket contains illegal path traversal")
        resolved = (bucket_root / clean_key).resolve()
        if resolved != bucket_root and bucket_root not in resolved.parents:
            raise AppError("INVALID_INPUT", "OSS key contains illegal path traversal")
        return resolved

    def read_bytes(self, *, bucket: str, key: str, region: str) -> bytes:
        path = self._path(bucket, key)
        if not path.exists():
            raise AppError(
                "OSS_OBJECT_NOT_FOUND",
                f"OSS object not found: {bucket}/{key}",
                details={"oss_bucket": bucket, "oss_key": key, "oss_region": region},
            )
        try:
            return path.read_bytes()
        except OSError as exc:
            raise AppError("OSS_FETCH_FAILED", "Failed to read OSS object") from exc

    def write_bytes(
        self,
        *,
        bucket: str,
        key: str,
        region: str,
        data: bytes,
        content_type: str = "application/octet-stream",
    ) -> dict:
        path = self._path(bucket, key)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        except OSError as exc:
            raise AppError("OSS_WRITE_FAILED", "Failed to write OSS object") from exc
        return ObjectWriteResult(
            provider="local",
            bucket=bucket,
            key=key,
            region=region,
            content_type=content_type,
            content_hash=sha256_digest(data),
            content_size_bytes=len(data),
        ).to_legacy_dict()

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
    def __init__(self, client: AliyunOSSClient):
        self.client = client

    def _assert_target(self, *, bucket: str, region: str) -> None:
        if bucket != self.client.config.bucket:
            raise AppError(
                "OSS_BUCKET_NOT_CONFIGURED",
                "OSS bucket does not match configured Aliyun OSS bucket",
                details={"oss_bucket": bucket, "configured_bucket": self.client.config.bucket},
            )
        if region != self.client.config.region:
            raise AppError(
                "OSS_REGION_NOT_CONFIGURED",
                "OSS region does not match configured Aliyun OSS region",
                details={"oss_region": region, "configured_region": self.client.config.region},
            )

    def read_bytes(self, *, bucket: str, key: str, region: str) -> bytes:
        self._assert_target(bucket=bucket, region=region)
        try:
            return self.client.get_object(key)
        except AliyunOSSError as exc:
            message = str(exc)
            code = "OSS_OBJECT_NOT_FOUND" if "status=404" in message else "OSS_FETCH_FAILED"
            raise AppError(
                code,
                "OSS object not found" if code == "OSS_OBJECT_NOT_FOUND" else "Failed to read OSS object",
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
    ) -> dict:
        self._assert_target(bucket=bucket, region=region)
        try:
            self.client.put_object(key, data, content_type=content_type)
        except AliyunOSSError as exc:
            raise AppError(
                "OSS_WRITE_FAILED",
                "Failed to write OSS object",
                details={"oss_bucket": bucket, "oss_key": key, "oss_region": region},
            ) from exc
        return ObjectWriteResult(
            provider="aliyun_oss",
            bucket=bucket,
            key=self.client.object_key(key),
            region=region,
            content_type=content_type,
            content_hash=sha256_digest(data),
            content_size_bytes=len(data),
        ).to_legacy_dict()

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
    return AliyunObjectStorage(
        AliyunOSSClient(
            AliyunOSSConfig(
                bucket=settings.storage.oss_bucket,
                region=settings.storage.oss_region,
                access_key_id=settings.storage.oss_access_key_id,
                access_key_secret=settings.storage.oss_access_key_secret_value,
                project_root=settings.storage.oss_project_root,
                endpoint=settings.storage.oss_endpoint,
                endpoint_style=settings.storage.oss_endpoint_style,
                scheme=settings.storage.oss_scheme,
            )
        )
    )


storage = _build_storage()
