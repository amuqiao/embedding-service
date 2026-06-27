from __future__ import annotations

from dataclasses import dataclass
import json
import time
import uuid
from pathlib import Path
from typing import Any
import urllib.error
import urllib.request
from urllib.parse import quote, urlsplit

from app.core.exceptions import AppError
from app.integrations.aliyun_oss import AliyunOSSClient, AliyunOSSError
from app.integrations.image import (
    TRANSPARENT_REFERENCE_ALLOWED_CONTENT_TYPES,
    validate_transparent_reference_image,
)
from app.integrations.object_storage.aliyun_url import parse_aliyun_oss_url
from scripts.jobs import formatters
from scripts.real_flow.flows import llm_job_billing, oss_image_upload
from scripts.verify import image_inspect

FlowError = llm_job_billing.FlowError
ROOT_DIR = llm_job_billing.ROOT_DIR
DEFAULT_REFERENCE_IMAGE = ".data/title/英语.png"
DEFAULT_OUTPUT_DIR = ".data/real-flow/poster-title-image"
DEFAULT_JOB_TYPE = "poster_title_image"
DEFAULT_IMAGE_MODEL_ID = "gpt-image-2"
DEFAULT_BUCKET = "local-dev"
DEFAULT_REGION = "local"
ALLOWED_CONTENT_TYPES = TRANSPARENT_REFERENCE_ALLOWED_CONTENT_TYPES
SCRIPT_ENV_REFERENCE_PUBLIC_URL = "POSTER_TITLE_IMAGE_REFERENCE_PUBLIC_URL"
SCRIPT_ENV_REFERENCE_INTERNAL_URL = "POSTER_TITLE_IMAGE_REFERENCE_INTERNAL_URL"
SCRIPT_ENV_REFERENCE_CONTENT_TYPE = "POSTER_TITLE_IMAGE_REFERENCE_CONTENT_TYPE"
SCRIPT_ENV_REFERENCE_SHA256 = "POSTER_TITLE_IMAGE_REFERENCE_SHA256"


@dataclass(frozen=True)
class ReferenceImageResolution:
    ref: dict[str, str]
    uploaded_image: dict[str, Any] | None = None


def _resolve_repo_path(value: str) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return ROOT_DIR / path


def _content_type(path: Path, explicit: str | None) -> str:
    return oss_image_upload.image_content_type(path, explicit)


def _bare_sha256(data: bytes) -> str:
    return oss_image_upload.bare_sha256(data)


