import hashlib
import ipaddress
import json
import uuid
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError, NotFoundAppError, ValidationAppError
from app.core.config import settings
from app.core.model_registry import get_enabled_model
from app.core.prompt_templates import get_template
from app.integrations.storage import sha256_digest, storage
from app.models.job import AIJob
from app.repositories.job_repo import JobRepo
from app.schemas.jobs import CreateJobRequest, JobResult, JobStatusResponse


def _status_url(job_id: uuid.UUID) -> str:
    return f"{settings.SERVICE_API_PREFIX}/jobs/{job_id}"


def _configured_oss_bucket() -> str:
    return settings.OSS_BUCKET or "local-dev"


def _configured_oss_region() -> str:
    return settings.OSS_REGION or "local"


def _job_output_payload(job_id: uuid.UUID) -> dict[str, str]:
    root = settings.OSS_OUTPUT_PREFIX.strip("/")
    prefix = f"{root}/{job_id}/" if root else f"{job_id}/"
    return {
        "type": "oss_prefix",
        "oss_bucket": _configured_oss_bucket(),
        "oss_prefix": prefix,
        "oss_region": _configured_oss_region(),
    }


def _request_fingerprint(payload: CreateJobRequest, job_params: dict[str, Any]) -> str:
    body = {
        "job_type": payload.job_type,
        "job_params": job_params,
        "callback": payload.callback.model_dump() if payload.callback else None,
        "metadata": payload.metadata,
        "options": payload.options.model_dump() if payload.options else None,
    }
    encoded = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _job_to_response(job: AIJob) -> JobStatusResponse:
    input_payload = job.input_payload or {}
    result_payload = job.public_result_payload if job.public_result_payload is not None else job.result_payload
    return JobStatusResponse(
        job_id=job.id,
        client_request_id=job.client_request_id,
        job_type=job.job_type,
        status=job.status,
        progress={
            "percent": job.progress_percent,
            "message": job.progress_text,
            "stage": job.progress_stage,
        },
        result=result_payload,
        error=job.error_payload,
        callback={
            "status": job.callback_status,
            "attempts": job.callback_attempts,
            "next_retry_at": job.callback_next_retry_at,
            "last_error": job.callback_last_error,
        },
        metadata=job.public_metadata or input_payload.get("metadata") or {},
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
    )


def _validate_prompt(job_type: str, prompt_payload: dict[str, Any]) -> None:
    template = get_template(job_type)
    if not template:
        raise ValidationAppError("INVALID_JOB_TYPE", f"不支持的 job_type: {job_type}")
    expected = {block.key: block.role for block in template.prompt_blocks}
    received = prompt_payload.get("blocks") or []
    keys = [block.get("key") for block in received]
    if len(keys) != len(set(keys)):
        raise ValidationAppError("INVALID_INPUT", "prompt.blocks contains duplicate key")
    if set(keys) != set(expected):
        raise ValidationAppError(
            "INVALID_INPUT",
            "prompt.blocks must include exactly the template keys",
            {"expected": sorted(expected), "received": sorted(keys)},
        )
    for block in received:
        if block.get("role") != expected[block["key"]]:
            raise ValidationAppError(
                "INVALID_INPUT",
                f"prompt block role mismatch: {block['key']}",
                {"expected_role": expected[block["key"]], "received_role": block.get("role")},
            )


def _is_private_host(hostname: str) -> bool:
    try:
        addr = ipaddress.ip_address(hostname)
        return addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_unspecified
    except ValueError:
        return False


def _normalize_job_params(payload: CreateJobRequest, handler: Any) -> dict[str, Any]:
    try:
        params = handler.normalize_job_params(payload.job_params)
    except Exception as exc:
        raise ValidationAppError(
            "INVALID_INPUT",
            "job_params does not match job_type schema",
            {"job_type": payload.job_type},
        ) from exc
    if not isinstance(params, dict):
        raise ValidationAppError(
            "INVALID_INPUT",
            "job_params normalizer must return an object",
            {"job_type": payload.job_type},
        )
    return params


