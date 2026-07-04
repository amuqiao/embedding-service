"""Submit DAG-lite workflow modes and wait for root jobs to finish."""

from __future__ import annotations

import argparse
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from scripts.verify.job_workflow_smoke import (
    build_headers,
    env_value,
    job_from_envelope,
    load_dotenv,
    request_json,
)
from app.jobs.types.example_catalog import all_example_workflow_mode_specs


@dataclass(frozen=True)
class WorkflowModeCase:
    mode: str
    expected_node_count: int
    expected_node_keys: tuple[str, ...]
    expected_result_kinds: dict[str, str]


WORKFLOW_MODE_CASES = tuple(
    WorkflowModeCase(
        mode=str(spec["mode"]),
        expected_node_count=int(spec["expected_node_count"]),
        expected_node_keys=tuple(spec["expected_node_keys"]),
        expected_result_kinds=dict(spec["expected_result_kinds"]),
    )
    for spec in all_example_workflow_mode_specs()
)


def _create_job(jobs_url: str, headers: dict[str, str], case: WorkflowModeCase) -> dict[str, Any]:
    label = f"example_workflow_{case.mode}-{uuid.uuid4().hex[:8]}"
    return job_from_envelope(
        request_json(
            jobs_url,
            method="POST",
            headers=headers,
            payload={
                "client_request_id": f"verify-example-workflow-{case.mode}-{uuid.uuid4()}",
                "job_type": "example_workflow",
                "job_params": {"mode": case.mode, "label": label},
                "metadata": {"source": "scripts/verify/workflow_modes_smoke.py", "mode": case.mode},
                "options": {"priority": "normal", "idempotency_mode": "reject_duplicate"},
            },
        )
    )


def _wait_terminal(
    jobs_url: str,
    headers: dict[str, str],
    *,
    job_id: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last_job: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        last_job = job_from_envelope(request_json(f"{jobs_url}/{job_id}", method="GET", headers=headers))
        status = last_job["job_status"]
        if status in {"succeeded", "failed"}:
            return last_job
        time.sleep(0.5)
    raise RuntimeError(f"workflow job {job_id} did not finish within {timeout_seconds}s; last={last_job}")


def _validate_result(job: dict[str, Any], case: WorkflowModeCase) -> None:
    if job["job_status"] != "succeeded":
        raise RuntimeError(f"{case.mode} finished with {job['job_status']}: {job}")
    result = job.get("job_result")
    if not isinstance(result, dict):
        raise RuntimeError(f"{case.mode} missing job_result: {job}")
    workflow = result.get("workflow")
    if not isinstance(workflow, dict):
        raise RuntimeError(f"{case.mode} missing workflow result: {result}")
    if workflow.get("workflow_type") != "example_workflow":
        raise RuntimeError(f"{case.mode} returned wrong workflow_type: {workflow}")
    if workflow.get("outcome") != "success":
        raise RuntimeError(f"{case.mode} returned non-success outcome: {workflow}")
    if workflow.get("node_count") != case.expected_node_count:
        raise RuntimeError(f"{case.mode} returned wrong node_count: {workflow}")
    nodes = workflow.get("nodes")
    if not isinstance(nodes, list):
        raise RuntimeError(f"{case.mode} missing workflow nodes: {workflow}")
    by_key = {node.get("node_key"): node for node in nodes if isinstance(node, dict)}
    if set(by_key) != set(case.expected_node_keys):
        raise RuntimeError(f"{case.mode} returned wrong node keys: {nodes}")
    not_succeeded = {
        key: node.get("status")
        for key, node in by_key.items()
        if node.get("status") != "succeeded"
    }
    if not_succeeded:
        raise RuntimeError(f"{case.mode} has non-succeeded child nodes: {not_succeeded}")
    missing_job_ids = [key for key, node in by_key.items() if not node.get("job_id")]
    if missing_job_ids:
        raise RuntimeError(f"{case.mode} has child nodes without job_id: {missing_job_ids}")
    for key, expected_kind in case.expected_result_kinds.items():
        _validate_node_result(case, key, by_key[key].get("result"), expected_kind)


def _validate_node_result(
    case: WorkflowModeCase,
    node_key: str,
    result: Any,
    expected_kind: str,
) -> None:
    if not isinstance(result, dict):
        raise RuntimeError(f"{case.mode} node {node_key} missing result object: {result}")
    if expected_kind == "sleep":
        repeated = result.get("repeated")
        if not isinstance(result.get("message"), str) or not isinstance(repeated, list) or result.get("count") != 1:
            raise RuntimeError(f"{case.mode} node {node_key} returned invalid sleep result: {result}")
        if repeated != [result["message"]]:
            raise RuntimeError(f"{case.mode} node {node_key} returned inconsistent sleep result: {result}")
        return
    if expected_kind == "pair":
        a = result.get("a")
        b = result.get("b")
        total = result.get("result")
        if not isinstance(a, (int, float)) or not isinstance(b, (int, float)) or total != a + b:
            raise RuntimeError(f"{case.mode} node {node_key} returned invalid pair result: {result}")
        return
    if expected_kind == "collect":
        items = result.get("items")
        if not isinstance(items, list) or result.get("count") != len(items) or not items:
            raise RuntimeError(f"{case.mode} node {node_key} returned invalid collect result: {result}")
        return
    raise RuntimeError(f"{case.mode} node {node_key} has unsupported result kind: {expected_kind}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Submit DAG-lite workflow modes and wait for completion.")
    parser.add_argument("--api-url", required=True)
    parser.add_argument("--timeout-seconds", type=int, default=60)
    args = parser.parse_args()

    dotenv = load_dotenv()
    headers = build_headers(dotenv)
    api_url = args.api_url.rstrip("/")
    api_prefix = (env_value("SERVICE_API_PREFIX", dotenv) or "/api/v1/ai-jobs").rstrip("/")
    jobs_url = f"{api_url}{api_prefix}/jobs"

    completed: list[str] = []
    for case in WORKFLOW_MODE_CASES:
        created = _create_job(jobs_url, headers, case)
        job_id = created["job_id"]
        terminal = _wait_terminal(
            jobs_url,
            headers,
            job_id=job_id,
            timeout_seconds=args.timeout_seconds,
        )
        _validate_result(terminal, case)
        completed.append(f"{case.mode}:{job_id}")

    print("workflow modes smoke ok: " + ", ".join(completed))


if __name__ == "__main__":
    main()
