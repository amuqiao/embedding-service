# audio_stem_separation 开发服务器准备与验证 Runbook

本文说明如何在开发服务器准备 `audio_stem_separation` 需要的 htdemucs-ft ONNX 模型、测试音频和真实流程验证命令。核心原则是：服务部署仍交给 `./scripts/deploy.sh`，模型和测试数据作为前置资产由人按 runbook 显式准备。

本文不负责自动部署生产环境，不把模型下载塞进 `deploy.sh`，不管理远端密钥，不修改云平台或 K8s 资源。模型下载、测试音频拷贝、音频校验和真实 Job 验证都必须由执行者明确运行命令。

## 先理解运行边界

`audio_stem_separation` 和普通文本 Job 不一样，它依赖两类本地资产：

```text
模型文件
  htdemucs-ft ONNX required 文件
  默认路径：.data/models/htdemucs-ft
  管理入口：./scripts/models.sh

测试音频
  WAV / 44100 Hz / 双声道
  示例路径：.data/misc/2485_0003_S6_梁萧.wav
  管理入口：./scripts/media.sh audio
```

服务部署和资产准备是两条线：

| 动作 | 执行环境 | 入口 |
|---|---|---|
| 启动 PostgreSQL / Redis / API / worker | 开发服务器 | `./scripts/deploy.sh` |
| 下载 htdemucs-ft ONNX 模型 | 开发服务器 | `./scripts/models.sh download htdemucs-ft` |
| 拷贝本机测试音频到开发服务器 | 本机 | `rsync` 或 `scp` |
| 校验测试音频格式 | 开发服务器 | `./scripts/media.sh audio verify htdemucs-input ...` |
| 提交真实 `audio_stem_separation` Job | 开发服务器，或本机远程调 API | `./scripts/real-flow.sh audio-stem-separation ...` |

最容易踩坑的是 worker 读取模型的位置：

```text
宿主机下载成功
  不等于
worker 运行进程一定读得到模型
```

如果 worker 直接运行在开发服务器宿主机，`HTDEMUCS_MODEL_DIR=.data/models/htdemucs-ft` 会解析到仓库根目录下的 `.data/models/htdemucs-ft`。如果使用 `compose-full`，worker 在容器内运行，模型目录必须能在容器内看到，通常应是 `/app/.data/models/htdemucs-ft`。

## 约定变量

下面命令默认开发服务器地址是 `47.94.108.140`。先在本机 shell 设置连接变量，`SSH_TARGET` 和 `REMOTE_REPO` 按你的真实环境修改：

```bash
export DEV_SERVER=47.94.108.140
export SSH_TARGET=<ssh-user>@${DEV_SERVER}
export REMOTE_REPO=/path/to/fastapi-best-ai-architecture
export LOCAL_REPO=/Users/admin/Code/fastapi-best-ai-architecture
export TEST_AUDIO=.data/misc/2485_0003_S6_梁萧.wav
```

这些变量只对当前本机 shell 生效。登录开发服务器后，需要在远端 shell 再设置一次 `REMOTE_REPO` 和 `TEST_AUDIO`。

确认本机能连上开发服务器：

```bash
ssh "$SSH_TARGET" "hostname && pwd"
```

确认开发服务器上已经有本仓库：

```bash
ssh "$SSH_TARGET" "cd '$REMOTE_REPO' && pwd && git status --short"
```

## 本机：拷贝测试音频到开发服务器

如果测试音频只在本机，先在本机确认格式：

```bash
cd "$LOCAL_REPO"

./scripts/media.sh audio probe "$TEST_AUDIO"
./scripts/media.sh audio verify htdemucs-input "$TEST_AUDIO"
```

把测试音频拷贝到开发服务器同名路径：

```bash
cd "$LOCAL_REPO"

ssh "$SSH_TARGET" "mkdir -p '$REMOTE_REPO/.data/misc'"
rsync -av "$TEST_AUDIO" "$SSH_TARGET:$REMOTE_REPO/$TEST_AUDIO"
```

如果开发服务器没有 `rsync`，改用 `scp`：

