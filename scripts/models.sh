#!/usr/bin/env bash
# models.sh - 本地模型资产入口
#
# 运行环境：Bash；下载和远端校验需要 uv 或 hf CLI。
# 作用域：管理当前仓库本地模型资产的下载路径、必需文件检查和可重复下载命令。
# 约束：不实现自定义下载器，不登录，不自动切换镜像源，不删除模型文件。
# 输出：默认人读；支持 --json 的只读命令 stdout 只输出 JSON。

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT_DIR/scripts/lib/common.sh"

usage() {
  cat <<EOF
用法：
  ./scripts/models.sh <command> [args...]
  ./scripts/models.sh -h|--help

作用域：
  当前仓库的本地模型资产入口。封装模型名、下载源、默认下载路径和必需文件检查。
  不负责 Job 执行、模型推理、生产部署、鉴权登录、自动镜像切换或删除本地模型。

运行环境：
  Requires: Bash
  Dependencies: 下载和远端校验需要 hf CLI；没有 hf 时会尝试通过 uv run hf 执行。

命令：
  list                列出脚本已知的本地模型资产。
  status <model>      检查模型目录下的文件是否存在且非空；默认只检查必需文件。
  verify <model>      执行本地文件校验；--remote-check 时额外调用 hf cache verify。
  download <model>    通过官方 hf download 下载模型到约定目录。
  help                显示帮助。

已知模型：
  htdemucs-ft         StemSplitio/htdemucs-ft-onnx -> .data/models/htdemucs-ft

下载与校验范围：
  required            默认范围，只包含当前 htdemucs 推理必需的 6 个文件：
                      4 个 fp32 专家 .onnx、bag_infer.py、requirements.txt。
  all-files           完整 Hugging Face 仓库范围；包含 README、.gitattributes 和 4 个 fp16 权重。
                      只有传 --all-files 时才下载或要求校验这些非必需文件。

配置与环境变量：
  HF_ENDPOINT         可选，显式指定 Hugging Face endpoint，例如 https://hf-mirror.com。
  HF_CLI              可选，显式指定 hf 可执行文件路径。
  UV_CACHE_DIR        可选，uv run hf 使用的缓存目录；默认 .uv-cache。

输出：
  stdout: 人读状态、文件清单或第三方 hf 输出；--json 只用于 list/status/verify 的本地检查输出。
  stderr: 非法命令、非法参数、缺少依赖、缺失必需文件或不支持的下载源。

副作用与保护边界：
  list/status 不写文件、不访问网络。
  verify 默认不访问网络；传 --remote-check 时会访问 Hugging Face 元数据并校验本地目录。
  download 会写入 .data/models/... 或 --model-dir 指定目录；不会把模型文件加入 git。
  不支持自动 fallback 到 ModelScope 或其他镜像源；source 不支持时直接失败。
  如需镜像源，必须显式设置 HF_ENDPOINT，脚本只透传该环境变量。

常用示例：
  ./scripts/models.sh list
  ./scripts/models.sh status htdemucs-ft
  ./scripts/models.sh verify htdemucs-ft
  ./scripts/models.sh verify htdemucs-ft --all-files
  HF_ENDPOINT=https://hf-mirror.com ./scripts/models.sh verify htdemucs-ft --remote-check
  HF_ENDPOINT=https://hf-mirror.com ./scripts/models.sh verify htdemucs-ft --remote-check --all-files
  HF_ENDPOINT=https://hf-mirror.com ./scripts/models.sh download htdemucs-ft --dry-run
  HF_ENDPOINT=https://hf-mirror.com ./scripts/models.sh download htdemucs-ft
  HF_ENDPOINT=https://hf-mirror.com ./scripts/models.sh download htdemucs-ft --all-files

Exit Codes:
  0  成功
  2  缺少 command、非法命令、非法参数、未知模型、不支持的 source 或缺少依赖
  4  模型必需文件缺失、为空或远端校验失败
EOF
}

