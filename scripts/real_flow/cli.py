from __future__ import annotations

from typing import Annotated

import typer

from scripts.real_flow.flows import adapter_image_probe, llm_job_billing, oss_image_upload, poster_title_image


POSTER_TITLE_IMAGE_HELP_EPILOG = """\b
常用示例：

\b
  # 单 item：使用本地透明 PNG 参考图，脚本自动转成 API reference_image URL Ref。
  ./scripts/real-flow.sh poster-title-image \\
    --confirm-cost \\
    --reference .data/title/英语.png \\
    --language es \\
    --title-text "Cuando el amor se alejo" \\
    --json

\b
  # 单 item：生成后下载全部输出图，并校验 sha256 与透明背景。
  ./scripts/real-flow.sh poster-title-image \\
    --confirm-cost \\
    --reference .data/title/英语.png \\
    --language es \\
    --title-text "Cuando el amor se alejo" \\
    --download-outputs \\
    --json

\b
  # 多 item：每个 item 在 JSON 中指定 language/title_text/reference。
  ./scripts/real-flow.sh poster-title-image \\
    --confirm-cost \\
    --items-json .data/title/poster-items.json \\
    --download-outputs \\
    --json

\b
  # 推荐：先上传参考图得到 URL Ref JSON，再用 URL Ref 创建 Job。
  mkdir -p .run

\b
  ./scripts/real-flow.sh oss-upload-image \\
    --env-file env_test/.env \\
    --confirm-upload \\
    --image .data/title/英语.png \\
    --json-ref-only > .run/reference-image.json

\b
  ./scripts/real-flow.sh poster-title-image \\
    --confirm-cost \\
    --reference-url-ref-json .run/reference-image.json \\
    --language es \\
    --title-text "Cuando el amor se alejo" \\
    --json

\b
  # 已有 OSS URL Ref：不 stage 本地图片，也可以直接传四字段。
  ./scripts/real-flow.sh poster-title-image \\
    --confirm-cost \\
    --language es \\
    --title-text "Cuando el amor se alejo" \\
    --reference-public-url "https://bucket.oss-region.aliyuncs.com/path/title.png" \\
    --reference-internal-url "https://bucket.oss-region-internal.aliyuncs.com/path/title.png" \\
    --reference-content-type image/png \\
    --reference-sha256 "<64位小写sha256>" \\
    --json

\b
  # STORAGE_BACKEND=aliyun_oss 且传本地参考图时，需要显式确认上传。
  ./scripts/real-flow.sh poster-title-image \\
    --confirm-cost \\
    --confirm-upload \\
    --reference .data/title/英语.png \\
    --language es \\
    --title-text "Cuando el amor se alejo" \\
    --download-outputs \\
    --json

\b
  # 远端测试环境：必须显式允许远端 API；SERVICE_API_KEY 优先从 --env-file 或运行时环境读取。
  ./scripts/real-flow.sh poster-title-image \\
    --allow-remote-api \\
    --env-file env_test/.env \\
    --api-url http://test-cms-poster-title.epubgame.com \\
    --x-ai-service-caller-id default \\
    --confirm-cost \\
    --confirm-upload \\
    --reference .data/title/英语.png \\
    --language es \\
    --title-text "Cuando el amor se alejo" \\
    --download-outputs \\
    --json

\b
items-json 最小格式：
  {
    "items": [
      {
        "item_id": "es",
        "language": "es",
        "title_text": "Cuando el amor se alejo",
        "reference": {
          "image": ".data/title/英语.png",
          "content_type": "image/png"
        }
      }
    ]
  }

\b
参考图要求：
  必须是透明背景 PNG 标题图层，不是完整海报图。
  最大 20 MB，最大 4096x4096，总像素不超过 16777216。
  STORAGE_BACKEND=local 时，本地图片会写入 LOCAL_OBJECT_STORAGE_PATH 并生成 reference_image 四字段。
  STORAGE_BACKEND=aliyun_oss 时，本地图片会上传到 OSS；也可以直接传 --reference-public-url / --reference-internal-url / --reference-content-type / --reference-sha256。

\b
语种与输出：
  poster_title_image 语种必须来自 docs/api/业务语种规范.md 的共享业务语种列表。
  同一 Job 内 item_id 必须唯一；language 允许重复；不传 --model-id 时使用服务端 poster_title_image 默认生图模型。
  --download-outputs 默认保存到 .data/real-flow/poster-title-image/<job_id>/<item_id>-<language>/。
"""

