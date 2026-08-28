from __future__ import annotations

from typing import Any

from smoke.harness.callback_capture import CallbackExpectation


def job_callback_expectation(
    *,
    job_id: str | None = None,
    event: str | None = None,
    job_status: str | None = None,
) -> CallbackExpectation:
    parts = []
    if job_id is not None:
        parts.append(f"job_id={job_id}")
    if event is not None:
        parts.append(f"event={event}")
    if job_status is not None:
        parts.append(f"job_status={job_status}")

    def matcher(captured_event: dict[str, Any]) -> bool:
        body = captured_event.get("body")
        if not isinstance(body, dict):
            return False
        if event is not None and body.get("event") != event:
            return False
        job = body.get("job")
        if not isinstance(job, dict):
            return False
        if job_id is not None and str(job.get("job_id")) != job_id:
            return False
        if job_status is not None and job.get("job_status") != job_status:
            return False
        return True

    return CallbackExpectation(
        description=", ".join(parts) if parts else "job callback",
        matcher=matcher,
    )
