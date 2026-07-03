from __future__ import annotations

import csv
import json
import os
import shlex
import subprocess
from pathlib import Path
from typing import Annotated, Any

import typer

from scripts.jobs import formatters
from scripts.load.scenarios import LoadScenario, get_scenario, scenario_rows
from scripts.load.support import (
    LoadError,
    ROOT_DIR,
    ensure_parent,
    is_demo_job_type,
    load_app_env,
    load_json_object,
    resolve_api_prefix,
    resolve_api_url,
    resolve_auth,
    resolve_env_file_path,
    utc_now_iso,
)


HELP_EPILOG = """\b
作用域：
  项目级压测入口。负责选择场景、生成 Locust 命令、归档结果、输出 manifest，并联动 jobs.sh 做压后诊断。
  默认面向本地 API；远端 API 必须显式传 --allow-remote-api。

\b
不负责：
  不维护生产压测平台，不做多机压测编排，不替代 ./scripts/jobs.sh 的只读排障能力。
  不默认调用真实模型业务 Job；非 demo job_type 必须显式传 --allow-real-job。

\b
配置与环境变量：
  默认读取仓库根目录 .env；运行时环境变量优先；--env-file 可显式指定。
  API_URL 优先于 API_HOST/API_PORT；SERVICE_API_PREFIX 默认 /api/v1/ai-jobs。
  SERVICE_API_KEY 仅通过环境传给 Locust，不写入 manifest。

\b
输出：
  默认输出人读摘要、Locust 命令和结果路径。
  --json 输出机器可读 payload；错误原因输出到 stderr。

\b
运行产物：
  .run/load/<run_id>/manifest.json
  .run/load/<run_id>/locust_stats.csv
  .run/load/<run_id>/locust_failures.csv
  .run/load/<run_id>/locust_exceptions.csv
  .run/load/<run_id>/report.html

\b
常用示例：
  ./scripts/load.sh scenarios
  ./scripts/load.sh smoke
  ./scripts/load.sh run job-flow --users 4 --spawn-rate 1 --time 60s
  ./scripts/load.sh run job-submit --users 20 --spawn-rate 10 --time 30s
  ./scripts/load.sh ui job-flow --users 10 --spawn-rate 2 --time 2m
  ./scripts/load.sh pressure --run-id <run_id>

\b
副作用与保护边界：
  job-flow、job-submit、workflow-flow 会创建 Job 并写数据库。
  非本机 API 必须传 --allow-remote-api。
  非 job_test_* 的 job_type 必须传 --allow-real-job；真实模型业务可能产生费用。
  本入口不保存 token，不打印完整请求体，不自动重试失败 Job。

\b
Exit Codes:
  0  成功
  2  参数、配置、环境或安全确认错误
  4  Locust 执行失败、报告读取失败或压后诊断失败
"""

GUIDE_TEXT = """Load 压测心智模型

1. 先选问题，再选场景
   - job-submit: API 能不能接住创建请求。
   - job-query: 查询接口能不能承受轮询。
   - job-flow: 创建、执行、查询终态能不能闭环。
   - workflow-flow: root/child/finalize workflow 链路能不能闭环。
   - api-health: 基础 HTTP health 压力。

2. load.sh 负责产生压力和归档结果
   每次 run/ui/smoke 都生成 .run/load/<run_id>/manifest.json。
   Locust CSV 前缀固定为 .run/load/<run_id>/locust，HTML 报告固定为 report.html。

3. jobs.sh 负责压后诊断
   ./scripts/load.sh pressure --run-id <run_id>
   ./scripts/load.sh drain --run-id <run_id> --strict

4. 安全边界
   默认只允许本机 API 和 job_test_* demo job_type。
   远端 API 用 --allow-remote-api；真实业务 job_type 用 --allow-real-job。
"""

SMOKE_HELP_EPILOG = """\b
常用示例：
  ./scripts/load.sh smoke
  ./scripts/load.sh smoke --api-url http://127.0.0.1:18200 --run-id smoke-1
  ./scripts/load.sh smoke --dry-run --json
"""

RUN_HELP_EPILOG = """\b
常用示例：
  ./scripts/load.sh run job-flow --users 4 --spawn-rate 1 --time 60s
  ./scripts/load.sh run job-submit --users 20 --spawn-rate 10 --time 30s
  ./scripts/load.sh run job-query --query-job-ids-file .run/load/query-job-ids.txt --users 100
  ./scripts/load.sh run workflow-flow --workflow-mode group --flow-timeout-seconds 90

\b
真实业务 Job：
  ./scripts/load.sh run job-flow \\
    --job-type your_job_type \\
    --job-params-json-file .run/load/your-job-params.json \\
    --allow-real-job
"""

UI_HELP_EPILOG = """\b
常用示例：
  ./scripts/load.sh ui job-flow --users 10 --spawn-rate 2 --time 2m
  ./scripts/load.sh ui job-submit --web-host 127.0.0.1 --web-port 8089

\b
浏览器地址：
  http://127.0.0.1:8089
"""

