#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

RELEASE_NAME="release-master"
REQUIRED_LOCAL_BRANCH="test"
SOURCE_REF="${SOURCE_REF:-origin/test}"
TARGET_BRANCH="${TARGET_BRANCH:-master}"
TARGET_ENV_LABEL="生产环境"
RELEASE_TMP_NAME="release-master"
RELEASE_TMP_DIR="${RELEASE_TMP_DIR:-}"
BUILD_MODE="${BUILD_MODE:-ci}"

# 合并 test 到 master 时，需要保留 master 分支版本的发版文件。
PROTECTED_FILES=(".gitlab-ci.yml" "Dockerfile" "Dockerfile_OS")

# 生产发布同样提示构建风险文件，由人工确认是否符合预期。
RISK_FILES=(".dockerignore" ".gitignore" "pyproject.toml" "uv.lock" ".python-version")

CI_IMAGE_NEXT_STEP="拿到代码镜像版本后，在生产环境 Kuboard 调整本项目初始化容器的新版本；不要改工作容器 OS 镜像。"
OS_IMAGE_NEXT_STEP="拿到运行环境镜像版本后，由运维确认是否调整生产环境工作容器的 OS-* 新版本。"

source "$ROOT_DIR/deploy/lib/release-flow.sh"

release_flow_main "$@"
