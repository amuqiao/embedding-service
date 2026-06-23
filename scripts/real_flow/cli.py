from __future__ import annotations

from typing import Annotated

import typer

from scripts.real_flow.flows import llm_job_billing


HELP_EPILOG = """\b
作用域：
  手动验证真实业务流程。命令会调用本地 API、创建真实 Job、等待 worker 执行，并查询结果证据。
  本入口允许真实 LLM 调用，可能产生费用；不会被 ./scripts/verify.sh check 默认执行。

\b
命令说明：
  llm-job-billing   创建 job_real_llm_echo，触发真实 LLM，并查询 /jobs/{job_id}/billing。
  llm-job-double-billing   创建 job_real_llm_double_echo，触发两次真实 LLM，并查询汇总 billing。

\b
环境变量：
  scripts/.env: API_HOST / API_PORT 用于定位本地 API。
  .env: SERVICE_API_PREFIX / SERVICE_API_KEY / DISABLE_HTTP_AUTH_HEADER / DISABLE_CALLER_ID_HEADER / DEFAULT_MODEL_ID。

\b
常用示例：
  ./scripts/real-flow.sh llm-job-billing --confirm-cost --model-id gpt-5.4-mini
  ./scripts/real-flow.sh llm-job-billing --confirm-cost --input-text "用一句话回复：计费验证成功" --json
  ./scripts/real-flow.sh llm-job-double-billing --confirm-cost --model-id gpt-5.4-mini --json

\b
保护边界：
  必须显式传入 --confirm-cost。
  只通过公开 HTTP API 创建 Job、轮询状态和查询 billing，不直接改数据库，不重试历史 Job。

\b
Exit Codes:
  0  成功
  2  参数或本地配置错误
  4  真实流程失败、Job 失败或证据不可达
"""

app = typer.Typer(
    name="real-flow.sh",
    help="真实业务流程验证入口。",
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


@app.command("llm-job-billing", help="真实调用 LLM，并查询 Job billing。")
def llm_job_billing_command(
    confirm_cost: Annotated[
        bool,
        typer.Option("--confirm-cost", help="确认本命令会真实调用 LLM 并可能产生费用。"),
    ] = False,
    api_url: Annotated[
        str | None,
        typer.Option("--api-url", help="本地 API 基础 URL；默认从 scripts/.env 的 API_HOST/API_PORT 推导。"),
    ] = None,
    model_id: Annotated[
        str | None,
        typer.Option("--model-id", help="模型 ID；默认使用 DEFAULT_MODEL_ID。"),
    ] = None,
    input_text: Annotated[
        str,
        typer.Option("--input-text", help="传给真实 LLM Job 的输入文本。"),
    ] = "用一句话回复：真实 LLM 计费验证成功。",
    instruction: Annotated[
        str,
        typer.Option("--instruction", help="传给验证 Job 的指令。"),
    ] = "用一句话确认真实 LLM 计费链路可用。",
    caller_id: Annotated[
        str,
        typer.Option("--caller-id", help="X-AI-Service-Caller-ID。"),
    ] = "real-flow-cli",
    timeout_seconds: Annotated[
        int,
        typer.Option("--timeout-seconds", min=1, help="等待 Job 到达终态的最长秒数。"),
    ] = 180,
    poll_interval_seconds: Annotated[
        float,
        typer.Option("--poll-interval-seconds", min=0.1, help="轮询 Job 状态的间隔秒数。"),
    ] = 1.0,
    client_request_id: Annotated[
        str | None,
        typer.Option("--client-request-id", help="显式 client_request_id；默认自动生成。"),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="输出 summary 和原始 HTTP envelope responses。"),
    ] = False,
) -> None:
    try:
        llm_job_billing.run(
            confirm_cost=confirm_cost,
            job_type="job_real_llm_echo",
            api_url=api_url,
            model_id=model_id,
            input_text=input_text,
            instruction=instruction,
            second_instruction=None,
            caller_id=caller_id,
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
            client_request_id=client_request_id,
            json_output=json_output,
        )
    except llm_job_billing.FlowError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(exc.exit_code) from exc


@app.command("llm-job-double-billing", help="真实调用两次 LLM，并查询同一 Job 的汇总 billing。")
def llm_job_double_billing_command(
    confirm_cost: Annotated[
        bool,
        typer.Option("--confirm-cost", help="确认本命令会真实调用两次 LLM 并可能产生费用。"),
    ] = False,
    api_url: Annotated[
        str | None,
        typer.Option("--api-url", help="本地 API 基础 URL；默认从 scripts/.env 的 API_HOST/API_PORT 推导。"),
    ] = None,
    model_id: Annotated[
        str | None,
        typer.Option("--model-id", help="模型 ID；默认使用 DEFAULT_MODEL_ID。"),
    ] = None,
    input_text: Annotated[
        str,
        typer.Option("--input-text", help="传给真实 LLM Job 的输入文本。"),
    ] = "用一句话回复：两次计费验证成功。",
    first_instruction: Annotated[
        str,
        typer.Option("--first-instruction", help="第一次真实 LLM 调用的指令。"),
    ] = "第一次调用：用一句话确认真实 LLM 计费链路可用。",
    second_instruction: Annotated[
        str,
        typer.Option("--second-instruction", help="第二次真实 LLM 调用的指令。"),
    ] = "第二次调用：用另一句话确认同一 Job 的多次 LLM 计费可汇总。",
    caller_id: Annotated[
        str,
        typer.Option("--caller-id", help="X-AI-Service-Caller-ID。"),
    ] = "real-flow-cli",
    timeout_seconds: Annotated[
        int,
        typer.Option("--timeout-seconds", min=1, help="等待 Job 到达终态的最长秒数。"),
    ] = 240,
    poll_interval_seconds: Annotated[
        float,
        typer.Option("--poll-interval-seconds", min=0.1, help="轮询 Job 状态的间隔秒数。"),
    ] = 1.0,
    client_request_id: Annotated[
        str | None,
        typer.Option("--client-request-id", help="显式 client_request_id；默认自动生成。"),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="输出 summary 和原始 HTTP envelope responses。"),
    ] = False,
) -> None:
    try:
        llm_job_billing.run(
            confirm_cost=confirm_cost,
            job_type="job_real_llm_double_echo",
            api_url=api_url,
            model_id=model_id,
            input_text=input_text,
            instruction=first_instruction,
            second_instruction=second_instruction,
            caller_id=caller_id,
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
            client_request_id=client_request_id,
            json_output=json_output,
        )
    except llm_job_billing.FlowError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(exc.exit_code) from exc


if __name__ == "__main__":
    app()
