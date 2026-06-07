import hashlib
import hmac
import json
import asyncio
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx

from app.infrastructure.config import settings
from app.models.job import AIJob


def _sign(timestamp: str, body: bytes) -> str:
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
    return {
        "event": event,
        "job_id": str(job.id),
        "job_type": job.job_type,
        "status": job.status,
        "result": job.result_payload,
        "error": job.error_payload,
        "metadata": job.metadata_payload or {},
        "finished_at": job.finished_at.isoformat() if job.finished_at else datetime.now(timezone.utc).isoformat(),
    }


async def deliver_callback(job: AIJob) -> None:
    callback = job.callback_payload or {}
    url = callback.get("url")
    if not url:
        return
    try:
        _validate_callback_url(url)
    except ValueError:
        return

    event = "job.succeeded" if job.status == "succeeded" else "job.failed"
    events = set(callback.get("events") or ["job.succeeded", "job.failed"])
    if event not in events:
        return

    body = json.dumps(build_callback_body(job), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    delays = [0, 10, 30, 60]
    async with httpx.AsyncClient(timeout=settings.CALLBACK_TIMEOUT_SECONDS) as client:
        for delay in delays:
            if delay:
                await asyncio.sleep(delay)
            timestamp = datetime.now(timezone.utc).isoformat()
            headers = {
                "Content-Type": "application/json",
                "X-AI-Service-Job-Id": str(job.id),
                "X-AI-Service-Event": event,
                "X-AI-Service-Timestamp": timestamp,
                "X-AI-Service-Signature": _sign(timestamp, body),
            }
            try:
                response = await client.post(url, content=body, headers=headers)
                if 200 <= response.status_code < 300:
                    return
            except httpx.HTTPError:
                continue
