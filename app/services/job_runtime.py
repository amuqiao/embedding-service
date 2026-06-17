import hashlib
import json
import uuid
from typing import Any

from app.core.config import settings
from app.core.exceptions import AppError
from app.integrations.storage import storage
from app.models.job import AIJob, AIJobWorkItem


def payload_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def configured_output_target(job_id: uuid.UUID) -> dict[str, str]:
    root = settings.OSS_OUTPUT_PREFIX.strip("/")
    prefix = f"{root}/{job_id}/" if root else f"{job_id}/"
    return {
        "type": "oss_prefix",
        "oss_bucket": settings.OSS_BUCKET or "local-dev",
        "oss_prefix": prefix,
        "oss_region": settings.OSS_REGION or "local",
    }


def build_runtime_snapshot(
    *,
    job_type: str,
    job_params_hash: str,
    runtime_fields: dict[str, Any],
    output_target: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "job_type": job_type,
        "job_params_hash": job_params_hash,
        "runtime_fields": runtime_fields,
        "output_target": output_target,
    }


def _validate_output_target(target: Any) -> dict[str, Any]:
    if not isinstance(target, dict):
        raise AppError("RUNTIME_REF_INVALID", "运行时输出目标必须是 JSON object", status_code=500)
    for key in ("oss_bucket", "oss_prefix", "oss_region"):
        if not isinstance(target.get(key), str) or not target[key]:
            raise AppError("RUNTIME_REF_INVALID", f"运行时输出目标缺少 {key}", status_code=500)
    return target


def _runtime_prefix(output_target: dict[str, Any]) -> str:
    prefix = output_target["oss_prefix"]
    return f"{prefix.strip('/')}/runtime"


def write_runtime_json(job: AIJob, name: str, payload: dict[str, Any]) -> dict[str, Any]:
    output_target = output_target_from_job(job) if job.runtime_ref else configured_output_target(job.id)
    key = f"{_runtime_prefix(output_target)}/{name.strip('/')}.json"
    content = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    stored = storage.write_text(
        bucket=output_target["oss_bucket"],
        key=key,
        region=output_target["oss_region"],
        content=content,
    )
    return {"storage": "oss_object", "type": "json", **stored}


def read_runtime_json(ref: dict[str, Any] | None) -> dict[str, Any]:
    if not ref:
        raise AppError("RUNTIME_REF_MISSING", "运行时引用不存在", status_code=500)
    try:
        text = storage.read_text(bucket=ref["oss_bucket"], key=ref["oss_key"], region=ref["oss_region"])
        value = json.loads(text)
    except AppError:
        raise
    except Exception as exc:
        raise AppError("RUNTIME_REF_INVALID", "运行时引用读取失败", status_code=500) from exc
    if not isinstance(value, dict):
        raise AppError("RUNTIME_REF_INVALID", "运行时引用必须是 JSON object", status_code=500)
    return value


def job_params_from_job(job: AIJob) -> dict[str, Any]:
    params = read_runtime_json(job.job_params_ref)
    if not job.job_params_hash:
        raise AppError("RUNTIME_HASH_MISSING", "运行时参数 hash 不存在", status_code=500)
    actual_hash = payload_hash(params)
    if actual_hash != job.job_params_hash:
        raise AppError(
            "RUNTIME_HASH_MISMATCH",
            "运行时参数 hash 不匹配",
            status_code=500,
            details={"expected": job.job_params_hash, "actual": actual_hash},
        )
    return params


def runtime_snapshot_from_job(job: AIJob) -> dict[str, Any]:
    snapshot = read_runtime_json(job.runtime_ref)
    if not job.job_params_hash:
        raise AppError("RUNTIME_HASH_MISSING", "运行时参数 hash 不存在", status_code=500)
    if snapshot.get("job_type") != job.job_type:
        raise AppError("RUNTIME_REF_INVALID", "运行时快照 job_type 不匹配", status_code=500)
    if snapshot.get("job_params_hash") != job.job_params_hash:
        raise AppError("RUNTIME_HASH_MISMATCH", "运行时快照参数 hash 不匹配", status_code=500)
    return snapshot


def runtime_fields_from_job(job: AIJob) -> dict[str, Any]:
    fields = runtime_snapshot_from_job(job).get("runtime_fields")
    if not isinstance(fields, dict):
        raise AppError("RUNTIME_REF_INVALID", "运行时字段必须是 JSON object", status_code=500)
    return fields


def output_target_from_job(job: AIJob) -> dict[str, Any]:
    return _validate_output_target(runtime_snapshot_from_job(job).get("output_target"))


def model_id_from_job(job: AIJob) -> str | None:
    value = runtime_fields_from_job(job).get("model_id")
    return value if isinstance(value, str) and value else None


def prompt_payload_from_job(job: AIJob) -> dict[str, Any]:
    value = runtime_fields_from_job(job).get("prompt_payload")
    return value if isinstance(value, dict) else {}


def work_item_payload(item: AIJobWorkItem) -> dict[str, Any]:
    return read_runtime_json(item.input_ref)
