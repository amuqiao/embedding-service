import hashlib
import json
import logging
import uuid
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.callback_security import validate_callback_url_security
from app.core.exceptions import AppError, InternalAppError, NotFoundAppError, ValidationAppError
from app.core.config import settings
from app.core.error_registry import get_error_spec
from app.core.model_registry import get_enabled_model
from app.core.prompt_templates import get_template
from app.integrations.storage import sha256_digest, storage
from app.models.job import Job
from app.repositories.job_repo import JobRepo
from app.schemas.errors import JobErrorDetail
from app.schemas.jobs import CreateJobRequest, CreateJobResponse, JobResult, JobStatusResponse
from app.services.job_runtime import (
    build_runtime_snapshot,
    configured_output_target,
    job_params_from_job,
    output_target_from_job,
    payload_hash,
    runtime_fields_from_job,
    write_runtime_json,
)
from app.workflows.registry import compile_registered_workflow, has_workflow

logger = logging.getLogger(__name__)


def _status_url(job_id: uuid.UUID) -> str:
    return f"{settings.service.api_prefix}/jobs/{job_id}"


def _configured_oss_bucket() -> str:
    return settings.storage.oss_bucket or "local-dev"


def _configured_oss_region() -> str:
    return settings.storage.oss_region or "local"


def _canonical_callback(callback: Any) -> dict[str, Any] | None:
    if callback is None:
        return None
    parsed = urlsplit(callback.url)
    hostname = parsed.hostname or ""
    ascii_host = hostname.encode("idna").decode("ascii").lower()
    scheme = parsed.scheme.lower()
    netloc = ascii_host
    if parsed.port is not None and not (scheme == "https" and parsed.port == 443):
        netloc = f"{netloc}:{parsed.port}"
    canonical_url = urlunsplit((scheme, netloc, parsed.path or "/", parsed.query or "", ""))
    return {
        "url": canonical_url,
        "events": sorted(set(callback.events or ["job.failed", "job.succeeded"])),
    }


def _canonical_options(payload: CreateJobRequest) -> dict[str, Any]:
    if payload.options is None:
        return {"priority": "normal", "idempotency_mode": "reject_duplicate"}
    return {
        "priority": payload.options.priority,
        "idempotency_mode": payload.options.idempotency_mode,
    }


def _request_fingerprint(payload: CreateJobRequest, caller_id: str, job_params: dict[str, Any]) -> str:
    body = {
        "caller_id": caller_id,
        "client_request_id": payload.client_request_id,
        "job_type": payload.job_type,
        "job_params": job_params,
        "callback": _canonical_callback(payload.callback),
        "options": _canonical_options(payload),
    }
    encoded = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _job_error_detail(error: dict[str, Any] | None) -> dict[str, Any] | None:
    if error is None:
        return None
    reason = str(error.get("code") or "INTERNAL_ERROR")
    details = dict(error.get("details")) if isinstance(error.get("details"), dict) else {}
    for key, value in error.items():
        if key not in {"code", "reason", "message", "details"}:
            details[key] = value
    return JobErrorDetail(reason=reason, details=details, retryable=get_error_spec(reason).retryable).model_dump()


def _callback_state(job: Job) -> dict[str, Any]:
    if not job.callback_url:
        return {
            "status": "not_configured",
            "attempt": 0,
            "last_error": None,
            "next_retry_at": None,
        }
    status = job.callback_status
    if status == "failed" and job.callback_next_retry_at is not None:
        status = "retrying"
    if status == "skipped":
        status = "failed" if job.callback_last_error else "not_configured"
    return {
        "status": status,
        "attempt": job.callback_attempts or 0,
        "last_error": _job_error_detail(job.callback_last_error),
        "next_retry_at": job.callback_next_retry_at,
    }


