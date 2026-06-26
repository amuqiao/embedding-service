from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

import httpx
from PIL import Image


def _event(status: str, name: str, detail: str) -> None:
    print(f"[{status}] {name}: {detail}")


def _normalize_path_source(source: str) -> str:
    if source.startswith("@") and not source.startswith("@http://") and not source.startswith("@https://"):
        return source[1:]
    return source


def _source_kind(source: str) -> str:
    parsed = urlparse(source)
    if parsed.scheme in {"http", "https"}:
        return "url"
    if parsed.scheme == "file":
        return "file-url"
    return "file"


def _read_source(source: str, *, timeout_seconds: float) -> tuple[bytes, dict[str, str]]:
    kind = _source_kind(source)
    if kind == "url":
        response = httpx.get(source, timeout=timeout_seconds, follow_redirects=True)
        response.raise_for_status()
        return response.content, {
            "source_kind": kind,
            "content_type": response.headers.get("content-type", ""),
            "final_url": str(response.url),
        }
    if kind == "file-url":
        parsed = urlparse(source)
        path = Path(unquote(parsed.path)).expanduser()
    else:
        path = Path(_normalize_path_source(source)).expanduser()
    data = path.read_bytes()
    return data, {"source_kind": kind, "path": str(path)}


def _alpha_stats(image: Image.Image, *, alpha_threshold: int) -> dict[str, object]:
    width, height = image.size
    rgba = image.convert("RGBA")
    alpha = rgba.getchannel("A")
    alpha_min, alpha_max = alpha.getextrema()
    histogram = alpha.histogram()
    transparent_pixels = sum(histogram[: alpha_threshold + 1])
    translucent_pixels = sum(histogram[alpha_threshold + 1 : 255])
    opaque_pixels = histogram[255]
    total_pixels = width * height
    corners = [
        alpha.getpixel((0, 0)),
        alpha.getpixel((width - 1, 0)),
        alpha.getpixel((0, height - 1)),
        alpha.getpixel((width - 1, height - 1)),
    ]
    has_alpha_channel = image.mode in {"LA", "PA", "RGBA"} or "transparency" in image.info
    return {
        "has_alpha_channel": has_alpha_channel,
        "has_transparency": alpha_min <= alpha_threshold,
        "fully_transparent": alpha_max <= alpha_threshold,
        "fully_opaque": alpha_min > alpha_threshold,
        "transparent_background": all(value <= alpha_threshold for value in corners),
        "alpha_min": alpha_min,
        "alpha_max": alpha_max,
        "transparent_pixels": transparent_pixels,
        "translucent_pixels": translucent_pixels,
        "opaque_pixels": opaque_pixels,
        "total_pixels": total_pixels,
        "transparent_ratio": transparent_pixels / total_pixels if total_pixels else 0.0,
        "corner_alpha": corners,
    }


def inspect_source(source: str, *, timeout_seconds: float = 30.0, alpha_threshold: int = 0) -> dict[str, object]:
    if not 0 <= alpha_threshold <= 254:
        raise ValueError("--alpha-threshold must be between 0 and 254")
    data, source_info = _read_source(source, timeout_seconds=timeout_seconds)
    with Image.open(io.BytesIO(data)) as image:
        image.load()
        frames = getattr(image, "n_frames", 1)
        result: dict[str, object] = {
            "source": source,
            "source_kind": source_info["source_kind"],
            "bytes": len(data),
            "format": image.format,
            "mime": Image.MIME.get(image.format or "", ""),
            "mode": image.mode,
            "width": image.width,
            "height": image.height,
            "frames": frames,
            "alpha": _alpha_stats(image, alpha_threshold=alpha_threshold),
        }
        if "content_type" in source_info:
            result["http_content_type"] = source_info["content_type"]
            result["final_url"] = source_info["final_url"]
        if "path" in source_info:
            result["path"] = source_info["path"]
        return result


def _check_expectations(
    results: list[dict[str, object]],
    *,
    require_transparent: bool,
    require_opaque: bool,
    require_transparent_background: bool,
) -> None:
    for result in results:
        alpha = result["alpha"]
        assert isinstance(alpha, dict)
        source = result["source"]
        if require_transparent and not alpha["has_transparency"]:
            raise RuntimeError(f"image is not transparent: {source}")
        if require_opaque and not alpha["fully_opaque"]:
            raise RuntimeError(f"image is not fully opaque: {source}")
        if require_transparent_background and not alpha["transparent_background"]:
            raise RuntimeError(f"image background corners are not transparent: {source}")


def _print_result(result: dict[str, object]) -> None:
    alpha = result["alpha"]
    assert isinstance(alpha, dict)
    _event(
        "OK",
        "image",
        (
            f"source(来源)={result['source']} kind(来源类型)={result['source_kind']} "
            f"format(格式)={result['format']} mime={result['mime']} "
            f"size(尺寸)={result['width']}x{result['height']} mode={result['mode']} "
            f"frames(帧数)={result['frames']} bytes(字节)={result['bytes']}"
        ),
    )
    _event(
        "OK",
        "alpha",
        (
            f"alpha_channel(Alpha通道)={alpha['has_alpha_channel']} "
            f"transparency(存在透明)={alpha['has_transparency']} "
            f"transparent_bg(透明底)={alpha['transparent_background']} "
            f"fully_opaque(全不透明)={alpha['fully_opaque']} "
            f"alpha_min={alpha['alpha_min']} alpha_max={alpha['alpha_max']} "
            f"transparent_pixels(透明像素)={alpha['transparent_pixels']}/{alpha['total_pixels']} "
            f"transparent_ratio(透明占比)={alpha['transparent_ratio']:.6f}"
        ),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect local or remote image type and transparency.")
    parser.add_argument("sources", nargs="+", help="Local file path, file:// URL, or http(s) image URL.")
    parser.add_argument("--json", action="store_true", help="Output machine-readable JSON.")
    parser.add_argument("--timeout-seconds", type=float, default=30.0, help="HTTP download timeout for URL sources.")
    parser.add_argument(
        "--alpha-threshold",
        type=int,
        default=0,
        help="Alpha values <= this threshold count as transparent; default requires exact alpha=0.",
    )
    parser.add_argument("--require-transparent", action="store_true", help="Fail unless each image has transparent pixels.")
    parser.add_argument("--require-opaque", action="store_true", help="Fail unless each image is fully opaque.")
    parser.add_argument(
        "--require-transparent-background",
        action="store_true",
        help="Fail unless each image has transparent alpha at all four corners.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.require_transparent and args.require_opaque:
        raise ValueError("--require-transparent and --require-opaque cannot be used together")
    results = [
        inspect_source(source, timeout_seconds=args.timeout_seconds, alpha_threshold=args.alpha_threshold)
        for source in args.sources
    ]
    _check_expectations(
        results,
        require_transparent=args.require_transparent,
        require_opaque=args.require_opaque,
        require_transparent_background=args.require_transparent_background,
    )
    if args.json:
        payload: object = results[0] if len(results) == 1 else {"images": results}
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        for result in results:
            _print_result(result)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
