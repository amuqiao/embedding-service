#!/usr/bin/env bash
set -euo pipefail

# 让脚本在执行过程中即使切换 Git 分支，也不会因为当前分支没有该脚本而中断。
if [[ -z "${RELEASE_MASTER_BOOTSTRAPPED:-}" && -f "${BASH_SOURCE[0]}" ]]; then
  tmp_script="${TMPDIR:-/tmp}/release-master.$$.$RANDOM.sh"
  cp "${BASH_SOURCE[0]}" "$tmp_script"
  chmod +x "$tmp_script"
  RELEASE_MASTER_BOOTSTRAPPED=1 exec bash "$tmp_script" "$@"
fi

###############################################################################
# 适用前提
#
# 本脚本适合迁移到采用以下发布约定的项目：
# - 分支流向固定为 dev -> test -> master。
# - 远端仓库名固定为 origin。
# - GitLab CI 通过提交信息中的构建标记触发镜像构建。
# - .gitlab-ci.yml、Dockerfile、Dockerfile_OS 属于目标发布分支维护的发版控制文件，
#   合并时需要保留目标分支版本。
###############################################################################

###############################################################################
# 命令速查
#
# 这几条命令都在主项目 test 目录执行。脚本内部会进入 tmp 发布目录处理 master。
#
# 推荐流程：
#   1. ./scripts/deploy/release-master.sh          # test 目录发起；tmp 副本准备 master 合并
#   2. ./scripts/deploy/release-master.sh status   # 同时查看 test 目录和 tmp 发布目录
#   3. ./scripts/deploy/release-master.sh --push   # test 目录发起；tmp 副本提交并推送 master
#
# 所有命令：
#   ./scripts/deploy/release-master.sh             # 等同于 prepare
#   ./scripts/deploy/release-master.sh prepare     # 准备生产发布
#   ./scripts/deploy/release-master.sh --push      # 提交并推送，推荐第二次执行使用
#   ./scripts/deploy/release-master.sh push        # --push 的等价写法
#   ./scripts/deploy/release-master.sh -p          # --push 的短选项
#   ./scripts/deploy/release-master.sh status      # 查看状态和发布判断
#   ./scripts/deploy/release-master.sh --help      # 查看完整帮助
#
# 如果默认执行有问题，脚本会输出“告警”和“下一步”。不要跳过告警直接 push。
###############################################################################

###############################################################################
# 维护者心智模型
#
# 生产环境发布链路：
#   test 代码 -> 合入 master -> CI 构建镜像 -> Kuboard 手动切镜像 -> 验证服务
#
# 本脚本只负责前两步 Git 流程。它不负责 Kuboard 切镜像，也不判断业务功能
# 是否正确。为避免影响日常 test 工作区，脚本会把主项目复制到 RELEASE_TMP_DIR
# 指向的 tmp 发布目录，再在副本里切换 master、merge 和 push。tmp 发布目录出问题
# 可以删除重建；主项目目录不会被切换到 master，也不会进入 merge 状态。
#
# 目录职责：
# - 主项目目录：只允许在 test 分支执行；只做状态检查和复制来源。
# - tmp 发布目录：允许切换 master、merge origin/test、commit 和 push origin master。
#
# 文件分层：
# - 发版控制文件：.gitlab-ci.yml、Dockerfile、Dockerfile_OS
#   决定 CI 和镜像构建方式，脚本默认保留 master 分支版本。
# - 业务代码文件：main.py、detectors/*、config.py、auth.py 等
#   应该已经在 test 环境验证通过；本脚本只负责推进到 master。
###############################################################################

###############################################################################
# 可修改配置区
#
# 常规发布只需要改这里，或在命令前用环境变量临时覆盖。
# 示例：
#   SOURCE_REF=origin/feature-x ./scripts/deploy/release-master.sh
#   BUILD_MODE=os ./scripts/deploy/release-master.sh --push   # 通常仅运维需要
#   COMMIT_MESSAGE="[ci build] release tested changes to master" ./scripts/deploy/release-master.sh --push
###############################################################################