REPORT_HELP_EPILOG = """\b
常用示例：
  ./scripts/load.sh report --run-id <run_id>
  ./scripts/load.sh report --run-id <run_id> --json
"""

PRESSURE_HELP_EPILOG = """\b
常用示例：
  ./scripts/load.sh pressure --run-id <run_id>
  ./scripts/load.sh pressure --run-id <run_id> --since 10m --api-log logs/api.log
"""

DRAIN_HELP_EPILOG = """\b
常用示例：
  ./scripts/load.sh drain --run-id <run_id>
  ./scripts/load.sh drain --run-id <run_id> --strict
"""


app = typer.Typer(
    name="load.sh",
    help="项目级压测入口。",
    epilog=HELP_EPILOG,
    no_args_is_help=False,
    add_completion=False,
    rich_markup_mode=None,
    context_settings={"help_option_names": ["-h", "--help"]},
)


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help(), err=True)
        raise typer.Exit(2)


def _run_dir(run_id: str, output_dir: str) -> Path:
    base = Path(output_dir).expanduser()
    if not base.is_absolute():
        base = ROOT_DIR / base
    return base / run_id


def _manifest_path(run_id: str, output_dir: str) -> Path:
    return _run_dir(run_id, output_dir) / "manifest.json"


def _read_manifest(run_id: str, output_dir: str) -> dict[str, Any]:
    path = _manifest_path(run_id, output_dir)
    if not path.is_file():
        raise LoadError(f"manifest not found: {path}", exit_code=4)
    return json.loads(path.read_text(encoding="utf-8"))


def _write_manifest(path: Path, payload: dict[str, Any]) -> None:
    ensure_parent(path)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def _format_float(value: float) -> str:
    return str(int(value)) if value == int(value) else str(value)


def _print_run_payload(payload: dict[str, Any]) -> None:
    formatters.section("Load Run")
    formatters.event(payload["status"].upper(), "load", f"run_id={payload['run_id']} scenario={payload['scenario_key']}")
    formatters.print_table(
        [
            {
                "api_url": payload["api_url"],
                "scenario": payload["scenario_key"],
                "job_type": payload.get("job_type") or "-",
                "users": payload["users"],
                "spawn_rate": payload["spawn_rate"],
                "time": payload["run_time"],
            }
        ],
        [
            ("api_url", "api_url"),
            ("scenario", "scenario"),
            ("job_type", "job_type"),
            ("users", "users"),
            ("spawn_rate", "spawn_rate"),
            ("time", "time"),
        ],
    )
    formatters.section("Artifacts")
    formatters.print_table(
        [
            {"name": "manifest", "path": payload["paths"]["manifest"]},
            {"name": "csv_prefix", "path": payload["paths"]["csv_prefix"]},
            {"name": "html", "path": payload["paths"]["html_report"]},
        ],
        [("name", "name"), ("path", "path")],
    )
    formatters.section("Command")
    typer.echo(shlex.join(payload["command"]))
    formatters.section("Next Checks")
    for item in payload["next_checks"]:
        typer.echo(f"- {item}")


def _job_params_env(
    *,
    scenario: LoadScenario,
    job_type: str | None,
    job_params: dict[str, Any] | None,
    echo_sleep_seconds: float,
    echo_repeat: int,
    workflow_mode: str,
    workflow_sleep_seconds: float,
) -> dict[str, str]:
    env: dict[str, str] = {}
    if job_params is not None:
        env["LOAD_INTERNAL_JOB_PARAMS_JSON"] = json.dumps(job_params, ensure_ascii=False)
        env["LOAD_INTERNAL_JOB_PARAMS_SOURCE"] = "explicit"
        return env
    if job_type == "job_test_echo":
        env.update(
            {
                "LOAD_INTERNAL_ECHO_SLEEP_SECONDS": _format_float(echo_sleep_seconds),
                "LOAD_INTERNAL_ECHO_REPEAT": str(echo_repeat),
                "LOAD_INTERNAL_JOB_PARAMS_SOURCE": "job_test_echo_defaults",
            }
        )
        return env
    if job_type == "job_test_workflow":
        env.update(
            {
                "LOAD_INTERNAL_WORKFLOW_MODE": workflow_mode,
                "LOAD_INTERNAL_WORKFLOW_SLEEP_SECONDS": _format_float(workflow_sleep_seconds),
                "LOAD_INTERNAL_JOB_PARAMS_SOURCE": "job_test_workflow_defaults",
            }
        )
        return env
    if scenario.kind in {"job_submit", "job_flow"}:
        raise LoadError(
            "custom job_type requires --job-params-json or --job-params-json-file",
            exit_code=2,
        )
    return env


def _next_checks(payload: dict[str, Any]) -> list[str]:
    checks: list[str] = []
    run_id = payload["run_id"]
    if "drain" in payload["scenario"]["post_checks"]:
        checks.append(f"./scripts/load.sh drain --run-id {run_id} --strict")
    if "pressure" in payload["scenario"]["post_checks"]:
        checks.append(f"./scripts/load.sh pressure --run-id {run_id}")
    checks.append(f"./scripts/load.sh report --run-id {run_id}")
    return checks


