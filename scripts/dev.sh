#!/usr/bin/env bash
# dev.sh - 本地开发服务入口
#
# 作用域：只管理当前仓库的本地 FastAPI/Taskiq 服务和 docker compose 本地依赖。
# 验证、smoke、e2e 等一次性检查不属于本入口。
# 约束：入口脚本只做参数分发和帮助说明，具体实现下沉到 scripts/dev/ 原子脚本。

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT_DIR/scripts/dev/services.sh"

usage() {
  cat <<EOF
用法：
  ./scripts/dev.sh <command> [service]
  ./scripts/dev.sh --help

作用域：
  当前仓库的本地服务入口。管理 FastAPI API、Taskiq worker，以及 docker compose 中的 postgres/redis 本地依赖。
  不负责部署、验证任务、生产运维、数据库重置、远程资源或其他仓库。

服务：
  api       FastAPI 服务，URL: ${API_URL}，文档: ${API_DOCS_URL}，OpenAPI: ${API_OPENAPI_URL}，健康检查: ${API_HEALTH_URL}
            本地默认使用 uvicorn --reload 热更新；如需旧启动路径，设置 DEV_API_RELOAD=false。
  worker    Taskiq worker，处理 jobs.run_attempt 异步任务
            worker 通过 PostgreSQL attempt lease 获取执行权。
            worker 代码变更后使用 ./scripts/dev.sh restart worker。

命令：
  bootstrap           缺少 .env 时从 .env.example 创建，并执行 uv sync。
  start [service]     启动服务；不传 service 时启动依赖、执行迁移、启动 api 和 worker。
  stop [service]      停止服务；不传 service 时停止 api、worker、postgres 和 redis。
  restart [service]   重启服务；不传 service 时重启完整本地服务栈。
  status [service]    查看状态；不传 service 时展示依赖、api、worker 和健康检查。
  logs <service>      跟随查看 api 或 worker 日志，Ctrl-C 退出。
  migrate             对本地开发数据库执行 Alembic 迁移。
  ports [port ...]    扫描本地可用端口，支持 --ports、端口范围和 --format json。
  help                显示帮助。

成功标准：
  start 成功 = postgres/redis healthy，迁移成功，api/worker 进程存活，/health 可访问。

运行产物：
  PID:  ${RUN_DIR}/api.pid, ${RUN_DIR}/worker.pid
  日志: ${LOG_DIR}/api.log, ${LOG_DIR}/worker.log

保护边界：
  生命周期和迁移动作会拒绝非本地 DATABASE_URL / REDIS_URL。
  未知 service 会直接报错。
  启动 api 前会检查端口 ${API_PORT} 是否已被其他进程占用。
EOF
}

command="${1:-help}"
case "$command" in
  --help|-h|help)
    usage
    ;;
  bootstrap)
    bootstrap
    ;;
  start)
    start_target "${2:-}"
    ;;
  stop)
    stop_target "${2:-}"
    ;;
  restart)
    restart_target "${2:-}"
    ;;
  status)
    status_target "${2:-}"
    ;;
  logs)
    follow_logs "${2:-}"
    ;;
  migrate)
    migrate
    ;;
  ports)
    shift
    scan_ports "$@"
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
