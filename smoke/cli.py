from __future__ import annotations

from typing import Annotated, Any
from urllib.error import HTTPError, URLError

import typer

from smoke import scenarios as smoke_scenarios
from smoke.harness import cli_contract
from smoke.harness import env_runtime
from smoke.harness import formatters
from smoke.harness import http_runtime
from smoke.harness import service_runtime
from smoke.harness.errors import FlowError
from smoke.harness.cli_contract import (
    AllowRemoteApiOption,
    BaseUrlOption,
    CallbackEventOption,
    CallbackTimeoutOption,
    CallbackUrlOption,
    CallerIdOption,
    EnvFileOption,
    JsonOutputOption,
    LocalCallbackOption,
    OutputDirOption,
    PollIntervalOption,
    ServiceApiKeyOption,
    TimeoutOption,
)
from smoke.jobs import cli_contract as job_cli_contract


DEFAULT_AUDIO_STEM_JOB_TYPE = "audio_stem_separation"
DEFAULT_AUDIO_STEM_OUTPUT_DIR = ".data/smoke/audio-stem-separation"
DEFAULT_POSTER_TITLE_IMAGE_OUTPUT_DIR = ".data/smoke/poster-title-image"

ConfirmRunOption = Annotated[
    bool,
    typer.Option("--confirm-run", help="确认本命令会创建真实 Job 并写入 Job/Outbox/Callback 数据。"),
]
ConfirmCostOption = Annotated[
    bool,
    typer.Option("--confirm-cost", help="确认本命令会调用真实模型或 provider，并可能产生费用。"),
]
ConfirmUploadOption = Annotated[
    bool,
    typer.Option("--confirm-upload", help="确认本命令可能上传本地文件到对象存储。"),
]
ClientRequestIdOption = Annotated[
    str | None,
    typer.Option("--client-request-id", help="显式 client_request_id；默认由场景自动生成。"),
]
ExpectStatusOption = Annotated[
    str,
    typer.Option("--expect-status", help="期望终态：auto、succeeded 或 failed。"),
]


POSTER_TITLE_IMAGE_HELP_EPILOG = """\b
常用示例：

\b
  # 单 item：使用本地透明 PNG 参考图，脚本自动转成 API reference_image URL Ref。
  ./scripts/smoke.sh --json poster-title-image \\
    --confirm-cost \\
    --reference .data/title/英语.png \\
    --language es \\
    --title-text "Cuando el amor se alejo"

\b
  # 单 item：生成后下载全部输出图，并校验 sha256 与透明背景。
  ./scripts/smoke.sh --json poster-title-image \\
    --confirm-cost \\
    --reference .data/title/英语.png \\
    --language es \\
    --title-text "Cuando el amor se alejo" \\
    --download-outputs

\b
  # 多 item：每个 item 在 JSON 中指定 language/title_text/reference。
  ./scripts/smoke.sh --json poster-title-image \\
    --confirm-cost \\
    --items-json .data/title/poster-items.json \\
    --download-outputs

\b
  # 推荐：先上传参考图得到 URL Ref JSON，再用 URL Ref 创建 Job。
  mkdir -p .run

\b
  ./scripts/smoke.sh --env-file env_test/.env oss-upload-image \\
    --confirm-upload \\
    --image .data/title/英语.png \\
    --json-ref-only > .run/reference-image.json

\b
  ./scripts/smoke.sh --json poster-title-image \\
    --confirm-cost \\
    --reference-url-ref-json .run/reference-image.json \\
    --language es \\
    --title-text "Cuando el amor se alejo"

\b
  # 已有 OSS URL Ref：不 stage 本地图片，也可以直接传四字段。
  ./scripts/smoke.sh --json poster-title-image \\
    --confirm-cost \\
    --language es \\
    --title-text "Cuando el amor se alejo" \\
    --reference-public-url "https://bucket.oss-region.aliyuncs.com/path/title.png" \\
    --reference-internal-url "https://bucket.oss-region-internal.aliyuncs.com/path/title.png" \\
    --reference-content-type image/png \\
    --reference-sha256 "<64位小写sha256>"

\b
  # STORAGE_BACKEND=aliyun_oss 且传本地参考图时，需要显式确认上传。
  ./scripts/smoke.sh --json poster-title-image \\
    --confirm-cost \\
    --confirm-upload \\
    --reference .data/title/英语.png \\
    --language es \\
    --title-text "Cuando el amor se alejo" \\
    --download-outputs

\b
  # 远端测试环境：必须显式允许远端 API；SERVICE_API_KEY 优先从 --env-file 或运行时环境读取。
  ./scripts/smoke.sh \\
    --allow-remote-api \\
    --env-file env_test/.env \\
    --base-url http://test-cms-poster-title.epubgame.com \\
    --caller-id default \\
    --json \\
    poster-title-image \\
    --confirm-cost \\
    --confirm-upload \\
    --reference .data/title/英语.png \\
    --language es \\
    --title-text "Cuando el amor se alejo" \\
    --download-outputs

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
  --download-outputs 默认保存到 .data/smoke/poster-title-image/<job_id>/<item_id>-<language>/。
"""

READY_HELP_EPILOG = """\b
常用示例：
  ./scripts/smoke.sh ready
  ./scripts/smoke.sh --json ready

\b
  ./scripts/smoke.sh \\
    --env-file env_test/.env \\
    --allow-remote-api \\
    --base-url http://test-cms-poster-title.epubgame.com \\
    --json \\
    ready
"""

