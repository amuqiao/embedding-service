from __future__ import annotations

from fastapi.routing import APIRoute

from app.api.operations import all_operation_specs
from app.core import prompt_templates
from app.core.config import settings
from app.core.error_registry import all_error_reasons, all_error_specs
from app.core.logging import all_log_events
from app.jobs.base import EXECUTION_MODES, PLATFORM_RETRY_POLICIES, SIDE_EFFECT_POLICIES
from app.jobs import registry as job_registry
from app.schemas.registry import all_schema_names


def _missing(values: set[str], allowed: set[str]) -> list[str]:
    return sorted(values - allowed)


def _required_metadata_str(value: object, *, field_name: str, owner: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{owner} requires non-empty string field: {field_name}")
    return value.strip()


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
    prompt_templates.validate_prompt_config_shape(known_output_schemas=known_schemas)
    known_prompt_refs = prompt_templates.all_prompt_refs()
    prompt_output_schemas = prompt_templates.prompt_output_schema_refs()
    specs = job_registry.all_job_type_specs()
    prompt_template_job_types = prompt_templates.prompt_template_job_types()
    unknown_template_job_types = _missing(prompt_template_job_types, set(specs))
    if unknown_template_job_types:
        raise ValueError(f"prompt config references unknown job_types: {unknown_template_job_types}")

    for spec in specs.values():
        if spec.prompt_template_required_blocks:
            template = prompt_templates.get_template(spec.job_type)
            if template is None:
                raise ValueError(f"job_type {spec.job_type} requires prompt template")
            block_keys = {block.key for block in template.prompt_blocks}
            missing_blocks = _missing(set(spec.prompt_template_required_blocks), block_keys)
            if missing_blocks:
                raise ValueError(
                    f"job_type {spec.job_type} prompt template missing blocks: {missing_blocks}"
                )
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
        if spec.execution_mode not in EXECUTION_MODES:
            raise ValueError(f"job_type {spec.job_type} declares invalid execution_mode: {spec.execution_mode}")
        if spec.side_effect_policy not in SIDE_EFFECT_POLICIES:
            raise ValueError(f"job_type {spec.job_type} declares invalid side_effect_policy: {spec.side_effect_policy}")
        if spec.platform_retry_policy not in PLATFORM_RETRY_POLICIES:
            raise ValueError(
                f"job_type {spec.job_type} declares invalid platform_retry_policy: {spec.platform_retry_policy}"
            )
        if spec.max_attempts < 1:
            raise ValueError(f"job_type {spec.job_type} must declare max_attempts >= 1")
        if spec.timeout_seconds < 1:
            raise ValueError(f"job_type {spec.job_type} must declare timeout_seconds >= 1")
        if spec.max_attempts > 1 and spec.platform_retry_policy == "no_platform_retry":
            raise ValueError(
                f"job_type {spec.job_type} must declare platform_retry_policy when max_attempts > 1"
            )
        seen_prompt_steps: set[str] = set()
        for prompt_spec in spec.prompt_specs:
            owner = f"job_type {spec.job_type} prompt_spec"
            step_name = _required_metadata_str(prompt_spec.step_name, field_name="step_name", owner=owner)
            runtime_field = _required_metadata_str(prompt_spec.runtime_field, field_name="runtime_field", owner=owner)
            prompt_ref = _required_metadata_str(prompt_spec.prompt_ref, field_name="prompt_ref", owner=owner)
            output_schema_ref = _required_metadata_str(
                prompt_spec.output_schema_ref,
                field_name="output_schema_ref",
                owner=owner,
            )
            if step_name in seen_prompt_steps:
                raise ValueError(f"job_type {spec.job_type} has duplicate prompt step: {step_name}")
            seen_prompt_steps.add(step_name)
            if prompt_ref not in known_prompt_refs:
                raise ValueError(
                    f"job_type {spec.job_type} references unknown prompt_ref: {prompt_ref}"
                )
            if output_schema_ref not in known_schemas:
                raise ValueError(
                    f"job_type {spec.job_type} references unknown prompt output_schema_ref: "
                    f"{output_schema_ref}"
                )
            configured_output_schema_ref = prompt_output_schemas.get(prompt_ref)
            if configured_output_schema_ref is None:
                raise ValueError(f"prompt {prompt_ref} must declare output_schema_ref")
            if configured_output_schema_ref != output_schema_ref:
                raise ValueError(
                    f"job_type {spec.job_type} prompt {prompt_ref} output_schema_ref mismatch: "
                    f"{configured_output_schema_ref} != {output_schema_ref}"
                )
        if spec.execution_mode == "builtin_llm_text_runtime":
            if len(spec.prompt_specs) != 1:
                raise ValueError(f"job_type {spec.job_type} builtin_llm_text_runtime requires one prompt_spec")
            prompt_spec = spec.prompt_specs[0]
            runtime_field = _required_metadata_str(
                prompt_spec.runtime_field,
                field_name="runtime_field",
                owner=f"job_type {spec.job_type} prompt_spec",
            )
            if runtime_field != "prompt_payload":
                raise ValueError(
                    f"job_type {spec.job_type} builtin_llm_text_runtime prompt_spec must use prompt_payload"
                )


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
