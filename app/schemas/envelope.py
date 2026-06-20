from __future__ import annotations

import time
from typing import Any, Generic, TypeVar

from fastapi import Request
from pydantic import Field

from app.schemas.common import StrictBaseModel

TData = TypeVar("TData")


class ResponseEnvelope(StrictBaseModel, Generic[TData]):
    code: int
    msg: str
    data: TData
    request_id: str
    server_time: int = Field(ge=0)


def build_response_envelope(
    *,
    data: Any,
    request_id: str,
    code: int = 0,
    msg: str = "success",
) -> dict[str, Any]:
    return {
        "code": code,
        "msg": msg,
        "data": data,
        "request_id": request_id,
        "server_time": int(time.time()),
    }


def request_id_from_request(request: Request) -> str:
    return getattr(request.state, "request_id", "-")


def success_envelope(data: TData, request: Request) -> ResponseEnvelope[TData]:
    return ResponseEnvelope.model_validate(
        build_response_envelope(
            data=data,
            request_id=request_id_from_request(request),
        )
    )
