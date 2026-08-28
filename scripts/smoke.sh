#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
source "$ROOT_DIR/scripts/lib/runtime.sh"

usage() {
  cat <<'EOF'
Usage:
  ./scripts/smoke.sh [smoke options] <command> [args...]
  ./scripts/smoke.sh -h|--help

职责:
  E2E smoke 薄入口。验证已经运行的 FastAPI Job 服务链路是否符合外部 HTTP 合同，并输出可排障证据。

不负责:
  不启动或停止 API、worker、PostgreSQL、Redis、Triton 或模型服务。
  不执行 Alembic migration。
  不直接查库推进流程或替代 jobs.sh / job-ops.sh 排障查询。

通用参数:
  --base-url <url>        显式覆盖服务 HTTP base URL；不传时由 API_URL / API_HOST:API_PORT 推导。
  --env-file <path>      显式加载 env 文件；也可通过 ENV_FILE 指定。
  --timeout <seconds>    场景最大等待时间。
  --poll-interval <sec>  轮询间隔。
  --output-dir <path>    artifacts 或下载输出目录，默认由场景决定。
  --json                 输出机器可读 JSON；全局参数，放在 <command> 前。

命令:
  health                  检查服务进程级健康。
  ready                   检查 smoke 运行上下文和必要依赖配置。
  list                    列出当前项目 smoke 场景。
  example-lifecycle-probe 提交 local/dev 标准探针 Job，验收 api/dispatcher/taskiq_worker；配置 callback 后验收 callbacker。
  llm-job-billing         提交真实 LLM Job，轮询终态并查询 billing。
  llm-job-double-billing  提交两次 LLM 调用 Job，轮询终态并查询汇总 billing。
  tagged-text-translation 提交 tagged_text_translation Job，校验标签和占位符保留；人读输出翻译前后 preview。
  poster-title-image      提交 poster_title_image Job，轮询终态并校验输出。
  audio-stem-separation   提交 audio_stem_separation / audio_stem_separation_triton Job。
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
    --timeout 300 \
    --poll-interval 2 \
    tagged-text-translation \
    --confirm-cost \
    --source-language en \
    --target-language zh \
    --text '<span>Hello {user_name}, welcome back!</span>'

  ENV_FILE=.env ./scripts/smoke.sh \
    --json \
    --timeout 300 \
    --poll-interval 2 \
    tagged-text-translation \
    --confirm-cost \
    --source-language en \
    --target-language zh \
    --text '<span>Hello {user_name}, welcome back!</span>'

  # 远端测试环境 tagged_text_translation。
  API_URL=http://test-cms-ai-translation-service.epubgame.com \
    SERVICE_API_KEY='<测试环境 SERVICE_API_KEY>' \
    ./scripts/smoke.sh \
      --allow-remote-api \
      --caller-id cms-test \
      --json \
      --timeout 300 \
      --poll-interval 2 \
      tagged-text-translation \
      --confirm-cost \
      --source-language en \
      --target-language zh \
      --client-request-id test-translate-001 \
      --text '<span>Hello {user_name}, welcome back!</span>'

  # 同一 client-request-id 重复执行可能命中幂等结果；需要新 Job 时改掉 test-translate-001。

  ENV_FILE=.env ./scripts/smoke.sh \
    --timeout 7200 \
    --poll-interval 5 \
    audio-stem-separation run \
    --confirm-run \
    --confirm-upload \
    --input-file .data/misc/2485_0003_S6_梁萧.wav

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

require_project_python
PYTHONPATH="$ROOT_DIR${PYTHONPATH:+:$PYTHONPATH}" PYTHONUNBUFFERED=1 \
  exec "$PYTHON_BIN" -m smoke "$@"
