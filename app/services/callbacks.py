import hashlib
import hmac
import json
import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel

from app.core.config import settings
from app.models.job import AIJob
from app.schemas.jobs import CallbackEnvelope, CallbackResponseEnvelope

logger = logging.getLogger(__name__)

_CALLBACK_RESPONSE_V1_HINT_KEYS = {
    "schema_version",
    "event",
    "event_id",
    "job_id",
    "client_request_id",
    "job_type",
    "metadata",
    "data",
}
_CALLBACK_RESPONSE_LEGACY_KEYS = {"status", "msg"}


class CallbackDeliveryResult(BaseModel):
    status: str
    attempts: int = 0
    last_error: dict | None = None
    response: dict[str, Any] | None = None


def _sign(timestamp: str, body: bytes) -> str | None:
    if not settings.CALLBACK_SIGNING_SECRET:
        return None
    digest = hmac.new(
        settings.CALLBACK_SIGNING_SECRET.encode("utf-8"),
        timestamp.encode("utf-8") + b"." + body,
        hashlib.sha256,
    ).hexdigest()
    return f"sha256={digest}"


def _validate_callback_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme == "https":
        return
    if settings.ALLOW_INSECURE_CALLBACKS and parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}:
        return
    raise ValueError("callback.url must be HTTPS")


def _callback_data(job: AIJob) -> dict[str, Any]:
    from app.core import workflow_registry

    return workflow_registry.get(job.job_type).build_callback_data(job)


def validate_callback_response_payload(payload: dict[str, Any]) -> CallbackResponseEnvelope:
    from app.core import workflow_registry

    envelope = CallbackResponseEnvelope.model_validate(payload)
    workflow_registry.get(envelope.job_type).validate_callback_response(envelope)
    return envelope


def build_callback_body(job: AIJob) -> dict:
    event = "job.succeeded" if job.status == "succeeded" else "job.failed"
    sent_at = datetime.now(timezone.utc)
    envelope = CallbackEnvelope.model_validate({
        "schema_version": "v1",
        "event": event,
        "event_id": str(uuid.uuid4()),
        "attempt": (job.callback_attempts or 0) + 1,
        "sent_at": sent_at.isoformat(),
        "job_id": job.id,
        "client_request_id": job.client_request_id,
        "job_type": job.job_type,
        "status": job.status,
        "progress": {
            "percent": job.progress_percent,
            "message": job.progress_text,
            "stage": job.progress_stage,
        },
        "error": job.error,
        "metadata": job.metadata_ or {},
        "data": _callback_data(job),
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
    })
    return envelope.model_dump(mode="json")


def _callback_response_summary(response_text: str, callback_body: dict[str, Any]) -> dict[str, Any] | None:
    text = response_text.strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {"format": "text", "message": text[:500]}
    if not isinstance(parsed, dict):
        return {"format": "json", "body": parsed}

    has_v1_hint = any(key in parsed for key in _CALLBACK_RESPONSE_V1_HINT_KEYS)
    if parsed.get("schema_version") == "v1":
        try:
            envelope = validate_callback_response_payload(parsed)
        except Exception as exc:
            return {
                "format": "v1",
                "valid": False,
                "error": str(exc)[:500],
            }
        dump = envelope.model_dump(mode="json")
        mismatches: list[dict[str, Any]] = []
        for key in ("event", "event_id", "job_id", "client_request_id", "job_type", "status"):
            expected = callback_body.get(key)
            actual = dump.get(key)
            if actual != expected:
                mismatches.append({"field": key, "expected": expected, "actual": actual})
        return {
            "format": "v1",
            "valid": not mismatches,
            "mismatches": mismatches,
            "event": envelope.event,
            "job_id": str(envelope.job_id),
            "status": envelope.status,
            "msg": envelope.msg,
            "data": envelope.data,
        }

    if has_v1_hint:
        return {
            "format": "v1",
            "valid": False,
            "error": "callback response looks like v1 but schema_version is missing or unsupported",
        }

    if set(parsed).issubset(_CALLBACK_RESPONSE_LEGACY_KEYS) and ("status" in parsed or "msg" in parsed):
        return {
            "format": "legacy",
            "status": parsed.get("status"),
            "msg": parsed.get("msg"),
        }
    return {"format": "json", "body": parsed}


async def deliver_callback(job: AIJob) -> CallbackDeliveryResult:
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

    event = "job.succeeded" if job.status == "succeeded" else "job.failed"
    events = set(job.callback_events or ["job.succeeded", "job.failed"])
    if event not in events:
        return CallbackDeliveryResult(status="skipped")

    try:
        callback_body = build_callback_body(job)
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
                "X-AI-Service-Job-Id": str(job.id),
                "X-AI-Service-Event": event,
                "X-AI-Service-Timestamp": timestamp,
            }
            signature = _sign(timestamp, body)
            if signature:
                headers["X-AI-Service-Signature"] = signature
            try:
                attempts += 1
                response = await client.post(url, content=body, headers=headers)
                response_summary = _callback_response_summary(response.text, callback_body)
                if 200 <= response.status_code < 300:
                    if response_summary and response_summary.get("format") == "v1" and not response_summary.get("valid"):
                        last_error = {
                            "code": "CALLBACK_RESPONSE_CONTRACT_INVALID",
                            "message": "callback endpoint returned invalid v1 response",
                            "response": response_summary,
                        }
                        logger.warning(
                            "callback_response_contract_mismatch job_id=%s summary=%s",
                            job.id,
                            response_summary,
                        )
                        continue
                    elif response_summary:
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
