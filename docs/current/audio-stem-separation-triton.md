# audio_stem_separation_triton 调用链说明

本文说明 `audio_stem_separation_triton` 如何把 FastAPI Job 服务、Triton 模型服务和独立 Triton model repository 串起来。它解释当前调用链和边界，不替代 `audio-stem-separation-triton` 仓库的部署文档，也不记录一次性本地排障过程。

## 先理解三层关系

这条链路里有三个对象，职责不能混在一起：

```text
FastAPI Job 服务
  位置：本仓库
  职责：业务编排、OSS、WAV 处理、Job 状态、Callback
        |
        | Triton HTTP infer
        v
Triton 模型服务
  运行方式：tritonserver 镜像或云上 PAI-EAS Triton 服务
  职责：加载 ONNX 模型，提供 HTTP/gRPC 推理接口
        |
        | --model-repository=/models
        v
Triton model repository
  位置：独立 Triton 项目的 models/ 目录
  职责：提供 config.pbtxt、数字版本目录和 model.onnx
```

一句话：

```text
FastAPI 是调用方和业务服务
Triton 是模型运行服务器
audio-stem-separation-triton/models 是 Triton 要加载的模型仓库
```

`audio-stem-separation-triton` 不是 FastAPI 项目的一部分。它是一个独立模型服务项目，核心产物是标准 Triton model repository。GitHub 仓库负责维护配置、脚本和文档；模型服务运行时真正被 Triton 加载的是该项目的 `models/` 目录。

## Triton 镜像和模型目录是什么关系

`tritonserver` 镜像不自带本业务的 htdemucs 模型。它提供的是运行环境：

```text
tritonserver 镜像
  - tritonserver 可执行程序
  - ONNX Runtime backend
  - HTTP/gRPC 推理服务能力
```

模型文件来自独立 Triton 项目的 `models/` 目录：

```text
models/
  htdemucs_ft_drums/
    config.pbtxt
    1/model.onnx
  htdemucs_ft_bass/
    config.pbtxt
    1/model.onnx
  htdemucs_ft_other/
    config.pbtxt
    1/model.onnx
  htdemucs_ft_vocals/
    config.pbtxt
    1/model.onnx
```

Triton 运行时会把模型仓库挂进容器或服务实例：

```text
外部 Triton model repository
        |
        | 挂载
        v
容器内 /models
        |
        | tritonserver --model-repository=/models
        v
Triton 加载 4 个 ONNX 模型并提供 HTTP/gRPC 推理接口
```

外部 Triton 项目负责维护正式 `models/`、本地验证副本、启动脚本和部署说明。本 FastAPI 仓库不把这些目录当作自身稳定入口；它只依赖一个可访问的 Triton HTTP endpoint。

云上部署时应让外部 Triton 服务加载正式 model repository；本地 CPU/GPU 如何渲染和启动由外部 Triton 项目文档负责。

## FastAPI 传给 Triton 的不是音频文件

当前 FastAPI 集成不会把 WAV、MP3、OSS URL 或本地文件路径交给 Triton。FastAPI worker 会先把音频文件处理成固定 shape 的 `float32` tensor，再调用 Triton HTTP infer。

完整数据流：

```text
用户 / real-flow
  提交 input_audio URL Ref
        |
        v
FastAPI Job API
  创建 audio_stem_separation_triton Job
        |
        v
Taskiq worker
  校验 URL Ref、content_type、bucket/region 白名单
  通过 input_audio.public_url 读取 WAV bytes
  校验 sha256
  读取 WAV，要求 44100Hz stereo
  转成 float32 ndarray
  按 7.8s segment 切块，不足补 0
        |
        v
Triton HTTP infer
  每个 segment 分别请求 drums/bass/other/vocals 四个模型
        |
        v
Taskiq worker
  校验 Triton 输出 tensor
  取对应 target row
  overlap-add 合并所有 segment
  编码 4 个 WAV
  写回 OSS
        |
        v
Job result
  返回 drums/bass/other/vocals 四条 stem 的 URL Ref
```

输入 schema 与旧 `audio_stem_separation` 保持一致：

```text
input_audio:
  public_url
  internal_url
  content_type: audio/wav
  sha256: 64 位 lowercase hex，不带 sha256: 前缀
max_duration_seconds:
  可选，> 0 且 <= 3600
```

Triton 输入合同固定为：

```text
input name: mix
data_type: TYPE_FP32
shape: [1, 2, 343980]
```

含义：

```text
1        固定维度；当前不启用 Triton dynamic batching
2        双声道 stereo
343980   每个 segment 的 samples 数
```

`343980` 来自：

```text
44100 Hz * 7.8s = 343980 samples
```

FastAPI 侧实际传入 Triton client 的对象是：

