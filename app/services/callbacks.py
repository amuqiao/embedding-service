import hmac
import json
import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
from pydantic import BaseModel

from app.core.callback_security import validate_callback_url_security
from app.core.config import settings
from app.models.job import Job
from app.schemas.callbacks import CallbackEnvelope, CallbackResponseEnvelope
from app.services.jobs import _job_payload, trigger_request_id_from_job

logger = logging.getLogger(__name__)
CALLBACK_EVENT_NAMESPACE = "ai-job-callback"


class CallbackDeliveryResult(BaseModel):
    status: str
    attempts: int = 0
    last_error: dict | None = None
    response: dict[str, Any] | None = None


def _sign(timestamp: str, body: bytes) -> str:
    if not settings.CALLBACK_SIGNING_SECRET:
        raise ValueError("CALLBACK_SIGNING_SECRET must be configured")
    digest = hmac.new(
        settings.CALLBACK_SIGNING_SECRET.encode("utf-8"),
        timestamp.encode("utf-8") + b"." + body,
        "sha256",
    ).hexdigest()
    return f"sha256={digest}"


def _validate_callback_url(url: str) -> None:
    validate_callback_url_security(
        url,
        allow_insecure_local=settings.ALLOW_INSECURE_CALLBACKS,
    )


def validate_callback_response_payload(payload: dict[str, Any], *, job_type: str) -> CallbackResponseEnvelope:
    envelope = CallbackResponseEnvelope.model_validate(payload)
    from app.jobs.factory import get_job_executor

    get_job_executor(job_type).validate_callback_response(envelope)
    return envelope


def build_callback_body(job: Job) -> dict:
    event = "job.succeeded" if job.status == "succeeded" else "job.failed"
    sent_at = datetime.now(timezone.utc)
    event_id = uuid.uuid5(uuid.NAMESPACE_URL, f"{CALLBACK_EVENT_NAMESPACE}:{job.id}:{event}")
    envelope = CallbackEnvelope.model_validate(
        {
            "event": event,
            "event_id": str(event_id),
            "attempt": (job.callback_attempts or 0) + 1,
            "sent_at": sent_at.isoformat(),
            "trigger_request_id": trigger_request_id_from_job(job),
            "caller_id": job.caller_id,
            "job": _job_payload(job),
        }
    )
    return envelope.model_dump(mode="json")


def _header_value(headers: Any, name: str) -> str | None:
    if hasattr(headers, "get"):
        value = headers.get(name)
        if value is not None:
            return value
    if hasattr(headers, "items"):
        lowered_name = name.lower()
        for key, value in headers.items():
            if str(key).lower() == lowered_name:
                return value
    return None


def _is_json_content_type(content_type: str | None) -> bool:
    if content_type is None:
        return False
    return content_type.split(";", 1)[0].strip().lower() == "application/json"


def _invalid_ack_summary(error: str) -> dict[str, Any]:
    return {"format": "ack", "valid": False, "error": error}


def _callback_response_summary(
    *,
    status_code: int,
    response_headers: Any,
    response_text: str,
    callback_body: dict[str, Any],
) -> dict[str, Any]:
    if status_code == 204:
        return _invalid_ack_summary("callback response HTTP 204 is not a valid acknowledgment")
    content_type = _header_value(response_headers, "content-type")
    if not _is_json_content_type(content_type):
        return _invalid_ack_summary("callback response Content-Type must be application/json")
    text = response_text.strip()
    if not text:
        return _invalid_ack_summary("callback response body must be a JSON acknowledgment object")
    job_type = callback_body["job"]["job_type"]
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return _invalid_ack_summary("callback response must be JSON acknowledgment")
    if not isinstance(parsed, dict):
        return _invalid_ack_summary("callback response acknowledgment must be an object")
    if "accepted" not in parsed:
        return _invalid_ack_summary("callback response acknowledgment must include accepted")
    if not isinstance(parsed["accepted"], bool):
        return _invalid_ack_summary("callback response accepted must be a boolean")
    try:
        envelope = validate_callback_response_payload(parsed, job_type=job_type)
    except Exception as exc:
        return {
            "format": "ack",
            "valid": False,
            "error": str(exc)[:500],
        }
    return {
        "format": "ack",
        "valid": True,
        "accepted": envelope.accepted,
        "msg": envelope.msg,
        "details": envelope.details,
    }