DOCTOR_HELP_EPILOG = """\b
常用示例：
  ./scripts/real-flow.sh doctor
  ./scripts/real-flow.sh doctor --json

\b
  ./scripts/real-flow.sh doctor \\
    --env-file env_test/.env \\
    --allow-remote-api \\
    --api-url http://test-cms-poster-title.epubgame.com \\
    --json
"""

LLM_JOB_BILLING_HELP_EPILOG = """\b
常用示例：
  ./scripts/real-flow.sh llm-job-billing --confirm-cost --model-id gpt-5.4-mini
  ./scripts/real-flow.sh llm-job-billing --confirm-cost --input-text "用一句话回复：计费验证成功" --json

\b
  ./scripts/real-flow.sh llm-job-billing \\
    --allow-remote-api \\
    --env-file env_test/.env \\
    --api-url http://test-cms-poster-title.epubgame.com \\
    --confirm-cost \\
    --json
"""

LLM_JOB_DOUBLE_BILLING_HELP_EPILOG = """\b
常用示例：
  ./scripts/real-flow.sh llm-job-double-billing --confirm-cost --model-id gpt-5.4-mini
  ./scripts/real-flow.sh llm-job-double-billing --confirm-cost --model-id gpt-5.4-mini --json

\b
  ./scripts/real-flow.sh llm-job-double-billing \\
    --allow-remote-api \\
    --env-file env_test/.env \\
    --api-url http://test-cms-poster-title.epubgame.com \\
    --confirm-cost \\
    --json
"""

OSS_UPLOAD_IMAGE_HELP_EPILOG = """\b
常用示例：
  ./scripts/real-flow.sh oss-upload-image \\
    --confirm-upload \\
    --image .data/title/英语.png \\
    --signed-url-expires-seconds 3600 \\
    --json

\b
  mkdir -p .run

\b
  ./scripts/real-flow.sh oss-upload-image \\
    --confirm-upload \\
    --image .data/title/英语.png \\
    --json-ref-only > .run/reference-image.json

\b
  ./scripts/real-flow.sh oss-upload-image --confirm-upload --image .data/title/英语.png --emit-poster-args
"""

ADAPTER_IMAGE_PROBE_HELP_EPILOG = """\b
常用示例：
  ./scripts/real-flow.sh adapter-image-probe \\
    --confirm-cost \\
    --models-config app/jobs/types/poster_title_image/models.yaml \\
    --json

\b
  ./scripts/real-flow.sh adapter-image-probe \\
    --confirm-cost \\
    --models-config app/jobs/types/poster_title_image/models.yaml \\
    --prompt "Generate a simple transparent title image saying Hola" \\
    --size 1024x1024 \\
    --quality low \\
    --json

\b
  ./scripts/real-flow.sh adapter-image-probe \\
    --confirm-cost \\
    --reference .data/title/英语.png \\
    --reference-content-type image/png \\
    --json

\b
用途：
  直接调用本仓库封装的 openai_images 与 openai_responses 两个 image adapter。
  每次执行都会调用两个 adapter；generation.image_adapter 指定的 adapter 会排第一，另一个 adapter 排第二。
  默认从 poster_title_image models.yaml 读取 generation.image_adapter、default_model_id 和 style_probe model；可用 --models-config 指定配置文件。
  输出 adapter 返回的 usage（SDK 返回时包含 provider_usage）、revised_prompt、图片数量，以及每张图片的 sha256 和 size_bytes。
  不经过服务 HTTP Job，不查询 billing，不打印图片二进制。
"""


