from __future__ import annotations

from app.core.exceptions import AppError
from app.integrations.storage import storage


def read_object_bytes(*, bucket: str, region: str, key: str, max_bytes: int | None = None) -> bytes:
    data = storage.read_bytes(bucket=bucket, region=region, key=key)
    if max_bytes is not None and len(data) > max_bytes:
        raise AppError(
            "INPUT_TOO_LARGE",
            "object input exceeds service limit",
            details={"max_bytes": max_bytes, "size_bytes": len(data)},
        )
    return data

