from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

from smoke.harness import env_runtime
from smoke.harness import http_runtime
from smoke.harness import service_runtime
from smoke.harness.errors import FlowError


DEFAULT_JOB_API_PREFIX = "/api/v1/ai-jobs"
TERMINAL_STATUSES = {"succeeded", "failed"}
JobRuntimeContext = service_runtime.RuntimeContext


def resolve_job_context(
    *,
    env_file: str | None,
    api_url: str | None,
    allow_remote_api: bool,
    caller_id: str,
    service_api_key: str | None = None,
    root_dir: Path | None = None,
) -> JobRuntimeContext:
    context = service_runtime.resolve_runtime_context(
        env_file=env_file,
        api_url=api_url,
        allow_remote_api=allow_remote_api,
        caller_id=caller_id,
        service_api_key=service_api_key,
        root_dir=root_dir,
    )
    app_env = context.app_env
    api_prefix = (env_runtime.env_value("SERVICE_API_PREFIX", app_env) or DEFAULT_JOB_API_PREFIX).rstrip("/")
    storage_backend = env_runtime.env_value("STORAGE_BACKEND", app_env) or "local"
    problems = list(context.summary["problems"])
    if storage_backend == "aliyun_oss":
        for name in ["OSS_BUCKET", "OSS_REGION", "OSS_ACCESS_KEY_ID", "OSS_ACCESS_KEY_SECRET", "OSS_PROJECT_ROOT"]:
            if not env_runtime.env_value(name, app_env):
                problems.append(f"{name} is required when STORAGE_BACKEND=aliyun_oss")

    summary = {
        **context.summary,
        "ready": not problems,
        "problems": problems,
        "api_prefix": api_prefix,
        "jobs_url": f"{context.summary['api_url']}{api_prefix}/jobs",
        "storage_backend": storage_backend,
        "oss_bucket": env_runtime.env_value("OSS_BUCKET", app_env) or "-",
        "oss_region": env_runtime.env_value("OSS_REGION", app_env) or "-",
        "oss_project_root": env_runtime.env_value("OSS_PROJECT_ROOT", app_env) or "-",
        "oss_output_prefix": env_runtime.env_value("OSS_OUTPUT_PREFIX", app_env) or "ai-jobs",
        "oss_endpoint": env_runtime.env_value("OSS_ENDPOINT", app_env) or "-",
        "oss_public_endpoint": env_runtime.env_value("OSS_PUBLIC_ENDPOINT", app_env) or "-",
    }
    return service_runtime.RuntimeContext(app_env=app_env, summary=summary)


def poll_job_envelope(
    *,
    jobs_url: str,
    job_id: str,
    headers: dict[str, str],
    timeout_seconds: float,
    poll_interval_seconds: float,
    progress_callback: Callable[[dict[str, Any], float], None] | None = None,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    started = time.monotonic()
    last_envelope: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        last_envelope = http_runtime.request_json(f"{jobs_url}/{job_id}", method="GET", headers=headers)
        last_job = http_runtime.data_object(last_envelope, "job")
        if last_job.get("job_status") in TERMINAL_STATUSES:
            return last_envelope
        if progress_callback is not None:
            progress_callback(last_job, time.monotonic() - started)
        time.sleep(poll_interval_seconds)
    raise FlowError(f"job {job_id} did not finish within {timeout_seconds}s; last={last_envelope}", exit_code=5)


def poll_job(
    *,
    jobs_url: str,
    job_id: str,
    headers: dict[str, str],
    timeout_seconds: float,
    poll_interval_seconds: float,
) -> dict[str, Any]:
    return http_runtime.data_object(
        poll_job_envelope(
            jobs_url=jobs_url,
            job_id=job_id,
            headers=headers,
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
        ),
        "job",
    )