def _prepare_run(
    *,
    scenario_key: str,
    api_url: str | None,
    env_file: str | None,
    allow_remote_api: bool,
    service_api_key: str | None,
    caller_id: str,
    allow_real_job: bool,
    job_type: str | None,
    job_params_json: str | None,
    job_params_json_file: str | None,
    query_job_ids: str | None,
    query_job_ids_file: str | None,
    users: int | None,
    spawn_rate: float | None,
    run_time: str | None,
    run_id: str | None,
    output_dir: str,
    echo_sleep_seconds: float,
    echo_repeat: int,
    workflow_mode: str,
    workflow_sleep_seconds: float,
    wait_min_seconds: float | None,
    wait_max_seconds: float | None,
    poll_interval_seconds: float | None,
    flow_timeout_seconds: float | None,
    web: bool,
    web_host: str,
    web_port: int,
) -> tuple[dict[str, Any], list[str], dict[str, str]]:
    try:
        scenario = get_scenario(scenario_key)
    except ValueError as exc:
        raise LoadError(str(exc), exit_code=2) from exc
    app_env = load_app_env(env_file)
    base_url = resolve_api_url(api_url, app_env, allow_remote_api=allow_remote_api)
    api_prefix = resolve_api_prefix(app_env)
    auth = resolve_auth(app_env, service_api_key=service_api_key, caller_id=caller_id)
    selected_job_type = job_type or scenario.default_job_type
    if scenario.kind in {"job_submit", "job_flow"} and not selected_job_type:
        raise LoadError(f"scenario {scenario.key} requires --job-type", exit_code=2)
    if scenario.requires_job_ids and not query_job_ids and not query_job_ids_file:
        raise LoadError(f"scenario {scenario.key} requires --query-job-ids or --query-job-ids-file", exit_code=2)
    if selected_job_type and not is_demo_job_type(selected_job_type) and not allow_real_job:
        raise LoadError(
            f"job_type={selected_job_type} is not a demo job_type; pass --allow-real-job to confirm",
            exit_code=2,
        )
    job_params = load_json_object(
        raw=job_params_json,
        file_path=job_params_json_file,
        option_name="job params",
    )

    effective_users = users or scenario.default_users
    effective_spawn_rate = spawn_rate if spawn_rate is not None else scenario.default_spawn_rate
    effective_time = run_time or scenario.default_time
    effective_wait_min = wait_min_seconds if wait_min_seconds is not None else scenario.default_wait_min_seconds
    effective_wait_max = wait_max_seconds if wait_max_seconds is not None else scenario.default_wait_max_seconds
    if effective_wait_min > effective_wait_max:
        raise LoadError("--wait-min-seconds must be <= --wait-max-seconds", exit_code=2)
    effective_poll = (
        poll_interval_seconds if poll_interval_seconds is not None else scenario.default_poll_interval_seconds
    )
    effective_flow_timeout = (
        flow_timeout_seconds if flow_timeout_seconds is not None else scenario.default_flow_timeout_seconds
    )

    effective_run_id = run_id or utc_now_iso().replace(":", "").replace("+", "Z").replace(".", "-")
    run_dir = _run_dir(effective_run_id, output_dir)
    csv_prefix = run_dir / "locust"
    html_report = run_dir / "report.html"
    manifest = run_dir / "manifest.json"
    run_dir.mkdir(parents=True, exist_ok=True)

    locust_env = os.environ.copy()
    locust_env.update(
        {
            "LOAD_INTERNAL_ENTRYPOINT": "scripts/load.sh",
            "LOAD_INTERNAL_SCENARIO_KEY": scenario.key,
            "LOAD_INTERNAL_SCENARIO_KIND": scenario.kind,
            "LOAD_INTERNAL_API_PREFIX": api_prefix,
            "LOAD_INTERNAL_AUTH_ENABLED": "true" if auth["auth_enabled"] else "false",
            "LOAD_INTERNAL_AUTH_TOKEN": auth["auth_token"],
            "LOAD_INTERNAL_CALLER_HEADER_ENABLED": "true" if auth["caller_header_enabled"] else "false",
            "LOAD_INTERNAL_CALLER_ID": auth["caller_id"],
            "LOAD_INTERNAL_WAIT_MIN_SECONDS": _format_float(effective_wait_min),
            "LOAD_INTERNAL_WAIT_MAX_SECONDS": _format_float(effective_wait_max),
            "LOAD_INTERNAL_POLL_INTERVAL_SECONDS": _format_float(effective_poll),
            "LOAD_INTERNAL_FLOW_TIMEOUT_SECONDS": _format_float(effective_flow_timeout),
            "LOAD_INTERNAL_HTTP_METHOD": scenario.default_http_method or "GET",
            "LOAD_INTERNAL_HTTP_PATH": scenario.default_http_path or "",
        }
    )
    if selected_job_type:
        locust_env["LOAD_INTERNAL_JOB_TYPE"] = selected_job_type
    if query_job_ids:
        locust_env["LOAD_INTERNAL_QUERY_JOB_IDS"] = query_job_ids
    if query_job_ids_file:
        locust_env["LOAD_INTERNAL_QUERY_JOB_IDS_FILE"] = query_job_ids_file
    locust_env.update(
        _job_params_env(
            scenario=scenario,
            job_type=selected_job_type,
            job_params=job_params,
            echo_sleep_seconds=echo_sleep_seconds,
            echo_repeat=echo_repeat,
            workflow_mode=workflow_mode,
            workflow_sleep_seconds=workflow_sleep_seconds,
        )
    )

    command = [
        "uv",
        "run",
        "--group",
        "load",
        "locust",
        "-f",
        "scripts/load/locustfile.py",
        "--host",
        base_url,
        "-u",
        str(effective_users),
        "-r",
        _format_float(effective_spawn_rate),
        "-t",
        effective_time,
        "--csv",
        str(csv_prefix),
        "--html",
        str(html_report),
    ]
    if web:
        command.extend(["--autostart", "--web-host", web_host, "--web-port", str(web_port)])
    else:
        command.append("--headless")

    payload: dict[str, Any] = {
        "status": "prepared",
        "run_id": effective_run_id,
        "scenario_key": scenario.key,
        "scenario": {
            "key": scenario.key,
            "kind": scenario.kind,
            "target": scenario.target,
            "writes_jobs": scenario.writes_jobs,
            "requires_job_ids": scenario.requires_job_ids,
            "billable_risk": scenario.billable_risk or bool(selected_job_type and not is_demo_job_type(selected_job_type)),
            "post_checks": list(scenario.post_checks),
        },
        "api_url": base_url,
        "api_prefix": api_prefix,
        "env_file": str(resolve_env_file_path(env_file)),
        "allow_remote_api": allow_remote_api,
        "auth_header_enabled": auth["auth_enabled"],
        "caller_header_enabled": auth["caller_header_enabled"],
        "caller_id": auth["caller_id"] if auth["caller_header_enabled"] else "default",
        "job_type": selected_job_type,
        "job_params_source": locust_env.get("LOAD_INTERNAL_JOB_PARAMS_SOURCE", "-"),
        "query_job_ids_source": "inline" if query_job_ids else ("file" if query_job_ids_file else "-"),
        "users": effective_users,
        "spawn_rate": effective_spawn_rate,
        "run_time": effective_time,
        "wait_min_seconds": effective_wait_min,
        "wait_max_seconds": effective_wait_max,
        "poll_interval_seconds": effective_poll,
        "flow_timeout_seconds": effective_flow_timeout,
        "paths": {
            "run_dir": str(run_dir),
            "manifest": str(manifest),
            "csv_prefix": str(csv_prefix),
            "html_report": str(html_report),
        },
        "command": command,
        "started_at": utc_now_iso(),
        "completed_at": None,
        "exit_code": None,
    }
    payload["next_checks"] = _next_checks(payload)
    return payload, command, locust_env


