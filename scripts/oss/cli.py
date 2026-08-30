from __future__ import annotations

import json
import mimetypes
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlsplit

import typer

from app.core.oss_endpoint import normalize_oss_endpoint
from app.object_storage import (
    BaseObjectStorageAdapter,
    ExpectedObjectIntegrity,
    ObjectReadPolicy,
    ObjectReadSpec,
    ObjectRef,
    ObjectStorageAdapterContext,
    ObjectStorageConfig,
    ObjectStorageError,
    build_repository,
    sha256_digest,
)
from smoke.harness import env_runtime
from smoke.harness.errors import FlowError


ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_TEST_CONTENT = b"embedding-service object storage connectivity check\n"
DEFAULT_TEST_CONTENT_TYPE = "text/plain; charset=utf-8"
DEFAULT_UPLOAD_PREFIX = "oss/uploads/images"
ALLOWED_IMAGE_CONTENT_TYPES = {"image/png", "image/jpeg", "image/webp"}

TOP_HELP_EPILOG = """\b
作用域：
  本入口是对象存储能力事实源。verify.sh 和 k8s.sh 只编排本入口，不维护独立对象存储逻辑。

\b
保护边界：
  check 默认只解析配置；--remote 必须同时传 --confirm。
  upload-image 必须传 --confirm-upload。
  --json 输出保持纯 JSON。

\b
常用示例：
  ./scripts/oss.sh check
  ./scripts/oss.sh check --env-file .env.test
  ./scripts/oss.sh check --remote --confirm
  ./scripts/oss.sh upload-image .data/image.png --confirm-upload
"""

CHECK_HELP_EPILOG = """\b
副作用与保护边界：
  默认只检查配置，不访问对象存储。
  --remote 会写入、读取、HEAD 一个测试对象；当前运维权限不要求也不执行 DeleteObject。
  测试对象会保留在对象存储，需要按输出 key 手动清理或依赖 bucket 生命周期。
  --json 输出保持 stdout 纯 JSON。

\b
常用示例：
  ./scripts/oss.sh check
  ./scripts/oss.sh check --env-file .env.test
  ./scripts/oss.sh check --remote --confirm
  ./scripts/oss.sh check --remote --confirm --key ai-jobs/manual/check.txt
"""

UPLOAD_HELP_EPILOG = """\b
副作用与保护边界：
  写入当前对象存储后端，必须传 --confirm-upload。
  只支持 image/png、image/jpeg、image/webp。
  输出 object_storage 写入结果和 public_url；不生成 signed_url。
  --json 输出保持 stdout 纯 JSON。

\b
常用示例：
  ./scripts/oss.sh upload-image .data/image.png --confirm-upload
  ./scripts/oss.sh upload-image .data/image.png --env-file .env.test --confirm-upload
  ./scripts/oss.sh upload-image .data/image.png --confirm-upload --json
"""


app = typer.Typer(
    name="./scripts/oss.sh",
    help="对象存储能力事实源：检查配置、PUT/GET/HEAD 连通性和显式上传。",
    epilog=TOP_HELP_EPILOG,
    context_settings={"help_option_names": ["-h", "--help"]},
    no_args_is_help=True,
)


@dataclass(frozen=True)
class StorageRuntime:
    app_env: dict[str, str]
    repository_config: ObjectStorageConfig
    adapter: BaseObjectStorageAdapter


def _event(status: str, name: str, detail: str) -> None:
    print(f"[{status}] {name}: {detail}")


def _print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def _resolve_repo_path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return ROOT_DIR / path


def _env_value(name: str, app_env: dict[str, str]) -> str:
    return env_runtime.env_value(name, app_env) or ""


def _required_env(name: str, app_env: dict[str, str]) -> str:
    value = _env_value(name, app_env)
    if not value:
        raise FlowError(f"{name} is required for object storage", exit_code=2)
    return value