EXAMPLE_LIFECYCLE_PROBE_HELP_EPILOG = """\b
常用示例：
  # 验证 api -> dispatcher -> taskiq_worker 基础成功链路。
  ./scripts/smoke.sh example-lifecycle-probe --confirm-run

\b
  # 验证 callbacker：本地临时启动 callback receiver，等待 callback.status=delivered。
  ./scripts/smoke.sh --json example-lifecycle-probe \\
    --confirm-run \\
    --local-callback

\b
  # 模拟耗时与失败，用于复盘失败终态、错误记录和 callback 失败事件。
  ./scripts/smoke.sh example-lifecycle-probe \\
    --confirm-run \\
    --fail \\
    --fail-after-seconds 1 \\
    --expect-status failed \\
    --local-callback

\b
说明：
  本场景使用 visibility=demo 的 example_lifecycle_probe 标准 Job，仅用于 local/dev 平台验收，不调用真实模型、不产生模型费用。
  --local-callback 只适合本机 API/worker 运行形态；远端或容器内 callbacker 无法访问调用方的 127.0.0.1。
  普通成功链路不能证明 reconciler 被触发；输出会把 reconciler 标记为兜底收敛合同角色。
"""

EXAMPLE_RECONCILER_PROBE_HELP_EPILOG = """\b
常用示例：
  # 验证 api -> dispatcher -> taskiq_worker -> reconciler -> callbacker 全链路。
  ./scripts/smoke.sh --json --timeout 120 --poll-interval 1 \\
    example-reconciler-probe \\
    --confirm-run \\
    --confirm-fault-injection \\
    --local-callback

\b
  # 验证失败终态缺失 callback_outbox 时也能兜底创建并投递 failed callback。
  ./scripts/smoke.sh --json --timeout 120 --poll-interval 1 \\
    example-reconciler-probe \\
    --confirm-run \\
    --confirm-fault-injection \\
    --fail \\
    --fail-after-seconds 1 \\
    --expect-status failed \\
    --local-callback

\b
说明：
  本场景使用 visibility=demo 的 example_lifecycle_probe 标准 Job，仅用于 local/dev 平台验收，不调用真实模型、不产生模型费用。
  本场景会先创建一个未配置 callback 的 Job，等待它终态后，再通过受保护的 DB fault injection 写入 callback_url 但不创建 callback_outbox。
  只有真实 reconciler 扫描到“终态 Job 缺失 callback_outbox”并创建 outbox 后，callbacker 才能投递到 receiver。
  必须同时传入 --confirm-run 和 --confirm-fault-injection；非 local/dev APP_ENV 或非 loopback API 会拒绝执行。
"""

LLM_JOB_BILLING_HELP_EPILOG = """\b
常用示例：
  ./scripts/smoke.sh llm-job-billing --confirm-cost --model-id gpt-5.4-mini
  ./scripts/smoke.sh --json llm-job-billing --confirm-cost --input-text "用一句话回复：计费验证成功"

\b
  ./scripts/smoke.sh \\
    --allow-remote-api \\
    --env-file env_test/.env \\
    --base-url http://test-cms-poster-title.epubgame.com \\
    --json \\
    llm-job-billing \\
    --confirm-cost
"""

LLM_JOB_DOUBLE_BILLING_HELP_EPILOG = """\b
常用示例：
  ./scripts/smoke.sh llm-job-double-billing --confirm-cost --model-id gpt-5.4-mini
  ./scripts/smoke.sh --json llm-job-double-billing --confirm-cost --model-id gpt-5.4-mini

\b
  ./scripts/smoke.sh \\
    --allow-remote-api \\
    --env-file env_test/.env \\
    --base-url http://test-cms-poster-title.epubgame.com \\
    --json \\
    llm-job-double-billing \\
    --confirm-cost
"""

OSS_UPLOAD_IMAGE_HELP_EPILOG = """\b
常用示例：
  ./scripts/smoke.sh --json oss-upload-image \\
    --confirm-upload \\
    --image .data/title/英语.png \\
    --signed-url-expires-seconds 3600

\b
  mkdir -p .run

\b
  ./scripts/smoke.sh oss-upload-image \\
    --confirm-upload \\
    --image .data/title/英语.png \\
    --json-ref-only > .run/reference-image.json

\b
  ./scripts/smoke.sh oss-upload-image --confirm-upload --image .data/title/英语.png --emit-poster-args
"""

ADAPTER_IMAGE_PROBE_HELP_EPILOG = """\b
常用示例：
  ./scripts/smoke.sh --json adapter-image-probe \\
    --confirm-cost \\
    --models-config app/jobs/types/poster_title_image/models.yaml

\b
  ./scripts/smoke.sh --json adapter-image-probe \\
    --confirm-cost \\
    --models-config app/jobs/types/poster_title_image/models.yaml \\
    --prompt "Generate a simple transparent title image saying Hola" \\
    --size 1024x1024 \\
    --quality low

\b
  ./scripts/smoke.sh --json adapter-image-probe \\
    --confirm-cost \\
    --reference .data/title/英语.png \\
    --reference-content-type image/png

\b
用途：
  直接调用本仓库封装的 openai_images 与 openai_responses 两个 image adapter。
  每次执行都会调用两个 adapter；全局 catalog 中图片模型 route 的 adapter 会排第一，另一个 adapter 排第二。
  默认从 poster_title_image models.yaml 读取 generation/style_probe model slot；可用 --models-config 指定配置文件。
  输出 adapter 返回的 usage（SDK 返回时包含 provider_usage）、revised_prompt、图片数量，以及每张图片的 sha256 和 size_bytes。
  不经过服务 HTTP Job，不查询 billing，不打印图片二进制。
"""