list_usage() {
  cat <<EOF
用法：
  ./scripts/models.sh list [--json]
  ./scripts/models.sh list -h|--help

说明：
  列出脚本已知的模型资产和默认本地路径。

输出：
  stdout: 默认人读表格；--json 输出机器可读 JSON。

副作用与保护边界：
  不读取 .env，不写文件，不访问网络。

常用示例：
  ./scripts/models.sh list
  ./scripts/models.sh list --json
EOF
}

status_usage() {
  cat <<EOF
用法：
  ./scripts/models.sh status <model> [--model-dir DIR] [--all-files] [--json]
  ./scripts/models.sh status -h|--help

说明：
  检查模型目录下的文件是否存在且非空；不访问网络。
  默认 scope=required，只检查当前 htdemucs 推理必需的 6 个文件。
  传 --all-files 时，scope=all-files，检查 Hugging Face 仓库当前已知的 12 个文件。

选项：
  --model-dir DIR     覆盖模型本地目录；相对路径按仓库根目录解析。
  --all-files         检查完整仓库文件；默认只检查必需文件。
  --json              输出机器可读 JSON。
  -h, --help          显示帮助。

常用示例：
  ./scripts/models.sh status htdemucs-ft
  ./scripts/models.sh status htdemucs-ft --model-dir .data/models/htdemucs-ft
  ./scripts/models.sh status htdemucs-ft --all-files
  ./scripts/models.sh status htdemucs-ft --json
EOF
}

verify_usage() {
  cat <<EOF
用法：
  ./scripts/models.sh verify <model> [--model-dir DIR] [--revision REV] [--remote-check] [--all-files]
  ./scripts/models.sh verify <model> [--model-dir DIR] [--all-files] [--json]
  ./scripts/models.sh verify -h|--help

说明：
  默认 scope=required，只执行本地必需文件校验，缺失或空文件会返回 4。
  传 --remote-check 时，额外调用 hf cache verify 校验本地已有文件和 Hugging Face revision。
  传 --all-files 时，scope=all-files，并要求完整仓库文件都存在。

选项：
  --model-dir DIR     覆盖模型本地目录；相对路径按仓库根目录解析。
  --revision REV      Hugging Face revision，建议正式复现时固定 commit hash。
  --remote-check      调用 hf cache verify；可能访问网络。
  --all-files         校验完整仓库文件；默认只校验必需文件。
  --token TOKEN       传给 hf；当前 htdemucs-ft 不需要 token，保留给私有仓库。
  --json              只输出本地检查 JSON；不能和 --remote-check 同用。
  -h, --help          显示帮助。

常用示例：
  ./scripts/models.sh verify htdemucs-ft
  ./scripts/models.sh verify htdemucs-ft --all-files
  ./scripts/models.sh verify htdemucs-ft --remote-check
  HF_ENDPOINT=https://hf-mirror.com ./scripts/models.sh verify htdemucs-ft --remote-check
  ./scripts/models.sh verify htdemucs-ft --remote-check --all-files --revision <commit-sha>
  HF_ENDPOINT=https://hf-mirror.com ./scripts/models.sh verify htdemucs-ft --remote-check --all-files

Exit Codes:
  0  校验通过
  2  非法参数、未知模型或缺少 hf
  4  本地文件缺失、为空或远端校验失败
EOF
}

