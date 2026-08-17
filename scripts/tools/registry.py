from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR))
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")


def registry_snapshot() -> dict[str, list[dict[str, Any]]]:
    from app.api.operations import all_operation_specs
    from app.capabilities import registry as capability_registry
    from app.jobs import registry as job_registry
    from app.jobs.types.register import register_all_job_types
    from app.tools import registry as tool_registry
    from app.workflows import registry as workflow_registry

    register_all_job_types()

    operations = [
        {
            "operation_id": spec.operation_id,
            "channel": spec.channel,
            "method": spec.method,
            "path": spec.path,
            "success_status": spec.success_status,
            "auth_boundary": spec.auth_boundary,
            "request_schema": spec.request_schema,
            "response_data_schema": spec.response_data_schema,
            "error_codes": sorted(spec.error_codes),
            "idempotency_key": spec.idempotency_key,
            "side_effects": list(spec.side_effects),
            "log_events": list(spec.log_events),
            "metrics": list(spec.metrics),
            "change_policy": spec.change_policy,
        }
        for spec in sorted(all_operation_specs().values(), key=lambda item: item.operation_id)
    ]
    job_types = [
        {
            "job_type": spec.job_type,
            "visibility": spec.visibility,
            "role": spec.role,
            "execution_mode": spec.execution_mode,
            "params_schema": spec.params_schema,
            "runtime_fields_schema": spec.runtime_fields_schema,
            "canonical_result_schema": spec.canonical_result_schema,
            "public_result_schema": spec.public_result_schema,
            "callback_envelope_schema": spec.callback_envelope_schema,
            "allow_callback": spec.allow_callback,
            "result_snapshot_statuses": sorted(spec.result_snapshot_statuses),
            "large_artifact_keys": sorted(spec.large_artifact_keys),
            "error_codes": sorted(spec.error_codes),
            "log_events": list(spec.log_events),
            "timeout_seconds": spec.timeout_seconds,
            "retry_policy": spec.retry_policy,
            "side_effect_policy": spec.side_effect_policy,
            "allowed_capability_refs": sorted(spec.allowed_capability_refs),
            "prompt_specs": [
                {
                    "step_name": prompt.step_name,
                    "runtime_field": prompt.runtime_field,
                    "prompt_ref": prompt.prompt_ref,
                    "output_schema_ref": prompt.output_schema_ref,
                }
                for prompt in spec.prompt_specs
            ],
            "prompt_template_required_blocks": sorted(spec.prompt_template_required_blocks),
        }
        for spec in sorted(job_registry.all_job_type_specs().values(), key=lambda item: item.job_type)
    ]
    workflows = [
        {
            "workflow_type": definition.workflow_type,
            "root_job_type": definition.root_job_type,
            "workflow_version": definition.workflow_version,
            "failure_policy": definition.failure_policy,
            "max_nodes": definition.max_nodes,
            "runtime_job_type_dependencies": sorted(definition.runtime_job_type_dependencies),
            "build": f"{definition.build.__module__}:{definition.build.__qualname__}",
        }
        for definition in sorted(
            workflow_registry.all_workflow_definitions().values(),
            key=lambda item: item.workflow_type,
        )
    ]
    tools = [
        {
            "tool_ref": definition.tool_ref,
            "kind": definition.kind,
            "entrypoint": definition.entrypoint_path,
            "request_schema": definition.request_schema,
            "result_schema": definition.result_schema,
            "required_settings": list(definition.required_settings),
            "startup_validators": list(definition.startup_validators),
            "error_codes": sorted(definition.error_codes),
            "log_events": list(definition.log_events),
        }
        for definition in sorted(tool_registry.all_tool_definitions().values(), key=lambda item: item.tool_ref)
    ]
    capabilities = [
        {
            "capability_ref": definition.capability_ref,
            "plan_schema": definition.plan_schema,
            "result_schema": definition.result_schema,
            "service_entrypoint": definition.service_entrypoint,
            "allowed_tool_refs": sorted(definition.allowed_tool_refs),
            "error_codes": sorted(definition.error_codes),
            "log_events": list(definition.log_events),
        }
        for definition in sorted(
            capability_registry.all_capability_definitions().values(),
            key=lambda item: item.capability_ref,
        )
    ]
    job_capabilities = [
        {
            "job_type": job_type,
            "visibility": spec.visibility,
            "role": spec.role,
            "allowed_capability_refs": sorted(spec.allowed_capability_refs),
        }
        for job_type, spec in sorted(job_registry.all_job_type_specs().items())
        if spec.allowed_capability_refs
    ]
    return {
        "operations": operations,
        "job_types": job_types,
        "workflows": workflows,
        "tools": tools,
        "capabilities": capabilities,
        "job_capabilities": job_capabilities,
    }


def print_human(snapshot: dict[str, list[dict[str, Any]]]) -> None:
    print("Operations")
    for operation in snapshot["operations"]:
        print(f"- {operation['operation_id']}")
        print(f"  route: {operation['method']} {operation['path']}")
        print(f"  success_status: {operation['success_status']}")
        print(f"  response_data_schema: {operation['response_data_schema']}")

    print("")
    print("Job Types")
    for job_type in snapshot["job_types"]:
        print(f"- {job_type['job_type']}")
        print(f"  visibility: {job_type['visibility']}")
        print(f"  role: {job_type['role']}")
        print(f"  params_schema: {job_type['params_schema']}")
        print(f"  public_result_schema: {job_type['public_result_schema']}")

    print("")
    print("Workflows")
    for workflow in snapshot["workflows"]:
        print(f"- {workflow['workflow_type']}")
        print(f"  root_job_type: {workflow['root_job_type']}")
        print(f"  failure_policy: {workflow['failure_policy']}")
        print(f"  max_nodes: {workflow['max_nodes']}")
        if workflow["runtime_job_type_dependencies"]:
            print(f"  runtime_job_type_dependencies: {', '.join(workflow['runtime_job_type_dependencies'])}")

    print("")
    print("Tools")
    for tool in snapshot["tools"]:
        print(f"- {tool['tool_ref']}")
        print(f"  kind: {tool['kind']}")
        print(f"  entrypoint: {tool['entrypoint']}")
        if tool["request_schema"]:
            print(f"  request_schema: {tool['request_schema']}")
        if tool["result_schema"]:
            print(f"  result_schema: {tool['result_schema']}")
        if tool["required_settings"]:
            print(f"  required_settings: {', '.join(tool['required_settings'])}")
        if tool["startup_validators"]:
            print(f"  startup_validators: {', '.join(tool['startup_validators'])}")

    print("")
    print("Capabilities")
    for capability in snapshot["capabilities"]:
        print(f"- {capability['capability_ref']}")
        print(f"  plan_schema: {capability['plan_schema']}")
        print(f"  result_schema: {capability['result_schema']}")
        print(f"  service_entrypoint: {capability['service_entrypoint']}")
        print(f"  tools: {', '.join(capability['allowed_tool_refs'])}")

    print("")
    print("Job Type Capabilities")
    for relation in snapshot["job_capabilities"]:
        print(f"- {relation['job_type']}")
        print(f"  visibility: {relation['visibility']}")
        print(f"  role: {relation['role']}")
        print(f"  capabilities: {', '.join(relation['allowed_capability_refs'])}")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="./scripts/tools.sh registry",
        description="Print the registered operation, job_type, workflow, tool, and capability graph.",
    )
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    snapshot = registry_snapshot()
    if args.json:
        print(json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True))
        return
    print_human(snapshot)


if __name__ == "__main__":
    main()