AUDIO_STEM_SEPARATION_HELP_EPILOG = """\b
常用示例：
  # 构建完整 create-job payload；默认输出到 stdout。
  ./scripts/smoke.sh --env-file .env audio-stem-separation build-payload \\
    --input-file .data/misc/2485_0003_S6_梁萧.wav > .run/audio-stem-payload.json

\b
  # 使用已构建 payload 提交真实 Job。
  ./scripts/smoke.sh --env-file .env --json audio-stem-separation run \\
    --confirm-run \\
    --payload-file .run/audio-stem-payload.json

\b
  # 直接用本地 WAV 构建入参并提交；STORAGE_BACKEND=aliyun_oss 时需要 --confirm-upload。
  ./scripts/smoke.sh \\
    --env-file env_test/.env \\
    --allow-remote-api \\
    --base-url http://test-cms-poster-title.epubgame.com \\
    --json \\
    audio-stem-separation run \\
    --confirm-run \\
    --confirm-upload \\
    --input-file .data/misc/2485_0003_S6_梁萧.wav \\
    --download-outputs

\b
输入要求：
  input_file 必须是 htdemucs-input：WAV、44.1kHz、双声道。
  build-payload 不提交 Job；使用 --input-file 时会先 stage/upload 输入音频来生成 URL Ref。
  run 会真实提交 Job、等待终态，并可下载四条 stem。
  --env-file 沿用 smoke 既有配置入口；选择 profile 后优先于本机同名环境变量。
  本地文件会先 stage/upload 成 input_audio URL Ref，Job 本身仍按 public_url 读取输入，不直接接收本地文件路径。
  本地 STORAGE_BACKEND=local 只适合 public_url 已能被 API/worker 读取的环境；远端测试优先使用 STORAGE_BACKEND=aliyun_oss + --confirm-upload。
"""

TAGGED_TEXT_TRANSLATION_HELP_EPILOG = """\b
常用示例：
  # 人读模式：提交真实 Job，输出摘要、计费估算和翻译前后 preview。
  ./scripts/smoke.sh \\
    --timeout 300 \\
    --poll-interval 2 \\
    tagged-text-translation \\
    --confirm-cost \\
    --source-language en \\
    --target-language zh \\
    --text '<span>Hello {user_name}, welcome back!</span>'

\b
  # JSON 模式：--json 是 smoke 全局参数，必须放在场景命令前；输出完整 source_text / translated_text。
  ./scripts/smoke.sh \\
    --json \\
    --timeout 300 \\
    --poll-interval 2 \\
    tagged-text-translation \\
    --confirm-cost \\
    --source-language en \\
    --target-language zh \\
    --text '<span>Hello {user_name}, welcome back!</span>'

\b
  # 多 item：从 JSON 文件读取 items[]，人读模式最多展示前 3 条；完整结果使用 --json。
  ./scripts/smoke.sh --json \\
    tagged-text-translation \\
    --confirm-cost \\
    --source-language en \\
    --target-language zh \\
    --items-json .data/translation/items.json

\b
输出模式：
  默认人读模式输出 Job 状态、translation/billing 摘要，以及翻译前后 preview；长文本会截断，多 item 只展示前 3 条。
  --json 输出机器可读 JSON，包含 ok/scenario/job/request/result/billing/summary/responses；request.items 保留完整 source_text，result.items 保留完整 source_text 和 translated_text。
  不支持把 --json 放到 tagged-text-translation 后面；全局参数统一放在场景命令前。

\b
items-json 最小格式：
  {
    "items": [
      {
        "id": "homepage.title",
        "text": "<span>Hello {user_name}, welcome back!</span>",
        "max_target_chars_hint": 30
      }
    ]
  }
"""


HELP_EPILOG = """\b
作用域：
  Smoke/E2E runtime。Job 类场景会调用已运行的服务 HTTP API、创建真实 Job、等待 worker 执行，并查询结果证据。
  adapter-image-probe 会直接调用本仓库封装的 provider adapter，不经过本地 API/worker。
  本入口允许真实 LLM 调用，可能产生费用；不会被 ./scripts/verify.sh check 默认执行。
  服务启动、停止、迁移和排障查询分别归属 run/dev/deploy、jobs.sh 与 job-ops.sh。

\b
参数分层：
  全局参数放在 <command> 前：--base-url、--env-file、--allow-remote-api、--service-api-key、--caller-id、--timeout、--poll-interval、--output-dir、--json。
  标准 Job 参数由 Job 场景复用：--confirm-run、--confirm-cost、--confirm-upload、--client-request-id、--expect-status。
  标准 Callback 参数由需要 callback 的 Job 场景复用：--callback-url、--local-callback、--callback-event、--wait-callback/--no-wait-callback、--callback-timeout-seconds。
  业务参数只放在对应 <command> -h，不放进顶层 help。

\b
输出：
  默认输出 smoke 摘要和关键证据。
  --json 输出机器可读 JSON，stdout 只包含 JSON；Job 类场景保留 summary/responses，业务场景可增加结构化证据字段。
  错误原因输出到 stderr。

\b
常用示例：
  ./scripts/smoke.sh --json list
  ./scripts/smoke.sh health
  ./scripts/smoke.sh ready
  ./scripts/smoke.sh --json --timeout 120 example-lifecycle-probe --confirm-run --local-callback
  ./scripts/smoke.sh --json --timeout 120 example-reconciler-probe --confirm-run --confirm-fault-injection --local-callback
  ./scripts/smoke.sh <command> -h

\b
扩展规范：
  新增 Job smoke 时，命令函数只声明标准参数和业务参数；Job context、Job 轮询和 Job 标准参数放在 smoke/jobs。
  跨项目通用能力放在 smoke/harness：env、HTTP、service runtime、callback receiver、CLI contract。
  平台故障注入能力只放在 smoke/jobs，并必须由专用 platform_acceptance 场景显式确认后调用。
  新增业务 flow 放在 smoke/flows/<domain>/，只实现 payload 构造、业务断言和业务证据。

\b
副作用与保护边界：
  LLM/image 计费命令必须显式传入 --confirm-cost；audio-stem-separation run 必须显式传入 --confirm-run。
  adapter-image-probe 必须显式传入 --confirm-cost，会直接调用真实 OpenAI image adapter。
  oss-upload-image 必须显式传入 --confirm-upload。
  poster-title-image 在 STORAGE_BACKEND=aliyun_oss 且使用本地参考图时也必须传入 --confirm-upload。
  audio-stem-separation 在 STORAGE_BACKEND=aliyun_oss 且使用本地音频时也必须传入 --confirm-upload。
  非本机 --base-url 必须显式传入 --allow-remote-api。
  远端测试 OSS 配置优先通过 --env-file 指向测试环境配置文件。
  --service-api-key 会出现在 shell history 和进程参数中；共享机器或 CI 优先通过 SERVICE_API_KEY 环境变量注入。
  普通业务场景只通过公开 HTTP API 创建 Job、轮询状态和查询 billing，不直接改数据库，不重试历史 Job。
  platform_acceptance 故障注入场景只允许 local/dev，并且必须显式确认。

\b
Exit Codes:
  0  成功
  1  场景失败
  2  参数或本地配置错误
  3  服务未 ready
  4  外部依赖不可用或证据不可达
  5  超时
"""

