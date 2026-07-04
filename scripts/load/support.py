from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from ipaddress import ip_address
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_API_PREFIX = "/api/v1/ai-jobs"
LOCAL_API_HOSTS = {"localhost", "0.0.0.0", "::1"}


class LoadError(RuntimeError):
    exit_code: int

    def __init__(self, message: str, *, exit_code: int = 2) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def resolve_env_file_path(env_file: str | None = None) -> Path:
    if env_file is None:
        return ROOT_DIR / ".env"
    path = Path(env_file).expanduser()
    if path.is_absolute():
        return path
    return ROOT_DIR / path


def load_app_env(env_file: str | None = None) -> dict[str, str]:
    path = resolve_env_file_path(env_file)
    if env_file is not None and not path.is_file():
        raise LoadError(f"env file not found: {path}", exit_code=2)
    return load_env_file(path)


def env_value(name: str, *files: dict[str, str]) -> str | None:
    if os.environ.get(name) is not None:
        return os.environ[name]
    for values in files:
        if values.get(name) is not None:
            return values[name]
    return None


def bool_enabled(value: str | None) -> bool:
    return value in {"true", "True", "TRUE", "1", "yes", "YES"}


def is_local_api_url(api_url: str) -> bool:
    parsed = urlparse(api_url)
    host = parsed.hostname
    if parsed.scheme not in {"http", "https"} or not host:
        raise LoadError(f"api url must be an absolute http(s) URL: {api_url}", exit_code=2)
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise LoadError("api url must not contain userinfo, query, or fragment", exit_code=2)
    if host in LOCAL_API_HOSTS:
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


def require_api_url(api_url: str, *, allow_remote_api: bool) -> str:
    normalized = api_url.rstrip("/")
    if is_local_api_url(normalized) or allow_remote_api:
        return normalized
    host = urlparse(normalized).hostname
    raise LoadError(
        f"load.sh only targets local API URLs unless --allow-remote-api is set, got host={host}",
        exit_code=2,
    )


def resolve_api_url(api_url: str | None, app_env: dict[str, str], *, allow_remote_api: bool) -> str:
    if api_url:
        return require_api_url(api_url, allow_remote_api=allow_remote_api)
    configured = env_value("API_URL", app_env)
    if configured:
        return require_api_url(configured, allow_remote_api=allow_remote_api)
    host = env_value("API_HOST", app_env) or "127.0.0.1"
    port = env_value("API_PORT", app_env) or "8100"
    return require_api_url(f"http://{host}:{port}", allow_remote_api=allow_remote_api)


def resolve_api_prefix(app_env: dict[str, str]) -> str:
    return (env_value("SERVICE_API_PREFIX", app_env) or DEFAULT_API_PREFIX).rstrip("/")


def resolve_auth(
    app_env: dict[str, str],
    *,
    service_api_key: str | None,
    caller_id: str,
) -> dict[str, Any]:
    auth_enabled = not bool_enabled(env_value("DISABLE_HTTP_AUTH_HEADER", app_env))
    caller_header_enabled = not bool_enabled(env_value("DISABLE_CALLER_ID_HEADER", app_env))
    token = service_api_key if service_api_key is not None else env_value("SERVICE_API_KEY", app_env)
    if auth_enabled and (not token or token.startswith("<")):
        raise LoadError(
            "SERVICE_API_KEY is required unless DISABLE_HTTP_AUTH_HEADER=true; "
            "set SERVICE_API_KEY in the shell/env file or pass --service-api-key",
            exit_code=2,
        )
    return {
        "auth_enabled": auth_enabled,
        "auth_token": token or "",
        "caller_header_enabled": caller_header_enabled,
        "caller_id": caller_id,
    }


def load_json_object(*, raw: str | None, file_path: str | None, option_name: str) -> dict[str, Any] | None:
    if raw and file_path:
        raise LoadError(f"{option_name} only accepts one of inline JSON or file", exit_code=2)
    if not raw and not file_path:
        return None
    source = raw
    if file_path:
        path = Path(file_path).expanduser()
        if not path.is_absolute():
            path = ROOT_DIR / path
        if not path.is_file():
            raise LoadError(f"{option_name} file not found: {path}", exit_code=2)
        source = path.read_text(encoding="utf-8")
    try:
        value = json.loads(source or "")
    except json.JSONDecodeError as exc:
        raise LoadError(f"{option_name} must be valid JSON object: {exc}", exit_code=2) from exc
    if not isinstance(value, dict):
        raise LoadError(f"{option_name} must be a JSON object", exit_code=2)
    return value


def is_demo_job_type(job_type: str | None) -> bool:
    if not job_type:
        return False
    from app.jobs import registry as job_registry
    from app.jobs.types.register import register_all_job_types

    register_all_job_types()
    try:
        spec = job_registry.all_job_type_specs()[job_type]
    except KeyError as exc:
        raise LoadError(f"unknown job_type: {job_type}", exit_code=2) from exc
    return spec.visibility == "demo" and job_type.startswith("example_")


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
