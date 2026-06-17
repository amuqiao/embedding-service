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

_SHORT_DRAMA_SUCCESS_SIGNAL_KEYS = (
    "t_book_id",
    "result_status",
    "validation_issue_count",
    "validation_issues",
    "reason_codes",
    "subtitle_language",
    "requested_schema_language",
)
_CALLBACK_RESPONSE_V1_HINT_KEYS = {
    "schema_version",
    "event",
    "event_id",
    "job_id",
    "client_request_id",
    "job_type",
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


def _canonical_signals(job: AIJob) -> dict[str, Any] | None:
    canonical_result = job.canonical_result
    if not isinstance(canonical_result, dict):
        return None
    signals = canonical_result.get("signals")
    return signals if isinstance(signals, dict) else None


def _short_drama_success_data_from_signals(signals: dict[str, Any]) -> dict[str, Any]:
    missing = [key for key in _SHORT_DRAMA_SUCCESS_SIGNAL_KEYS if key not in signals]
    if missing:
        raise ValueError(f"short drama callback signals missing keys: {', '.join(missing)}")
    if not isinstance(signals["t_book_id"], str) or not signals["t_book_id"]:
        raise ValueError("short drama callback signal t_book_id must be a non-empty string")
    if signals["result_status"] not in {"success", "partial_success"}:
        raise ValueError("short drama callback signal result_status must be success or partial_success")
    if not isinstance(signals["validation_issue_count"], int) or signals["validation_issue_count"] < 0:
        raise ValueError("short drama callback signal validation_issue_count must be a non-negative integer")
    if not isinstance(signals["validation_issues"], list):
        raise ValueError("short drama callback signal validation_issues must be an array")
    if not isinstance(signals["reason_codes"], list):
        raise ValueError("short drama callback signal reason_codes must be an array")
    if not isinstance(signals["subtitle_language"], str) or not signals["subtitle_language"]:
        raise ValueError("short drama callback signal subtitle_language must be a non-empty string")
    if not isinstance(signals["requested_schema_language"], str) or not signals["requested_schema_language"]:
        raise ValueError("short drama callback signal requested_schema_language must be a non-empty string")
    return {key: signals[key] for key in _SHORT_DRAMA_SUCCESS_SIGNAL_KEYS}


def _short_drama_callback_data(job: AIJob) -> dict[str, Any]:
    from app.services.job_runtime import job_params_from_job

    signals = _canonical_signals(job)
    if job.status == "succeeded":
        if signals is None:
            raise ValueError("short drama callback requires canonical_result.signals when job succeeded")
        return _short_drama_success_data_from_signals(signals)

    data: dict[str, Any] = {}
    t_book_id = signals.get("t_book_id") if signals is not None else None
    if not isinstance(t_book_id, str) or not t_book_id:
        params = job_params_from_job(job)
        t_book_id = params.get("t_book_id")
    if isinstance(t_book_id, str) and t_book_id:
        data["t_book_id"] = t_book_id
    return data


def _callback_data(job: AIJob) -> dict[str, Any]:
    if job.job_type in {"short_drama.tagging.initial", "short_drama.tagging.incremental"}:
        return _short_drama_callback_data(job)
    return job.result if isinstance(job.result, dict) else {}


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
            envelope = CallbackResponseEnvelope.model_validate(parsed)
        except Exception as exc:
            return {
                "format": "v1",
                "valid": False,
                "error": str(exc)[:500],
            }
        mismatches: list[dict[str, Any]] = []
        for key in ("event", "event_id", "job_id", "client_request_id", "job_type", "status"):
            expected = callback_body.get(key)
            actual = envelope.model_dump(mode="json").get(key)
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
                        logger.warning(
                            "callback_response_contract_mismatch job_id=%s summary=%s",
                            job.id,
                            response_summary,
                        )
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