# 默认动作：prepare 只准备发布，不提交、不推送。
ACTION="${1:-prepare}"
MAIN_REPO_ROOT=""

# 要合入生产环境的来源分支。
SOURCE_REF="${SOURCE_REF:-origin/test}"

# 生产环境发版分支。
TARGET_BRANCH="${TARGET_BRANCH:-master}"

# tmp 发布目录。默认放在当前仓库同级 tmp 目录下，避免影响日常 test 工作区。
# 未显式设置时，根据当前仓库目录名生成：../tmp/<repo-name>-release-master。
RELEASE_TMP_DIR="${RELEASE_TMP_DIR:-}"

# 镜像构建方式：
#   ci：使用 [ci build]，生成代码镜像。开发默认只负责这个。
#   os：使用 [build:os]，生成运行环境镜像。通常由运维维护。
BUILD_MODE="${BUILD_MODE:-ci}"

# 合并 test 到 master 时，需要保留 master 分支版本的发版文件。
# Dockerfile 是代码镜像构建入口，Dockerfile_OS 是运行环境镜像构建入口；
# 二者都属于 master 分支发布配置，不用 test 分支版本覆盖。
PROTECTED_FILES=(".gitlab-ci.yml" "Dockerfile" "Dockerfile_OS")

log() {
  printf '[release-master] %s\n' "$*"
}

die() {
  printf '[release-master] 错误：%s\n' "$*" >&2
  exit 1
}

usage() {
  cat <<'EOF'
用法：
  ./scripts/deploy/release-master.sh [prepare|push|--push|status]

执行位置：
  建议始终在主项目 test 目录执行。本脚本会自动进入 tmp 发布目录处理 master。

默认动作：
  prepare

动作说明：
  prepare  检查主 test，复制到 tmp，在 tmp 副本切到 master 并合入 origin/test。
  push     进入 tmp 副本，提交 prepare 产生的待发布 merge，并推送到 origin/master 触发 CI。
  --push   push 的别名，适合第二次执行时直接添加选项。
  status   同时输出主 test 目录、tmp 发布目录状态，并给出 prepare/push 判断。

关键配置：
  SOURCE_REF       要合入生产环境的来源分支，默认：origin/test
  TARGET_BRANCH   生产环境发版分支，默认：master
  RELEASE_TMP_DIR  tmp 发布目录，默认：../tmp/<repo-name>-release-master
  BUILD_MODE      构建方式，ci 或 os，默认：ci
                   ci -> [ci build]，生成代码镜像，开发默认使用
                   os -> [build:os]，生成运行环境镜像，通常由运维使用
  COMMIT_MESSAGE  push 阶段使用的提交信息，必须包含对应构建标记。

脚本内维护的文件分组：
  PROTECTED_FILES 合并时自动保留 master 版本的发版控制文件。

示例：
  ./scripts/deploy/release-master.sh
  ./scripts/deploy/release-master.sh prepare
  ./scripts/deploy/release-master.sh push
  ./scripts/deploy/release-master.sh --push
  BUILD_MODE=os ./scripts/deploy/release-master.sh --push
  COMMIT_MESSAGE="[ci build] release tested changes to master" ./scripts/deploy/release-master.sh --push
EOF
}

run() {
  log "+ $*"
  "$@"
}

log_boundary() {
  printf '[release-master] ---- %s ----\n' "$*"
}

warn() {
  printf '[release-master] 告警：%s\n' "$*" >&2
}

next_step() {
  printf '[release-master] 下一步：%s\n' "$*"
}

ok() {
  printf '[release-master] OK：%s\n' "$*"
}

stop_with_next_step() {
  local message="$1"
  local suggestion="$2"

  warn "$message"
  next_step "$suggestion"
  exit 1
}

repo_root() {
  git rev-parse --show-toplevel 2>/dev/null
}

