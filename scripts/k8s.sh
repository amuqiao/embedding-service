#!/usr/bin/env bash
# k8s.sh - K8s Pod 内手动运维入口
#
# 运行环境：Bash；需要在已注入应用环境变量或可读取应用 env 文件的 K8s Pod 内执行。
# 作用域：只提供 Pod 内连接检查、OSS 连通性检查、dashboard read model 检查、Alembic 迁移和迁移状态查询。
# 约束：不创建或管理 Kubernetes 资源，不调用 kubectl，不管理 API/worker 生命周期。
# 输出：按生产排障需要打印完整连接串和解析结果；Alembic 输出透传。

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT_DIR/scripts/lib/common.sh"
source "$ROOT_DIR/scripts/k8s/ops.sh"

cd "$ROOT_DIR"

usage() {
  cat <<EOF
用法：
  ./scripts/k8s.sh <command> [args...]
  ./scripts/k8s.sh -h|--help

作用域：
  本脚本是 K8s Pod 内手动运维入口。
  进入已部署的 api 或 worker Pod 后，使用同一份应用代码和同一组环境变量连接外部依赖。
  若当前环境未显式注入配置，会按 ENV_FILE 指定文件或根目录 .env 补充导出缺失变量。
  只负责 PostgreSQL / Redis / OSS 连接检查、dashboard read model 检查、Alembic 迁移状态查询和手动执行迁移。

不负责：
  不创建 Job、Pod、Deployment、Secret、ConfigMap。
  不调用 kubectl、helm、docker compose。
  不管理 API/worker 生命周期。
  不替代 CI/CD 发布编排。

运行环境：
  Requires: Bash, Python；check、current、heads、history 和 migrate 还需要 Alembic。
  必须在 K8s Pod 内执行，且环境变量 KUBERNETES_SERVICE_HOST 必须存在。
  check 会打印完整 DATABASE_URL / REDIS_URL、编码密码和解码密码，输出包含敏感信息。
  check oss 会向 OSS 写入、读取、HEAD 一个临时对象并打印 URL Ref；默认不打印 OSS secret。
  check / current / heads / history / migrate 必须可通过环境变量、ENV_FILE 或根目录 .env 读取 DATABASE_URL。

命令：
  check               聚合执行 PostgreSQL、Redis、Alembic current 和 heads 无副作用检查。
  check postgres      检查 DATABASE_URL 解析结果，并执行 PostgreSQL SELECT 1。
  check redis         检查 REDIS_URL 解析结果，并执行 Redis PING。
  check dashboard     检查 ops_dashboard 有效配置，并执行 dashboard read model。
  check oss --confirm 检查 OSS 配置，并执行临时对象 PUT / GET / HEAD。
  current             查看当前数据库 Alembic revision。
  heads               查看代码中的 Alembic head revision。
  history             查看 Alembic revision 历史。
  migrate --confirm   对当前 DATABASE_URL 执行 alembic upgrade head。
  help                显示帮助。

输出：
  stdout: 连接串解析结果、连通性证据、dashboard read model 结果、Alembic 输出和 OSS URL Ref。
  stderr: 非 Pod 环境、缺少依赖、缺少配置、连接失败或迁移失败详情。

副作用与保护边界：
  migrate 是写库动作，必须显式传入 --confirm。
  生产多副本部署时，只应在一个 Pod 内执行一次 migrate。
  执行迁移前应确认当前 Pod 运行的是要发布的代码版本。
  check 和 check postgres / redis 会输出明文连接串和密码，只应在受控终端中执行。
  check 是无副作用一键检查，不包含 check dashboard / check oss，也不会执行 migrate。
  check oss 是远程写入动作，必须显式传入 --confirm；不会执行 DeleteObject，检查对象会保留在 OSS。

常用示例：
  kubectl exec -it <api-pod> -- ./scripts/k8s.sh check
  kubectl exec -it <api-pod> -- ./scripts/k8s.sh check postgres
  kubectl exec -it <api-pod> -- ./scripts/k8s.sh check redis
  kubectl exec -it <api-pod> -- ./scripts/k8s.sh check dashboard
  kubectl exec -it <api-pod> -- ./scripts/k8s.sh check oss --confirm
  kubectl exec -it <api-pod> -- ./scripts/k8s.sh current
  kubectl exec -it <api-pod> -- ./scripts/k8s.sh migrate --confirm

Exit Codes:
  0  成功
  1  check 连接串解析、认证、连通性或 Alembic 查询失败。
  2  缺少 command、非法参数、未在 K8s Pod 内执行或缺少 Python/Alembic。
EOF
}

