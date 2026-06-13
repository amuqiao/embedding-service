#!/usr/bin/env bash
# deploy.sh - cms-novel-localize 部署形态入口
#
# 维护的 3 种模式：
#   local         宿主机运行 api/worker，docker compose 只提供 postgres/redis；入口是 scripts/dev.sh。
#   compose-deps docker compose 只管理 postgres/redis 依赖服务。
#   compose-full docker compose 管理 api/worker/postgres/redis，并在应用启动前执行 Alembic 迁移。

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-.env}"
PROJECT_NAME="${COMPOSE_PROJECT_NAME:-cms-novel-localize}"

cd "$ROOT_DIR"

section() {
  printf "\n== %s ==\n" "$1"
}

event() {
  printf "%-9s %-14s %s\n" "$1" "$2" "${3:-}"
}

die() {
  printf "ERROR: %s\n" "$1" >&2
  exit "${2:-1}"
}

usage() {
  cat <<EOF
用法：
  ./scripts/deploy.sh <command> [mode]

命令：
  modes                 展示本项目维护的 3 种部署模式。
  check                 校验部署入口、Dockerfile 和 compose 配置。
  up compose-deps       启动 PostgreSQL / Redis 依赖服务。
  down compose-deps     停止 PostgreSQL / Redis 依赖服务。
  status compose-deps   查看依赖服务状态。
  up compose-full       使用 compose 启动 api / worker / PostgreSQL / Redis。
  down compose-full     停止 compose 全量服务。
  status compose-full   查看 compose 全量服务状态。

配置加载优先级：
  运行时显式环境变量 > docker-compose.yml environment > ENV_FILE 指定文件 > .env > 应用默认值

边界：
  local 模式继续使用 ./scripts/dev.sh；本脚本不管理生产部署、远程数据库、K8s 或云平台资源。
EOF
}

compose() {
  if docker compose version >/dev/null 2>&1; then
    ENV_FILE="$ENV_FILE" COMPOSE_PROJECT_NAME="$PROJECT_NAME" docker compose "$@"
    return
  fi
  if command -v docker-compose >/dev/null 2>&1; then
    ENV_FILE="$ENV_FILE" COMPOSE_PROJECT_NAME="$PROJECT_NAME" docker-compose "$@"
    return
  fi
  die "Docker Compose is not available. Install Docker Desktop or docker-compose." 2
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
  event "MODE" "local" "api/worker 跑宿主机，postgres/redis 由 compose 提供；入口：./scripts/dev.sh"
  event "MODE" "compose-deps" "只启动 postgres/redis；适合给本地应用进程提供依赖"
  event "MODE" "compose-full" "api/worker/postgres/redis 全部由 compose 管理；启动前执行 Alembic 迁移"
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

  section "Compose Config"
  ENV_FILE=.env.example compose config --quiet
  event "OK" "compose-deps" "docker compose config"
  ENV_FILE=.env.example compose --profile app config --quiet
  event "OK" "compose-full" "docker compose --profile app config"

  section "Scripts"
  bash -n "$ROOT_DIR/scripts/dev.sh"
  event "OK" "dev.sh" "syntax"
  bash -n "$ROOT_DIR/scripts/deploy.sh"
  event "OK" "deploy.sh" "syntax"
  sh -n "$ROOT_DIR/start-api.sh"
  event "OK" "start-api.sh" "syntax"
  sh -n "$ROOT_DIR/start-worker.sh"
  event "OK" "start-worker.sh" "syntax"
}

up_deps() {
  section "Compose Deps"
  compose up -d postgres redis
}

down_deps() {
  section "Compose Deps"
  compose stop postgres redis
}

status_deps() {
  section "Compose Deps"
  compose ps postgres redis
}

up_full() {
  require_env_file
  section "Compose Full"
  compose --profile app up -d --build api worker
}

down_full() {
  section "Compose Full"
  compose --profile app stop api worker postgres redis
}

status_full() {
  section "Compose Full"
  compose --profile app ps
}

command="${1:-help}"
mode="${2:-}"

case "$command" in
  --help|-h|help)
    usage
    ;;
  modes)
    show_modes
    ;;
  check)
    check_deploy
    ;;
  up)
    case "$mode" in
      compose-deps) up_deps ;;
      compose-full) up_full ;;
      *) die "up requires mode: compose-deps or compose-full" 2 ;;
    esac
    ;;
  down)
    case "$mode" in
      compose-deps) down_deps ;;
      compose-full) down_full ;;
      *) die "down requires mode: compose-deps or compose-full" 2 ;;
    esac
    ;;
  status)
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