def _progress_stage(job: Job) -> str:
    if job.status == "queued":
        return "accepted"
    if job.status == "succeeded":
        return "completed"
    if job.status == "failed":
        return "failed"
    stage = (job.progress_stage or "").strip().lower()
    stage_map = {
        "accepted": "accepted",
        "planning": "planning",
        "calling_model": "calling_model",
        "merging": "merging",
        "writing_result": "writing_result",
        "delivering_callback": "writing_result",
        "completed": "completed",
        "failed": "failed",
        "success_side_effect": "writing_result",
        "success_side_effect_done": "writing_result",
        "succeeded": "completed",
    }
    if stage in stage_map:
        return stage_map[stage]
    if job.progress_percent < 15:
        return "planning"
    if job.progress_percent < 85:
        return "calling_model"
    if job.progress_percent < 100:
        return "merging"
    return "calling_model"


def _job_payload(job: Job) -> dict[str, Any]:
    try:
        return validate_job_status_payload(
            {
                "job_id": job.id,
                "client_request_id": job.client_request_id or "",
                "job_type": job.job_type,
                "job_status": job.status,
                "job_progress": {
                    "percent": job.progress_percent or 0,
                    "message": job.progress_text or _progress_stage(job),
                    "stage": _progress_stage(job),
                },
                "job_result": job.result,
                "job_error": _job_error_detail(job.error),
                "callback": _callback_state(job),
                "status_url": _status_url(job.id),
                "created_at": job.created_at,
                "updated_at": job.updated_at or job.created_at,
                "finished_at": job.finished_at,
            }
        ).model_dump(mode="json")
    except Exception as exc:
        raise AppError(
            "JOB_VIEW_CONTRACT_INVALID",
            "stored job view does not match job_type contract",
            details={"job_id": str(job.id), "job_type": job.job_type},
        ) from exc


def _job_to_response(job: Job, request_id: str = "-") -> JobStatusResponse:
    return JobStatusResponse.model_validate(_job_payload(job))


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


def _normalize_job_params(payload: CreateJobRequest, handler: Any) -> dict[str, Any]:
    try:
        params = handler.normalize_job_params(payload.job_params)
    except AppError:
        raise
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
    try:
        validate_callback_url_security(
            callback.url,
            allow_insecure_local=settings.callback.allow_insecure_callbacks,
        )
    except ValueError as exc:
        raise ValidationAppError("INVALID_INPUT", str(exc)) from exc


def validate_create_contract(payload: CreateJobRequest) -> tuple[Any, dict[str, Any]]:
    from app.jobs.factory import get_job_executor
    try:
        handler = get_job_executor(payload.job_type)
    except KeyError:
        raise ValidationAppError("INVALID_JOB_TYPE", f"不支持的 job_type: {payload.job_type}")
    job_params = _normalize_job_params(payload, handler)
    if payload.callback is not None and not handler.allow_callback:
        raise ValidationAppError(
            "INVALID_INPUT",
            "callback is not supported for this job_type",
            {"job_type": payload.job_type},
        )
    _validate_callback(payload.callback)
    return handler, job_params


def validate_job_status_payload(payload: dict[str, Any]):
    from app.jobs.registry import validate_job_view_payload

    return validate_job_view_payload(payload)


def _validate_create_request(payload: CreateJobRequest) -> tuple[Any, dict[str, Any], dict[str, Any]]:
    handler, job_params = validate_create_contract(payload)
    try:
        handler.validate_normalized_job_params(job_params)
    except AppError:
        raise
    except ValueError as exc:
        raise ValidationAppError(
            "INVALID_INPUT",
            "job_params does not match job_type schema",
            {"job_type": payload.job_type},
        ) from exc
    except Exception as exc:
        raise InternalAppError(
            "JOB_PREREQUISITE_CHECK_FAILED",
            "job_type runtime prerequisite check failed",
            {"job_type": payload.job_type},
        ) from exc
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
    return handler, job_params, runtime_fields


