#!/usr/bin/env bash
# tools.sh - 本地无副作用工具入口
#
# 运行环境：Bash；secret 子命令需要 Python 标准库。
# 作用域：提供与服务生命周期、部署、验证和 Job 排障无关的小型开发辅助工具。
# 约束：默认不读取 .env，不写文件，不访问网络；stdout 保持可复制的结果。

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

命令：
  secret              生成 URL-safe 随机 secret，适合 SERVICE_API_KEY 这类 Bearer token。
  help                显示帮助。

输出：
  stdout: 子命令结果；secret 只输出生成值，方便复制到 .env 或 Secret Manager。
  stderr: 非法命令、非法参数或缺少依赖。

常用示例：
  ./scripts/tools.sh secret
  ./scripts/tools.sh secret --prefix prd_
  ./scripts/tools.sh secret -h

Exit Codes:
  0  成功
  2  缺少 command、非法命令、非法参数或缺少依赖
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

注意：
  - 本命令不读取或修改 .env。
  - 本命令不访问网络、不写文件。
  - APP_ENV=test/prd 时，SERVICE_API_KEY 必须不是占位值，且长度至少 16 个字符。

示例：
  ./scripts/tools.sh secret
  ./scripts/tools.sh secret --prefix prd_

Exit Codes:
  0  成功
  2  非法参数或缺少 Python
EOF
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

resolve_python_bin() {
  if [[ -n "${PYTHON_BIN:-}" ]]; then
    require_command "$PYTHON_BIN" "install Python 3 or set PYTHON_BIN"
    printf "%s" "$PYTHON_BIN"
  elif [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
    printf "%s" "$ROOT_DIR/.venv/bin/python"
  elif command -v python3 >/dev/null 2>&1; then
    command -v python3
  elif command -v python >/dev/null 2>&1; then
    command -v python
  else
    die "python is not available; run: ./scripts/dev.sh bootstrap" 2
  fi
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
  *)
    usage >&2
    exit 2
    ;;
esac
