from __future__ import annotations

from pathlib import Path
from typing import Protocol

from app.core.exceptions import AppError
from app.integrations.image.png_chroma_key import ProcessedImage, remove_green_background
from app.integrations.object_storage.aliyun_url import parse_aliyun_oss_url


class ObjectStorageReader(Protocol):
    def read_bytes(self, *, bucket: str, key: str, region: str) -> bytes: ...


def transparent_title_layer_from_green_screen_bytes(data: bytes) -> ProcessedImage:
    return remove_green_background(data)


def transparent_title_layer_from_green_screen_file(path: str | Path) -> ProcessedImage:
    try:
        data = Path(path).expanduser().read_bytes()
    except OSError as exc:
        raise AppError("OSS_FETCH_FAILED", "Failed to read local title image") from exc
    return transparent_title_layer_from_green_screen_bytes(data)


def transparent_title_layer_from_green_screen_oss_url(
    url: str,
    *,
    object_storage: ObjectStorageReader,
) -> ProcessedImage:
    location = parse_aliyun_oss_url(url)
    data = object_storage.read_bytes(
        bucket=location.bucket,
        region=location.region,
        key=location.key,
    )
    return transparent_title_layer_from_green_screen_bytes(data)
