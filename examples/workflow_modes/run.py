"""Run the six built-in workflow mode examples against a local API."""

from __future__ import annotations

import argparse
import os
import sys
import time
import uuid
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
from scripts.verify.workflow_modes_smoke import WORKFLOW_MODE_CASES, _validate_result

CASES = {case.mode: case for case in WORKFLOW_MODE_CASES}
MODES = tuple(CASES)


def load_script_env() -> dict[str, str]:
    env_path = ROOT_DIR / "scripts" / ".env"
    if not env_path.exists():
        return {}
    values: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip("'\"")
    return values


def default_api_url() -> str:
    script_env = load_script_env()
    if os.environ.get("API_URL"):
        return os.environ["API_URL"]
    host = os.environ.get("API_HOST") or script_env.get("API_HOST") or "127.0.0.1"
    port = os.environ.get("API_PORT") or script_env.get("API_PORT") or "8100"
    return f"http://{host}:{port}"


def create_payload(mode: str) -> dict[str, Any]:
    return {
        "client_request_id": f"example-job-test-workflow-{mode}-{uuid.uuid4()}",
        "job_type": "job_test_workflow",
        "job_params": {
            "mode": mode,
            "label": f"example-{mode}-{uuid.uuid4().hex[:8]}",
        },
        "metadata": {
            "source": "examples/workflow_modes/run.py",
            "mode": mode,
        },
        "options": {
            "priority": "normal",
            "idempotency_mode": "reject_duplicate",
        },
    }


def wait_terminal(
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
        if last_job["job_status"] in {"succeeded", "failed"}:
            return last_job
        time.sleep(0.5)
    raise RuntimeError(f"job {job_id} did not finish within {timeout_seconds}s; last={last_job}")


def run_mode(jobs_url: str, headers: dict[str, str], mode: str, timeout_seconds: int) -> str:
    created = job_from_envelope(request_json(jobs_url, method="POST", headers=headers, payload=create_payload(mode)))
    job_id = created["job_id"]
    terminal = wait_terminal(jobs_url, headers, job_id=job_id, timeout_seconds=timeout_seconds)
    if terminal["job_status"] != "succeeded":
        raise RuntimeError(f"{mode} failed: {terminal}")
    _validate_result(terminal, CASES[mode])
    return job_id


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Submit built-in job_test_workflow examples.")
    parser.add_argument("--api-url", default=default_api_url())
    parser.add_argument("--mode", choices=MODES, help="Run one mode. Defaults to all six.")
    parser.add_argument("--timeout-seconds", type=int, default=60)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    dotenv = load_dotenv()
    headers = build_headers(dotenv)
    api_prefix = (env_value("SERVICE_API_PREFIX", dotenv) or "/api/v1/ai-jobs").rstrip("/")
    jobs_url = f"{args.api_url.rstrip('/')}{api_prefix}/jobs"

    modes = (args.mode,) if args.mode else MODES
    for mode in modes:
        job_id = run_mode(jobs_url, headers, mode, args.timeout_seconds)
        print(f"{mode}: succeeded job_id={job_id}")


if __name__ == "__main__":
    main()