app = typer.Typer(
    name="smoke.sh",
    help="标准 smoke/E2E 验证入口。",
    epilog=HELP_EPILOG,
    no_args_is_help=False,
    add_completion=False,
    rich_markup_mode=None,
    context_settings={"help_option_names": ["-h", "--help"]},
)

audio_stem_separation_app = typer.Typer(
    name="audio-stem-separation",
    help="真实验证 htdemucs-ft 音乐源分离 Job，支持本地 ONNX 和 Triton 模型服务两种 job_type。",
    epilog=AUDIO_STEM_SEPARATION_HELP_EPILOG,
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode=None,
    context_settings={"help_option_names": ["-h", "--help"]},
)


_smoke_options = cli_contract.smoke_options
_validate_global_options = cli_contract.validate_global_options


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    api_url: BaseUrlOption = None,
    env_file: EnvFileOption = None,
    allow_remote_api: AllowRemoteApiOption = False,
    service_api_key: ServiceApiKeyOption = None,
    caller_id: CallerIdOption = "smoke-cli",
    timeout_seconds: TimeoutOption = 300,
    poll_interval_seconds: PollIntervalOption = 2.0,
    output_dir: OutputDirOption = None,
    json_output: JsonOutputOption = False,
) -> None:
    ctx.obj = cli_contract.SmokeOptions(
        api_url=api_url,
        env_file=env_file,
        allow_remote_api=allow_remote_api,
        service_api_key=service_api_key,
        caller_id=caller_id,
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
        output_dir=output_dir,
        json_output=json_output,
    )
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help(), err=True)
        raise typer.Exit(2)


SCENARIOS: list[dict[str, Any]] = smoke_scenarios.scenario_payloads()


def _health_url(base_url: str) -> str:
    return f"{base_url.rstrip('/')}/health"


def _ready_url(base_url: str) -> str:
    return f"{base_url.rstrip('/')}/healthz"


@app.command("list", help="列出当前项目 smoke 场景。")
def list_command(ctx: typer.Context) -> None:
    _validate_global_options(ctx, "list", cli_contract.GLOBAL_LIST_OPTIONS)
    options = _smoke_options(ctx)
    payload = {"scenarios": SCENARIOS}
    if options.json_output:
        formatters.print_json(payload)
        return
    formatters.print_table(
        SCENARIOS,
        columns=["name", "entrypoints", "type", "acceptance_class", "dependencies", "destructive", "supports_resume"],
    )


@app.command("health", help="检查服务进程级健康，不上传、不提交 Job、不产生费用。")
def health_command(ctx: typer.Context) -> None:
    _validate_global_options(ctx, "health", cli_contract.GLOBAL_HEALTH_OPTIONS)
    options = _smoke_options(ctx)
    try:
        app_env = env_runtime.load_app_env(options.env_file)
        resolved_base_url = service_runtime.resolved_api_url(
            options.api_url,
            app_env,
            allow_remote_api=options.allow_remote_api,
        )
        payload = http_runtime.request_json(
            _health_url(resolved_base_url),
            method="GET",
            headers={"Accept": "application/json"},
            timeout_seconds=10,
        )
    except FlowError as exc:
        if options.json_output:
            formatters.print_json({"ready": False, "phase": "health", "error": str(exc)})
        else:
            typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(3 if exc.exit_code == 4 else exc.exit_code) from exc
    except (HTTPError, URLError) as exc:
        if options.json_output:
            formatters.print_json({"ready": False, "phase": "health", "error": str(exc)})
        else:
            typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(3) from exc

    result = {"ready": payload.get("status") == "ok", "base_url": resolved_base_url, "health": payload}
    if options.json_output:
        formatters.print_json(result)
    else:
        formatters.print_table([result], columns=["ready", "base_url", "health"])
    if not result["ready"]:
        raise typer.Exit(3)


@app.command("ready", help="检查 smoke 服务运行上下文和 /healthz，不上传、不提交 Job、不产生费用。", epilog=READY_HELP_EPILOG)
def ready_command(ctx: typer.Context) -> None:
    _validate_global_options(ctx, "ready", cli_contract.GLOBAL_READY_OPTIONS)
    options = _smoke_options(ctx)
    try:
        context = service_runtime.resolve_runtime_context(
            env_file=options.env_file,
            api_url=options.api_url,
            allow_remote_api=options.allow_remote_api,
            caller_id=options.caller_id,
            service_api_key=options.service_api_key,
        )
        ready_payload = None
        if context.summary["ready"]:
            ready_payload = http_runtime.request_json(
                _ready_url(str(context.summary["api_url"])),
                method="GET",
                headers={"Accept": "application/json"},
                timeout_seconds=10,
            )
    except FlowError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(3 if exc.exit_code == 4 else exc.exit_code) from exc
    if options.json_output:
        payload = {**context.summary, "ready_response": ready_payload}
        formatters.print_json(payload)
        if not context.summary["ready"]:
            raise typer.Exit(2)
        return
    typer.echo("Smoke Ready")
    for key, value in context.summary.items():
        typer.echo(f"{key}={value}")
    if ready_payload is not None:
        typer.echo(f"ready_response={ready_payload}")
    if not context.summary["ready"]:
        raise typer.Exit(2)