def _execute_run(payload: dict[str, Any], command: list[str], locust_env: dict[str, str], *, dry_run: bool) -> dict[str, Any]:
    manifest = Path(payload["paths"]["manifest"])
    _write_manifest(manifest, payload)
    if dry_run:
        payload["status"] = "dry_run"
        payload["completed_at"] = utc_now_iso()
        payload["exit_code"] = 0
        _write_manifest(manifest, payload)
        return payload
    try:
        result = subprocess.run(command, cwd=ROOT_DIR, env=locust_env, check=False)
    except OSError as exc:
        raise LoadError(f"failed to execute Locust command: {exc}", exit_code=4) from exc
    payload["completed_at"] = utc_now_iso()
    payload["exit_code"] = result.returncode
    payload["status"] = "succeeded" if result.returncode == 0 else "failed"
    _write_manifest(manifest, payload)
    return payload


def _run_command(
    *,
    scenario_key: str,
    api_url: str | None,
    env_file: str | None,
    allow_remote_api: bool,
    service_api_key: str | None,
    caller_id: str,
    allow_real_job: bool,
    job_type: str | None,
    job_params_json: str | None,
    job_params_json_file: str | None,
    query_job_ids: str | None,
    query_job_ids_file: str | None,
    users: int | None,
    spawn_rate: float | None,
    run_time: str | None,
    run_id: str | None,
    output_dir: str,
    echo_sleep_seconds: float,
    echo_repeat: int,
    workflow_mode: str,
    workflow_sleep_seconds: float,
    wait_min_seconds: float | None,
    wait_max_seconds: float | None,
    poll_interval_seconds: float | None,
    flow_timeout_seconds: float | None,
    web: bool,
    web_host: str,
    web_port: int,
    dry_run: bool,
    json_output: bool,
) -> None:
    if json_output and not dry_run:
        typer.echo("ERROR: --json is only supported with --dry-run for run/ui/smoke; Locust writes to stdout", err=True)
        raise typer.Exit(2)
    try:
        payload, command, locust_env = _prepare_run(
            scenario_key=scenario_key,
            api_url=api_url,
            env_file=env_file,
            allow_remote_api=allow_remote_api,
            service_api_key=service_api_key,
            caller_id=caller_id,
            allow_real_job=allow_real_job,
            job_type=job_type,
            job_params_json=job_params_json,
            job_params_json_file=job_params_json_file,
            query_job_ids=query_job_ids,
            query_job_ids_file=query_job_ids_file,
            users=users,
            spawn_rate=spawn_rate,
            run_time=run_time,
            run_id=run_id,
            output_dir=output_dir,
            echo_sleep_seconds=echo_sleep_seconds,
            echo_repeat=echo_repeat,
            workflow_mode=workflow_mode,
            workflow_sleep_seconds=workflow_sleep_seconds,
            wait_min_seconds=wait_min_seconds,
            wait_max_seconds=wait_max_seconds,
            poll_interval_seconds=poll_interval_seconds,
            flow_timeout_seconds=flow_timeout_seconds,
            web=web,
            web_host=web_host,
            web_port=web_port,
        )
        payload = _execute_run(payload, command, locust_env, dry_run=dry_run)
    except LoadError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(exc.exit_code) from exc
    if json_output:
        formatters.print_json(payload)
    else:
        _print_run_payload(payload)
    if payload["exit_code"] not in {0, None}:
        raise typer.Exit(4)