def _required_str(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise FlowError(f"items_json item.{field} is required", exit_code=2)
    return value


def _optional_str(raw_item: dict[str, Any], field: str, default: str) -> str:
    if field not in raw_item:
        return default
    return _required_str(raw_item[field], field=field)


def _optional_draw_count(raw_item: dict[str, Any], default: int) -> int:
    if "draw_count" not in raw_item:
        return default
    value = raw_item["draw_count"]
    if isinstance(value, bool) or not isinstance(value, int):
        raise FlowError("items_json item.draw_count must be an integer", exit_code=2)
    if value < 1 or value > 4:
        raise FlowError("items_json item.draw_count must be between 1 and 4", exit_code=2)
    return value


def _validate_reference_input_bytes(data: bytes, *, content_type: str) -> None:
    try:
        validate_transparent_reference_image(data, content_type=content_type)
    except AppError as exc:
        raise FlowError(f"poster_title_image reference image invalid: {exc.message}", exit_code=2) from exc


def _aliyun_oss_url_ref(
    *,
    bucket: str,
    region: str,
    key: str,
    content_type: str,
    sha256: str,
) -> dict[str, str]:
    encoded_key = quote(key.lstrip("/"), safe="/")
    return {
        "public_url": f"https://{bucket}.oss-{region}.aliyuncs.com/{encoded_key}",
        "internal_url": f"https://{bucket}.oss-{region}-internal.aliyuncs.com/{encoded_key}",
        "content_type": content_type,
        "sha256": sha256,
    }


def explicit_reference_image(
    *,
    public_url: str | None,
    internal_url: str | None,
    sha256: str | None,
    content_type: str | None,
) -> dict[str, str] | None:
    provided = [public_url, internal_url, sha256]
    if not any(provided):
        return None
    if not all(provided):
        raise FlowError(
            "--reference-public-url, --reference-internal-url and --reference-sha256 must be provided together",
            exit_code=2,
        )
    if content_type is None:
        raise FlowError("--reference-content-type is required when explicit OSS URL Ref options are used", exit_code=2)
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise FlowError(f"--reference-content-type must be one of {sorted(ALLOWED_CONTENT_TYPES)}", exit_code=2)
    assert public_url is not None
    assert internal_url is not None
    assert sha256 is not None
    return {
        "public_url": public_url,
        "internal_url": internal_url,
        "content_type": content_type,
        "sha256": sha256.removeprefix("sha256:"),
    }


def stage_local_reference_image(
    *,
    reference_image: str,
    content_type: str | None,
    app_env: dict[str, str],
) -> dict[str, str]:
    storage_backend = llm_job_billing.env_value("STORAGE_BACKEND", app_env) or "local"
    if storage_backend != "local":
        raise FlowError(
            "local reference staging requires STORAGE_BACKEND=local; pass explicit OSS URL Ref options instead",
            exit_code=2,
        )
    source = _resolve_repo_path(reference_image)
    if not source.is_file():
        raise FlowError(f"reference image not found: {source}", exit_code=2)
    data = source.read_bytes()
    resolved_content_type = _content_type(source, content_type)
    _validate_reference_input_bytes(data, content_type=resolved_content_type)
    bucket = llm_job_billing.env_value("OSS_BUCKET", app_env) or DEFAULT_BUCKET
    region = llm_job_billing.env_value("OSS_REGION", app_env) or DEFAULT_REGION
    storage_root = _resolve_repo_path(
        llm_job_billing.env_value("LOCAL_OBJECT_STORAGE_PATH", app_env) or "storage/objects"
    )
    key = f"real-flow/poster-title-image/reference/{int(time.time())}-{uuid.uuid4().hex}/{source.name}"
    storage_root_resolved = storage_root.resolve()
    storage_bucket_root = (storage_root / bucket).resolve()
    if storage_bucket_root != storage_root_resolved and storage_root_resolved not in storage_bucket_root.parents:
        raise FlowError("OSS_BUCKET resolves outside LOCAL_OBJECT_STORAGE_PATH", exit_code=2)
    target = (storage_bucket_root / key).resolve()
    if target != storage_bucket_root and storage_bucket_root not in target.parents:
        raise FlowError("resolved local storage path escapes bucket root", exit_code=2)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return _aliyun_oss_url_ref(
        bucket=bucket,
        region=region,
        key=key,
        content_type=resolved_content_type,
        sha256=_bare_sha256(data),
    )


def resolve_reference_image(
    *,
    reference_image: str,
    reference_public_url: str | None,
    reference_internal_url: str | None,
    reference_sha256: str | None,
    reference_content_type: str | None,
    app_env: dict[str, str],
    confirm_upload: bool,
) -> ReferenceImageResolution:
    explicit = explicit_reference_image(
        public_url=reference_public_url,
        internal_url=reference_internal_url,
        sha256=reference_sha256,
        content_type=reference_content_type,
    )
    if explicit is not None:
        return ReferenceImageResolution(ref=explicit)
    storage_backend = llm_job_billing.env_value("STORAGE_BACKEND", app_env) or "local"
    if storage_backend == "aliyun_oss":
        if not confirm_upload:
            raise FlowError("poster title image Aliyun OSS reference upload requires --confirm-upload", exit_code=2)
        source = _resolve_repo_path(reference_image)
        if not source.is_file():
            raise FlowError(f"reference image not found: {source}", exit_code=2)
        resolved_content_type = _content_type(source, reference_content_type)
        _validate_reference_input_bytes(source.read_bytes(), content_type=resolved_content_type)
        upload = oss_image_upload.upload_image(
            image=str(source),
            content_type=reference_content_type,
            app_env=app_env,
            key_prefix="real-flow/poster-title-image/reference",
        )
        return ReferenceImageResolution(ref=upload["url_ref"], uploaded_image=upload)
    return ReferenceImageResolution(
        ref=stage_local_reference_image(
            reference_image=reference_image,
            content_type=reference_content_type,
            app_env=app_env,
        )
    )


def reference_image_ref(
    *,
    reference_image: str,
    reference_public_url: str | None,
    reference_internal_url: str | None,
    reference_sha256: str | None,
    reference_content_type: str | None,
    app_env: dict[str, str],
) -> dict[str, str]:
    return resolve_reference_image(
        reference_image=reference_image,
        reference_public_url=reference_public_url,
        reference_internal_url=reference_internal_url,
        reference_sha256=reference_sha256,
        reference_content_type=reference_content_type,
        app_env=app_env,
        confirm_upload=True,
    ).ref


def resolved_reference_options(
    *,
    reference_public_url: str | None,
    reference_internal_url: str | None,
    reference_sha256: str | None,
    reference_content_type: str | None,
    script_env: dict[str, str],
) -> tuple[str | None, str | None, str | None, str | None]:
    cli_ref_values = [reference_public_url, reference_internal_url, reference_sha256]
    if any(cli_ref_values):
        return reference_public_url, reference_internal_url, reference_sha256, reference_content_type

    env_public_url = llm_job_billing.env_value(SCRIPT_ENV_REFERENCE_PUBLIC_URL, script_env)
    env_internal_url = llm_job_billing.env_value(SCRIPT_ENV_REFERENCE_INTERNAL_URL, script_env)
    env_sha256 = llm_job_billing.env_value(SCRIPT_ENV_REFERENCE_SHA256, script_env)
    if not any([env_public_url, env_internal_url, env_sha256]):
        return reference_public_url, reference_internal_url, reference_sha256, reference_content_type
    return (
        env_public_url,
        env_internal_url,
        env_sha256,
        reference_content_type or llm_job_billing.env_value(SCRIPT_ENV_REFERENCE_CONTENT_TYPE, script_env),
    )


def _cleanup_uploaded_reference(uploaded_image: dict[str, Any], app_env: dict[str, str]) -> None:
    oss_image_upload.delete_uploaded_image(upload_result=uploaded_image, app_env=app_env)


def _should_cleanup_uploaded_reference(*, create_attempted: bool, terminal_job: dict[str, Any] | None) -> bool:
    return not create_attempted or terminal_job is not None


def _cleanup_failure_message(original: BaseException, cleanup_exc: BaseException) -> str:
    return (
        f"{original}; uploaded reference cleanup failed: {cleanup_exc}"
    )


def load_items_json(path: str) -> list[dict[str, Any]]:
    source = _resolve_repo_path(path)
    if not source.is_file():
        raise FlowError(f"items json not found: {source}", exit_code=2)
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise FlowError(f"items json is invalid JSON: {exc}", exit_code=2) from exc
    if isinstance(raw, dict):
        raw_items = raw.get("items")
    else:
        raw_items = raw
    if not isinstance(raw_items, list) or not raw_items:
        raise FlowError("items json must be a non-empty array or an object with items[]", exit_code=2)
    return raw_items


def _item_reference_input(raw_item: dict[str, Any]) -> dict[str, Any]:
    reference = raw_item.get("reference")
    if reference is None:
        reference = raw_item.get("reference_image")
    if not isinstance(reference, dict):
        raise FlowError("items_json item.reference is required", exit_code=2)
    return reference


def _resolve_item_reference(
    reference: dict[str, Any],
    *,
    app_env: dict[str, str],
    confirm_upload: bool,
) -> ReferenceImageResolution:
    if {"public_url", "internal_url", "sha256"}.issubset(reference):
        explicit = explicit_reference_image(
            public_url=str(reference.get("public_url")),
            internal_url=str(reference.get("internal_url")),
            sha256=str(reference.get("sha256")),
            content_type=str(reference.get("content_type")) if reference.get("content_type") is not None else None,
        )
        assert explicit is not None
        return ReferenceImageResolution(ref=explicit)
    image = _required_str(reference.get("image"), field="reference.image")
    content_type = reference.get("content_type")
    return resolve_reference_image(
        reference_image=image,
        reference_public_url=None,
        reference_internal_url=None,
        reference_sha256=None,
        reference_content_type=str(content_type) if content_type is not None else None,
        app_env=app_env,
        confirm_upload=confirm_upload,
    )


def build_items_from_json(
    raw_items: list[dict[str, Any]],
    *,
    app_env: dict[str, str],
    confirm_upload: bool,
    size: str,
    quality: str,
    draw_count: int,
    model_id: str | None = None,
) -> tuple[list[dict[str, Any]], list[ReferenceImageResolution]]:
    items: list[dict[str, Any]] = []
    resolutions: list[ReferenceImageResolution] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            raise FlowError("items json entries must be objects", exit_code=2)
        resolution = _resolve_item_reference(
            _item_reference_input(raw),
            app_env=app_env,
            confirm_upload=confirm_upload,
        )
        resolutions.append(resolution)
        item_model_id = _optional_str(raw, "model_id", model_id) if model_id is not None or "model_id" in raw else None
        item = {
            "item_id": _required_str(raw.get("item_id"), field="item_id"),
            "language": _required_str(raw.get("language"), field="language"),
            "title_text": _required_str(raw.get("title_text"), field="title_text"),
            "model_options": {
                "size": _optional_str(raw, "size", size),
                "quality": _optional_str(raw, "quality", quality),
                "draw_count": _optional_draw_count(raw, draw_count),
                "background": "transparent",
                "output_format": "png",
            },
            "reference_image": resolution.ref,
        }
        if item_model_id is not None:
            item["model_id"] = item_model_id
        items.append(item)
    return items, resolutions


def build_job_payload(
    *,
    items: list[dict[str, Any]],
    client_request_id: str | None,
) -> dict[str, Any]:
    return {
        "client_request_id": client_request_id or f"real-flow-poster-title-image-{uuid.uuid4()}",
        "job_type": DEFAULT_JOB_TYPE,
        "job_params": {"items": items},
        "metadata": {"source": "scripts/real-flow.sh poster-title-image"},
        "options": {"priority": "normal", "idempotency_mode": "reject_duplicate"},
    }


def _result_objects(job: dict[str, Any]) -> list[dict[str, Any]]:
    result = job.get("job_result")
    if not isinstance(result, dict):
        return []
    objects: list[dict[str, Any]] = []
    for item in result.get("items", []):
        if not isinstance(item, dict):
            continue
        for index, image in enumerate(item.get("images", []), start=1):
            if isinstance(image, dict) and isinstance(image.get("object"), dict):
                objects.append(
                    {
                        "item_id": item.get("item_id"),
                        "language": item.get("language"),
                        "image_index": index,
                        **image["object"],
                    }
                )
    return objects


def _display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT_DIR.resolve()))
    except ValueError:
        return str(path)


