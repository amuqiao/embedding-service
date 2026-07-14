from __future__ import annotations

from typing import Any

from app.core.error_registry import get_error_spec
from app.jobs import registry as job_registry

_FALLBACK_PUBLIC_ERROR = "JOB_EXECUTION_FAILED"


def project_public_job_error(job_type: str, error: dict[str, Any]) -> dict[str, Any]:
    reason = str(error.get("code") or _FALLBACK_PUBLIC_ERROR)
    try:
        spec = get_error_spec(reason)
    except KeyError:
        spec = None
    try:
        job_spec = job_registry.get_job_type_spec(job_type)
        allowed_error_codes = job_spec.error_codes
    except KeyError:
        allowed_error_codes = frozenset({reason})
    if spec is not None and reason in allowed_error_codes and spec.visibility == "public":
        return error

    target_reason = _projected_reason(reason)
    return {
        "code": target_reason,
        "message": get_error_spec(target_reason).msg,
        "details": {"internal_reason": reason},
    }


def _projected_reason(reason: str) -> str:
    try:
        spec = get_error_spec(reason)
    except KeyError:
        return _FALLBACK_PUBLIC_ERROR
    if spec.projection_targets:
        return sorted(spec.projection_targets)[0]
    return _FALLBACK_PUBLIC_ERROR
