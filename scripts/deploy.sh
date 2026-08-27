#!/usr/bin/env bash
# deploy.sh - fastapi-best-ai-architecture 部署形态入口
#
# 运行环境：Bash；需要 Docker Compose。
# 作用域：只管理 docker compose 部署形态。
#   compose-deps docker compose 只管理 postgres/redis 依赖服务。
#   compose-full docker compose 管理 api/worker/postgres/redis，并在应用启动前执行 Alembic 迁移。
# local 本地服务生命周期由 scripts/dev.sh 管理，不属于本入口命令面。
# 输出：check 汇总为稳定 OK 事件；up/down/status 先打印部署阶段，再透传 compose 输出。

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-.env}"
source "$ROOT_DIR/scripts/lib/common.sh"
source "$ROOT_DIR/scripts/lib/compose.sh"
source "$ROOT_DIR/scripts/lib/modes.sh"

cd "$ROOT_DIR"

usage() {
  cat <<EOF
用法：
  ./scripts/deploy.sh <command> [mode]
  ./scripts/deploy.sh -h|--help

作用域：
  本脚本只管理 compose-deps / compose-full。
  不管理 local 本地服务生命周期、一次性验证任务、生产部署、远程数据库、K8s 或云平台资源。

运行环境：
  Requires: Bash
  Dependencies: Docker Compose；check / up 的 project 名冲突检查需要 Docker CLI 读取容器 label。

命令：
  modes                 展示本脚本管理的 compose 部署模式。
  check                 校验部署入口、Dockerfile、compose 配置和 compose project 名冲突。
  up compose-deps       启动 PostgreSQL / Redis 依赖服务。
  down compose-deps     停止 PostgreSQL / Redis 依赖服务。
  status compose-deps   查看依赖服务状态。
  up compose-full       使用 compose 启动 api / worker / PostgreSQL / Redis。
  down compose-full     停止 compose 全量服务。
  status compose-full   查看 compose 全量服务状态。

配置与环境变量：
  加载优先级：运行时显式环境变量 > docker-compose.yml environment > ENV_FILE 指定文件 > .env > 应用默认值。
  ENV_FILE                 可选，指定 compose 使用的 env 文件，默认 .env。
  COMPOSE_PROJECT_NAME     可选，覆盖 compose project 名。
  TEMPLATE_NAME            可选，作为默认 compose project 名来源。

输出：
  stdout: check 结果、compose 状态、启动/停止结果。
  stderr: 缺少文件、非法 mode、Docker Compose / Docker CLI 错误或 project 名冲突。

副作用与保护边界：
  check 不启动服务；会通过 Docker CLI 读取 Docker compose 容器 label 检查 project 名是否被其他目录占用。
  up 会先检查 ENV_FILE 和 project 名冲突，再创建或更新 compose 服务。
  down 使用 compose stop，停止服务但不删除 volume。
  compose-full 会拒绝与 ./scripts/dev.sh 管理的本地 api/worker 混跑。

常用示例：
  ./scripts/deploy.sh check
  ./scripts/deploy.sh modes
  ./scripts/deploy.sh up compose-deps
  ./scripts/deploy.sh status compose-full

Exit Codes:
  0  成功
  2  缺少 command、非法 mode、缺少必要文件或 Docker Compose 不可用
  4  compose project 名已被其他目录占用
EOF
}

command_usage() {
  local name="$1"
  case "$name" in
    modes)
      cat <<EOF
用法：
  ./scripts/deploy.sh modes
  ./scripts/deploy.sh modes -h|--help

作用域：
  展示 deploy.sh 管理的 compose 部署模式。

常用示例：
  ./scripts/deploy.sh modes
EOF
      ;;
    check)
      cat <<EOF
用法：
  ./scripts/deploy.sh check
  ./scripts/deploy.sh check -h|--help

作用域：
  校验部署入口、Dockerfile、compose 配置和 compose project 名冲突。

副作用与保护边界：
  不启动服务；会通过 Docker CLI 读取 compose 容器 label。

常用示例：
  ./scripts/deploy.sh check
EOF
      ;;
    up|down|status)
      local effect
      case "$name" in
        up) effect="创建或更新 docker compose 服务。" ;;
        down) effect="使用 compose stop 停止服务但不删除 volume。" ;;
        status) effect="只读查看 compose 服务状态。" ;;
      esac
      cat <<EOF
用法：
  ./scripts/deploy.sh ${name} <compose-deps|compose-full>
  ./scripts/deploy.sh ${name} -h|--help

作用域：
  ${effect}

配置与环境变量：
  ENV_FILE 默认 .env。
  COMPOSE_PROJECT_NAME 可覆盖 compose project 名。

副作用与保护边界：
  compose-full 会拒绝与 ./scripts/dev.sh 管理的本地 api/worker 混跑。

常用示例：
  ./scripts/deploy.sh ${name} compose-deps
  ./scripts/deploy.sh ${name} compose-full
EOF
      ;;
    *)
      usage >&2
      return 2
      ;;
  esac
}

require_file() {
  local path="$1"
  [[ -f "$path" ]] || die "$path not found" 2
}

require_env_file() {
  require_file "$ENV_FILE"
}

show_modes() {
  section "Deployment Modes"
  event "MODE" "compose-deps" "只启动 postgres/redis；适合给本地应用进程提供依赖"
  event "MODE" "compose-full" "api/worker/postgres/redis 全部由 compose 管理；启动前执行 Alembic 迁移"
  event "INFO" "local" "不由 deploy.sh 管理；入口：./scripts/dev.sh"
}

