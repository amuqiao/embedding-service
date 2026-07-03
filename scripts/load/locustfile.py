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


JOB_COUNTER = count()
TERMINAL_STATUSES = {"succeeded", "failed"}


class LoadConfigError(RuntimeError):
    pass


def env_required(name: str) -> str:
    value = os.environ.get(name)
    if value is None or value == "":
        raise LoadConfigError(f"{name} is required; run this load test through ./scripts/load.sh")
    return value


def env_optional(name: str, default: str | None = None) -> str | None:
    return os.environ.get(name) or default


def env_float(name: str, default: float) -> float:
    value = env_optional(name)
    if value is None:
        return default
    return float(value)


def env_bool(name: str, default: bool = False) -> bool:
    value = env_optional(name)
    if value is None:
        return default
    return value in {"true", "True", "TRUE", "1", "yes", "YES"}


def build_headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if env_bool("LOAD_INTERNAL_AUTH_ENABLED"):
        headers["Authorization"] = f"Bearer {env_required('LOAD_INTERNAL_AUTH_TOKEN')}"
    if env_bool("LOAD_INTERNAL_CALLER_HEADER_ENABLED"):
        headers["X-AI-Service-Caller-ID"] = env_required("LOAD_INTERNAL_CALLER_ID")
    return headers


def job_from_envelope(envelope: dict[str, Any]) -> dict[str, Any]:
    if envelope.get("code") != "0":
        raise ValueError(f"unexpected response code: {envelope.get('code')}")
    job = envelope.get("data", {}).get("job")
    if not isinstance(job, dict):
        raise ValueError("response missing data.job")
    return job


def load_query_job_ids() -> list[str]:
    inline = env_optional("LOAD_INTERNAL_QUERY_JOB_IDS")
    if inline:
        return [validated_job_id(item.strip()) for item in inline.split(",") if item.strip()]
    file_path = env_optional("LOAD_INTERNAL_QUERY_JOB_IDS_FILE")
    if not file_path:
        return []
    path = Path(file_path)
    return [validated_job_id(line.strip()) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def validated_job_id(value: str) -> str:
    try:
        return str(uuid.UUID(value))
    except ValueError as exc:
        raise LoadConfigError(f"query job id must be UUID: {value[:80]}") from exc


def failure_message(response) -> str:
    request_id = response.headers.get("x-request-id") or response.headers.get("X-Request-ID") or "-"
    code = "-"
    try:
        data = response.json()
        if isinstance(data, dict):
            code = str(data.get("code") or "-")
    except Exception:  # noqa: BLE001 - failure path must not leak body.
        pass
    return f"HTTP {response.status_code} code={code} request_id={request_id}"


def build_job_params(job_type: str, sequence: int) -> dict[str, Any]:
    raw = env_optional("LOAD_INTERNAL_JOB_PARAMS_JSON")
    if raw:
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise LoadConfigError("LOAD_INTERNAL_JOB_PARAMS_JSON must be an object")
        return value
    if job_type == "job_test_echo":
        return {
            "message": f"load-{sequence}",
            "repeat": int(env_optional("LOAD_INTERNAL_ECHO_REPEAT", "1") or "1"),
            "sleep_seconds": env_float("LOAD_INTERNAL_ECHO_SLEEP_SECONDS", 15.0),
        }
    if job_type == "job_test_workflow":
        return {
            "mode": env_optional("LOAD_INTERNAL_WORKFLOW_MODE", "group") or "group",
            "label": f"load-{sequence}",
            "sleep_seconds": env_float("LOAD_INTERNAL_WORKFLOW_SLEEP_SECONDS", 15.0),
        }
    raise LoadConfigError("custom job_type requires LOAD_INTERNAL_JOB_PARAMS_JSON")


class ProjectLoadUser(HttpUser):
    wait_time = between(
        env_float("LOAD_INTERNAL_WAIT_MIN_SECONDS", 0.1),
        env_float("LOAD_INTERNAL_WAIT_MAX_SECONDS", 1.0),
    )

    def on_start(self) -> None:
        self.scenario_key = env_required("LOAD_INTERNAL_SCENARIO_KEY")
        self.scenario_kind = env_required("LOAD_INTERNAL_SCENARIO_KIND")
        self.api_prefix = env_required("LOAD_INTERNAL_API_PREFIX").rstrip("/")
        self.headers = build_headers()
        self.jobs_path = f"{self.api_prefix}/jobs"
        self.job_type = env_optional("LOAD_INTERNAL_JOB_TYPE")
        self.query_job_ids = load_query_job_ids()
        self.poll_interval = env_float("LOAD_INTERNAL_POLL_INTERVAL_SECONDS", 0.5)
        self.flow_timeout = env_float("LOAD_INTERNAL_FLOW_TIMEOUT_SECONDS", 45.0)
        self.http_method = env_optional("LOAD_INTERNAL_HTTP_METHOD", "GET") or "GET"
        self.http_path = env_optional("LOAD_INTERNAL_HTTP_PATH", "") or ""

        if self.scenario_kind in {"job_submit", "job_flow"} and not self.job_type:
            raise LoadConfigError(f"{self.scenario_key} requires LOAD_INTERNAL_JOB_TYPE")
        if self.scenario_kind == "job_query" and not self.query_job_ids:
            raise LoadConfigError(f"{self.scenario_key} requires query job ids")
        if self.scenario_kind == "api_request" and not self.http_path.startswith("/"):
            raise LoadConfigError("api_request requires an absolute LOAD_INTERNAL_HTTP_PATH")

    @task
    def run_scenario(self) -> None:
        if self.scenario_kind == "job_submit":
            self.submit_job()
        elif self.scenario_kind == "job_query":
            self.query_job(random.choice(self.query_job_ids))
        elif self.scenario_kind == "job_flow":
            self.run_job_flow()
        elif self.scenario_kind == "api_request":
            self.run_api_request()
        else:
            raise LoadConfigError(f"unsupported scenario kind: {self.scenario_kind}")

    def submit_job(self) -> dict[str, Any] | None:
        sequence = next(JOB_COUNTER)
        payload = {
            "client_request_id": f"load-{self.scenario_key}-{uuid.uuid4()}-{sequence}",
            "job_type": self.job_type,
            "job_params": build_job_params(str(self.job_type), sequence),
            "metadata": {
                "source": "scripts/load.sh",
                "scenario_key": self.scenario_key,
            },
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
                    raise ValueError(failure_message(response))
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
                    raise ValueError(failure_message(response))
                return job_from_envelope(response.json())
            except Exception as exc:
                response.failure(str(exc))
                return None

    def run_job_flow(self) -> None:
        started = time.perf_counter()
        job = self.submit_job()
        if not job:
            return
        job_id = str(job["job_id"])
        last_job = job
        deadline = time.monotonic() + self.flow_timeout
        while time.monotonic() < deadline:
            last_job = self.query_job(job_id) or last_job
            if last_job["job_status"] in TERMINAL_STATUSES:
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

    def run_api_request(self) -> None:
        name = f"{self.http_method} {self.http_path}"
        with self.client.request(
            self.http_method,
            self.http_path,
            headers=self.headers,
            name=name,
            catch_response=True,
        ) as response:
            if response.status_code >= 400:
                response.failure(failure_message(response))