def _public_base_url(public_endpoint: str) -> str:
    value = public_endpoint.strip().rstrip("/")
    if not value:
        return ""
    if value.startswith("http://"):
        raise FlowError("OSS_PUBLIC_ENDPOINT must use https or host-only form", exit_code=2)
    if value.startswith("https://"):
        parsed = urlsplit(value)
        if not parsed.hostname or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise FlowError("OSS_PUBLIC_ENDPOINT must not include path, query, or fragment", exit_code=2)
        return f"https://{parsed.hostname.lower()}"
    if "://" in value:
        raise FlowError("OSS_PUBLIC_ENDPOINT must use https or host-only form", exit_code=2)
    host = normalize_oss_endpoint(value)
    if not host:
        return ""
    return f"https://{host}"


def _endpoint_style(app_env: dict[str, str], *, endpoint: str) -> str:
    endpoint_override = _env_value("OSS_ENDPOINT", app_env)
    public_endpoint = _env_value("OSS_PUBLIC_ENDPOINT", app_env)
    if endpoint_override and public_endpoint and endpoint == normalize_oss_endpoint(public_endpoint):
        return "custom_domain"
    return "virtual_host"


def _repository_config_from_env(app_env: dict[str, str]) -> ObjectStorageConfig:
    backend = _env_value("STORAGE_BACKEND", app_env) or "local"
    public_base_url = _public_base_url(_env_value("OSS_PUBLIC_ENDPOINT", app_env))

    if backend == "local":
        return ObjectStorageConfig(
            provider="local",
            options={
                "root": _resolve_repo_path(_env_value("LOCAL_OBJECT_STORAGE_PATH", app_env) or "storage/objects"),
                "bucket": _env_value("OSS_BUCKET", app_env) or "local-dev",
                "region": _env_value("OSS_REGION", app_env) or "local",
                "public_base_url": public_base_url,
            },
        )

    if backend == "aliyun_oss":
        region = _required_env("OSS_REGION", app_env)
        endpoint = normalize_oss_endpoint(_env_value("OSS_ENDPOINT", app_env)) or f"oss-{region}.aliyuncs.com"
        return ObjectStorageConfig(
            provider="aliyun_oss",
            options={
                "bucket": _required_env("OSS_BUCKET", app_env),
                "region": region,
                "access_key_id": _required_env("OSS_ACCESS_KEY_ID", app_env),
                "access_key_secret": _required_env("OSS_ACCESS_KEY_SECRET", app_env),
                "key_prefix": _required_env("OSS_PROJECT_ROOT", app_env),
                "endpoint": endpoint,
                "endpoint_style": _endpoint_style(app_env, endpoint=endpoint),
                "public_base_url": public_base_url,
                "scheme": "https",
            },
        )

    raise FlowError("STORAGE_BACKEND must be local or aliyun_oss", exit_code=2)


def _storage_runtime(env_file: Path | None) -> StorageRuntime:
    app_env = env_runtime.load_app_env(str(env_file) if env_file is not None else None, root_dir=ROOT_DIR)
    repository_config = _repository_config_from_env(app_env)
    adapter = BaseObjectStorageAdapter(
        ObjectStorageAdapterContext(repository=build_repository(repository_config))
    )
    return StorageRuntime(app_env=app_env, repository_config=repository_config, adapter=adapter)


def _config_summary(runtime: StorageRuntime) -> dict[str, str | bool]:
    options = runtime.repository_config.options
    backend = runtime.repository_config.provider
    endpoint = str(options.get("endpoint") or "")
    return {
        "backend": backend,
        "bucket": str(options.get("bucket") or ""),
        "region": str(options.get("region") or ""),
        "project_root": str(options.get("key_prefix") or ""),
        "output_prefix": _env_value("OSS_OUTPUT_PREFIX", runtime.app_env) or "ai-jobs",
        "endpoint": endpoint,
        "endpoint_style": _endpoint_style(runtime.app_env, endpoint=endpoint) if backend == "aliyun_oss" else "",
        "public_endpoint": _env_value("OSS_PUBLIC_ENDPOINT", runtime.app_env),
        "scheme": str(options.get("scheme") or "https"),
        "access_key_id_present": bool(_env_value("OSS_ACCESS_KEY_ID", runtime.app_env)),
        "access_key_secret_present": bool(_env_value("OSS_ACCESS_KEY_SECRET", runtime.app_env)),
    }


