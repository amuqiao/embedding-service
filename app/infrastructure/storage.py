import hashlib
from pathlib import Path

from app.core.exceptions import AppError
from app.infrastructure.config import settings


def sha256_digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


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


storage = LocalObjectStorage(settings.local_object_storage_path)