def _safe_path_segment(value: Any, *, fallback: str) -> str:
    raw = str(value or "").strip()
    cleaned = "".join(char if char.isalnum() or char in {"-", "_", "."} else "_" for char in raw)
    cleaned = cleaned.strip("._")
    return cleaned or fallback


def _filename_from_output_object(output: dict[str, Any]) -> str:
    public_url = output.get("public_url")
    if isinstance(public_url, str):
        name = Path(urlsplit(public_url).path).name
        if name:
            return _safe_path_segment(name, fallback="title-layer.png")
    content_type = output.get("content_type")
    if content_type == "image/jpeg":
        return "title-layer.jpg"
    if content_type == "image/webp":
        return "title-layer.webp"
    return "title-layer.png"


def _download_target(
    *,
    output_dir: Path,
    job_id: str,
    output: dict[str, Any],
) -> Path:
    item_id = _safe_path_segment(output.get("item_id"), fallback="item")
    language = _safe_path_segment(output.get("language"), fallback="lang")
    image_index = int(output.get("image_index") or 1)
    filename = _filename_from_output_object(output)
    return output_dir / job_id / f"{item_id}-{language}" / f"{image_index:02d}-{filename}"


def _download_url(url: str, *, timeout_seconds: int = 30) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "real-flow.sh/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        raise FlowError(f"download failed: status={exc.code}", exit_code=4) from exc
    except urllib.error.URLError as exc:
        raise FlowError(f"download failed: {exc.reason}", exit_code=4) from exc


