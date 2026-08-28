from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from app.integrations.aliyun_oss import AliyunOSSClient, AliyunOSSError
from app.integrations.object_storage import sha256_digest
from app.jobs.payload_adapters.oss_url_ref import oss_url_ref_from_output_object
from smoke.flows import oss_image_upload
from smoke.harness import job_runtime


ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_TEST_CONTENT = b"cms-ai-translation-service aliyun oss connectivity check\n"
DEFAULT_TEST_CONTENT_TYPE = "text/plain; charset=utf-8"


def _event(status: str, name: str, detail: str) -> None:
    print(f"[{status}] {name}: {detail}")


def _config_summary(client: AliyunOSSClient, app_env: dict[str, str]) -> dict[str, str | bool]:
    config = client.config
    return {
        "backend": job_runtime.env_value("STORAGE_BACKEND", app_env) or "local",
        "bucket": config.bucket,
        "region": config.region,
        "project_root": config.normalized_project_root,
        "output_prefix": job_runtime.env_value("OSS_OUTPUT_PREFIX", app_env) or "ai-jobs",
        "endpoint": config.normalized_endpoint,
        "endpoint_style": config.endpoint_style,
        "public_endpoint": job_runtime.env_value("OSS_PUBLIC_ENDPOINT", app_env) or "",
        "scheme": config.scheme,
        "access_key_id_present": bool(job_runtime.env_value("OSS_ACCESS_KEY_ID", app_env)),
        "access_key_secret_present": bool(job_runtime.env_value("OSS_ACCESS_KEY_SECRET", app_env)),
    }


def _load_client(env_file: Path | None) -> tuple[dict[str, str], AliyunOSSClient]:
    app_env = job_runtime.load_app_env(str(env_file) if env_file is not None else None, root_dir=ROOT_DIR)
    config = oss_image_upload.load_aliyun_oss_config(app_env)
    return app_env, AliyunOSSClient(config)


def _default_check_key(app_env: dict[str, str]) -> str:
    output_prefix = (job_runtime.env_value("OSS_OUTPUT_PREFIX", app_env) or "ai-jobs").strip().strip("/")
    parts = [part for part in (output_prefix, "oss-check", f"check-{int(time.time())}-{uuid.uuid4().hex}.txt") if part]
    return "/".join(parts)


def _connectivity_check(
    *,
    app_env: dict[str, str],
    client: AliyunOSSClient,
    key: str,
) -> dict[str, str | int | bool | dict[str, str]]:
    object_key = client.object_key(key)
    try:
        client.put_object(key, DEFAULT_TEST_CONTENT, content_type=DEFAULT_TEST_CONTENT_TYPE)
        body = client.get_object(key)
        if body != DEFAULT_TEST_CONTENT:
            raise RuntimeError("GET body does not match uploaded content")
        headers = client.head_object(key)
    except AliyunOSSError as exc:
        raise RuntimeError(f"Aliyun OSS connectivity check failed: {exc}") from exc
    url_ref = oss_url_ref_from_output_object(
        bucket=client.config.bucket,
        region=client.config.region,
        key=object_key,
        content_type=DEFAULT_TEST_CONTENT_TYPE,
        content_hash=sha256_digest(DEFAULT_TEST_CONTENT),
        public_endpoint=job_runtime.env_value("OSS_PUBLIC_ENDPOINT", app_env) or None,
    )
    return {
        "key": object_key,
        "bytes": len(body),
        "content_length": headers.get("Content-Length", ""),
        "content_type": DEFAULT_TEST_CONTENT_TYPE,
        "sha256": url_ref["sha256"],
        "url_ref": url_ref,
        "retained": True,
    }


def _print_result(result: dict[str, Any], *, json_output: bool) -> None:
    if json_output:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return

    config_summary = result["config"]
    assert isinstance(config_summary, dict)
    _event(
        "OK",
        "config",
        " ".join(f"{key}={value}" for key, value in config_summary.items()),
    )
    if "remote" in result:
        remote = result["remote"]
        assert isinstance(remote, dict)
        _event(
            "OK",
            "remote",
            (
                f"key={remote['key']} bytes={remote['bytes']} "
                f"sha256={remote['sha256']} retained=true"
            ),
        )
        ref = remote["url_ref"]
        assert isinstance(ref, dict)
        _event("OK", "url-ref", f"public_url={ref['public_url']} internal_url={ref['internal_url']}")
    if "upload" in result:
        upload = result["upload"]
        assert isinstance(upload, dict)
        ref = upload["url_ref"]
        assert isinstance(ref, dict)
        _event(
            "OK",
            "upload",
            (
                f"bucket={upload['bucket']} region={upload['region']} key={upload['key']} "
                f"signed_url={upload['signed_url']} public_url={ref['public_url']}"
            ),
        )