@app.command("guide", help="查看压测入口心智模型。")
def guide() -> None:
    typer.echo(GUIDE_TEXT)


@app.command("scenarios", help="查看已注册压测场景。")
def scenarios(json_output: Annotated[bool, typer.Option("--json", help="输出 JSON。")] = False) -> None:
    payload = {"scenarios": scenario_rows()}
    if json_output:
        formatters.print_json(payload)
        return
    formatters.section("Load Scenarios")
    formatters.print_table(
        payload["scenarios"],
        [
            ("key", "key"),
            ("target", "target"),
            ("writes_jobs", "writes"),
            ("requires_job_ids", "needs_ids"),
            ("default_job_type", "default_job_type"),
            ("question", "question"),
        ],
    )


@app.command("list", help="scenarios 的别名。")
def list_scenarios(json_output: Annotated[bool, typer.Option("--json", help="输出 JSON。")] = False) -> None:
    scenarios(json_output=json_output)


@app.command("smoke", help="运行小流量 job-flow 冒烟压测。", epilog=SMOKE_HELP_EPILOG)
def smoke(
    api_url: Annotated[str | None, typer.Option("--api-url", help="API 基础 URL。")] = None,
    env_file: Annotated[str | None, typer.Option("--env-file", help="显式配置文件路径。")] = None,
    allow_remote_api: Annotated[bool, typer.Option("--allow-remote-api", help="允许非本机 API URL。")] = False,
    service_api_key: Annotated[
        str | None,
        typer.Option("--service-api-key", help="高风险：覆盖 SERVICE_API_KEY；会出现在 shell history/ps，优先用环境变量。"),
    ] = None,
    caller_id: Annotated[str, typer.Option("--caller-id", "--x-ai-service-caller-id", help="Caller ID。")] = "load-cli",
    run_id: Annotated[str | None, typer.Option("--run-id", help="显式 run_id。")] = None,
    output_dir: Annotated[str, typer.Option("--output-dir", help="结果目录根路径。")] = ".run/load",
    dry_run: Annotated[bool, typer.Option("--dry-run", help="只生成 manifest，不执行 Locust。")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="输出 JSON；仅支持 --dry-run。")] = False,
) -> None:
    _run_command(
        scenario_key="job-flow",
        api_url=api_url,
        env_file=env_file,
        allow_remote_api=allow_remote_api,
        service_api_key=service_api_key,
        caller_id=caller_id,
        allow_real_job=False,
        job_type=None,
        job_params_json=None,
        job_params_json_file=None,
        query_job_ids=None,
        query_job_ids_file=None,
        users=4,
        spawn_rate=1.0,
        run_time="60s",
        run_id=run_id,
        output_dir=output_dir,
        echo_sleep_seconds=15.0,
        echo_repeat=1,
        workflow_mode="group",
        workflow_sleep_seconds=15.0,
        wait_min_seconds=None,
        wait_max_seconds=None,
        poll_interval_seconds=0.5,
        flow_timeout_seconds=45.0,
        web=False,
        web_host="127.0.0.1",
        web_port=8089,
        dry_run=dry_run,
        json_output=json_output,
    )


