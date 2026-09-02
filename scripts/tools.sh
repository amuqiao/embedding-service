#!/usr/bin/env bash
# tools.sh - 本地无副作用工具入口
#
# 运行环境：Bash；secret/env-url/registry 子命令需要 Python 标准库。
# 作用域：提供与服务生命周期、部署、验证和 Job 排障无关的小型开发辅助工具。
# 约束：默认不修改 .env，不写文件，不访问网络；stdout 保持可复制的结果。

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT_DIR/scripts/lib/common.sh"

usage() {
  cat <<EOF
用法：
  ./scripts/tools.sh <command> [args...]
  ./scripts/tools.sh -h|--help

作用域：
  当前仓库的小型本地开发辅助工具入口。只放无默认持久副作用、与服务生命周期无关的工具。
  不负责启动服务、运行验证、部署、Job 查询、真实模型流程或生产运维。

运行环境：
  Requires: Bash
  Dependencies: Python 标准库。

命令：
  secret              生成 URL-safe 随机 secret，适合 SERVICE_API_KEY 这类 Bearer token。
  env-url             生成 DATABASE_URL 或 REDIS_URL，并默认输出解析摘要。
  registry            查看已注册 operation、job_type、workflow、tool 和 job_type tool 关系。
  help                显示帮助。

输出：
  stdout: 子命令结果；secret 只输出生成值；env-url 输出可复制到 .env 的 env 行和注释摘要；registry 输出只读清单。
  stderr: 非法命令、非法参数或缺少依赖。

副作用与保护边界：
  默认不修改 .env，不写文件，不访问网络。
  secret 只生成随机值；env-url 只生成 URL 文本和解析摘要；registry 只读取代码注册事实和应用配置。

常用示例：
  ./scripts/tools.sh secret
  ./scripts/tools.sh secret --prefix prd_
  ./scripts/tools.sh registry
  ./scripts/tools.sh registry --json
  printf '%s' 'raw-password' | ./scripts/tools.sh env-url postgres --username app_user --host postgres.fortress --database app_db --password-stdin
  printf '%s' 'raw-password' | ./scripts/tools.sh env-url redis --host 192.168.0.5 --port 6390 --db 8 --password-stdin
  ./scripts/tools.sh secret -h
  ./scripts/tools.sh env-url -h
  ./scripts/tools.sh registry -h

Exit Codes:
  0  成功
  2  缺少 command、非法命令、非法参数或缺少依赖
EOF
}

registry_usage() {
  cat <<EOF
用法：
  ./scripts/tools.sh registry [--json]
  ./scripts/tools.sh registry -h|--help

说明：
  查看当前代码注册的 operation、job_type、workflow、tool 和 job_type -> tool 关系。
  本命令只调用 registry composition root，不启动 API/worker，不访问网络，不修改 .env。
  composition root 可能读取应用配置；命令输出仍只展示代码注册事实。

选项：
  --json              输出机器可读 JSON。
  -h, --help          显示帮助。

输出：
  stdout: 默认输出人读清单；--json 输出 JSON。
  stderr: 非法参数或缺少 Python。

副作用与保护边界：
  - 本命令不执行 registry consistency 校验；验证请使用 ./scripts/verify.sh check。
  - 本命令不代表 public API，只用于开发者查看代码事实。

常用示例：
  ./scripts/tools.sh registry
  ./scripts/tools.sh registry --json

Exit Codes:
  0  成功
  2  非法参数或缺少 Python
EOF
}

secret_usage() {
  cat <<EOF
用法：
  ./scripts/tools.sh secret [--prefix PREFIX]
  ./scripts/tools.sh secret -h|--help

说明：
  生成一个 URL-safe 随机 secret。当前实现等价于：
    import secrets; print(secrets.token_urlsafe(32))

  secrets.token_urlsafe(32) 会使用 32 字节密码学安全随机数，并编码为 URL-safe token。
  输出适合用作 SERVICE_API_KEY 这类 Authorization: Bearer token。

选项：
  --prefix PREFIX     在生成值前追加 URL-safe 前缀，例如 prd_。
  -h, --help          显示帮助。

输出：
  stdout: 只输出生成后的 secret。
  stderr: 非法参数或缺少 Python。

副作用与保护边界：
  - 本命令不读取或修改 .env。
  - 本命令不访问网络、不写文件。
  - APP_ENV=test/prd 时，SERVICE_API_KEY 必须不是占位值，且长度至少 16 个字符。

常用示例：
  ./scripts/tools.sh secret
  ./scripts/tools.sh secret --prefix prd_

Exit Codes:
  0  成功
  2  非法参数或缺少 Python
EOF
}

