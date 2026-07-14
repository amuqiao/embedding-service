# Job Capability Service 骨架计划

本文只记录 `Job Type -> Capability Service -> Integration Adapter` 的目标骨架和后续落地计划。当前已实现事实仍以 `docs/current/` 和代码为准；本文中的目录、表和能力名称都是未来工作目标，不能当作当前合同。

## 心智模型

本计划要解决的不是“给某个 job 增加一个工具函数”，而是把可复用处理能力放到稳定边界内，让 job 只声明业务需求和调用能力，不在每个 `job_type` 里重复实现下载、探测、转码、模型适配、文件处理或外部工具调用。

目标调用方向：

```text
Job Type
  定义外部 job_params / runtime_fields / result / 错误码
  把调用方 payload 转成内部 canonical contract
        |
        v
Capability Service
  承载可复用业务能力
  接收内部 Spec / Policy / Ref
  编排校验、处理、结果建模和能力级日志
        |
        v
Integration Adapter
  封装具体外部系统、SDK、命令行工具或运行时
  不写 Job 状态，不暴露调用方 payload 形状
```

三层边界：

| 层 | 应该负责 | 不应该负责 |
|---|---|---|
| `app/jobs/types/*` | `job_type` 合同、参数规范化、runtime snapshot、结果投影、业务错误码 | 直接拼外部 SDK / ffmpeg / Triton / provider 细节，跨 job import 私有函数 |
| `app/capabilities/*` | 可复用能力的 Spec、Policy、Service、Result、Error、处理流水线 | 解析 CPP/外部 payload 形状，直接暴露 provider 原始响应，直接写 provider billing |
| `app/integrations/*` | 技术适配器：OSS、Triton、ONNX Runtime、ffmpeg、provider SDK | 业务 Job 状态迁移、Job result 结构、Callback、外部调用方合同 |

`app/jobs/payload_adapters` 不升级为通用能力层。它继续只负责“调用方 payload 形状 -> 内部 canonical ref”的兼容，例如 OSS URL Ref、CPP URL Ref 或受控 HTTP 读取。

## Current Baseline

- `JobExecutor`、job registry 和 `app/services/jobs.py` 已经提供稳定 `job_type` 扩展骨架：executor 声明 params/runtime/result schema、`visibility`、`role`、错误码、timeout，并在创建 Job 时冻结 runtime fields。
- `app/services/ai_gateway_facade.py` 和 `app/services/ai_capability_kernel.py` 已经形成可借鉴的 facade/kernel 模式：业务 Job 走稳定 facade，具体 provider adapter 放在 `app/integrations/ai_adapters/`。
- `app/integrations/` 当前承载外部技术边界，包括对象存储、ONNX Runtime、Triton HTTP client、AI provider adapter 和图片处理工具；这些模块不应该直接修改 Job 状态。
- `app/jobs/payload_adapters/` 当前是调用方 payload 适配层，不是 integration adapter registry。它已经用于 OSS URL Ref、CPP URL Ref 和 HTTP URL bytes 读取。
- `audio_stem_separation` 当前在 executor 私有函数里完成 OSS URL Ref 校验、HTTP 下载、sha256 校验、`soundfile` 解码 WAV、44.1kHz 双声道校验和 tensor 构建。
- `audio_stem_separation_triton` 当前复用 `audio_stem_separation.executor` 的私有输入函数，再通过 `app/integrations/triton_audio_stem.py` 调 Triton。这个复用证明公共能力确实存在，但边界还不是稳定的 capability service。
- 模型目录当前分两类：AI provider 模型由 `app/core/models.yaml` 和 `model_registry.py` 管理；HTDemucs 模型资产由 `audio_stem_separation/model_asset.yaml` 和 `HTDEMUCS_MODEL_DIR` 管理。当前没有模型资产数据库表，也没有 capability 运行记录表。

## Remaining Gaps

- 音频输入兼容逻辑还在具体 job executor 内部，未来新增视频、图片、文档或压缩包处理时容易复制一套类似流程。
- `audio_stem_separation_triton` 跨包 import 旧 job 的私有函数，说明共享边界不稳定，后续维护容易让两个 job 行为意外漂移。
- `read_http_url_bytes()` 名称通用，但错误文案仍偏图片场景；说明当前 adapter 是按局部需求演进的，不适合继续承载更重的公共能力。
- URL Ref 的通用内部形状已有 `CanonicalObjectRef`，但不同 job 的 schema 仍各自声明 content type 限制，缺少公共能力层对 MIME、大小、hash、duration、采样率等策略的统一表达。
- 当前 registry/test 主要验证 `job_type`、schema、错误码、prompt 和 workflow；还没有验证 `job_type -> capability -> integration adapter` 映射。
- 是否需要模型数据表、capability 运行表、媒体处理结果表尚未形成阶段门槛。如果过早建表，会制造第二套事实源；如果永远不建表，未来独立重放、跨 job 缓存和运营查询会缺少基础。

## Planned Work

### Phase 0：定轻量骨架

新增公共能力层的目标目录：

```text
app/capabilities/
  <capability>/
    specs.py
    service.py
    errors.py
```