def _add_common_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--env-file", type=Path, help="Env file to read; defaults to .env when present.")
    parser.add_argument("--json", action="store_true", help="Output machine-readable JSON.")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="./scripts/oss.sh",
        description="OSS 配置、连通性和显式上传检查入口。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""作用域：
  本入口是对象存储能力事实源。verify.sh 和 k8s.sh 只编排本入口，不维护独立 OSS 逻辑。

保护边界：
  check 默认只解析配置；--remote 必须同时传 --confirm。
  upload-image 必须传 --confirm-upload。
  --json 输出保持纯 JSON。

常用示例：
  ./scripts/oss.sh check
  ./scripts/oss.sh check --remote --confirm
  ./scripts/oss.sh upload-image .data/image.png --confirm-upload
""",
    )
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")

    check = subparsers.add_parser(
        "check",
        help="检查 OSS 配置；--remote --confirm 才执行 PUT/GET/HEAD。",
        description="检查 OSS 配置；--remote --confirm 才执行 PUT/GET/HEAD。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""副作用与保护边界：
  默认只检查配置，不访问 OSS。
  --remote 会写入、读取、HEAD 一个测试对象；当前运维权限不要求也不执行 DeleteObject。
  测试对象会保留在 OSS，需要按输出 key 手动清理或依赖 bucket 生命周期。

常用示例：
  ./scripts/oss.sh check
  ./scripts/oss.sh check --remote --confirm
  ./scripts/oss.sh check --remote --confirm --key ai-jobs/manual/check.txt
""",
    )
    _add_common_options(check)
    check.add_argument("--remote", action="store_true", help="Run PUT/GET/HEAD against Aliyun OSS.")
    check.add_argument("--confirm", action="store_true", help="Required when --remote is used.")
    check.add_argument("--key", help="Remote test object key; defaults under OSS_OUTPUT_PREFIX.")
    check.set_defaults(func=_run_check)

    upload = subparsers.add_parser(
        "upload-image",
        help="显式上传本地图片并输出 URL Ref。",
        description="显式上传本地图片并输出 URL Ref。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""副作用与保护边界：
  写 OSS，必须传 --confirm-upload。
  只支持当前项目上传工具认可的图片类型。

常用示例：
  ./scripts/oss.sh upload-image .data/image.png --confirm-upload
  ./scripts/oss.sh upload-image .data/image.png --confirm-upload --json
""",
    )
    _add_common_options(upload)
    upload.add_argument("image", help="Local image path to upload.")
    upload.add_argument("--content-type", help="Image MIME type; defaults to extension inference.")
    upload.add_argument("--key", help="Explicit object key.")
    upload.add_argument("--key-prefix", default="oss/uploads/images", help="Object key prefix when --key is omitted.")
    upload.add_argument("--confirm-upload", action="store_true", help="Required for upload.")
    upload.add_argument(
        "--signed-url-expires-seconds",
        type=int,
        default=3600,
        help="Temporary signed_url lifetime for upload output.",
    )
    upload.set_defaults(func=_run_upload_image)

    return parser


def _run_check(args: argparse.Namespace) -> int:
    if args.remote and not args.confirm:
        raise job_runtime.FlowError("oss check --remote requires --confirm", exit_code=2)
    app_env, client = _load_client(args.env_file)
    result: dict[str, Any] = {"config": _config_summary(client, app_env)}
    if args.remote:
        key = args.key or _default_check_key(app_env)
        result["remote"] = _connectivity_check(app_env=app_env, client=client, key=key)
    _print_result(result, json_output=args.json)
    return 0


def _run_upload_image(args: argparse.Namespace) -> int:
    if not args.confirm_upload:
        raise job_runtime.FlowError("oss upload-image requires --confirm-upload", exit_code=2)
    app_env, client = _load_client(args.env_file)
    result: dict[str, Any] = {"config": _config_summary(client, app_env)}
    result["upload"] = oss_image_upload.upload_image(
        image=args.image,
        content_type=args.content_type,
        app_env=app_env,
        key=args.key,
        key_prefix=args.key_prefix,
        signed_url_expires_seconds=args.signed_url_expires_seconds,
        client=client,
    )
    _print_result(result, json_output=args.json)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help(sys.stderr)
        return 2
    return int(args.func(args))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except job_runtime.FlowError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(exc.exit_code)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
