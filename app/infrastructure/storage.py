import hashlib
from pathlib import Path
from typing import Protocol

from app.core.exceptions import AppError
from app.infrastructure.aliyun_oss import AliyunOSSClient, AliyunOSSConfig, AliyunOSSError
from app.infrastructure.config import settings


def sha256_digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


class ObjectStorage(Protocol):
    def read_text(self, *, bucket: str, key: str, region: str) -> str: ...

    def write_text(self, *, bucket: str, key: str, region: str, content: str) -> dict: ...


class LocalObjectStorage:
    def __init__(self, root: Path):
        self.root = root

    def _path(self, bucket: str, key: str) -> Path:
        clean_key = key.lstrip("/")
        return self.root / bucket / clean_key

    def read_text(self, *, bucket: str, key: str, region: str) -> str:
        path = self._path(bucket, key)
        if not path.exists():
            raise AppError(
                "OSS_OBJECT_NOT_FOUND",
                f"OSS object not found: {bucket}/{key}",
                status_code=422,
                details={"oss_bucket": bucket, "oss_key": key, "oss_region": region},
            )
        try:
            return path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise AppError("INVALID_INPUT", "OSS object must be UTF-8 text", status_code=422) from exc
        except OSError as exc:
            raise AppError("OSS_FETCH_FAILED", "Failed to read OSS object", status_code=422) from exc

    def write_text(self, *, bucket: str, key: str, region: str, content: str) -> dict:
        path = self._path(bucket, key)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            data = content.encode("utf-8")
            path.write_bytes(data)
        except OSError as exc:
            raise AppError("OSS_WRITE_FAILED", "Failed to write OSS object", status_code=500) from exc
        return {
            "oss_bucket": bucket,
            "oss_key": key,
            "oss_region": region,
            "content_hash": sha256_digest(data),
            "content_size_bytes": len(data),
        }


class AliyunObjectStorage:
    def __init__(self, client: AliyunOSSClient):
        self.client = client

    def _assert_target(self, *, bucket: str, region: str) -> None:
        if bucket != self.client.config.bucket:
            raise AppError(
                "OSS_BUCKET_NOT_CONFIGURED",
                "OSS bucket does not match configured Aliyun OSS bucket",
                status_code=422,
                details={"oss_bucket": bucket, "configured_bucket": self.client.config.bucket},
            )
        if region != self.client.config.region:
            raise AppError(
                "OSS_REGION_NOT_CONFIGURED",
                "OSS region does not match configured Aliyun OSS region",
                status_code=422,
                details={"oss_region": region, "configured_region": self.client.config.region},
            )

    def read_text(self, *, bucket: str, key: str, region: str) -> str:
        self._assert_target(bucket=bucket, region=region)
        try:
            data = self.client.get_object(key)
        except AliyunOSSError as exc:
            message = str(exc)
            code = "OSS_OBJECT_NOT_FOUND" if "status=404" in message else "OSS_FETCH_FAILED"
            raise AppError(
                code,
                "OSS object not found" if code == "OSS_OBJECT_NOT_FOUND" else "Failed to read OSS object",
                status_code=422,
                details={"oss_bucket": bucket, "oss_key": key, "oss_region": region},
            ) from exc
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise AppError("INVALID_INPUT", "OSS object must be UTF-8 text", status_code=422) from exc

    def write_text(self, *, bucket: str, key: str, region: str, content: str) -> dict:
        self._assert_target(bucket=bucket, region=region)
        data = content.encode("utf-8")
        try:
            self.client.put_object(key, data, content_type="text/plain; charset=utf-8")
        except AliyunOSSError as exc:
            raise AppError(
                "OSS_WRITE_FAILED",
                "Failed to write OSS object",
                status_code=500,
                details={"oss_bucket": bucket, "oss_key": key, "oss_region": region},
            ) from exc
        return {
            "oss_bucket": bucket,
            "oss_key": self.client.object_key(key),
            "oss_region": region,
            "content_hash": sha256_digest(data),
            "content_size_bytes": len(data),
        }


def _build_storage() -> ObjectStorage:
    if settings.STORAGE_BACKEND == "local":
        return LocalObjectStorage(settings.local_object_storage_path)
    required = {
        "OSS_BUCKET": settings.OSS_BUCKET,
        "OSS_REGION": settings.OSS_REGION,
        "OSS_ACCESS_KEY_ID": settings.OSS_ACCESS_KEY_ID,
        "OSS_ACCESS_KEY_SECRET": settings.OSS_ACCESS_KEY_SECRET,
    }
    missing = [key for key, value in required.items() if not value]
    if missing:
        raise RuntimeError(f"missing Aliyun OSS config: {', '.join(missing)}")
    return AliyunObjectStorage(
        AliyunOSSClient(
            AliyunOSSConfig(
                bucket=settings.OSS_BUCKET,
                region=settings.OSS_REGION,
                access_key_id=settings.OSS_ACCESS_KEY_ID,
                access_key_secret=settings.OSS_ACCESS_KEY_SECRET,
                project_root=settings.OSS_PROJECT_ROOT,
                endpoint=settings.oss_endpoint,
                endpoint_style=settings.oss_endpoint_style,
                scheme=settings.OSS_SCHEME,
            )
        )
    )


storage = _build_storage()