@app.command("run", help="运行指定压测场景并生成 CSV/HTML/manifest。", epilog=RUN_HELP_EPILOG)
def run(
    scenario_key: Annotated[str, typer.Argument(help="场景 key，查看 ./scripts/load.sh scenarios。")],
    api_url: Annotated[str | None, typer.Option("--api-url", help="API 基础 URL。")] = None,
    env_file: Annotated[str | None, typer.Option("--env-file", help="显式配置文件路径。")] = None,
    allow_remote_api: Annotated[bool, typer.Option("--allow-remote-api", help="允许非本机 API URL。")] = False,
    service_api_key: Annotated[
        str | None,
        typer.Option("--service-api-key", help="高风险：覆盖 SERVICE_API_KEY；会出现在 shell history/ps，优先用环境变量。"),
    ] = None,
    caller_id: Annotated[str, typer.Option("--caller-id", "--x-ai-service-caller-id", help="Caller ID。")] = "load-cli",
    allow_real_job: Annotated[bool, typer.Option("--allow-real-job", help="允许非 job_test_* job_type。")] = False,
    job_type: Annotated[str | None, typer.Option("--job-type", help="覆盖场景默认 job_type。")] = None,
    job_params_json: Annotated[
        str | None,
        typer.Option("--job-params-json", help="高风险：自定义 job_params JSON 对象；会出现在 shell history/ps，优先用文件。"),
    ] = None,
    job_params_json_file: Annotated[str | None, typer.Option("--job-params-json-file", help="自定义 job_params JSON 文件。")] = None,
    query_job_ids: Annotated[
        str | None,
        typer.Option("--query-job-ids", help="逗号分隔的 UUID job_id 列表；大量输入优先用文件。"),
    ] = None,
    query_job_ids_file: Annotated[str | None, typer.Option("--query-job-ids-file", help="每行一个 job_id 的文件。")] = None,
    users: Annotated[int | None, typer.Option("--users", "-u", min=1, help="Locust 用户并发。")] = None,
    spawn_rate: Annotated[float | None, typer.Option("--spawn-rate", "-r", min=0.1, help="每秒启动用户数。")] = None,
    run_time: Annotated[str | None, typer.Option("--time", "-t", help="持续时间，例如 60s、2m。")] = None,
    run_id: Annotated[str | None, typer.Option("--run-id", help="显式 run_id。")] = None,
    output_dir: Annotated[str, typer.Option("--output-dir", help="结果目录根路径。")] = ".run/load",
    echo_sleep_seconds: Annotated[float, typer.Option("--echo-sleep-seconds", min=0, help="job_test_echo sleep 秒数。")] = 15.0,
    echo_repeat: Annotated[int, typer.Option("--echo-repeat", min=1, help="job_test_echo repeat。")] = 1,
    workflow_mode: Annotated[str, typer.Option("--workflow-mode", help="job_test_workflow mode。")] = "group",
    workflow_sleep_seconds: Annotated[float, typer.Option("--workflow-sleep-seconds", min=0, help="workflow sleep 秒数。")] = 15.0,
    wait_min_seconds: Annotated[float | None, typer.Option("--wait-min-seconds", min=0, help="Locust 用户最小等待。")] = None,
    wait_max_seconds: Annotated[float | None, typer.Option("--wait-max-seconds", min=0, help="Locust 用户最大等待。")] = None,
    poll_interval_seconds: Annotated[float | None, typer.Option("--poll-interval-seconds", min=0.1, help="flow 轮询间隔。")] = None,
    flow_timeout_seconds: Annotated[float | None, typer.Option("--flow-timeout-seconds", min=1, help="flow 终态等待上限。")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="只生成 manifest，不执行 Locust。")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="输出 JSON；仅支持 --dry-run。")] = False,
) -> None:
    _run_command(
        scenario_key=scenario_key,
        api_url=api_url,
        env_file=env_file,
        allow_remote_api=allow_remote_api,
        service_api_key=service_api_key,
        caller_id=caller_id,
        allow_real_job=allow_real_job,
        job_type=job_type,
        job_params_json=job_params_json,
        job_params_json_file=job_params_json_file,
        query_job_ids=query_job_ids,
        query_job_ids_file=query_job_ids_file,
        users=users,
        spawn_rate=spawn_rate,
        run_time=run_time,
        run_id=run_id,
        output_dir=output_dir,
        echo_sleep_seconds=echo_sleep_seconds,
        echo_repeat=echo_repeat,
        workflow_mode=workflow_mode,
        workflow_sleep_seconds=workflow_sleep_seconds,
        wait_min_seconds=wait_min_seconds,
        wait_max_seconds=wait_max_seconds,
        poll_interval_seconds=poll_interval_seconds,
        flow_timeout_seconds=flow_timeout_seconds,
        web=False,
        web_host="127.0.0.1",
        web_port=8089,
        dry_run=dry_run,
        json_output=json_output,
    )


