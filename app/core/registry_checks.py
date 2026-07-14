from __future__ import annotations

import importlib

from fastapi.routing import APIRoute

from app.api.operations import all_operation_specs
from app.capabilities import registry as capability_registry
from app.core import prompt_templates
from app.core.config import APPLICATION_ENV_FIELD_MAP
from app.core.config import settings
from app.core.error_registry import all_error_specs
from app.core.logging import all_log_events
from app.core.registries.refs import require_capability_ref, require_tool_ref
from app.jobs.base import (
    ATTEMPT_PURPOSES,
    EXECUTION_MODES,
    JOB_RESULT_SNAPSHOT_STATUSES,
    JOB_TYPE_ROLES,
    JOB_TYPE_VISIBILITIES,
    RETRY_BACKOFF_KINDS,
    SIDE_EFFECT_POLICIES,
)
from app.jobs import registry as job_registry
from app.schemas.registry import all_schema_names
from app.tools import registry as tool_registry

_ERROR_VISIBILITIES = {"public", "internal"}
_PUBLIC_OPERATION_CHANNELS = {"http", "callback", "external_write"}


def _missing(values: set[str], allowed: set[str]) -> list[str]:
    return sorted(values - allowed)


def _required_metadata_str(value: object, *, field_name: str, owner: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{owner} requires non-empty string field: {field_name}")
    return value.strip()


def _resolve_entrypoint(path: str, *, owner: str, field_name: str):
    value = _required_metadata_str(path, field_name=field_name, owner=owner)
    module_name, separator, attr_path = value.partition(":")
    if not separator or not module_name or not attr_path:
        raise ValueError(f"{owner} {field_name} must use module:attribute")
    try:
        target = importlib.import_module(module_name)
    except Exception as exc:
        raise ValueError(f"{owner} {field_name} module is not importable: {module_name}") from exc
    for attr in attr_path.split("."):
        if not attr:
            raise ValueError(f"{owner} {field_name} contains empty attribute path")
        try:
            target = getattr(target, attr)
        except AttributeError as exc:
            raise ValueError(f"{owner} {field_name} attribute is not importable: {attr_path}") from exc
    return target


def _settings_path_exists(path: str) -> bool:
    target = settings
    for part in path.split("."):
        if not part or not hasattr(target, part):
            return False
        target = getattr(target, part)
    return True


def _validate_retry_policy_snapshot(job_type: str, retry_policy: object) -> None:
    if not isinstance(retry_policy, dict):
        raise ValueError(f"job_type {job_type} retry_policy must be an object")
    missing_domains = ATTEMPT_PURPOSES - set(retry_policy)
    if missing_domains:
        raise ValueError(f"job_type {job_type} retry_policy missing domains: {sorted(missing_domains)}")
    for domain in ATTEMPT_PURPOSES:
        policy = retry_policy.get(domain)
        if not isinstance(policy, dict):
            raise ValueError(f"job_type {job_type} retry_policy.{domain} must be an object")
        if policy.get("domain") != domain:
            raise ValueError(f"job_type {job_type} retry_policy.{domain}.domain mismatch")
        max_attempts = policy.get("max_attempts")
        if not isinstance(max_attempts, int) or max_attempts < 1:
            raise ValueError(f"job_type {job_type} retry_policy.{domain}.max_attempts must be >= 1")
        retry_delay = policy.get("retry_delay_seconds")
        if retry_delay is not None and (not isinstance(retry_delay, int) or retry_delay < 0):
            raise ValueError(f"job_type {job_type} retry_policy.{domain}.retry_delay_seconds must be >= 0")
        if policy.get("backoff_kind") not in RETRY_BACKOFF_KINDS:
            raise ValueError(f"job_type {job_type} retry_policy.{domain}.backoff_kind is invalid")
        retryable_error_codes = policy.get("retryable_error_codes")
        if not isinstance(retryable_error_codes, list) or not all(
            isinstance(code, str) and code for code in retryable_error_codes
        ):
            raise ValueError(f"job_type {job_type} retry_policy.{domain}.retryable_error_codes must be strings")


def validate_error_registry() -> None:
    seen_codes: dict[str, str] = {}
    specs = all_error_specs()
    for reason, spec in specs.items():
        if reason != spec.reason:
            raise ValueError(f"error registry key mismatch: {reason} != {spec.reason}")
        if not spec.code:
            raise ValueError(f"error {reason} requires non-empty code")
        if not spec.msg:
            raise ValueError(f"error {reason} requires non-empty msg")
        if spec.visibility not in _ERROR_VISIBILITIES:
            raise ValueError(f"error {reason} declares invalid visibility: {spec.visibility}")
        if spec.visibility != "internal" and spec.projection_targets:
            raise ValueError(f"public error {reason} must not declare projection_targets")
        missing_projection_targets = _missing(set(spec.projection_targets), set(specs))
        if missing_projection_targets:
            raise ValueError(f"error {reason} references unknown projection targets: {missing_projection_targets}")
        internal_projection_targets = sorted(
            target
            for target in spec.projection_targets
            if specs[target].visibility != "public"
        )
        if internal_projection_targets:
            raise ValueError(
                f"error {reason} projection targets must be public errors: {internal_projection_targets}"
            )
        previous = seen_codes.get(spec.code)
        if previous is not None:
            raise ValueError(f"duplicate error code {spec.code}: {previous}, {reason}")
        seen_codes[spec.code] = reason


def validate_operation_registry() -> None:
    error_specs = all_error_specs()
    known_errors = set(error_specs)
    known_events = all_log_events()
    known_schemas = all_schema_names()
    for spec in all_operation_specs().values():
        missing_errors = _missing(set(spec.error_codes), known_errors)
        if missing_errors:
            raise ValueError(f"operation {spec.operation_id} references unknown errors: {missing_errors}")
        if spec.channel in _PUBLIC_OPERATION_CHANNELS:
            internal_errors = sorted(
                reason
                for reason in spec.error_codes
                if error_specs[reason].visibility != "public"
            )
            if internal_errors:
                raise ValueError(f"operation {spec.operation_id} references internal errors: {internal_errors}")
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
    error_specs = all_error_specs()
    known_errors = set(error_specs)
    known_events = all_log_events()
    known_schemas = all_schema_names()
    prompt_templates.validate_prompt_config_shape(known_output_schemas=known_schemas)
    known_prompt_refs = prompt_templates.all_prompt_refs()
    prompt_output_schemas = prompt_templates.prompt_output_schema_refs()
    specs = job_registry.all_job_type_specs()
    known_error_owners = {"core", "jobs", "storage", "ai", "billing", "broker", "callbacks"} | set(specs)
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
        internal_errors = sorted(
            reason
            for reason in spec.error_codes
            if error_specs[reason].visibility != "public"
        )
        if internal_errors:
            raise ValueError(f"job_type {spec.job_type} references internal errors: {internal_errors}")
        unknown_error_owners = sorted(
            f"{reason}:{error_specs[reason].owner}"
            for reason in spec.error_codes
            if error_specs[reason].owner not in known_error_owners
        )
        if unknown_error_owners:
            raise ValueError(f"job_type {spec.job_type} references errors with unknown owners: {unknown_error_owners}")
        missing_events = _missing(set(spec.log_events), known_events)
        if missing_events:
            raise ValueError(f"job_type {spec.job_type} references unknown log events: {missing_events}")
        for capability_ref in spec.allowed_capability_refs:
            require_capability_ref(capability_ref)
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
        _validate_retry_policy_snapshot(spec.job_type, spec.retry_policy)
        if spec.visibility not in JOB_TYPE_VISIBILITIES:
            raise ValueError(f"job_type {spec.job_type} declares invalid visibility: {spec.visibility}")
        if spec.role not in JOB_TYPE_ROLES:
            raise ValueError(f"job_type {spec.job_type} declares invalid role: {spec.role}")
        invalid_snapshot_statuses = set(spec.result_snapshot_statuses) - JOB_RESULT_SNAPSHOT_STATUSES
        if invalid_snapshot_statuses:
            raise ValueError(
                f"job_type {spec.job_type} declares invalid result_snapshot_statuses: "
                f"{sorted(invalid_snapshot_statuses)}"
            )
        if spec.timeout_seconds < 1:
            raise ValueError(f"job_type {spec.job_type} must declare timeout_seconds >= 1")
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


def validate_capability_tool_registry() -> None:
    error_specs = all_error_specs()
    known_errors = set(error_specs)
    known_events = all_log_events()
    known_schemas = all_schema_names()
    known_capability_refs = capability_registry.all_capability_refs()
    known_tool_refs = tool_registry.all_tool_refs()
    known_setting_paths = {
        f"{section}.{field_name}"
        for section, field_name in APPLICATION_ENV_FIELD_MAP.values()
    }

    for job_type, spec in job_registry.all_job_type_specs().items():
        for capability_ref in spec.allowed_capability_refs:
            normalized_ref = require_capability_ref(capability_ref)
            if normalized_ref not in known_capability_refs:
                raise ValueError(f"job_type {job_type} references unknown capability_ref: {normalized_ref}")

    for capability_ref, definition in capability_registry.all_capability_definitions().items():
        owner = f"capability {capability_ref}"
        if capability_ref != definition.capability_ref:
            raise ValueError(f"capability registry key mismatch: {capability_ref} != {definition.capability_ref}")
        require_capability_ref(definition.capability_ref)
        missing_schemas = _missing({definition.plan_schema, definition.result_schema}, known_schemas)
        if missing_schemas:
            raise ValueError(f"{owner} references unknown schemas: {missing_schemas}")
        _resolve_entrypoint(definition.service_entrypoint, owner=owner, field_name="service_entrypoint")
        missing_tools = _missing({require_tool_ref(ref) for ref in definition.allowed_tool_refs}, known_tool_refs)
        if missing_tools:
            raise ValueError(f"{owner} references unknown tool_refs: {missing_tools}")
        missing_errors = _missing(set(definition.error_codes), known_errors)
        if missing_errors:
            raise ValueError(f"{owner} references unknown errors: {missing_errors}")
        missing_events = _missing(set(definition.log_events), known_events)
        if missing_events:
            raise ValueError(f"{owner} references unknown log events: {missing_events}")

    for tool_ref, definition in tool_registry.all_tool_definitions().items():
        owner = f"tool {tool_ref}"
        if tool_ref != definition.tool_ref:
            raise ValueError(f"tool registry key mismatch: {tool_ref} != {definition.tool_ref}")
        require_tool_ref(definition.tool_ref)
        _required_metadata_str(definition.kind, field_name="kind", owner=owner)
        _resolve_entrypoint(definition.entrypoint_path, owner=owner, field_name="entrypoint_path")
        referenced_schemas = {
            schema
            for schema in (definition.request_schema, definition.result_schema)
            if schema is not None
        }
        missing_schemas = _missing(referenced_schemas, known_schemas)
        if missing_schemas:
            raise ValueError(f"{owner} references unknown schemas: {missing_schemas}")
        unknown_settings = sorted(
            setting_path
            for setting_path in definition.required_settings
            if setting_path not in known_setting_paths or not _settings_path_exists(setting_path)
        )
        if unknown_settings:
            raise ValueError(f"{owner} references unknown settings: {unknown_settings}")
        for startup_validator in definition.startup_validators:
            _resolve_entrypoint(startup_validator, owner=owner, field_name="startup_validators")
        missing_errors = _missing(set(definition.error_codes), known_errors)
        if missing_errors:
            raise ValueError(f"{owner} references unknown errors: {missing_errors}")
        missing_events = _missing(set(definition.log_events), known_events)
        if missing_events:
            raise ValueError(f"{owner} references unknown log events: {missing_events}")


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
    validate_capability_tool_registry()
    if app is not None:
        validate_app_route_operations(app)
