from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR))

from app.integrations.aliyun_oss import AliyunOSSClient, AliyunOSSError
from smoke.flows import llm_job_billing, oss_image_upload

DEFAULT_TEST_CONTENT = b"fastapi-best-ai-architecture aliyun oss connectivity check\n"


def _event(status: str, name: str, detail: str) -> None:
    print(f"[{status}] {name}: {detail}")


def _config_summary(client: AliyunOSSClient) -> dict[str, str]:
    config = client.config
    return {
        "backend": "aliyun_oss",
        "bucket": config.bucket,
        "region": config.region,
        "project_root": config.normalized_project_root,
        "endpoint": config.normalized_endpoint,
        "endpoint_style": config.endpoint_style,
        "scheme": config.scheme,
    }


def _connectivity_check(
    *,
    client: AliyunOSSClient,
    key: str,
    keep: bool,
) -> dict[str, str | int | bool]:
    object_key = client.object_key(key)
    try:
        client.put_object(key, DEFAULT_TEST_CONTENT, content_type="text/plain; charset=utf-8")
        body = client.get_object(key)
        if body != DEFAULT_TEST_CONTENT:
            raise RuntimeError("GET body does not match uploaded content")
        headers = client.head_object(key)
        if not keep:
            client.delete_object(key)
    except AliyunOSSError as exc:
        raise RuntimeError(f"Aliyun OSS connectivity check failed: {exc}") from exc
    return {
        "key": object_key,
        "bytes": len(body),
        "content_length": headers.get("Content-Length", ""),
        "kept": keep,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check Aliyun OSS config and optional connectivity.")
    parser.add_argument("--env-file", type=Path, default=ROOT_DIR / ".env", help="Env file to read; defaults to .env.")
    parser.add_argument("--remote", action="store_true", help="Run PUT/GET/HEAD/DELETE against Aliyun OSS.")
    parser.add_argument("--key", default=f"verify/oss-config/check-{int(time.time())}.txt", help="Remote test object key.")
    parser.add_argument("--keep", action="store_true", help="Keep the --remote test object after verification.")
    parser.add_argument("--upload-image", type=str, help="Upload a local image and return OSS URL Ref.")
    parser.add_argument("--content-type", type=str, help="Image MIME type; defaults to extension inference.")
    parser.add_argument("--confirm-upload", action="store_true", help="Required when --upload-image is used.")
    parser.add_argument(
        "--signed-url-expires-seconds",
        type=int,
        default=3600,
        help="Temporary signed_url lifetime for --upload-image output.",
    )
    parser.add_argument("--json", action="store_true", help="Output machine-readable JSON.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    app_env = llm_job_billing.load_env_file(args.env_file)
    config = oss_image_upload.load_aliyun_oss_config(app_env)
    client = AliyunOSSClient(config)
    result: dict[str, object] = {"config": _config_summary(client)}

    if args.remote:
        result["remote"] = _connectivity_check(client=client, key=args.key, keep=args.keep)

    if args.upload_image:
        if not args.confirm_upload:
            raise RuntimeError("--upload-image requires --confirm-upload")
        result["upload"] = oss_image_upload.upload_image(
            image=args.upload_image,
            content_type=args.content_type,
            app_env=app_env,
            key=None,
            key_prefix="verify/oss-config/uploads",
            signed_url_expires_seconds=args.signed_url_expires_seconds,
            client=client,
        )

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
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
            delete_detail = "kept" if remote["kept"] else "deleted"
            _event("OK", "remote", f"key={remote['key']} bytes={remote['bytes']} {delete_detail}")
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
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