```bash
cd "$LOCAL_REPO"

ssh "$SSH_TARGET" "mkdir -p '$REMOTE_REPO/.data/misc'"
scp "$TEST_AUDIO" "$SSH_TARGET:$REMOTE_REPO/$TEST_AUDIO"
```

## 开发服务器：安装运行依赖

登录开发服务器：

```bash
ssh "$SSH_TARGET"

export REMOTE_REPO=/path/to/fastapi-best-ai-architecture
export TEST_AUDIO=.data/misc/2485_0003_S6_梁萧.wav

cd "$REMOTE_REPO"
```

如果服务或真实流程脚本运行在开发服务器宿主机，先安装项目依赖和音频分离可选依赖：

```bash
uv sync --extra audio-separation
```

如果缺少 `ffmpeg` / `ffprobe`，先按开发服务器系统安装。例如 Debian / Ubuntu：

```bash
sudo apt-get update
sudo apt-get install -y ffmpeg
```

确认脚本帮助可用：

```bash
./scripts/models.sh --help
./scripts/media.sh --help
./scripts/real-flow.sh audio-stem-separation -h
```

## 开发服务器：下载和校验模型

先 dry-run，确认下载范围和路径：

```bash
HF_ENDPOINT=https://hf-mirror.com \
./scripts/models.sh download htdemucs-ft --dry-run
```

正式下载默认只下载当前运行必需的 required 文件：

```bash
HF_ENDPOINT=https://hf-mirror.com \
./scripts/models.sh download htdemucs-ft
```

下载中断时可以重复执行同一条命令，`hf` 会继续补齐缺失文件：

```bash
HF_ENDPOINT=https://hf-mirror.com \
./scripts/models.sh download htdemucs-ft
```

校验本地 required 文件：

```bash
./scripts/models.sh verify htdemucs-ft
```

查看模型目录：

```bash
./scripts/models.sh status htdemucs-ft
./scripts/models.sh list
```

探测 4 个 ONNX 专家模型的 I/O 签名和 sha256：

```bash
mkdir -p .run

./scripts/models.sh inspect htdemucs-ft \
  --providers CPUExecutionProvider \
  --json > .run/htdemucs-ft-onnx-inspect.json
```

如果开发服务器要强制 CUDA，先确认运行环境已经安装 GPU 版 ONNX Runtime，再检查 provider：

```bash
python -c "import onnxruntime as ort; print(ort.get_available_providers())"
```

期望列表中包含：

```text
CUDAExecutionProvider
CPUExecutionProvider
```

## 开发服务器：校验测试音频

确认从本机拷贝过来的测试音频存在：

```bash
test -f "$TEST_AUDIO" && ls -lh "$TEST_AUDIO"
```

探测音频：

```bash
./scripts/media.sh audio probe "$TEST_AUDIO"
```

校验是否满足 htdemucs 输入规格：

```bash
./scripts/media.sh audio verify htdemucs-input "$TEST_AUDIO"
```

通过时应满足：

```text
container      wav
sample rate    44100
channels       2
```

如果输入不合规，转换成标准 WAV：

```bash
mkdir -p .data/audio

./scripts/media.sh audio prepare htdemucs-input "$TEST_AUDIO" \
  --output .data/audio/htdemucs-input.wav

./scripts/media.sh audio verify htdemucs-input .data/audio/htdemucs-input.wav
```

后续真实流程把 `TEST_AUDIO` 换成 `.data/audio/htdemucs-input.wav` 即可。

## 开发服务器：配置环境

检查 `.env` 里至少包含下面配置。不要把真实密钥写进文档或提交到 git。

```bash
grep -E '^(HTDEMUCS_MODEL_DIR|AUDIO_STEM_SEPARATION_EXECUTION_PROVIDER|STORAGE_BACKEND|API_HOST|API_PORT|SERVICE_API_PREFIX)=' .env
```

推荐开发服务器 CPU 首次验证使用：

```bash
HTDEMUCS_MODEL_DIR=.data/models/htdemucs-ft
AUDIO_STEM_SEPARATION_EXECUTION_PROVIDER=auto
```

GPU 开发服务器确认 CUDA 可用后，可以改成：

