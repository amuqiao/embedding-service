from dataclasses import dataclass
from typing import Literal

ErrorScope = Literal["global", "http", "job", "callback", "runtime", "integration"]


@dataclass(frozen=True)
class ErrorSpec:
    code: str
    reason: str
    msg: str
    http_status: int
    retryable: bool = False
    scope: ErrorScope = "global"
    owner: str = "core"
    details_schema: str | None = None


_CORE_ERROR_SPECS: dict[str, ErrorSpec] = {
    "INVALID_INPUT": ErrorSpec("100001", "INVALID_INPUT", "invalid input", 400, scope="http"),
    "REQUEST_ID_INVALID": ErrorSpec("100002", "REQUEST_ID_INVALID", "invalid request id", 400, scope="http"),
    "MALFORMED_JSON": ErrorSpec("100003", "MALFORMED_JSON", "malformed json", 400, scope="http"),
    "HTTP_ERROR": ErrorSpec("100004", "HTTP_ERROR", "http error", 400, scope="http"),
    "INVALID_JOB_TYPE": ErrorSpec("100011", "INVALID_JOB_TYPE", "invalid job_type", 400, scope="job", owner="jobs"),
    "INVALID_JOB_PARAMS": ErrorSpec("100012", "INVALID_JOB_PARAMS", "invalid job_params", 400, scope="job", owner="jobs"),
    "MODEL_NOT_AVAILABLE": ErrorSpec("100013", "MODEL_NOT_AVAILABLE", "model not available", 400, scope="job", owner="jobs"),
    "INPUT_TOO_LARGE": ErrorSpec("100014", "INPUT_TOO_LARGE", "input too large", 400, scope="job", owner="jobs"),
    "INPUT_HASH_MISMATCH": ErrorSpec("100015", "INPUT_HASH_MISMATCH", "input hash mismatch", 400, scope="job", owner="jobs"),
    "OSS_OBJECT_NOT_FOUND": ErrorSpec(
        "100016",
        "OSS_OBJECT_NOT_FOUND",
        "oss object not found",
        400,
        scope="integration",
        owner="storage",
    ),
    "OSS_BUCKET_NOT_CONFIGURED": ErrorSpec(
        "100017",
        "OSS_BUCKET_NOT_CONFIGURED",
        "oss bucket not configured",
        400,
        scope="integration",
        owner="storage",
    ),
    "OSS_REGION_NOT_CONFIGURED": ErrorSpec(
        "100018",
        "OSS_REGION_NOT_CONFIGURED",
        "oss region not configured",
        400,
        scope="integration",
        owner="storage",
    ),
    "METHOD_NOT_ALLOWED": ErrorSpec("100405", "METHOD_NOT_ALLOWED", "method not allowed", 405, scope="http"),
    "CLIENT_REQUEST_ID_CONFLICT": ErrorSpec(
        "100409",
        "CLIENT_REQUEST_ID_CONFLICT",
        "client_request_id conflict",
        409,
        scope="job",
        owner="jobs",
    ),
    "ALL_ITEMS_FAILED": ErrorSpec("100420", "ALL_ITEMS_FAILED", "all batch items failed", 502, scope="job", owner="jobs"),
    "UNAUTHORIZED": ErrorSpec("200001", "UNAUTHORIZED", "missing or invalid service token", 401, scope="http"),
    "FORBIDDEN": ErrorSpec("200003", "FORBIDDEN", "caller forbidden", 403, scope="http"),
    "JOB_NOT_FOUND": ErrorSpec("300004", "JOB_NOT_FOUND", "job not found", 404, scope="job", owner="jobs"),
    "NOT_FOUND": ErrorSpec("300404", "NOT_FOUND", "resource not found", 404, scope="http"),
    "INTERNAL_ERROR": ErrorSpec("900500", "INTERNAL_ERROR", "internal error", 500),
    "RUNTIME_CONFIG_MISSING": ErrorSpec(
        "900501",
        "RUNTIME_CONFIG_MISSING",
        "runtime config missing",
        500,
        scope="runtime",
        owner="jobs",
    ),
    "JOB_PREREQUISITE_CHECK_FAILED": ErrorSpec(
        "900505",
        "JOB_PREREQUISITE_CHECK_FAILED",
        "job prerequisite check failed",
        500,
        scope="job",
        owner="jobs",
    ),
    "JOB_STATE_TRANSITION_CONFLICT": ErrorSpec(
        "900506",
        "JOB_STATE_TRANSITION_CONFLICT",
        "job state transition conflict",
        500,
        retryable=True,
        scope="job",
        owner="jobs",
    ),
    "JOB_VIEW_CONTRACT_INVALID": ErrorSpec(
        "900507",
        "JOB_VIEW_CONTRACT_INVALID",
        "job view contract invalid",
        500,
        scope="job",
        owner="jobs",
    ),
    "JOB_RUNTIME_NOT_SUPPORTED": ErrorSpec(
        "900508",
        "JOB_RUNTIME_NOT_SUPPORTED",
        "job runtime not supported",
        500,
        scope="job",
        owner="jobs",
    ),
    "JOB_EXECUTION_FAILED": ErrorSpec("900509", "JOB_EXECUTION_FAILED", "job execution failed", 500, scope="job", owner="jobs"),
    "WORKFLOW_AFTER_SUCCESS_FAILED": ErrorSpec(
        "900510",
        "WORKFLOW_AFTER_SUCCESS_FAILED",
        "workflow after success failed",
        500,
        scope="job",
        owner="jobs",
    ),
    "RUNTIME_REF_MISSING": ErrorSpec("900511", "RUNTIME_REF_MISSING", "runtime reference missing", 500, scope="runtime", owner="jobs"),
    "RUNTIME_REF_INVALID": ErrorSpec("900512", "RUNTIME_REF_INVALID", "runtime reference invalid", 500, scope="runtime", owner="jobs"),
    "RUNTIME_HASH_MISSING": ErrorSpec("900513", "RUNTIME_HASH_MISSING", "runtime hash missing", 500, scope="runtime", owner="jobs"),
    "RUNTIME_HASH_MISMATCH": ErrorSpec("900514", "RUNTIME_HASH_MISMATCH", "runtime hash mismatch", 500, scope="runtime", owner="jobs"),
    "AI_PROVIDER_FAILED": ErrorSpec(
        "900502",
        "AI_PROVIDER_FAILED",
        "ai provider failed",
        502,
        retryable=True,
        scope="integration",
        owner="ai",
    ),
    "MODEL_CALL_FAILED": ErrorSpec(
        "900521",
        "MODEL_CALL_FAILED",
        "ai provider failed",
        502,
        retryable=True,
        scope="integration",
        owner="ai",
    ),
    "MODEL_OUTPUT_INVALID": ErrorSpec("900522", "MODEL_OUTPUT_INVALID", "model output invalid", 502, scope="integration", owner="ai"),
    "MODEL_USAGE_MISSING": ErrorSpec("900531", "MODEL_USAGE_MISSING", "model usage missing", 502, scope="integration", owner="ai"),
    "MODEL_COST_CALCULATION_FAILED": ErrorSpec(
        "900532",
        "MODEL_COST_CALCULATION_FAILED",
        "model cost calculation failed",
        502,
        scope="integration",
        owner="ai",
    ),
    "AI_LEDGER_UPDATE_FAILED": ErrorSpec(
        "900533",
        "AI_LEDGER_UPDATE_FAILED",
        "ai call ledger update failed",
        500,
        scope="integration",
        owner="ai",
    ),
    "BILLING_DISABLED": ErrorSpec("900534", "BILLING_DISABLED", "billing disabled", 403, scope="http", owner="billing"),
    "BILLING_SCOPE_NOT_TERMINAL": ErrorSpec(
        "900535",
        "BILLING_SCOPE_NOT_TERMINAL",
        "billing scope not terminal",
        409,
        scope="http",
        owner="billing",
    ),
    "OSS_FETCH_FAILED": ErrorSpec(
        "900523",
        "OSS_FETCH_FAILED",
        "oss fetch failed",
        502,
        scope="integration",
        owner="storage",
        retryable=True,
    ),
    "OSS_WRITE_FAILED": ErrorSpec(
        "900524",
        "OSS_WRITE_FAILED",
        "oss write failed",
        502,
        retryable=True,
        scope="integration",
        owner="storage",
    ),
    "BROKER_UNAVAILABLE": ErrorSpec(
        "900525",
        "BROKER_UNAVAILABLE",
        "broker unavailable",
        502,
        retryable=True,
        scope="integration",
        owner="broker",
    ),
    "TASKIQ_PUBLISH_FAILED": ErrorSpec(
        "900526",
        "TASKIQ_PUBLISH_FAILED",
        "taskiq publish failed",
        502,
        retryable=True,
        scope="integration",
        owner="broker",
    ),
    "CALLBACK_RESPONSE_CONTRACT_INVALID": ErrorSpec(
        "900527",
        "CALLBACK_RESPONSE_CONTRACT_INVALID",
        "callback response contract invalid",
        502,
        retryable=True,
        scope="callback",
        owner="callbacks",
    ),
    "CALLBACK_HTTP_ERROR": ErrorSpec(
        "900528",
        "CALLBACK_HTTP_ERROR",
        "callback http error",
        502,
        retryable=True,
        scope="callback",
        owner="callbacks",
    ),
    "CALLBACK_REQUEST_ERROR": ErrorSpec(
        "900529",
        "CALLBACK_REQUEST_ERROR",
        "callback request error",
        502,
        retryable=True,
        scope="callback",
        owner="callbacks",
    ),
    "CALLBACK_ACK_REJECTED": ErrorSpec(
        "900530",
        "CALLBACK_ACK_REJECTED",
        "callback acknowledgment rejected",
        502,
        retryable=True,
        scope="callback",
        owner="callbacks",
    ),
    "QUEUE_FULL": ErrorSpec("900503", "QUEUE_FULL", "service unavailable", 503, retryable=True, scope="job", owner="jobs"),
    "MODEL_CALL_TIMEOUT": ErrorSpec(
        "900504",
        "MODEL_CALL_TIMEOUT",
        "model call timeout",
        504,
        retryable=True,
        scope="integration",
        owner="ai",
    ),
    "JOB_TIMEOUT": ErrorSpec("900541", "JOB_TIMEOUT", "job timeout", 504, retryable=True, scope="job", owner="jobs"),
    "WORKFLOW_CHILD_FAILED": ErrorSpec(
        "900542",
        "WORKFLOW_CHILD_FAILED",
        "workflow child job failed",
        500,
        scope="job",
        owner="jobs",
    ),
    "CALLBACK_URL_INVALID": ErrorSpec("900551", "CALLBACK_URL_INVALID", "callback url invalid", 500, scope="callback", owner="callbacks"),
    "CALLBACK_BODY_INVALID": ErrorSpec("900552", "CALLBACK_BODY_INVALID", "callback body invalid", 500, scope="callback", owner="callbacks"),
}

_SPECS: dict[str, ErrorSpec] = {}
_FROZEN = False


def register_error_specs(specs: dict[str, ErrorSpec]) -> None:
    if not specs:
        return
    if _FROZEN:
        mismatched = [reason for reason, spec in specs.items() if _SPECS.get(reason) != spec]
        if mismatched:
            raise RuntimeError(f"error registry is frozen; cannot register: {sorted(mismatched)}")
        return
    for reason, spec in specs.items():
        if reason != spec.reason:
            raise ValueError(f"error registry key mismatch: {reason} != {spec.reason}")
        existing = _SPECS.get(reason)
        if existing is not None:
            if existing == spec:
                continue
            raise ValueError(f"duplicate error reason {reason}")
        for registered_reason, registered_spec in _SPECS.items():
            if registered_spec.code == spec.code:
                raise ValueError(f"duplicate error code {spec.code}: {registered_reason}, {reason}")
        _SPECS[reason] = spec


def freeze_error_registry() -> None:
    global _FROZEN

    _FROZEN = True


def error_registry_is_frozen() -> bool:
    return _FROZEN


register_error_specs(_CORE_ERROR_SPECS)

def get_error_spec(reason: str) -> ErrorSpec:
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