download_usage() {
  cat <<EOF
用法：
  ./scripts/models.sh download <model> [--model-dir DIR] [--revision REV] [--dry-run]
  ./scripts/models.sh download -h|--help

说明：
  通过官方 hf download 下载模型到约定目录。默认只下载当前模型运行必需文件。
  下载逻辑完全交给 hf CLI，不在本脚本中重写断点续传或差异下载。

选项：
  --model-dir DIR     覆盖模型本地目录；相对路径按仓库根目录解析。
  --source SOURCE     下载源；当前只支持 huggingface。
  --revision REV      Hugging Face revision，建议正式复现时固定 commit hash。
  --dry-run           只让 hf 计算将下载的文件，不写模型文件。
  --all-files         下载仓库全部文件；默认不下载 fp16 权重、README 等非必需文件。
  --token TOKEN       传给 hf；当前 htdemucs-ft 不需要 token，保留给私有仓库。
  --max-workers N     传给 hf download 的并发数。
  -h, --help          显示帮助。

默认模型：
  htdemucs-ft -> Hugging Face repo StemSplitio/htdemucs-ft-onnx
              -> .data/models/htdemucs-ft

常用示例：
  ./scripts/models.sh download htdemucs-ft
  ./scripts/models.sh download htdemucs-ft --dry-run
  ./scripts/models.sh download htdemucs-ft --revision <commit-sha>
  HF_ENDPOINT=https://hf-mirror.com ./scripts/models.sh download htdemucs-ft --dry-run
  HF_ENDPOINT=https://hf-mirror.com ./scripts/models.sh download htdemucs-ft
  HF_ENDPOINT=https://hf-mirror.com ./scripts/models.sh download htdemucs-ft --all-files
  HF_ENDPOINT=https://hf-mirror.com ./scripts/models.sh download htdemucs-ft --max-workers 1

Exit Codes:
  0  下载命令成功，且非 dry-run 时本地必需文件存在且非空
  2  非法参数、未知模型、不支持的 source 或缺少 hf
  4  hf 下载失败，或下载后必需文件缺失/为空
EOF
}

model_repo() {
  case "$1" in
    htdemucs-ft) printf "%s" "StemSplitio/htdemucs-ft-onnx" ;;
    *) die "unknown model: $1" 2 ;;
  esac
}

model_source() {
  case "$1" in
    htdemucs-ft) printf "%s" "huggingface" ;;
    *) die "unknown model: $1" 2 ;;
  esac
}

model_default_dir() {
  case "$1" in
    htdemucs-ft) printf "%s" ".data/models/htdemucs-ft" ;;
    *) die "unknown model: $1" 2 ;;
  esac
}

model_required_files() {
  case "$1" in
    htdemucs-ft)
      cat <<EOF
htdemucs_ft_drums.onnx
htdemucs_ft_bass.onnx
htdemucs_ft_other.onnx
htdemucs_ft_vocals.onnx
bag_infer.py
requirements.txt
EOF
      ;;
    *) die "unknown model: $1" 2 ;;
  esac
}

model_optional_files() {
  case "$1" in
    htdemucs-ft)
      cat <<EOF
.gitattributes
README.md
htdemucs_ft_bass_fp16weights.onnx
htdemucs_ft_drums_fp16weights.onnx
htdemucs_ft_other_fp16weights.onnx
htdemucs_ft_vocals_fp16weights.onnx
EOF
      ;;
    *) die "unknown model: $1" 2 ;;
  esac
}

model_files_for_scope() {
  local model="$1"
  local scope="$2"

  model_required_files "$model"
  if [[ "$scope" == "all-files" ]]; then
    model_optional_files "$model"
  fi
}

json_escape() {
  local value="$1"
  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"
  value="${value//$'\n'/\\n}"
  printf "%s" "$value"
}

resolve_model_dir() {
  local model="$1"
  local override="${2:-}"
  if [[ -n "$override" ]]; then
    resolve_repo_path "$override"
  else
    resolve_repo_path "$(model_default_dir "$model")"
  fi
}

run_hf() {
  if [[ -n "${HF_CLI:-}" ]]; then
    require_command "$HF_CLI" "install Hugging Face hf CLI or unset HF_CLI"
    "$HF_CLI" "$@"
  elif command -v hf >/dev/null 2>&1; then
    hf "$@"
  elif command -v uv >/dev/null 2>&1; then
    UV_CACHE_DIR="${UV_CACHE_DIR:-$ROOT_DIR/.uv-cache}" uv run hf "$@"
  else
    die "hf is not available; install Hugging Face CLI or run: ./scripts/dev.sh bootstrap" 2
  fi
}

