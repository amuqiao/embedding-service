# audio_stem_separation_triton 真实流程压测与 baseline Runbook

本文用于复现 `audio_stem_separation_triton` 在远程 Triton 服务上的安全压测和观测流程，并保留一份 2026-07-13 开发服务器 baseline 样本用于下次对比。目标不是把开发服务器打到极限，而是在不影响其他服务的前提下，确认真实业务链路能否跑通、显存是否接近风险线、Triton 是否出现错误或积压，并为业务并发配置提供保守依据。

## 文档边界

本文负责：

- 说明真实 `audio_stem_separation_triton` 压测前应建立的心智模型。
- 给出安全执行一次真实 Job 的步骤。
- 给出压测时应观察的本地 Job、远程 Triton、GPU 和日志指标。
- 记录 2026-07-13 这轮开发服务器 baseline 样本，方便下次复现实验时对比。

本文不负责：

- Triton model repository 的部署、模型文件管理或远程生命周期脚本维护。
- `audio_stem_separation_triton` 的 API 字段合同和当前实现事实。
- 生产容量承诺或自动扩缩容策略。

当前实现事实以 [`../../current/audio-stem-separation-triton.md`](../../current/audio-stem-separation-triton.md) 为准。本文中的 baseline 只代表当时那台共享开发服务器、当时 Triton 配置和当时输入音频，不是稳定容量合同，也不是生产配置建议。

## 心智模型

`audio_stem_separation_triton` 不是把音频文件直接丢给 Triton。真实链路是：

```text
real-flow / 调用方
  提交 input_audio URL Ref
        |
        v
FastAPI Job API
  创建 audio_stem_separation_triton Job
        |
        v
Taskiq worker
  下载 WAV
  校验格式、sha256、URL Ref
  解码成 44100Hz stereo float32
  按 7.8s segment 切块
        |
        v
Triton HTTP infer
  每个 segment 顺序调用 4 个 expert model
        |
        v
Taskiq worker
  校验输出 tensor
  合并 segment
  编码 drums / bass / other / vocals
  上传 OSS
        |
        v
Job succeeded / failed
```

真实流程的推理次数按 segment 计算：

```text
1 个 audio_stem_separation_triton Job
  -> N 个 segment
  -> 每个 segment 调 4 个 Triton 模型
  -> 总 Triton infer 次数 = N * 4
```

每个 segment 当前按顺序调用：

```text
drums -> bass -> other -> vocals
```

因此要区分三类测试：

| 测试类型 | 入口 | 用途 | 不能说明什么 |
|---|---|---|---|
| Triton 单模型直压 | `scripts/triton-bench.sh` | 测单个 Triton endpoint / model 的 p50、p95、RPS、failure | 不能代表完整业务链路耗时 |
| 真实 Job 流程 | `scripts/real-flow.sh audio-stem-separation build-payload/run ... --job-type audio_stem_separation_triton` | 验证下载、切块、Triton 调用、合并、OSS 上传、Job 状态 | 不适合直接追求极限并发 |
| Job 层压测 | `scripts/load.sh` | 测 FastAPI 接单、队列、worker、callback 等业务层容量 | 如果 Triton 单副本未确认容量，可能直接放大模型服务压力 |

本 runbook 重点是第二类：**真实 Job 流程**。第一类直压只作为前置校准和参考。

## 安全原则

共享开发服务器压测只找安全边界，不找物理极限。

压测前先确认：

- 本次是否只提交 1 个真实 Job。
- 是否知道输入音频时长、采样率、声道和预期 segment 数。
- 是否能观察远程 `docker stats`、`nvidia-smi`、Triton metrics 和 Triton logs。
- 是否设置了明确停止线。
- 是否不会在已有 GPU 显存高位时继续加并发。

开发服务器建议停止线：

| 指标 | 停止条件 |
|---|---|
| Triton failure | 任一模型 `nv_inference_request_failure` 增加 |
| Triton pending | `nv_inference_pending_request_count` 持续大于 0 |
| GPU 显存 | 23GiB 卡上超过约 22GiB，或剩余显存低于 1GiB |
| GPU/CPU | 长时间满载且吞吐不再增加 |
| 日志 | 出现 `OOM`、`CUDA`、`backend error`、`timeout`、`Traceback` |
| Job | 长时间停在 `calling_model` 且 Triton 无新增成功计数 |
| 延迟 | p95 超过低并发基线 2 倍且 RPS 没有明显增长 |

这轮真实流程已经把两张 23GiB GPU 推到约 `22.4GiB / 22.5GiB`，所以下次在相同服务器上不要直接跑真实并发 2。

## 前置条件

本地 FastAPI 服务和 worker 应已运行，并且 `.env` 指向远程 Triton：

```bash
./scripts/run.sh status dev
```