HELP_EPILOG = f"""\b
作用域：
  手动验证真实业务流程。Job 类命令会调用本地 API、创建真实 Job、等待 worker 执行，并查询结果证据。
  adapter-image-probe 会直接调用本仓库封装的 provider adapter，不经过本地 API/worker。
  本入口允许真实 LLM 调用，可能产生费用；不会被 ./scripts/verify.sh check 默认执行。

\b
配置与环境变量：
  .env: API_HOST / API_PORT / SERVICE_API_PREFIX / SERVICE_API_KEY / DISABLE_HTTP_AUTH_HEADER / DISABLE_CALLER_ID_HEADER / DEFAULT_MODEL_ID。
  --env-file 可以显式指定配置文件路径；运行时环境变量仍优先于 env 文件。

\b
输出：
  默认输出真实流程摘要和关键证据。
  --json 输出 summary 和原始 HTTP envelope responses，stdout 只包含 JSON。
  错误原因输出到 stderr。

\b
常用示例：
  ./scripts/real-flow.sh doctor
  ./scripts/real-flow.sh llm-job-billing --confirm-cost --model-id gpt-5.4-mini
  ./scripts/real-flow.sh llm-job-double-billing --confirm-cost --model-id gpt-5.4-mini
  ./scripts/real-flow.sh oss-upload-image --confirm-upload --image .data/title/英语.png
  ./scripts/real-flow.sh adapter-image-probe --confirm-cost --json
  ./scripts/real-flow.sh poster-title-image --confirm-cost --reference .data/title/英语.png --language es --title-text "Cuando el amor se alejo" --json

\b
进阶用法：
  各子命令的参数组合、确认参数、远端环境和 JSON 输出示例请查看：
  ./scripts/real-flow.sh <command> -h
  poster-title-image 的本地参考图、多 item JSON、OSS URL Ref 和输出下载示例请查看：
  ./scripts/real-flow.sh poster-title-image -h

\b
副作用与保护边界：
  真实模型 Job 命令必须显式传入 --confirm-cost。
  adapter-image-probe 必须显式传入 --confirm-cost，会直接调用真实 OpenAI image adapter。
  oss-upload-image 必须显式传入 --confirm-upload。
  poster-title-image 在 STORAGE_BACKEND=aliyun_oss 且使用本地参考图时也必须传入 --confirm-upload。
  非本机 --api-url 必须显式传入 --allow-remote-api。
  远端测试 OSS 配置优先通过 --env-file 指向测试环境配置文件。
  --service-api-key 会出现在 shell history 和进程参数中；共享机器或 CI 优先通过 SERVICE_API_KEY 环境变量注入。
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


@app.command("doctor", help="只解析 real-flow 上下文，不上传、不提交 Job、不产生费用。", epilog=DOCTOR_HELP_EPILOG)
def doctor_command(
    api_url: Annotated[
        str | None,
        typer.Option("--api-url", help="API 基础 URL；默认从 env 文件的 API_URL 或 API_HOST/API_PORT 推导。"),
    ] = None,
    env_file: Annotated[
        str | None,
        typer.Option("--env-file", help="显式配置文件路径；默认读取仓库根目录 .env，运行时环境变量优先。"),
    ] = None,
    allow_remote_api: Annotated[
        bool,
        typer.Option("--allow-remote-api", help="允许解析非本机 API URL；doctor 不会发 HTTP 请求。"),
    ] = False,
    service_api_key: Annotated[
        str | None,
        typer.Option("--service-api-key", help="仅用于判断鉴权来源；doctor 不会打印 token。"),
    ] = None,
    caller_id: Annotated[
        str,
        typer.Option("--caller-id", "--x-ai-service-caller-id", help="X-AI-Service-Caller-ID。"),
    ] = "real-flow-cli",
    json_output: Annotated[
        bool,
        typer.Option("--json", help="输出机器可读上下文。"),
    ] = False,
) -> None:
    try:
        context = llm_job_billing.resolve_runtime_context(
            env_file=env_file,
            api_url=api_url,
            allow_remote_api=allow_remote_api,
            caller_id=caller_id,
            service_api_key=service_api_key,
        )
    except llm_job_billing.FlowError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(exc.exit_code) from exc
    if json_output:
        from scripts.jobs import formatters

        formatters.print_json(context.summary)
        if not context.summary["ready"]:
            raise typer.Exit(2)
        return
    typer.echo("Real Flow Context")
    for key, value in context.summary.items():
        typer.echo(f"{key}={value}")
    if not context.summary["ready"]:
        raise typer.Exit(2)


@app.command("llm-job-billing", help="真实调用 LLM，并查询 Job billing。", epilog=LLM_JOB_BILLING_HELP_EPILOG)
def llm_job_billing_command(
    confirm_cost: Annotated[
        bool,
        typer.Option("--confirm-cost", help="确认本命令会真实调用 LLM 并可能产生费用。"),
    ] = False,
    api_url: Annotated[
        str | None,
        typer.Option("--api-url", help="API 基础 URL；默认从 .env 的 API_HOST/API_PORT 推导，远端 URL 必须配合 --allow-remote-api。"),
    ] = None,
    env_file: Annotated[
        str | None,
        typer.Option("--env-file", help="显式配置文件路径；默认读取仓库根目录 .env，运行时环境变量优先。"),
    ] = None,
    allow_remote_api: Annotated[
        bool,
        typer.Option("--allow-remote-api", help="允许 --api-url 或 API_URL 指向非本机地址；用于显式测试远端环境。"),
    ] = False,
    service_api_key: Annotated[
        str | None,
        typer.Option("--service-api-key", help="覆盖 SERVICE_API_KEY，作为 Authorization: Bearer token 发送。"),
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
        typer.Option("--caller-id", "--x-ai-service-caller-id", help="X-AI-Service-Caller-ID。"),
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
            allow_remote_api=allow_remote_api,
            service_api_key=service_api_key,
            env_file=env_file,
        )
    except llm_job_billing.FlowError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(exc.exit_code) from exc


@app.command(
    "llm-job-double-billing",
    help="真实调用两次 LLM，并查询同一 Job 的汇总 billing。",
    epilog=LLM_JOB_DOUBLE_BILLING_HELP_EPILOG,
)
def llm_job_double_billing_command(
    confirm_cost: Annotated[
        bool,
        typer.Option("--confirm-cost", help="确认本命令会真实调用两次 LLM 并可能产生费用。"),
    ] = False,
    api_url: Annotated[
        str | None,
        typer.Option("--api-url", help="API 基础 URL；默认从 .env 的 API_HOST/API_PORT 推导，远端 URL 必须配合 --allow-remote-api。"),
    ] = None,
    env_file: Annotated[
        str | None,
        typer.Option("--env-file", help="显式配置文件路径；默认读取仓库根目录 .env，运行时环境变量优先。"),
    ] = None,
    allow_remote_api: Annotated[
        bool,
        typer.Option("--allow-remote-api", help="允许 --api-url 或 API_URL 指向非本机地址；用于显式测试远端环境。"),
    ] = False,
    service_api_key: Annotated[
        str | None,
        typer.Option("--service-api-key", help="覆盖 SERVICE_API_KEY，作为 Authorization: Bearer token 发送。"),
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
        typer.Option("--caller-id", "--x-ai-service-caller-id", help="X-AI-Service-Caller-ID。"),
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
            allow_remote_api=allow_remote_api,
            service_api_key=service_api_key,
            env_file=env_file,
        )
    except llm_job_billing.FlowError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(exc.exit_code) from exc


@app.command("oss-upload-image", help="上传本地图片到阿里云 OSS，并输出 URL Ref。", epilog=OSS_UPLOAD_IMAGE_HELP_EPILOG)
def oss_upload_image_command(
    confirm_upload: Annotated[
        bool,
        typer.Option("--confirm-upload", help="确认本命令会上传文件到阿里云 OSS。"),
    ] = False,
    env_file: Annotated[
        str | None,
        typer.Option("--env-file", help="显式配置文件路径；默认读取仓库根目录 .env，运行时环境变量优先。"),
    ] = None,
    image: Annotated[
        str | None,
        typer.Option("--image", help="需要上传的本地图片路径；必须显式传入。"),
    ] = None,
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
    json_ref_only: Annotated[
        bool,
        typer.Option("--json-ref-only", help="只输出 reference_image 可直接使用的 URL Ref JSON。"),
    ] = False,
    emit_poster_args: Annotated[
        bool,
        typer.Option("--emit-poster-args", help="输出可复制到 poster-title-image 的 --reference-* 参数。"),
    ] = False,
) -> None:
    try:
        if image is None:
            raise oss_image_upload.FlowError("OSS image upload requires --image", exit_code=2)
        output_flags = sum([json_output, json_ref_only, emit_poster_args])
        if output_flags > 1:
            raise oss_image_upload.FlowError("--json, --json-ref-only and --emit-poster-args are mutually exclusive", exit_code=2)
        oss_image_upload.run(
            confirm_upload=confirm_upload,
            image=image,
            content_type=content_type,
            key=key,
            key_prefix=key_prefix,
            signed_url_expires_seconds=signed_url_expires_seconds,
            output_mode="url-ref-json" if json_ref_only else "poster-args" if emit_poster_args else "json" if json_output else "table",
            env_file=env_file,
        )
    except oss_image_upload.FlowError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(exc.exit_code) from exc


@app.command(
    "adapter-image-probe",
    help="直接调用 openai_images 与 openai_responses 两个 image adapter，并打印 adapter 返回摘要。",
    epilog=ADAPTER_IMAGE_PROBE_HELP_EPILOG,
)
def adapter_image_probe_command(
    confirm_cost: Annotated[
        bool,
        typer.Option("--confirm-cost", help="确认本命令会直接调用真实 OpenAI image adapter 并可能产生费用。"),
    ] = False,
    env_file: Annotated[
        str | None,
        typer.Option("--env-file", help="显式配置文件路径；默认读取仓库根目录 .env，运行时环境变量优先。"),
    ] = None,
    models_config: Annotated[
        str | None,
        typer.Option("--models-config", help="poster_title_image models.yaml 路径；默认使用内置配置。"),
    ] = None,
    prompt: Annotated[
        str,
        typer.Option("--prompt", help="传给两个 image adapter 的 prompt。"),
    ] = "Generate a simple transparent title image with the text Hola.",
    reference_image: Annotated[
        str | None,
        typer.Option("--reference", "--reference-image", help="可选本地参考图；传入后两个 adapter 都走 edit 路径。"),
    ] = None,
    reference_content_type: Annotated[
        str | None,
        typer.Option("--reference-content-type", help="参考图 MIME type；默认按扩展名推断。"),
    ] = None,
    provider_model: Annotated[
        str | None,
        typer.Option("--provider-model", help="覆盖图片生成 provider model；默认读取 models.yaml public_model_selection.default_model_id。"),
    ] = None,
    response_model: Annotated[
        str | None,
        typer.Option("--response-model", help="覆盖 openai_responses adapter 的 response model；默认读取 models.yaml internal_models.style_probe.model_id。"),
    ] = None,
    size: Annotated[
        str,
        typer.Option("--size", help="图片尺寸。"),
    ] = "1024x1024",
    quality: Annotated[
        str,
        typer.Option("--quality", help="图片质量。"),
    ] = "low",
    background: Annotated[
        str,
        typer.Option("--background", help="背景参数。"),
    ] = "auto",
    output_format: Annotated[
        str,
        typer.Option("--output-format", help="输出格式。"),
    ] = "png",
    timeout_seconds: Annotated[
        int | None,
        typer.Option("--timeout-seconds", min=1, help="模型调用超时；默认读取 MODEL_CALL_TIMEOUT_SECONDS 或 300。"),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="输出机器可读 JSON。"),
    ] = False,
) -> None:
    try:
        adapter_image_probe.run(
            confirm_cost=confirm_cost,
            env_file=env_file,
            models_config=models_config,
            prompt=prompt,
            reference_image=reference_image,
            reference_content_type=reference_content_type,
            provider_model=provider_model,
            response_model=response_model,
            size=size,
            quality=quality,
            background=background,
            output_format=output_format,
            timeout_seconds=timeout_seconds,
            json_output=json_output,
        )
    except adapter_image_probe.FlowError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(exc.exit_code) from exc


@app.command(
    "poster-title-image",
    help="真实调用 gpt-image-2 生成透明海报标题图，并查询 Job billing。",
    epilog=POSTER_TITLE_IMAGE_HELP_EPILOG,
)
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
        typer.Option("--api-url", help="API 基础 URL；默认从 .env 的 API_HOST/API_PORT 推导，远端 URL 必须配合 --allow-remote-api。"),
    ] = None,
    env_file: Annotated[
        str | None,
        typer.Option("--env-file", help="显式配置文件路径；默认读取仓库根目录 .env，运行时环境变量优先。"),
    ] = None,
    allow_remote_api: Annotated[
        bool,
        typer.Option("--allow-remote-api", help="允许 --api-url 或 API_URL 指向非本机地址；用于显式测试远端环境。"),
    ] = False,
    service_api_key: Annotated[
        str | None,
        typer.Option("--service-api-key", help="覆盖 SERVICE_API_KEY，作为 Authorization: Bearer token 发送。"),
    ] = None,
    reference_image: Annotated[
        str | None,
        typer.Option(
            "--reference",
            "--reference-image",
            help="本地参考标题图；必须显式传入，除非使用 --items-json 或完整 OSS URL Ref。",
        ),
    ] = None,
    items_json: Annotated[
        str | None,
        typer.Option("--items-json", help="多 item JSON 文件；支持每个 item 指定 language/title_text/reference。传入后忽略单 item 参考图与文案参数。"),
    ] = None,
    reference_public_url: Annotated[
        str | None,
        typer.Option("--reference-public-url", help="已有 OSS URL Ref 的 public_url；传入后不会 stage 本地文件。"),
    ] = None,
    reference_url_ref_json: Annotated[
        str | None,
        typer.Option("--reference-url-ref-json", help="读取 oss-upload-image --json 或 --json-ref-only 输出，作为 reference_image。传 - 表示 stdin。"),
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
        str | None,
        typer.Option("--model-id", help="图片生成模型 ID；不传时使用服务端 poster_title_image 默认模型。"),
    ] = None,
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
    caller_id: Annotated[
        str,
        typer.Option("--caller-id", "--x-ai-service-caller-id", help="X-AI-Service-Caller-ID。"),
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
        typer.Option("--download-outputs", help="下载 Job 结果里的全部输出图片到本地目录，并校验 sha256 与透明背景。"),
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
            items_json=items_json,
            reference_image=reference_image,
            reference_url_ref_json=reference_url_ref_json,
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
            caller_id=caller_id,
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
            client_request_id=client_request_id,
            json_output=json_output,
            download_outputs=download_outputs,
            output_dir=output_dir,
            signed_url_expires_seconds=signed_url_expires_seconds,
            allow_remote_api=allow_remote_api,
            service_api_key=service_api_key,
            env_file=env_file,
        )
    except poster_title_image.FlowError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(exc.exit_code) from exc


if __name__ == "__main__":
    app(prog_name="./scripts/real-flow.sh")
