from __future__ import annotations

from pathlib import Path

from app.core.exceptions import AppError
from app.business_packages.poster_title_image.png_chroma_key import ProcessedImage, remove_green_background


def transparent_title_layer_from_green_screen_bytes(data: bytes) -> ProcessedImage:
    return remove_green_background(data)


def transparent_title_layer_from_green_screen_file(path: str | Path) -> ProcessedImage:
    try:
        data = Path(path).expanduser().read_bytes()
    except OSError as exc:
        raise AppError("OSS_FETCH_FAILED", "Failed to read local title image") from exc
    return transparent_title_layer_from_green_screen_bytes(data)
