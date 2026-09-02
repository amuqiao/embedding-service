import hashlib
import json
import logging
import uuid
from copy import deepcopy
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.callback_security import validate_callback_url_security
from app.core.exceptions import AppError, InternalAppError, NotFoundAppError, ValidationAppError
from app.core.config import settings
from app.core.error_registry import get_error_spec
from app.core.prompt_templates import get_template
from app.services import object_storage
from app.models.job import Job
from app.repositories.job_repo import JobRepo
from app.schemas.errors import JobErrorDetail
from app.schemas.jobs import CreateJobRequest, CreateJobResponse, JobResult, JobStatusResponse
from app.ai.kernel import require_enabled_text_model
from app.services.billing import get_scope_billing, job_cost_from_billing, job_usage_from_billing
from app.services.job_runtime import (
    build_runtime_snapshot,
    configured_output_target,
    job_params_from_job,
    output_target_from_job,
    payload_hash,
    runtime_fields_from_job,
    write_runtime_json,
)
from app.workflows.registry import (
    WorkflowRuntimeDependencyDisabledError,
    WorkflowRuntimeDependencyError,
    compile_registered_workflow,
    has_workflow,
)

logger = logging.getLogger(__name__)
_JOB_RESULT_UNSET = object()
_MAX_ACTIVE_JOBS_GATE_LOCK_SQL = text("SELECT pg_advisory_xact_lock(hashtext('max_active_jobs_gate'))")