检查 real-flow 上下文：

```bash
./scripts/real-flow.sh doctor \
  --env-file .env \
  --json
```

关键环境变量：

```text
AUDIO_STEM_TRITON_URL=<triton-http-host-port>
AUDIO_STEM_TRITON_MODEL_VERSION=1
AUDIO_STEM_TRITON_REQUEST_TIMEOUT_SECONDS=300
STORAGE_BACKEND=aliyun_oss
```

`AUDIO_STEM_TRITON_URL` 按 `tritonclient` HTTP 约定填写，不带 `http://` 或 `https://`。

`real-flow.sh` 默认面向本地 API。只有明确验证远端测试 API 时才使用 `--allow-remote-api` 和 `--api-url`，不要把它作为默认压测路径。

## 留证命名

每次压测前先确定一个唯一 `RUN_ID`，后续 payload、JSON 输出、Job 查询结果和人工记录都围绕它命名，避免事后无法把 API Job、Triton metrics 和 GPU 采样对齐。

```bash
RUN_ID=real-audio-triton-$(date +%Y%m%d-%H%M%S)
RUN_DIR=.run/audio-stem-triton/$RUN_ID
mkdir -p "$RUN_DIR"
```

建议留证路径：

```text
.run/audio-stem-triton/<RUN_ID>/payload.json
.run/audio-stem-triton/<RUN_ID>/real-flow-result.json
.run/audio-stem-triton/<RUN_ID>/job.json
.run/audio-stem-triton/<RUN_ID>/triton-metrics-before.txt
.run/audio-stem-triton/<RUN_ID>/triton-metrics-after.txt
.run/audio-stem-triton/<RUN_ID>/gpu-before.txt
.run/audio-stem-triton/<RUN_ID>/gpu-after.txt
```

如果只是手工观察，也至少记录：

```text
RUN_ID
client_request_id
job_id
开始时间
结束时间
输入音频 path / duration / segment_count
压测前 GPU 显存
压测中 GPU 显存峰值
压测后 GPU 显存
Triton success/failure/pending 前后计数
```

这里的 `RUN_ID` 会作为 `client_request_id` 使用，不是 `metadata.run_id`。`jobs.sh --run-id` 只适用于 load 测试这类写入 `metadata.run_id` 的场景，本 runbook 不使用它。

## 观测命令

以下命令均为只读。把 `<USER@HOST>`、容器名和 metrics 端口替换为当前环境。

### 远程容器和 GPU

```bash
ssh <USER@HOST> \
  'date; docker stats --no-stream audio-stem-triton; nvidia-smi --query-gpu=index,memory.used,memory.total,utilization.gpu --format=csv,noheader,nounits'
```

输出示例：

```text
0, 22415, 23028, 0
1, 22509, 23028, 0
```

含义是：

```text
gpu_index, memory_used_mib, memory_total_mib, gpu_util_percent
```

### Triton metrics

如果 metrics 端口在远程宿主机映射为 `18002`：

```bash
ssh <USER@HOST> \
  'curl -fsS http://127.0.0.1:18002/metrics | grep -E "nv_inference_request_success|nv_inference_request_failure|nv_inference_pending_request_count|nv_inference_request_duration_us|nv_inference_compute_infer_duration_us|nv_gpu_memory_used_bytes"'
```

重点看：

```text
nv_inference_request_success
nv_inference_request_failure
nv_inference_pending_request_count
nv_inference_request_duration_us
nv_inference_compute_infer_duration_us
nv_gpu_memory_used_bytes
```

`*_duration_us` 是累计 counter。不要只看一次采样的绝对值，要用压测前后差值判断本次新增耗时。

### Triton 日志

```bash
ssh <USER@HOST> \
  'docker logs --since 15m audio-stem-triton | grep -Ei "oom|cuda|backend|error|fail|exception|traceback|timeout" || true'
```

如果没有输出，表示这次窗口内没有匹配到这些高风险日志关键词。

### 本地 Job 状态

```bash
./scripts/jobs.sh list \
  --status queued,running \
  --job-type audio_stem_separation_triton \
  --caller-id real-flow-cli \
  --client-request-id "$RUN_ID" \
  --scope family \
  --limit 10 \
  --json
```

```bash
./scripts/jobs.sh job <JOB_ID> --json
```

本地 worker 日志：

```bash
tail -n 80 logs/worker.log
```

Job 结束后补充单 Job 证据：

```bash
./scripts/jobs.sh inspect <JOB_ID> --json
./scripts/jobs.sh trace <JOB_ID> --json
./scripts/jobs.sh timeline <JOB_ID> --json
./scripts/jobs.sh payload <JOB_ID> --json
./scripts/jobs.sh diagnose <JOB_ID> --json
```

压测窗口结束后看系统是否排空：