env_url_usage() {
  cat <<EOF
用法：
  ./scripts/tools.sh env-url postgres --username USER --host HOST --database DB (--password-stdin | --password PASSWORD) [--port PORT]
  ./scripts/tools.sh env-url redis --host HOST [--password-stdin | --password PASSWORD] [--username USER] [--port PORT] [--db DB]
  ./scripts/tools.sh env-url -h|--help

说明：
  生成本项目 .env 使用的 DATABASE_URL 或 REDIS_URL。
  本命令不读取或修改 .env，不访问网络，只把原始连接参数拼成标准 URL 并输出解析摘要。

固定编码规则：
  PostgreSQL:
    - username: URL encode
    - password: URL encode
    - database name: URL encode
    - host: 不 encode
    - port: 不 encode
  Redis:
    - username: 使用 Redis ACL 时 URL encode
    - password: 配置时 URL encode
    - db: 数字，不 encode
    - host: 不 encode
    - port: 不 encode

输出：
  stdout: 第一行是可复制到 .env 的 DATABASE_URL=... 或 REDIS_URL=...。
          后续解析摘要以 # 开头，复制到 .env 时仍是注释。
          摘要不输出解码后的原始密码，只显示 password_present。
  stderr: 非法参数或缺少 Python。

副作用与保护边界：
  - 生成时始终执行 URL encode，不提供 --no-encode。
  - PostgreSQL 固定输出 async URL：postgresql+asyncpg://...
  - 本命令不输出 SYNC_DATABASE_URL；该值在项目内由代码派生。
  - 推荐使用 --password-stdin，避免密码进入 shell history。
  - Redis 无密码时不要传 --password；使用 Redis ACL username 时必须同时传密码。

常用示例：
  printf '%s' 'raw-password' | ./scripts/tools.sh env-url postgres \\
    --username test_cms_poster_title_user \\
    --host postgres.fortress \\
    --port 5432 \\
    --database test-cms-poster-title \\
    --password-stdin

  printf '%s' 'raw-password' | ./scripts/tools.sh env-url redis \\
    --host 192.168.0.5 \\
    --port 6390 \\
    --db 8 \\
    --password-stdin

Exit Codes:
  0  成功
  2  非法参数或缺少 Python
EOF
}

run_env_url() {
  local python_bin

  if [[ $# -eq 0 ]]; then
    env_url_usage >&2
    return 2
  fi

  case "${1:-}" in
    -h|--help)
      env_url_usage
      return 0
      ;;
  esac

  python_bin="$(resolve_python_bin)"
  "$python_bin" "$ROOT_DIR/scripts/tools/env_url.py" "$@"
}

run_secret() {
  local prefix=""
  local python_bin

  while [[ $# -gt 0 ]]; do
    case "$1" in
      -h|--help)
        secret_usage
        return 0
        ;;
      --prefix)
        [[ $# -ge 2 ]] || die "--prefix requires a value" 2
        prefix="$2"
        shift 2
        ;;
      --prefix=*)
        prefix="${1#--prefix=}"
        shift
        ;;
      *)
        secret_usage >&2
        exit 2
        ;;
    esac
  done

  [[ "$prefix" =~ ^[A-Za-z0-9_-]*$ ]] || die "--prefix must contain only URL-safe characters: A-Z a-z 0-9 _ -" 2
  python_bin="$(resolve_python_bin)"
  TOOLS_SECRET_PREFIX="$prefix" "$python_bin" -c 'import os, secrets; print(os.environ["TOOLS_SECRET_PREFIX"] + secrets.token_urlsafe(32))'
}

run_registry() {
  local python_bin

  case "${1:-}" in
    -h|--help)
      registry_usage
      return 0
      ;;
  esac

  python_bin="$(resolve_python_bin)"
  "$python_bin" "$ROOT_DIR/scripts/tools/registry.py" "$@"
}

resolve_python_bin() {
  project_python_bin
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
  secret)
    shift
    run_secret "$@"
    ;;
  env-url)
    shift
    run_env_url "$@"
    ;;
  registry)
    shift
    run_registry "$@"
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