async def deliver_callback(job: Job, *, payload: dict[str, Any] | None = None) -> CallbackDeliveryResult:
    url = job.callback_url
    if not url:
        return CallbackDeliveryResult(status="skipped")
    try:
        _validate_callback_url(url)
    except ValueError as e:
        logger.warning("callback_url_invalid job_id=%s url=%s reason=%s", job.id, url, e)
        return CallbackDeliveryResult(
            status="skipped",
            last_error={"code": "CALLBACK_URL_INVALID", "message": str(e)},
        )

    try:
        if payload is None:
            event = "job.succeeded" if job.status == "succeeded" else "job.failed"
            callback_events = job.callback_events if job.callback_events is not None else ["job.succeeded", "job.failed"]
            events = set(callback_events)
            if event not in events:
                return CallbackDeliveryResult(status="skipped")
            callback_body = build_callback_body(job)
        else:
            callback_body = payload
        body = json.dumps(callback_body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except Exception as exc:
        logger.error("callback_body_invalid job_id=%s error_type=%s", job.id, type(exc).__name__, exc_info=True)
        return CallbackDeliveryResult(
            status="failed",
            attempts=1,
            last_error={
                "code": "CALLBACK_BODY_INVALID",
                "type": type(exc).__name__,
                "message": str(exc)[:500],
            },
        )
    delays = [0]
    attempts = 0
    last_error: dict | None = None
    async with httpx.AsyncClient(timeout=settings.CALLBACK_TIMEOUT_SECONDS) as client:
        for attempt, delay in enumerate(delays):
            if delay:
                await asyncio.sleep(delay)
            timestamp = datetime.now(timezone.utc).isoformat()
            headers = {
                "Content-Type": "application/json",
                "X-Callback-Timestamp": timestamp,
                "X-Callback-Signature": _sign(timestamp, body),
            }
            try:
                attempts += 1
                response = await client.post(url, content=body, headers=headers)
                response_summary = _callback_response_summary(
                    status_code=response.status_code,
                    response_headers=response.headers,
                    response_text=response.text,
                    callback_body=callback_body,
                )
                if 200 <= response.status_code < 300:
                    if response_summary.get("format") == "ack" and not response_summary.get("valid"):
                        last_error = {
                            "code": "CALLBACK_RESPONSE_CONTRACT_INVALID",
                            "message": "callback endpoint returned invalid acknowledgment response",
                            "response": response_summary,
                        }
                        logger.warning(
                            "callback_response_contract_mismatch job_id=%s summary=%s",
                            job.id,
                            response_summary,
                        )
                        continue
                    if response_summary.get("accepted") is False:
                        last_error = {
                            "code": "CALLBACK_ACK_REJECTED",
                            "message": "callback endpoint rejected the event acknowledgment",
                            "response": response_summary,
                        }
                        logger.warning(
                            "callback_response_rejected job_id=%s summary=%s",
                            job.id,
                            response_summary,
                        )
                        continue
                    logger.info(
                        "callback_response_received job_id=%s summary=%s",
                        job.id,
                        response_summary,
                    )
                    return CallbackDeliveryResult(
                        status="delivered",
                        attempts=attempts,
                        response=response_summary,
                    )
                last_error = {
                    "code": "CALLBACK_HTTP_ERROR",
                    "status_code": response.status_code,
                    "message": response.text[:500],
                    "response": response_summary,
                }
                logger.warning(
                    "callback attempt %d failed for job %s: status=%d",
                    attempt + 1, job.id, response.status_code,
                )
            except httpx.HTTPError as exc:
                last_error = {
                    "code": "CALLBACK_REQUEST_ERROR",
                    "type": type(exc).__name__,
                    "message": str(exc)[:500],
                }
                logger.warning(
                    "callback attempt %d error for job %s: %s",
                    attempt + 1, job.id, exc,
                )
    logger.error("all callback attempts exhausted for job %s url=%s", job.id, url)
    return CallbackDeliveryResult(status="failed", attempts=attempts, last_error=last_error)
