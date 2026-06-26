"""Submit a template echo job and wait for the local Taskiq execution path to finish.

The shell wrapper prints the "Workflow Smoke" section. This script prints one
success summary with job_id; failures raise RuntimeError with the failing object
and enough detail for the caller to inspect API/worker logs.
"""

from __future__ import annotations

import argparse
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT_DIR = Path(__file__).resolve().parents[2]


def load_dotenv() -> dict[str, str]:
    env_path = ROOT_DIR / ".env"
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


def env_value(name: str, dotenv: dict[str, str]) -> str | None:
    return os.environ.get(name) or dotenv.get(name)


def is_true(value: str | None) -> bool:
    return value in {"true", "True", "TRUE"}


def request_json(url: str, *, method: str, headers: dict[str, str], payload: dict[str, Any] | None = None) -> dict[str, Any]:
    # HTTP errors include method, URL, status, and response body because smoke failures need the API-side evidence.
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen(req, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {url} failed with HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"{method} {url} failed: {exc.reason}") from exc


def build_headers(dotenv: dict[str, str]) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if not is_true(env_value("DISABLE_HTTP_AUTH_HEADER", dotenv)):
        token = env_value("SERVICE_API_KEY", dotenv)
        if not token or token.startswith("<"):
            raise RuntimeError("SERVICE_API_KEY is required unless DISABLE_HTTP_AUTH_HEADER=true")
        headers["Authorization"] = f"Bearer {token}"
    if not is_true(env_value("DISABLE_CALLER_ID_HEADER", dotenv)):
        headers["X-AI-Service-Caller-ID"] = "verify-workflow-smoke"
    return headers


def job_from_envelope(envelope: dict[str, Any]) -> dict[str, Any]:
    if envelope.get("code") != "0":
        raise RuntimeError(f"unexpected response envelope: {envelope}")
    job = envelope.get("data", {}).get("job")
    if not isinstance(job, dict):
        raise RuntimeError(f"response missing data.job: {envelope}")
    return job


def main() -> None:
    parser = argparse.ArgumentParser(description="Submit job_test_echo and wait for the Taskiq job flow to finish.")
    parser.add_argument("--api-url", required=True)
    parser.add_argument("--timeout-seconds", type=int, default=30)
    args = parser.parse_args()

    dotenv = load_dotenv()
    headers = build_headers(dotenv)
    api_url = args.api_url.rstrip("/")
    api_prefix = (env_value("SERVICE_API_PREFIX", dotenv) or "/api/v1/ai-jobs").rstrip("/")
    jobs_url = f"{api_url}{api_prefix}/jobs"
    message = f"taskiq-smoke-{uuid.uuid4().hex[:8]}"
    create_payload = {
        "client_request_id": f"verify-workflow-smoke-{uuid.uuid4()}",
        "job_type": "job_test_echo",
        "job_params": {"message": message, "repeat": 2},
        "metadata": {"source": "scripts/verify/job_workflow_smoke.py"},
        "options": {"priority": "normal", "idempotency_mode": "reject_duplicate"},
    }

    created = job_from_envelope(
        request_json(jobs_url, method="POST", headers=headers, payload=create_payload)
    )
    job_id = created["job_id"]

    deadline = time.monotonic() + args.timeout_seconds
    last_job = created
    while time.monotonic() < deadline:
        last_job = job_from_envelope(request_json(f"{jobs_url}/{job_id}", method="GET", headers=headers))
        status = last_job["job_status"]
        if status in {"succeeded", "failed"}:
            break
        time.sleep(0.5)
    else:
        raise RuntimeError(f"job {job_id} did not finish within {args.timeout_seconds}s; last={last_job}")

    if last_job["job_status"] != "succeeded":
        raise RuntimeError(f"job {job_id} finished with {last_job['job_status']}: {last_job}")

    expected = {"message": message, "repeated": [message, message], "count": 2}
    result = last_job.get("job_result")
    if result != expected:
        raise RuntimeError(f"job {job_id} returned unexpected result: {result}")

    print(f"workflow smoke ok: job_id={job_id} job_type=job_test_echo")


if __name__ == "__main__":
    main()
