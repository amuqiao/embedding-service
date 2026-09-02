import hashlib
import json
import uuid
from typing import Any

from app.core.config import settings
from app.core.exceptions import AppError
from app.tools.private.storage import storage
from app.models.job import Job


def payload_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def configured_output_target(job_id: uuid.UUID) -> dict[str, str]:
    root = settings.storage.oss_output_prefix.strip("/")
    prefix = f"{root}/{job_id}/" if root else f"{job_id}/"
    return {
        "type": "oss_prefix",
        "oss_bucket": settings.storage.oss_bucket or "local-dev",
        "oss_prefix": prefix,
        "oss_region": settings.storage.oss_region or "local",
    }


def build_runtime_snapshot(
    *,
    job_type: str,
    job_params_hash: str,
    runtime_fields: dict[str, Any],
    output_target: dict[str, Any],
    workflow_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    snapshot = {
        "schema_version": 1,
        "job_type": job_type,
        "job_params_hash": job_params_hash,
        "runtime_fields": runtime_fields,
        "output_target": output_target,
    }
    if workflow_plan is not None:
        snapshot["workflow_plan"] = workflow_plan
    return snapshot


def _validate_output_target(target: Any) -> dict[str, Any]:
    if not isinstance(target, dict):
        raise AppError("RUNTIME_REF_INVALID", "运行时输出目标必须是 JSON object")
    for key in ("oss_bucket", "oss_prefix", "oss_region"):
        if not isinstance(target.get(key), str) or not target[key]:
            raise AppError("RUNTIME_REF_INVALID", f"运行时输出目标缺少 {key}")
    return target


def write_runtime_json(job: Job | None, name: str, payload: dict[str, Any]) -> dict[str, Any]:
    content = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    data = content.encode("utf-8")
    return {
        "storage": "db_inline",
        "type": "json",
        "name": name.strip("/"),
        "payload": payload,
        "content_hash": "sha256:" + hashlib.sha256(data).hexdigest(),
        "content_size_bytes": len(data),
    }


def read_runtime_json(ref: dict[str, Any] | None) -> dict[str, Any]:
    if not ref:
        raise AppError("RUNTIME_REF_MISSING", "运行时引用不存在")
    if ref.get("storage") == "db_inline":
        value = ref.get("payload")
        if not isinstance(value, dict):
            raise AppError("RUNTIME_REF_INVALID", "运行时内联引用必须包含 JSON object payload")
        return value
    try:
        text = storage.read_text(bucket=ref["oss_bucket"], key=ref["oss_key"], region=ref["oss_region"])
        value = json.loads(text)
    except AppError:
        raise
    except Exception as exc:
        raise AppError("RUNTIME_REF_INVALID", "运行时引用读取失败") from exc
    if not isinstance(value, dict):
        raise AppError("RUNTIME_REF_INVALID", "运行时引用必须是 JSON object")
    return value


def job_params_from_job(job: Job) -> dict[str, Any]:
    params = read_runtime_json(job.job_params_ref)
    if not job.job_params_hash:
        raise AppError("RUNTIME_HASH_MISSING", "运行时参数 hash 不存在")
    actual_hash = payload_hash(params)
    if actual_hash != job.job_params_hash:
        raise AppError(
            "RUNTIME_HASH_MISMATCH",
            "运行时参数 hash 不匹配",
            details={"expected": job.job_params_hash, "actual": actual_hash},
        )
    return params


def runtime_snapshot_from_job(job: Job) -> dict[str, Any]:
    snapshot = read_runtime_json(job.runtime_ref)
    if not job.job_params_hash:
        raise AppError("RUNTIME_HASH_MISSING", "运行时参数 hash 不存在")
    if snapshot.get("job_type") != job.job_type:
        raise AppError("RUNTIME_REF_INVALID", "运行时快照 job_type 不匹配")
    if snapshot.get("job_params_hash") != job.job_params_hash:
        raise AppError("RUNTIME_HASH_MISMATCH", "运行时快照参数 hash 不匹配")
    return snapshot


def runtime_fields_from_job(job: Job) -> dict[str, Any]:
    fields = runtime_snapshot_from_job(job).get("runtime_fields")
    if not isinstance(fields, dict):
        raise AppError("RUNTIME_REF_INVALID", "运行时字段必须是 JSON object")
    return fields


def workflow_plan_from_job(job: Job) -> dict[str, Any] | None:
    if not job.runtime_ref:
        return None
    plan = runtime_snapshot_from_job(job).get("workflow_plan")
    if plan is None:
        return None
    if not isinstance(plan, dict):
        raise AppError("RUNTIME_REF_INVALID", "workflow_plan must be a JSON object")
    if plan.get("kind") != "dag_lite":
        raise AppError("RUNTIME_REF_INVALID", "workflow_plan kind must be dag_lite")
    nodes = plan.get("nodes")
    if not isinstance(nodes, list):
        raise AppError("RUNTIME_REF_INVALID", "workflow_plan nodes must be a JSON array")
    return plan


def output_target_from_job(job: Job) -> dict[str, Any]:
    return _validate_output_target(runtime_snapshot_from_job(job).get("output_target"))


def model_id_from_job(job: Job) -> str | None:
    value = runtime_fields_from_job(job).get("model_id")
    return value if isinstance(value, str) and value else None


def prompt_payload_from_job(job: Job) -> dict[str, Any]:
    value = runtime_fields_from_job(job).get("prompt_payload")
    return value if isinstance(value, dict) else {}


def ai_billing_scope_id_from_job(job: Job) -> uuid.UUID:
    return job.root_job_id or job.id