missing_files_for_scope() {
  local model="$1"
  local dir="$2"
  local scope="$3"
  local file
  local path

  while IFS= read -r file; do
    path="$dir/$file"
    if [[ ! -s "$path" ]]; then
      printf "%s\n" "$file"
    fi
  done < <(model_files_for_scope "$model" "$scope")
}

files_complete_for_scope() {
  local model="$1"
  local dir="$2"
  local scope="$3"
  local file
  local path

  while IFS= read -r file; do
    path="$dir/$file"
    if [[ ! -s "$path" ]]; then
      return 1
    fi
  done < <(model_files_for_scope "$model" "$scope")
  return 0
}

print_status_human() {
  local model="$1"
  local dir="$2"
  local scope="$3"
  local repo
  local source
  local file
  local path
  local status
  repo="$(model_repo "$model")"
  source="$(model_source "$model")"

  section "Model"
  row "model" "$model" ""
  row "source" "$source" "$repo"
  row "local-dir" "$dir" ""
  row "scope" "$scope" "$([[ "$scope" == "all-files" ]] && printf 'complete Hugging Face repo' || printf 'runtime required files')"

  if [[ "$scope" == "all-files" ]]; then
    section "All Files"
  else
    section "Required Files"
  fi
  while IFS= read -r file; do
    path="$dir/$file"
    if [[ -s "$path" ]]; then
      status="OK"
    else
      status="MISSING"
    fi
    event "$status" "$file" "$path"
  done < <(model_files_for_scope "$model" "$scope")
}

print_status_json() {
  local model="$1"
  local dir="$2"
  local scope="$3"
  local repo
  local source
  local file
  local path
  local first=true
  local complete=true
  repo="$(model_repo "$model")"
  source="$(model_source "$model")"

  files_complete_for_scope "$model" "$dir" "$scope" || complete=false

  printf '{"model":"%s","source":"%s","repo":"%s","local_dir":"%s","scope":"%s","complete":%s,"files":[' \
    "$(json_escape "$model")" \
    "$(json_escape "$source")" \
    "$(json_escape "$repo")" \
    "$(json_escape "$dir")" \
    "$(json_escape "$scope")" \
    "$complete"

  while IFS= read -r file; do
    path="$dir/$file"
    if [[ "$first" == "true" ]]; then
      first=false
    else
      printf ','
    fi
    printf '{"path":"%s","required":true,"present":%s,"non_empty":%s}' \
      "$(json_escape "$file")" \
      "$([[ -e "$path" ]] && printf true || printf false)" \
      "$([[ -s "$path" ]] && printf true || printf false)"
  done < <(model_files_for_scope "$model" "$scope")
  printf ']}\n'
}

run_list() {
  local json_output=false

  while [[ $# -gt 0 ]]; do
    case "$1" in
      -h|--help)
        list_usage
        return 0
        ;;
      --json)
        json_output=true
        shift
        ;;
      *)
        list_usage >&2
        return 2
        ;;
    esac
  done

  if [[ "$json_output" == "true" ]]; then
    printf '[{"model":"htdemucs-ft","source":"huggingface","repo":"StemSplitio/htdemucs-ft-onnx","default_dir":".data/models/htdemucs-ft"}]\n'
  else
    section "Models"
    row "htdemucs-ft" "huggingface" "StemSplitio/htdemucs-ft-onnx -> .data/models/htdemucs-ft"
  fi
}