def _sha256_content_hash(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _status_url(job_id: uuid.UUID) -> str:
    return f"{settings.service.api_prefix}/jobs/{job_id}"


async def _active_job_capacity_available(
    db: AsyncSession,
    *,
    exclude_job_id: uuid.UUID | None = None,
) -> tuple[bool, int | None]:
    if settings.job.max_active_jobs <= 0:
        return True, None
    await db.execute(_MAX_ACTIVE_JOBS_GATE_LOCK_SQL)
    if exclude_job_id is None:
        active = await JobRepo.count_active_jobs(db)
    else:
        active = await JobRepo.count_active_jobs(db, exclude_job_id=exclude_job_id)
    return active < settings.job.max_active_jobs, active


async def _ensure_active_job_capacity(db: AsyncSession) -> None:
    available, active = await _active_job_capacity_available(db)
    if available:
        return
    raise AppError(
        "QUEUE_FULL",
        "服务当前繁忙，请稍后重试",
        details={"active_jobs": active, "limit": settings.job.max_active_jobs},
    )


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


def _callback_state(job: Job, callback_outbox: Any | None = None) -> dict[str, Any]:
    if not job.callback_url:
        return {
            "status": "not_configured",
            "attempt": 0,
            "last_error": None,
            "next_retry_at": None,
        }
    if callback_outbox is None:
        return {
            "status": "pending",
            "attempt": 0,
            "last_error": None,
            "next_retry_at": None,
        }
    status = callback_outbox.status
    if status == "leased":
        status = "delivering"
    if status == "dead_letter":
        status = "failed"
    if status == "skipped":
        status = "failed" if callback_outbox.last_error else "not_configured"
    return {
        "status": status,
        "attempt": callback_outbox.delivery_attempts or 0,
        "last_error": _job_error_detail(callback_outbox.last_error),
        "next_retry_at": callback_outbox.next_attempt_at,
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


def _job_payload(
    job: Job,
    *,
    cost: dict[str, Any] | None = None,
    usage: dict[str, Any] | None = None,
    include_usage: bool = True,
    job_result: dict[str, Any] | None | object = _JOB_RESULT_UNSET,
    callback_outbox: Any | None = None,
) -> dict[str, Any]:
    result = job.result if job_result is _JOB_RESULT_UNSET else job_result
    try:
        payload = validate_job_status_payload(
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
                "job_result": result,
                "job_error": _job_error_detail(job.error),
                "cost": cost,
                "usage": usage,
                "callback": _callback_state(job, callback_outbox),
                "status_url": _status_url(job.id),
                "created_at": job.created_at,
                "updated_at": job.updated_at or job.created_at,
                "finished_at": job.finished_at,
            }
        ).model_dump(mode="json")
        if not include_usage:
            payload.pop("usage", None)
        return payload
    except Exception as exc:
        raise AppError(
            "JOB_VIEW_CONTRACT_INVALID",
            "stored job view does not match job_type contract",
            details={"job_id": str(job.id), "job_type": job.job_type},
        ) from exc


def _job_to_response(
    job: Job,
    request_id: str = "-",
    *,
    cost: dict[str, Any] | None = None,
    usage: dict[str, Any] | None = None,
    job_result: dict[str, Any] | None | object = _JOB_RESULT_UNSET,
    callback_outbox: Any | None = None,
) -> JobStatusResponse:
    return JobStatusResponse.model_validate(
        _job_payload(job, cost=cost, usage=usage, job_result=job_result, callback_outbox=callback_outbox)
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
    from app.jobs.registry import get_external
    try:
        handler = get_external(payload.job_type)
    except KeyError:
        raise ValidationAppError("INVALID_JOB_TYPE", f"不支持的 job_type: {payload.job_type}")
    spec = handler.job_type_spec()
    if spec.role == "leaf" or spec.visibility == "internal" or (settings.runtime.is_release_env and spec.visibility != "public"):
        raise ValidationAppError(
            "INVALID_JOB_TYPE",
            f"当前环境不支持的 job_type: {payload.job_type}",
            {
                "job_type": payload.job_type,
                "visibility": spec.visibility,
                "role": spec.role,
                "app_env": settings.runtime.app_env,
            },
        )
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


def _requires_text_generation_model(handler: Any) -> bool:
    spec = handler.job_type_spec()
    return spec.execution_mode == "builtin_llm_text_runtime" or bool(
        getattr(handler, "requires_text_generation_model", False)
    )


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
    if model_id and _requires_text_generation_model(handler):
        require_enabled_text_model(model_id)
    # Validate runtime prompt payload only for job types that declare prompt specs.
    # Some custom executors publish prompt template metadata for operator visibility
    # while still taking prompt overrides through job_params.
    template = get_template(payload.job_type)
    prompt_payload = runtime_fields.get("prompt_payload")
    if template and handler.prompt_specs:
        if not isinstance(prompt_payload, dict):
            raise ValidationAppError("INVALID_INPUT", "job_type runtime fields must include prompt_payload")
        _validate_prompt(payload.job_type, prompt_payload)
    return handler, job_params, runtime_fields


def _validate_workflow_child_admission(workflow_plan: dict[str, Any]) -> None:
    from app.jobs.factory import get_enabled_job_executor

    for node in workflow_plan.get("nodes", []):
        if not isinstance(node, dict):
            raise ValidationAppError("INVALID_INPUT", "workflow plan node must be an object")
        job_type = node.get("job_type")
        node_key = node.get("key")
        if not isinstance(job_type, str) or not isinstance(node_key, str):
            raise ValidationAppError("INVALID_INPUT", "workflow plan node requires key and job_type")
        try:
            handler = get_enabled_job_executor(job_type)
        except KeyError as exc:
            raise ValidationAppError(
                "INVALID_JOB_TYPE",
                f"不支持或未启用的 child job_type: {job_type}",
                {"job_type": job_type, "workflow_node_key": node_key},
            ) from exc
        spec = handler.job_type_spec()
        if spec.role not in {"leaf", "root_or_leaf"}:
            raise ValidationAppError(
                "INVALID_JOB_TYPE",
                f"job_type 不允许作为 workflow child: {job_type}",
                {"job_type": job_type, "role": spec.role, "workflow_node_key": node_key},
            )
        try:
            job_params = handler.normalize_job_params(deepcopy(node.get("job_params", {})))
            if not isinstance(job_params, dict):
                raise ValueError("child job_params normalizer must return an object")
            handler.validate_normalized_job_params(job_params)
            runtime_fields = handler.runtime_job_fields(job_params)
            if not isinstance(runtime_fields, dict):
                raise ValueError("child runtime fields must be an object")
        except AppError:
            raise
        except ValueError as exc:
            raise ValidationAppError(
                "INVALID_INPUT",
                "workflow child job_params does not match job_type schema",
                {"job_type": job_type, "workflow_node_key": node_key},
            ) from exc
        except NotImplementedError as exc:
            raise ValidationAppError(
                "INVALID_JOB_TYPE",
                f"child job_type 缺少运行时适配: {job_type}",
            ) from exc
        model_id = runtime_fields.get("model_id")
        if model_id and _requires_text_generation_model(handler):
            require_enabled_text_model(model_id)
        template = get_template(job_type)
        if template and handler.prompt_specs:
            prompt_payload = runtime_fields.get("prompt_payload")
            if not isinstance(prompt_payload, dict):
                raise ValidationAppError("INVALID_INPUT", "child job_type runtime fields must include prompt_payload")
            _validate_prompt(job_type, prompt_payload)


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
            _validate_workflow_child_admission(workflow_plan)
        except WorkflowRuntimeDependencyDisabledError as exc:
            raise ValidationAppError(
                "INVALID_JOB_TYPE",
                "workflow plan references a disabled child job_type",
                {"job_type": payload.job_type},
            ) from exc
        except WorkflowRuntimeDependencyError as exc:
            raise InternalAppError(
                "JOB_PREREQUISITE_CHECK_FAILED",
                "workflow runtime dependency check failed",
                {"job_type": payload.job_type},
            ) from exc
        except (KeyError, TypeError, ValueError) as exc:
            raise ValidationAppError(
                "INVALID_INPUT",
                "workflow plan is invalid",
                {"job_type": payload.job_type},
            ) from exc

    await _ensure_active_job_capacity(db)

    timeout_seconds = int(getattr(handler, "timeout_seconds", settings.ai_provider.model_call_timeout_seconds))
    job_id = uuid.uuid4()
    job_params_hash = payload_hash(job_params)
    output_target = configured_output_target(job_id)
    job_params_ref = write_runtime_json(None, "job_params", job_params)
    runtime_ref = write_runtime_json(
        None,
        "runtime",
        build_runtime_snapshot(
            job_type=payload.job_type,
            job_params_hash=job_params_hash,
            runtime_fields=runtime_fields,
            output_target=output_target,
            workflow_plan=workflow_plan,
        ),
    )
    job = await JobRepo.create(
        db,
        caller_id=caller_id,
        client_request_id=payload.client_request_id,
        job_type=payload.job_type,
        job_id=job_id,
        job_params_ref=job_params_ref,
        job_params_hash=job_params_hash,
        runtime_ref=runtime_ref,
        metadata=payload.metadata,
        priority=payload.options.priority if payload.options else "normal",
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
    purpose = "workflow_orchestration" if workflow_plan is not None else "business_execution"
    await JobRepo.create_initial_attempt(
        db,
        job,
        timeout_seconds=timeout_seconds,
        purpose=purpose,
        retry_policy=handler.effective_retry_policy().for_purpose(purpose),
    )
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
    cost = None
    usage = None
    projected_result: dict[str, Any] | None | object = _JOB_RESULT_UNSET
    if job.status in {"succeeded", "failed"}:
        billing = await get_scope_billing(db, scope_type="job", scope_id=str(job.id), caller_id=caller_id)
        mapped = job_cost_from_billing(billing)
        cost = mapped.model_dump() if mapped is not None else None
        mapped_usage = job_usage_from_billing(billing)
        usage = mapped_usage.model_dump() if mapped_usage is not None else None
        if job.status == "failed":
            from app.jobs.factory import get_job_executor

            projected_result = None
            handler = get_job_executor(job.job_type)
            if handler.supports_result_snapshot(job.status):
                projected_result = await handler.build_result_snapshot(job.status, job, db)
    elif job.status == "running":
        from app.jobs.factory import get_job_executor

        projected_result = None
        handler = get_job_executor(job.job_type)
        if handler.supports_result_snapshot(job.status):
            projected_result = await handler.build_result_snapshot(job.status, job, db)
    callback_outbox = await JobRepo.get_terminal_callback_outbox(db, job) if job.callback_url else None
    return _job_to_response(
        job,
        request_id,
        cost=cost,
        usage=usage,
        job_result=projected_result,
        callback_outbox=callback_outbox,
    )


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
    if created:
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
        text = object_storage.read_text(
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
    if expected_hash and _sha256_content_hash(data) != expected_hash:
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
        stored = object_storage.write_text(
            bucket=output_target["oss_bucket"],
            key=_artifact_key(output_target, artifact_key, scope=scope),
            region=output_target["oss_region"],
            content=content,
        )
        artifact.update({"storage": "oss_object", **stored})
    return result_data


def _persist_large_artifacts(
    job: Job,
    result: JobResult | dict[str, Any],
    *,
    attempt_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    result_data = result.model_dump() if isinstance(result, JobResult) else dict(result)
    scope = f"attempts/{attempt_id}/results" if attempt_id is not None else "results"
    return _persist_large_artifact_payload(job, result_data, scope=scope)
