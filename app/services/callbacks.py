import hashlib
import hmac
import json
import asyncio
import logging
import uuid
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel

from app.core.config import settings
from app.models.job import AIJob
from app.services.jobs import _job_to_response

logger = logging.getLogger(__name__)


class CallbackDeliveryResult(BaseModel):
    status: str
    attempts: int = 0
    last_error: dict | None = None


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


def build_callback_body(job: AIJob) -> dict:
    event = "job.succeeded" if job.status == "succeeded" else "job.failed"
    sent_at = datetime.now(timezone.utc)
    return {
        "event": event,
        "event_id": str(uuid.uuid4()),
        "attempt": (job.callback_attempts or 0) + 1,
        "sent_at": sent_at.isoformat(),
        "job": _job_to_response(job).model_dump(mode="json"),
    }


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

    body = json.dumps(build_callback_body(job), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
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
                if 200 <= response.status_code < 300:
                    return CallbackDeliveryResult(status="delivered", attempts=attempts)
                last_error = {
                    "code": "CALLBACK_HTTP_ERROR",
                    "status_code": response.status_code,
                    "message": response.text[:500],
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
