#!/usr/bin/env bash
# media.sh - 本地音视频素材准备入口
#
# 运行环境：Bash；audio 子命令需要 Python 标准库，probe/verify 需要 ffprobe，prepare 需要 ffmpeg。
# 作用域：检查和准备本地音视频素材，按 audio/video 子域分发到下沉实现。
# 约束：不下载模型、不执行模型推理、不提交 Job、不上传对象存储、不做生产媒体流水线。

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT_DIR/scripts/lib/common.sh"

usage() {
  cat <<EOF
用法：
  ./scripts/media.sh <domain> <command> [args...]
  ./scripts/media.sh -h|--help

作用域：
  当前仓库的本地音视频素材准备入口。按 audio/video 子域组织功能，避免单个脚本堆积媒体处理逻辑。
  不负责模型下载、模型推理、Job 提交、对象存储上传、生产转码流水线或自动修复业务输入。

运行环境：
  Requires: Bash
  Dependencies: audio probe/verify 需要 ffprobe；audio prepare 需要 ffmpeg 和 ffprobe。

子域：
  audio               音频素材探测、业务输入校验和本地转换准备。
  video               视频素材处理预留入口；当前只提供边界说明。
  help                显示帮助。

配置与环境变量：
  FFMPEG_BIN          可选，显式指定 ffmpeg 可执行文件路径。
  FFPROBE_BIN         可选，显式指定 ffprobe 可执行文件路径。
  PYTHON_BIN          可选，显式指定 Python 可执行文件路径。

输出：
  stdout: 默认人读探测、校验或准备结果；--json 输出机器可读 JSON。
  stderr: 非法命令、非法参数、缺少依赖、输入不存在、校验失败或 ffmpeg/ffprobe 错误。

副作用与保护边界：
  audio probe/verify 不写文件、不访问网络。
  audio prepare 会写入 --output 指定文件，并创建输出父目录；默认拒绝覆盖，传 --force 才覆盖。
  不读取 .env，不访问网络，不删除输入文件。

常用示例：
  ./scripts/media.sh audio probe .data/audio/input.wav
  ./scripts/media.sh audio verify htdemucs-input .data/audio/input.wav
  ./scripts/media.sh audio prepare htdemucs-input input.mp3 --output .data/audio/input.wav
  ./scripts/media.sh audio prepare htdemucs-input input.mp3 --output .data/audio/input.wav --force
  ./scripts/media.sh audio -h

Exit Codes:
  0  成功
  2  缺少 domain、非法 domain、非法参数、输入不存在、输出已存在或缺少依赖
  4  音频不满足目标输入规格，或 prepare 后产物校验失败
EOF
}

run_python_module() {
  local module="$1"
  shift
  exec_project_python_module "$module" "$@"
}

domain="${1:-}"
case "$domain" in
  --help|-h|help)
    usage
    ;;
  audio)
    shift
    run_python_module scripts.media.audio "$@"
    ;;
  video)
    shift
    run_python_module scripts.media.video "$@"
    ;;
  "")
    usage >&2
    exit 2
    ;;
  *)
    usage >&2
    die "unknown media domain '$domain'" 2
    ;;
esac
