from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


OperationChannel = Literal["http", "callback", "external_write", "internal_service"]


class OperationID:
    LIST_MODELS = "list_models"
    LIST_LANGUAGES = "list_languages"
    LIST_PROMPT_TEMPLATES = "list_prompt_templates"
    CREATE_AI_JOB = "create_ai_job"
    GET_AI_JOB = "get_ai_job"
    GET_JOB_BILLING = "get_job_billing"


@dataclass(frozen=True)
class OperationSpec:
    operation_id: str
    channel: OperationChannel
    method: str
    path: str
    success_status: int
    auth_boundary: str
    request_schema: str | None
    response_data_schema: str
    error_codes: frozenset[str]
    idempotency_key: str | None
    side_effects: tuple[str, ...]
    log_events: tuple[str, ...]
    metrics: tuple[str, ...] = ()
    change_policy: str = "current_schema_only"
    response_model_exclude_none: bool = False


_SERVICE_AUTH_ERRORS = frozenset({"UNAUTHORIZED", "FORBIDDEN", "INTERNAL_ERROR"})
_SERVICE_AUTH_BOUNDARY = "service bearer token (locally disable-able) + caller id header (optionally ignored)"

_CORE_OPERATIONS: dict[str, OperationSpec] = {
    OperationID.LIST_MODELS: OperationSpec(
        operation_id=OperationID.LIST_MODELS,
        channel="http",
        method="GET",
        path="/models",
        success_status=200,
        auth_boundary=_SERVICE_AUTH_BOUNDARY,
        request_schema=None,
        response_data_schema="ModelsResponse",
        error_codes=frozenset({*_SERVICE_AUTH_ERRORS, "INVALID_JOB_TYPE"}),
        idempotency_key=None,
        side_effects=(),
        log_events=("request_completed", "request_failed"),
        response_model_exclude_none=True,
    ),
    OperationID.LIST_LANGUAGES: OperationSpec(
        operation_id=OperationID.LIST_LANGUAGES,
        channel="http",
        method="GET",
        path="/languages",
        success_status=200,
        auth_boundary=_SERVICE_AUTH_BOUNDARY,
        request_schema=None,
        response_data_schema="LanguagesResponse",
        error_codes=_SERVICE_AUTH_ERRORS,
        idempotency_key=None,
        side_effects=(),
        log_events=("request_completed", "request_failed"),
    ),
    OperationID.LIST_PROMPT_TEMPLATES: OperationSpec(
        operation_id=OperationID.LIST_PROMPT_TEMPLATES,
        channel="http",
        method="GET",
        path="/prompt-templates",
        success_status=200,
        auth_boundary=_SERVICE_AUTH_BOUNDARY,
        request_schema=None,
        response_data_schema="PromptTemplateResponseData",
        error_codes=frozenset({*_SERVICE_AUTH_ERRORS, "INVALID_JOB_TYPE", "INVALID_INPUT"}),
        idempotency_key=None,
        side_effects=(),
        log_events=("request_completed", "request_failed"),
    ),
    OperationID.CREATE_AI_JOB: OperationSpec(
        operation_id=OperationID.CREATE_AI_JOB,
        channel="http",
        method="POST",
        path="/jobs",
        success_status=200,
        auth_boundary=_SERVICE_AUTH_BOUNDARY,
        request_schema="CreateJobRequest",
        response_data_schema="JobResponseData",
        error_codes=frozenset(
            {
                *_SERVICE_AUTH_ERRORS,
                "CLIENT_REQUEST_ID_CONFLICT",
                "INVALID_INPUT",
                "INVALID_JOB_TYPE",
                "INVALID_JOB_PARAMS",
                "MODEL_NOT_AVAILABLE",
                "QUEUE_FULL",
                "JOB_PREREQUISITE_CHECK_FAILED",
            }
        ),
        idempotency_key="caller_id + client_request_id",
        side_effects=(
            "db:job_submission_keys",
            "db:job_aggregates",
            "db:job_execution_attempts",
            "db:dispatch_outbox",
            "broker:taskiq",
            "storage:runtime_snapshot",
        ),
        log_events=("request_completed", "request_failed"),
    ),
    OperationID.GET_AI_JOB: OperationSpec(
        operation_id=OperationID.GET_AI_JOB,
        channel="http",
        method="GET",
        path="/jobs/{job_id}",
        success_status=200,
        auth_boundary=f"{_SERVICE_AUTH_BOUNDARY} + caller owned job",
        request_schema=None,
        response_data_schema="JobResponseData",
        error_codes=frozenset(
            {
                *_SERVICE_AUTH_ERRORS,
                "JOB_NOT_FOUND",
                "JOB_VIEW_CONTRACT_INVALID",
            }
        ),
        idempotency_key=None,
        side_effects=(),
        log_events=("request_completed", "request_failed"),
    ),
    OperationID.GET_JOB_BILLING: OperationSpec(
        operation_id=OperationID.GET_JOB_BILLING,
        channel="http",
        method="GET",
        path="/jobs/{job_id}/billing",
        success_status=200,
        auth_boundary=f"{_SERVICE_AUTH_BOUNDARY} + caller owned job",
        request_schema=None,
        response_data_schema="JobBillingResponseData",
        error_codes=frozenset(
            {
                *_SERVICE_AUTH_ERRORS,
                "JOB_NOT_FOUND",
                "BILLING_DISABLED",
                "BILLING_SCOPE_NOT_TERMINAL",
            }
        ),
        idempotency_key=None,
        side_effects=(),
        log_events=("request_completed", "request_failed"),
    ),
}

