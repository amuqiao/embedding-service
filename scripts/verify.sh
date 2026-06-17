#!/usr/bin/env bash
# verify.sh - 本地验证入口
#
# 作用域：承接测试、smoke、e2e、对象存储连通性等一次性验证任务。
# 本地服务生命周期不属于本入口。
# 约束：入口脚本只做参数分发和帮助说明，具体实现下沉到 scripts/verify/ 原子脚本。

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

usage() {
  cat <<EOF
用法：
  ./scripts/verify.sh <command> [args...]
  ./scripts/verify.sh --help

作用域：
  当前仓库的一次性验证入口。验证任务可以依赖已运行的本地 API/worker，但不负责完整本地服务生命周期。
  mock-smoke 是显式例外：它会临时接管 worker 作为验证夹具，并在结束时仅恢复原本处于运行状态的 worker。

命令：
  test                运行 pytest。
  smoke               对已运行 API/worker 执行真实模型 Job 冒烟验证。
  rs-translation-smoke 对已运行 API/worker 执行真实模型 + 正式 jobs 路由的 RS 标签翻译冒烟验证。
  cpp-tagging-smoke   以 CPP 调用方身份请求正式 jobs 路由，执行短剧打标 Job 冒烟验证。
  mock-smoke          不调用真实模型，用 Mock OpenAI + 本地存储验证完整任务流程。
  workflow-smoke      使用真实模型和放大输入验证内部自动分块、canvas 和 merge。
  e2e                 从 .data 读取 .txt，验证 meta、jobs、轮询、callback 和三阶段链路。
  oss                 校验 Aliyun OSS 读写删除连通性，参数透传给 check_aliyun_oss.py。
  env-config          校验 env 文件键名；默认检查 .env.example 和已存在的本地/测试 env，可传文件路径。
  check               执行脚本语法、env 配置检查和 pytest。
  help                显示帮助。

成功标准：
  smoke 成功 = 真实模型 localization job 进入 succeeded 状态。
  rs-translation-smoke 成功 = 真实模型 short_drama.tag_schema.translation job 进入 succeeded 状态并通过结果校验。
  cpp-tagging-smoke 成功 = 真实模型 short_drama.tagging.initial job 进入 succeeded 状态，public result 为 null。
  mock-smoke 成功 = Mock AI step1_localize job 进入 succeeded 状态，全程不调用真实模型。
  workflow-smoke 成功 = 真实模型长文本触发内部 workflow，localized.txt 和 translated.txt 存在且非空。
  e2e 成功 = meta 契约、错误请求预检、三个 Job、轮询结果、callback 和核心 artifact 均通过校验。

rs-translation-smoke 常用参数：
  --list-cases          列出内置用例。
  --case <name>         运行指定内置用例；可重复传入。--case all 运行全部内置用例。
  --cases-file <path>   从 JSON 文件读取自定义用例列表。
  --service-api-key <key> 运行时指定目标环境鉴权 key。
  --keep-going          单个用例失败后继续执行剩余用例。

cpp-tagging-smoke 常用参数：
  --list-cases          从 POC input 目录列出可运行作品。
  --case <t_book_id>    运行指定作品；可重复传入。--case all 运行全部发现的作品。
  --input-dir <path>    指定 POC per_book 目录，默认 .data/poc/short_drama_tagging/inputs/jobs/per_book。
  --base-url <url>      目标 AI 服务地址，例如 http://127.0.0.1:8100。
  --service-api-key <key> 运行时指定目标环境鉴权 key。
EOF
}

command="${1:-help}"
case "$command" in
  --help|-h|help)
    usage
    ;;
  cpp-tagging-smoke)
    shift
    python_bin="$ROOT_DIR/.venv/bin/python"
    if [[ ! -x "$python_bin" ]]; then
      echo "missing $python_bin; run: ./scripts/dev.sh bootstrap" >&2
      exit 2
    fi
    PYTHONUNBUFFERED=1 "$python_bin" "$ROOT_DIR/scripts/verify/cpp_short_drama_tagging_job.py" "$@"
    ;;
  *)
    source "$ROOT_DIR/scripts/verify/tasks.sh"
    case "$command" in
  test)
    run_tests
    ;;
  smoke)
    run_smoke
    ;;
  rs-translation-smoke)
    shift
    run_rs_translation_smoke "$@"
    ;;
  mock-smoke)
    run_mock_smoke
    ;;
  workflow-smoke)
    shift
    run_workflow_smoke "$@"
    ;;
  e2e)
    shift
    run_e2e "$@"
    ;;
  oss)
    shift
    run_oss_check "$@"
    ;;
  env-config)
    shift
    run_env_config_check "$@"
    ;;
  check)
    run_check
    ;;
  *)
    usage >&2
    exit 2
    ;;
    esac
    ;;
esac
