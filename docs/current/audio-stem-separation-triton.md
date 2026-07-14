# audio_stem_separation_triton 当前模型

本文只记录本仓库中 `audio_stem_separation_triton` Job 的当前实现事实。真实流程执行、开发服务器 baseline 和压测操作见 [`../runbooks/audio/audio-stem-separation-triton-real-flow-benchmark.md`](../runbooks/audio/audio-stem-separation-triton-real-flow-benchmark.md)。

## 当前定位

`audio_stem_separation_triton` 是 `visibility="demo"`、`role="root"` 的真实模型示例 Job。它用于验证本服务如何通过 capability/tool 注册、OSS 输入、Triton HTTP endpoint、对象存储输出和 Job result 表达音频源分离链路。

它不是模板 smoke 的低成本示例，也不是外部 Triton 部署手册。生产级 Triton model repository、PAI-EAS 服务、GPU 容量和模型镜像由外部运行环境负责。

## 三层关系

```text
FastAPI Job 服务
  拥有 Job schema、输入校验、OSS 读写、结果结构、Callback、billing 外围骨架

Triton HTTP endpoint
  拥有模型推理服务、batch/concurrency、GPU/CPU 运行时和 tensor I/O

Model repository
  拥有 Triton 模型目录、config.pbtxt、模型权重和模型版本
```

本仓库只依赖一个符合当前 tensor I/O 的 Triton HTTP endpoint。它不在 current 文档中维护外部 model repository 的部署步骤。

## Runtime Path

```text
POST /jobs audio_stem_separation_triton
  -> schema validate
  -> media.audio_input:2 capability
  -> object_storage_read:1 fetch audio input from OSS
  -> audio_decode_normalize:1 canonicalize to 44.1kHz stereo
  -> segment canonical audio
  -> TritonAudioStemClient infer
  -> validate stems
  -> write WAV stems to OSS
  -> return AudioStemSeparationTritonResult
```

核心代码位置：

| 层 | 文件 |
|---|---|
| Job executor | `app/jobs/types/audio_stem_separation_triton/executor.py` |
| Job schema/result | `app/schemas/jobs.py` |
| Triton client | `app/integrations/triton_audio_stem.py` |
| Capability/tool 注册 | `app/capabilities/register.py`、`app/tools/register.py` |
| 模型资产声明 | `app/jobs/types/audio_stem_separation_triton/model_asset.yaml` |

## Job 合同边界

`audio_stem_separation_triton` 复用 Job API 的统一提交、查询和 Callback 合同。它的参数和结果 schema 由 `app/schemas/jobs.py` 定义；本文不重复维护完整字段表。

当前输入边界：

- 输入音频来自 OSS 引用。
- 当前支持 `audio/wav`、`audio/x-wav`、`audio/mpeg` 和 `audio/mp3` 输入；执行期统一规范化为当前模型需要的 44.1kHz stereo canonical audio。
- 输入大小、时长和来源白名单由 schema、配置和 capability 校验共同约束。

当前输出边界：

- 输出四条 stem：`vocals`、`drums`、`bass`、`other`。
- 输出写入对象存储，并在 Job result 中返回 artifact 引用和基础音频元信息。
- 输出 WAV 的公开可访问性由对象存储配置决定，不由 Triton endpoint 决定。

## Triton I/O

FastAPI 传给 Triton 的不是音频文件路径，而是分段后的 tensor。Triton 返回的也是 stem tensor，再由本服务转换为 WAV artifact。

当前固定边界：

| 项 | 当前事实 |
|---|---|
| request | Triton HTTP infer |
| input | 音频 segment tensor |
| output | stem tensor |
| stem count | 4 |
| segment orchestration | 本服务负责 |
| artifact write | 本服务负责 |

Triton 推理失败、输出形状不符合预期或 stem 无法写入对象存储时，Job 失败并暴露标准错误；不做 silent fallback。需要本地 ONNX 路径时，应显式使用旧 `audio_stem_separation` job_type。

## 配置边界

关键配置包括：

| 配置 | 当前用途 |
|---|---|
| `AUDIO_STEM_TRITON_URL` | Triton HTTP endpoint |
| `AUDIO_STEM_TRITON_MODEL_NAME` | Triton model name |
| `AUDIO_STEM_TRITON_MODEL_VERSION` | 可选 model version |
| `AUDIO_STEM_TRITON_TOKEN` | 可选鉴权 token，以 `Authorization` header 传给 Triton client |
| `AUDIO_STEM_ALLOWED_OSS_BUCKETS` / `AUDIO_STEM_ALLOWED_OSS_REGIONS` | 输入来源白名单 |
| `OSS_*` | 输出 artifact 写入对象存储 |

token 不应写入 result、runtime fields 或日志。Triton endpoint 是运行环境依赖，不能通过 silent fallback 改为本地模型路径。

## 当前边界

- `audio_stem_separation_triton` 是 demo root job_type；`APP_ENV=test/prd` 不允许外部提交 demo 类型。
- 本服务负责 Job 生命周期、输入输出、对象存储和错误投影；Triton 负责推理。
- 本服务不维护 Triton 镜像构建、GPU 调度、PAI-EAS 发布、模型仓库同步或生产容量承诺。
- 真实流程 baseline 只能作为当时环境的复现样本，不是稳定容量合同。

## 验证

- `tests/test_audio_stem_separation_triton.py`
- `tests/test_real_flow_cli.py`
- `./scripts/verify.sh check`
- 真实流程验证见 [`../runbooks/audio/audio-stem-separation-triton-real-flow-benchmark.md`](../runbooks/audio/audio-stem-separation-triton-real-flow-benchmark.md)
