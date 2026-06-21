import logging
import re
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.routes import health, jobs, meta
from app.core.exceptions import AppError
from app.core.logging import configure_logging, set_request_id
from app.core.config import settings
from app.schemas.errors import build_error_envelope
from app.jobs.types.register import register_all_job_types

logger = logging.getLogger(__name__)

API_PREFIX = settings.SERVICE_API_PREFIX


def _remove_http_bearer_security(schema: dict) -> None:
    security_schemes = schema.get("components", {}).get("securitySchemes")
    if security_schemes:
        security_schemes.pop("HTTPBearer", None)
        if not security_schemes:
            schema.get("components", {}).pop("securitySchemes", None)

    for path_item in schema.get("paths", {}).values():
        for operation in path_item.values():
            if not isinstance(operation, dict) or "security" not in operation:
                continue
            operation["security"] = [
                item for item in operation["security"]
                if "HTTPBearer" not in item
            ]
            if not operation["security"]:
                operation.pop("security", None)


def _remove_caller_id_header_parameter(schema: dict) -> None:
    for path_item in schema.get("paths", {}).values():
        for operation in path_item.values():
            if not isinstance(operation, dict) or "parameters" not in operation:
                continue
            operation["parameters"] = [
                parameter for parameter in operation["parameters"]
                if not (
                    parameter.get("in") == "header"
                    and parameter.get("name") == "X-AI-Service-Caller-ID"
                )
            ]
            if not operation["parameters"]:
                operation.pop("parameters", None)


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


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "-")


def bootstrap_runtime() -> None:
    register_all_job_types()
    configure_logging()
    logger.info("app_start service=%s version=0.1.0", settings.SERVICE_NAME)


def install_openapi(application: FastAPI) -> None:
    def custom_openapi() -> dict:
        if application.openapi_schema:
            return application.openapi_schema

        schema = get_openapi(
            title=application.title,
            version=application.version,
            routes=application.routes,
        )
        if settings.DISABLE_HTTP_AUTH_HEADER:
            _remove_http_bearer_security(schema)
        if settings.DISABLE_CALLER_ID_HEADER:
            _remove_caller_id_header_parameter(schema)
        application.openapi_schema = schema
        return application.openapi_schema

    application.openapi = custom_openapi


def install_middlewares(application: FastAPI) -> None:
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.add_middleware(RequestIDMiddleware)


def install_exception_handlers(application: FastAPI) -> None:
    @application.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        status_code, body = build_error_envelope(
            reason=exc.code,
            request_id=_request_id(request),
            details=exc.details,
            status_code=exc.status_code,
        )
        return JSONResponse(
            status_code=status_code,
            content=jsonable_encoder(body),
        )

    @application.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        status_code, body = build_error_envelope(
            reason="INVALID_INPUT",
            request_id=_request_id(request),
            details={"errors": exc.errors()},
            status_code=422,
        )
        return JSONResponse(
            status_code=status_code,
            content=jsonable_encoder(body),
        )

    @application.exception_handler(StarletteHTTPException)
    async def http_error_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        reason = {
            404: "NOT_FOUND",
            405: "METHOD_NOT_ALLOWED",
        }.get(exc.status_code, "HTTP_ERROR")
        details = {} if isinstance(exc.detail, str) else {"detail": exc.detail}
        status_code, body = build_error_envelope(
            reason=reason,
            request_id=_request_id(request),
            details=details,
            status_code=exc.status_code,
        )
        return JSONResponse(
            status_code=status_code,
            content=jsonable_encoder(body),
            headers=getattr(exc, "headers", None),
        )

    @application.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled_exception method=%s path=%s", request.method, request.url.path)
        status_code, body = build_error_envelope(
            reason="INTERNAL_ERROR",
            request_id=_request_id(request),
            details={},
            status_code=500,
        )
        return JSONResponse(
            status_code=status_code,
            content=jsonable_encoder(body),
        )


def include_routes(application: FastAPI) -> None:
    application.include_router(health.router)
    application.include_router(meta.router, prefix=API_PREFIX)
    application.include_router(jobs.router, prefix=API_PREFIX)


def create_app() -> FastAPI:
    bootstrap_runtime()
    application = FastAPI(title=settings.SERVICE_TITLE, version="0.1.0")
    install_openapi(application)
    install_middlewares(application)
    install_exception_handlers(application)
    include_routes(application)
    return application


app = create_app()
