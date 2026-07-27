import logging
import json
import re
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.routes import health, jobs, meta
from app.core.exceptions import AppError
from app.core.logging import configure_logging, set_request_id
from app.core.config import settings
from app.core.database import close_db_engine, init_db_engine
from app.core.error_registry import freeze_error_registry
from app.core.registry_checks import validate_all_registries
from app.schemas.errors import build_error_envelope
from app.schemas.envelope import success_resp
from app.jobs.types.register import register_all_job_types

logger = logging.getLogger(__name__)

API_PREFIX = settings.service.api_prefix


@asynccontextmanager
async def api_lifespan(_application: FastAPI):
    init_db_engine()
    try:
        yield
    finally:
        await close_db_engine()


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


def _http_envelope_schema(data_schema: dict) -> dict:
    return {
        "type": "object",
        "required": ["code", "msg", "data", "request_id", "server_time"],
        "properties": {
            "code": {"type": "string", "example": "0"},
            "msg": {"type": "string", "example": "success"},
            "data": data_schema,
            "request_id": {"type": "string", "minLength": 1, "maxLength": 128},
            "server_time": {"type": "string", "format": "date-time"},
        },
    }


def _error_envelope_schema() -> dict:
    return {
        "type": "object",
        "required": ["code", "msg", "data", "request_id", "server_time"],
        "properties": {
            "code": {"type": "string", "example": "100001"},
            "msg": {"type": "string", "example": "invalid input"},
            "data": {"type": "object", "nullable": True},
            "request_id": {"type": "string", "minLength": 1, "maxLength": 128},
            "server_time": {"type": "string", "format": "date-time"},
        },
    }


def _install_envelope_openapi_contract(schema: dict) -> None:
    components = schema.setdefault("components", {}).setdefault("schemas", {})
    components["ErrorEnvelope"] = _error_envelope_schema()
    request_id_parameter = {
        "name": "X-Request-ID",
        "in": "header",
        "required": False,
        "schema": {
            "type": "string",
            "minLength": 1,
            "maxLength": 128,
            "pattern": r"^[a-zA-Z0-9._:-]{1,128}$",
        },
        "example": "trace-id-123",
        "description": (
            "Optional request trace ID. When omitted, the service generates one. "
            "Invalid values return HTTP 400 with code 100002."
        ),
    }
    for path, path_item in schema.get("paths", {}).items():
        if not path.startswith(API_PREFIX):
            continue
        for operation in path_item.values():
            if not isinstance(operation, dict):
                continue
            parameters = operation.setdefault("parameters", [])
            if not any(parameter.get("name") == "X-Request-ID" and parameter.get("in") == "header" for parameter in parameters):
                parameters.append(request_id_parameter)
            responses = operation.setdefault("responses", {})
            success = responses.get("200")
            if success:
                media = success.get("content", {}).get("application/json")
                if media and "schema" in media:
                    media["schema"] = _http_envelope_schema(media["schema"])
            responses.pop("202", None)
            responses.pop("422", None)
            for status_code in ("400", "401", "403", "404", "405", "409", "500", "502", "503", "504"):
                response = responses.setdefault(status_code, {"description": "ErrorEnvelope"})
                response.setdefault("description", "ErrorEnvelope")
                response["content"] = {
                    "application/json": {
                        "schema": {"$ref": "#/components/schemas/ErrorEnvelope"}
                    }
                }


_HEALTH_PATHS = {"/health", "/healthz"}
_ENVELOPE_FIELDS = {"code", "msg", "data", "request_id", "server_time"}
# 只允许 ASCII 字母、数字、点号、下划线、冒号和连字符，最长 128 字符
_REQUEST_ID_RE = re.compile(r"^[a-zA-Z0-9._:-]{1,128}$")


def _new_request_id() -> str:
    return uuid.uuid4().hex


def _is_standard_json_api_path(path: str) -> bool:
    if path in _HEALTH_PATHS or path in {"/openapi.json", "/docs", "/redoc"}:
        return False
    return path.startswith(API_PREFIX)


def _is_json_response(response: Response) -> bool:
    content_type = response.headers.get("content-type", "")
    return content_type.split(";", 1)[0].lower() == "application/json"


