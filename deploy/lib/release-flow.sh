#!/usr/bin/env bash
# release-flow.sh - shared Git release flow for deploy/release-*.sh
#
# Callers set RELEASE_NAME, REQUIRED_LOCAL_BRANCH, SOURCE_REF, TARGET_BRANCH,
# TARGET_ENV_LABEL, RELEASE_TMP_NAME, PROTECTED_FILES and optional RISK_FILES,
# then call release_flow_main "$@".

set -euo pipefail

MAIN_REPO_ROOT=""

release_log() {
  printf '[%s] %s\n' "$RELEASE_NAME" "$*"
}

release_warn() {
  printf '[%s] 告警：%s\n' "$RELEASE_NAME" "$*" >&2
}

release_die() {
  local message="$1"
  local code="${2:-1}"
  printf '[%s] 错误：%s\n' "$RELEASE_NAME" "$message" >&2
  exit "$code"
}

release_ok() {
  printf '[%s] OK：%s\n' "$RELEASE_NAME" "$*"
}

release_next_step() {
  printf '[%s] 下一步：%s\n' "$RELEASE_NAME" "$*"
}

release_boundary() {
  printf '[%s] ---- %s ----\n' "$RELEASE_NAME" "$*"
}

release_stop_with_next_step() {
  local message="$1"
  local suggestion="$2"

  release_warn "$message"
  release_next_step "$suggestion"
  exit 1
}

release_run() {
  release_log "+ $*"
  "$@"
}

release_entry_cmd() {
  printf './deploy/%s.sh' "$RELEASE_NAME"
}

release_usage() {
  local entry
  entry="$(release_entry_cmd)"
  cat <<EOF
用法：
  ${entry} [prepare|push|--push|status]
  ${entry} -h|--help

发布 ${SOURCE_REF} 到 ${TARGET_ENV_LABEL} 分支 ${TARGET_BRANCH} 的 Git 发布流入口。

命令：
  prepare  检查主 ${REQUIRED_LOCAL_BRANCH}，重建 tmp 仓库，在 tmp 仓库切到 ${TARGET_BRANCH} 并合入 ${SOURCE_REF}。
  push     进入 tmp 仓库，提交 prepare 产生的待发布 merge，并推送到 origin/${TARGET_BRANCH} 触发 CI。
  --push   push 的别名，适合第二次执行时直接添加选项。
  status   同时输出主 ${REQUIRED_LOCAL_BRANCH} 目录、tmp 发布目录状态，并给出 prepare/push 判断。

作用域：
  本脚本只负责 Git 发布流：检查 ${REQUIRED_LOCAL_BRANCH}，在独立 tmp 仓库合入 ${SOURCE_REF} 到 ${TARGET_BRANCH}，再按显式 push 推送。
  建议始终在主项目 ${REQUIRED_LOCAL_BRANCH} 目录执行。本脚本会自动进入 tmp 发布目录处理 ${TARGET_BRANCH}。

不负责：
  Kuboard 切镜像、业务验证、生产部署、远程数据库、K8s 或云平台资源。

运行环境：
  Requires: Bash, Git。
  Dependencies: 可访问 origin 远端；push 阶段需要当前身份有目标分支推送权限。

默认行为：
  无参数时默认执行 prepare，只准备 tmp 发布目录，不提交、不推送。

配置与环境变量：
  SOURCE_REF       要合入 ${TARGET_ENV_LABEL} 的来源分支，默认：${SOURCE_REF}
  TARGET_BRANCH   ${TARGET_ENV_LABEL} 发版分支，默认：${TARGET_BRANCH}
  RELEASE_TMP_DIR  tmp 发布目录，默认：../tmp/<repo-name>-${RELEASE_TMP_NAME}
  BUILD_MODE      构建方式，ci 或 os，默认：${BUILD_MODE}
                   ci -> [ci build]，生成代码镜像，开发默认使用
                   os -> [build:os]，生成运行环境镜像，通常由运维使用
  COMMIT_MESSAGE  push 阶段使用的提交信息，必须包含对应构建标记。

输出：
  stdout: 发布阶段、Git 检查结果、下一步建议和 push 结果。
  stderr: 参数错误、Git 前置条件失败、merge 冲突或 push 失败详情。

副作用与保护边界：
  prepare 只会删除并重建带脚本 marker 的安全 tmp 发布目录；主项目目录不会被切分支、merge、commit 或 push。
  push 只在 tmp 发布目录存在未提交 merge 且 source/target 远端引用未变化时提交并推送。
  缺少远端分支、dirty 工作区、冲突、缺失 prepare meta、远端引用变化都会 fail-fast。

常用示例：
  ${entry}
  ${entry} status
  ${entry} --push
  BUILD_MODE=os ${entry} --push
  COMMIT_MESSAGE="[ci build] release ${SOURCE_REF} to ${TARGET_BRANCH}" ${entry} --push

Exit Codes:
  0  成功
  1  Git 发布前置条件、merge、检查或 push 失败
  2  参数或脚本配置错误
EOF
}

