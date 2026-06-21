from __future__ import annotations

from typing import Any

from app.core.error_registry import get_error_spec
from app.schemas.common import StrictBaseModel
from app.schemas.envelope import error_resp


class JobErrorDetail(StrictBaseModel):
    reason: str
    details: dict[str, Any]
    retryable: bool = False


class CallbackErrorDetail(StrictBaseModel):
    reason: str
    details: dict[str, Any]
    retryable: bool = False


ErrorDetail = JobErrorDetail


class ErrorData(StrictBaseModel):
    error: JobErrorDetail


def build_error_envelope(
    *,
    reason: str,
    request_id: str,
    details: dict[str, Any] | None = None,
    status_code: int = 500,
) -> tuple[int, dict[str, Any]]:
    spec = get_error_spec(reason)
    body = error_resp(
        code=spec.code,
        msg=spec.msg,
        data=details or None,
        request_id=request_id,
    ).model_dump(mode="json")
    return spec.http_status, body