command_usage() {
  local name="$1"
  local target="${2:-}"
  case "$name:$target" in
    check:)
      cat <<EOF
用法：
  ./scripts/k8s.sh check
  ./scripts/k8s.sh check <postgres|redis|dashboard|oss> [--confirm] [args...]
  ./scripts/k8s.sh check -h|--help

作用域：
  聚合执行 PostgreSQL、Redis、Alembic current 和 heads 无副作用检查。

副作用与保护边界：
  check 不包含 check dashboard / check oss，也不会执行 migrate。
  check postgres / redis 会输出明文连接串和密码。

常用示例：
  kubectl exec -it <api-pod> -- ./scripts/k8s.sh check
  kubectl exec -it <api-pod> -- ./scripts/k8s.sh check postgres
  kubectl exec -it <api-pod> -- ./scripts/k8s.sh check redis
  kubectl exec -it <api-pod> -- ./scripts/k8s.sh check dashboard
EOF
      ;;
    check:postgres|check:redis)
      cat <<EOF
用法：
  ./scripts/k8s.sh check ${target}
  ./scripts/k8s.sh check ${target} -h|--help

作用域：
  检查 ${target} 连接串解析结果和连通性。

副作用与保护边界：
  会输出明文连接串和密码，只应在受控终端中执行。

常用示例：
  kubectl exec -it <api-pod> -- ./scripts/k8s.sh check ${target}
EOF
      ;;
    check:dashboard)
      cat <<EOF
用法：
  ./scripts/k8s.sh check dashboard
  ./scripts/k8s.sh check dashboard -h|--help

作用域：
  检查 ops_dashboard 有效配置，并直接执行 overview / failures read model。
  适合定位 dashboard 页面 500、SQL prepare、配置加载和数据库读模型问题。

副作用与保护边界：
  只读 DB 查询；不调用 Job 写路径，不 replay dispatch / callback。
  不包含在默认 check 中，必须显式执行。

常用示例：
  kubectl exec -it <api-pod> -- ./scripts/k8s.sh check dashboard
EOF
      ;;
    check:oss)
      cat <<EOF
用法：
  ./scripts/k8s.sh check oss --confirm [--json] [--key KEY]
  ./scripts/k8s.sh check oss -h|--help

作用域：
  编排 scripts/oss.sh check --remote，检查 OSS 配置，并执行临时对象 PUT / GET / HEAD。

副作用与保护边界：
  远程写入动作，必须显式传入 --confirm。
  不执行 DeleteObject；检查对象会保留在 OSS，需要按输出 key 手动清理或依赖 bucket 生命周期。

常用示例：
  kubectl exec -it <api-pod> -- ./scripts/k8s.sh check oss --confirm
  kubectl exec -it <api-pod> -- ./scripts/k8s.sh check oss --confirm --key ai-jobs/manual/check.txt
EOF
      ;;
    current:|heads:|history:)
      cat <<EOF
用法：
  ./scripts/k8s.sh ${name}
  ./scripts/k8s.sh ${name} -h|--help

作用域：
  查看 Alembic ${name} 信息。

常用示例：
  kubectl exec -it <api-pod> -- ./scripts/k8s.sh ${name}
EOF
      ;;
    migrate:)
      cat <<EOF
用法：
  ./scripts/k8s.sh migrate --confirm
  ./scripts/k8s.sh migrate -h|--help

作用域：
  对当前 DATABASE_URL 执行 alembic upgrade head。

副作用与保护边界：
  写库动作，必须显式传入 --confirm。
  生产多副本部署时，只应在一个 Pod 内执行一次。

常用示例：
  kubectl exec -it <api-pod> -- ./scripts/k8s.sh migrate --confirm
EOF
      ;;
    *)
      usage >&2
      return 2
      ;;
  esac
}

command="${1:-}"
case "$command" in
  --help|-h|help)
    usage
    ;;
  "")
    usage >&2
    exit 2
    ;;
  check)
    shift
    if args_include_help "$@"; then
      case "${1:-}" in
        -h|--help) command_usage check ;;
        *) command_usage check "${1:-}" ;;
      esac
      exit $?
    fi
    run_check "$@"
    ;;
  current)
    shift
    if args_include_help "$@"; then command_usage current; exit $?; fi
    run_current "$@"
    ;;
  heads)
    shift
    if args_include_help "$@"; then command_usage heads; exit $?; fi
    run_heads "$@"
    ;;
  history)
    shift
    if args_include_help "$@"; then command_usage history; exit $?; fi
    run_history "$@"
    ;;
  migrate)
    shift
    if args_include_help "$@"; then command_usage migrate; exit $?; fi
    run_migrate "$@"
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