首批必需文件只保留三类：

- `specs.py`：能力输入、目标规格、策略快照和结果对象。
- `service.py`：job 可调用的稳定入口。
- `errors.py`：能力级稳定错误码和注册函数。

以下文件只在确实需要时新增：

- `policy.py`：当限制项之间存在派生、联动校验或环境配置映射时再引入。
- `pipeline.py`：当处理阶段有独立失败语义、独立观测价值或未来可能拆成 child Job 时再引入。
- `registry.py`：当至少 3 个 capability 被多个 job 或同步 API 复用时，再考虑显式 registry；第一阶段不做动态发现、插件 manifest 或数据库 catalog。

目录边界目标：

```text
app/jobs/types/<job_type>/executor.py
  -> app/capabilities/<capability>/service.py
  -> app/integrations/<technology_or_system>/*
```

### Phase 1：Media Preprocessor 作为首个能力

首个能力建议是 `media_preprocessor`，因为 `audio_stem_separation` 已经暴露真实痛点：模型需要 44.1kHz 双声道 WAV，但真实用户可能上传 MP3、M4A、FLAC、不同采样率 WAV，甚至未来可能上传视频。

目标能力入口示意：

```python
prepared = prepare_audio_for_job(
    source_ref=canonical_ref,
    spec=AudioSpec(sample_rate=44100, channels=2, container="wav"),
    policy=MediaPolicy(max_duration_seconds=600, max_source_bytes=...),
)
```

首版范围：

- capability service 只接收内部 `CanonicalObjectRef` 或已经受控的本地路径，不直接接收任意 `public_url` payload。
- job executor 继续负责把外部 `job_params` 转成 canonical ref，并把 capability 选择写入 runtime fields。
- media capability 负责探测、校验、转码、标准化，并输出本地 prepared media。
- `ffmpeg` / `ffprobe` 命令封装放入 `app/integrations/media/ffmpeg.py`，不放在 job 包内。
- `audio_stem_separation` 和 `audio_stem_separation_triton` 逐步改为共享同一套 prepared audio 输入，而不是共享某个 job 的私有函数。

首版 source resolution owner：

| 动作 | Owner |
|---|---|
| 外部 `job_params`、CPP/OSS URL Ref 形状校验 | `job_type` schema + `app/jobs/payload_adapters` |
| bucket / region / content-type allowlist | `job_type` 调用 `app/jobs/payload_adapters` 时传入策略 |
| `CanonicalObjectRef` 作为 capability 输入 | `job_type` executor |
| 禁止 redirect、下载超时、最大字节数 | capability service 调用受控 fetcher 统一执行 |
| sha256 校验 | capability service 在读取字节后按 `CanonicalObjectRef.content_hash` 执行 |
| MIME、duration、采样率、声道等媒体策略 | capability service 按 `MediaPolicy` / `AudioSpec` 执行 |
| `ffprobe` / `ffmpeg` 调用细节 | `app/integrations/media/ffmpeg.py` |

这条 contract 的核心是：`job_type` 负责把调用方输入变成可信内部引用，capability service 负责把可信引用解析成受限字节流和 prepared media；任何层都不能绕过 allowlist、字节上限、超时和 hash 校验。

首版中间产物只落本地 per-attempt 临时目录。prepared media 不写对象存储，也不进入 Job result。后续如果需要把中间产物写对象存储，必须先明确 `job_id/attempt_id` 前缀、TTL、成功/失败/重试后的清理时机，以及 recovery 清理规则；否则不能引入对象存储中间产物。

首版不直接支持“万能视频处理”。如果要接受视频，应先明确这是：

- 音频 job 的“从视频抽音频”输入兼容能力；还是
- 新的 video capability / video job。

两者不能混在同一个隐式 fallback 里。

### Phase 2：模型资产和模型目录边界

第一阶段不把 HTDemucs 模型资产强行搬进 `app/core/models.yaml`，也不新增模型数据表。

当前判断：

- `app/core/models.yaml` 主要服务对外 `/models` 公开投影和 AI provider 模型选择。
- `audio_stem_separation/model_asset.yaml` 描述本 job 内部固定 ONNX/Triton tensor 合同、sha256、segment 参数和 stem row 映射。
- 两者职责不同，不能因为都叫 model 就合并。

后续只有满足以下条件之一，才重新评估模型资产 registry 或模型数据表：

- 同一类模型资产被多个 `job_type` 或多个 capability 复用，且需要统一版本选择。
- 模型资产需要对调用方公开选择，并进入 `/models` 合同。
- 模型资产需要运行期热切换、灰度、禁用、审计或运营配置。
- 模型资产的下载、校验、部署状态需要被 API 查询或运维页面展示。

如果未来需要模型资产 registry，优先考虑配置文件 registry + 启动期校验；只有运行期可变、需要审计或需要跨实例动态变更时，才考虑数据库表。

### Phase 3：持久化表的触发门槛

第一阶段不新增 capability/model/media processing 数据表。原因是当前 Job、Attempt、runtime snapshot、最终 Job result、最终对象存储产物和 AI call ledger 已经覆盖主要事实源；过早建表会制造维护成本和事实源竞争。