release_args_include_help() {
  local arg
  for arg in "$@"; do
    case "$arg" in
      -h|--help)
        return 0
        ;;
    esac
  done
  return 1
}

release_repo_root() {
  git rev-parse --show-toplevel 2>/dev/null
}

release_ensure_main_repo() {
  local root

  if [[ -n "$MAIN_REPO_ROOT" ]]; then
    cd "$MAIN_REPO_ROOT"
    return
  fi

  root="$(release_repo_root)" || release_die "当前目录不在 Git 仓库中" 2
  cd "$root"
  MAIN_REPO_ROOT="$root"
}

release_tmp_path() {
  local release_dir
  release_dir="${RELEASE_TMP_DIR:-../tmp/$(basename "$(pwd)")-${RELEASE_TMP_NAME}}"

  case "$release_dir" in
    /*) printf '%s\n' "$release_dir" ;;
    *) printf '%s/%s\n' "$(pwd)" "$release_dir" ;;
  esac
}

release_origin_remote_url() {
  git remote get-url origin 2>/dev/null ||
    release_die "当前仓库未配置 origin remote，无法准备 tmp 发布目录" 2
}

release_print_remote_branch_diagnostics() {
  local ref="$1"
  local branch="${ref#origin/}"

  release_warn "找不到远端分支或引用：${ref}"
  release_log "当前可见远端分支："
  git branch -r | sed "s/^/[${RELEASE_NAME}]   /" || true
  release_log "可检查命令："
  release_log "  git branch -a"
  release_log "  git remote show origin"
  release_log "  git ls-remote --heads origin"
  if [[ "$ref" == origin/* ]]; then
    release_log "如果确认需要创建远端 ${branch}，请人工确认后执行："
    release_log "  git push -u origin ${branch}:${branch}"
  fi
}

release_ensure_remote_ref_exists() {
  local ref="$1"

  if git rev-parse --verify --quiet "$ref" >/dev/null; then
    return
  fi

  if [[ "$ref" == origin/* ]]; then
    release_print_remote_branch_diagnostics "$ref"
    exit 1
  fi

  release_die "找不到分支或引用：${ref}" 1
}

release_ensure_build_mode() {
  case "$BUILD_MODE" in
    os|ci) ;;
    *) release_die "BUILD_MODE 只能是 os 或 ci，当前值：${BUILD_MODE}" 2 ;;
  esac

  if [[ "$BUILD_MODE" == "os" ]]; then
    release_warn "当前 BUILD_MODE=os，会生成运行环境镜像 [build:os]；通常这部分由运维维护。开发发代码请使用默认 BUILD_MODE=ci。"
  fi
}

release_build_marker() {
  case "$BUILD_MODE" in
    os) printf '[build:os]' ;;
    ci) printf '[ci build]' ;;
  esac
}

release_default_commit_message() {
  printf '%s release %s to %s' "$(release_build_marker)" "${SOURCE_REF}" "${TARGET_BRANCH}"
}

release_meta_source_sha_path() {
  git rev-parse --git-path "${RELEASE_NAME}-source-sha"
}

release_meta_target_sha_path() {
  git rev-parse --git-path "${RELEASE_NAME}-target-sha"
}

release_tmp_marker_name() {
  printf '.%s-owned' "$RELEASE_TMP_NAME"
}

release_has_pending_merge() {
  [[ -f "$(git rev-parse --git-path MERGE_HEAD)" ]]
}

release_has_unresolved_conflicts() {
  [[ -n "$(git diff --name-only --diff-filter=U)" ]]
}

release_has_tracked_changes() {
  [[ -n "$(git status --porcelain --untracked-files=no)" ]]
}

release_main_readiness_reason() {
  local branch ahead behind

  branch="$(git branch --show-current)"
  if [[ "$branch" != "$REQUIRED_LOCAL_BRANCH" ]]; then
    printf '主项目目录必须在 %s 分支，当前是 %s' "$REQUIRED_LOCAL_BRANCH" "$branch"
    return
  fi

  if [[ -n "$(git status --porcelain)" ]]; then
    printf '主项目 %s 工作区不干净' "$REQUIRED_LOCAL_BRANCH"
    return
  fi

  if [[ "$SOURCE_REF" == "origin/${REQUIRED_LOCAL_BRANCH}" ]] &&
    git show-ref --verify --quiet "refs/heads/${REQUIRED_LOCAL_BRANCH}" &&
    git show-ref --verify --quiet "refs/remotes/origin/${REQUIRED_LOCAL_BRANCH}"; then
    ahead="$(git rev-list --count "origin/${REQUIRED_LOCAL_BRANCH}..${REQUIRED_LOCAL_BRANCH}")"
    behind="$(git rev-list --count "${REQUIRED_LOCAL_BRANCH}..origin/${REQUIRED_LOCAL_BRANCH}")"
    if [[ "$ahead" != "0" ]]; then
      printf '本地 %s 领先 origin/%s %s 个提交，需要先 git push origin %s' "$REQUIRED_LOCAL_BRANCH" "$REQUIRED_LOCAL_BRANCH" "$ahead" "$REQUIRED_LOCAL_BRANCH"
      return
    fi
    if [[ "$behind" != "0" ]]; then
      printf '本地 %s 落后 origin/%s %s 个提交，需要先同步 %s' "$REQUIRED_LOCAL_BRANCH" "$REQUIRED_LOCAL_BRANCH" "$behind" "$REQUIRED_LOCAL_BRANCH"
      return
    fi
  fi
}

release_ensure_main_ready_for_prepare() {
  local branch
  branch="$(git branch --show-current)"
  release_boundary "检查主项目 ${REQUIRED_LOCAL_BRANCH} 目录"
  release_log "当前执行目录：$(pwd)"
  release_log "当前分支：${branch}"
  release_log "说明：主项目目录只做检查和来源分支发布前确认，不会切到 ${TARGET_BRANCH}。"

  [[ "$branch" == "$REQUIRED_LOCAL_BRANCH" ]] ||
    release_stop_with_next_step "当前主目录必须在 ${REQUIRED_LOCAL_BRANCH} 分支，实际是：${branch}" "切回 ${REQUIRED_LOCAL_BRANCH} 后重新执行：git switch ${REQUIRED_LOCAL_BRANCH} && $(release_entry_cmd)"

  if [[ -n "$(git status --porcelain)" ]]; then
    release_warn "主 ${REQUIRED_LOCAL_BRANCH} 工作区不干净，prepare 不会继续执行。"
    git status --short
    release_next_step "先提交或清理 ${REQUIRED_LOCAL_BRANCH} 工作区改动，然后重新执行：$(release_entry_cmd)"
    exit 1
  fi

  release_run git fetch origin
  release_ensure_remote_ref_exists "${SOURCE_REF}"
  release_ensure_remote_ref_exists "origin/${TARGET_BRANCH}"

  if [[ "$SOURCE_REF" == "origin/${REQUIRED_LOCAL_BRANCH}" ]] &&
    git show-ref --verify --quiet "refs/heads/${REQUIRED_LOCAL_BRANCH}" &&
    git show-ref --verify --quiet "refs/remotes/origin/${REQUIRED_LOCAL_BRANCH}"; then
    local ahead behind
    ahead="$(git rev-list --count "origin/${REQUIRED_LOCAL_BRANCH}..${REQUIRED_LOCAL_BRANCH}")"
    behind="$(git rev-list --count "${REQUIRED_LOCAL_BRANCH}..origin/${REQUIRED_LOCAL_BRANCH}")"
    if [[ "$ahead" != "0" ]]; then
      release_stop_with_next_step "本地 ${REQUIRED_LOCAL_BRANCH} 领先 origin/${REQUIRED_LOCAL_BRANCH} ${ahead} 个提交；脚本默认发布 origin/${REQUIRED_LOCAL_BRANCH}，不会包含这些本地提交。" "先执行：git push origin ${REQUIRED_LOCAL_BRANCH}；然后重新执行：$(release_entry_cmd)"
    fi
    if [[ "$behind" != "0" ]]; then
      release_stop_with_next_step "本地 ${REQUIRED_LOCAL_BRANCH} 落后 origin/${REQUIRED_LOCAL_BRANCH} ${behind} 个提交；脚本不会在旧 ${REQUIRED_LOCAL_BRANCH} 状态下发布。" "先同步 ${REQUIRED_LOCAL_BRANCH} 后重新执行，例如：git pull --ff-only origin ${REQUIRED_LOCAL_BRANCH}"
    fi
  fi
}

release_assert_tmp_safe() {
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

  [[ -n "$release_root" ]] || release_die "tmp 发布目录为空" 2
  [[ "$release_root" != "/" ]] || release_die "tmp 发布目录不能是根目录" 2
  [[ "$release_base" == *"$RELEASE_TMP_NAME"* ]] || release_die "tmp 发布目录名称必须包含 ${RELEASE_TMP_NAME}：${release_root}" 2
  [[ "$release_parent_base" == "tmp" ]] || release_die "tmp 发布目录必须放在 tmp 目录下：${release_root}" 2

  if [[ -d "$release_parent" ]]; then
    release_real="$(cd "$release_parent" && pwd -P)/${release_base}"
    [[ "$release_real" != "$main_real" ]] || release_die "tmp 发布目录不能等于主项目目录：${release_real}" 2
    case "${release_real}/" in
      "${main_real}/"*) release_die "tmp 发布目录不能放在主项目目录内部：${release_real}" 2 ;;
    esac
  fi
}

release_tmp_marker_path() {
  local release_root="$1"
  local git_path

  if git_path="$(git -C "$release_root" rev-parse --git-path "$(release_tmp_marker_name)" 2>/dev/null)"; then
    case "$git_path" in
      /*) printf '%s\n' "$git_path" ;;
      *) printf '%s/%s\n' "$release_root" "$git_path" ;;
    esac
    return
  fi

  printf '%s/%s\n' "$release_root" "$(release_tmp_marker_name)"
}

release_write_tmp_marker() {
  local release_root="$1"
  {
    printf 'release_name=%s\n' "$RELEASE_NAME"
    printf 'release_tmp_name=%s\n' "$RELEASE_TMP_NAME"
    printf 'main_repo=%s\n' "$MAIN_REPO_ROOT"
  } >"$(release_tmp_marker_path "$release_root")"
}

release_guard_existing_tmp_owned() {
  local release_root="$1"
  local marker
  marker="$(release_tmp_marker_path "$release_root")"

  [[ -f "$marker" ]] ||
    release_stop_with_next_step "tmp 发布目录已存在但缺少脚本创建标记，发布流程不会继续执行：${release_root}" "请人工检查该目录；确认废弃后手动删除，再重新执行：$(release_entry_cmd)"
  grep -Fxq "release_tmp_name=${RELEASE_TMP_NAME}" "$marker" ||
    release_stop_with_next_step "tmp 发布目录标记不属于当前发布入口，发布流程不会继续执行：${release_root}" "请人工检查 RELEASE_TMP_DIR 或删除错误 tmp 目录后重试。"
  grep -Fxq "main_repo=${MAIN_REPO_ROOT}" "$marker" ||
    release_stop_with_next_step "tmp 发布目录标记不属于当前主项目，发布流程不会继续执行：${release_root}" "请人工检查 RELEASE_TMP_DIR 或删除错误 tmp 目录后重试。"
}

release_prepare_copy() {
  local main_root release_root release_parent origin_url

  release_ensure_main_repo
  main_root="$(pwd)"
  release_root="$(release_tmp_path)"
  release_parent="$(dirname "$release_root")"
  origin_url="$(release_origin_remote_url)"

  release_boundary "准备 tmp 发布目录"
  release_log "主 ${REQUIRED_LOCAL_BRANCH} 目录：${main_root}"
  release_log "tmp 发布目录：${release_root}"
  release_log "说明：下面的切分支、merge、commit、push 都只发生在 tmp 发布目录。"

  release_assert_tmp_safe "$main_root" "$release_root"

  release_run mkdir -p "$release_parent"
  if [[ -e "$release_root" ]]; then
    if git -C "$release_root" rev-parse --show-toplevel >/dev/null 2>&1 &&
      [[ -f "$(git -C "$release_root" rev-parse --git-path MERGE_HEAD)" ]]; then
      release_stop_with_next_step "tmp 发布目录已经存在一个未完成的 merge，prepare 不会覆盖。" "先检查 tmp 发布目录；确认无误后执行：$(release_entry_cmd) --push"
    fi

    release_guard_existing_tmp_owned "$release_root"
    release_log "重建 tmp 发布目录。该目录可删除重建，不影响主项目。"
    release_run rm -rf "$release_root"
  fi

  release_log "创建独立 tmp Git 仓库，不复制主项目 .git/objects。"
  release_log "+ git clone --origin origin --no-checkout <origin-url> ${release_root}"
  git clone --origin origin --no-checkout "$origin_url" "$release_root"
  release_write_tmp_marker "$release_root"

  cd "$release_root"
  release_boundary "进入 tmp 发布目录处理 ${TARGET_BRANCH}"
  release_log "当前目录：$(pwd)"
  git rev-parse --show-toplevel >/dev/null 2>&1 ||
    release_die "tmp 发布目录不是有效 Git 仓库：${release_root}" 2

  release_run git fetch origin
  release_ensure_remote_ref_exists "${SOURCE_REF}"
  release_ensure_remote_ref_exists "origin/${TARGET_BRANCH}"
  release_log "在 tmp 发布目录中将 ${TARGET_BRANCH} 对齐到 origin/${TARGET_BRANCH}"
  release_run git switch -C "${TARGET_BRANCH}" "origin/${TARGET_BRANCH}"
}

release_enter_existing_copy() {
  local release_root

  release_ensure_main_repo
  release_root="$(release_tmp_path)"
  [[ -e "$release_root" ]] ||
    release_stop_with_next_step "tmp 发布目录不存在：${release_root}" "先执行默认准备流程：$(release_entry_cmd)"

  git -C "$release_root" rev-parse --show-toplevel >/dev/null 2>&1 ||
    release_die "tmp 发布目录不是有效 Git 仓库：${release_root}" 2
  release_guard_existing_tmp_owned "$release_root"

  cd "$release_root"
  release_boundary "进入 tmp 发布目录执行 push"
  release_log "当前目录：$(pwd)"
  release_log "说明：主项目 ${REQUIRED_LOCAL_BRANCH} 目录不会被提交、切分支或推送 ${TARGET_BRANCH}。"
}

release_restore_protected_files_from_head() {
  local existing_files=()
  local file

  for file in "${PROTECTED_FILES[@]}"; do
    if git cat-file -e "HEAD:$file" 2>/dev/null; then
      existing_files+=("$file")
    fi
  done

  if [[ "${#existing_files[@]}" -gt 0 ]]; then
    release_log "保留 ${TARGET_BRANCH} 分支上的发版文件：${existing_files[*]}"
    release_run git restore --source=HEAD --staged --worktree -- "${existing_files[@]}"
  fi
}

release_warn_risk_files_changed() {
  local changed

  if [[ "${#RISK_FILES[@]}" -eq 0 ]]; then
    return
  fi

  changed="$(git diff --name-only HEAD -- "${RISK_FILES[@]}" || true)"
  if [[ -n "$changed" ]]; then
    release_log "注意：以下构建风险文件在本次发布中发生变化，请确认符合预期："
    printf '%s\n' "$changed" | sed "s/^/[${RELEASE_NAME}]   - /"
    release_log "这些文件可能影响 Docker 构建上下文、依赖安装、Python 版本或 Git 跟踪范围。"
  fi
}

release_show_status() {
  release_log "仓库路径：$(pwd)"
  release_log "当前分支：$(git branch --show-current)"
  release_log "来源分支：${SOURCE_REF}"
  release_log "目标分支：${TARGET_BRANCH}"
  release_log "构建方式：${BUILD_MODE}"
  release_log "构建标记：$(release_build_marker)"
  release_log "保护文件：${PROTECTED_FILES[*]}"
  if [[ "${#RISK_FILES[@]}" -gt 0 ]]; then
    release_log "风险文件：${RISK_FILES[*]}"
  fi
  release_log "Git 状态："
  git status --short
}

release_show_decision() {
  local main_root release_root main_reason tmp_exists tmp_branch tmp_has_merge tmp_has_conflicts tmp_has_changes

  release_ensure_main_repo
  main_root="$(pwd)"
  release_root="$(release_tmp_path)"
  main_reason="$(release_main_readiness_reason)"

  tmp_exists="no"
  tmp_branch=""
  tmp_has_merge="no"
  tmp_has_conflicts="no"
  tmp_has_changes="no"

  if [[ -e "$release_root" ]] && git -C "$release_root" rev-parse --show-toplevel >/dev/null 2>&1; then
    tmp_exists="yes"
    release_guard_existing_tmp_owned "$release_root"
    cd "$release_root"
    tmp_branch="$(git branch --show-current)"
    release_has_pending_merge && tmp_has_merge="yes"
    release_has_unresolved_conflicts && tmp_has_conflicts="yes"
    release_has_tracked_changes && tmp_has_changes="yes"
  fi

  cd "$main_root"

  printf '\n'
  release_boundary "发布判断"

  if [[ -n "$main_reason" ]]; then
    release_warn "prepare：不可执行，原因：${main_reason}"
  elif [[ "$tmp_has_merge" == "yes" ]]; then
    release_warn "prepare：不建议重复执行，原因：tmp 发布目录已有待发布 merge"
  else
    release_ok "prepare：可以执行"
  fi

  if [[ "$tmp_exists" != "yes" ]]; then
    release_warn "push：不可执行，原因：tmp 发布目录尚未创建"
    if [[ -n "$main_reason" ]]; then
      release_next_step "先处理 prepare 不可执行的原因，然后重新执行：$(release_entry_cmd) status"
    else
      release_next_step "执行默认准备流程：$(release_entry_cmd)"
    fi
    return
  fi

  if [[ "$tmp_branch" != "$TARGET_BRANCH" ]]; then
    release_warn "push：不可执行，原因：tmp 发布目录当前分支是 ${tmp_branch}，不是 ${TARGET_BRANCH}"
    release_next_step "重新执行准备流程：$(release_entry_cmd)"
    return
  fi

  if [[ "$tmp_has_conflicts" == "yes" ]]; then
    release_warn "push：不可执行，原因：tmp 发布目录仍有未解决冲突"
    release_next_step "进入 tmp 发布目录解决冲突并 git add，然后执行：$(release_entry_cmd) --push"
    return
  fi

  if [[ "$tmp_has_merge" == "yes" ]]; then
    release_ok "push：可以执行"
    release_next_step "检查 tmp 发布目录 diff 后执行：$(release_entry_cmd) --push"
    return
  fi

  if [[ "$tmp_has_changes" == "yes" ]]; then
    release_warn "push：不可执行，原因：tmp 发布目录有变更，但不是 prepare 产生的待发布 merge"
    release_next_step "如确认废弃本次发布，可删除 tmp 发布目录后重新执行：$(release_entry_cmd)"
    return
  fi

  release_warn "push：不可执行，原因：tmp 发布目录干净，没有待提交 merge"
  if [[ -n "$main_reason" ]]; then
    release_next_step "先处理 prepare 不可执行的原因，然后重新执行：$(release_entry_cmd) status"
  else
    release_next_step "如需发布，先执行默认准备流程：$(release_entry_cmd)"
  fi
}

release_write_prepare_meta() {
  local source_sha target_sha

  source_sha="$(git rev-parse "${SOURCE_REF}")"
  target_sha="$(git rev-parse "origin/${TARGET_BRANCH}")"
  printf '%s\n' "$source_sha" >"$(release_meta_source_sha_path)"
  printf '%s\n' "$target_sha" >"$(release_meta_target_sha_path)"
}

release_assert_prepare_meta_current() {
  local source_meta target_meta prepared_source prepared_target current_source current_target

  source_meta="$(release_meta_source_sha_path)"
  target_meta="$(release_meta_target_sha_path)"
  [[ -f "$source_meta" ]] ||
    release_stop_with_next_step "缺少 prepare 源提交记录，无法确认 ${SOURCE_REF} 是否在 prepare 后变化。" "重新执行准备流程：$(release_entry_cmd)"
  [[ -f "$target_meta" ]] ||
    release_stop_with_next_step "缺少 prepare 目标提交记录，无法确认 origin/${TARGET_BRANCH} 是否在 prepare 后变化。" "重新执行准备流程：$(release_entry_cmd)"

  prepared_source="$(cat "$source_meta")"
  prepared_target="$(cat "$target_meta")"
  release_run git fetch origin
  current_source="$(git rev-parse "${SOURCE_REF}")"
  current_target="$(git rev-parse "origin/${TARGET_BRANCH}")"

  [[ "$prepared_source" == "$current_source" ]] ||
    release_stop_with_next_step "${SOURCE_REF} 在 prepare 后发生变化，当前 tmp 发布内容可能不是最新代码。" "重新执行：$(release_entry_cmd)"
  [[ "$prepared_target" == "$current_target" ]] ||
    release_stop_with_next_step "origin/${TARGET_BRANCH} 在 prepare 后发生变化，当前 tmp 发布目标已经过期。" "重新执行：$(release_entry_cmd)"
}

release_assert_push_target_branch() {
  local branch
  branch="$(git branch --show-current)"
  [[ "$branch" == "${TARGET_BRANCH}" ]] || {
    release_show_decision
    release_stop_with_next_step "当前分支必须是 ${TARGET_BRANCH}，实际是：${branch}" "先执行默认准备流程：$(release_entry_cmd)"
  }
}

release_assert_pending_merge() {
  release_has_pending_merge || {
    release_show_decision
    release_stop_with_next_step "没有检测到待提交的 merge，不能直接 push。" "先执行默认准备流程：$(release_entry_cmd)"
  }
}

release_assert_no_unresolved_conflicts() {
  if release_has_unresolved_conflicts; then
    git status --short
    release_show_decision
    release_stop_with_next_step "仍有未解决的合并冲突。" "解决冲突并 git add 后，再执行：$(release_entry_cmd) --push"
  fi
}

release_assert_no_untracked_files() {
  if [[ -n "$(git ls-files --others --exclude-standard)" ]]; then
    git status --short
    release_show_decision
    release_stop_with_next_step "存在未跟踪文件，脚本不会自动把它们带入发布提交。" "确认这些文件后，添加、删除或加入 .gitignore，再执行：$(release_entry_cmd) --push"
  fi
}

release_assert_diff_whitespace_clean() {
  if git diff --check && git diff --cached --check; then
    release_log "diff 空白字符检查通过"
  else
    release_die "diff 空白字符检查失败" 1
  fi
}

release_assert_commit_message_valid() {
  local message="$1"

  case "$message" in
    *build*) ;;
    *) release_die "提交信息必须包含 build 才能触发 CI：${message}" 2 ;;
  esac

  case "$BUILD_MODE" in
    os)
      [[ "$message" == *"[build:os]"* ]] || release_die "BUILD_MODE=os 时提交信息必须包含 [build:os]" 2
      ;;
    ci)
      [[ "$message" == *"[ci build]"* ]] || release_die "BUILD_MODE=ci 时提交信息必须包含 [ci build]" 2
      ;;
  esac
}

release_run_push_preflight() {
  local message="$1"

  release_assert_push_target_branch
  release_assert_pending_merge
  release_assert_no_unresolved_conflicts
  release_assert_prepare_meta_current
  release_assert_no_untracked_files
  release_assert_diff_whitespace_clean
  release_assert_commit_message_valid "$message"
}

release_prepare() {
  release_ensure_main_repo
  release_ensure_build_mode
  release_ensure_main_ready_for_prepare

  release_log "开始准备 ${TARGET_ENV_LABEL} 发布"
  release_prepare_copy

  if release_has_pending_merge; then
    release_show_decision
    release_stop_with_next_step "tmp 发布目录已经存在一个未完成的 merge，prepare 不会重复执行。" "先检查 tmp 发布目录；确认无误后执行：$(release_entry_cmd) --push"
  fi

  if [[ -n "$(git status --porcelain)" ]]; then
    release_warn "tmp 发布目录不干净，prepare 不会继续执行。"
    git status --short
    release_show_decision
    release_next_step "确认废弃本次发布时，可删除 tmp 发布目录后重新执行：$(release_entry_cmd)"
    exit 1
  fi

  release_write_prepare_meta

  release_log "合入 ${SOURCE_REF} 到 ${TARGET_BRANCH}，但暂不提交"
  if ! git merge --no-commit --no-ff "${SOURCE_REF}"; then
    release_log "合并出现冲突；先保留 ${TARGET_BRANCH} 分支上的发版文件，其余业务冲突需要人工处理"
    release_restore_protected_files_from_head
    if [[ -z "$(git diff --name-only --diff-filter=U)" ]]; then
      release_warn_risk_files_changed
      release_log "发版文件冲突已自动处理，当前没有未解决冲突；请检查 tmp 发布目录 diff。"
      release_show_status
      release_show_decision
      release_next_step "$(release_entry_cmd) --push"
      exit 0
    fi
    git status --short
    release_show_decision
    release_stop_with_next_step "检测到合并冲突，脚本已停止在待人工处理状态。" "解决业务代码冲突并 git add 后，执行：$(release_entry_cmd) --push"
  fi

  release_restore_protected_files_from_head
  release_warn_risk_files_changed

  release_log "prepare 完成；请先检查待发布改动，再执行 push"
  release_show_status
  release_show_decision
  release_log "建议检查命令："
  release_log "  $(release_entry_cmd) status"
  release_log "  cd $(pwd)"
  release_log "  git diff --cached"
  release_log "  cd ${MAIN_REPO_ROOT}"
  release_log "下一步："
  release_log "  $(release_entry_cmd) --push"
}

release_push() {
  release_enter_existing_copy
  release_ensure_build_mode

  local message
  message="${COMMIT_MESSAGE:-$(release_default_commit_message)}"
  release_run_push_preflight "$message"

  release_log "使用以下提交信息提交待发布 merge："
  release_log "  ${message}"
  release_run git add -u
  release_run git commit -m "${message}"
  release_run git push origin "${TARGET_BRANCH}"

  release_log "push 完成；请等待 CI 镜像通知，然后到 ${TARGET_ENV_LABEL} Kuboard 更新镜像版本"
  if [[ "$BUILD_MODE" == "ci" ]]; then
    release_next_step "${CI_IMAGE_NEXT_STEP}"
  else
    release_next_step "${OS_IMAGE_NEXT_STEP}"
  fi
}

release_status() {
  local release_root

  release_ensure_main_repo
  release_ensure_build_mode
  release_boundary "主项目 ${REQUIRED_LOCAL_BRANCH} 目录状态"
  release_log "说明：这里是日常 ${REQUIRED_LOCAL_BRANCH} 分支目录，只检查，不做 ${TARGET_BRANCH} 分支发布操作。"
  release_show_status

  release_root="$(release_tmp_path)"
  if [[ -e "$release_root" ]] && git -C "$release_root" rev-parse --show-toplevel >/dev/null 2>&1; then
    printf '\n'
    release_guard_existing_tmp_owned "$release_root"
    cd "$release_root"
    release_boundary "tmp 发布目录状态"
    release_log "说明：这里才是 ${TARGET_BRANCH} 分支合并、提交、推送发生的地方。"
    release_show_status
  else
    printf '\n'
    release_log "tmp 发布目录尚未创建：${release_root}"
  fi

  release_show_decision
}

release_validate_config() {
  [[ -n "${RELEASE_NAME:-}" ]] || release_die "缺少 RELEASE_NAME" 2
  [[ -n "${REQUIRED_LOCAL_BRANCH:-}" ]] || release_die "缺少 REQUIRED_LOCAL_BRANCH" 2
  [[ -n "${SOURCE_REF:-}" ]] || release_die "缺少 SOURCE_REF" 2
  [[ -n "${TARGET_BRANCH:-}" ]] || release_die "缺少 TARGET_BRANCH" 2
  [[ -n "${TARGET_ENV_LABEL:-}" ]] || release_die "缺少 TARGET_ENV_LABEL" 2
  [[ -n "${RELEASE_TMP_NAME:-}" ]] || release_die "缺少 RELEASE_TMP_NAME" 2
  declare -p PROTECTED_FILES >/dev/null 2>&1 || release_die "缺少 PROTECTED_FILES" 2
  declare -p RISK_FILES >/dev/null 2>&1 || RISK_FILES=()
  [[ "${#PROTECTED_FILES[@]}" -gt 0 ]] || release_die "缺少 PROTECTED_FILES" 2
  [[ -n "${CI_IMAGE_NEXT_STEP:-}" ]] || release_die "缺少 CI_IMAGE_NEXT_STEP" 2
  [[ -n "${OS_IMAGE_NEXT_STEP:-}" ]] || release_die "缺少 OS_IMAGE_NEXT_STEP" 2
}

release_flow_main() {
  local action="${1:-prepare}"

  release_validate_config
  if release_args_include_help "$@"; then
    release_usage
    return 0
  fi
  case "$action" in
    prepare)
      release_prepare
      ;;
    push|--push|-p)
      release_push
      ;;
    status)
      release_status
      ;;
    -h|--help|help)
      release_usage
      ;;
    *)
      release_usage >&2
      release_die "未知动作：${action}" 2
      ;;
  esac
}
