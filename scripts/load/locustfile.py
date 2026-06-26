from __future__ import annotations

import json
import os
import random
import time
import uuid
from itertools import count
from pathlib import Path
from typing import Any

import gevent
from locust import HttpUser, between, events, task


ROOT_DIR = Path(__file__).resolve().parents[2]
JOB_COUNTER = count()


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


DOTENV = load_dotenv()


def env_value(name: str, default: str | None = None) -> str | None:
    return os.environ.get(name) or DOTENV.get(name) or default


def env_float(name: str, default: float) -> float:
    value = env_value(name)
    if value is None:
        return default
    return float(value)


def is_true(value: str | None) -> bool:
    return value in {"true", "True", "TRUE"}


def api_prefix() -> str:
    return (env_value("SERVICE_API_PREFIX", "/api/v1/ai-jobs") or "").rstrip("/")


def build_headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if not is_true(env_value("DISABLE_HTTP_AUTH_HEADER")):
        token = env_value("SERVICE_API_KEY")
        if not token or token.startswith("<"):
            raise RuntimeError("SERVICE_API_KEY is required unless DISABLE_HTTP_AUTH_HEADER=true")
        headers["Authorization"] = f"Bearer {token}"
    if not is_true(env_value("DISABLE_CALLER_ID_HEADER")):
        headers["X-AI-Service-Caller-ID"] = env_value("LOAD_CALLER_ID", "locust-load") or "locust-load"
    return headers


def job_from_envelope(envelope: dict[str, Any]) -> dict[str, Any]:
    if envelope.get("code") != "0":
        raise ValueError(f"unexpected response code: {envelope.get('code')}")
    job = envelope.get("data", {}).get("job")
    if not isinstance(job, dict):
        raise ValueError("response missing data.job")
    return job


class JobLoadUser(HttpUser):
    wait_time = between(env_float("LOAD_WAIT_MIN_SECONDS", 0.1), env_float("LOAD_WAIT_MAX_SECONDS", 1.0))

    def on_start(self) -> None:
        self.headers = build_headers()
        self.jobs_path = f"{api_prefix()}/jobs"
        self.scenario = env_value("LOAD_SCENARIO", "flow")
        self.job_type = env_value("LOAD_JOB_TYPE", "job_test_echo")
        self.poll_interval = env_float("LOAD_POLL_INTERVAL_SECONDS", 0.5)
        self.flow_timeout = env_float("LOAD_FLOW_TIMEOUT_SECONDS", 30.0)
        self.query_job_ids = self._load_query_job_ids()
        if self.scenario not in {"submit", "query", "flow"}:
            raise RuntimeError("LOAD_SCENARIO must be one of: submit, query, flow")
        if self.scenario == "query" and not self.query_job_ids:
            raise RuntimeError("LOAD_QUERY_JOB_IDS or LOAD_QUERY_JOB_IDS_FILE is required when LOAD_SCENARIO=query")

    @task
    def run_scenario(self) -> None:
        if self.scenario == "submit":
            self.submit_job()
        elif self.scenario == "query":
            self.query_job(random.choice(self.query_job_ids))
        else:
            self.run_flow()

    def submit_job(self) -> dict[str, Any] | None:
        sequence = next(JOB_COUNTER)
        payload = {
            "client_request_id": f"locust-{uuid.uuid4()}-{sequence}",
            "job_type": self.job_type,
            "job_params": self.job_params(sequence),
            "metadata": {"source": "scripts/load/locustfile.py", "scenario": self.scenario},
            "options": {"priority": "normal", "idempotency_mode": "reject_duplicate"},
        }
        with self.client.post(
            self.jobs_path,
            data=json.dumps(payload),
            headers=self.headers,
            name="POST /jobs",
            catch_response=True,
        ) as response:
            try:
                if response.status_code >= 400:
                    raise ValueError(f"HTTP {response.status_code}: {response.text[:300]}")
                return job_from_envelope(response.json())
            except Exception as exc:
                response.failure(str(exc))
                return None

    def query_job(self, job_id: str) -> dict[str, Any] | None:
        with self.client.get(
            f"{self.jobs_path}/{job_id}",
            headers=self.headers,
            name="GET /jobs/{job_id}",
            catch_response=True,
        ) as response:
            try:
                if response.status_code >= 400:
                    raise ValueError(f"HTTP {response.status_code}: {response.text[:300]}")
                return job_from_envelope(response.json())
            except Exception as exc:
                response.failure(str(exc))
                return None

    def run_flow(self) -> None:
        started = time.perf_counter()
        job = self.submit_job()
        if not job:
            return
        job_id = str(job["job_id"])
        last_job = job
        deadline = time.monotonic() + self.flow_timeout
        while time.monotonic() < deadline:
            last_job = self.query_job(job_id) or last_job
            if last_job["job_status"] in {"succeeded", "failed"}:
                break
            gevent.sleep(self.poll_interval)

        elapsed_ms = (time.perf_counter() - started) * 1000
        status = last_job["job_status"]
        exception = None if status == "succeeded" else RuntimeError(f"job {job_id} ended with {status}")
        events.request.fire(
            request_type="JOB",
            name="flow terminal latency",
            response_time=elapsed_ms,
            response_length=0,
            exception=exception,
        )

    def job_params(self, sequence: int) -> dict[str, Any]:
        if self.job_type == "job_test_echo":
            return {
                "message": f"load-{sequence}",
                "repeat": int(env_value("LOAD_ECHO_REPEAT", "1") or "1"),
                "sleep_seconds": env_float("LOAD_ECHO_SLEEP_SECONDS", 15.0),
            }
        if self.job_type == "job_test_workflow":
            return {
                "mode": env_value("LOAD_WORKFLOW_MODE", "group"),
                "label": f"load-{sequence}",
                "sleep_seconds": env_float("LOAD_WORKFLOW_SLEEP_SECONDS", 15.0),
            }
        raw = env_value("LOAD_JOB_PARAMS_JSON")
        if not raw:
            raise RuntimeError(
                "LOAD_JOB_PARAMS_JSON is required when LOAD_JOB_TYPE is not job_test_echo or job_test_workflow"
            )
        return json.loads(raw)

    def _load_query_job_ids(self) -> list[str]:
        inline = env_value("LOAD_QUERY_JOB_IDS")
        if inline:
            return [item.strip() for item in inline.split(",") if item.strip()]
        file_path = env_value("LOAD_QUERY_JOB_IDS_FILE")
        if not file_path:
            return []
        return [
            line.strip()
            for line in Path(file_path).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
