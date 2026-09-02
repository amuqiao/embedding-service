#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
source "$ROOT_DIR/scripts/lib/runtime.sh"

usage() {
  cat <<'EOF'
Usage:
  ./scripts/smoke.sh [global options] <command> [standard job options] [business options]
  ./scripts/smoke.sh -h|--help

职责:
  E2E smoke 薄入口。验证已经运行的 FastAPI Job 服务链路是否符合外部 HTTP 合同，并输出可排障证据。

不负责:
  不启动或停止 API、worker、PostgreSQL、Redis、Triton 或模型服务。
  不执行 Alembic migration。
  普通业务 smoke 不直接查库推进流程，也不替代 jobs.sh / job-ops.sh 排障查询。
  platform_acceptance 故障注入场景只允许 local/dev，并且必须显式确认。

通用参数:
  --base-url <url>        显式覆盖服务 HTTP base URL；不传时由 API_URL / API_HOST:API_PORT 推导。
  --env-file <path>      显式加载 smoke profile；优先于本机同名环境变量。也可通过 ENV_FILE 指定。
  --allow-remote-api      允许 --base-url 或 API_URL 指向非本机地址。
  --service-api-key <key> 显式覆盖 SERVICE_API_KEY；优先级高于 smoke profile 和本机环境变量。
  --caller-id <id>        X-AI-Service-Caller-ID。
  --timeout <seconds>    场景最大等待时间。
  --poll-interval <sec>  轮询间隔。
  --output-dir <path>    artifacts 或下载输出目录，默认由场景决定。
  --json                 输出机器可读 JSON；全局参数，放在 <command> 前。

标准 Job 参数:
  --confirm-run           确认会创建真实 Job。
  --confirm-cost          确认会调用真实模型或 provider，并可能产生费用。
  --confirm-upload        确认可能上传本地文件到对象存储。
  --client-request-id <id> 显式幂等键；不传时由场景自动生成。
  --expect-status <status> 期望终态：auto、succeeded 或 failed。

标准 Callback 参数:
  --callback-url <url>    外部 callback receiver URL。
  --local-callback        本地启动临时 callback receiver，用于验收真实 callbacker 投递。
  --callback-event <name> succeeded、failed 或 both。
  --wait-callback/--no-wait-callback
                          配置 callback 后是否等待 delivered。
  --callback-timeout-seconds <seconds>
                          等待 callback 的最长秒数；默认使用场景剩余 timeout。

平台故障注入参数:
  --confirm-fault-injection
                          确认 local/dev 平台验收场景会写入可恢复 DB 漂移；普通业务场景不使用。

命令:
  health                  检查服务进程级健康。
  ready                   检查 smoke 服务运行上下文和 /healthz。
  list                    列出当前项目 smoke 场景。
  example-lifecycle-probe 提交 local/dev 标准探针 Job，验收 api/dispatcher/taskiq_worker；配置 callback 后验收 callbacker。
  example-reconciler-probe 提交 local/dev 标准探针 Job，注入 callback_outbox 缺失故障，验收 reconciler/callbacker。
  llm-job-billing         提交真实 LLM Job，轮询终态并查询 billing。
  llm-job-double-billing  提交两次 LLM 调用 Job，轮询终态并查询汇总 billing。
  tagged-text-translation 提交 tagged_text_translation Job，校验标签和占位符保留；人读输出翻译前后 preview。
  poster-title-image      提交 poster_title_image Job，轮询终态并校验输出。
  audio-stem-separation   提交 audio_stem_separation / audio_stem_separation_triton Job。
  asset-image-tagging     提交 asset_image_tagging Job，验证批量素材打标签链路。
  asset-vector            提交 asset_vector Job，验证素材向量更新、删除、搜索和对账链路。
  asset-search-eval       编排 AI 打标、向量入库、搜索评估，并输出 JSON/HTML 复盘报告。
  adapter-image-probe     直连 image adapter 的 provider probe。
  oss-upload-image        显式上传本地图片，生成 URL Ref 输入。

常用示例:
  ./scripts/run.sh up dev

  ENV_FILE=.env ./scripts/smoke.sh --json list

  ENV_FILE=.env ./scripts/smoke.sh \
    --json \
    --timeout 120 \
    --poll-interval 1 \
    example-lifecycle-probe \
    --confirm-run \
    --local-callback

  ENV_FILE=.env ./scripts/smoke.sh \
    --json \
    --timeout 120 \
    --poll-interval 1 \
    example-reconciler-probe \
    --confirm-run \
    --confirm-fault-injection \
    --local-callback

  ENV_FILE=.env ./scripts/smoke.sh \
    asset-image-tagging \
    --confirm-run \
    --confirm-cost

  ENV_FILE=.env ./scripts/smoke.sh \
    asset-vector \
    --confirm-run \
    --confirm-cost

  ENV_FILE=.env ./scripts/smoke.sh \
    --output-dir poc/asset-vector/reports/evals/latest \
    asset-search-eval \
    --confirm-run \
    --confirm-cost

  ./scripts/smoke.sh <command> -h

扩展规范:
  顶层只维护全局参数、标准 Job/Callback 参数和场景列表。
  跨项目通用能力放在 smoke/harness：env、HTTP、service runtime、callback receiver、CLI contract。
  Job 服务能力放在 smoke/jobs：Job 参数、jobs_url 推导、Job 轮询和 Job 服务依赖检查。
  业务参数只出现在对应 <command> -h 中；业务 flow 放在 smoke/flows/<domain>/。

输出:
  默认人读模式输出 Job 状态、计费摘要和关键证据；tagged-text-translation 会展示翻译前后 preview。
  --json 模式 stdout 只输出 JSON，tagged-text-translation 包含完整 source_text / translated_text。

排障入口:
  ./scripts/jobs.sh show <job_id>
  ./scripts/jobs.sh timeline <job_id>
  ./scripts/jobs.sh attempts <job_id>

Exit Codes:
  0  通过
  1  场景失败，例如 job failed、callback failed 或输出断言失败
  2  参数错误或配置缺失
  3  服务未 ready
  4  外部依赖不可用
  5  超时
EOF
}

case "${1:-}" in
  -h|--help|help)
    usage
    exit 0
    ;;
esac

selected_env_file="${ENV_FILE:-}"
args=("$@")
for ((i = 0; i < ${#args[@]}; i++)); do
  case "${args[$i]}" in
    --env-file)
      if (( i + 1 < ${#args[@]} )); then
        selected_env_file="${args[$((i + 1))]}"
      fi
      break
      ;;
    --env-file=*)
      selected_env_file="${args[$i]#--env-file=}"
      break
      ;;
  esac
done

if [[ -n "$selected_env_file" ]]; then
  export ENV_FILE="$selected_env_file"
fi

require_project_python
PYTHONPATH="$ROOT_DIR${PYTHONPATH:+:$PYTHONPATH}" PYTHONUNBUFFERED=1 \
  exec "$PYTHON_BIN" -m smoke "$@"
