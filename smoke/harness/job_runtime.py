from __future__ import annotations

from dataclasses import dataclass
import json
import os
import time
from ipaddress import ip_address
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_API_PREFIX = "/api/v1/ai-jobs"
TERMINAL_STATUSES = {"succeeded", "failed"}
LOCAL_API_HOSTS = {"localhost", "0.0.0.0", "::1"}


class FlowError(RuntimeError):
    exit_code: int

    def __init__(self, message: str, *, exit_code: int = 4) -> None:
        super().__init__(message)
        self.exit_code = exit_code


@dataclass(frozen=True)
class RuntimeContext:
    app_env: dict[str, str]
    summary: dict[str, Any]


def load_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip("'\"")
    return values


def configured_env_file(env_file: str | None = None) -> str | None:
    if env_file is not None:
        return env_file
    return os.environ.get("ENV_FILE")


def resolve_env_file_path(env_file: str | None = None, *, root_dir: Path | None = None) -> Path:
    base_dir = root_dir or ROOT_DIR
    selected = configured_env_file(env_file)
    if selected is None:
        return base_dir / ".env"
    path = Path(selected).expanduser()
    if path.is_absolute():
        return path
    return base_dir / path


def load_app_env(env_file: str | None = None, *, root_dir: Path | None = None) -> dict[str, str]:
    path = resolve_env_file_path(env_file, root_dir=root_dir)
    if configured_env_file(env_file) is not None and not path.is_file():
        raise FlowError(f"env file not found: {path}", exit_code=2)
    return load_env_file(path)


def env_value(name: str, *files: dict[str, str]) -> str | None:
    if os.environ.get(name) is not None:
        return os.environ[name]
    for values in files:
        if values.get(name) is not None:
            return values[name]
    return None


def env_source(name: str, *files: dict[str, str]) -> str:
    if os.environ.get(name) is not None:
        return "runtime_env"
    for values in files:
        if values.get(name) is not None:
            return "env_file"
    return "missing"


def bool_enabled(value: str | None) -> bool:
    return value in {"true", "True", "TRUE"}


def require_api_url(api_url: str, *, allow_remote_api: bool = False) -> str:
    parsed = urlparse(api_url)
    host = parsed.hostname
    if parsed.scheme not in {"http", "https"} or not host:
        raise FlowError(f"api url must be an absolute http(s) URL: {api_url}", exit_code=2)
    if host in LOCAL_API_HOSTS:
        return api_url.rstrip("/")
    try:
        if ip_address(host).is_loopback:
            return api_url.rstrip("/")
    except ValueError:
        pass
    if allow_remote_api:
        return api_url.rstrip("/")
    raise FlowError(
        f"smoke only targets local API URLs unless --allow-remote-api is set, got host={host}",
        exit_code=2,
    )


def resolved_api_url(api_url: str | None, app_env: dict[str, str], *, allow_remote_api: bool = False) -> str:
    if api_url:
        return require_api_url(api_url.rstrip("/"), allow_remote_api=allow_remote_api)
    configured = env_value("API_URL", app_env)
    if configured:
        return require_api_url(configured.rstrip("/"), allow_remote_api=allow_remote_api)
    host = env_value("API_HOST", app_env) or "127.0.0.1"
    port = env_value("API_PORT", app_env) or "8100"
    return require_api_url(f"http://{host}:{port}", allow_remote_api=allow_remote_api)


def build_headers(app_env: dict[str, str], *, caller_id: str, service_api_key: str | None = None) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if not bool_enabled(env_value("DISABLE_HTTP_AUTH_HEADER", app_env)):
        token = service_api_key if service_api_key is not None else env_value("SERVICE_API_KEY", app_env)
        if not token or token.startswith("<"):
            raise FlowError(
                "SERVICE_API_KEY is required unless DISABLE_HTTP_AUTH_HEADER=true; "
                "set SERVICE_API_KEY in the shell/env file or pass --service-api-key",
                exit_code=2,
            )
        headers["Authorization"] = f"Bearer {token}"
    if not bool_enabled(env_value("DISABLE_CALLER_ID_HEADER", app_env)):
        headers["X-AI-Service-Caller-ID"] = caller_id
    return headers


