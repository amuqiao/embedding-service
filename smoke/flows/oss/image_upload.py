from __future__ import annotations

import hashlib
import mimetypes
import shlex
import time
import uuid
from pathlib import Path
from typing import Any

from app.core.oss_endpoint import normalize_oss_endpoint
from app.tools.private.object_storage_refs import sha256_digest
from app.tools.providers.aliyun_oss import AliyunOSSClient, AliyunOSSConfig, AliyunOSSError
from smoke.harness import formatters
from smoke.harness import env_runtime
from smoke.harness.errors import FlowError
from smoke.flows.oss.url_ref import oss_url_ref_from_output_object
ROOT_DIR = env_runtime.ROOT_DIR
ALLOWED_IMAGE_CONTENT_TYPES = {"image/png", "image/jpeg", "image/webp"}
DEFAULT_UPLOAD_PREFIX = "smoke/uploads/images"
OUTPUT_MODES = {"table", "json", "url-ref-json", "poster-args"}


def _resolve_repo_path(value: str) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return ROOT_DIR / path


def image_content_type(path: Path, explicit: str | None) -> str:
    value = explicit or mimetypes.guess_type(path.name)[0] or ""
    if value not in ALLOWED_IMAGE_CONTENT_TYPES:
        raise FlowError(f"image content type must be one of {sorted(ALLOWED_IMAGE_CONTENT_TYPES)}, got {value!r}", exit_code=2)
    return value


def bare_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _required_env(name: str, app_env: dict[str, str]) -> str:
    value = env_runtime.env_value(name, app_env)
    if not value:
        raise FlowError(f"{name} is required for Aliyun OSS upload", exit_code=2)
    return value


def load_aliyun_oss_config(app_env: dict[str, str]) -> AliyunOSSConfig:
    storage_backend = env_runtime.env_value("STORAGE_BACKEND", app_env) or "local"
    if storage_backend != "aliyun_oss":
        raise FlowError("Aliyun OSS upload requires STORAGE_BACKEND=aliyun_oss", exit_code=2)

    bucket = _required_env("OSS_BUCKET", app_env)
    region = _required_env("OSS_REGION", app_env)
    public_endpoint = env_runtime.env_value("OSS_PUBLIC_ENDPOINT", app_env) or ""
    endpoint_override = normalize_oss_endpoint(env_runtime.env_value("OSS_ENDPOINT", app_env) or "")
    endpoint = endpoint_override or f"oss-{region}.aliyuncs.com"
    endpoint_style = (
        "custom_domain"
        if public_endpoint and endpoint_override == normalize_oss_endpoint(public_endpoint)
        else "virtual_host"
    )

    return AliyunOSSConfig(
        bucket=bucket,
        region=region,
        access_key_id=_required_env("OSS_ACCESS_KEY_ID", app_env),
        access_key_secret=_required_env("OSS_ACCESS_KEY_SECRET", app_env),
        project_root=_required_env("OSS_PROJECT_ROOT", app_env),
        endpoint=endpoint,
        endpoint_style=endpoint_style,
        scheme="https",
    )


def _default_key(*, source: Path, app_env: dict[str, str], key_prefix: str | None) -> str:
    output_prefix = (env_runtime.env_value("OSS_OUTPUT_PREFIX", app_env) or "ai-jobs").strip().strip("/")
    clean_key_prefix = (key_prefix or DEFAULT_UPLOAD_PREFIX).strip().strip("/")
    parts = [part for part in (output_prefix, clean_key_prefix) if part]
    parts.append(f"{int(time.time())}-{uuid.uuid4().hex}")
    parts.append(source.name)
    return "/".join(parts)