parse_model_dir_args() {
  # Sets PARSED_MODEL_DIR_OVERRIDE, PARSED_ALL_FILES and PARSED_JSON_OUTPUT for status-like commands.
  PARSED_MODEL_DIR_OVERRIDE=""
  PARSED_ALL_FILES=false
  PARSED_JSON_OUTPUT=false
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --model-dir)
        [[ $# -ge 2 ]] || die "--model-dir requires a value" 2
        PARSED_MODEL_DIR_OVERRIDE="$2"
        shift 2
        ;;
      --model-dir=*)
        PARSED_MODEL_DIR_OVERRIDE="${1#--model-dir=}"
        shift
        ;;
      --json)
        PARSED_JSON_OUTPUT=true
        shift
        ;;
      --all-files)
        PARSED_ALL_FILES=true
        shift
        ;;
      *)
        die "unknown argument: $1" 2
        ;;
    esac
  done
}

run_status() {
  local model="${1:-}"
  local dir
  local scope

  if [[ "$model" == "-h" || "$model" == "--help" || -z "$model" ]]; then
    if [[ -n "$model" ]]; then
      status_usage
      return 0
    fi
    status_usage >&2
    return 2
  fi
  shift

  parse_model_dir_args "$@"
  dir="$(resolve_model_dir "$model" "$PARSED_MODEL_DIR_OVERRIDE")"
  if [[ "$PARSED_ALL_FILES" == "true" ]]; then
    scope="all-files"
  else
    scope="required"
  fi
  if [[ "$PARSED_JSON_OUTPUT" == "true" ]]; then
    print_status_json "$model" "$dir" "$scope"
  else
    print_status_human "$model" "$dir" "$scope"
  fi
}

run_verify() {
  local model="${1:-}"
  local dir
  local revision=""
  local remote_check=false
  local all_files=false
  local token=""
  local json_output=false
  local missing
  local hf_args
  local scope

  if [[ "$model" == "-h" || "$model" == "--help" || -z "$model" ]]; then
    if [[ -n "$model" ]]; then
      verify_usage
      return 0
    fi
    verify_usage >&2
    return 2
  fi
  shift

  PARSED_MODEL_DIR_OVERRIDE=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --model-dir)
        [[ $# -ge 2 ]] || die "--model-dir requires a value" 2
        PARSED_MODEL_DIR_OVERRIDE="$2"
        shift 2
        ;;
      --model-dir=*)
        PARSED_MODEL_DIR_OVERRIDE="${1#--model-dir=}"
        shift
        ;;
      --revision)
        [[ $# -ge 2 ]] || die "--revision requires a value" 2
        revision="$2"
        shift 2
        ;;
      --revision=*)
        revision="${1#--revision=}"
        shift
        ;;
      --remote-check)
        remote_check=true
        shift
        ;;
      --all-files)
        all_files=true
        shift
        ;;
      --token)
        [[ $# -ge 2 ]] || die "--token requires a value" 2
        token="$2"
        shift 2
        ;;
      --token=*)
        token="${1#--token=}"
        shift
        ;;
      --json)
        json_output=true
        shift
        ;;
      *)
        verify_usage >&2
        return 2
        ;;
    esac
  done

  [[ "$remote_check" == "false" || "$json_output" == "false" ]] || die "--json cannot be combined with --remote-check because hf output is not controlled by this script" 2

  dir="$(resolve_model_dir "$model" "$PARSED_MODEL_DIR_OVERRIDE")"
  if [[ "$all_files" == "true" ]]; then
    scope="all-files"
  else
    scope="required"
  fi
  if [[ "$json_output" == "true" ]]; then
    print_status_json "$model" "$dir" "$scope"
  else
    print_status_human "$model" "$dir" "$scope"
  fi

  missing="$(missing_files_for_scope "$model" "$dir" "$scope")"
  if [[ -z "$missing" ]]; then
    :
  else
    if [[ "$json_output" != "true" ]]; then
      printf "ERROR: model files for scope '%s' are missing or empty:\n%s\n" "$scope" "$missing" >&2
    fi
    return 4
  fi

  if [[ "$remote_check" == "true" ]]; then
    section "Remote Check"
    if [[ "$all_files" == "false" ]]; then
      event "NOTE" "scope" "required; hf may warn about optional remote files missing locally"
    fi
    hf_args=(cache verify "$(model_repo "$model")" --local-dir "$dir")
    if [[ "$all_files" == "true" ]]; then
      hf_args+=(--fail-on-missing-files)
    fi
    [[ -z "$revision" ]] || hf_args+=(--revision "$revision")
    [[ -z "$token" ]] || hf_args+=(--token "$token")
    run_hf "${hf_args[@]}" || return 4
  fi
}