async def create_job(
    db: AsyncSession,
    payload: CreateJobRequest,
    caller_id: str,
    *,
    trigger_request_id: str | None = None,
) -> tuple[Job, bool]:
    handler, job_params, runtime_fields = _validate_create_request(payload)
    if trigger_request_id:
        runtime_fields = {
            **runtime_fields,
            "_system": {
                **(runtime_fields.get("_system") if isinstance(runtime_fields.get("_system"), dict) else {}),
                "trigger_request_id": trigger_request_id,
            },
        }
    request_fingerprint = _request_fingerprint(payload, caller_id, job_params)
    await JobRepo.advisory_lock_for_client_request(db, caller_id, payload.client_request_id)
    existing_submission = await JobRepo.get_submission_by_client_request(
        db, caller_id=caller_id, client_request_id=payload.client_request_id
    )
    if existing_submission:
        existing, submission_key = existing_submission
        if submission_key.request_fingerprint != request_fingerprint:
            raise AppError(
                "CLIENT_REQUEST_ID_CONFLICT",
                "client_request_id already used with a different request payload",
                details={"client_request_id": payload.client_request_id, "existing_job_id": str(existing.id)},
            )
        if not payload.options or payload.options.idempotency_mode == "reject_duplicate":
            raise AppError(
                "CLIENT_REQUEST_ID_CONFLICT",
                "client_request_id already used",
                details={"client_request_id": payload.client_request_id, "existing_job_id": str(existing.id)},
            )
        return existing, False

    workflow_plan = None
    if has_workflow(payload.job_type):
        try:
            workflow_plan = compile_registered_workflow(payload.job_type, job_params)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValidationAppError(
                "INVALID_INPUT",
                "workflow plan is invalid",
                {"job_type": payload.job_type},
            ) from exc

    if settings.job.max_active_jobs > 0:
        await db.execute(text("SELECT pg_advisory_lock(hashtext('max_active_jobs_gate'))"))
        try:
            active = await JobRepo.count_active_jobs(db)
        finally:
            await db.execute(text("SELECT pg_advisory_unlock(hashtext('max_active_jobs_gate'))"))
        if active >= settings.job.max_active_jobs:
            raise AppError(
                "QUEUE_FULL",
                "服务当前繁忙，请稍后重试",
                details={"active_jobs": active, "limit": settings.job.max_active_jobs},
            )

    timeout_seconds = int(getattr(handler, "timeout_seconds", settings.ai_provider.model_call_timeout_seconds))
    max_attempts = int(getattr(handler, "max_attempts", 1))
    job = await JobRepo.create(
        db,
        caller_id=caller_id,
        client_request_id=payload.client_request_id,
        job_type=payload.job_type,
        metadata=payload.metadata,
        priority=payload.options.priority if payload.options else "normal",
        timeout_seconds=timeout_seconds,
        max_attempts=max_attempts,
        job_params=job_params,
        callback_url=payload.callback.url if payload.callback else None,
        callback_events=payload.callback.events if payload.callback else None,
    )
    await JobRepo.create_submission_key(
        db,
        caller_id=caller_id,
        client_request_id=payload.client_request_id,
        request_fingerprint=request_fingerprint,
        job=job,
    )
    job_params_hash = payload_hash(job_params)
    output_target = configured_output_target(job.id)
    job.job_params_hash = job_params_hash
    job.job_params_ref = write_runtime_json(job, "job_params", job_params)
    job.runtime_ref = write_runtime_json(
        job,
        "runtime",
        build_runtime_snapshot(
            job_type=payload.job_type,
            job_params_hash=job_params_hash,
            runtime_fields=runtime_fields,
            output_target=output_target,
            workflow_plan=workflow_plan,
        ),
    )
    await JobRepo.create_initial_attempt(db, job, timeout_seconds=timeout_seconds)
    await db.flush()
    return job, True


async def get_job_or_404(db: AsyncSession, job_id: uuid.UUID) -> Job:
    job = await JobRepo.get(db, job_id)
    if not job:
        raise NotFoundAppError("JOB_NOT_FOUND", f"job_id 不存在: {job_id}")
    return job