ensure_main_repo() {
  local root
  if [[ -n "$MAIN_REPO_ROOT" ]]; then
    cd "$MAIN_REPO_ROOT"
    return
  fi

  root="$(repo_root)" || die "当前目录不在 Git 仓库中"
  cd "$root"
  MAIN_REPO_ROOT="$root"
}

release_tmp_path() {
  local release_dir
  release_dir="${RELEASE_TMP_DIR:-../tmp/$(basename "$(pwd)")-release-master}"

  case "$release_dir" in
    /*) printf '%s\n' "$release_dir" ;;
    *) printf '%s/%s\n' "$(pwd)" "$release_dir" ;;
  esac
}

ensure_main_ready_for_prepare() {
  local branch
  branch="$(git branch --show-current)"
  log_boundary "检查主项目 test 目录"
  log "当前执行目录：$(pwd)"
  log "当前分支：${branch}"
  log "说明：主项目目录只做检查和复制来源，不会切到 ${TARGET_BRANCH}。"

  [[ "$branch" == "test" ]] ||
    stop_with_next_step "当前主目录必须在 test 分支，实际是：${branch}" "切回 test 后重新执行：git switch test && ./scripts/deploy/release-master.sh"

  if [[ -n "$(git status --porcelain)" ]]; then
    warn "主 test 工作区不干净，prepare 不会继续执行。"
    git status --short
    next_step "先提交或清理 test 工作区改动，然后重新执行：./scripts/deploy/release-master.sh"
    exit 1
  fi

  run git fetch origin
  ensure_remote_branch_exists "${SOURCE_REF}"
  ensure_remote_branch_exists "origin/${TARGET_BRANCH}"

  if [[ "$SOURCE_REF" == "origin/test" ]] &&
    git show-ref --verify --quiet refs/heads/test &&
    git show-ref --verify --quiet refs/remotes/origin/test; then
    local ahead
    local behind
    ahead="$(git rev-list --count origin/test..test)"
    behind="$(git rev-list --count test..origin/test)"
    if [[ "$ahead" != "0" ]]; then
      stop_with_next_step "本地 test 领先 origin/test ${ahead} 个提交；脚本默认发布 origin/test，不会包含这些本地提交。" "先执行：git push origin test；然后重新执行：./scripts/deploy/release-master.sh"
    fi
    if [[ "$behind" != "0" ]]; then
      stop_with_next_step "本地 test 落后 origin/test ${behind} 个提交；脚本不会在旧 test 状态下发布。" "先同步 test 后重新执行，例如：git pull --ff-only origin test"
    fi
  fi
}

ensure_remote_branch_exists() {
  local ref="$1"
  git rev-parse --verify --quiet "$ref" >/dev/null || die "找不到分支或引用：${ref}"
}

ensure_build_mode() {
  case "$BUILD_MODE" in
    os|ci) ;;
    *) die "BUILD_MODE 只能是 os 或 ci，当前值：${BUILD_MODE}" ;;
  esac

  if [[ "$BUILD_MODE" == "os" ]]; then
    warn "当前 BUILD_MODE=os，会生成运行环境镜像 [build:os]；通常这部分由运维维护。开发发代码请使用默认 BUILD_MODE=ci。"
  fi
}

build_marker() {
  case "$BUILD_MODE" in
    os) printf '[build:os]' ;;
    ci) printf '[ci build]' ;;
  esac
}

default_commit_message() {
  printf '%s release %s to %s' "$(build_marker)" "${SOURCE_REF}" "${TARGET_BRANCH}"
}

release_tmp_source_meta() {
  git rev-parse --git-path release-master-source-sha
}

has_pending_merge() {
  [[ -f "$(git rev-parse --git-path MERGE_HEAD)" ]]
}

has_unresolved_conflicts() {
  [[ -n "$(git diff --name-only --diff-filter=U)" ]]
}

has_tracked_changes() {
  [[ -n "$(git status --porcelain --untracked-files=no)" ]]
}

main_readiness_reason() {
  local branch ahead behind

  branch="$(git branch --show-current)"
  if [[ "$branch" != "test" ]]; then
    printf '主项目目录必须在 test 分支，当前是 %s' "$branch"
    return
  fi

  if [[ -n "$(git status --porcelain)" ]]; then
    printf '主项目 test 工作区不干净'
    return
  fi

  if [[ "$SOURCE_REF" == "origin/test" ]] &&
    git show-ref --verify --quiet refs/heads/test &&
    git show-ref --verify --quiet refs/remotes/origin/test; then
    ahead="$(git rev-list --count origin/test..test)"
    behind="$(git rev-list --count test..origin/test)"
    if [[ "$ahead" != "0" ]]; then
      printf '本地 test 领先 origin/test %s 个提交，需要先 git push origin test' "$ahead"
      return
    fi
    if [[ "$behind" != "0" ]]; then
      printf '本地 test 落后 origin/test %s 个提交，需要先同步 test' "$behind"
      return
    fi
  fi
}

assert_release_tmp_safe() {
  local main_root="$1"
  local release_root="$2"
  local main_real
  local release_parent
  local release_real
  local release_base
  local release_parent_base

  main_real="$(cd "$main_root" && pwd -P)"
  release_parent="$(dirname "$release_root")"
  release_base="$(basename "$release_root")"
  release_parent_base="$(basename "$release_parent")"

  [[ -n "$release_root" ]] || die "tmp 发布目录为空"
  [[ "$release_root" != "/" ]] || die "tmp 发布目录不能是根目录"
  [[ "$release_base" == *"release-master"* ]] || die "tmp 发布目录名称必须包含 release-master：${release_root}"
  [[ "$release_parent_base" == "tmp" ]] || die "tmp 发布目录必须放在 tmp 目录下：${release_root}"

  if [[ -d "$release_parent" ]]; then
    release_real="$(cd "$release_parent" && pwd -P)/${release_base}"
    [[ "$release_real" != "$main_real" ]] || die "tmp 发布目录不能等于主项目目录：${release_real}"
    case "${release_real}/" in
      "${main_real}/"*) die "tmp 发布目录不能放在主项目目录内部：${release_real}" ;;
    esac
  fi
}

prepare_release_copy() {
  local main_root release_root release_parent

  ensure_main_repo
  main_root="$(pwd)"
  release_root="$(release_tmp_path)"
  release_parent="$(dirname "$release_root")"

  log_boundary "准备 tmp 发布目录"
  log "主 test 目录：${main_root}"
  log "tmp 发布目录：${release_root}"
  log "说明：下面的切分支、merge、commit、push 都只发生在 tmp 发布目录。"

  assert_release_tmp_safe "$main_root" "$release_root"

  run mkdir -p "$release_parent"
  log "复制主项目到 tmp 发布目录。该目录可删除重建，不影响主项目。"
  run rsync -a --delete --delete-excluded \
    --include ".env.example" \
    --exclude ".env" \
    --exclude ".env.*" \
    --exclude ".venv/" \
    --exclude ".agents/" \
    --exclude ".data/" \
    --exclude "env_test/" \
    --exclude "storage/objects/" \
    --exclude "logs/" \
    --exclude "*.pid" \
    --exclude "__pycache__/" \
    --exclude ".pytest_cache/" \
    --exclude ".mypy_cache/" \
    --exclude ".ruff_cache/" \
    --exclude ".DS_Store" \
    "${main_root}/" "${release_root}/"

  cd "$release_root"
  log_boundary "进入 tmp 发布目录处理 ${TARGET_BRANCH}"
  log "当前目录：$(pwd)"
  git rev-parse --show-toplevel >/dev/null 2>&1 ||
    die "tmp 发布目录不是有效 Git 仓库：${release_root}"

  run git fetch origin
  ensure_remote_branch_exists "${SOURCE_REF}"
  ensure_remote_branch_exists "origin/${TARGET_BRANCH}"
  log "在 tmp 发布目录中将 ${TARGET_BRANCH} 对齐到 origin/${TARGET_BRANCH}"
  run git switch -C "${TARGET_BRANCH}" "origin/${TARGET_BRANCH}"
}

enter_existing_release_copy() {
  local release_root

  ensure_main_repo
  release_root="$(release_tmp_path)"
  [[ -e "$release_root" ]] ||
    stop_with_next_step "tmp 发布目录不存在：${release_root}" "先执行默认准备流程：./scripts/deploy/release-master.sh"

  git -C "$release_root" rev-parse --show-toplevel >/dev/null 2>&1 ||
    die "tmp 发布目录不是有效 Git 仓库：${release_root}"

  cd "$release_root"
  log_boundary "进入 tmp 发布目录执行 push"
  log "当前目录：$(pwd)"
  log "说明：主项目 test 目录不会被提交、切分支或推送 master。"
}

restore_protected_files_from_head() {
  local existing_files=()
  local file

  for file in "${PROTECTED_FILES[@]}"; do
    if git cat-file -e "HEAD:$file" 2>/dev/null; then
      existing_files+=("$file")
    fi
  done

  if [[ "${#existing_files[@]}" -gt 0 ]]; then
    log "保留 ${TARGET_BRANCH} 分支上的发版文件：${existing_files[*]}"
    run git restore --source=HEAD --staged --worktree -- "${existing_files[@]}"
  fi
}

show_release_status() {
  log "仓库路径：$(pwd)"
  log "当前分支：$(git branch --show-current)"
  log "来源分支：${SOURCE_REF}"
  log "目标分支：${TARGET_BRANCH}"
  log "构建方式：${BUILD_MODE}"
  log "构建标记：$(build_marker)"
  log "保护文件：${PROTECTED_FILES[*]}"
  log "Git 状态："
  git status --short
}

show_release_decision() {
  local main_root release_root main_reason tmp_exists tmp_branch tmp_has_merge tmp_has_conflicts tmp_has_changes

  ensure_main_repo
  main_root="$(pwd)"
  release_root="$(release_tmp_path)"
  main_reason="$(main_readiness_reason)"

  tmp_exists="no"
  tmp_branch=""
  tmp_has_merge="no"
  tmp_has_conflicts="no"
  tmp_has_changes="no"

  if [[ -e "$release_root" ]] && git -C "$release_root" rev-parse --show-toplevel >/dev/null 2>&1; then
    tmp_exists="yes"
    cd "$release_root"
    tmp_branch="$(git branch --show-current)"
    has_pending_merge && tmp_has_merge="yes"
    has_unresolved_conflicts && tmp_has_conflicts="yes"
    has_tracked_changes && tmp_has_changes="yes"
  fi

  cd "$main_root"

  printf '\n'
  log_boundary "发布判断"

  if [[ -n "$main_reason" ]]; then
    warn "prepare：不可执行，原因：${main_reason}"
  elif [[ "$tmp_has_merge" == "yes" ]]; then
    warn "prepare：不建议重复执行，原因：tmp 发布目录已有待发布 merge"
  else
    ok "prepare：可以执行"
  fi

  if [[ "$tmp_exists" != "yes" ]]; then
    warn "push：不可执行，原因：tmp 发布目录尚未创建"
    if [[ -n "$main_reason" ]]; then
      next_step "先处理 prepare 不可执行的原因，然后重新执行：./scripts/deploy/release-master.sh status"
    else
      next_step "执行默认准备流程：./scripts/deploy/release-master.sh"
    fi
    return
  fi

  if [[ "$tmp_branch" != "$TARGET_BRANCH" ]]; then
    warn "push：不可执行，原因：tmp 发布目录当前分支是 ${tmp_branch}，不是 ${TARGET_BRANCH}"
    next_step "重新执行准备流程：./scripts/deploy/release-master.sh"
    return
  fi

  if [[ "$tmp_has_conflicts" == "yes" ]]; then
    warn "push：不可执行，原因：tmp 发布目录仍有未解决冲突"
    next_step "进入 tmp 发布目录解决冲突并 git add，然后执行：./scripts/deploy/release-master.sh --push"
    return
  fi

  if [[ "$tmp_has_merge" == "yes" ]]; then
    ok "push：可以执行"
    next_step "检查 tmp 发布目录 diff 后执行：./scripts/deploy/release-master.sh --push"
    return
  fi

  if [[ "$tmp_has_changes" == "yes" ]]; then
    warn "push：不可执行，原因：tmp 发布目录有变更，但不是 prepare 产生的待发布 merge"
    next_step "如确认废弃本次发布，可删除 tmp 发布目录后重新执行：./scripts/deploy/release-master.sh"
    return
  fi

  warn "push：不可执行，原因：tmp 发布目录干净，没有待提交 merge"
  if [[ -n "$main_reason" ]]; then
    next_step "先处理 prepare 不可执行的原因，然后重新执行：./scripts/deploy/release-master.sh status"
  else
    next_step "如需发布，先执行默认准备流程：./scripts/deploy/release-master.sh"
  fi
}

prepare_release() {
  ensure_main_repo
  ensure_build_mode
  ensure_main_ready_for_prepare

  log "开始准备生产环境发布"
  prepare_release_copy

  if [[ -f "$(git rev-parse --git-path MERGE_HEAD)" ]]; then
    show_release_decision
    stop_with_next_step "tmp 发布目录已经存在一个未完成的 merge，prepare 不会重复执行。" "先检查 tmp 发布目录；确认无误后执行：./scripts/deploy/release-master.sh --push"
  fi

  if [[ -n "$(git status --porcelain)" ]]; then
    warn "tmp 发布目录不干净，prepare 不会继续执行。"
    git status --short
    show_release_decision
    next_step "确认废弃本次发布时，可删除 tmp 发布目录后重新执行：./scripts/deploy/release-master.sh"
    exit 1
  fi

  local source_sha
  source_sha="$(git rev-parse "${SOURCE_REF}")"
  printf '%s\n' "$source_sha" >"$(release_tmp_source_meta)"

  log "合入 ${SOURCE_REF} 到 ${TARGET_BRANCH}，但暂不提交"
  if ! git merge --no-commit --no-ff "${SOURCE_REF}"; then
    log "合并出现冲突；先保留 ${TARGET_BRANCH} 分支上的发版文件，其余业务冲突需要人工处理"
    restore_protected_files_from_head
    if [[ -z "$(git diff --name-only --diff-filter=U)" ]]; then
      log "发版文件冲突已自动处理，当前没有未解决冲突；请检查 tmp 发布目录 diff。"
      show_release_status
      show_release_decision
      next_step "./scripts/deploy/release-master.sh --push"
      exit 0
    fi
    git status --short
    show_release_decision
    stop_with_next_step "检测到合并冲突，脚本已停止在待人工处理状态。" "解决业务代码冲突并 git add 后，执行：./scripts/deploy/release-master.sh --push"
  fi

  restore_protected_files_from_head

  log "prepare 完成；请先检查待发布改动，再执行 push"
  show_release_status
  show_release_decision
  log "建议检查命令："
  log "  ./scripts/deploy/release-master.sh status"
  log "  cd $(pwd)"
  log "  git diff --cached"
  log "  cd ${MAIN_REPO_ROOT}"
  log "下一步："
  log "  ./scripts/deploy/release-master.sh --push"
}

push_release() {
  enter_existing_release_copy
  ensure_build_mode

  local branch
  branch="$(git branch --show-current)"
  [[ "$branch" == "${TARGET_BRANCH}" ]] || {
    show_release_decision
    stop_with_next_step "当前分支必须是 ${TARGET_BRANCH}，实际是：${branch}" "先执行默认准备流程：./scripts/deploy/release-master.sh"
  }
  [[ -f "$(git rev-parse --git-path MERGE_HEAD)" ]] || {
    show_release_decision
    stop_with_next_step "没有检测到待提交的 merge，不能直接 push。" "先执行默认准备流程：./scripts/deploy/release-master.sh"
  }

  if [[ -n "$(git diff --name-only --diff-filter=U)" ]]; then
    git status --short
    show_release_decision
    stop_with_next_step "仍有未解决的合并冲突。" "解决冲突并 git add 后，再执行：./scripts/deploy/release-master.sh --push"
  fi

  local meta_file
  meta_file="$(release_tmp_source_meta)"
  if [[ -f "$meta_file" ]]; then
    local prepared_sha current_sha
    prepared_sha="$(cat "$meta_file")"
    run git fetch origin
    current_sha="$(git rev-parse "${SOURCE_REF}")"
    [[ "$prepared_sha" == "$current_sha" ]] ||
      {
        show_release_decision
        stop_with_next_step "${SOURCE_REF} 在 prepare 后发生变化，当前 tmp 发布内容可能不是最新代码。" "重新执行：./scripts/deploy/release-master.sh"
      }
  else
    warn "未找到 prepare 源提交记录，无法确认 ${SOURCE_REF} 是否在 prepare 后变化。"
  fi

  if [[ -n "$(git ls-files --others --exclude-standard)" ]]; then
    git status --short
    show_release_decision
    stop_with_next_step "存在未跟踪文件，脚本不会自动把它们带入发布提交。" "确认这些文件后，添加、删除或加入 .gitignore，再执行：./scripts/deploy/release-master.sh --push"
  fi

  if git diff --check && git diff --cached --check; then
    log "diff 空白字符检查通过"
  else
    die "diff 空白字符检查失败"
  fi

  local message
  message="${COMMIT_MESSAGE:-$(default_commit_message)}"

  case "$message" in
    *build*) ;;
    *) die "提交信息必须包含 build 才能触发 CI：${message}" ;;
  esac

  case "$BUILD_MODE" in
    os)
      [[ "$message" == *"[build:os]"* ]] || die "BUILD_MODE=os 时提交信息必须包含 [build:os]"
      ;;
    ci)
      [[ "$message" == *"[ci build]"* ]] || die "BUILD_MODE=ci 时提交信息必须包含 [ci build]"
      ;;
  esac

  log "使用以下提交信息提交待发布 merge："
  log "  ${message}"
  run git add -u
  run git commit -m "${message}"
  run git push origin "${TARGET_BRANCH}"

  log "push 完成；请等待钉钉 CI 镜像通知，然后到生产环境 Kuboard 更新镜像版本"
  if [[ "$BUILD_MODE" == "ci" ]]; then
    next_step "拿到代码镜像版本后，在生产环境 Kuboard 调整本项目初始化容器的新版本；不要改工作容器 OS 镜像。"
  else
    next_step "拿到运行环境镜像版本后，由运维确认是否调整生产环境工作容器的 OS-* 新版本。"
  fi
}

status_release() {
  ensure_main_repo
  ensure_build_mode
  log_boundary "主项目 test 目录状态"
  log "说明：这里是日常测试分支目录，只检查，不做 master 分支发布操作。"
  show_release_status

  local release_root
  release_root="$(release_tmp_path)"
  if [[ -e "$release_root" ]] && git -C "$release_root" rev-parse --show-toplevel >/dev/null 2>&1; then
    printf '\n'
    cd "$release_root"
    log_boundary "tmp 发布目录状态"
    log "说明：这里才是 master 分支合并、提交、推送发生的地方。"
    show_release_status
  else
    printf '\n'
    log "tmp 发布目录尚未创建：${release_root}"
  fi

  show_release_decision
}

case "$ACTION" in
  prepare)
    prepare_release
    ;;
  push|--push|-p)
    push_release
    ;;
  status)
    status_release
    ;;
  -h|--help|help)
    usage
    ;;
  *)
    usage
    die "未知动作：${ACTION}"
    ;;
esac