def _local_storage_path(*, app_env: dict[str, str], output: dict[str, Any]) -> Path:
    public_url = output.get("public_url")
    if not isinstance(public_url, str):
        raise FlowError("output object public_url is required for local download", exit_code=4)
    try:
        location = parse_aliyun_oss_url(public_url)
    except AppError as exc:
        raise FlowError(f"output public_url is not a supported OSS URL: {exc.message}", exit_code=4) from exc

    storage_root = _resolve_repo_path(
        llm_job_billing.env_value("LOCAL_OBJECT_STORAGE_PATH", app_env) or "storage/objects"
    )
    root_resolved = storage_root.resolve()
    bucket_root = (storage_root / location.bucket).resolve()
    if bucket_root != root_resolved and root_resolved not in bucket_root.parents:
        raise FlowError("output OSS bucket resolves outside LOCAL_OBJECT_STORAGE_PATH", exit_code=4)
    path = (bucket_root / location.key).resolve()
    if path != bucket_root and bucket_root not in path.parents:
        raise FlowError("output OSS object path escapes bucket root", exit_code=4)
    return path


def _read_local_output(*, app_env: dict[str, str], output: dict[str, Any]) -> bytes:
    path = _local_storage_path(app_env=app_env, output=output)
    if not path.is_file():
        raise FlowError(f"local output object not found: {_display_path(path)}", exit_code=4)
    return path.read_bytes()