_business_operations: dict[str, OperationSpec] = {}


def _http_route_key(spec: OperationSpec) -> tuple[str, str] | None:
    if spec.channel != "http":
        return None
    return spec.method.upper(), spec.path


def replace_business_operation_specs(specs: tuple[OperationSpec, ...]) -> None:
    operations: dict[str, OperationSpec] = {}
    routes: dict[tuple[str, str], str] = {
        route_key: spec.operation_id
        for spec in _CORE_OPERATIONS.values()
        if (route_key := _http_route_key(spec)) is not None
    }
    for spec in specs:
        if not isinstance(spec, OperationSpec):
            raise TypeError("business operation must be OperationSpec")
        if spec.operation_id in _CORE_OPERATIONS:
            raise ValueError(f"business operation duplicates core operation: {spec.operation_id}")
        if spec.operation_id in operations:
            raise ValueError(f"duplicate business operation: {spec.operation_id}")
        route_key = _http_route_key(spec)
        if route_key is not None:
            existing_operation_id = routes.get(route_key)
            if existing_operation_id is not None:
                method, path = route_key
                raise ValueError(
                    "business operation duplicates http route: "
                    f"{method} {path} ({existing_operation_id})"
                )
            routes[route_key] = spec.operation_id
        operations[spec.operation_id] = spec
    global _business_operations
    _business_operations = operations


def business_operation_specs() -> dict[str, OperationSpec]:
    return dict(_business_operations)


def get_operation_spec(operation_id: str) -> OperationSpec:
    return all_operation_specs()[operation_id]


def all_operation_specs() -> dict[str, OperationSpec]:
    return {**_CORE_OPERATIONS, **_business_operations}


def all_operation_ids() -> set[str]:
    return set(all_operation_specs())


def operation_path(operation_id: str) -> str:
    return get_operation_spec(operation_id).path


def operation_responses(operation_id: str) -> dict[int, dict[str, object]]:
    return operation_responses_for_spec(get_operation_spec(operation_id))


def operation_responses_for_spec(spec: OperationSpec) -> dict[int, dict[str, object]]:
    from app.core.error_registry import get_error_spec

    grouped: dict[int, list[str]] = {}
    for reason in sorted(spec.error_codes):
        status = get_error_spec(reason).http_status
        grouped.setdefault(status, []).append(reason)
    return {
        status: {"description": ", ".join(reasons)}
        for status, reasons in grouped.items()
    }


def operation_route_kwargs(operation_id: str) -> dict[str, Any]:
    return operation_route_kwargs_for_spec(get_operation_spec(operation_id))


def operation_route_kwargs_for_spec(spec: OperationSpec) -> dict[str, Any]:
    from app.schemas.registry import get_schema

    kwargs: dict[str, Any] = {
        "operation_id": spec.operation_id,
        "response_model": get_schema(spec.response_data_schema),
        "status_code": spec.success_status,
        "responses": operation_responses_for_spec(spec),
    }
    if spec.response_model_exclude_none:
        kwargs["response_model_exclude_none"] = True
    return kwargs
