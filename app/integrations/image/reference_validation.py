from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image, UnidentifiedImageError

from app.core.exceptions import AppError

TRANSPARENT_REFERENCE_ALLOWED_CONTENT_TYPES = frozenset({"image/png"})
TRANSPARENT_REFERENCE_MAX_BYTES = 20 * 1024 * 1024
TRANSPARENT_REFERENCE_MAX_WIDTH = 4096
TRANSPARENT_REFERENCE_MAX_HEIGHT = 4096
TRANSPARENT_REFERENCE_MAX_PIXELS = 16_777_216
TRANSPARENT_ALPHA_THRESHOLD = 16
TRANSPARENT_BORDER_MIN_RATIO = 0.95


@dataclass(frozen=True)
class ImageValidationResult:
    content_type: str
    width: int
    height: int


def _content_type_from_image(image: Image.Image) -> str:
    if not image.format:
        raise AppError("INVALID_INPUT", "reference image format is missing")
    content_type = Image.MIME.get(image.format.upper())
    if content_type is None:
        raise AppError("INVALID_INPUT", f"reference image format is not supported: {image.format}")
    return content_type


def _validate_dimensions(width: int, height: int) -> None:
    if width <= 0 or height <= 0:
        raise AppError("INVALID_INPUT", "reference image dimensions are invalid")
    if width > TRANSPARENT_REFERENCE_MAX_WIDTH or height > TRANSPARENT_REFERENCE_MAX_HEIGHT:
        raise AppError(
            "INPUT_TOO_LARGE",
            "reference image dimensions exceed poster_title_image limit",
            details={
                "max_width": TRANSPARENT_REFERENCE_MAX_WIDTH,
                "max_height": TRANSPARENT_REFERENCE_MAX_HEIGHT,
                "width": width,
                "height": height,
            },
        )
    pixels = width * height
    if pixels > TRANSPARENT_REFERENCE_MAX_PIXELS:
        raise AppError(
            "INPUT_TOO_LARGE",
            "reference image pixel count exceeds poster_title_image limit",
            details={"max_pixels": TRANSPARENT_REFERENCE_MAX_PIXELS, "pixels": pixels},
        )


def _validate_transparent_background(image: Image.Image) -> None:
    alpha = image.convert("RGBA").getchannel("A")
    width, height = alpha.size
    corners = [
        alpha.getpixel((0, 0)),
        alpha.getpixel((width - 1, 0)),
        alpha.getpixel((0, height - 1)),
        alpha.getpixel((width - 1, height - 1)),
    ]
    if any(value > TRANSPARENT_ALPHA_THRESHOLD for value in corners):
        raise AppError("INVALID_INPUT", "reference image background must be transparent")

    border_values = bytearray()
    border_values.extend(alpha.crop((0, 0, width, 1)).tobytes())
    border_values.extend(alpha.crop((0, height - 1, width, height)).tobytes())
    if height > 2:
        border_values.extend(alpha.crop((0, 1, 1, height - 1)).tobytes())
        border_values.extend(alpha.crop((width - 1, 1, width, height - 1)).tobytes())
    transparent_count = sum(1 for value in border_values if value <= TRANSPARENT_ALPHA_THRESHOLD)
    if transparent_count / len(border_values) < TRANSPARENT_BORDER_MIN_RATIO:
        raise AppError("INVALID_INPUT", "reference image border must be transparent")


def validate_transparent_reference_image(data: bytes, *, content_type: str) -> ImageValidationResult:
    if content_type not in TRANSPARENT_REFERENCE_ALLOWED_CONTENT_TYPES:
        raise AppError(
            "INVALID_INPUT",
            "reference image content_type must be image/png for transparent style probe input",
        )
    if len(data) > TRANSPARENT_REFERENCE_MAX_BYTES:
        raise AppError(
            "INPUT_TOO_LARGE",
            "reference image exceeds poster_title_image byte limit",
            details={"max_bytes": TRANSPARENT_REFERENCE_MAX_BYTES, "size_bytes": len(data)},
        )
    try:
        with Image.open(io.BytesIO(data)) as image:
            actual_content_type = _content_type_from_image(image)
            if actual_content_type != content_type:
                raise AppError(
                    "INVALID_INPUT",
                    "reference image content_type does not match actual image format",
                    details={"content_type": content_type, "actual_content_type": actual_content_type},
                )
            width, height = image.size
            _validate_dimensions(width, height)
            _validate_transparent_background(image)
            return ImageValidationResult(content_type=actual_content_type, width=width, height=height)
    except AppError:
        raise
    except (OSError, UnidentifiedImageError) as exc:
        raise AppError("INVALID_INPUT", "reference image is not a decodable image") from exc
