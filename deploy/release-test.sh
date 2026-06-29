#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

RELEASE_NAME="release-test"
REQUIRED_LOCAL_BRANCH="dev"
SOURCE_REF="${SOURCE_REF:-origin/dev}"
TARGET_BRANCH="${TARGET_BRANCH:-test}"
TARGET_ENV_LABEL="测试环境"
RELEASE_TMP_NAME="release-test"
RELEASE_TMP_DIR="${RELEASE_TMP_DIR:-}"
BUILD_MODE="${BUILD_MODE:-ci}"

# 合并 dev 到 test 时，需要保留 test 分支版本的发版文件。
PROTECTED_FILES=(".gitlab-ci.yml" "Dockerfile" "Dockerfile_OS")

# 这些文件变更会影响构建上下文、依赖安装或 Git 跟踪范围；脚本只提示，不自动恢复。
RISK_FILES=(".dockerignore" ".gitignore" "pyproject.toml" "uv.lock" ".python-version")

project_name="${PROJECT_NAME:-$(basename "$ROOT_DIR")}"
CI_IMAGE_NEXT_STEP="拿到代码镜像版本后，在 Kuboard 调整“初始化容器 test-${project_name}”的新版本；不要改工作容器 OS 镜像。"
OS_IMAGE_NEXT_STEP="拿到运行环境镜像版本后，由运维确认是否调整“工作容器 os-${project_name}”的 OS-* 新版本。"

source "$ROOT_DIR/deploy/lib/release-flow.sh"

release_flow_main "$@"