def _signed_download_url(
    *,
    app_env: dict[str, str],
    output: dict[str, Any],
    expires_seconds: int,
) -> str:
    public_url = output.get("public_url")
    if not isinstance(public_url, str):
        raise FlowError("output object public_url is required for signed download", exit_code=4)
    try:
        location = parse_aliyun_oss_url(public_url)
    except AppError as exc:
        raise FlowError(f"output public_url is not a supported OSS URL: {exc.message}", exit_code=4) from exc

    config = oss_image_upload.load_aliyun_oss_config(app_env)
    if location.bucket != config.bucket or location.region != config.region:
        raise FlowError(
            "output OSS URL does not match configured OSS_BUCKET/OSS_REGION",
            exit_code=4,
        )
    try:
        return AliyunOSSClient(config).signed_get_url(location.key, expires_seconds=expires_seconds)
    except AliyunOSSError as exc:
        raise FlowError(f"failed to generate signed output URL: {exc}", exit_code=4) from exc


def _read_remote_output(
    *,
    app_env: dict[str, str],
    output: dict[str, Any],
    signed_url_expires_seconds: int,
) -> tuple[bytes, str]:
    public_url = output.get("public_url")
    if not isinstance(public_url, str):
        raise FlowError("output object public_url is required for download", exit_code=4)
    try:
        return _download_url(public_url), "public_url"
    except FlowError:
        storage_backend = llm_job_billing.env_value("STORAGE_BACKEND", app_env) or "local"
        if storage_backend != "aliyun_oss":
            raise
        signed_url = _signed_download_url(
            app_env=app_env,
            output=output,
            expires_seconds=signed_url_expires_seconds,
        )
        return _download_url(signed_url), "signed_url"


def _read_output_bytes(
    *,
    app_env: dict[str, str],
    output: dict[str, Any],
    signed_url_expires_seconds: int,
) -> tuple[bytes, str]:
    storage_backend = llm_job_billing.env_value("STORAGE_BACKEND", app_env) or "local"
    if storage_backend == "local":
        return _read_local_output(app_env=app_env, output=output), "local_storage"
    return _read_remote_output(
        app_env=app_env,
        output=output,
        signed_url_expires_seconds=signed_url_expires_seconds,
    )


def _inspect_downloaded_output(path: Path, *, output: dict[str, Any]) -> dict[str, Any]:
    try:
        result = image_inspect.inspect_source(str(path))
        image_inspect._check_expectations(
            [result],
            require_transparent=False,
            require_opaque=False,
            require_transparent_background=True,
        )
    except Exception as exc:
        raise FlowError(
            "downloaded output image inspection failed "
            f"for item={output.get('item_id')} image={output.get('image_index')}: {exc}",
            exit_code=4,
        ) from exc
    return {
        "passed": True,
        "require_transparent_background": True,
        "result": result,
    }


