# htdemucs-ft ONNX 本地模型与音频数据准备 Runbook

本文说明如何在本仓库本地准备 `htdemucs-ft` ONNX 模型文件和可用于后续 `audio_stem_separation` 接入验证的音频输入。推荐心智模型是：先准备模型资产，再准备 44.1kHz 双声道 WAV，最后分别用 `models.sh` 和 `media.sh` 做可重复校验。

本文只覆盖**本地资产和本地素材准备**。它不负责实现或提交 `audio_stem_separation` Job，不执行模型推理，不上传对象存储，也不覆盖 GPU / 生产部署。`audio_stem_separation` 当前仍是计划中的能力，接入计划见 [`../plans/htdemucs-audio-separation-integration.md`](../plans/htdemucs-audio-separation-integration.md)。

## 先理解这件事

`htdemucs-ft` 本地准备分成两类文件：

```text
模型资产
  .data/models/htdemucs-ft/
  由 ./scripts/models.sh 管理

测试音频
  .data/audio/*.wav 或已有本地 wav
  由 ./scripts/media.sh audio 管理
```

两类入口职责不同：

| 入口 | 负责 | 不负责 |
|---|---|---|
| `./scripts/models.sh` | 下载和校验 `htdemucs-ft` ONNX 模型文件 | 音频转码、模型推理、Job 提交 |
| `./scripts/media.sh audio` | 探测、转换和校验本地音频素材 | 模型下载、模型推理、对象存储上传 |

当前 htdemucs 本地推理需要的模型范围是 `required`，也就是 6 个文件：4 个 fp32 专家 ONNX、`bag_infer.py`、`requirements.txt`。完整 Hugging Face 仓库里的 4 个 `*_fp16weights.onnx`、`README.md`、`.gitattributes` 属于 `--all-files` 范围；本地 macOS CPU 验证默认不需要下载这些文件。

音频输入要求是：

```text
container      WAV
sample rate    44100 Hz
channels       2
```

## 前置工具

模型下载需要 `hf` CLI；本仓库脚本找不到 `hf` 时会尝试通过 `uv run hf` 执行。ONNX 签名探测需要可选依赖 `audio-separation` 中的 `onnxruntime`。音频探测和转换需要 `ffprobe` / `ffmpeg`。

如果当前虚拟环境还没安装音频分离相关可选依赖：

```bash
uv sync --extra audio-separation
```

macOS 上如果缺少 `ffmpeg`：

```bash
brew install ffmpeg
```

查看脚本入口说明：

```bash
./scripts/models.sh --help
./scripts/media.sh --help
```

这两条只打印帮助，不会访问网络，也不会检查 `hf` / `ffmpeg` / `ffprobe` 是否可执行。真正的依赖检查会在后续 `download`、`probe`、`prepare` 命令执行时触发。

如果 `ffmpeg` 或 `ffprobe` 不在 `PATH`，可以显式指定：

```bash
FFMPEG_BIN=/path/to/ffmpeg \
FFPROBE_BIN=/path/to/ffprobe \
./scripts/media.sh audio probe .data/audio/input.wav
```

## 下载模型

优先先做 dry-run，确认将要下载的文件范围和路径：

```bash
HF_ENDPOINT=https://hf-mirror.com \
./scripts/models.sh download htdemucs-ft --dry-run
```

正式下载默认只下载 `required` 范围：

```bash
HF_ENDPOINT=https://hf-mirror.com \
./scripts/models.sh download htdemucs-ft
```

默认路径是：

```text
.data/models/htdemucs-ft
```

下载完成后做本地校验：

```bash
./scripts/models.sh verify htdemucs-ft
```

探测 4 个 ONNX 专家模型的 I/O 签名和 sha256：

```bash
./scripts/models.sh inspect htdemucs-ft --providers CPUExecutionProvider
```

机器可读探测结果可以保存为阶段 0 证据，后续人工确认后再写入 `model_asset.yaml`：