@app.command("ui", help="启动 Locust Web UI 并自动开始指定场景。", epilog=UI_HELP_EPILOG)
def ui(
    scenario_key: Annotated[str, typer.Argument(help="场景 key，查看 ./scripts/load.sh scenarios。")],
    api_url: Annotated[str | None, typer.Option("--api-url", help="API 基础 URL。")] = None,
    env_file: Annotated[str | None, typer.Option("--env-file", help="显式配置文件路径。")] = None,
    allow_remote_api: Annotated[bool, typer.Option("--allow-remote-api", help="允许非本机 API URL。")] = False,
    service_api_key: Annotated[
        str | None,
        typer.Option("--service-api-key", help="高风险：覆盖 SERVICE_API_KEY；会出现在 shell history/ps，优先用环境变量。"),
    ] = None,
    caller_id: Annotated[str, typer.Option("--caller-id", "--x-ai-service-caller-id", help="Caller ID。")] = "load-cli",
    allow_real_job: Annotated[bool, typer.Option("--allow-real-job", help="允许非 job_test_* job_type。")] = False,
    job_type: Annotated[str | None, typer.Option("--job-type", help="覆盖场景默认 job_type。")] = None,
    job_params_json_file: Annotated[str | None, typer.Option("--job-params-json-file", help="自定义 job_params JSON 文件。")] = None,
    query_job_ids: Annotated[
        str | None,
        typer.Option("--query-job-ids", help="逗号分隔的 UUID job_id 列表；大量输入优先用文件。"),
    ] = None,
    query_job_ids_file: Annotated[str | None, typer.Option("--query-job-ids-file", help="每行一个 UUID job_id 的文件。")] = None,
    users: Annotated[int | None, typer.Option("--users", "-u", min=1, help="Locust 用户并发。")] = None,
    spawn_rate: Annotated[float | None, typer.Option("--spawn-rate", "-r", min=0.1, help="每秒启动用户数。")] = None,
    run_time: Annotated[str | None, typer.Option("--time", "-t", help="持续时间，例如 60s、2m。")] = None,
    run_id: Annotated[str | None, typer.Option("--run-id", help="显式 run_id。")] = None,
    output_dir: Annotated[str, typer.Option("--output-dir", help="结果目录根路径。")] = ".run/load",
    echo_sleep_seconds: Annotated[float, typer.Option("--echo-sleep-seconds", min=0, help="job_test_echo sleep 秒数。")] = 15.0,
    echo_repeat: Annotated[int, typer.Option("--echo-repeat", min=1, help="job_test_echo repeat。")] = 1,
    workflow_mode: Annotated[str, typer.Option("--workflow-mode", help="job_test_workflow mode。")] = "group",
    workflow_sleep_seconds: Annotated[float, typer.Option("--workflow-sleep-seconds", min=0, help="workflow sleep 秒数。")] = 15.0,
    wait_min_seconds: Annotated[float | None, typer.Option("--wait-min-seconds", min=0, help="Locust 用户最小等待。")] = None,
    wait_max_seconds: Annotated[float | None, typer.Option("--wait-max-seconds", min=0, help="Locust 用户最大等待。")] = None,
    poll_interval_seconds: Annotated[float | None, typer.Option("--poll-interval-seconds", min=0.1, help="flow 轮询间隔。")] = None,
    flow_timeout_seconds: Annotated[float | None, typer.Option("--flow-timeout-seconds", min=1, help="flow 终态等待上限。")] = None,
    web_host: Annotated[str, typer.Option("--web-host", help="Locust UI 监听地址。")] = "127.0.0.1",
    web_port: Annotated[int, typer.Option("--web-port", min=1, max=65535, help="Locust UI 监听端口。")] = 8089,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="只生成 manifest，不执行 Locust。")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="输出 JSON；仅支持 --dry-run。")] = False,
) -> None:
    _run_command(
        scenario_key=scenario_key,
        api_url=api_url,
        env_file=env_file,
        allow_remote_api=allow_remote_api,
        service_api_key=service_api_key,
        caller_id=caller_id,
        allow_real_job=allow_real_job,
        job_type=job_type,
        job_params_json=None,
        job_params_json_file=job_params_json_file,
        query_job_ids=query_job_ids,
        query_job_ids_file=query_job_ids_file,
        users=users,
        spawn_rate=spawn_rate,
        run_time=run_time,
        run_id=run_id,
        output_dir=output_dir,
        echo_sleep_seconds=echo_sleep_seconds,
        echo_repeat=echo_repeat,
        workflow_mode=workflow_mode,
        workflow_sleep_seconds=workflow_sleep_seconds,
        wait_min_seconds=wait_min_seconds,
        wait_max_seconds=wait_max_seconds,
        poll_interval_seconds=poll_interval_seconds,
        flow_timeout_seconds=flow_timeout_seconds,
        web=True,
        web_host=web_host,
        web_port=web_port,
        dry_run=dry_run,
        json_output=json_output,
    )


