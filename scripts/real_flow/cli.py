from __future__ import annotations

from typing import Annotated

import typer

from scripts.real_flow.flows import llm_job_billing, oss_image_upload, poster_title_image


HELP_EPILOG = """\b
作用域：
  手动验证真实业务流程。命令会调用本地 API、创建真实 Job、等待 worker 执行，并查询结果证据。
  本入口允许真实 LLM 调用，可能产生费用；不会被 ./scripts/verify.sh check 默认执行。

\b
命令说明：
  llm-job-billing   创建 job_real_llm_echo，触发真实 LLM，并查询 /jobs/{job_id}/billing。
  llm-job-double-billing   创建 job_real_llm_double_echo，触发两次真实 LLM，并查询汇总 billing。
  oss-upload-image  上传本地图片到阿里云 OSS，返回 URL Ref。
  poster-title-image   创建 poster_title_image，触发真实 gpt-image-2 标题图生成，并查询结果和 billing。

\b
环境变量：
  scripts/.env: API_HOST / API_PORT 用于定位本地 API。
  .env: SERVICE_API_PREFIX / SERVICE_API_KEY / DISABLE_HTTP_AUTH_HEADER / DISABLE_CALLER_ID_HEADER / DEFAULT_MODEL_ID。

\b
常用示例：
  ./scripts/real-flow.sh llm-job-billing --confirm-cost --model-id gpt-5.4-mini
  ./scripts/real-flow.sh llm-job-billing --confirm-cost --input-text "用一句话回复：计费验证成功" --json
  ./scripts/real-flow.sh llm-job-double-billing --confirm-cost --model-id gpt-5.4-mini --json
  ./scripts/real-flow.sh oss-upload-image --confirm-upload --image .data/title/英语.png --signed-url-expires-seconds 3600 --json
  ./scripts/real-flow.sh poster-title-image --confirm-cost --model-id gpt-image-2 --language es --title-text "Cuando el amor se alejo" --json
  ./scripts/real-flow.sh poster-title-image --confirm-cost --language es --title-text "Cuando el amor se alejo" --download-outputs --json

\b
poster-title-image 输出下载：
  --download-outputs 会下载 Job 结果里的全部 job_result.items[].images[]，不是只下载第一张。
  默认保存目录是 .data/real-flow/poster-title-image。
  默认文件结构是 .data/real-flow/poster-title-image/<job_id>/<item_id>-<language>/<序号>-<原文件名>。
  可用 --output-dir 指定目录，例如 --output-dir .data/poster-title-output。
  STORAGE_BACKEND=local 时从本地对象存储复制；aliyun_oss 时先尝试 public_url，403 等失败后用本机 .env 的 OSS 凭证生成临时 signed URL 下载。
  下载后会校验 sha256；--json 输出会在 summary.artifacts[] 返回 local_path、download_method 和 sha256_verified。

\b
保护边界：
  必须显式传入 --confirm-cost。
  oss-upload-image 必须显式传入 --confirm-upload。
  poster-title-image 在 STORAGE_BACKEND=aliyun_oss 且使用本地参考图时也必须传入 --confirm-upload。
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


@app.command("oss-upload-image", help="上传本地图片到阿里云 OSS，并输出 URL Ref。")
def oss_upload_image_command(
    confirm_upload: Annotated[
        bool,
        typer.Option("--confirm-upload", help="确认本命令会上传文件到阿里云 OSS。"),
    ] = False,
    image: Annotated[
        str,
        typer.Option("--image", help="需要上传的本地图片路径。"),
    ] = poster_title_image.DEFAULT_REFERENCE_IMAGE,
    content_type: Annotated[
        str | None,
        typer.Option("--content-type", help="图片 MIME type；默认按扩展名推断。"),
    ] = None,
    key: Annotated[
        str | None,
        typer.Option("--key", help="显式 OSS object key；默认按 OSS_OUTPUT_PREFIX 和时间戳生成。"),
    ] = None,
    key_prefix: Annotated[
        str | None,
        typer.Option("--key-prefix", help="默认 key 的业务前缀。"),
    ] = None,
    signed_url_expires_seconds: Annotated[
        int,
        typer.Option("--signed-url-expires-seconds", min=1, help="返回的临时 signed_url 有效秒数。"),
    ] = 3600,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="输出机器可读上传结果和 URL Ref。"),
    ] = False,
) -> None:
    try:
        oss_image_upload.run(
            confirm_upload=confirm_upload,
            image=image,
            content_type=content_type,
            key=key,
            key_prefix=key_prefix,
            signed_url_expires_seconds=signed_url_expires_seconds,
            json_output=json_output,
        )
    except oss_image_upload.FlowError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(exc.exit_code) from exc


@app.command("poster-title-image", help="真实调用 gpt-image-2 生成透明海报标题图，并查询 Job billing。")
def poster_title_image_command(
    confirm_cost: Annotated[
        bool,
        typer.Option("--confirm-cost", help="确认本命令会真实调用 gpt-image-2 并可能产生费用。"),
    ] = False,
    confirm_upload: Annotated[
        bool,
        typer.Option("--confirm-upload", help="确认 STORAGE_BACKEND=aliyun_oss 时会上传本地参考图到阿里云 OSS。"),
    ] = False,
    api_url: Annotated[
        str | None,
        typer.Option("--api-url", help="本地 API 基础 URL；默认从 scripts/.env 的 API_HOST/API_PORT 推导。"),
    ] = None,
    reference_image: Annotated[
        str,
        typer.Option("--reference-image", help="本地参考标题图；STORAGE_BACKEND=local 写入本地对象存储，aliyun_oss 上传到阿里云 OSS。"),
    ] = poster_title_image.DEFAULT_REFERENCE_IMAGE,
    reference_public_url: Annotated[
        str | None,
        typer.Option("--reference-public-url", help="已有 OSS URL Ref 的 public_url；传入后不会 stage 本地文件。"),
    ] = None,
    reference_internal_url: Annotated[
        str | None,
        typer.Option("--reference-internal-url", help="已有 OSS URL Ref 的 internal_url；需与 public_url 指向同一对象。"),
    ] = None,
    reference_sha256: Annotated[
        str | None,
        typer.Option("--reference-sha256", help="已有参考图 OSS object 的 64 位小写 SHA-256。"),
    ] = None,
    reference_content_type: Annotated[
        str | None,
        typer.Option("--reference-content-type", help="参考图 MIME type；本地文件默认按扩展名推断。"),
    ] = None,
    model_id: Annotated[
        str,
        typer.Option("--model-id", help="图片生成模型 ID；默认 gpt-image-2。"),
    ] = poster_title_image.DEFAULT_IMAGE_MODEL_ID,
    item_id: Annotated[
        str,
        typer.Option("--item-id", help="poster_title_image item_id。"),
    ] = "es",
    language: Annotated[
        str,
        typer.Option("--language", help="目标标题语言代码。"),
    ] = "es",
    title_text: Annotated[
        str,
        typer.Option("--title-text", help="需要渲染的目标标题文本。"),
    ] = "Cuando el amor se alejo",
    size: Annotated[
        str,
        typer.Option("--size", help="gpt-image-2 图片尺寸。"),
    ] = "auto",
    quality: Annotated[
        str,
        typer.Option("--quality", help="gpt-image-2 图片质量。"),
    ] = "high",
    draw_count: Annotated[
        int,
        typer.Option("--draw-count", min=1, max=4, help="生成张数。"),
    ] = 1,
    style_probe: Annotated[
        str | None,
        typer.Option("--style-probe", help="覆盖 style_probe prompt。"),
    ] = None,
    additional_prompt: Annotated[
        str | None,
        typer.Option("--additional-prompt", help="覆盖 additional_prompt。"),
    ] = None,
    layout_rules: Annotated[
        str | None,
        typer.Option("--layout-rules", help="覆盖 layout_rules。"),
    ] = None,
    caller_id: Annotated[
        str,
        typer.Option("--caller-id", help="X-AI-Service-Caller-ID。"),
    ] = "real-flow-cli",
    timeout_seconds: Annotated[
        int,
        typer.Option("--timeout-seconds", min=1, help="等待 Job 到达终态的最长秒数。"),
    ] = 900,
    poll_interval_seconds: Annotated[
        float,
        typer.Option("--poll-interval-seconds", min=0.1, help="轮询 Job 状态的间隔秒数。"),
    ] = 2.0,
    client_request_id: Annotated[
        str | None,
        typer.Option("--client-request-id", help="显式 client_request_id；默认自动生成。"),
    ] = None,
    download_outputs: Annotated[
        bool,
        typer.Option("--download-outputs", help="下载 Job 结果里的全部输出图片到本地目录，并校验 sha256。"),
    ] = False,
    output_dir: Annotated[
        str,
        typer.Option("--output-dir", help="--download-outputs 的本地保存目录；默认 .data/real-flow/poster-title-image。"),
    ] = poster_title_image.DEFAULT_OUTPUT_DIR,
    signed_url_expires_seconds: Annotated[
        int,
        typer.Option("--signed-url-expires-seconds", min=1, help="public_url 不可读时生成临时 signed URL 的有效秒数。"),
    ] = 3600,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="输出 summary 和原始 HTTP envelope responses。"),
    ] = False,
) -> None:
    try:
        poster_title_image.run(
            confirm_cost=confirm_cost,
            confirm_upload=confirm_upload,
            api_url=api_url,
            reference_image=reference_image,
            reference_public_url=reference_public_url,
            reference_internal_url=reference_internal_url,
            reference_sha256=reference_sha256,
            reference_content_type=reference_content_type,
            model_id=model_id,
            item_id=item_id,
            language=language,
            title_text=title_text,
            size=size,
            quality=quality,
            draw_count=draw_count,
            style_probe=style_probe,
            additional_prompt=additional_prompt,
            layout_rules=layout_rules,
            caller_id=caller_id,
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
            client_request_id=client_request_id,
            json_output=json_output,
            download_outputs=download_outputs,
            output_dir=output_dir,
            signed_url_expires_seconds=signed_url_expires_seconds,
        )
    except poster_title_image.FlowError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(exc.exit_code) from exc


if __name__ == "__main__":
    app()