def _validate_callback(callback: Any) -> None:
    if callback is None:
        return
    parsed_callback = urlparse(callback.url)
    hostname = parsed_callback.hostname or ""
    is_allowed_local = (
        settings.ALLOW_INSECURE_CALLBACKS
        and parsed_callback.scheme == "http"
        and hostname in {"127.0.0.1", "localhost"}
    )
    if parsed_callback.scheme != "https" and not is_allowed_local:
        raise ValidationAppError("INVALID_INPUT", "callback.url must be HTTPS")
    if not is_allowed_local and _is_private_host(hostname):
        raise ValidationAppError("INVALID_INPUT", "callback.url must not target private network addresses")


def _validate_create_request(payload: CreateJobRequest) -> tuple[Any, dict[str, Any], dict[str, Any]]:
    from app.core import workflow_registry
    try:
        handler = workflow_registry.get(payload.job_type)
    except KeyError:
        raise ValidationAppError("INVALID_JOB_TYPE", f"不支持的 job_type: {payload.job_type}")
    job_params = _normalize_job_params(payload, handler)
    try:
        runtime_fields = handler.runtime_job_fields(job_params)
    except NotImplementedError as exc:
        raise ValidationAppError(
            "INVALID_JOB_TYPE",
            f"job_type 缺少运行时适配: {payload.job_type}",
        ) from exc
    model_id = runtime_fields.get("model_id")
    if model_id and not get_enabled_model(model_id):
        raise ValidationAppError("MODEL_NOT_AVAILABLE", f"模型不可用: {model_id}")
    # Validate prompt blocks against YAML template if available
    template = get_template(payload.job_type)
    if template:
        prompt_payload = runtime_fields.get("prompt_payload")
        if not isinstance(prompt_payload, dict):
            raise ValidationAppError("INVALID_INPUT", "job_type runtime fields must include prompt_payload")
        _validate_prompt(payload.job_type, prompt_payload)
    _validate_callback(payload.callback)
    return handler, job_params, runtime_fields


async def create_job(db: AsyncSession, payload: CreateJobRequest, caller_id: str) -> tuple[AIJob, bool]:
    _handler, job_params, runtime_fields = _validate_create_request(payload)
    request_fingerprint = _request_fingerprint(payload, job_params)
    if payload.client_request_id:
        await JobRepo.advisory_lock_for_client_request(db, caller_id, payload.client_request_id)
        existing = await JobRepo.get_recent_by_client_request(
            db, caller_id=caller_id, client_request_id=payload.client_request_id
        )
        if existing:
            existing_fingerprint = existing.request_fingerprint or (existing.input_payload or {}).get("request_fingerprint")
            if existing_fingerprint is not None and existing_fingerprint != request_fingerprint:
                raise AppError(
                    "CLIENT_REQUEST_ID_CONFLICT",
                    "client_request_id already used with a different request payload",
                    status_code=409,
                    details={"job_id": str(existing.id)},
                )
            return existing, False

    if settings.MAX_ACTIVE_JOBS > 0:
        await db.execute(text("SELECT pg_advisory_lock(hashtext('max_active_jobs_gate'))"))
        try:
            active = await JobRepo.count_active_jobs(db)
        finally:
            await db.execute(text("SELECT pg_advisory_unlock(hashtext('max_active_jobs_gate'))"))
        if active >= settings.MAX_ACTIVE_JOBS:
            raise AppError(
                "QUEUE_FULL",
                "服务当前繁忙，请稍后重试",
                status_code=503,
                details={"active_jobs": active, "limit": settings.MAX_ACTIVE_JOBS},
            )

    options_payload = payload.options.model_dump() if payload.options else None
    final_output_payload = _job_output_payload(uuid.uuid4())
    input_payload_data = {
        "job_params": job_params,
    }
    job = await JobRepo.create(
        db,
        caller_id=caller_id,
        client_request_id=payload.client_request_id,
        job_type=payload.job_type,
        model_id=runtime_fields.get("model_id"),
        input_payload=input_payload_data,
        output_payload={},
        callback_payload={},
        prompt_payload=runtime_fields.get("prompt_payload") or {},
        request_fingerprint=request_fingerprint,
        public_metadata=payload.metadata,
        options_payload=options_payload,
        priority=payload.options.priority if payload.options else "normal",
        timeout_seconds=payload.options.timeout_seconds if payload.options else None,
        runtime_ref={"model_id": runtime_fields.get("model_id")} if runtime_fields.get("model_id") else None,
        callback_url=payload.callback.url if payload.callback else None,
        callback_events=payload.callback.events if payload.callback else None,
        output_oss_bucket=final_output_payload["oss_bucket"],
        output_oss_prefix=final_output_payload["oss_prefix"],
        output_oss_region=final_output_payload["oss_region"],
    )
    final_output_payload = _job_output_payload(job.id)
    job.output_oss_bucket = final_output_payload["oss_bucket"]
    job.output_oss_prefix = final_output_payload["oss_prefix"]
    job.output_oss_region = final_output_payload["oss_region"]
    await db.flush()
    return job, True