```bash
./scripts/jobs.sh drain --since 30m --json
./scripts/jobs.sh pressure --since 30m --json
./scripts/jobs.sh runtime --json
```

`drain` 和 `pressure` 是窗口级视图，不按本次 `client_request_id` 精确隔离。共享开发机上应先用单 Job 命令确认本次 `JOB_ID`，再把窗口级结果当作背景证据。

## 执行真实流程

先确认测试音频。推荐先用短音频，不要直接用长音频或多个并发 Job。

本轮使用的样本：

```text
文件: .data/misc/2485_0003_S6_梁萧.wav
格式: WAV
编码: pcm_s16le
采样率: 44100 Hz
声道: 2
时长: 11.899977s
文件大小: 约 2.0 MiB
真实流程 segment_count: 2
```

换音频时按下面公式估算预期 segment 数：

```text
sample_rate = 44100
segment_samples = 343980
overlap_ratio = 0.25
overlap = int(segment_samples * overlap_ratio) = 85995
stride = segment_samples - overlap = 257985
total_len = ceil(duration_seconds * sample_rate)

segment_count = 1 + ceil(max(0, total_len - segment_samples) / stride)
```

等价理解：

```text
每个 segment 覆盖 7.8s 音频
相邻 segment 重叠 25%
后续 segment 的步长约 5.85s
```

实际 `segment_count` 以 Job result 为准。估算值用于压测前判断预期 Triton infer 次数和显存风险。

先构建 payload 做预检。这个步骤会根据输入文件生成 Job payload；如果本地文件需要 stage/upload，则同样要确认上传副作用。

```bash
./scripts/real-flow.sh audio-stem-separation build-payload \
  --env-file .env \
  --confirm-upload \
  --job-type audio_stem_separation_triton \
  --input-file .data/misc/2485_0003_S6_梁萧.wav \
  --max-duration-seconds 12.5 \
  --client-request-id "$RUN_ID" \
  --output "$RUN_DIR/payload.json"
```

提交真实 `audio_stem_separation_triton` Job：

```bash
./scripts/real-flow.sh audio-stem-separation run \
  --confirm-run \
  --env-file .env \
  --job-type audio_stem_separation_triton \
  --payload-file "$RUN_DIR/payload.json" \
  --timeout-seconds 900 \
  --poll-interval-seconds 5 \
  --client-request-id "$RUN_ID" \
  --json > "$RUN_DIR/real-flow-result.json"
```

注意事项：

- `build-payload --confirm-upload` 会把本地测试音频上传到真实 OSS，并把生成的 URL Ref 写入 payload。
- `run --payload-file` 会复用已构建 payload，便于保留本次入参证据。
- `--timeout-seconds 900` 只是本地等待 Job 终态的超时，不是取消远端 Job 的开关。
- 如果本地等待中断，Job 可能仍在 worker 中运行，需要用 `jobs.sh` 查状态。
- 不要在第一轮真实流程还没结束时提交第二个 Job。

## 采样时间线

一次安全压测至少采 5 个时间点：

| 时间点 | 记录内容 | 判断目的 |
|---|---|---|
| 压测前 | GPU 显存、Triton success/failure/pending、容器 CPU/mem | 建立基线 |
| Job 创建后 | `job_id`、`queued/running`、worker 是否接单 | 确认业务链路启动 |
| `calling_model` 中 | 每个 model success 是否按 `drums -> bass -> other -> vocals` 增长 | 判断是否卡在某个 expert |
| Job 终态后 | `succeeded/failed`、duration、stems、Triton failure/pending | 判断是否跑通 |
| 冷却后 | GPU 显存是否回落、pending 是否为 0、Job 是否 drain | 判断是否可以继续或必须停止 |

## 预期计数

如果输入产生 `segment_count=2`，那么 Triton metrics 中每个 expert model 的成功计数应各增加 2：

```text
drums   +2
bass    +2
other   +2
vocals  +2
```

通用公式：

```text
每个模型 success 增量 = segment_count
四个模型 success 增量合计 = segment_count * 4
```

如果某个模型没有增长，或者增长停在中间，按当前顺序判断卡在哪个 expert：

```text
drums -> bass -> other -> vocals
```

例如 `drums` 增长但 `bass` 不增长，通常表示流程在 drums 之后、bass 之前或 bass 调用阶段停住。

## 附录：2026-07-13 baseline 样本

执行时间：`2026-07-13`  
远程服务：共享开发服务器上的 `audio-stem-triton` 容器  
测试方式：单个真实 `audio_stem_separation_triton` Job，不继续加并发

### Job 结果

```text
job_id: 6c149ff6-c071-4e4f-b68e-75b4e9c4fd8b
status: succeeded
job_type: audio_stem_separation_triton
source_duration_seconds: 11.89997732426304
segment_count: 2
sample_rate: 44100
channels: 2
model_service: triton
triton_model_version: 1
```

