from __future__ import annotations

from dataclasses import dataclass
from ipaddress import ip_address
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from smoke.harness import env_runtime
from smoke.harness.errors import FlowError


LOCAL_API_HOSTS = {"localhost", "0.0.0.0", "::1"}


@dataclass(frozen=True)
class RuntimeContext:
    app_env: dict[str, str]
    summary: dict[str, Any]


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
    configured = env_runtime.env_value("API_URL", app_env)
    if configured:
        return require_api_url(configured.rstrip("/"), allow_remote_api=allow_remote_api)
    host = env_runtime.env_value("API_HOST", app_env) or "127.0.0.1"
    port = env_runtime.env_value("API_PORT", app_env) or "8100"
    return require_api_url(f"http://{host}:{port}", allow_remote_api=allow_remote_api)


def build_headers(app_env: dict[str, str], *, caller_id: str, service_api_key: str | None = None) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if not env_runtime.bool_enabled(env_runtime.env_value("DISABLE_HTTP_AUTH_HEADER", app_env)):
        token = service_api_key if service_api_key is not None else env_runtime.env_value("SERVICE_API_KEY", app_env)
        if not token or token.startswith("<"):
            raise FlowError(
                "SERVICE_API_KEY is required unless DISABLE_HTTP_AUTH_HEADER=true; "
                "set SERVICE_API_KEY in the shell/env file or pass --service-api-key",
                exit_code=2,
            )
        headers["Authorization"] = f"Bearer {token}"
    if not env_runtime.bool_enabled(env_runtime.env_value("DISABLE_CALLER_ID_HEADER", app_env)):
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
    env_path = env_runtime.resolve_env_file_path(env_file, root_dir=root_dir)
    app_env = env_runtime.load_app_env(env_file, root_dir=root_dir)
    base_url = resolved_api_url(api_url, app_env, allow_remote_api=allow_remote_api)
    api_url_source = "cli" if api_url else env_runtime.env_source("API_URL", app_env)
    if api_url_source == "missing":
        api_url_source = "derived_from_api_host_port"
    auth_enabled = not env_runtime.bool_enabled(env_runtime.env_value("DISABLE_HTTP_AUTH_HEADER", app_env))
    caller_header_enabled = not env_runtime.bool_enabled(env_runtime.env_value("DISABLE_CALLER_ID_HEADER", app_env))
    token_source = "cli" if service_api_key is not None else env_runtime.env_source("SERVICE_API_KEY", app_env)
    problems: list[str] = []
    token = service_api_key if service_api_key is not None else env_runtime.env_value("SERVICE_API_KEY", app_env)
    if auth_enabled and (not token or token.startswith("<")):
        problems.append("SERVICE_API_KEY is required unless DISABLE_HTTP_AUTH_HEADER=true")
    summary = {
        "ready": not problems,
        "problems": problems,
        "env_file": str(env_path),
        "env_file_exists": env_path.is_file(),
        "api_url": base_url,
        "api_url_source": api_url_source,
        "allow_remote_api": allow_remote_api,
        "auth_header_enabled": auth_enabled,
        "service_api_key_source": token_source if auth_enabled else "disabled",
        "service_api_key_present": (service_api_key is not None or env_runtime.env_value("SERVICE_API_KEY", app_env) is not None),
        "caller_id_header_enabled": caller_header_enabled,
        "caller_id": caller_id if caller_header_enabled else "-",
    }
    return RuntimeContext(app_env=app_env, summary=summary)
