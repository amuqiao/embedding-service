#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RELEASE_FLOW_SMOKE_BASE=""
CASE_SCRIPT=""
CASE_REQUIRED_BRANCH=""
CASE_TARGET_BRANCH=""
CASE_TMP_NAME=""
CASE_MARKER=""
CASE_COMMIT_MESSAGE=""
CASE_DRIFT_MESSAGE=""

make_release_repo() {
  local base="$1"

  git init --bare --initial-branch "$CASE_REQUIRED_BRANCH" "$base/origin.git" >/dev/null
  git clone "$base/origin.git" "$base/work" >/dev/null 2>&1
  cd "$base/work"
  git config user.email smoke@example.test
  git config user.name Smoke
  printf 'base\n' >app.txt
  git add app.txt
  git commit -m base >/dev/null
  git branch -M "$CASE_REQUIRED_BRANCH"
  git push -u origin "$CASE_REQUIRED_BRANCH" >/dev/null 2>&1
  git switch -c "$CASE_TARGET_BRANCH" >/dev/null 2>&1
  git push -u origin "$CASE_TARGET_BRANCH" >/dev/null 2>&1
  git switch "$CASE_REQUIRED_BRANCH" >/dev/null 2>&1
  printf '%s\n' "$CASE_REQUIRED_BRANCH" >>app.txt
  git commit -am "${CASE_REQUIRED_BRANCH}-change" >/dev/null
  git push origin "$CASE_REQUIRED_BRANCH" >/dev/null 2>&1
}

run_release_push() {
  local tmp_dir="$1"
  GIT_AUTHOR_NAME=Smoke \
    GIT_AUTHOR_EMAIL=smoke@example.test \
    GIT_COMMITTER_NAME=Smoke \
    GIT_COMMITTER_EMAIL=smoke@example.test \
    COMMIT_MESSAGE="$CASE_COMMIT_MESSAGE" \
    RELEASE_TMP_DIR="$tmp_dir" \
    "$CASE_SCRIPT" --push
}

smoke_prepare_status_push() {
  local base="$1"
  local tmp_dir="$base/tmp/$CASE_TMP_NAME"
  make_release_repo "$base"

  RELEASE_TMP_DIR="$tmp_dir" "$CASE_SCRIPT" prepare >"$base/prepare.out" 2>&1
  [[ -f "$tmp_dir/.git/$CASE_MARKER" ]]
  RELEASE_TMP_DIR="$tmp_dir" "$CASE_SCRIPT" status >"$base/status.out" 2>&1
  run_release_push "$tmp_dir" >"$base/push.out" 2>&1

  git fetch origin "$CASE_TARGET_BRANCH" >/dev/null 2>&1
  git merge-base --is-ancestor "origin/$CASE_REQUIRED_BRANCH" "origin/$CASE_TARGET_BRANCH"
}

smoke_refuse_unowned_tmp() {
  local base="$1"
  local tmp_dir="$base/tmp/$CASE_TMP_NAME"
  make_release_repo "$base"

  mkdir -p "$tmp_dir"
  printf 'keep\n' >"$tmp_dir/keep.txt"
  if RELEASE_TMP_DIR="$tmp_dir" "$CASE_SCRIPT" prepare >"$base/unowned.out" 2>&1; then
    printf 'release prepare unexpectedly accepted unowned tmp dir\n' >&2
    return 1
  fi
  [[ -f "$tmp_dir/keep.txt" ]]
  grep -q '缺少脚本创建标记' "$base/unowned.out"
}

smoke_refuse_target_drift() {
  local base="$1"
  local tmp_dir="$base/tmp/$CASE_TMP_NAME"
  local prepared_head
  make_release_repo "$base"

  RELEASE_TMP_DIR="$tmp_dir" "$CASE_SCRIPT" prepare >"$base/prepare.out" 2>&1
  prepared_head="$(git -C "$tmp_dir" rev-parse HEAD)"

  git clone "$base/origin.git" "$base/target-work" >/dev/null 2>&1
  cd "$base/target-work"
  git config user.email smoke@example.test
  git config user.name Smoke
  git switch "$CASE_TARGET_BRANCH" >/dev/null 2>&1
  printf 'target\n' >>target.txt
  git add target.txt
  git commit -m target-change >/dev/null
  git push origin "$CASE_TARGET_BRANCH" >/dev/null 2>&1

  cd "$base/work"
  if run_release_push "$tmp_dir" >"$base/target-drift.out" 2>&1; then
    printf 'release push unexpectedly accepted target drift\n' >&2
    return 1
  fi
  grep -q "$CASE_DRIFT_MESSAGE" "$base/target-drift.out"
  [[ "$(git -C "$tmp_dir" rev-parse HEAD)" == "$prepared_head" ]]
  [[ -f "$tmp_dir/.git/MERGE_HEAD" ]]
}

run_case() {
  local name="$1"
  CASE_SCRIPT="$2"
  CASE_REQUIRED_BRANCH="$3"
  CASE_TARGET_BRANCH="$4"
  CASE_TMP_NAME="$5"
  CASE_MARKER=".$CASE_TMP_NAME-owned"
  CASE_COMMIT_MESSAGE="[ci build] smoke release origin/${CASE_REQUIRED_BRANCH} to ${CASE_TARGET_BRANCH}"
  CASE_DRIFT_MESSAGE="origin/${CASE_TARGET_BRANCH} 在 prepare 后发生变化"

  smoke_prepare_status_push "$RELEASE_FLOW_SMOKE_BASE/$name/normal"
  smoke_refuse_unowned_tmp "$RELEASE_FLOW_SMOKE_BASE/$name/unowned"
  smoke_refuse_target_drift "$RELEASE_FLOW_SMOKE_BASE/$name/target-drift"
}

main() {
  RELEASE_FLOW_SMOKE_BASE="$(mktemp -d "${TMPDIR:-/tmp}/release-flow-smoke.XXXXXX")"
  trap 'rm -rf "$RELEASE_FLOW_SMOKE_BASE"' EXIT

  run_case release-test "$ROOT_DIR/deploy/release-test.sh" dev test release-test
  run_case release-master "$ROOT_DIR/deploy/release-master.sh" test master release-master
}

main "$@"