def resolve_runtime_context(
    *,
    env_file: str | None,
    api_url: str | None,
    allow_remote_api: bool,
    caller_id: str,
    service_api_key: str | None = None,
    root_dir: Path | None = None,
) -> RuntimeContext:
    env_path = resolve_env_file_path(env_file, root_dir=root_dir)
    app_env = load_app_env(env_file, root_dir=root_dir)
    base_url = resolved_api_url(api_url, app_env, allow_remote_api=allow_remote_api)
    api_url_source = "cli" if api_url else env_source("API_URL", app_env)
    if api_url_source == "missing":
        api_url_source = "derived_from_api_host_port"
    api_prefix = (env_value("SERVICE_API_PREFIX", app_env) or DEFAULT_API_PREFIX).rstrip("/")
    auth_enabled = not bool_enabled(env_value("DISABLE_HTTP_AUTH_HEADER", app_env))
    caller_header_enabled = not bool_enabled(env_value("DISABLE_CALLER_ID_HEADER", app_env))
    token_source = "cli" if service_api_key is not None else env_source("SERVICE_API_KEY", app_env)
    storage_backend = env_value("STORAGE_BACKEND", app_env) or "local"
    problems: list[str] = []
    token = service_api_key if service_api_key is not None else env_value("SERVICE_API_KEY", app_env)
    if auth_enabled and (not token or token.startswith("<")):
        problems.append("SERVICE_API_KEY is required unless DISABLE_HTTP_AUTH_HEADER=true")
    if storage_backend == "aliyun_oss":
        for name in ["OSS_BUCKET", "OSS_REGION", "OSS_ACCESS_KEY_ID", "OSS_ACCESS_KEY_SECRET", "OSS_PROJECT_ROOT"]:
            if not env_value(name, app_env):
                problems.append(f"{name} is required when STORAGE_BACKEND=aliyun_oss")
    summary = {
        "ready": not problems,
        "problems": problems,
        "env_file": str(env_path),
        "env_file_exists": env_path.is_file(),
        "api_url": base_url,
        "api_url_source": api_url_source,
        "api_prefix": api_prefix,
        "jobs_url": f"{base_url}{api_prefix}/jobs",
        "allow_remote_api": allow_remote_api,
        "auth_header_enabled": auth_enabled,
        "service_api_key_source": token_source if auth_enabled else "disabled",
        "service_api_key_present": (service_api_key is not None or env_value("SERVICE_API_KEY", app_env) is not None),
        "caller_id_header_enabled": caller_header_enabled,
        "caller_id": caller_id if caller_header_enabled else "-",
        "storage_backend": storage_backend,
        "oss_bucket": env_value("OSS_BUCKET", app_env) or "-",
        "oss_region": env_value("OSS_REGION", app_env) or "-",
        "oss_project_root": env_value("OSS_PROJECT_ROOT", app_env) or "-",
        "oss_output_prefix": env_value("OSS_OUTPUT_PREFIX", app_env) or "ai-jobs",
        "oss_endpoint": env_value("OSS_ENDPOINT", app_env) or "-",
        "oss_public_endpoint": env_value("OSS_PUBLIC_ENDPOINT", app_env) or "-",
    }
    return RuntimeContext(app_env=app_env, summary=summary)


def request_json(
    url: str,
    *,
    method: str,
    headers: dict[str, str],
    payload: dict[str, Any] | None = None,
    timeout_seconds: int = 10,
) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen(req, timeout=timeout_seconds) as response:
            data = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise FlowError(f"{method} {url} failed with HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise FlowError(f"{method} {url} failed: {exc.reason}") from exc
    if not isinstance(data, dict):
        raise FlowError(f"{method} {url} returned non-object JSON")
    return data


def data_object(envelope: dict[str, Any], key: str) -> dict[str, Any]:
    if envelope.get("code") != "0":
        raise FlowError(f"unexpected response envelope: {envelope}")
    data = envelope.get("data")
    if not isinstance(data, dict):
        raise FlowError(f"response missing data object: {envelope}")
    value = data.get(key)
    if not isinstance(value, dict):
        raise FlowError(f"response missing data.{key}: {envelope}")
    return value


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
        last_envelope = request_json(f"{jobs_url}/{job_id}", method="GET", headers=headers)
        last_job = data_object(last_envelope, "job")
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
    return data_object(
        poll_job_envelope(
            jobs_url=jobs_url,
            job_id=job_id,
            headers=headers,
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
        ),
        "job",
    )