def _default_check_key(app_env: dict[str, str]) -> str:
    output_prefix = (_env_value("OSS_OUTPUT_PREFIX", app_env) or "ai-jobs").strip().strip("/")
    parts = [part for part in (output_prefix, "oss-check", f"check-{int(time.time())}-{uuid.uuid4().hex}.txt") if part]
    return "/".join(parts)


def _default_upload_key(*, source: Path, app_env: dict[str, str], key_prefix: str | None) -> str:
    output_prefix = (_env_value("OSS_OUTPUT_PREFIX", app_env) or "ai-jobs").strip().strip("/")
    clean_key_prefix = (key_prefix or DEFAULT_UPLOAD_PREFIX).strip().strip("/")
    parts = [part for part in (output_prefix, clean_key_prefix) if part]
    parts.append(f"{int(time.time())}-{uuid.uuid4().hex}")
    parts.append(source.name)
    return "/".join(parts)


def _object_ref_from_written(written: Any) -> ObjectRef:
    return ObjectRef(
        provider=written.provider,
        bucket=written.bucket,
        region=written.region,
        key=written.key,
    )


def _connectivity_check(*, runtime: StorageRuntime, key: str) -> dict[str, Any]:
    try:
        written = runtime.adapter.write_object_bytes(
            key,
            DEFAULT_TEST_CONTENT,
            content_type=DEFAULT_TEST_CONTENT_TYPE,
        )
        ref = _object_ref_from_written(written)
        meta = runtime.adapter.head_object(ref)
        body = runtime.adapter.read_object(
            ObjectReadSpec(
                ref=ref,
                integrity=ExpectedObjectIntegrity(
                    size_bytes=len(DEFAULT_TEST_CONTENT),
                    sha256=sha256_digest(DEFAULT_TEST_CONTENT),
                ),
                policy=ObjectReadPolicy(
                    verify_size_bytes=True,
                    verify_sha256=True,
                    max_bytes=len(DEFAULT_TEST_CONTENT),
                ),
            )
        )
    except ObjectStorageError as exc:
        raise RuntimeError(f"object storage connectivity check failed: {exc}") from exc
    if body != DEFAULT_TEST_CONTENT:
        raise RuntimeError("GET body does not match uploaded content")
    return {
        "key": written.key,
        "bytes": len(body),
        "content_length": meta.size_bytes if meta.size_bytes is not None else "",
        "content_type": written.content_type,
        "sha256": written.sha256,
        "object": written.to_dict(),
        "retained": True,
    }


def _image_content_type(path: Path, explicit: str | None) -> str:
    value = explicit or mimetypes.guess_type(path.name)[0] or ""
    if value not in ALLOWED_IMAGE_CONTENT_TYPES:
        raise FlowError(
            f"image content-type must be one of {sorted(ALLOWED_IMAGE_CONTENT_TYPES)}, got {value or '<unknown>'}",
            exit_code=2,
        )
    return value


def _upload_image(
    *,
    runtime: StorageRuntime,
    image: str,
    content_type: str | None,
    key: str | None,
    key_prefix: str | None,
) -> dict[str, Any]:
    source = _resolve_repo_path(image)
    if not source.is_file():
        raise FlowError(f"image not found: {source}", exit_code=2)
    data = source.read_bytes()
    resolved_content_type = _image_content_type(source, content_type)
    object_key = key or _default_upload_key(source=source, app_env=runtime.app_env, key_prefix=key_prefix)
    try:
        written = runtime.adapter.write_object_bytes(object_key, data, content_type=resolved_content_type)
    except ObjectStorageError as exc:
        raise FlowError(f"failed to upload image to object storage: {exc}", exit_code=4) from exc
    return {
        "provider": written.provider,
        "bucket": written.bucket,
        "region": written.region,
        "key": written.key,
        "content_type": written.content_type,
        "content_hash": written.sha256,
        "sha256": written.sha256,
        "content_size_bytes": written.size_bytes,
        "public_url": written.public_url or "",
        "object": written.to_dict(),
        "retained": True,
    }


