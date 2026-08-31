from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from dotenv import dotenv_values
from PIL import Image, UnidentifiedImageError


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_DIR = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from storage_adapter import (  # noqa: E402
    build_oss_adapter_from_env,
    image_content_type,
    public_base_url_from_env,
    public_host_from_url,
)


DEFAULT_ASSETS_DIR = REPO_ROOT / ".data/assets"
DEFAULT_MANIFEST = SCRIPT_DIR / "reports/manifests/assets-oss-manifest.jsonl"
DEFAULT_KEY_PREFIX = "asset-vector-poc/assets"


class UploadError(RuntimeError):
    pass


@dataclass(frozen=True)
class AssetFile:
    local_path: Path
    relative_path: str
    relative_dir: str
    file_name: str
    stem: str
    extension: str
    content_type: str
    resource_id: str
    group_id: str | None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Upload image assets to OSS and write a JSONL manifest for asset_vector_poc.py index-manifest.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  uv run python poc/asset-vector/upload_assets_to_oss.py --dry-run
  uv run python poc/asset-vector/upload_assets_to_oss.py --confirm-upload
  uv run python poc/asset-vector/upload_assets_to_oss.py .data/assets --manifest poc/asset-vector/reports/manifests/assets-oss-manifest.jsonl --confirm-upload

The manifest is directly consumable by:
  uv run python poc/asset-vector/asset_vector_poc.py index-manifest poc/asset-vector/reports/manifests/assets-oss-manifest.jsonl --confirm-remote