不建表时的事实源：

| 事实 | 第一阶段来源 |
|---|---|
| 调用方请求 | `jobs.job_params_ref` / runtime snapshot |
| 选择的 job_type、runtime fields、output target | `runtime_ref` |
| attempt 生命周期 | `job_execution_attempts` |
| 最终结果产物 | Job result + 对象存储 ref |
| AI provider 调用成本 | `ai_call_ledger_entries` |

后续需要持久化 capability 表的触发条件：

- capability 需要独立状态查询、重放、人工恢复或取消。
- capability 处理时间足够长，需要独立 lease / retry / heartbeat。
- capability 输出需要跨 job 去重、缓存、复用或过期清理。
- 运维需要按 capability 维度统计吞吐、失败阶段、输入输出大小和成本。
- capability 成为同步 API 和多个 job 共同依赖的独立运营对象。

触发后也不应先建多张领域表。优先从一张泛化表开始：

```text
capability_runs
  id
  capability_key
  capability_version
  job_id
  attempt_id
  status
  stage
  input_hash
  spec_snapshot
  policy_snapshot
  adapter_key
  output_ref
  error_code
  started_at
  finished_at
```

只有当输出资产需要跨 job 复用和生命周期管理时，再考虑：

```text
capability_materializations
  id
  capability_run_id
  materialization_key
  object_ref
  content_hash
  content_type
  expires_at
```

如果中间 prepared artifact 需要写入对象存储，即使不跨 job 复用，也必须先选择一种所有权方案：

- 作为 Job 私有临时对象：绑定 `job_id/attempt_id` 前缀、TTL 和 recovery 清理规则，不进入公开 result。
- 作为可复用 materialization：写入 `capability_materializations`，并明确过期、复用和删除策略。

如果某个处理阶段需要独立可靠性，但不需要独立查询表，优先把它提升为 `visibility="internal"` 的 child Job，复用现有 Job / workflow / Attempt / recovery 机制，而不是新建 capability 专用状态机。

### Phase 4：扩展到其他公共能力

Media Preprocessor 稳定后，其他能力按同一骨架进入：

| 能力 | 可能入口 | 首要边界 |
|---|---|---|
| Image Preprocessor | `app/capabilities/image_preprocessor` | 尺寸、格式、alpha、背景、hash、EXIF 清理 |
| Document Parser | `app/capabilities/document_parser` | 文件类型探测、页数/大小限制、文本抽取、结构化结果 |
| Archive Extractor | `app/capabilities/archive_extractor` | 解压限制、路径穿越防护、文件数/大小上限 |
| Model Input Builder | `app/capabilities/model_input_builder` | 模型输入 tensor / prompt / media bundle 构建，不直接调用 provider |

每个新能力都必须先回答：

- 是否已有 job 内部逻辑重复出现？
- 是否会被至少两个 job 或一个 job + 一个同步 API 复用？
- 是否需要独立错误码、日志事件和测试？
- 是否需要独立持久化？如果需要，为什么 Job/Attempt/runtime snapshot 不够？

## Acceptance

- `app/jobs/payload_adapters` 的职责保持为 caller payload adapter，不承载媒体转码、模型输入构建或通用工具流水线。
- 新 capability 的 job-facing 入口只接收内部 canonical contract，不直接接收 CPP/HTTP payload 形状。
- 本计划覆盖的可复用媒体、模型输入和公共处理能力，不再让 `job_type` executor 直连外部 provider/Triton/ONNX/ffmpeg adapter，也不跨 import 其他 job_type 的私有函数；一次性、单 job 私有且无复用价值的局部逻辑不强制抽成 capability。
- 首个 `Media Preprocessor` 落地时，`audio_stem_separation` 和 `audio_stem_separation_triton` 共享同一套输入标准化能力。
- 能力选择、关键 spec/policy 和 adapter 版本能写入 runtime fields 或其他 Job 可查询快照，不影响已创建 Job。
- 第一阶段不新增数据库表；如后续新增 `capability_runs` 或 `capability_materializations`，必须有 Alembic migration、repo/query、状态迁移测试和恢复说明。
- 失败路径必须 fail-fast，返回稳定错误码；不做 silent fallback、隐式降级、截断成功或部分产物伪装成功。
- 能力级日志必须使用白名单事件，不记录密钥、完整 URL token、原始媒体内容、base64 或大 payload。
- 至少补齐正常路径、非法输入、外部依赖失败和配置缺失测试；涉及 child Job 时再补 workflow/recovery 验证。

## Non-goals

- 不把本仓库改成插件平台，不做 entrypoint 自动发现、插件 manifest 或数据库 capability catalog。
- 不把所有公共函数塞进 `utils`、`helpers` 或继续膨胀 `app/jobs/payload_adapters`。
- 不在第一阶段新增模型资产数据库表、媒体处理运行表或 capability catalog 表。
- 不把 AI provider billing、usage normalizer 或 pricing 逻辑复制进 capability；涉及 AI provider 时继续走 AI facade。
- 不把 Triton model repository、ffmpeg 二进制安装、模型下载和业务 Job 合同混成一个目录或一个配置面。
