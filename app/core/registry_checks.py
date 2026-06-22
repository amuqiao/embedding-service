from __future__ import annotations

from fastapi.routing import APIRoute

from app.api.operations import all_operation_specs
from app.core.config import settings
from app.core.error_registry import all_error_reasons, all_error_specs
from app.core.logging import all_log_events
from app.jobs import registry as job_registry
from app.schemas.registry import all_schema_names


def _missing(values: set[str], allowed: set[str]) -> list[str]:
    return sorted(values - allowed)


def validate_error_registry() -> None:
    seen_codes: dict[int, str] = {}
    for reason, spec in all_error_specs().items():
        if reason != spec.reason:
            raise ValueError(f"error registry key mismatch: {reason} != {spec.reason}")
        previous = seen_codes.get(spec.code)
        if previous is not None:
            raise ValueError(f"duplicate error code {spec.code}: {previous}, {reason}")
        seen_codes[spec.code] = reason


def validate_operation_registry() -> None:
    known_errors = all_error_reasons()
    known_events = all_log_events()
    known_schemas = all_schema_names()
    for spec in all_operation_specs().values():
        missing_errors = _missing(set(spec.error_codes), known_errors)
        if missing_errors:
            raise ValueError(f"operation {spec.operation_id} references unknown errors: {missing_errors}")
        missing_events = _missing(set(spec.log_events), known_events)
        if missing_events:
            raise ValueError(f"operation {spec.operation_id} references unknown log events: {missing_events}")
        referenced_schemas = {spec.response_data_schema}
        if spec.request_schema is not None:
            referenced_schemas.add(spec.request_schema)
        missing_schemas = _missing(referenced_schemas, known_schemas)
        if missing_schemas:
            raise ValueError(f"operation {spec.operation_id} references unknown schemas: {missing_schemas}")


def validate_job_type_registry() -> None:
    known_errors = all_error_reasons()
    known_events = all_log_events()
    known_schemas = all_schema_names()
    for spec in job_registry.all_job_type_specs().values():
        missing_errors = _missing(set(spec.error_codes), known_errors)
        if missing_errors:
            raise ValueError(f"job_type {spec.job_type} references unknown errors: {missing_errors}")
        missing_events = _missing(set(spec.log_events), known_events)
        if missing_events:
            raise ValueError(f"job_type {spec.job_type} references unknown log events: {missing_events}")
        referenced_schemas = {
            spec.params_schema,
            spec.runtime_fields_schema,
            spec.canonical_result_schema,
            spec.public_result_schema,
        }
        missing_schemas = _missing(referenced_schemas, known_schemas)
        if missing_schemas:
            raise ValueError(f"job_type {spec.job_type} references unknown schemas: {missing_schemas}")
        if not spec.params_schema or spec.params_schema == "null":
            raise ValueError(f"job_type {spec.job_type} must declare params_schema")
        if not spec.runtime_fields_schema or spec.runtime_fields_schema in {"null", "dict"}:
            raise ValueError(f"job_type {spec.job_type} must declare runtime_fields_schema")
        if not spec.canonical_result_schema or spec.canonical_result_schema == "null":
            raise ValueError(f"job_type {spec.job_type} must declare canonical_result_schema")
        if not spec.public_result_schema or spec.public_result_schema == "null":
            raise ValueError(f"job_type {spec.job_type} must declare public_result_schema")


def _operation_route_map(app) -> dict[str, APIRoute]:
    routes: dict[str, APIRoute] = {}
    for route in app.routes:
        if not isinstance(route, APIRoute) or not route.include_in_schema:
            continue
        if route.path in {"/health", "/healthz"}:
            continue
        if route.operation_id is None:
            raise ValueError(f"schema route without explicit operation_id: {route.path}")
        if route.operation_id in routes:
            raise ValueError(f"duplicate mounted operation_id: {route.operation_id}")
        routes[route.operation_id] = route
    return routes


def _openapi_operation(app, path: str, method: str) -> dict:
    openapi = app.openapi()
    try:
        return openapi["paths"][path][method.lower()]
    except KeyError as exc:
        raise ValueError(f"operation missing from OpenAPI: {method} {path}") from exc


def _schema_ref_name(schema: dict) -> str:
    ref = schema.get("$ref")
    if not isinstance(ref, str):
        return ""
    return ref.rsplit("/", 1)[-1]


def validate_app_route_operations(app) -> None:
    registered = set(all_operation_specs())
    specs = all_operation_specs()
    routes = _operation_route_map(app)
    route_operation_ids = set(routes)
    missing = _missing(route_operation_ids, registered)
    if missing:
        raise ValueError(f"route operation ids not registered: {missing}")
    unused = _missing(registered, route_operation_ids)
    if unused:
        raise ValueError(f"registered operation ids not mounted by routes: {unused}")

    for operation_id, spec in specs.items():
        route = routes[operation_id]
        methods = {method for method in route.methods or set() if method not in {"HEAD", "OPTIONS"}}
        if methods != {spec.method}:
            raise ValueError(f"operation {operation_id} method mismatch: route={sorted(methods)} spec={spec.method}")
        expected_path = f"{settings.service.api_prefix}{spec.path}"
        if route.path != expected_path:
            raise ValueError(f"operation {operation_id} path mismatch: route={route.path} spec={expected_path}")
        operation = _openapi_operation(app, expected_path, spec.method)
        request_body = operation.get("requestBody")
        if spec.request_schema is None and request_body is not None:
            raise ValueError(f"operation {operation_id} must not declare request body")
        if spec.request_schema is not None:
            schema = request_body["content"]["application/json"]["schema"] if request_body else {}
            if _schema_ref_name(schema) != spec.request_schema:
                raise ValueError(f"operation {operation_id} request schema mismatch")
        success_status = "200"
        response_schema = operation["responses"][success_status]["content"]["application/json"]["schema"]
        response_ref = _schema_ref_name(response_schema)
        if not response_ref:
            response_ref = _schema_ref_name(response_schema.get("properties", {}).get("data", {}))
        if spec.response_data_schema not in response_ref:
            raise ValueError(f"operation {operation_id} response schema mismatch: {response_ref}")


def validate_all_registries(app=None) -> None:
    validate_error_registry()
    validate_operation_registry()
    validate_job_type_registry()
    if app is not None:
        validate_app_route_operations(app)
