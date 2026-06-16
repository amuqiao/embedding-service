import logging
import re
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.routes import health, jobs, meta, mock_interfaces
from app.core.exceptions import AppError
from app.core.logging import configure_logging, set_request_id
from app.core.config import settings
from app.workflows.register import register_all_workflows

register_all_workflows()

configure_logging()

logger = logging.getLogger(__name__)
logger.info("app_start service=%s version=0.1.0", settings.SERVICE_NAME)

app = FastAPI(title=settings.SERVICE_TITLE, version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


_HEALTH_PATHS = {"/health", "/healthz"}
# 只允许 ASCII 字母、数字、连字符、下划线，最长 128 字符
_REQUEST_ID_RE = re.compile(r'^[a-zA-Z0-9\-_]{1,128}$')


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        raw_id = request.headers.get("X-Request-ID", "")
        request_id = raw_id if (raw_id and _REQUEST_ID_RE.match(raw_id)) else str(uuid.uuid4())
        request.state.request_id = request_id
        set_request_id(request_id)
        started = time.monotonic()
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = int((time.monotonic() - started) * 1000)
            logger.exception(
                "request_failed method=%s path=%s duration_ms=%d",
                request.method, request.url.path, duration_ms,
            )
            raise
        duration_ms = int((time.monotonic() - started) * 1000)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Response-Time-Ms"] = str(duration_ms)
        if request.url.path not in _HEALTH_PATHS:
            logger.info(
                "request_completed method=%s path=%s status=%d duration_ms=%d",
                request.method, request.url.path, response.status_code, duration_ms,
            )
        return response


app.add_middleware(RequestIDMiddleware)


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": exc.message, "details": exc.details}},
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content=jsonable_encoder({
            "error": {
                "code": "INVALID_INPUT",
                "message": "Request validation failed",
                "details": {"errors": exc.errors()},
            }
        }),
    )


API_PREFIX = settings.SERVICE_API_PREFIX
app.include_router(health.router)
app.include_router(meta.router, prefix=API_PREFIX)
app.include_router(jobs.router, prefix=API_PREFIX)
app.include_router(mock_interfaces.router)
