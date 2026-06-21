from dataclasses import dataclass
from typing import Literal

ErrorScope = Literal["global", "http", "job", "callback", "runtime", "integration"]


@dataclass(frozen=True)
class ErrorSpec:
    code: int
    reason: str
    msg: str
    http_status: int
    retryable: bool = False
    scope: ErrorScope = "global"
    owner: str = "core"
    details_schema: str | None = None


_SPECS: dict[str, ErrorSpec] = {
    "MALFORMED_JSON": ErrorSpec(400001, "MALFORMED_JSON", "malformed json", 400, scope="http"),
    "HTTP_ERROR": ErrorSpec(400002, "HTTP_ERROR", "http error", 400, scope="http"),
    "UNAUTHORIZED": ErrorSpec(401001, "UNAUTHORIZED", "missing or invalid service token", 401, scope="http"),
    "FORBIDDEN": ErrorSpec(403001, "FORBIDDEN", "caller forbidden", 403, scope="http"),
    "NOT_FOUND": ErrorSpec(404001, "NOT_FOUND", "resource not found", 404, scope="http"),
    "JOB_NOT_FOUND": ErrorSpec(404002, "JOB_NOT_FOUND", "job not found", 404, scope="job", owner="jobs"),
    "METHOD_NOT_ALLOWED": ErrorSpec(405001, "METHOD_NOT_ALLOWED", "method not allowed", 405, scope="http"),
    "CLIENT_REQUEST_ID_CONFLICT": ErrorSpec(
        409001,
        "CLIENT_REQUEST_ID_CONFLICT",
        "duplicate client_request_id",
        409,
        scope="job",
        owner="jobs",
    ),
    "INVALID_JOB_TYPE": ErrorSpec(422001, "INVALID_JOB_TYPE", "invalid job_type", 422, scope="job", owner="jobs"),
    "INVALID_JOB_PARAMS": ErrorSpec(422002, "INVALID_JOB_PARAMS", "invalid job_params", 422, scope="job", owner="jobs"),
    "INVALID_INPUT": ErrorSpec(422003, "INVALID_INPUT", "invalid input", 422, scope="http"),
    "MODEL_NOT_AVAILABLE": ErrorSpec(
        422004,
        "MODEL_NOT_AVAILABLE",
        "model not available",
        422,
        scope="job",
        owner="jobs",
    ),
    "INPUT_TOO_LARGE": ErrorSpec(422005, "INPUT_TOO_LARGE", "input too large", 422, scope="job", owner="jobs"),
    "INPUT_HASH_MISMATCH": ErrorSpec(
        422006,
        "INPUT_HASH_MISMATCH",
        "input hash mismatch",
        422,
        scope="job",
        owner="jobs",
    ),
    "OSS_OBJECT_NOT_FOUND": ErrorSpec(
        422007,
        "OSS_OBJECT_NOT_FOUND",
        "oss object not found",
        422,
        scope="integration",
        owner="storage",
    ),
    "OSS_FETCH_FAILED": ErrorSpec(
        422008,
        "OSS_FETCH_FAILED",
        "oss fetch failed",
        422,
        scope="integration",
        owner="storage",
        retryable=True,
    ),
    "OSS_BUCKET_NOT_CONFIGURED": ErrorSpec(
        422009,
        "OSS_BUCKET_NOT_CONFIGURED",
        "oss bucket not configured",
        422,
        scope="integration",
        owner="storage",
    ),
    "OSS_REGION_NOT_CONFIGURED": ErrorSpec(
        422010,
        "OSS_REGION_NOT_CONFIGURED",
        "oss region not configured",
        422,
        scope="integration",
        owner="storage",
    ),
    "QUEUE_FULL": ErrorSpec(
        503001,
        "QUEUE_FULL",
        "service unavailable",
        503,
        retryable=True,
        scope="job",
        owner="jobs",
    ),
    "BROKER_UNAVAILABLE": ErrorSpec(
        503002,
        "BROKER_UNAVAILABLE",
        "broker unavailable",
        503,
        retryable=True,
        scope="integration",
        owner="broker",
    ),
    "AI_PROVIDER_FAILED": ErrorSpec(
        502001,
        "AI_PROVIDER_FAILED",
        "ai provider failed",
        502,
        retryable=True,
        scope="integration",
        owner="ai",
    ),
    "MODEL_CALL_FAILED": ErrorSpec(
        502002,
        "MODEL_CALL_FAILED",
        "ai provider failed",
        502,
        retryable=True,
        scope="integration",
        owner="ai",
    ),
    "MODEL_OUTPUT_INVALID": ErrorSpec(
        502005,
        "MODEL_OUTPUT_INVALID",
        "model output invalid",
        502,
        scope="integration",
        owner="ai",
    ),
    "MODEL_CALL_TIMEOUT": ErrorSpec(
        504001,
        "MODEL_CALL_TIMEOUT",
        "model call timeout",
        504,
        retryable=True,
        scope="integration",
        owner="ai",
    ),
    "JOB_TIMEOUT": ErrorSpec(
        504002,
        "JOB_TIMEOUT",
        "job timeout",
        504,
        retryable=True,
        scope="job",
        owner="jobs",
    ),
    "INTERNAL_ERROR": ErrorSpec(500001, "INTERNAL_ERROR", "internal error", 500),
    "RUNTIME_CONFIG_MISSING": ErrorSpec(
        500002,
        "RUNTIME_CONFIG_MISSING",
        "runtime config missing",
        500,
        scope="runtime",
        owner="jobs",
    ),
    "JOB_PREREQUISITE_CHECK_FAILED": ErrorSpec(
        500003,
        "JOB_PREREQUISITE_CHECK_FAILED",
        "job prerequisite check failed",
        500,
        scope="job",
        owner="jobs",
    ),
    "JOB_STATE_TRANSITION_CONFLICT": ErrorSpec(
        500004,
        "JOB_STATE_TRANSITION_CONFLICT",
        "job state transition conflict",
        500,
        retryable=True,
        scope="job",
        owner="jobs",
    ),
    "JOB_VIEW_CONTRACT_INVALID": ErrorSpec(
        500005,
        "JOB_VIEW_CONTRACT_INVALID",
        "job view contract invalid",
        500,
        scope="job",
        owner="jobs",
    ),
    "JOB_RUNTIME_NOT_SUPPORTED": ErrorSpec(
        500006,
        "JOB_RUNTIME_NOT_SUPPORTED",
        "job runtime not supported",
        500,
        scope="job",
        owner="jobs",
    ),
    "WORK_ITEM_FAILED": ErrorSpec(
        500007,
        "WORK_ITEM_FAILED",
        "work item failed",
        500,
        scope="job",
        owner="jobs",
    ),
    "WORKFLOW_AFTER_SUCCESS_FAILED": ErrorSpec(
        500008,
        "WORKFLOW_AFTER_SUCCESS_FAILED",
        "workflow after success failed",
        500,
        scope="job",
        owner="jobs",
    ),
    "RUNTIME_REF_MISSING": ErrorSpec(
        500009,
        "RUNTIME_REF_MISSING",
        "runtime reference missing",
        500,
        scope="runtime",
        owner="jobs",
    ),
    "RUNTIME_REF_INVALID": ErrorSpec(
        500010,
        "RUNTIME_REF_INVALID",
        "runtime reference invalid",
        500,
        scope="runtime",
        owner="jobs",
    ),
    "RUNTIME_HASH_MISSING": ErrorSpec(
        500011,
        "RUNTIME_HASH_MISSING",
        "runtime hash missing",
        500,
        scope="runtime",
        owner="jobs",
    ),
    "RUNTIME_HASH_MISMATCH": ErrorSpec(
        500012,
        "RUNTIME_HASH_MISMATCH",
        "runtime hash mismatch",
        500,
        scope="runtime",
        owner="jobs",
    ),
    "OSS_WRITE_FAILED": ErrorSpec(
        500013,
        "OSS_WRITE_FAILED",
        "oss write failed",
        500,
        retryable=True,
        scope="integration",
        owner="storage",
    ),
    "CALLBACK_URL_INVALID": ErrorSpec(
        500014,
        "CALLBACK_URL_INVALID",
        "callback url invalid",
        500,
        scope="callback",
        owner="callbacks",
    ),
    "CALLBACK_BODY_INVALID": ErrorSpec(
        500015,
        "CALLBACK_BODY_INVALID",
        "callback body invalid",
        500,
        scope="callback",
        owner="callbacks",
    ),
    "CALLBACK_RESPONSE_CONTRACT_INVALID": ErrorSpec(
        500016,
        "CALLBACK_RESPONSE_CONTRACT_INVALID",
        "callback response contract invalid",
        500,
        retryable=True,
        scope="callback",
        owner="callbacks",
    ),
    "CALLBACK_HTTP_ERROR": ErrorSpec(
        502003,
        "CALLBACK_HTTP_ERROR",
        "callback http error",
        502,
        retryable=True,
        scope="callback",
        owner="callbacks",
    ),
    "CALLBACK_REQUEST_ERROR": ErrorSpec(
        502004,
        "CALLBACK_REQUEST_ERROR",
        "callback request error",
        502,
        retryable=True,
        scope="callback",
        owner="callbacks",
    ),
    "CALLBACK_ACK_REJECTED": ErrorSpec(
        502006,
        "CALLBACK_ACK_REJECTED",
        "callback acknowledgment rejected",
        502,
        retryable=True,
        scope="callback",
        owner="callbacks",
    ),
}

_STATUS_DEFAULTS: dict[int, ErrorSpec] = {
    400: _SPECS["MALFORMED_JSON"],
    401: _SPECS["UNAUTHORIZED"],
    403: _SPECS["FORBIDDEN"],
    404: _SPECS["NOT_FOUND"],
    405: _SPECS["METHOD_NOT_ALLOWED"],
    422: _SPECS["INVALID_INPUT"],
    500: _SPECS["INTERNAL_ERROR"],
    502: _SPECS["AI_PROVIDER_FAILED"],
    503: _SPECS["QUEUE_FULL"],
    504: _SPECS["MODEL_CALL_TIMEOUT"],
}


def get_error_spec(reason: str, status_code: int | None = None) -> ErrorSpec:
    spec = _SPECS.get(reason)
    if spec is None:
        raise KeyError(f"unknown error reason: {reason or 'INTERNAL_ERROR'}")
    return spec


def all_error_specs() -> dict[str, ErrorSpec]:
    return dict(_SPECS)


def all_error_reasons() -> set[str]:
    return set(_SPECS)


def is_registered_error_reason(reason: str) -> bool:
    return reason in _SPECS
