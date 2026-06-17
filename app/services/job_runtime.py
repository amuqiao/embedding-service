import json
from typing import Any

from app.core.config import settings
from app.core.exceptions import AppError
from app.integrations.storage import storage
from app.models.job import AIJob, AIJobWorkItem


def _bucket(job: AIJob) -> str:
    return job.output_oss_bucket or (job.output_payload or {}).get("oss_bucket") or settings.OSS_BUCKET or "local-dev"


def _region(job: AIJob) -> str:
    return job.output_oss_region or (job.output_payload or {}).get("oss_region") or settings.OSS_REGION or "local"


def _runtime_prefix(job: AIJob) -> str:
    prefix = job.output_oss_prefix or (job.output_payload or {}).get("oss_prefix") or f"ai-jobs/{job.id}/"
    return f"{prefix.strip('/')}/runtime"


def write_runtime_json(job: AIJob, name: str, payload: dict[str, Any]) -> dict[str, Any]:
    key = f"{_runtime_prefix(job)}/{name.strip('/')}.json"
    content = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    stored = storage.write_text(bucket=_bucket(job), key=key, region=_region(job), content=content)
    return {"storage": "oss_object", "type": "json", **stored}


def read_runtime_json(ref: dict[str, Any] | None) -> dict[str, Any]:
    if not ref:
        return {}
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
    input_ref = getattr(job, "input_ref", None)
    if input_ref:
        return read_runtime_json(input_ref)
    input_payload = getattr(job, "input_payload", None) or {}
    return input_payload.get("job_params") or input_payload


def prompt_payload_from_job(job: AIJob) -> dict[str, Any]:
    prompt_ref = getattr(job, "prompt_ref", None)
    if prompt_ref:
        return read_runtime_json(prompt_ref)
    return getattr(job, "prompt_payload", None) or {}


def work_item_payload(item: AIJobWorkItem) -> dict[str, Any]:
    input_ref = getattr(item, "input_ref", None)
    if input_ref:
        return read_runtime_json(input_ref)
    return getattr(item, "input_payload", None) or {}
