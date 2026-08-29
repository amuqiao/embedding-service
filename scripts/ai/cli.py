from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from dotenv import dotenv_values

ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR))


class AiCliError(RuntimeError):
    def __init__(self, message: str, *, exit_code: int = 4) -> None:
        super().__init__(message)
        self.exit_code = exit_code


@dataclass(frozen=True)
class ProviderRuntime:
    provider: str
    api_key_env: str
    api_key: str
    base_url_env: str | None
    base_url: str

    @property
    def api_key_configured(self) -> bool:
        return bool(self.api_key)


@dataclass(frozen=True)
class EndpointSummary:
    scheme: str
    host: str
    path: str

    def as_dict(self) -> dict[str, str]:
        return {
            "scheme": self.scheme,
            "host": self.host,
            "path": self.path,
        }


def _print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def _provider_definitions():
    from app.ai.providers.registry import all_provider_definitions

    return all_provider_definitions()


def _resolve_repo_path(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = ROOT_DIR / path
    return path


def _read_env_file(path: Path, *, required: bool) -> dict[str, str]:
    if not path.exists():
        if required:
            raise AiCliError(f"ENV_FILE not found: {path}", exit_code=2)
        return {}
    values = dotenv_values(path)
    return {key: value for key, value in values.items() if key and value is not None}


def _selected_env_file_value(env_file: str | None) -> tuple[str, bool]:
    if env_file is not None:
        selected = env_file.strip()
        if not selected:
            raise AiCliError("--env-file must not be empty", exit_code=2)
        return selected, True

    if "ENV_FILE" in os.environ:
        selected = os.environ["ENV_FILE"].strip()
        if not selected:
            raise AiCliError("ENV_FILE must not be empty", exit_code=2)
        return selected, True

    return ".env", False


def _load_env_values(env_file: str | None) -> tuple[dict[str, str], Path | None]:
    selected_value, explicit_env_file = _selected_env_file_value(env_file)
    selected_path = _resolve_repo_path(selected_value)

    values: dict[str, str] = {}
    values.update(_read_env_file(selected_path, required=True))
    if explicit_env_file:
        return values, selected_path

    provider_env_keys = {
        key
        for definition in _provider_definitions().values()
        for key in (definition.api_key_env, definition.base_url_env)
        if key
    }
    for key in provider_env_keys:
        if key in os.environ:
            values[key] = os.environ[key]
    return values, selected_path


def _runtime_for_provider(provider: str, values: dict[str, str]) -> ProviderRuntime:
    definition = _provider_definitions().get(provider)
    if definition is None:
        available = ", ".join(sorted(_provider_definitions()))
        raise AiCliError(f"unsupported provider: {provider}; available providers: {available}", exit_code=2)

    api_key = values.get(definition.api_key_env, "").strip()
    base_url = ""
    if definition.base_url_env is not None:
        base_url = values.get(definition.base_url_env, "").strip()
    if not base_url:
        base_url = definition.default_base_url
    if not base_url and definition.name == "openai":
        base_url = "https://api.openai.com/v1"
    base_url = _validated_base_url(base_url, env_name=definition.base_url_env or f"{definition.name}.base_url")

    return ProviderRuntime(
        provider=definition.name,
        api_key_env=definition.api_key_env,
        api_key=api_key,
        base_url_env=definition.base_url_env,
        base_url=base_url,
    )


def _selected_providers(provider: str | None, values: dict[str, str]) -> list[ProviderRuntime]:
    if provider is not None:
        runtime = _runtime_for_provider(provider.strip().lower(), values)
        if not runtime.api_key_configured:
            raise AiCliError(f"{runtime.api_key_env} is required for provider {runtime.provider}", exit_code=2)
        return [runtime]

    runtimes: list[ProviderRuntime] = []
    for name in sorted(_provider_definitions()):
        runtime = _runtime_for_provider(name, values)
        if runtime.api_key_configured:
            runtimes.append(runtime)
    if not runtimes:
        keys = ", ".join(sorted(definition.api_key_env for definition in _provider_definitions().values()))
        raise AiCliError(f"no provider API key configured; set one of: {keys}", exit_code=2)
    return runtimes


def _models_url(base_url: str) -> str:
    normalized = base_url.strip().rstrip("/")
    if not normalized:
        raise AiCliError("provider base_url is required", exit_code=2)
    if normalized.endswith("/models"):
        return normalized
    return f"{normalized}/models"


def _endpoint_summary(base_url: str) -> EndpointSummary:
    parsed = urlparse(base_url)
    return EndpointSummary(
        scheme=parsed.scheme,
        host=parsed.hostname or "",
        path=parsed.path or "/",
    )


def _validated_base_url(base_url: str, *, env_name: str) -> str:
    normalized = base_url.strip().rstrip("/")
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise AiCliError(f"{env_name} must be an absolute http(s) URL", exit_code=2)
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise AiCliError(f"{env_name} must not include credentials, query, or fragment", exit_code=2)
    return normalized


def _model_row(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item.get("id"),
        "object": item.get("object"),
        "created": item.get("created"),
        "owned_by": item.get("owned_by") or item.get("owner"),
    }


def _fetch_models(runtime: ProviderRuntime, *, timeout_seconds: float) -> dict[str, Any]:
    url = _models_url(runtime.base_url)
    headers = {"Authorization": f"Bearer {runtime.api_key}"}
    try:
        with httpx.Client(timeout=timeout_seconds, follow_redirects=True) as client:
            response = client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        raise AiCliError(f"{runtime.provider} models request failed: {exc.__class__.__name__}") from exc

    if response.status_code in {401, 403}:
        raise AiCliError(f"{runtime.provider} API key is not authorized; HTTP {response.status_code}")
    if response.status_code >= 400:
        raise AiCliError(f"{runtime.provider} models request returned HTTP {response.status_code}")

    try:
        payload = response.json()
    except ValueError as exc:
        raise AiCliError(f"{runtime.provider} models response is not JSON") from exc

    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        raise AiCliError(f"{runtime.provider} models response must contain a data list")

    models = [_model_row(item) for item in data if isinstance(item, dict)]
    models.sort(key=lambda item: str(item.get("id") or ""))
    return {
        "provider": runtime.provider,
        "status": "ok",
        "api_key_env": runtime.api_key_env,
        "base_url_env": runtime.base_url_env,
        "endpoint": _endpoint_summary(runtime.base_url).as_dict(),
        "model_count": len(models),
        "models": models,
    }


def command_models(args: argparse.Namespace) -> int:
    env_file = args.command_env_file or args.env_file
    values, loaded_env_file = _load_env_values(env_file)
    provider = args.provider.strip().lower() if args.provider else None
    providers = _selected_providers(provider, values)
    results = [_fetch_models(runtime, timeout_seconds=args.timeout) for runtime in providers]

    payload = {
        "ok": True,
        "env_file": str(loaded_env_file) if loaded_env_file is not None else None,
        "providers": results,
    }
    if args.json:
        _print_json(payload)
        return 0

    print("AI Models")
    for result in results:
        print(f"- provider: {result['provider']}")
        print(f"  status: {result['status']}")
        print(f"  model_count: {result['model_count']}")
        endpoint = result["endpoint"]
        print(f"  endpoint: {endpoint['scheme']}://{endpoint['host']}{endpoint['path']}")
        for model in result["models"]:
            model_id = model.get("id")
            owned_by = model.get("owned_by")
            if owned_by:
                print(f"  - {model_id} owned_by={owned_by}")
            else:
                print(f"  - {model_id}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="./scripts/ai.sh",
        description="检查云模型厂商 API Key，并列出厂商账号当前可见模型。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
作用域:
  默认读取根目录 .env；也可通过 ENV_FILE 或 --env-file 指定配置文件。
  真实访问模型厂商的 models list 接口，用返回成功来验证 API Key 可用。

不负责:
  不提交 Job，不启动服务，不读本项目 models.yaml 作为模型列表事实源。
  不下载本地模型资产；本地权重归 scripts/models.sh。
  不执行模型推理；列模型不产生推理费用。

配置与环境变量:
  DashScope 使用 DASHSCOPE_API_KEY / DASHSCOPE_BASE_URL。
  OpenAI 使用 OPENAI_API_KEY / OPENAI_BASE_URL；OPENAI_BASE_URL 为空时使用 https://api.openai.com/v1。
  显式指定 --env-file 或 ENV_FILE 时，以指定文件为准。
  未显式指定配置文件时，读取 .env，并允许进程环境变量覆盖同名 provider 配置。

常用示例:
  ./scripts/ai.sh models
  ./scripts/ai.sh models dashscope
  ./scripts/ai.sh models openai --json
  ./scripts/ai.sh --env-file .env.test models dashscope

Exit Codes:
  0  成功
  2  参数错误或 provider API key 缺失
  4  远端 provider 请求失败、鉴权失败或响应格式不符合预期
""",
    )
    parser.add_argument("--env-file", default=None, help="显式读取 env 文件；默认读取 .env，也可用 ENV_FILE。")
    subparsers = parser.add_subparsers(dest="command", required=True)

    models = subparsers.add_parser(
        "models",
        help="列出模型厂商账号当前可见模型。",
        description="读取 env 配置，访问真实模型厂商 models list 接口，并输出该账号当前可见模型。",
    )
    models.add_argument("provider", nargs="?", help="可选 provider；不传时检查所有已配置 API key 的 provider。")
    models.add_argument("--env-file", dest="command_env_file", default=None, help="显式读取 env 文件。")
    models.add_argument("--timeout", type=float, default=20.0, help="远端请求超时秒数，默认 20。")
    models.add_argument("--json", action="store_true", help="输出机器可读 JSON。")
    models.set_defaults(func=command_models)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    try:
        return int(args.func(args))
    except AiCliError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return exc.exit_code
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
