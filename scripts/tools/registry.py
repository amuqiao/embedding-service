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
    from app.capabilities import registry as capability_registry
    from app.jobs import registry as job_registry
    from app.jobs.types.register import register_all_job_types
    from app.tools import registry as tool_registry

    register_all_job_types()

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
    return {"tools": tools, "capabilities": capabilities, "job_capabilities": job_capabilities}


def print_human(snapshot: dict[str, list[dict[str, Any]]]) -> None:
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
        description="Print the registered tool, capability, and job_type capability graph.",
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