def _print_result(result: dict[str, Any], *, json_output: bool) -> None:
    if json_output:
        _print_json(result)
        return

    config_summary = result["config"]
    assert isinstance(config_summary, dict)
    _event("OK", "config", " ".join(f"{key}={value}" for key, value in config_summary.items()))

    if "remote" in result:
        remote = result["remote"]
        assert isinstance(remote, dict)
        _event(
            "OK",
            "remote",
            f"key={remote['key']} bytes={remote['bytes']} sha256={remote['sha256']} retained=true",
        )
        remote_object = remote["object"]
        assert isinstance(remote_object, dict)
        if remote_object.get("public_url"):
            _event("OK", "object", f"public_url={remote_object['public_url']}")

    if "upload" in result:
        upload = result["upload"]
        assert isinstance(upload, dict)
        _event(
            "OK",
            "upload",
            (
                f"bucket={upload['bucket']} region={upload['region']} key={upload['key']} "
                f"public_url={upload['public_url']} retained=true"
            ),
        )


@app.command(help="默认只检查配置；--remote --confirm 才执行 PUT/GET/HEAD。", epilog=CHECK_HELP_EPILOG)
def check(
    env_file: Annotated[Path | None, typer.Option("--env-file", help="Env file to read; defaults to .env when present.")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Output machine-readable JSON.")] = False,
    remote: Annotated[bool, typer.Option("--remote", help="Run PUT/GET/HEAD against current object storage backend.")] = False,
    confirm: Annotated[bool, typer.Option("--confirm", help="Required when --remote is used.")] = False,
    key: Annotated[str | None, typer.Option("--key", help="Remote test object key; defaults under OSS_OUTPUT_PREFIX.")] = None,
) -> None:
    if remote and not confirm:
        raise FlowError("oss check --remote requires --confirm", exit_code=2)
    runtime = _storage_runtime(env_file)
    result: dict[str, Any] = {"config": _config_summary(runtime)}
    if remote:
        result["remote"] = _connectivity_check(runtime=runtime, key=key or _default_check_key(runtime.app_env))
    _print_result(result, json_output=json_output)


@app.command(help="显式上传本地图片到当前对象存储后端。", epilog=UPLOAD_HELP_EPILOG)
def upload_image(
    image: Annotated[str, typer.Argument(help="Local image path to upload.")],
    env_file: Annotated[Path | None, typer.Option("--env-file", help="Env file to read; defaults to .env when present.")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Output machine-readable JSON.")] = False,
    content_type: Annotated[str | None, typer.Option("--content-type", help="Image MIME type; defaults to extension inference.")] = None,
    key: Annotated[str | None, typer.Option("--key", help="Explicit object key.")] = None,
    key_prefix: Annotated[str, typer.Option("--key-prefix", help="Object key prefix when --key is omitted.")] = DEFAULT_UPLOAD_PREFIX,
    confirm_upload: Annotated[bool, typer.Option("--confirm-upload", help="Required for upload.")] = False,
) -> None:
    if not confirm_upload:
        raise FlowError("oss upload-image requires --confirm-upload", exit_code=2)
    runtime = _storage_runtime(env_file)
    result: dict[str, Any] = {"config": _config_summary(runtime)}
    result["upload"] = _upload_image(
        runtime=runtime,
        image=image,
        content_type=content_type,
        key=key,
        key_prefix=key_prefix,
    )
    _print_result(result, json_output=json_output)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print("Usage: ./scripts/oss.sh [OPTIONS] COMMAND [ARGS]...", file=sys.stderr)
        print("Try './scripts/oss.sh --help' for help.", file=sys.stderr)
        return 2
    app(args=args, prog_name="./scripts/oss.sh", standalone_mode=False)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except FlowError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(exc.exit_code) from exc
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