async def get_job_or_404(db: AsyncSession, job_id: uuid.UUID) -> AIJob:
    job = await JobRepo.get(db, job_id)
    if not job:
        raise NotFoundAppError("JOB_NOT_FOUND", f"job_id 不存在: {job_id}")
    return job


async def get_job_response(db: AsyncSession, job_id: uuid.UUID, caller_id: str) -> JobStatusResponse:
    job = await JobRepo.get_for_caller(db, job_id, caller_id)
    if not job:
        raise NotFoundAppError("JOB_NOT_FOUND", f"job_id 不存在: {job_id}")
    return _job_to_response(job)


def create_job_response(job: AIJob) -> dict[str, Any]:
    return {
        "job_id": job.id,
        "client_request_id": job.client_request_id,
        "job_type": job.job_type,
        "status": job.status,
        "status_url": _status_url(job.id),
        "created_at": job.created_at,
    }


def _load_input_text(job: AIJob) -> str:
    input_payload = (job.input_payload or {}).get("job_params") or job.input_payload

    # Inline source: text stored directly
    source_payload = input_payload.get("source") or input_payload
    if source_payload.get("inline"):
        return source_payload["inline"]["text"]

    oss_payload = source_payload.get("oss") or source_payload

    try:
        text = storage.read_text(
            bucket=source_payload.get("oss_bucket") or _configured_oss_bucket(),
            key=oss_payload["oss_key"],
            region=source_payload.get("oss_region") or _configured_oss_region(),
        )
    except AppError:
        raise
    except Exception as exc:
        raise AppError("OSS_FETCH_FAILED", "OSS 对象读取失败", status_code=422) from exc

    data = text.encode("utf-8")
    if len(data) > settings.OSS_INPUT_MAX_BYTES:
        raise AppError("INPUT_TOO_LARGE", "OSS input exceeds service limit", status_code=422)
    expected_hash = oss_payload.get("content_hash")
    if expected_hash and sha256_digest(data) != expected_hash:
        raise AppError("INPUT_HASH_MISMATCH", "OSS input content_hash mismatch", status_code=422)
    return text


def _artifact_key(job: AIJob, artifact_key: str) -> str:
    prefix = (job.output_oss_prefix or (job.output_payload or {}).get("oss_prefix") or "").strip("/")
    filename = f"{artifact_key}.txt"
    return f"{prefix}/{filename}" if prefix else filename


def _persist_large_artifacts(job: AIJob, result: JobResult) -> dict[str, Any]:
    from app.core import workflow_registry
    try:
        persist_keys = workflow_registry.get(job.job_type).large_artifact_keys
    except KeyError:
        persist_keys = frozenset()
    result_data = result.model_dump()
    for artifact in result_data["artifacts"]:
        if artifact["key"] not in persist_keys:
            continue
        content = artifact.pop("content", None)
        if content is None:
            continue
        stored = storage.write_text(
            bucket=job.output_oss_bucket or job.output_payload["oss_bucket"],
            key=_artifact_key(job, artifact["key"]),
            region=job.output_oss_region or job.output_payload["oss_region"],
            content=content,
        )
        artifact.update({"storage": "oss_object", **stored})
    return result_data
