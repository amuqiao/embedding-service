from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

import typer


@dataclass(frozen=True)
class SmokeOptions:
    api_url: str | None
    env_file: str | None
    allow_remote_api: bool
    service_api_key: str | None
    caller_id: str
    timeout_seconds: int
    poll_interval_seconds: float
    output_dir: str | None
    json_output: bool


@dataclass(frozen=True)
class CallbackSmokeOptions:
    callback_url: str | None
    local_callback: bool
    callback_event: str
    wait_callback: bool
    callback_timeout_seconds: int | None


BaseUrlOption = Annotated[
    str | None,
    typer.Option("--base-url", help="服务 HTTP base URL；默认从 env 文件的 API_URL 或 API_HOST/API_PORT 推导。"),
]
EnvFileOption = Annotated[
    str | None,
    typer.Option("--env-file", help="显式配置文件路径；默认读取仓库根目录 .env，运行时环境变量优先。"),
]
AllowRemoteApiOption = Annotated[
    bool,
    typer.Option("--allow-remote-api", help="允许 --base-url 或 API_URL 指向非本机地址。"),
]
ServiceApiKeyOption = Annotated[
    str | None,
    typer.Option("--service-api-key", help="覆盖 SERVICE_API_KEY，作为 Authorization: Bearer token 发送。"),
]
CallerIdOption = Annotated[
    str,
    typer.Option("--caller-id", help="X-AI-Service-Caller-ID。"),
]
TimeoutOption = Annotated[
    int,
    typer.Option("--timeout", min=1, help="场景最大等待秒数；Job 场景覆盖提交、轮询、callback 等全流程。"),
]
PollIntervalOption = Annotated[
    float,
    typer.Option("--poll-interval", min=0.1, help="轮询 Job 状态的间隔秒数。"),
]
OutputDirOption = Annotated[
    str | None,
    typer.Option("--output-dir", help="artifacts 或下载输出目录，默认由场景决定。"),
]
JsonOutputOption = Annotated[
    bool,
    typer.Option("--json", help="输出机器可读 JSON；全局参数，放在场景命令前。"),
]

CallbackUrlOption = Annotated[
    str | None,
    typer.Option("--callback-url", help="外部 callback receiver URL。"),
]
LocalCallbackOption = Annotated[
    bool,
    typer.Option("--local-callback", help="临时启动本地 callback receiver，并等待 callbacker 投递。"),
]
CallbackEventOption = Annotated[
    str,
    typer.Option("--callback-event", help="订阅 callback 事件：succeeded、failed 或 both。"),
]
WaitCallbackOption = Annotated[
    bool,
    typer.Option("--wait-callback/--no-wait-callback", help="配置 callback 后是否等待 callback.status=delivered。"),
]
CallbackTimeoutOption = Annotated[
    int | None,
    typer.Option("--callback-timeout-seconds", min=1, help="等待 callback 的最长秒数；默认使用场景剩余 timeout。"),
]


GLOBAL_OPTION_PARAMS = {
    "api_url": "--base-url",
    "env_file": "--env-file",
    "allow_remote_api": "--allow-remote-api",
    "service_api_key": "--service-api-key",
    "caller_id": "--caller-id",
    "timeout_seconds": "--timeout",
    "poll_interval_seconds": "--poll-interval",
    "output_dir": "--output-dir",
    "json_output": "--json",
}

GLOBAL_CONTEXT_OPTIONS = frozenset(
    {
        "--base-url",
        "--env-file",
        "--allow-remote-api",
        "--service-api-key",
        "--caller-id",
        "--timeout",
        "--poll-interval",
        "--json",
    }
)
GLOBAL_OUTPUT_OPTIONS = frozenset({"--output-dir"})
GLOBAL_HEALTH_OPTIONS = frozenset({"--base-url", "--env-file", "--allow-remote-api", "--json"})
GLOBAL_READY_OPTIONS = frozenset({"--base-url", "--env-file", "--allow-remote-api", "--service-api-key", "--caller-id", "--json"})
GLOBAL_LIST_OPTIONS = frozenset({"--json"})
GLOBAL_ENV_ONLY_OPTIONS = frozenset({"--env-file"})
GLOBAL_ENV_JSON_OPTIONS = frozenset({"--env-file", "--json"})
GLOBAL_PROVIDER_OPTIONS = frozenset({"--env-file", "--timeout", "--json"})


def smoke_options(ctx: typer.Context) -> SmokeOptions:
    if not isinstance(ctx.obj, SmokeOptions):
        raise RuntimeError("smoke global options are not initialized")
    return ctx.obj


def provided_global_options(ctx: typer.Context) -> set[str]:
    root = ctx.find_root()
    provided: set[str] = set()
    for param_name, option_name in GLOBAL_OPTION_PARAMS.items():
        source = root.get_parameter_source(param_name)
        if getattr(source, "name", None) == "COMMANDLINE":
            provided.add(option_name)
    return provided


def validate_global_options(ctx: typer.Context, scenario: str, supported: set[str] | frozenset[str]) -> None:
    unsupported = sorted(provided_global_options(ctx) - set(supported))
    if unsupported:
        typer.echo(f"ERROR: {unsupported[0]} is not supported by smoke scenario '{scenario}'", err=True)
        raise typer.Exit(2)


def callback_smoke_options(
    *,
    callback_url: str | None,
    local_callback: bool,
    callback_event: str,
    wait_callback: bool,
    callback_timeout_seconds: int | None,
) -> CallbackSmokeOptions:
    return CallbackSmokeOptions(
        callback_url=callback_url,
        local_callback=local_callback,
        callback_event=callback_event,
        wait_callback=wait_callback,
        callback_timeout_seconds=callback_timeout_seconds,
    )


def callback_timeout_budget(*, remaining_seconds: float, callback_timeout_seconds: int | None) -> float:
    if callback_timeout_seconds is None:
        return remaining_seconds
    return min(remaining_seconds, float(callback_timeout_seconds))