```python
np.ndarray(shape=(1, 2, 343980), dtype=np.float32)
```

Triton 输出合同固定为：

```text
output name: stems
data_type: TYPE_FP32
shape: [1, 4, 2, 343980]
```

FastAPI 按模型和 row 取目标 stem：

| Triton 模型 | FastAPI 读取 |
|---|---|
| `htdemucs_ft_drums` | `stems[0, 0]` |
| `htdemucs_ft_bass` | `stems[0, 1]` |
| `htdemucs_ft_other` | `stems[0, 2]` |
| `htdemucs_ft_vocals` | `stems[0, 3]` |

这意味着即使 Triton 每次返回完整 `[1, 4, 2, 343980]`，当前 FastAPI 也只使用该 expert 对应的一行。

## 当前 job_type 如何接入

`audio_stem_separation_triton` 是独立 job_type，不替换旧的 `audio_stem_separation`。

```text
audio_stem_separation
  本服务内加载本地 ONNX Runtime session
  适合保留旧路径和本地 ONNX 验证

audio_stem_separation_triton
  本服务仍处理 OSS/WAV/Job 状态
  模型推理改为 HTTP 调 Triton 模型服务
```

两个 job_type 的输入业务合同基本一致，都接收 `input_audio` URL Ref 和可选 `max_duration_seconds`。`audio_stem_separation_triton` 的结果会明确标识：

```text
job_type: audio_stem_separation_triton
model_service: triton
triton_model_version: 来自 AUDIO_STEM_TRITON_MODEL_VERSION
```

当前 FastAPI 侧关键实现位置：

| 模块 | 职责 |
|---|---|
| `app/jobs/types/audio_stem_separation_triton/executor.py` | Job executor，负责下载音频、切 segment、调用 Triton、合成输出、写 OSS |
| `app/integrations/triton_audio_stem.py` | Triton HTTP client 封装，负责构造 `mix` input、请求 `stems` output |
| `app/schemas/jobs.py` | `AudioStemSeparationTritonParams`、runtime fields 和 result schema |
| `scripts/real_flow/flows/audio_stem_separation.py` | `real-flow` 同时支持旧 job_type 和 Triton job_type |

## 配置如何连接模型服务

FastAPI worker 通过下面配置找到 Triton 模型服务：

```env
AUDIO_STEM_TRITON_URL=127.0.0.1:8000
AUDIO_STEM_TRITON_MODEL_VERSION=1
AUDIO_STEM_TRITON_REQUEST_TIMEOUT_SECONDS=300
AUDIO_STEM_TRITON_TOKEN=
```

`AUDIO_STEM_TRITON_URL` 按 `tritonclient` HTTP 约定填写，不带 `http://` 或 `https://`。本地外部 Triton 服务示例通常是：

```text
127.0.0.1:8000
```

云上 PAI-EAS Triton 服务则填写 EAS 给出的 HTTP endpoint。需要鉴权时，`AUDIO_STEM_TRITON_TOKEN` 会作为 `Authorization` header 传给 Triton client。token 不应写入 result、runtime fields 或日志。

如果 `AUDIO_STEM_TRITON_URL` 为空，`audio_stem_separation_triton` 首次执行会快速失败，不会回退到旧的本地 ONNX job。

## 本地验证和云上部署的边界

本地验证目标是模拟“模型服务单独部署后，FastAPI 通过 HTTP 调模型服务”：

```text
外部 Triton 模型服务
  独立启动
  暴露 Triton HTTP endpoint

FastAPI local 模式
  ./scripts/dev.sh start
  AUDIO_STEM_TRITON_URL=<Triton HTTP endpoint>
  real-flow 提交 audio_stem_separation_triton
```

本仓库侧只关心 `AUDIO_STEM_TRITON_URL` 能否被 worker 访问，以及远端是否符合固定 tensor I/O 合同。外部 Triton 服务如何拉镜像、挂模型目录、选择 CPU/GPU 配置和绑定端口，不属于本仓库 current 事实。

云上部署目标是让外部 Triton 模型服务加载正式 `models/`：

```text
audio-stem-separation-triton/models
        |
        | 上传
        v
OSS model repository 路径
        |
        | PAI-EAS Triton 部署挂载
        v
tritonserver --model-repository=/models
        |
        | EAS endpoint
        v
FastAPI worker 通过 AUDIO_STEM_TRITON_URL 调用
```

## 模型服务容量和横向扩展

`audio_stem_separation_triton` 的容量不只由 FastAPI worker 决定。完整链路里有三层并发控制：

```text
FastAPI Job 服务
  控制接单、排队、worker 并发、Job timeout
        |
        | 每个 Job 会产生多次 Triton infer
        v
Triton 单个副本
  控制单个模型服务实例内的模型加载、instance_group、单副本显存/内存占用
        |
        | 多个副本共同承接请求
        v
外部模型服务平台
  控制 Triton 服务副本数、资源规格和扩缩容策略
```