Only image files are uploaded by this script. Audio files are reported as skipped so they are not accidentally sent to image embedding.
""",
    )
    parser.add_argument(
        "assets_dir",
        nargs="?",
        type=Path,
        default=DEFAULT_ASSETS_DIR,
        help=f"Resource directory to scan. Default: {DEFAULT_ASSETS_DIR.relative_to(REPO_ROOT)}",
    )
    parser.add_argument("--env-file", type=Path, default=None, help="Env file. Default: .env or ENV_FILE.")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST, help="Output JSONL manifest path.")
    parser.add_argument(
        "--key-prefix",
        default=DEFAULT_KEY_PREFIX,
        help="Object key prefix under OSS_PROJECT_ROOT.",
    )
    parser.add_argument("--limit", type=int, default=None, help="Max number of image files to upload.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable summary.")
    parser.add_argument("--dry-run", action="store_true", help="Scan and print summary without uploading.")
    parser.add_argument(
        "--verify-public-url",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="GET each returned public URL and verify sha256 after upload. Default: true.",
    )
    parser.add_argument(
        "--confirm-upload",
        action="store_true",
        help="Required because this writes files to OSS and retains them.",
    )
    return parser


def load_effective_env(env_file: Path | None) -> dict[str, str]:
    explicit = env_file is not None
    selected = env_file or Path(os.environ.get("ENV_FILE", ".env"))
    if not selected.is_absolute():
        selected = REPO_ROOT / selected
    required_source = explicit or "ENV_FILE" in os.environ
    if required_source and not selected.exists():
        raise UploadError(f"env file does not exist: {selected}")

    loaded: dict[str, str] = {}
    if selected.exists():
        for key, value in dotenv_values(selected).items():
            if value is not None:
                loaded[key] = value
    if explicit or "ENV_FILE" in os.environ:
        return {**os.environ, **loaded}
    return {**loaded, **os.environ}


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def scan_image_assets(assets_dir: Path, *, limit: int | None) -> tuple[list[AssetFile], list[dict[str, str]]]:
    root = assets_dir.expanduser()
    if not root.is_absolute():
        root = REPO_ROOT / root
    if not root.is_dir():
        raise UploadError(f"assets_dir does not exist or is not a directory: {root}")
    if limit is not None and limit <= 0:
        raise UploadError("--limit must be greater than 0")

    assets: list[AssetFile] = []
    skipped: list[dict[str, str]] = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root)
        try:
            content_type = image_content_type(path)
            validate_image_file(path)
        except ValueError as exc:
            skipped.append(
                {
                    "local_path": display_path(path),
                    "relative_path": relative.as_posix(),
                    "reason": str(exc),
                }
            )
            continue

        relative_dir = relative.parent.as_posix()
        if relative_dir == ".":
            relative_dir = ""
        resource_id = relative.with_suffix("").as_posix()
        assets.append(
            AssetFile(
                local_path=path,
                relative_path=relative.as_posix(),
                relative_dir=relative_dir,
                file_name=path.name,
                stem=path.stem,
                extension=path.suffix.lower(),
                content_type=content_type,
                resource_id=resource_id,
                group_id=relative_dir or None,
            )
        )
        if limit is not None and len(assets) >= limit:
            break

    return assets, skipped


def validate_image_file(path: Path) -> None:
    try:
        with Image.open(path) as image:
            image.verify()
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError(f"invalid image file for {path}: {exc}") from exc


def object_key_for(asset: AssetFile, *, key_prefix: str) -> str:
    clean_prefix = key_prefix.strip().strip("/")
    return "/".join(part for part in (clean_prefix, asset.relative_path) if part)


def verify_public_url(public_url: str, *, expected_sha256: str, max_bytes: int) -> None:
    if not public_url.startswith("https://"):
        raise UploadError(f"public_url must be https: {public_url}")
    with httpx.Client(timeout=30.0, follow_redirects=False) as client:
        try:
            response = client.get(public_url)
        except httpx.HTTPError as exc:
            raise UploadError(f"public_url GET failed: {exc}") from exc
    if response.status_code >= 400:
        raise UploadError(f"public_url GET failed: status={response.status_code} url={public_url}")
    body = response.content
    if len(body) > max_bytes:
        raise UploadError(f"public_url response exceeds max_bytes={max_bytes}: {public_url}")
    actual_sha256 = hashlib.sha256(body).hexdigest()
    if actual_sha256 != expected_sha256:
        raise UploadError(f"public_url sha256 mismatch: expected {expected_sha256}, got {actual_sha256}")


def allowed_hosts_for_env(env: dict[str, str]) -> tuple[str, ...]:
    public_base_url = public_base_url_from_env(env)
    if not public_base_url:
        raise UploadError("OSS_PUBLIC_ENDPOINT is required so uploaded assets have stable public URLs")
    return (public_host_from_url(public_base_url),)


def manifest_entry(
    asset: AssetFile,
    *,
    oss_key: str,
    public_url: str,
    size_bytes: int,
    sha256: str,
    uploaded_at: str,
) -> dict[str, Any]:
    return {
        "resource_id": asset.resource_id,
        "group_id": asset.group_id,
        "public_url": public_url,
        "local_path": display_path(asset.local_path),
        "relative_path": asset.relative_path,
        "relative_dir": asset.relative_dir,
        "file_name": asset.file_name,
        "stem": asset.stem,
        "extension": asset.extension,
        "content_type": asset.content_type,
        "oss_key": oss_key,
        "size_bytes": size_bytes,
        "sha256": sha256,
        "uploaded_at": uploaded_at,
        "image": {
            "public_url": public_url,
            "sha256": sha256,
            "content_type": asset.content_type,
            "object_key": oss_key,
            "size_bytes": size_bytes,
        },
    }


def write_jsonl(path: Path, entries: list[dict[str, Any]]) -> None:
    output = path.expanduser()
    if not output.is_absolute():
        output = REPO_ROOT / output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        "".join(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n" for entry in entries),
        encoding="utf-8",
    )


def upload_assets(args: argparse.Namespace) -> dict[str, Any]:
    assets, skipped = scan_image_assets(args.assets_dir, limit=args.limit)
    if not assets:
        raise UploadError(f"no supported image files found under {args.assets_dir}")

    summary: dict[str, Any] = {
        "assets_dir": str(args.assets_dir),
        "manifest": str(args.manifest),
        "key_prefix": args.key_prefix,
        "dry_run": args.dry_run,
        "image_count": len(assets),
        "skipped_count": len(skipped),
        "skipped": skipped,
    }
    if args.dry_run:
        summary["images"] = [
            {
                "resource_id": asset.resource_id,
                "group_id": asset.group_id,
                "relative_path": asset.relative_path,
                "content_type": asset.content_type,
                "object_key": object_key_for(asset, key_prefix=args.key_prefix),
            }
            for asset in assets
        ]
        return summary

    if not args.confirm_upload:
        raise UploadError("upload writes files to OSS and retains them; pass --confirm-upload or use --dry-run")

    env = load_effective_env(args.env_file)
    adapter = build_oss_adapter_from_env(env, allowed_hosts=allowed_hosts_for_env(env))
    max_bytes = int(env.get("POC_ASSET_VECTOR_IMAGE_MAX_BYTES", "10485760"))
    uploaded_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    entries: list[dict[str, Any]] = []

    for asset in assets:
        object_key = object_key_for(asset, key_prefix=args.key_prefix)
        written = adapter.write_object_bytes(
            object_key,
            asset.local_path.read_bytes(),
            content_type=asset.content_type,
        )
        if not written.public_url:
            raise UploadError(f"object storage did not return public_url for {asset.relative_path}")
        if args.verify_public_url:
            verify_public_url(written.public_url, expected_sha256=written.sha256, max_bytes=max_bytes)
        entries.append(
            manifest_entry(
                asset,
                oss_key=written.key,
                public_url=written.public_url,
                size_bytes=written.size_bytes,
                sha256=written.sha256,
                uploaded_at=uploaded_at,
            )
        )

    write_jsonl(args.manifest, entries)
    summary["uploaded_count"] = len(entries)
    summary["manifest"] = str(args.manifest)
    summary["verify_public_url"] = args.verify_public_url
    summary["entries_preview"] = entries[:5]
    return summary


def print_summary(summary: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
        return

    print("Asset OSS Upload")
    print(f"- assets_dir: {summary['assets_dir']}")
    print(f"- manifest: {summary['manifest']}")
    print(f"- key_prefix: {summary['key_prefix']}")
    print(f"- dry_run: {str(summary['dry_run']).lower()}")
    print(f"- image_count: {summary['image_count']}")
    print(f"- skipped_count: {summary['skipped_count']}")
    if summary.get("uploaded_count") is not None:
        print(f"- uploaded_count: {summary['uploaded_count']}")
        print(f"- verify_public_url: {str(summary['verify_public_url']).lower()}")
    if summary["skipped_count"]:
        print("- skipped examples:")
        for item in summary["skipped"][:10]:
            print(f"  - {item['relative_path']}: {item['reason']}")
    if summary.get("entries_preview"):
        print("- manifest preview:")
        for item in summary["entries_preview"]:
            print(f"  - {item['resource_id']} -> {item['public_url']}")
    if summary.get("images"):
        print("- image preview:")
        for item in summary["images"][:10]:
            print(f"  - {item['resource_id']} -> {item['object_key']}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        summary = upload_assets(args)
    except UploadError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print_summary(summary, as_json=args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