@app.command(
    "example-lifecycle-probe",
    help="提交 example_lifecycle_probe 标准 Job，验收平台异步链路和 callbacker。",
    epilog=EXAMPLE_LIFECYCLE_PROBE_HELP_EPILOG,
)
def example_lifecycle_probe_command(
    ctx: typer.Context,
    confirm_run: ConfirmRunOption = False,
    probe_id: Annotated[
        str,
        typer.Option("--probe-id", help="写入 Job 参数和结果的探针 ID。"),
    ] = "default",
    message: Annotated[
        str,
        typer.Option("--message", help="写入 Job 参数和结果的探针消息。"),
    ] = "lifecycle probe",
    sleep_seconds: Annotated[
        float,
        typer.Option("--sleep-seconds", min=0, max=600, help="模拟 Job 执行耗时秒数。"),
    ] = 0,
    fail: Annotated[
        bool,
        typer.Option("--fail", help="让 Job 执行失败，用于验证失败终态和失败 callback。"),
    ] = False,
    fail_after_seconds: Annotated[
        float,
        typer.Option("--fail-after-seconds", min=0, max=600, help="失败前模拟等待秒数，仅在 --fail 时生效。"),
    ] = 0,
    result_payload: Annotated[
        str | None,
        typer.Option("--result-payload", help="成功结果中的自定义 payload，不能与 --result-size-bytes 同时使用。"),
    ] = None,
    result_size_bytes: Annotated[
        int,
        typer.Option("--result-size-bytes", min=0, max=65536, help="成功结果中生成固定大小 payload 字符串。"),
    ] = 0,
    expect_status: ExpectStatusOption = "auto",
    callback_url: CallbackUrlOption = None,
    local_callback: LocalCallbackOption = False,
    callback_event: CallbackEventOption = "both",
    wait_callback: cli_contract.WaitCallbackOption = True,
    callback_timeout_seconds: CallbackTimeoutOption = None,
    client_request_id: ClientRequestIdOption = None,
) -> None:
    from smoke.flows.examples import lifecycle_probe as example_lifecycle_probe
    from smoke.jobs import cli_contract as job_cli_contract

    _validate_global_options(ctx, "example-lifecycle-probe", cli_contract.GLOBAL_CONTEXT_OPTIONS)
    options = _smoke_options(ctx)
    job_options = job_cli_contract.job_smoke_options(
        confirm_run=confirm_run,
        client_request_id=client_request_id,
        expect_status=expect_status,
    )
    callback_options = cli_contract.callback_smoke_options(
        callback_url=callback_url,
        local_callback=local_callback,
        callback_event=callback_event,
        wait_callback=wait_callback,
        callback_timeout_seconds=callback_timeout_seconds,
    )
    try:
        example_lifecycle_probe.run(
            job_options=job_options,
            callback_options=callback_options,
            api_url=options.api_url,
            env_file=options.env_file,
            allow_remote_api=options.allow_remote_api,
            service_api_key=options.service_api_key,
            caller_id=options.caller_id,
            timeout_seconds=options.timeout_seconds,
            poll_interval_seconds=options.poll_interval_seconds,
            probe_id=probe_id,
            message=message,
            sleep_seconds=sleep_seconds,
            fail=fail,
            fail_after_seconds=fail_after_seconds,
            result_payload=result_payload,
            result_size_bytes=result_size_bytes,
            json_output=options.json_output,
        )
    except example_lifecycle_probe.FlowError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(exc.exit_code) from exc


@app.command(
    "example-reconciler-probe",
    help="提交 example_lifecycle_probe 并注入 callback_outbox 缺失故障，验收 reconciler 兜底收敛。",
    epilog=EXAMPLE_RECONCILER_PROBE_HELP_EPILOG,
)
def example_reconciler_probe_command(
    ctx: typer.Context,
    confirm_run: ConfirmRunOption = False,
    confirm_fault_injection: job_cli_contract.ConfirmFaultInjectionOption = False,
    probe_id: Annotated[
        str,
        typer.Option("--probe-id", help="写入 Job 参数和结果的探针 ID。"),
    ] = "reconciler",
    message: Annotated[
        str,
        typer.Option("--message", help="写入 Job 参数和结果的探针消息。"),
    ] = "reconciler probe",
    sleep_seconds: Annotated[
        float,
        typer.Option("--sleep-seconds", min=0, max=600, help="模拟 Job 执行耗时秒数。"),
    ] = 0,
    fail: Annotated[
        bool,
        typer.Option("--fail", help="让 Job 执行失败，用于验证失败终态的 reconciler callback 兜底。"),
    ] = False,
    fail_after_seconds: Annotated[
        float,
        typer.Option("--fail-after-seconds", min=0, max=600, help="失败前模拟等待秒数，仅在 --fail 时生效。"),
    ] = 0,
    result_payload: Annotated[
        str | None,
        typer.Option("--result-payload", help="成功结果中的自定义 payload，不能与 --result-size-bytes 同时使用。"),
    ] = None,
    result_size_bytes: Annotated[
        int,
        typer.Option("--result-size-bytes", min=0, max=65536, help="成功结果中生成固定大小 payload 字符串。"),
    ] = 0,
    expect_status: ExpectStatusOption = "auto",
    local_callback: LocalCallbackOption = False,
    callback_event: CallbackEventOption = "both",
    callback_timeout_seconds: CallbackTimeoutOption = None,
    client_request_id: ClientRequestIdOption = None,
) -> None:
    from smoke.flows.examples import reconciler_probe as example_reconciler_probe
    from smoke.jobs import cli_contract as job_cli_contract

    _validate_global_options(ctx, "example-reconciler-probe", cli_contract.GLOBAL_CONTEXT_OPTIONS)
    options = _smoke_options(ctx)
    job_options = job_cli_contract.job_smoke_options(
        confirm_run=confirm_run,
        client_request_id=client_request_id,
        expect_status=expect_status,
    )
    callback_options = cli_contract.callback_smoke_options(
        callback_url=None,
        local_callback=local_callback,
        callback_event=callback_event,
        wait_callback=True,
        callback_timeout_seconds=callback_timeout_seconds,
    )
    try:
        example_reconciler_probe.run(
            job_options=job_options,
            callback_options=callback_options,
            confirm_fault_injection=confirm_fault_injection,
            api_url=options.api_url,
            env_file=options.env_file,
            allow_remote_api=options.allow_remote_api,
            service_api_key=options.service_api_key,
            caller_id=options.caller_id,
            timeout_seconds=options.timeout_seconds,
            poll_interval_seconds=options.poll_interval_seconds,
            probe_id=probe_id,
            message=message,
            sleep_seconds=sleep_seconds,
            fail=fail,
            fail_after_seconds=fail_after_seconds,
            result_payload=result_payload,
            result_size_bytes=result_size_bytes,
            json_output=options.json_output,
        )
    except example_reconciler_probe.FlowError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(exc.exit_code) from exc