FastAPI 侧不要把模型服务扩容理解成“改一个 URL”。通常模型服务横向扩展后，FastAPI 仍然调用同一个 `AUDIO_STEM_TRITON_URL`；扩出来的多个 Triton 副本由外部模型服务平台或负载均衡层承接流量。

这条链路的推理请求数量需要按 segment 计算：

```text
1 个 audio_stem_separation_triton Job
  -> N 个 segment
  -> 每个 segment 调 4 个 Triton 模型
  -> 总 Triton infer 次数 = N * 4
```

所以容量估算不能只看“有多少 Job”。应同时看：

```text
Job 并发数
segment_count
每个 segment 的 4 次 infer
Triton 单副本 p95 / 显存 / 内存
模型服务副本数
OSS 下载和结果上传耗时
```

PAI-EAS 或其他模型服务平台的横向扩展属于模型服务部署层，不属于本 FastAPI 仓库的运行入口。本文只约定本服务如何调用一个可访问的 Triton HTTP endpoint；具体副本数、自动扩缩容指标、冷却时间、资源规格和最小/最大实例数，应在外部模型服务部署文档或平台配置中维护。

调优顺序建议保持分层：

```text
1. 先测单个 Triton 副本
   确认 4 个模型常驻是否稳定，拿到单副本 p95、吞吐、显存/内存。

2. 再调模型服务副本数
   横向扩展 Triton 副本，确认同一个 endpoint 后面的总体吞吐。

3. 最后调 FastAPI worker / Job 并发
   避免 FastAPI 放量超过模型服务容量，导致请求堆积或推理失败。
```

不要先盲目增加 FastAPI worker 并发。对当前实现来说，FastAPI worker 并发升高会直接放大 `N * 4` 的 Triton infer 压力；如果 Triton 副本数或单副本容量没有跟上，瓶颈会从业务队列转移到模型服务。

## real-flow 如何提交 Triton Job

`scripts/real-flow.sh audio-stem-separation` 支持通过 `--job-type` 指定旧本地 ONNX job 或新 Triton job。

构造 payload：

```bash
./scripts/real-flow.sh audio-stem-separation build-payload \
  --job-type audio_stem_separation_triton \
  --input-file .data/misc/2485_0003_S6_梁萧.wav \
  --output .run/audio-stem-triton-payload.json
```

提交真实 Job：

```bash
./scripts/real-flow.sh audio-stem-separation run \
  --confirm-run \
  --confirm-upload \
  --env-file .env \
  --job-type audio_stem_separation_triton \
  --input-file .data/misc/2485_0003_S6_梁萧.wav \
  --download-outputs \
  --json
```

当 `STORAGE_BACKEND=aliyun_oss` 且输入来自本地文件时，`real-flow` 会先把本地 WAV 上传到 OSS，再把 URL Ref 提交给 FastAPI。这个步骤会访问真实 OSS。

## 边界和常见坑

不要把旧 `audio_stem_separation` executor 整体搬进 Triton。Triton 模型服务只负责 tensor 推理，OSS、WAV 编解码、segment、overlap-add、Job 状态和 callback 都留在 FastAPI Job 服务。

不要把 GitHub 仓库和 Triton model repository 混为一谈：

```text
GitHub 仓库
  存 config、脚本、文档和可选模型文件管理规则

Triton model repository
  是 tritonserver 加载的 models/ 目录结构

OSS 路径
  是云上部署时保存 models/ 的对象存储位置
```

不要默认启用 `dynamic_batching`。当前 ONNX 签名是固定 `[1, 2, 343980]` 输入和 `[1, 4, 2, 343980]` 输出，FastAPI 当前也按固定 segment 逐次请求。

不要 silent fallback。`audio_stem_separation_triton` 调不到 Triton 或 Triton 推理失败时应暴露错误；如果需要本地 ONNX 路径，应显式使用旧 `audio_stem_separation` job_type。

## 最小心智模型

最后可以压缩成这张图：

```text
输入 WAV 文件
  当前集成不直接给 Triton
        |
        v
FastAPI worker
  下载/校验/解码/切片
  生成 float32 tensor [1, 2, 343980]
        |
        v
Triton 模型服务
  只做 ONNX 推理
  返回 float32 tensor [1, 4, 2, 343980]
        |
        v
FastAPI worker
  取 target row
  overlap-add
  写 4 个 WAV 到 OSS
        |
        v
Job 查询返回 URL Ref
```

如果这个图能说通，就说明边界已经对了：模型服务不是业务服务，模型仓库不是 API 服务，FastAPI 不是推理 runtime。
