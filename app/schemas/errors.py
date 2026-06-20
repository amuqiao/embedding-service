from __future__ import annotations

from typing import Any

from app.core.error_registry import get_error_spec
from app.schemas.common import StrictBaseModel
from app.schemas.envelope import build_response_envelope


class ErrorDetail(StrictBaseModel):
    reason: str
    details: dict[str, Any]
    retryable: bool = False


class ErrorData(StrictBaseModel):
    error: ErrorDetail


def build_error_envelope(
    *,
    reason: str,
    request_id: str,
    details: dict[str, Any] | None = None,
    status_code: int = 500,
) -> tuple[int, dict[str, Any]]:
    spec = get_error_spec(reason, status_code)
    body = build_response_envelope(
        data={
            "error": {
                "reason": spec.reason,
                "details": details or {},
                "retryable": spec.retryable,
            }
        },
        request_id=request_id,
        code=spec.code,
        msg=spec.msg,
    )
    return status_code, body