def _filtered_headers(response: Response) -> dict[str, str]:
    return {
        key: value
        for key, value in response.headers.items()
        if key.lower() not in {"content-length", "content-type"}
    }


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        raw_id = request.headers.get("X-Request-ID")
        if raw_id is not None and not _REQUEST_ID_RE.fullmatch(raw_id):
            request_id = _new_request_id()
            request.state.request_id = request_id
            set_request_id(request_id)
            status_code, body = build_error_envelope(
                reason="REQUEST_ID_INVALID",
                request_id=request_id,
                details={
                    "header": "X-Request-ID",
                    "allowed": "ASCII letters, digits, dot, underscore, colon, and hyphen; length 1-128",
                },
            )
            return JSONResponse(
                status_code=status_code,
                content=jsonable_encoder(body),
                headers={"X-Request-ID": request_id},
            )
        request_id = raw_id if raw_id is not None else _new_request_id()
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


class SuccessEnvelopeMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if (
            not _is_standard_json_api_path(request.url.path)
            or response.status_code != 200
            or not _is_json_response(response)
        ):
            return response

        body = b"".join([chunk async for chunk in response.body_iterator])
        if not body:
            payload = None
        else:
            try:
                payload = json.loads(body)
            except json.JSONDecodeError:
                return Response(
                    content=body,
                    status_code=response.status_code,
                    headers=_filtered_headers(response),
                    media_type=response.media_type,
                )

        if isinstance(payload, dict) and set(payload.keys()) == _ENVELOPE_FIELDS:
            return Response(
                content=body,
                status_code=response.status_code,
                headers=_filtered_headers(response),
                media_type="application/json",
            )

        envelope = success_resp(payload, _request_id(request))
        return JSONResponse(
            status_code=200,
            content=jsonable_encoder(envelope),
            headers=_filtered_headers(response),
        )


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "-")


def bootstrap_runtime() -> None:
    from app.capabilities.registry import freeze as freeze_capability_registry
    register_all_job_types()
    from app.tools.registry import freeze as freeze_tool_registry

    freeze_error_registry()
    freeze_tool_registry()
    freeze_capability_registry()
    from app.core.model_registry import validate_model_catalog

    validate_model_catalog()
    configure_logging()
    logger.info("app_start service=%s version=0.1.0", settings.service.name)


def install_openapi(application: FastAPI) -> None:
    def custom_openapi() -> dict:
        if application.openapi_schema:
            return application.openapi_schema

        schema = get_openapi(
            title=application.title,
            version=application.version,
            routes=application.routes,
        )
        if settings.security.disable_http_auth_header:
            _remove_http_bearer_security(schema)
        if settings.security.disable_caller_id_header:
            _remove_caller_id_header_parameter(schema)
        _install_envelope_openapi_contract(schema)
        application.openapi_schema = schema
        return application.openapi_schema

    application.openapi = custom_openapi


def install_middlewares(application: FastAPI) -> None:
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.security.allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.add_middleware(SuccessEnvelopeMiddleware)
    application.add_middleware(RequestIDMiddleware)


def install_exception_handlers(application: FastAPI) -> None:
    @application.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        status_code, body = build_error_envelope(
            reason=exc.code,
            request_id=_request_id(request),
            details=exc.details,
        )
        return JSONResponse(
            status_code=status_code,
            content=jsonable_encoder(body),
        )

    @application.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {
                "loc": list(error.get("loc", ())),
                "type": str(error.get("type", "")),
                "msg": str(error.get("msg", "")),
            }
            for error in exc.errors()
        ]
        status_code, body = build_error_envelope(
            reason="INVALID_INPUT",
            request_id=_request_id(request),
            details={"errors": errors},
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
        status_code, body = build_error_envelope(
            reason=reason,
            request_id=_request_id(request),
            details=None,
        )
        if reason == "HTTP_ERROR":
            status_code = exc.status_code
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
        )
        return JSONResponse(
            status_code=status_code,
            content=jsonable_encoder(body),
        )


def include_routes(application: FastAPI) -> None:
    application.include_router(health.router)
    application.include_router(meta.router, prefix=API_PREFIX)
    application.include_router(jobs.router, prefix=API_PREFIX)


def include_optional_ops_dashboard(application: FastAPI) -> None:
    if not settings.ops_dashboard.enabled:
        return

    from app.ops_dashboard.router import router as ops_dashboard_router

    application.include_router(ops_dashboard_router)


def create_app() -> FastAPI:
    bootstrap_runtime()
    application = FastAPI(title=settings.service.title, version="0.1.0", lifespan=api_lifespan)
    install_openapi(application)
    install_middlewares(application)
    install_exception_handlers(application)
    include_routes(application)
    include_optional_ops_dashboard(application)
    validate_all_registries(application)
    return application


app = create_app()
