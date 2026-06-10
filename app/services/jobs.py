import uuid
from typing import Any
from urllib.parse import urlparse

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError, NotFoundAppError, ValidationAppError
from app.infrastructure.config import settings
from app.infrastructure.model_registry import get_enabled_model
from app.infrastructure.prompt_templates import get_template
from app.infrastructure.storage import sha256_digest, storage
from app.models.job import AIJob
from app.repositories.job_repo import JobRepo
from app.schemas.jobs import CreateJobRequest, JobResult, JobStatusResponse


def _status_url(job_id: uuid.UUID) -> str:
    return f"/api/v1/novel-localization-ai/jobs/{job_id}"


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


def _job_to_response(job: AIJob) -> JobStatusResponse:
    return JobStatusResponse(
        job_id=job.id,
        job_type=job.job_type,
        status=job.status,
        progress_percent=job.progress_percent,
        progress_text=job.progress_text,
        result=job.result_payload,
        error=job.error_payload,
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


def _validate_create_request(payload: CreateJobRequest) -> None:
    if not get_template(payload.job_type):
        raise ValidationAppError("INVALID_JOB_TYPE", f"不支持的 job_type: {payload.job_type}")
    if not get_enabled_model(payload.model_id):
        raise ValidationAppError("MODEL_NOT_AVAILABLE", f"模型不可用: {payload.model_id}")
    _validate_prompt(payload.job_type, payload.prompt.model_dump())
    parsed_callback = urlparse(payload.callback.url)
    if parsed_callback.scheme != "https":
        local_insecure = (
            settings.ALLOW_INSECURE_CALLBACKS
            and parsed_callback.scheme == "http"
            and parsed_callback.hostname in {"127.0.0.1", "localhost"}
        )
        if not local_insecure:
            raise ValidationAppError("INVALID_INPUT", "callback.url must be HTTPS")


async def create_job(db: AsyncSession, payload: CreateJobRequest, caller_id: str) -> tuple[AIJob, bool]:
    _validate_create_request(payload)
    if payload.client_request_id:
        await JobRepo.advisory_lock_for_client_request(db, caller_id, payload.client_request_id)
        existing = await JobRepo.get_recent_by_client_request(
            db, caller_id=caller_id, client_request_id=payload.client_request_id
        )
        if existing:
            return existing, False

    job = await JobRepo.create(
        db,
        caller_id=caller_id,
        client_request_id=payload.client_request_id,
        job_type=payload.job_type,
        model_id=payload.model_id,
        input_payload=payload.source.model_dump(),
        output_payload=_job_output_payload(uuid.uuid4()),
        callback_payload=payload.callback.model_dump(),
        prompt_payload=payload.prompt.model_dump(),
    )
    job.output_payload = _job_output_payload(job.id)
    await db.flush()
    return job, True


async def get_job_or_404(db: AsyncSession, job_id: uuid.UUID) -> AIJob:
    job = await JobRepo.get(db, job_id)
    if not job:
        raise NotFoundAppError("JOB_NOT_FOUND", f"job_id 不存在: {job_id}")
    return job


async def get_job_response(db: AsyncSession, job_id: uuid.UUID) -> JobStatusResponse:
    return _job_to_response(await get_job_or_404(db, job_id))


def create_job_response(job: AIJob) -> dict[str, Any]:
    return {
        "job_id": job.id,
        "status": job.status,
        "status_url": _status_url(job.id),
        "created_at": job.created_at,
    }


def _load_input_text(job: AIJob) -> str:
    input_payload = job.input_payload
    oss_payload = input_payload.get("oss") or input_payload

    try:
        text = storage.read_text(
            bucket=input_payload.get("oss_bucket") or _configured_oss_bucket(),
            key=oss_payload["oss_key"],
            region=input_payload.get("oss_region") or _configured_oss_region(),
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
    prefix = job.output_payload["oss_prefix"].strip("/")
    filename = {
        "localized_text": "localized.txt",
        "translated_text": "translated.txt",
    }.get(artifact_key, f"{artifact_key}.txt")
    return f"{prefix}/{filename}" if prefix else filename


def _persist_large_artifacts(job: AIJob, result: JobResult) -> dict[str, Any]:
    result_data = result.model_dump()
    for artifact in result_data["artifacts"]:
        if artifact["key"] not in {"localized_text", "translated_text"}:
            continue
        content = artifact.pop("content", None)
        if content is None:
            continue
        stored = storage.write_text(
            bucket=job.output_payload["oss_bucket"],
            key=_artifact_key(job, artifact["key"]),
            region=job.output_payload["oss_region"],
            content=content,
        )
        artifact.update({"storage": "oss_object", **stored})
    return result_data