def download_output_artifacts(
    *,
    job: dict[str, Any],
    app_env: dict[str, str],
    output_dir: str,
    signed_url_expires_seconds: int,
) -> list[dict[str, Any]]:
    job_id = str(job.get("job_id") or "").strip()
    if not job_id:
        raise FlowError("job_id is required before downloading outputs", exit_code=4)
    target_root = _resolve_repo_path(output_dir)
    artifacts: list[dict[str, Any]] = []
    for output in _result_objects(job):
        expected_sha256 = output.get("sha256")
        if not isinstance(expected_sha256, str) or not expected_sha256.strip():
            raise FlowError("output object sha256 is required before downloading outputs", exit_code=4)
        data, method = _read_output_bytes(
            app_env=app_env,
            output=output,
            signed_url_expires_seconds=signed_url_expires_seconds,
        )
        actual_sha256 = _bare_sha256(data)
        if actual_sha256 != expected_sha256.removeprefix("sha256:"):
            raise FlowError(
                f"downloaded output sha256 mismatch for item={output.get('item_id')} image={output.get('image_index')}",
                exit_code=4,
            )
        target = _download_target(output_dir=target_root, job_id=job_id, output=output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        inspection = _inspect_downloaded_output(target, output=output)
        artifacts.append(
            {
                "item_id": output.get("item_id"),
                "language": output.get("language"),
                "image_index": output.get("image_index"),
                "content_type": output.get("content_type"),
                "sha256": actual_sha256,
                "sha256_verified": True,
                "download_method": method,
                "local_path": _display_path(target),
                "image_inspection": inspection,
            }
        )
    return artifacts


def _image_inspection_summary(artifacts: list[dict[str, Any]]) -> dict[str, Any]:
    inspections = [artifact.get("image_inspection") for artifact in artifacts]
    passed_count = sum(1 for inspection in inspections if isinstance(inspection, dict) and inspection.get("passed") is True)
    return {
        "enabled": True,
        "require_transparent_background": True,
        "checked_count": len(inspections),
        "passed_count": passed_count,
        "failed_count": len(inspections) - passed_count,
    }


def summarize(
    job: dict[str, Any],
    billing: dict[str, Any],
    *,
    artifacts: list[dict[str, Any]] | None = None,
    image_inspection_enabled: bool = False,
) -> dict[str, Any]:
    objects = _result_objects(job)
    summary = {
        "note": "summary is generated by scripts/real-flow.sh; raw HTTP envelopes are under responses",
        "job_id": job.get("job_id"),
        "job_status": job.get("job_status"),
        "job_type": job.get("job_type"),
        "cost": job.get("cost"),
        "billing_status": billing.get("status"),
        "currency": billing.get("currency"),
        "total_cost_amount": billing.get("total_cost_amount"),
        "usage_units": billing.get("usage_units"),
        "pricing_refs": billing.get("pricing_refs"),
        "ai_call_count": billing.get("ai_call_count"),
        "billable_call_count": billing.get("billable_call_count"),
        "failed_call_count": billing.get("failed_call_count"),
        "output_count": len(objects),
        "outputs": objects,
        "artifacts": artifacts or [],
    }
    if image_inspection_enabled:
        summary["image_inspection"] = _image_inspection_summary(artifacts or [])
    return summary


def conclusion(job: dict[str, Any], billing: dict[str, Any]) -> str:
    return (
        f"job={job.get('job_status')} billing={billing.get('status')} "
        f"cost={billing.get('total_cost_amount')} {billing.get('currency')} "
        f"outputs={len(_result_objects(job))}"
    )


def run(
    *,
    confirm_cost: bool,
    confirm_upload: bool,
    api_url: str | None,
    items_json: str | None,
    reference_image: str,
    reference_public_url: str | None,
    reference_internal_url: str | None,
    reference_sha256: str | None,
    reference_content_type: str | None,
    item_id: str,
    language: str,
    title_text: str,
    size: str,
    quality: str,
    draw_count: int,
    caller_id: str,
    timeout_seconds: int,
    poll_interval_seconds: float,
    client_request_id: str | None,
    json_output: bool,
    download_outputs: bool = False,
    output_dir: str = DEFAULT_OUTPUT_DIR,
    signed_url_expires_seconds: int = 3600,
    model_id: str | None = None,
) -> None:
    if not confirm_cost:
        raise FlowError("poster title image flow requires --confirm-cost", exit_code=2)
    app_env = llm_job_billing.load_env_file(ROOT_DIR / ".env")
    script_env = llm_job_billing.load_env_file(ROOT_DIR / "scripts/.env")
    base_url = llm_job_billing.resolved_api_url(api_url, app_env, script_env)
    api_prefix = (llm_job_billing.env_value("SERVICE_API_PREFIX", app_env) or llm_job_billing.DEFAULT_API_PREFIX).rstrip("/")
    jobs_url = f"{base_url}{api_prefix}/jobs"
    headers = llm_job_billing.build_headers(app_env, caller_id=caller_id)
    (
        resolved_reference_public_url,
        resolved_reference_internal_url,
        resolved_reference_sha256,
        resolved_reference_content_type,
    ) = resolved_reference_options(
        reference_public_url=reference_public_url,
        reference_internal_url=reference_internal_url,
        reference_sha256=reference_sha256,
        reference_content_type=reference_content_type,
        script_env=script_env,
    )
    resolutions: list[ReferenceImageResolution] = []
    create_attempted = False
    job_id: str | None = None
    terminal_job: dict[str, Any] | None = None
    try:
        if items_json:
            items, resolutions = build_items_from_json(
                load_items_json(items_json),
                app_env=app_env,
                confirm_upload=confirm_upload,
                model_id=model_id,
                size=size,
                quality=quality,
                draw_count=draw_count,
            )
        else:
            resolution = resolve_reference_image(
                reference_image=reference_image,
                reference_public_url=resolved_reference_public_url,
                reference_internal_url=resolved_reference_internal_url,
                reference_sha256=resolved_reference_sha256,
                reference_content_type=resolved_reference_content_type,
                app_env=app_env,
                confirm_upload=confirm_upload,
            )
            resolutions = [resolution]
            items = [
                {
                    "item_id": item_id,
                    "language": language,
                    "title_text": title_text,
                    "model_options": {
                        "size": size,
                        "quality": quality,
                        "draw_count": draw_count,
                        "background": "transparent",
                        "output_format": "png",
                    },
                    "reference_image": resolution.ref,
                }
            ]
            if model_id is not None:
                items[0]["model_id"] = model_id
        payload = build_job_payload(items=items, client_request_id=client_request_id)
        create_attempted = True
        create_envelope = llm_job_billing.request_json(jobs_url, method="POST", headers=headers, payload=payload)
        created = llm_job_billing.data_object(create_envelope, "job")
        job_id = str(created["job_id"])
        get_job_envelope = llm_job_billing.poll_job_envelope(
            jobs_url=jobs_url,
            job_id=job_id,
            headers=headers,
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
        )
        terminal_job = llm_job_billing.data_object(get_job_envelope, "job")
        billing_envelope = llm_job_billing.request_json(f"{jobs_url}/{job_id}/billing", method="GET", headers=headers)
        billing = llm_job_billing.data_object(billing_envelope, "billing")
    except Exception as exc:
        for resolution in resolutions:
            uploaded_image = resolution.uploaded_image
            if uploaded_image is not None and _should_cleanup_uploaded_reference(
                create_attempted=create_attempted,
                terminal_job=terminal_job,
            ):
                try:
                    _cleanup_uploaded_reference(uploaded_image, app_env)
                except Exception as cleanup_exc:
                    exit_code = exc.exit_code if isinstance(exc, FlowError) else 4
                    raise FlowError(_cleanup_failure_message(exc, cleanup_exc), exit_code=exit_code) from exc
        raise
    for resolution in resolutions:
        if resolution.uploaded_image is not None:
            _cleanup_uploaded_reference(resolution.uploaded_image, app_env)
    artifacts = (
        download_output_artifacts(
            job=terminal_job,
            app_env=app_env,
            output_dir=output_dir,
            signed_url_expires_seconds=signed_url_expires_seconds,
        )
        if download_outputs
        else []
    )
    summary = summarize(terminal_job, billing, artifacts=artifacts, image_inspection_enabled=download_outputs)
    if json_output:
        formatters.print_json(
            {
                "conclusion": conclusion(terminal_job, billing),
                "summary": summary,
                "responses": {
                    "create_job": create_envelope,
                    "get_job": get_job_envelope,
                    "get_billing": billing_envelope,
                },
            }
        )
    else:
        formatters.section("Poster Title Image Real Flow")
        formatters.event("OK", "job", f"id={job_id} status={terminal_job.get('job_status')}")
        formatters.event(
            "OK",
            "billing",
            f"status={billing.get('status')} total={billing.get('total_cost_amount')} {billing.get('currency')}",
        )
        formatters.print_table(
            [summary],
            [
                ("job_id", "job_id"),
                ("job_status", "job"),
                ("billing_status", "billing"),
                ("total_cost_amount", "cost"),
                ("currency", "currency"),
                ("output_count", "outputs"),
            ],
        )
        outputs = summary["outputs"]
        if outputs:
            formatters.print_table(
                outputs,
                [
                    ("item_id", "item"),
                    ("language", "lang"),
                    ("content_type", "content_type"),
                    ("sha256", "sha256"),
                    ("public_url", "public_url"),
                ],
            )
        if artifacts:
            formatters.print_table(
                artifacts,
                [
                    ("item_id", "item"),
                    ("language", "lang"),
                    ("image_index", "image"),
                    ("download_method", "method"),
                    ("local_path", "local_path"),
                ],
            )
    if terminal_job.get("job_status") != "succeeded":
        raise FlowError(f"job {job_id} finished with {terminal_job.get('job_status')}", exit_code=4)