```bash
mkdir -p .run
./scripts/models.sh inspect htdemucs-ft --providers CPUExecutionProvider --json > .run/htdemucs-ft-onnx-inspect.json
```

需要连远端元数据一起校验时：

```bash
HF_ENDPOINT=https://hf-mirror.com \
./scripts/models.sh verify htdemucs-ft --remote-check
```

如果网络中断或镜像连接被关闭，可以直接重复执行下载命令。`hf` 会复用本地缓存和未完成文件，继续补齐缺失内容：

```bash
HF_ENDPOINT=https://hf-mirror.com \
./scripts/models.sh download htdemucs-ft
```

## 查看模型路径和文件状态

查看脚本已知模型和默认路径：

```bash
./scripts/models.sh list
```

查看当前目录下模型文件是否齐全：

```bash
./scripts/models.sh status htdemucs-ft
```

机器可读输出：

```bash
./scripts/models.sh status htdemucs-ft --json
```

如果你只是为了本地 CPU 验证，看到 `required` 范围通过即可。不要因为缺少 `*_fp16weights.onnx` 就补 `--all-files`；这些不是当前 required 范围。

## 什么时候用 `--all-files`

默认不要用 `--all-files`。只有下面这些场景才考虑：

| 场景 | 是否需要 `--all-files` |
|---|---|
| 本地 macOS CPU 跑通 htdemucs 准备流程 | 不需要 |
| 只验证 4 个 fp32 专家 ONNX、`bag_infer.py`、`requirements.txt` | 不需要 |
| 想完整镜像 Hugging Face 仓库到开发服务器 | 可以 |
| 要研究或对比 `*_fp16weights.onnx` 文件 | 需要 |
| 要做离线归档，要求本地目录和远端仓库文件清单一致 | 需要 |

下载完整仓库：

```bash
HF_ENDPOINT=https://hf-mirror.com \
./scripts/models.sh download htdemucs-ft --all-files
```

校验完整仓库：

```bash
HF_ENDPOINT=https://hf-mirror.com \
./scripts/models.sh verify htdemucs-ft --remote-check --all-files
```

## 准备音频输入

如果你已经有 WAV，先探测它：

```bash
INPUT=.data/misc/2485_0003_S6_梁萧.wav

./scripts/media.sh audio probe "$INPUT"
```

期望输出类似：

```text
== Audio Probe ==
  file           .data/misc/2485_0003_S6_梁萧.wav
  codec          pcm_s16le
  sample-rate    44100
  channels       2
  duration-sec   11.900
  format         wav
```

再按 htdemucs 输入规格校验：

```bash
./scripts/media.sh audio verify htdemucs-input "$INPUT"
```

通过时应看到：

```text
== HTDemucs Input ==
  file           .data/misc/2485_0003_S6_梁萧.wav
OK        format             actual=wav expected=wav
OK        sample_rate        actual=44100 expected=44100
OK        channels           actual=2 expected=2
```

机器可读校验：

```bash
./scripts/media.sh audio verify htdemucs-input "$INPUT" --json
```

如果 `valid=true`，这份音频已经满足当前准备要求，不需要再转换。

## 转换不合规音频

如果输入是 `mp3`、`m4a`、非 44100Hz、单声道，先转换成标准 WAV。下面的 `INPUT` 是占位路径，复制时替换成你自己的本地音频文件：

```bash
INPUT=/path/to/input.mp3
OUTPUT=.data/audio/input.wav

./scripts/media.sh audio prepare htdemucs-input "$INPUT" --output "$OUTPUT"
```

`prepare htdemucs-input` 底层会调用 `ffmpeg` 生成 44100Hz 双声道 WAV，并调用 `ffprobe` 校验产物。默认拒绝覆盖已有输出，确实要覆盖时显式传 `--force`：

```bash
./scripts/media.sh audio prepare htdemucs-input "$INPUT" --output "$OUTPUT" --force
```