输出 stem：

```text
drums.wav
bass.wav
other.wav
vocals.wav
```

业务结果耗时：

```text
io: 10650 ms
inference: 409773 ms
total: 420430 ms
```

这里的 `inference` 是业务结果字段，不等同于 Triton 单次模型 infer counter。它包含 worker 内围绕推理的更多处理和等待成本。

### Triton 计数变化

```text
drums   13 -> 15  +2
bass     2 ->  4  +2
other    2 ->  4  +2
vocals   2 ->  4  +2
failure 全部 0
pending 全部 0
```

结论：这次真实流程符合 `2 segments * 4 models = 8` 次 Triton infer 的预期，没有 Triton failure，也没有 pending 积压。

### GPU 显存变化

真实流程前，Triton 已 warm：

```text
GPU0 18319 / 23028 MiB
GPU1 18821 / 23028 MiB
```

真实流程后或峰值附近：

```text
GPU0 22415 / 23028 MiB
GPU1 22509 / 23028 MiB
```

本次单 Job 额外推高显存约：

```text
GPU0 +4096 MiB
GPU1 +3688 MiB
```

这说明单个约 11.9s 音频、2 个 segment 的真实 Job 已经把两张 23GiB GPU 推到接近满卡。不能据此说真实并发 2 安全。

### 直压参考结果

此前用 `scripts/triton-bench.sh` 直压 `drums` 单模型得到：

```text
concurrency=1: 4/4 成功，RPS 0.141，p95 7.73s
concurrency=2: 4/4 成功，RPS 0.162，p95 13.23s
```

判断：

- Triton 至少能接受并处理 `drums` 单模型 `concurrency=2`。
- 并发 2 的吞吐收益很小，p95 明显变差。
- 这不能直接推出真实 Job 并发 2 安全，因为真实 Job 还包含 segment、4 个模型、合并和 OSS。

## 显存和并发判断

不要把本次结果理解为“11.9 秒音频固定消耗 4GB 显存”。更准确的模型是：

```text
总显存 = Triton / CUDA / 模型常驻显存
       + 当前活跃请求的中间张量 / workspace / input-output buffer
       + 框架缓存
```

并发时，显存大概率会部分累加，但不一定严格线性累加：

- 模型权重通常不按请求重复加载。
- 每个并发请求会增加 input/output buffer、中间 tensor、workspace 等运行时显存。
- 多个 Job 并发时，不同 Job 的 Triton infer 可能同时占用 GPU。
- CUDA、ONNX Runtime、Triton allocator 可能缓存显存，Job 结束后不一定立刻回落。

本次观测到的常驻显存变化：

```text
早期 / 冷启动附近:
GPU0 14223 MiB
GPU1 14725 MiB

直压 warm 后:
GPU0 18319 MiB
GPU1 18821 MiB

真实流程后:
GPU0 22415 MiB
GPU1 22509 MiB
```

在这台开发服务器上，真实流程结束后每张卡只剩约 `500-600 MiB` 余量。这个余量不足以安全验证真实并发 2。

## 如何写压测报告

每次压测后按这个结构记录，避免只留下零散结论：

```text
时间:
执行人:
目标 Triton endpoint:
目标容器:
输入音频:
  path:
  format:
  sample_rate:
  channels:
  duration_seconds:
  expected_segment_count:

执行命令:
  real-flow command:

Job 结果:
  job_id:
  status:
  segment_count:
  duration_ms.io:
  duration_ms.inference:
  duration_ms.total:
  stems_count:

Triton metrics:
  success before/after:
  failure before/after:
  pending peak:
  request_duration_us delta:
  compute_infer_duration_us delta:

GPU:
  before:
  peak:
  after:

日志:
  suspicious logs:

结论:
  是否跑通:
  本开发服务器是否允许继续加压:
  本环境临时建议并发:
  下一步:
```

## 附录：baseline 样本结论

这轮只作为后续复现时的对比基线：

```text
真实 audio_stem_separation_triton 单 Job 可跑通。
2 个 segment 会触发 8 次 Triton infer。
Triton failure = 0，pending = 0。
远程 Triton 确实用到了双 GPU。
单个 11.9s WAV 已把两张 23GiB GPU 推到约 22.4GiB / 22.5GiB。
不建议在共享开发服务器继续真实流程加并发。
```

业务上线前的保守建议：

- 先按 Triton in-flight `1` 设计业务基线。
- 不要直接把 FastAPI worker 并发调高来“试容量”。
- 如果要测更高并发，先准备独占窗口或独立压测环境。
- 继续优化前，先拆分 `audio_stem_separation_triton` executor 的耗时：下载、解码、segment 准备、4 次 Triton 调用、后处理、merge、上传、状态写回。