```bash
HTDEMUCS_MODEL_DIR=.data/models/htdemucs-ft
AUDIO_STEM_SEPARATION_EXECUTION_PROVIDER=cuda
```

`cuda` 是强制模式。没有 `CUDAExecutionProvider`，或者 ONNX Runtime session 实际没有跑在 CUDA 上，Job 应该失败，便于尽早暴露部署问题。

## 开发服务器：部署服务

部署仍使用现有入口：

```bash
./scripts/deploy.sh check
./scripts/deploy.sh up compose-full
./scripts/deploy.sh status compose-full
```

如果只启动 PostgreSQL / Redis，API 和 worker 由宿主机其他入口运行，则使用：

```bash
./scripts/deploy.sh up compose-deps
./scripts/deploy.sh status compose-deps
```

不要把模型下载放进 `deploy.sh`。模型是大文件资产，失败原因包括网络、镜像源、磁盘空间和远端限流，应作为显式前置步骤处理。

## compose-full：确认 worker 能看到模型和依赖

如果使用 `compose-full`，必须在 worker 容器里确认模型目录和运行依赖。宿主机模型校验通过，不代表容器内可见。

检查容器内配置值：

```bash
docker compose --profile app exec -T worker python - <<'PY'
from app.core.config import get_settings

settings = get_settings()
print("HTDEMUCS_MODEL_DIR =", settings.job.htdemucs_model_dir)
print("AUDIO_STEM_SEPARATION_EXECUTION_PROVIDER =", settings.job.audio_stem_separation_execution_provider)
PY
```

检查容器内 Python 依赖：

```bash
docker compose --profile app exec -T worker python - <<'PY'
import onnxruntime as ort
import soundfile

print("onnxruntime providers =", ort.get_available_providers())
print("soundfile =", soundfile.__version__)
PY
```

检查容器内模型文件：

```bash
docker compose --profile app exec worker \
  /app/scripts/models.sh verify htdemucs-ft --model-dir /app/.data/models/htdemucs-ft
```

如果这里失败，优先按下面顺序判断：

| 现象 | 含义 | 处理方向 |
|---|---|---|
| 宿主机 `models.sh verify` 通过，容器内失败 | 模型没有挂载进 worker 容器 | 给 compose-full 增加模型目录 bind mount，或改成容器内可见的模型路径 |
| 容器内 `import onnxruntime` 失败 | 镜像没有安装音频分离运行依赖 | 构建包含 `audio-separation` 依赖的镜像，或在运行环境显式安装 |
| 设置了 `cuda` 但 provider 里没有 `CUDAExecutionProvider` | 容器没有 GPU 版 ONNX Runtime 或没有拿到 GPU | 检查 GPU 镜像、CUDA runtime、NVIDIA container runtime 和 Pod/容器 GPU 资源 |

当前仓库的 `.data/` 不进入 git，也被 `.dockerignore` 排除。因此不要期待重新 build 镜像后自动带上模型文件。

## 开发服务器：构建真实 Job 入参

先构建 payload，不提交 Job：

```bash
mkdir -p .run

./scripts/real-flow.sh audio-stem-separation build-payload \
  --env-file .env \
  --input-file "$TEST_AUDIO" \
  --output .run/audio-stem-payload.json
```

如果 `.env` 使用 `STORAGE_BACKEND=aliyun_oss`，本地音频会上传到 OSS 才能生成可被 worker 读取的 `input_audio` URL Ref，需要显式确认上传：

```bash
mkdir -p .run

./scripts/real-flow.sh audio-stem-separation build-payload \
  --env-file .env \
  --confirm-upload \
  --input-file "$TEST_AUDIO" \
  --output .run/audio-stem-payload.json
```

查看 payload 摘要：

```bash
python -m json.tool .run/audio-stem-payload.json | sed -n '1,120p'
```

## 开发服务器：提交真实 Job 并下载输出

提交真实 Job，等待终态，并下载 `drums`、`bass`、`other`、`vocals` 四条 stem：

```bash
./scripts/real-flow.sh audio-stem-separation run \
  --confirm-run \
  --env-file .env \
  --payload-file .run/audio-stem-payload.json \
  --download-outputs \
  --json
```