@app.command("llm-job-billing", help="真实调用 LLM，并查询 Job billing。", epilog=LLM_JOB_BILLING_HELP_EPILOG)
def llm_job_billing_command(
    ctx: typer.Context,
    confirm_cost: ConfirmCostOption = False,
    model_id: Annotated[
        str | None,
        typer.Option("--model-id", help="模型 ID；默认读取 models.yaml default_model_ids.text_generation。"),
    ] = None,
    input_text: Annotated[
        str,
        typer.Option("--input-text", help="传给真实 LLM Job 的输入文本。"),
    ] = "用一句话回复：真实 LLM 计费验证成功。",
    instruction: Annotated[
        str,
        typer.Option("--instruction", help="传给验证 Job 的指令。"),
    ] = "用一句话确认真实 LLM 计费链路可用。",
    client_request_id: ClientRequestIdOption = None,
) -> None:
    from smoke.flows.llm import billing as llm_job_billing

    _validate_global_options(ctx, "llm-job-billing", cli_contract.GLOBAL_CONTEXT_OPTIONS)
    options = _smoke_options(ctx)
    try:
        llm_job_billing.run(
            confirm_cost=confirm_cost,
            job_type="job_real_llm_echo",
            api_url=options.api_url,
            model_id=model_id,
            input_text=input_text,
            instruction=instruction,
            second_instruction=None,
            caller_id=options.caller_id,
            timeout_seconds=options.timeout_seconds,
            poll_interval_seconds=options.poll_interval_seconds,
            client_request_id=client_request_id,
            json_output=options.json_output,
            allow_remote_api=options.allow_remote_api,
            service_api_key=options.service_api_key,
            env_file=options.env_file,
        )
    except FlowError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(exc.exit_code) from exc


