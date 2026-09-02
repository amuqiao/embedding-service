from __future__ import annotations

from app.api.operations import OperationSpec


EXAMPLE_BUSINESS_PACKAGE_PING = OperationSpec(
    operation_id="example_business_package_ping",
    channel="http",
    method="GET",
    path="/example-business-package/ping",
    success_status=200,
    auth_boundary="service bearer token (locally disable-able) + caller id header (optionally ignored)",
    request_schema=None,
    response_data_schema="ExampleBusinessPackagePingResponse",
    error_codes=frozenset({"UNAUTHORIZED", "FORBIDDEN", "INTERNAL_ERROR"}),
    idempotency_key=None,
    side_effects=(),
    log_events=("request_completed", "request_failed"),
)


OPERATIONS = (EXAMPLE_BUSINESS_PACKAGE_PING,)
