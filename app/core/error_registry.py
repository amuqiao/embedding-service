from dataclasses import dataclass


@dataclass(frozen=True)
class ErrorSpec:
    code: int
    reason: str
    msg: str
    retryable: bool = False


_SPECS: dict[str, ErrorSpec] = {
    "MALFORMED_JSON": ErrorSpec(400001, "MALFORMED_JSON", "malformed json"),
    "UNAUTHORIZED": ErrorSpec(401001, "UNAUTHORIZED", "missing or invalid service token"),
    "FORBIDDEN": ErrorSpec(403001, "FORBIDDEN", "caller forbidden"),
    "NOT_FOUND": ErrorSpec(404001, "NOT_FOUND", "resource not found"),
    "JOB_NOT_FOUND": ErrorSpec(404001, "JOB_NOT_FOUND", "job not found"),
    "METHOD_NOT_ALLOWED": ErrorSpec(405001, "METHOD_NOT_ALLOWED", "method not allowed"),
    "CLIENT_REQUEST_ID_CONFLICT": ErrorSpec(
        409001,
        "CLIENT_REQUEST_ID_CONFLICT",
        "duplicate client_request_id",
    ),
    "INVALID_JOB_TYPE": ErrorSpec(422001, "INVALID_JOB_TYPE", "invalid job_type"),
    "INVALID_JOB_PARAMS": ErrorSpec(422002, "INVALID_JOB_PARAMS", "invalid job_params"),
    "INVALID_INPUT": ErrorSpec(422003, "INVALID_INPUT", "invalid input"),
    "MODEL_NOT_AVAILABLE": ErrorSpec(422004, "MODEL_NOT_AVAILABLE", "model not available"),
    "QUEUE_FULL": ErrorSpec(503001, "QUEUE_FULL", "service unavailable", retryable=True),
    "AI_PROVIDER_FAILED": ErrorSpec(502001, "AI_PROVIDER_FAILED", "ai provider failed", retryable=True),
    "MODEL_CALL_FAILED": ErrorSpec(502001, "MODEL_CALL_FAILED", "ai provider failed", retryable=True),
    "MODEL_CALL_TIMEOUT": ErrorSpec(504001, "MODEL_CALL_TIMEOUT", "model call timeout", retryable=True),
    "INTERNAL_ERROR": ErrorSpec(500001, "INTERNAL_ERROR", "internal error"),
    "JOB_PREREQUISITE_CHECK_FAILED": ErrorSpec(
        500002,
        "JOB_PREREQUISITE_CHECK_FAILED",
        "job prerequisite check failed",
    ),
    "JOB_STATE_TRANSITION_CONFLICT": ErrorSpec(
        500003,
        "JOB_STATE_TRANSITION_CONFLICT",
        "job state transition conflict",
        retryable=True,
    ),
    "JOB_VIEW_CONTRACT_INVALID": ErrorSpec(500004, "JOB_VIEW_CONTRACT_INVALID", "job view contract invalid"),
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
    if spec is not None:
        return spec
    if status_code is not None:
        status_family = status_code if status_code in _STATUS_DEFAULTS else (status_code // 100) * 100
        fallback = _STATUS_DEFAULTS.get(status_family)
        if fallback is not None:
            return ErrorSpec(fallback.code, reason, fallback.msg, fallback.retryable)
    return ErrorSpec(500001, reason or "INTERNAL_ERROR", "internal error")