async def get_job_response(
    db: AsyncSession,
    job_id: uuid.UUID,
    caller_id: str,
    *,
    request_id: str = "-",
) -> JobStatusResponse:
    job = await JobRepo.get_for_caller(db, job_id, caller_id)
    if not job:
        raise NotFoundAppError("JOB_NOT_FOUND", f"job_id 不存在: {job_id}")
    return _job_to_response(job, request_id)


def create_job_response(job: Job, request_id: str = "-") -> CreateJobResponse:
    return CreateJobResponse.model_validate(_job_payload(job))


def trigger_request_id_from_job(job: Job) -> str | None:
    try:
        system_fields = runtime_fields_from_job(job).get("_system")
    except Exception:
        return None
    if not isinstance(system_fields, dict):
        return None
    value = system_fields.get("trigger_request_id")
    return value if isinstance(value, str) and value else None


async def submit_job_request(
    db: AsyncSession,
    payload: CreateJobRequest,
    caller_id: str,
    *,
    request_id: str,
) -> CreateJobResponse:
    job, created = await create_job(
        db,
        payload,
        caller_id,
        trigger_request_id=request_id,
    )
    await db.commit()
    if created and job.active_attempt_id is not None:
        await db.refresh(job)
        from app.tasks.jobs import TaskiqPublishDeferredError, publish_job_attempt

        try:
            await publish_job_attempt(job.active_attempt_id)
        except TaskiqPublishDeferredError:
            logger.exception(
                "job_attempt_publish_deferred_after_create job_id=%s attempt_id=%s",
                job.id,
                job.active_attempt_id,
            )
        else:
            await db.refresh(job)
    return create_job_response(job, request_id=request_id)


def _load_input_text(job: Job) -> str:
    job_params = job_params_from_job(job)

    # Inline source: text stored directly
    source_payload = job_params.get("source") or job_params
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
        raise AppError("OSS_FETCH_FAILED", "OSS 对象读取失败") from exc

    data = text.encode("utf-8")
    if len(data) > settings.job.oss_input_max_bytes:
        raise AppError("INPUT_TOO_LARGE", "OSS input exceeds service limit")
    expected_hash = oss_payload.get("content_hash")
    if expected_hash and sha256_digest(data) != expected_hash:
        raise AppError("INPUT_HASH_MISMATCH", "OSS input content_hash mismatch")
    return text


def _artifact_key(output_target: dict[str, Any], artifact_key: str, *, scope: str = "") -> str:
    prefix = output_target["oss_prefix"].strip("/")
    filename = f"{artifact_key}.txt"
    scoped = "/".join(part.strip("/") for part in (scope, filename) if part.strip("/"))
    return f"{prefix}/{scoped}" if prefix else scoped


def _persist_large_artifact_payload(
    job: Job,
    result_data: dict[str, Any],
    *,
    scope: str = "",
) -> dict[str, Any]:
    from app.jobs.factory import get_job_executor
    try:
        persist_keys = get_job_executor(job.job_type).large_artifact_keys
    except KeyError:
        persist_keys = frozenset()
    output_target: dict[str, Any] | None = None
    for artifact in result_data.get("artifacts") or []:
        artifact_key = artifact.get("key") if isinstance(artifact, dict) else None
        if not artifact_key or artifact_key not in persist_keys:
            continue
        content = artifact.pop("content", None)
        if content is None:
            continue
        if output_target is None:
            output_target = output_target_from_job(job)
        stored = storage.write_text(
            bucket=output_target["oss_bucket"],
            key=_artifact_key(output_target, artifact_key, scope=scope),
            region=output_target["oss_region"],
            content=content,
        )
        artifact.update({"storage": "oss_object", **stored})
    return result_data


def _persist_large_artifacts(job: Job, result: JobResult | dict[str, Any]) -> dict[str, Any]:
    generation = int(getattr(job, "execution_generation", None) or 1)
    result_data = result.model_dump() if isinstance(result, JobResult) else dict(result)
    return _persist_large_artifact_payload(job, result_data, scope=f"results/g{generation}")