转换后再单独校验一次：

```bash
./scripts/media.sh audio verify htdemucs-input "$OUTPUT"
```

如果只想准备短音频做本地 CPU 快速验证，可以显式加时长上限。超限会返回 `4`，不会静默截断：

```bash
./scripts/media.sh audio prepare htdemucs-input "$INPUT" \
  --output "$OUTPUT" \
  --max-duration-seconds 60

./scripts/media.sh audio verify htdemucs-input "$OUTPUT" \
  --max-duration-seconds 60
```

## 一次性复制流程

下面是一套从模型到音频都可重复执行的本地准备流程。`INPUT` 可以换成你的本地音频路径。

```bash
set -euo pipefail

MODEL=htdemucs-ft
INPUT=.data/misc/2485_0003_S6_梁萧.wav
OUTPUT=.data/audio/2485_0003_S6_梁萧.wav

mkdir -p .run
HF_ENDPOINT=https://hf-mirror.com ./scripts/models.sh download "$MODEL"
./scripts/models.sh verify "$MODEL"
./scripts/models.sh inspect "$MODEL" --providers CPUExecutionProvider --json > .run/htdemucs-ft-onnx-inspect.json

./scripts/media.sh audio probe "$INPUT"
./scripts/media.sh audio verify htdemucs-input "$INPUT" || {
  ./scripts/media.sh audio prepare htdemucs-input "$INPUT" --output "$OUTPUT" --force
  ./scripts/media.sh audio verify htdemucs-input "$OUTPUT"
}
```

如果原始 `INPUT` 已经合规，脚本不会生成 `OUTPUT`。如果 `INPUT` 不合规，会生成并校验 `OUTPUT`。

## 常见问题

### `hf` 提示未登录

`StemSplitio/htdemucs-ft-onnx` 当前不是 gated repo，本地准备不需要 token。未登录只会影响 Hugging Face 请求限速或下载速度。需要镜像源时显式加：

```bash
HF_ENDPOINT=https://hf-mirror.com ./scripts/models.sh download htdemucs-ft
```

### 下载中断或 `peer closed connection`

直接重复执行同一条下载命令：

```bash
HF_ENDPOINT=https://hf-mirror.com ./scripts/models.sh download htdemucs-ft
```

下载入口是可重复执行的；已完成文件不会重新从零下载。

### `verify --remote-check` 提示远端有 optional 文件缺失

默认 `required` 范围只要求 6 个运行必需文件。远端完整仓库里的 fp16 权重、README 和 `.gitattributes` 不属于默认本地 CPU 验证要求。只有你明确要完整仓库时才使用：

```bash
HF_ENDPOINT=https://hf-mirror.com ./scripts/models.sh verify htdemucs-ft --remote-check --all-files
```

### `ffprobe` 或 `ffmpeg` 不存在

macOS 本地安装：

```bash
brew install ffmpeg
```

或者显式指定工具路径：

```bash
FFPROBE_BIN=/path/to/ffprobe ./scripts/media.sh audio probe .data/audio/input.wav
```

### 音频校验失败

看失败项：

```text
FAIL      sample_rate        actual=48000 expected=44100
FAIL      channels           actual=1 expected=2
```

然后用 `prepare htdemucs-input` 生成标准 WAV：

```bash
INPUT=/path/to/input.mp3

./scripts/media.sh audio prepare htdemucs-input "$INPUT" --output .data/audio/input.wav
./scripts/media.sh audio verify htdemucs-input .data/audio/input.wav
```

### 已经准备好后下一步是什么

当前仓库还没有可执行的 `audio_stem_separation` Job。准备完成后，能确认的是：

```text
模型 required 文件齐全
音频满足 WAV / 44100Hz / 双声道要求
```

后续实现细节统一维护在 [`../plans/htdemucs-audio-separation-integration.md`](../plans/htdemucs-audio-separation-integration.md)，不要把计划步骤复制到本文。
