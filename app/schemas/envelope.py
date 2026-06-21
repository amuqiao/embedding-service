from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Generic, TypeVar

from fastapi import Request

from app.schemas.common import StrictBaseModel

TData = TypeVar("TData")


class HttpEnvelope(StrictBaseModel, Generic[TData]):
    code: str
    msg: str
    data: TData | None
    request_id: str
    server_time: str


class ErrorEnvelope(StrictBaseModel):
    code: str
    msg: str
    data: Any | None
    request_id: str
    server_time: str


ResponseEnvelope = HttpEnvelope


def server_time_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_response_envelope(
    *,
    data: Any,
    request_id: str,
    code: str = "0",
    msg: str = "success",
) -> dict[str, Any]:
    return {
        "code": code,
        "msg": msg,
        "data": data,
        "request_id": request_id,
        "server_time": server_time_now(),
    }


def request_id_from_request(request: Request) -> str:
    return getattr(request.state, "request_id", "-")


def success_resp(data: TData | None, request_id: str) -> HttpEnvelope[TData]:
    return HttpEnvelope[TData].model_validate(
        build_response_envelope(
            data=data,
            request_id=request_id,
        )
    )


def error_resp(
    *,
    code: str,
    msg: str,
    request_id: str,
    data: Any | None = None,
) -> ErrorEnvelope:
    return ErrorEnvelope.model_validate(
        build_response_envelope(
            data=data,
            request_id=request_id,
            code=code,
            msg=msg,
        )
    )


def success_envelope(data: TData, request: Request) -> HttpEnvelope[TData]:
    return success_resp(data, request_id_from_request(request))