run_download() {
  local model="${1:-}"
  local source="huggingface"
  local dir_override=""
  local dir
  local revision=""
  local dry_run=false
  local all_files=false
  local token=""
  local max_workers=""
  local hf_args
  local file

  if [[ "$model" == "-h" || "$model" == "--help" || -z "$model" ]]; then
    if [[ -n "$model" ]]; then
      download_usage
      return 0
    fi
    download_usage >&2
    return 2
  fi
  shift

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --model-dir)
        [[ $# -ge 2 ]] || die "--model-dir requires a value" 2
        dir_override="$2"
        shift 2
        ;;
      --model-dir=*)
        dir_override="${1#--model-dir=}"
        shift
        ;;
      --source)
        [[ $# -ge 2 ]] || die "--source requires a value" 2
        source="$2"
        shift 2
        ;;
      --source=*)
        source="${1#--source=}"
        shift
        ;;
      --revision)
        [[ $# -ge 2 ]] || die "--revision requires a value" 2
        revision="$2"
        shift 2
        ;;
      --revision=*)
        revision="${1#--revision=}"
        shift
        ;;
      --dry-run)
        dry_run=true
        shift
        ;;
      --all-files)
        all_files=true
        shift
        ;;
      --token)
        [[ $# -ge 2 ]] || die "--token requires a value" 2
        token="$2"
        shift 2
        ;;
      --token=*)
        token="${1#--token=}"
        shift
        ;;
      --max-workers)
        [[ $# -ge 2 ]] || die "--max-workers requires a value" 2
        max_workers="$2"
        shift 2
        ;;
      --max-workers=*)
        max_workers="${1#--max-workers=}"
        shift
        ;;
      *)
        download_usage >&2
        return 2
        ;;
    esac
  done

  [[ "$source" == "huggingface" ]] || die "unsupported source '$source'; only huggingface is supported and no fallback is attempted" 2

  dir="$(resolve_model_dir "$model" "$dir_override")"
  mkdir -p "$dir"

  section "Download"
  row "model" "$model" ""
  row "repo" "huggingface" "$(model_repo "$model")"
  row "local-dir" "$dir" ""
  [[ -z "$revision" ]] || row "revision" "$revision" ""
  [[ "$dry_run" == "false" ]] || event "DRY-RUN" "$model" "hf will not write model files"

  hf_args=(download "$(model_repo "$model")" --local-dir "$dir")
  if [[ "$all_files" == "false" ]]; then
    while IFS= read -r file; do
      hf_args+=(--include "$file")
    done < <(model_required_files "$model")
  fi
  [[ -z "$revision" ]] || hf_args+=(--revision "$revision")
  [[ "$dry_run" == "false" ]] || hf_args+=(--dry-run)
  [[ -z "$token" ]] || hf_args+=(--token "$token")
  [[ -z "$max_workers" ]] || hf_args+=(--max-workers "$max_workers")

  run_hf "${hf_args[@]}" || return 4

  if [[ "$dry_run" == "false" ]]; then
    if [[ "$all_files" == "true" ]]; then
      run_verify "$model" --model-dir "$dir" --all-files
    else
      run_verify "$model" --model-dir "$dir"
    fi
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
  list)
    shift
    run_list "$@"
    ;;
  status)
    shift
    run_status "$@"
    ;;
  verify)
    shift
    run_verify "$@"
    ;;
  download)
    shift
    run_download "$@"
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