@app.command(
    "llm-job-double-billing",
    help="真实调用两次 LLM，并查询同一 Job 的汇总 billing。",
    epilog=LLM_JOB_DOUBLE_BILLING_HELP_EPILOG,
)
def llm_job_double_billing_command(
    ctx: typer.Context,
    confirm_cost: ConfirmCostOption = False,
    model_id: Annotated[
        str | None,
        typer.Option("--model-id", help="模型 ID；默认读取 models.yaml default_model_ids.text_generation。"),
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
    client_request_id: ClientRequestIdOption = None,
) -> None:
    from smoke.flows.llm import billing as llm_job_billing

    _validate_global_options(ctx, "llm-job-double-billing", cli_contract.GLOBAL_CONTEXT_OPTIONS)
    options = _smoke_options(ctx)
    try:
        llm_job_billing.run(
            confirm_cost=confirm_cost,
            job_type="job_real_llm_double_echo",
            api_url=options.api_url,
            model_id=model_id,
            input_text=input_text,
            instruction=first_instruction,
            second_instruction=second_instruction,
            caller_id=options.caller_id,
            timeout_seconds=options.timeout_seconds,
            poll_interval_seconds=options.poll_interval_seconds,
            client_request_id=client_request_id,
            json_output=options.json_output,
            allow_remote_api=options.allow_remote_api,
            service_api_key=options.service_api_key,
            env_file=options.env_file,
        )
    except FlowError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(exc.exit_code) from exc


@app.command(
    "tagged-text-translation",
    help="提交 tagged_text_translation Job，轮询终态并校验标签和占位符保留。",
    epilog=TAGGED_TEXT_TRANSLATION_HELP_EPILOG,
)
def tagged_text_translation_command(
    ctx: typer.Context,
    confirm_cost: ConfirmCostOption = False,
    source_language: Annotated[
        str | None,
        typer.Option("--source-language", help="源语种；不传时由模型识别。"),
    ] = None,
    target_language: Annotated[
        str,
        typer.Option("--target-language", help="目标语种。"),
    ] = "zh",
    item_id: Annotated[
        str,
        typer.Option("--item-id", help="单 item id；传 --items-json 时忽略。"),
    ] = "homepage.title",
    text: Annotated[
        str,
        typer.Option("--text", help="单 item 待翻译文本；传 --items-json 时忽略。"),
    ] = "<span>Hello {user_name}, welcome back!</span>",
    max_target_chars_hint: Annotated[
        int | None,
        typer.Option("--max-target-chars-hint", min=1, help="目标译文可见文本字符数建议。"),
    ] = 30,
    items_json: Annotated[
        str | None,
        typer.Option("--items-json", help="读取 {\"items\": [...]} JSON 文件作为批量输入。"),
    ] = None,
    client_request_id: ClientRequestIdOption = None,
) -> None:
    from smoke.flows.translation import tagged_text_translation

    _validate_global_options(ctx, "tagged-text-translation", cli_contract.GLOBAL_CONTEXT_OPTIONS)
    options = _smoke_options(ctx)
    try:
        tagged_text_translation.run(
            confirm_cost=confirm_cost,
            api_url=options.api_url,
            env_file=options.env_file,
            allow_remote_api=options.allow_remote_api,
            service_api_key=options.service_api_key,
            caller_id=options.caller_id,
            timeout_seconds=options.timeout_seconds,
            poll_interval_seconds=options.poll_interval_seconds,
            source_language=source_language,
            target_language=target_language,
            item_id=item_id,
            text=text,
            max_target_chars_hint=max_target_chars_hint,
            items_json=items_json,
            client_request_id=client_request_id,
            json_output=options.json_output,
        )
    except tagged_text_translation.FlowError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(exc.exit_code) from exc


@app.command("oss-upload-image", help="上传本地图片到阿里云 OSS，并输出 URL Ref。", epilog=OSS_UPLOAD_IMAGE_HELP_EPILOG)
def oss_upload_image_command(
    ctx: typer.Context,
    confirm_upload: ConfirmUploadOption = False,
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
    json_ref_only: Annotated[
        bool,
        typer.Option("--json-ref-only", help="只输出 reference_image 可直接使用的 URL Ref JSON。"),
    ] = False,
    emit_poster_args: Annotated[
        bool,
        typer.Option("--emit-poster-args", help="输出可复制到 poster-title-image 的 --reference-* 参数。"),
    ] = False,
) -> None:
    from smoke.flows.oss import image_upload as oss_image_upload

    _validate_global_options(ctx, "oss-upload-image", cli_contract.GLOBAL_ENV_JSON_OPTIONS)
    options = _smoke_options(ctx)
    try:
        if image is None:
            raise oss_image_upload.FlowError("OSS image upload requires --image", exit_code=2)
        output_flags = sum([options.json_output, json_ref_only, emit_poster_args])
        if output_flags > 1:
            raise oss_image_upload.FlowError("--json, --json-ref-only and --emit-poster-args are mutually exclusive", exit_code=2)
        oss_image_upload.run(
            confirm_upload=confirm_upload,
            image=image,
            content_type=content_type,
            key=key,
            key_prefix=key_prefix,
            signed_url_expires_seconds=signed_url_expires_seconds,
            output_mode="url-ref-json" if json_ref_only else "poster-args" if emit_poster_args else "json" if options.json_output else "table",
            env_file=options.env_file,
        )
    except oss_image_upload.FlowError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(exc.exit_code) from exc


@audio_stem_separation_app.command(
    "build-payload",
    help="构建 audio_stem_separation/audio_stem_separation_triton create-job payload，不提交 Job。",
)
def audio_stem_separation_build_payload_command(
    ctx: typer.Context,
    input_file: Annotated[
        str | None,
        typer.Option("--input-file", help="本地 htdemucs-input WAV；脚本会 stage/upload 后生成 input_audio URL Ref。"),
    ] = None,
    job_type: Annotated[
        str,
        typer.Option("--job-type", help="提交的音频分离 job_type：audio_stem_separation 或 audio_stem_separation_triton。"),
    ] = DEFAULT_AUDIO_STEM_JOB_TYPE,
    input_url_ref_json: Annotated[
        str | None,
        typer.Option("--input-url-ref-json", help="读取已有 audio URL Ref JSON；传 - 表示 stdin。"),
    ] = None,
    input_public_url: Annotated[
        str | None,
        typer.Option("--input-public-url", help="已有 audio URL Ref 的 public_url。"),
    ] = None,
    input_internal_url: Annotated[
        str | None,
        typer.Option("--input-internal-url", help="已有 audio URL Ref 的 internal_url。"),
    ] = None,
    input_sha256: Annotated[
        str | None,
        typer.Option("--input-sha256", help="已有 audio object 的 64 位小写 SHA-256。"),
    ] = None,
    max_duration_seconds: Annotated[
        float | None,
        typer.Option("--max-duration-seconds", min=0.1, help="传给 Job 的最大输入时长限制，并用于本地输入预校验。"),
    ] = None,
    client_request_id: ClientRequestIdOption = None,
    confirm_upload: ConfirmUploadOption = False,
    key_prefix: Annotated[
        str | None,
        typer.Option("--key-prefix", help="本地 stage 或 OSS upload 的输入对象业务前缀。"),
    ] = None,
    signed_url_expires_seconds: Annotated[
        int,
        typer.Option("--signed-url-expires-seconds", min=1, help="Aliyun OSS 上传后生成的临时 signed_url 有效秒数。"),
    ] = 3600,
    output: Annotated[
        str,
        typer.Option("--output", help="payload 输出路径；- 表示 stdout。"),
    ] = "-",
) -> None:
    from smoke.flows.audio import stem_separation as audio_stem_separation

    _validate_global_options(ctx, "audio-stem-separation build-payload", cli_contract.GLOBAL_ENV_ONLY_OPTIONS)
    options = _smoke_options(ctx)
    try:
        payload, _staged_input = audio_stem_separation.build_payload(
            env_file=options.env_file,
            job_type=job_type,
            input_file=input_file,
            input_url_ref_json=input_url_ref_json,
            input_public_url=input_public_url,
            input_internal_url=input_internal_url,
            input_sha256=input_sha256,
            max_duration_seconds=max_duration_seconds,
            client_request_id=client_request_id,
            confirm_upload=confirm_upload,
            key_prefix=key_prefix,
            signed_url_expires_seconds=signed_url_expires_seconds,
        )
        audio_stem_separation.write_or_print_payload(payload, output=output)
    except audio_stem_separation.FlowError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(exc.exit_code) from exc


@audio_stem_separation_app.command(
    "run",
    help="提交 audio_stem_separation/audio_stem_separation_triton 真实 Job，等待终态并可下载四条 stem。",
)
def audio_stem_separation_run_command(
    ctx: typer.Context,
    confirm_run: ConfirmRunOption = False,
    confirm_upload: ConfirmUploadOption = False,
    client_request_id: ClientRequestIdOption = None,
    job_type: Annotated[
        str,
        typer.Option("--job-type", help="提交的音频分离 job_type：audio_stem_separation 或 audio_stem_separation_triton。"),
    ] = DEFAULT_AUDIO_STEM_JOB_TYPE,
    payload_file: Annotated[
        str | None,
        typer.Option("--payload-file", help="已构建的 audio_stem_separation/audio_stem_separation_triton create-job payload JSON。"),
    ] = None,
    input_file: Annotated[
        str | None,
        typer.Option("--input-file", help="本地 htdemucs-input WAV；不能与 --payload-file 同时使用。"),
    ] = None,
    input_url_ref_json: Annotated[
        str | None,
        typer.Option("--input-url-ref-json", help="读取已有 audio URL Ref JSON；传 - 表示 stdin。"),
    ] = None,
    input_public_url: Annotated[
        str | None,
        typer.Option("--input-public-url", help="已有 audio URL Ref 的 public_url。"),
    ] = None,
    input_internal_url: Annotated[
        str | None,
        typer.Option("--input-internal-url", help="已有 audio URL Ref 的 internal_url。"),
    ] = None,
    input_sha256: Annotated[
        str | None,
        typer.Option("--input-sha256", help="已有 audio object 的 64 位小写 SHA-256。"),
    ] = None,
    max_duration_seconds: Annotated[
        float | None,
        typer.Option("--max-duration-seconds", min=0.1, help="传给 Job 的最大输入时长限制，并用于本地输入预校验。"),
    ] = None,
    key_prefix: Annotated[
        str | None,
        typer.Option("--key-prefix", help="本地 stage 或 OSS upload 的输入对象业务前缀。"),
    ] = None,
    signed_url_expires_seconds: Annotated[
        int,
        typer.Option("--signed-url-expires-seconds", min=1, help="public_url 不可读时生成临时 signed URL 的有效秒数。"),
    ] = 3600,
    download_outputs: Annotated[
        bool,
        typer.Option("--download-outputs", help="下载 Job 结果里的 drums/bass/other/vocals WAV 到本地目录，并校验 sha256。"),
    ] = False,
) -> None:
    from smoke.flows.audio import stem_separation as audio_stem_separation

    _validate_global_options(
        ctx,
        "audio-stem-separation run",
        cli_contract.GLOBAL_CONTEXT_OPTIONS | cli_contract.GLOBAL_OUTPUT_OPTIONS,
    )
    options = _smoke_options(ctx)
    try:
        audio_stem_separation.run(
            confirm_run=confirm_run,
            confirm_upload=confirm_upload,
            api_url=options.api_url,
            env_file=options.env_file,
            allow_remote_api=options.allow_remote_api,
            service_api_key=options.service_api_key,
            caller_id=options.caller_id,
            timeout_seconds=options.timeout_seconds,
            poll_interval_seconds=options.poll_interval_seconds,
            job_type=job_type,
            client_request_id=client_request_id,
            payload_file=payload_file,
            input_file=input_file,
            input_url_ref_json=input_url_ref_json,
            input_public_url=input_public_url,
            input_internal_url=input_internal_url,
            input_sha256=input_sha256,
            max_duration_seconds=max_duration_seconds,
            key_prefix=key_prefix,
            signed_url_expires_seconds=signed_url_expires_seconds,
            download_outputs=download_outputs,
            output_dir=options.output_dir or DEFAULT_AUDIO_STEM_OUTPUT_DIR,
            json_output=options.json_output,
        )
    except audio_stem_separation.FlowError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(exc.exit_code) from exc


app.add_typer(
    audio_stem_separation_app,
    name="audio-stem-separation",
    help="真实验证 htdemucs-ft ONNX 音乐源分离 Job。",
)


@app.command(
    "adapter-image-probe",
    help="直接调用 openai_images 与 openai_responses 两个 image adapter，并打印 adapter 返回摘要。",
    epilog=ADAPTER_IMAGE_PROBE_HELP_EPILOG,
)
def adapter_image_probe_command(
    ctx: typer.Context,
    confirm_cost: ConfirmCostOption = False,
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
        typer.Option("--provider-model", help="覆盖图片生成 provider model；默认读取 models.yaml model_slots.generation.default_model_id。"),
    ] = None,
    response_model: Annotated[
        str | None,
        typer.Option("--response-model", help="覆盖 openai_responses adapter 的 response model；默认读取 models.yaml model_slots.style_probe.default_model_id。"),
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
) -> None:
    from smoke.flows.image import adapter_probe as adapter_image_probe

    _validate_global_options(ctx, "adapter-image-probe", cli_contract.GLOBAL_PROVIDER_OPTIONS)
    options = _smoke_options(ctx)
    try:
        adapter_image_probe.run(
            confirm_cost=confirm_cost,
            env_file=options.env_file,
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
            timeout_seconds=options.timeout_seconds,
            json_output=options.json_output,
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
    ctx: typer.Context,
    confirm_cost: ConfirmCostOption = False,
    confirm_upload: ConfirmUploadOption = False,
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
    client_request_id: ClientRequestIdOption = None,
    download_outputs: Annotated[
        bool,
        typer.Option("--download-outputs", help="下载 Job 结果里的全部输出图片到本地目录，并校验 sha256 与透明背景。"),
    ] = False,
    signed_url_expires_seconds: Annotated[
        int,
        typer.Option("--signed-url-expires-seconds", min=1, help="public_url 不可读时生成临时 signed URL 的有效秒数。"),
    ] = 3600,
) -> None:
    from smoke.flows.image import poster_title_image

    _validate_global_options(
        ctx,
        "poster-title-image",
        cli_contract.GLOBAL_CONTEXT_OPTIONS | cli_contract.GLOBAL_OUTPUT_OPTIONS,
    )
    options = _smoke_options(ctx)
    try:
        poster_title_image.run(
            confirm_cost=confirm_cost,
            confirm_upload=confirm_upload,
            api_url=options.api_url,
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
            caller_id=options.caller_id,
            timeout_seconds=options.timeout_seconds,
            poll_interval_seconds=options.poll_interval_seconds,
            client_request_id=client_request_id,
            json_output=options.json_output,
            download_outputs=download_outputs,
            output_dir=options.output_dir or DEFAULT_POSTER_TITLE_IMAGE_OUTPUT_DIR,
            signed_url_expires_seconds=signed_url_expires_seconds,
            allow_remote_api=options.allow_remote_api,
            service_api_key=options.service_api_key,
            env_file=options.env_file,
        )
    except poster_title_image.FlowError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(exc.exit_code) from exc


if __name__ == "__main__":
    app(prog_name="./scripts/smoke.sh")
