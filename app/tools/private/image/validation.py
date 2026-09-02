from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image, UnidentifiedImageError

from app.core.exceptions import AppError


@dataclass(frozen=True)
class ImageValidationPolicy:
    allowed_content_types: frozenset[str]
    max_bytes: int
    max_width: int
    max_height: int
    max_pixels: int


@dataclass(frozen=True)
class ImageValidationResult:
    content_type: str
    width: int
    height: int


def _content_type_from_image(image: Image.Image) -> str:
    if not image.format:
        raise AppError("INVALID_INPUT", "image format is missing")
    content_type = Image.MIME.get(image.format.upper())
    if content_type is None:
        raise AppError("INVALID_INPUT", f"image format is not supported: {image.format}")
    return content_type


def _validate_dimensions(width: int, height: int, *, policy: ImageValidationPolicy) -> None:
    if width <= 0 or height <= 0:
        raise AppError("INVALID_INPUT", "image dimensions are invalid")
    if width > policy.max_width or height > policy.max_height:
        raise AppError(
            "INPUT_TOO_LARGE",
            "image dimensions exceed service limit",
            details={
                "max_width": policy.max_width,
                "max_height": policy.max_height,
                "width": width,
                "height": height,
            },
        )
    pixels = width * height
    if pixels > policy.max_pixels:
        raise AppError(
            "INPUT_TOO_LARGE",
            "image pixel count exceeds service limit",
            details={"max_pixels": policy.max_pixels, "pixels": pixels},
        )


def validate_image_bytes(
    data: bytes,
    *,
    content_type: str,
    policy: ImageValidationPolicy,
) -> ImageValidationResult:
    if content_type not in policy.allowed_content_types:
        raise AppError(
            "INVALID_INPUT",
            "image content_type is not allowed",
            details={"allowed_content_types": sorted(policy.allowed_content_types), "content_type": content_type},
        )
    if len(data) > policy.max_bytes:
        raise AppError(
            "INPUT_TOO_LARGE",
            "image exceeds service byte limit",
            details={"max_bytes": policy.max_bytes, "size_bytes": len(data)},
        )
    try:
        with Image.open(io.BytesIO(data)) as image:
            actual_content_type = _content_type_from_image(image)
            if actual_content_type != content_type:
                raise AppError(
                    "INVALID_INPUT",
                    "image content_type does not match actual image format",
                    details={"content_type": content_type, "actual_content_type": actual_content_type},
                )
            width, height = image.size
            _validate_dimensions(width, height, policy=policy)
            image.load()
            return ImageValidationResult(content_type=actual_content_type, width=width, height=height)
    except AppError:
        raise
    except (OSError, UnidentifiedImageError) as exc:
        raise AppError("INVALID_INPUT", "image is not a decodable image") from exc