check_deploy() {
  section "Files"
  require_file "Dockerfile"
  event "OK" "Dockerfile" "present"
  require_file ".dockerignore"
  event "OK" ".dockerignore" "present"
  require_file "docker-compose.yml"
  event "OK" "compose" "present"
  require_file ".env.example"
  event "OK" ".env.example" "present"
  require_file "start-api.sh"
  event "OK" "start-api.sh" "present"
  require_file "start-worker.sh"
  event "OK" "start-worker.sh" "present"
  require_file "start-worker-bundle.sh"
  event "OK" "start-worker-bundle.sh" "present"
  require_file "start-dispatcher.sh"
  event "OK" "start-dispatcher.sh" "present"
  require_file "start-callbacker.sh"
  event "OK" "start-callbacker.sh" "present"
  require_file "start-reconciler.sh"
  event "OK" "start-reconciler.sh" "present"
  require_file "scripts/lib/modes.sh"
  event "OK" "modes.sh" "present"

  section "Compose Config"
  ENV_FILE=.env.example compose config --quiet
  event "OK" "compose-deps" "docker compose config"
  ENV_FILE=.env.example compose --profile app config --quiet
  event "OK" "compose-full" "docker compose --profile app config"
  ENV_FILE=.env.example compose --profile roles config --quiet
  event "OK" "compose-roles" "docker compose --profile roles config"
  assert_no_compose_project_name_conflict
  event "OK" "compose-project" "no working_dir conflict"

  section "Scripts"
  bash -n "$ROOT_DIR/scripts/run.sh"
  event "OK" "run.sh" "syntax"
  bash -n "$ROOT_DIR/scripts/deploy.sh"
  event "OK" "deploy.sh" "syntax"
  bash -n "$ROOT_DIR/scripts/lib/modes.sh"
  event "OK" "modes.sh" "syntax"
  sh -n "$ROOT_DIR/start-api.sh"
  event "OK" "start-api.sh" "syntax"
  sh -n "$ROOT_DIR/start-worker.sh"
  event "OK" "start-worker.sh" "syntax"
  bash -n "$ROOT_DIR/start-worker-bundle.sh"
  event "OK" "start-worker-bundle.sh" "syntax"
  sh -n "$ROOT_DIR/start-dispatcher.sh"
  event "OK" "start-dispatcher.sh" "syntax"
  sh -n "$ROOT_DIR/start-callbacker.sh"
  event "OK" "start-callbacker.sh" "syntax"
  sh -n "$ROOT_DIR/start-reconciler.sh"
  event "OK" "start-reconciler.sh" "syntax"
}

up_deps() {
  require_env_file
  assert_no_compose_project_name_conflict
  section "Compose Deps"
  compose up -d postgres redis
  wait_for_compose_service_health postgres 90
  wait_for_compose_service_health redis 60
}

down_deps() {
  section "Compose Deps"
  compose stop postgres redis
}

status_deps() {
  section "Compose Deps"
  compose ps postgres redis
}

wait_for_compose_service_health() {
  local service="$1"
  local timeout_seconds="$2"
  local elapsed=0
  local line
  local health
  local state

  while true; do
    line="$(compose ps "$service" --format '{{.State}}|{{.Health}}' 2>/dev/null || true)"
    IFS='|' read -r state health <<< "$line"
    if [[ "$health" == "healthy" ]]; then
      event "READY" "$service" "healthy"
      return 0
    fi

    if (( elapsed >= timeout_seconds )); then
      compose ps "$service" >&2 || true
      die "$service did not become healthy within ${timeout_seconds}s; state=${state:-unknown} health=${health:-unknown}" 4
    fi

    sleep 2
    elapsed=$((elapsed + 2))
  done
}

up_full() {
  require_env_file
  assert_no_compose_project_name_conflict
  assert_no_local_app_running_for_compose_full
  section "Compose Full"
  compose --profile app up -d --build api worker
}

down_full() {
  section "Compose Full"
  compose --profile app stop api worker postgres redis
}

status_full() {
  warn_if_local_app_running
  section "Compose Full"
  compose --profile app ps
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
  modes)
    shift
    if args_include_help "$@"; then command_usage "$command"; exit $?; fi
    show_modes
    ;;
  check)
    shift
    if args_include_help "$@"; then command_usage "$command"; exit $?; fi
    check_deploy
    ;;
  up)
    shift
    if args_include_help "$@"; then command_usage "$command"; exit $?; fi
    mode="${1:-}"
    case "$mode" in
      compose-deps) up_deps ;;
      compose-full) up_full ;;
      *) die "up requires mode: compose-deps or compose-full" 2 ;;
    esac
    ;;
  down)
    shift
    if args_include_help "$@"; then command_usage "$command"; exit $?; fi
    mode="${1:-}"
    case "$mode" in
      compose-deps) down_deps ;;
      compose-full) down_full ;;
      *) die "down requires mode: compose-deps or compose-full" 2 ;;
    esac
    ;;
  status)
    shift
    if args_include_help "$@"; then command_usage "$command"; exit $?; fi
    mode="${1:-}"
    case "$mode" in
      compose-deps) status_deps ;;
      compose-full) status_full ;;
      *) die "status requires mode: compose-deps or compose-full" 2 ;;
    esac
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