def upload_image(
    *,
    image: str,
    content_type: str | None,
    app_env: dict[str, str],
    key: str | None = None,
    key_prefix: str | None = None,
    signed_url_expires_seconds: int = 3600,
    client: AliyunOSSClient | None = None,
) -> dict[str, Any]:
    source = _resolve_repo_path(image)
    if not source.is_file():
        raise FlowError(f"image not found: {source}", exit_code=2)

    data = source.read_bytes()
    resolved_content_type = image_content_type(source, content_type)
    config = client.config if client is not None else load_aliyun_oss_config(app_env)
    oss_client = client or AliyunOSSClient(config)
    object_key = oss_client.object_key(key or _default_key(source=source, app_env=app_env, key_prefix=key_prefix))
    try:
        oss_client.put_object(object_key, data, content_type=resolved_content_type)
        signed_url = oss_client.signed_get_url(object_key, expires_seconds=signed_url_expires_seconds)
    except AliyunOSSError as exc:
        raise FlowError(f"failed to upload image to Aliyun OSS or generate signed URL: {exc}", exit_code=4) from exc

    content_hash = sha256_digest(data)
    url_ref = oss_url_ref_from_output_object(
        bucket=config.bucket,
        region=config.region,
        key=object_key,
        content_type=resolved_content_type,
        content_hash=content_hash,
        public_endpoint=env_runtime.env_value("OSS_PUBLIC_ENDPOINT", app_env) or None,
    )
    return {
        "provider": "aliyun_oss",
        "bucket": config.bucket,
        "region": config.region,
        "key": object_key,
        "content_type": resolved_content_type,
        "content_hash": content_hash,
        "sha256": bare_sha256(data),
        "content_size_bytes": len(data),
        "signed_url": signed_url,
        "signed_url_expires_seconds": signed_url_expires_seconds,
        "url_ref": url_ref,
    }


def delete_uploaded_image(
    *,
    upload_result: dict[str, Any],
    app_env: dict[str, str],
    client: AliyunOSSClient | None = None,
) -> None:
    config = client.config if client is not None else load_aliyun_oss_config(app_env)
    if upload_result.get("provider") != "aliyun_oss":
        raise FlowError("uploaded image cleanup requires provider=aliyun_oss", exit_code=4)
    if upload_result.get("bucket") != config.bucket:
        raise FlowError("uploaded image cleanup bucket does not match configured OSS bucket", exit_code=4)
    if upload_result.get("region") != config.region:
        raise FlowError("uploaded image cleanup region does not match configured OSS region", exit_code=4)
    key = upload_result.get("key")
    if not isinstance(key, str) or not key.strip():
        raise FlowError("uploaded image cleanup requires key", exit_code=4)
    oss_client = client or AliyunOSSClient(config)
    try:
        oss_client.delete_object(key)
    except AliyunOSSError as exc:
        raise FlowError(f"failed to delete uploaded Aliyun OSS image: {exc}", exit_code=4) from exc


def run(
    *,
    confirm_upload: bool,
    image: str,
    content_type: str | None,
    key: str | None,
    key_prefix: str | None,
    signed_url_expires_seconds: int,
    output_mode: str = "table",
    env_file: str | None = None,
) -> None:
    if not confirm_upload:
        raise FlowError("OSS image upload requires --confirm-upload", exit_code=2)
    if output_mode not in OUTPUT_MODES:
        raise FlowError(f"output mode must be one of {sorted(OUTPUT_MODES)}, got {output_mode!r}", exit_code=2)

    app_env = env_runtime.load_app_env(env_file, root_dir=ROOT_DIR)
    result = upload_image(
        image=image,
        content_type=content_type,
        app_env=app_env,
        key=key,
        key_prefix=key_prefix,
        signed_url_expires_seconds=signed_url_expires_seconds,
    )

    if output_mode == "json":
        formatters.print_json(result)
    elif output_mode == "url-ref-json":
        formatters.print_json(result["url_ref"])
    elif output_mode == "poster-args":
        ref = result["url_ref"]
        print(
            " ".join(
                [
                    "--reference-public-url",
                    shlex.quote(str(ref["public_url"])),
                    "--reference-internal-url",
                    shlex.quote(str(ref["internal_url"])),
                    "--reference-content-type",
                    shlex.quote(str(ref["content_type"])),
                    "--reference-sha256",
                    shlex.quote(str(ref["sha256"])),
                ]
            )
        )
    else:
        formatters.section("Aliyun OSS Image Upload")
        formatters.event("OK", "upload", f"bucket={result['bucket']} region={result['region']} key={result['key']}")
        formatters.print_table(
            [
                {
                    "bucket": result["bucket"],
                    "region": result["region"],
                    "key": result["key"],
                    "content_type": result["content_type"],
                    "sha256": result["sha256"],
                    "signed_url": result["signed_url"],
                    "public_url": result["url_ref"]["public_url"],
                }
            ],
            columns=["bucket", "region", "key", "content_type", "sha256", "signed_url", "public_url"],
        )
