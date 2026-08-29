from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from dotenv import dotenv_values


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_MODEL = "tongyi-embedding-vision-flash-2026-03-06"
DEFAULT_IMAGE_URL = "https://dashscope.oss-cn-beijing.aliyuncs.com/images/256_1.png"
MULTIMODAL_EMBEDDING_PATH = "/services/embeddings/multimodal-embedding/multimodal-embedding"


class ProbeError(RuntimeError):
    pass


def _resolve_repo_path(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = ROOT_DIR / path
    return path


def _read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        raise ProbeError(f"env file not found: {path}")
    values = dotenv_values(path)
    return {key: value for key, value in values.items() if key and value is not None}


def _selected_env_file(explicit_env_file: str | None) -> tuple[Path, bool]:
    if explicit_env_file is not None:
        value = explicit_env_file.strip()
        if not value:
            raise ProbeError("--env-file must not be empty")
        return _resolve_repo_path(value), True

    if "ENV_FILE" in os.environ:
        value = os.environ["ENV_FILE"].strip()
        if not value:
            raise ProbeError("ENV_FILE must not be empty")
        return _resolve_repo_path(value), True

    return ROOT_DIR / ".env", False


def _load_env(explicit_env_file: str | None) -> tuple[dict[str, str], Path]:
    path, explicit = _selected_env_file(explicit_env_file)
    values = _read_env_file(path)
    if explicit:
        return values, path

    for key in ("DASHSCOPE_API_KEY", "DASHSCOPE_BASE_URL"):
        if key in os.environ:
            values[key] = os.environ[key]
    return values, path


def _validate_base_url(base_url: str) -> str:
    normalized = base_url.strip().rstrip("/")
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ProbeError("DASHSCOPE_BASE_URL must be an absolute http(s) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ProbeError("DASHSCOPE_BASE_URL must not include credentials, query, or fragment")
    return normalized


def _dashscope_native_base_url(base_url: str) -> str:
    normalized = _validate_base_url(base_url)
    if normalized.endswith("/compatible-mode/v1"):
        return normalized[: -len("/compatible-mode/v1")] + "/api/v1"
    if normalized.endswith("/api/v1"):
        return normalized

    parsed = urlparse(normalized)
    path = parsed.path.rstrip("/")
    if path in {"", "/"}:
        return f"{parsed.scheme}://{parsed.netloc}/api/v1"
    raise ProbeError("DASHSCOPE_BASE_URL must end with /compatible-mode/v1 or /api/v1")


def _contents_for_args(args: argparse.Namespace) -> list[dict[str, str]]:
    if args.kind == "text":
        return [{"text": args.text}]
    if args.kind == "image":
        return [{"image": args.image_url}]
    if args.kind == "fused":
        return [{"text": args.text, "image": args.image_url}]
    raise ProbeError(f"unsupported kind: {args.kind}")


def _embedding_summary(payload: dict[str, Any]) -> dict[str, Any]:
    output = payload.get("output")
    embeddings = output.get("embeddings") if isinstance(output, dict) else None
    if not isinstance(embeddings, list):
        raise ProbeError("response output.embeddings must be a list")

    rows: list[dict[str, Any]] = []
    for item in embeddings:
        if not isinstance(item, dict):
            continue
        embedding = item.get("embedding")
        rows.append(
            {
                "type": item.get("type"),
                "dimension": len(embedding) if isinstance(embedding, list) else 0,
                "preview": embedding[:5] if isinstance(embedding, list) else [],
            }
        )
    return {"embedding_count": len(rows), "embeddings": rows}


def _call_dashscope(args: argparse.Namespace) -> dict[str, Any]:
    env, env_file = _load_env(args.env_file)
    api_key = (env.get("DASHSCOPE_API_KEY") or "").strip()
    base_url = (env.get("DASHSCOPE_BASE_URL") or "").strip()
    if not api_key:
        raise ProbeError("DASHSCOPE_API_KEY is required")
    if not base_url:
        raise ProbeError("DASHSCOPE_BASE_URL is required")

    native_base_url = _dashscope_native_base_url(base_url)
    endpoint = f"{native_base_url}{MULTIMODAL_EMBEDDING_PATH}"
    request_body = {
        "model": args.model,
        "input": {"contents": _contents_for_args(args)},
        "parameters": {"dimension": args.dimension},
    }

    try:
        response = httpx.post(
            endpoint,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=request_body,
            timeout=args.timeout,
        )
    except httpx.HTTPError as exc:
        raise ProbeError(f"request failed: {exc.__class__.__name__}") from exc

    try:
        payload = response.json()
    except ValueError as exc:
        raise ProbeError(f"response is not JSON; HTTP {response.status_code}") from exc

    if response.status_code >= 400:
        code = payload.get("code") if isinstance(payload, dict) else None
        message = payload.get("message") if isinstance(payload, dict) else None
        detail = f"; code={code}" if code else ""
        detail += f"; message={str(message)[:300]}" if message else ""
        raise ProbeError(f"DashScope returned HTTP {response.status_code}{detail}")

    summary = _embedding_summary(payload)
    parsed = urlparse(native_base_url)
    return {
        "ok": True,
        "env_file": str(env_file),
        "provider": "dashscope",
        "model": args.model,
        "kind": args.kind,
        "dimension_requested": args.dimension,
        "endpoint": {
            "scheme": parsed.scheme,
            "host": parsed.netloc,
            "base_path": parsed.path,
            "api_path": MULTIMODAL_EMBEDDING_PATH,
        },
        **summary,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="POC: call DashScope tongyi-embedding-vision-flash multimodal embedding API.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  uv run python poc/tongyi_embedding_vision_flash_probe.py --kind text
  uv run python poc/tongyi_embedding_vision_flash_probe.py --kind image
  uv run python poc/tongyi_embedding_vision_flash_probe.py --kind fused --text '白色运动鞋，轻量透气'

Env:
  DASHSCOPE_API_KEY is the workspace API key.
  DASHSCOPE_BASE_URL should be the workspace base URL, for example:
    https://<workspace-host>/compatible-mode/v1
  This POC derives native /api/v1 from the same host before calling:
    /services/embeddings/multimodal-embedding/multimodal-embedding
""",
    )
    parser.add_argument("--env-file", default=None, help="Env file path. Default: .env; ENV_FILE is also supported.")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"Model name. Default: {DEFAULT_MODEL}.")
    parser.add_argument("--kind", choices=("text", "image", "fused"), default="text", help="Input kind.")
    parser.add_argument("--text", default="通用多模态表征模型示例", help="Text input for text/fused probe.")
    parser.add_argument("--image-url", default=DEFAULT_IMAGE_URL, help="Image URL input for image/fused probe.")
    parser.add_argument("--dimension", type=int, default=768, help="Embedding dimension. Default: 768.")
    parser.add_argument("--timeout", type=float, default=30.0, help="HTTP timeout seconds. Default: 30.")
    parser.add_argument("--json", action="store_true", help="Print full JSON summary.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = _call_dashscope(args)
    except ProbeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    print("Tongyi Embedding Vision Flash Probe")
    print(f"- ok: {str(result['ok']).lower()}")
    print(f"- provider: {result['provider']}")
    print(f"- model: {result['model']}")
    print(f"- kind: {result['kind']}")
    print(f"- endpoint: {result['endpoint']['scheme']}://{result['endpoint']['host']}{result['endpoint']['base_path']}")
    print(f"- embedding_count: {result['embedding_count']}")
    for index, item in enumerate(result["embeddings"], start=1):
        print(f"- embedding[{index}].type: {item['type']}")
        print(f"- embedding[{index}].dimension: {item['dimension']}")
        print(f"- embedding[{index}].preview: {item['preview']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