@app.command("report", help="查看一次压测 run 的 manifest 和 Locust 结果文件摘要。", epilog=REPORT_HELP_EPILOG)
def report(
    run_id: Annotated[str, typer.Option("--run-id", help="压测 run_id。")],
    output_dir: Annotated[str, typer.Option("--output-dir", help="结果目录根路径。")] = ".run/load",
    json_output: Annotated[bool, typer.Option("--json", help="输出 JSON。")] = False,
) -> None:
    try:
        manifest = _read_manifest(run_id, output_dir)
        stats_path = Path(f"{manifest['paths']['csv_prefix']}_stats.csv")
        rows: list[dict[str, str]] = []
        if stats_path.is_file():
            with stats_path.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
        payload = {"manifest": manifest, "stats": rows[:20], "stats_path": str(stats_path)}
    except (LoadError, OSError, json.JSONDecodeError) as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(4) from exc
    if json_output:
        formatters.print_json(payload)
        return
    formatters.section("Load Report")
    formatters.event(
        str(manifest.get("status", "-")).upper(),
        "load",
        f"run_id={manifest.get('run_id')} scenario={manifest.get('scenario_key')}",
    )
    formatters.print_table(
        [
            {"name": "manifest", "path": manifest["paths"]["manifest"]},
            {"name": "stats", "path": str(stats_path)},
            {"name": "html", "path": manifest["paths"]["html_report"]},
        ],
        [("name", "name"), ("path", "path")],
    )
    if rows:
        formatters.section("Locust Stats")
        display_rows = [
            {
                "type": row.get("Type"),
                "name": row.get("Name"),
                "requests": row.get("Request Count"),
                "failures": row.get("Failure Count"),
                "median": row.get("Median Response Time"),
                "p95": row.get("95%"),
                "rps": row.get("Requests/s"),
            }
            for row in rows[:20]
        ]
        formatters.print_table(
            display_rows,
            [
                ("type", "type"),
                ("name", "name"),
                ("requests", "requests"),
                ("failures", "failures"),
                ("median", "median"),
                ("p95", "p95"),
                ("rps", "rps"),
            ],
        )


def _run_jobs_command(args: list[str]) -> int:
    result = subprocess.run([str(ROOT_DIR / "scripts/jobs.sh"), *args], cwd=ROOT_DIR, check=False)
    return result.returncode


@app.command("pressure", help="基于 run manifest 调用 jobs.sh pressure。", epilog=PRESSURE_HELP_EPILOG)
def pressure(
    run_id: Annotated[str, typer.Option("--run-id", help="压测 run_id。")],
    output_dir: Annotated[str, typer.Option("--output-dir", help="结果目录根路径。")] = ".run/load",
    since: Annotated[str, typer.Option("--since", help="诊断窗口。")] = "20m",
    older_than: Annotated[str, typer.Option("--older-than", help="stuck 判定窗口。")] = "1m",
    max_active_jobs: Annotated[int | None, typer.Option("--max-active-jobs", min=0, help="容量上限。")] = None,
    api_log: Annotated[str | None, typer.Option("--api-log", help="API 日志路径，透传给 jobs.sh pressure。")] = None,
    api_log_tail: Annotated[int, typer.Option("--api-log-tail", min=1, max=20000, help="扫描 API 日志末尾行数。")] = 1000,
) -> None:
    try:
        manifest = _read_manifest(run_id, output_dir)
    except (LoadError, json.JSONDecodeError) as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(4) from exc
    args = [
        "pressure",
        "--since",
        since,
        "--older-than",
        older_than,
        "--locust-prefix",
        manifest["paths"]["csv_prefix"],
    ]
    if manifest.get("job_type"):
        args.extend(["--job-type", manifest["job_type"]])
    if manifest.get("caller_id") and manifest.get("caller_id") != "-":
        args.extend(["--caller-id", manifest["caller_id"]])
    if max_active_jobs is not None:
        args.extend(["--max-active-jobs", str(max_active_jobs)])
    if api_log is not None:
        args.extend(["--api-log", api_log, "--api-log-tail", str(api_log_tail)])
    raise typer.Exit(_run_jobs_command(args))


@app.command("drain", help="基于 run manifest 调用 jobs.sh drain。", epilog=DRAIN_HELP_EPILOG)
def drain(
    run_id: Annotated[str, typer.Option("--run-id", help="压测 run_id。")],
    output_dir: Annotated[str, typer.Option("--output-dir", help="结果目录根路径。")] = ".run/load",
    since: Annotated[str, typer.Option("--since", help="检查窗口。")] = "30m",
    older_than: Annotated[str, typer.Option("--older-than", help="stuck 判定窗口。")] = "10m",
    strict: Annotated[bool, typer.Option("--strict", help="未排空时返回 exit 4。")] = False,
) -> None:
    try:
        manifest = _read_manifest(run_id, output_dir)
    except (LoadError, json.JSONDecodeError) as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(4) from exc
    args = ["drain", "--since", since, "--older-than", older_than]
    if manifest.get("job_type"):
        args.extend(["--job-type", manifest["job_type"]])
    if manifest.get("caller_id") and manifest.get("caller_id") != "-":
        args.extend(["--caller-id", manifest["caller_id"]])
    if strict:
        args.append("--strict")
    raise typer.Exit(_run_jobs_command(args))


if __name__ == "__main__":
    app(prog_name="./scripts/load.sh")
