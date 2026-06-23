from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


OperationChannel = Literal["http", "callback", "external_write", "internal_service"]


class OperationID:
    LIST_MODELS = "list_models"
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
    auth_boundary: str
    request_schema: str | None
    response_data_schema: str
    error_codes: frozenset[str]
    idempotency_key: str | None
    side_effects: tuple[str, ...]
    log_events: tuple[str, ...]
    metrics: tuple[str, ...] = ()
    change_policy: str = "current_schema_only"


_SERVICE_AUTH_ERRORS = frozenset({"UNAUTHORIZED", "FORBIDDEN", "INTERNAL_ERROR"})
_SERVICE_AUTH_BOUNDARY = "service bearer token (locally disable-able) + caller id header (optionally ignored)"

_OPERATIONS: dict[str, OperationSpec] = {
    OperationID.LIST_MODELS: OperationSpec(
        operation_id=OperationID.LIST_MODELS,
        channel="http",
        method="GET",
        path="/models",
        auth_boundary=_SERVICE_AUTH_BOUNDARY,
        request_schema=None,
        response_data_schema="ModelsResponse",
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
        auth_boundary=_SERVICE_AUTH_BOUNDARY,
        request_schema=None,
        response_data_schema="PromptTemplatesResponse",
        error_codes=_SERVICE_AUTH_ERRORS,
        idempotency_key=None,
        side_effects=(),
        log_events=("request_completed", "request_failed"),
    ),
    OperationID.CREATE_AI_JOB: OperationSpec(
        operation_id=OperationID.CREATE_AI_JOB,
        channel="http",
        method="POST",
        path="/jobs",
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
        side_effects=("db:jobs", "broker:taskiq", "storage:runtime_snapshot"),
        log_events=("request_completed", "request_failed"),
    ),
    OperationID.GET_AI_JOB: OperationSpec(
        operation_id=OperationID.GET_AI_JOB,
        channel="http",
        method="GET",
        path="/jobs/{job_id}",
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


def get_operation_spec(operation_id: str) -> OperationSpec:
    return _OPERATIONS[operation_id]


def all_operation_specs() -> dict[str, OperationSpec]:
    return dict(_OPERATIONS)


def all_operation_ids() -> set[str]:
    return set(_OPERATIONS)