如果直接用本地音频提交，不提前生成 payload：

```bash
./scripts/real-flow.sh audio-stem-separation run \
  --confirm-run \
  --env-file .env \
  --input-file "$TEST_AUDIO" \
  --download-outputs \
  --json
```

`STORAGE_BACKEND=aliyun_oss` 时加 `--confirm-upload`：

```bash
./scripts/real-flow.sh audio-stem-separation run \
  --confirm-run \
  --confirm-upload \
  --env-file .env \
  --input-file "$TEST_AUDIO" \
  --download-outputs \
  --json
```

成功时重点看这些字段：

```text
summary.job_status                       succeeded
summary.stems_count                      4
summary.stems.drums.public_url           非空
summary.stems.bass.public_url            非空
summary.stems.other.public_url           非空
summary.stems.vocals.public_url          非空
responses.get_job.data.job.job_result.execution_provider
```

CPU 环境通常应看到：

```text
execution_provider = CPUExecutionProvider
```

GPU 强制模式应看到：

```text
execution_provider = CUDAExecutionProvider
```

## 本机：远程调用开发服务器 API

如果服务已经部署在开发服务器，本机也可以直接调用远端 API 做真实流程验证。这个模式适合本机有测试音频、但不想先登录开发服务器执行 real-flow。

在本机执行：

```bash
cd "$LOCAL_REPO"

./scripts/real-flow.sh audio-stem-separation run \
  --confirm-run \
  --confirm-upload \
  --allow-remote-api \
  --api-url "http://${DEV_SERVER}:8100" \
  --env-file .env \
  --input-file "$TEST_AUDIO" \
  --download-outputs \
  --json
```

注意：

```text
本机负责上传输入音频
开发服务器 API 负责创建 Job
开发服务器 worker 负责读取模型并执行推理
输出对象写到开发服务器配置的 storage backend
```

所以远程调用成功的前提仍然是：开发服务器 worker 已经能看到模型文件，并且运行依赖正确。

## 常见问题

### 下载模型成功，但 Job 报模型缺失

先确认 Job 实际在哪个环境执行。

宿主机执行 worker：

```bash
./scripts/models.sh verify htdemucs-ft
```

compose-full 执行 worker：

```bash
docker compose --profile app exec worker \
  /app/scripts/models.sh verify htdemucs-ft --model-dir /app/.data/models/htdemucs-ft
```

如果只有宿主机通过，说明模型没有进入容器。

### `client_request_id conflict`

同一个 payload 被重复提交时，服务会按幂等键拒绝创建重复 Job。重新生成 payload，或者显式换一个 `--client-request-id`：

```bash
./scripts/real-flow.sh audio-stem-separation build-payload \
  --env-file .env \
  --input-file "$TEST_AUDIO" \
  --client-request-id "audio-stem-$(date +%s)" \
  --output .run/audio-stem-payload.json
```

### 远程 API 被脚本拒绝

`real-flow.sh` 默认保护本机调用。访问 `47.94.108.140` 这类远端 API 时必须显式传：

```bash
--allow-remote-api
```

### 需要下载完整 Hugging Face 仓库吗

默认不需要。当前 `audio_stem_separation` required 范围只需要 4 个 fp32 专家 ONNX、`bag_infer.py`、`requirements.txt`。

只有完整镜像仓库、离线归档、研究 fp16 权重或要求本地目录和远端仓库文件清单完全一致时，才使用：

```bash
HF_ENDPOINT=https://hf-mirror.com \
./scripts/models.sh download htdemucs-ft --all-files
```

### GPU 服务器是否必须有 GPU Pod 或 GPU 容器

是。`AUDIO_STEM_SEPARATION_EXECUTION_PROVIDER=cuda` 只是在应用层要求 ONNX Runtime 使用 CUDA provider。底层仍需要：

```text
机器有 NVIDIA GPU
驱动和 CUDA runtime 可用
容器或 Pod 被分配 GPU
Python 环境安装 GPU 版 onnxruntime
```

否则 `cuda` 模式应该失败，而不是静默退回 CPU。
