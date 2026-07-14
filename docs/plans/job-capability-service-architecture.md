# Job Capability Service 骨架计划

本文只记录 `Job Type -> Job Flow Step -> Capability Service -> Integration Adapter` 的目标骨架和后续落地计划。当前已实现事实仍以 `docs/current/` 和代码为准；本文中的目录、类型、表和能力名称都是未来工作目标，不能当作当前实现合同。

## 心智模型

Capability Service 不是独立于 Job 的任务系统，也不决定是否派发 child Job。它是 **Job Flow Step 的合同层**：把某个 Job 步骤对工具、媒体、模型输入或外部系统的使用方式类型化、策略化、可测试化。

```text
POST /jobs
  -> Job kernel 创建 Job
  -> job_type 冻结 runtime_fields
  -> job_type 定义 Job Flow
       |
       | Step 可以串行、并行、扇形或编排 child Job
       v
     Job Flow Step
       |
       | 调用能力合同
       v
     Capability Service
       |
       | 调用底层工具或外部系统
       v
     Integration Adapter
```

三件事必须分开：

| 问题 | Owner |
|---|---|
| 一个请求是不是 Job | Job API / Job kernel |
| 一个 `job_type` 的步骤、顺序、并行、child Job 编排 | `JobExecutor` / workflow definition |
| 某个步骤的输入、输出、策略、错误和工具调用合同 | Capability Service |

因此，Capability Service 始终属于 Job Flow。它可以被当前 worker 直接执行，也可以被 `job_type` 编排进 internal child Job；这个选择属于 `job_type` / workflow，不属于 capability 本身。

Capability Service 不拥有：

- queue / dispatch
- lease / heartbeat
- retry / recovery
- Callback
- Job / Attempt / outbox 状态迁移

一旦某个能力步骤需要独立调度、独立重试、独立可见状态或独立恢复入口，应由 `job_type` / workflow 把该步骤提升为 `visibility="internal"` 的 child Job / workflow node，而不是把调度语义塞进 capability。

## 边界

目标调用方向：

```text
Job Type
  定义外部 job_params / runtime_fields / result / 错误码
  定义 Job Flow 和 orchestration decision
  把调用方 payload 转成内部 source contract
        |
        v
Capability Service
  承载 Job Flow Step 的稳定合同
  接收类型化 Source / Spec / Policy / Context
  编排校验、处理、结果建模和能力级日志
        |
        v
Integration Adapter
  封装具体外部系统、SDK、命令行工具或运行时
  不写 Job 状态，不暴露调用方 payload 形状
```

目录边界：

| 层 | 应该负责 | 不应该负责 |
|---|---|---|
| `app/jobs/types/*` | `job_type` 合同、Job Flow、runtime snapshot、结果投影、业务错误码 | 复制通用媒体/文件/模型输入处理流程，跨 job import 私有函数 |
| `app/jobs/payload_adapters/*` | 调用方 payload 形状兼容，解析出未冻结的 source candidate / ref candidate | 构造完整 capability contract、冻结 runtime snapshot、媒体转码、模型输入构建、通用工具流水线 |
| `app/capabilities/*` | Job Flow Step 的 Spec、Policy、Service、Result、Error、处理流水线 | 解析 CPP/外部 payload 形状，反向依赖 Job kernel，直接写 Job/Attempt/outbox，直接暴露 provider 原始响应，直接写 provider billing |
| `app/integrations/*` | 技术适配器：OSS、Triton、ONNX Runtime、ffmpeg、provider SDK | 业务 Job 状态迁移、Job result 结构、Callback、外部调用方合同 |

`app/jobs/payload_adapters` 不升级为通用能力层。它继续只负责“调用方 payload 形状 -> 未冻结 source/ref candidate”的兼容，例如 OSS URL Ref、CPP URL Ref、public endpoint URL Ref 或受控 HTTP 读取提示。完整的 `SourceContract`、`ResolvedSource` 和 `FetchSpec` 只由 `job_type` / job executor 构造并冻结。

## Current Baseline

- `JobExecutor`、job registry 和 `app/services/jobs.py` 已经提供稳定 `job_type` 扩展骨架：executor 声明 params/runtime/result schema、`visibility`、`role`、错误码、timeout，并在创建 Job 时冻结 runtime fields。
- `app/services/ai_gateway_facade.py` 和 `app/services/ai_capability_kernel.py` 已经形成可借鉴的 facade/kernel 模式：业务 Job 走稳定 facade，具体 provider adapter 放在 `app/integrations/ai_adapters/`。
- `app/integrations/` 当前承载外部技术边界，包括对象存储、ONNX Runtime、Triton HTTP client、AI provider adapter 和图片处理工具；这些模块不应该直接修改 Job 状态。
- `app/jobs/payload_adapters/` 当前是调用方 payload 适配层，不是 integration adapter registry。它已经用于 OSS URL Ref、CPP URL Ref、public endpoint URL Ref 和 HTTP URL bytes 读取。
- `CanonicalObjectRef` 当前更像对象身份，而不是完整读取合同。现有 audio / poster 路径是在 canonical 校验后继续使用原始 `public_url` 读取 bytes，再校验 hash。
- `audio_stem_separation` 当前在 executor 私有函数里完成 OSS URL Ref 校验、HTTP 下载、sha256 校验、`soundfile` 解码 WAV、44.1kHz 双声道校验和 tensor 构建。
- `audio_stem_separation_triton` 当前复用 `audio_stem_separation.executor` 的私有输入函数，再通过 `app/integrations/triton_audio_stem.py` 调 Triton。这个复用证明公共能力确实存在，但边界还不是稳定的 capability service。
- 模型目录当前分两类：AI provider 模型由 `app/core/models.yaml` 和 `model_registry.py` 管理；HTDemucs 模型资产由 `audio_stem_separation/model_asset.yaml` 和 `HTDEMUCS_MODEL_DIR` 管理。当前没有模型资产数据库表，也没有 capability 运行记录表。

## Remaining Gaps

- 音频输入兼容逻辑还在具体 job executor 内部，未来新增视频、图片、文档或压缩包处理时容易复制一套类似流程。
- `audio_stem_separation_triton` 跨包 import 旧 job 的私有函数，说明共享边界不稳定，后续维护容易让两个 job 行为意外漂移。
- `CanonicalObjectRef` 不能直接作为 capability 的完整读取合同；还缺少 `ResolvedSource` / `FetchSpec` 这类可冻结、可校验的 source contract。
- 当前两个音频 job 的外部 schema 仍是 `content_type: audio/wav`。Media Preprocessor 可以先内部支持转码，但不能在不改外部合同的情况下悄悄接受 MP3/M4A/FLAC。
- `read_http_url_bytes()` 名称通用，但错误文案仍偏图片场景；说明当前 payload adapter 是按局部需求演进的，不适合继续承载更重的公共能力。
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

- `specs.py`：能力输入、目标规格、策略快照、source contract、运行期结果对象。
- `service.py`：Job Flow Step 可调用的稳定入口。
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

### Phase 1：Source Contract

Capability Service 不直接消费调用方 payload，也不应只消费裸 `CanonicalObjectRef`。首版应补三层 source 类型，不能折叠成一个万能 source 对象：

```text
payload adapter
  -> source candidate
job executor
  -> SourceContract / ResolvedSource / FetchSpec
  -> Capability Service
```

建议模型：

```python
class SourceContract(StrictBaseModel):
    schema_version: Literal["1"] = "1"
    source_kind: Literal["oss_url_ref"]
    accepted_content_types: tuple[str, ...]
    allowed_buckets: tuple[str, ...]
    allowed_regions: tuple[str, ...]


class CanonicalObjectRefSnapshot(StrictBaseModel):
    provider: Literal["aliyun_oss"]
    bucket: str
    region: str
    key: str
    content_type: str
    content_hash: str


class ResolvedSource(StrictBaseModel):
    schema_version: Literal["1"] = "1"
    contract: SourceContract
    ref: CanonicalObjectRefSnapshot
    observed_content_type: str | None = None
    observed_size_bytes: int | None = Field(default=None, gt=0)


class FetchSpec(StrictBaseModel):
    read_mode: Literal["object_storage", "oss_public_endpoint"]
    endpoint_key: str | None = None
    max_bytes: int = Field(gt=0)
    timeout_seconds: int = Field(gt=0)
    allow_redirects: Literal[False] = False
```

三层语义：

| 类型 | 表达什么 | 不表达什么 |
|---|---|---|
| `SourceContract` | job-facing 内部输入合同：来源类型、允许的引用形状、业务 allowlist 和 content type 约束 | 下载 URL、临时路径、provider 参数、调度状态 |
| `ResolvedSource` | 解析后的稳定内部事实：对象身份、hash、content-type、size、来源归属 | 外部 payload 歧义、临时 URL token、Job 状态 |
| `FetchSpec` | 受控读取计划：读取模式、endpoint key、timeout、max bytes、redirect 策略、hash 校验边界 | 完整 URL token、业务 result、临时文件路径、是否 child Job |

权威身份规则：

- `(provider, bucket, region, key, content_hash)` 是对象身份和审计身份。
- `public_url` / `internal_url` 只是读取入口或调用方兼容字段，不能单独作为权威身份。
- 如果使用 public endpoint URL Ref，payload adapter 必须把它投影回 `(bucket, region, key)`，再进入 capability。
- `ResolvedSource` 必须在创建 Job 时冻结到 runtime fields 或等价 runtime snapshot；执行期不能重新按当前配置推导出不同 source policy。
- snapshot 中不得冻结敏感明文、完整 URL token 或易失外部状态。需要保留来源事实时，优先冻结稳定 ref、hash、policy snapshot、endpoint key 和安全指纹；运行期认证、签名 URL、临时 token 由 integration adapter 在执行时注入，且不能放宽 `FetchSpec`。
- `endpoint_key` 只能标识稳定读取通道。如果 endpoint key 对应的 host、bucket 边界、认证方式或信任边界发生语义变化，必须引入新 key/version，或只影响新创建 Job；不能让旧 Job 的 plan snapshot 读到不同语义的端点。

首版 source resolution owner：

| 动作 | Owner |
|---|---|
| 外部 `job_params`、CPP/OSS URL Ref 形状校验 | `job_type` schema + `app/jobs/payload_adapters` |
| bucket / region / content-type allowlist | `job_type` 调用 payload adapter 时传入策略 |
| source candidate / ref candidate 解析 | `app/jobs/payload_adapters` |
| `SourceContract` 构造与冻结 | job executor |
| `CanonicalObjectRefSnapshot` 构造与冻结 | job executor |
| `ResolvedSource` 构造与冻结 | job executor |
| `FetchSpec` 构造与冻结 | job executor |
| 禁止 redirect、下载超时、最大字节数 | capability service 按 `FetchSpec` 执行 |
| sha256 校验 | capability service 按 `ResolvedSource.ref.content_hash` 执行 |
| MIME、duration、采样率、声道等媒体策略 | capability service 按 `MediaPolicy` / `AudioSpec` 执行 |
| `ffprobe` / `ffmpeg` 调用细节 | `app/integrations/media/ffmpeg.py` |

allowlist 规则值归 `job_type` 所有。payload adapter 只能按传入规则做解析和预校验；job executor 只能把规则值固化进 `SourceContract`，不能在冻结时发明新的 allowlist 规则。

source 事实优先级：

- `SourceContract.accepted_content_types` 是业务允许集合，不是对象事实。
- `CanonicalObjectRefSnapshot.content_type` 是来源元数据声明，可用于早期拒绝明显不符合合同的请求。
- `ResolvedSource.observed_content_type` 是解析或读取前探测到的来源事实，不能覆盖 `SourceContract`。
- capability probe 结果是执行期真实媒体事实。任一声明、观测或 probe 结果与 `SourceContract` / `MediaPolicy` 不一致时，必须以稳定 capability 错误拒绝，不能 silent fallback。
- hash 校验以 `ResolvedSource.ref.content_hash` 为准；读取内容与该 hash 不一致时，直接返回 `MEDIA_HASH_MISMATCH`。

### Phase 2：类型模型和 Snapshot

类型约束不能只靠 Python 注解。项目当前没有 mypy / pyright 质量门，跨层合同应优先使用 Pydantic `StrictBaseModel`；进程内临时对象可以使用 `dataclass(frozen=True)`。

类型分层规则：

| 类型类别 | 推荐实现 | 可持久化 | 用途 |
|---|---|---|---|
| 调用方 payload schema | `StrictBaseModel` | 是 | 外部 `job_params` 字段 |
| job canonical contract | `StrictBaseModel` | 是 | job executor 从 source candidate 构造的内部合同 |
| capability plan snapshot | `StrictBaseModel` | 是 | 创建 Job 时冻结的 source / spec / policy / adapter plan |
| capability execution metadata | `StrictBaseModel` | 按需 | attempt 级执行事实，不回写 plan snapshot |
| integration request / response | `StrictBaseModel` 或 frozen dataclass | 否 | adapter 调用边界 |
| public result projection | `StrictBaseModel` | 是 | 对外 Job result / Callback |
| 运行期临时对象 | `dataclass(frozen=True)` | 否 | 本地路径、打开的资源、内存 ndarray 等 |

Media Preprocessor 建议模型：

```python
class AudioSpec(StrictBaseModel):
    container: Literal["wav"]
    codec: Literal["pcm_s16le"]
    sample_rate: Literal[44100]
    channels: Literal[2]


class MediaPolicy(StrictBaseModel):
    max_source_bytes: int = Field(gt=0)
    max_duration_seconds: float = Field(gt=0, le=3600)
    download_timeout_seconds: int = Field(gt=0)
    transcode_timeout_seconds: int = Field(gt=0)
    allow_video_input: bool = False


class MediaPreparationPlanSnapshot(StrictBaseModel):
    schema_version: Literal["1"] = "1"
    capability_key: Literal["media_preprocessor"]
    capability_version: str
    source: ResolvedSource
    fetch: FetchSpec
    audio_spec: AudioSpec
    media_policy: MediaPolicy
    adapter_key: Literal["ffmpeg"]
    adapter_version: str
```

必须冻结到 capability plan snapshot 的内容：

- `capability_key`
- `capability_version`
- `source`
- `FetchSpec`
- `AudioSpec` / `VideoSpec`
- `MediaPolicy`
- `adapter_key`
- `adapter_version`

执行期只能读取已冻结 plan snapshot，不得按最新配置重新推导策略。这样才能避免已创建 Job 因配置变化产生行为漂移。

不可变计划和执行事实必须分开：

| 数据面 | 何时产生 | Owner | 是否可变 | 典型内容 |
|---|---|---|---|---|
| `capability_plan_snapshot` | 创建 Job / 冻结 runtime fields 时 | job executor | 不可变 | source、fetch、spec、policy、adapter key/version |
| `capability_execution_metadata` | 每次 attempt 执行时 | capability service | attempt 级追加 | prepared media sha256、size、duration、probe 结果、stage timing |
| public result projection | Job 成功或失败投影时 | job executor | 按 Job result 合同写入 | 对调用方可见的稳定 result / error |

第一阶段不新增 execution metadata 持久化字段。prepared media metadata 作为进程内返回对象传给当前 Job Flow 后续步骤，并通过结构化日志记录；它不写入 `capability_plan_snapshot`、Job result 或 Callback。只有出现独立查询、重放、运营统计或跨 attempt 排障需求时，才通过 migration 增加 attempt-scoped 内部字段或 `capability_runs`。

旧 Job、旧 plan snapshot、旧查询响应和旧 Callback 语义必须保持可读、可排障。配置、YAML、adapter 或模型资产变化只影响新创建的 Job，不能回写旧 Job 的 capability plan snapshot。

跨层泄漏限制：

- provider raw error、adapter request 字段、临时文件路径、内部 child id、workflow node key 和 Job kernel 字段不能进入 public schema 或业务 result。
- capability execution metadata 可以进入 attempt 级内部记录或内部日志，但不能回写 `capability_plan_snapshot`；进入 public result 前必须由 job result schema 显式投影。

### Phase 3：Media Preprocessor 作为首个能力

首个能力建议是 `media_preprocessor`，因为 `audio_stem_separation` 已经暴露真实痛点：模型需要 44.1kHz 双声道 WAV，但真实用户可能上传 MP3、M4A、FLAC、不同采样率 WAV，甚至未来可能上传视频。

首版能力入口示意：

```python
prepared = prepare_audio_for_job(
    snapshot=media_preparation_plan_snapshot,
    work_dir=attempt_work_dir,
)
```

首版范围：

- capability service 只接收 `MediaPreparationPlanSnapshot` 和 per-attempt `work_dir`，不直接接收任意 `public_url` payload。
- job executor 继续负责把外部 `job_params` 转成 `ResolvedSource`，并把 `capability_plan_snapshot` 写入 runtime fields。
- media capability 负责下载受限字节流、hash 校验、探测、校验、转码、标准化，并输出本地 prepared media。
- `ffmpeg` / `ffprobe` 命令封装放入 `app/integrations/media/ffmpeg.py`，不放在 job 包内。
- `audio_stem_separation` 和 `audio_stem_separation_triton` 逐步改为共享同一套 prepared audio 输入，而不是共享某个 job 的私有函数。

首版 prepared media 只落本地 per-attempt 临时目录：

- prepared media 不写对象存储。
- prepared media 不进入公开 Job result。
- `local_path` 必须位于 per-attempt `work_dir` 内。
- 输出文件必须存在、非空、不可通过 symlink 跳出 `work_dir`。
- 输出 metadata 必须包含 sha256、size、duration、sample_rate、channels、content_type。

后续如果需要把中间产物写对象存储，必须先明确 `job_id/attempt_id` 前缀、TTL、成功/失败/重试后的清理时机，以及 recovery 清理规则；否则不能引入对象存储中间产物。

### Phase 4：外部合同迁移

内部能力支持多格式，不等于外部 job contract 已经支持多格式。当前 `audio_stem_separation` 和 `audio_stem_separation_triton` 仍是 WAV-only 合同；首版不能在原 schema 下悄悄接受 MP3/M4A/FLAC。

变更分类：

| 变更 | 性质 | 要求 |
|---|---|---|
| 把现有 WAV 读取逻辑迁到 capability | 仅内部重构 | 不改变 `job_params`、Job result、Callback、`/models` |
| capability 内部支持 MP3/M4A/FLAC 转 WAV | 内部能力扩展 | 外部 schema 未放宽前不可被外部请求触达 |
| 允许外部提交 MP3/M4A/FLAC | 对外合同变更 | 必须同步 schema、API contract、测试、runbook、real-flow |
| 对外暴露视频输入 | 对外合同变更 / 可能是新能力 | 先决定是音频 job 兼容输入还是新 video job |
| capability plan / execution 字段进入查询响应 | 对外合同变更 | 必须显式进入 API 文档和兼容策略 |
| `/models` 展示新模型资产选择 | 对外合同变更 | 必须同步模型目录、API 文档和 registry 测试 |
| Callback 增加新字段 | 对外合同变更 | 必须保持旧字段稳定并制定兼容窗口 |

迁移规则：

- 对外合同变更采用 expand-contract 思路：先新增兼容字段或新 job_type，再迁移调用方，最后再收缩旧合同。
- Phase 3 可以先让 Media Preprocessor 内部具备 MP3/M4A/FLAC 转 canonical WAV 的能力。
- 只要外部 `AudioStemSeparationInputObject.content_type` 仍是 `Literal["audio/wav"]`，外部请求仍应拒绝非 WAV。
- 对外开放多格式输入时，必须显式修改 schema、API contract、测试、runbook 和 real-flow。
- 如果多格式输入语义明显不同，优先新增字段或新 `job_type`，不要在旧字段里隐式扩展。
- 视频输入必须单独决策：它是音频 job 的“从视频抽音频”兼容能力，还是新的 video capability / video job。两者不能混在隐式 fallback 里。

### Phase 5：错误投影和日志

Capability Service 应先定义能力级错误，再由 job 投影成业务错误。不要让每个 job 私自复制一套底层错误解释。

建议分层：

```text
Capability error
  MEDIA_SOURCE_INVALID
  MEDIA_FETCH_FAILED
  MEDIA_HASH_MISMATCH
  MEDIA_PROBE_FAILED
  MEDIA_POLICY_REJECTED
  MEDIA_TRANSCODE_FAILED
  MEDIA_OUTPUT_INVALID

Job error projection
  AUDIO_STEM_INPUT_INVALID
  AUDIO_STEM_DURATION_EXCEEDS_LIMIT
  AUDIO_STEM_INFERENCE_FAILED
  ...
```

投影规则：

- capability 内部错误必须携带 `stage`、`source_reason`、`retryable` 和安全的 `details`。
- job 可以把 capability 错误投影成自身允许的业务错误码；`stage`、`source_reason`、`retryable` 默认只作为内部排障事实保留。
- public error 必须稳定、可枚举、可测试。
- 外部工具缺失、ffmpeg 退出码、probe 失败等不能被吞掉或伪装成空结果。
- public error 不得暴露 provider 原始报错、密钥、完整 URL、文件系统路径、child id 或 workflow node key。
- 日志只记录 `capability_key`、`stage`、`job_id`、`attempt_id`、`request_id`、hash、size、duration、error_code；不记录密钥、完整 URL token、原始媒体内容、base64 或大 payload。

错误数据面：

| 层 | 是否公开 | 内容 |
|---|---|---|
| `CapabilityFailure` | 否 | capability error code、stage、source_reason、retryable、安全 details |
| Job business error | 是 | job_type 允许的稳定业务错误码和面向调用方的安全 message |
| debug projection | 按需显式公开 | 只有被 API contract 明确定义为稳定枚举的字段才能进入查询响应或 Callback |

如果需要对外暴露 `stage` 或 `source_reason`，必须先把它们定义成稳定公开枚举，并同步 API contract、Callback 兼容策略和测试；不能直接把内部 stage 名称透传给调用方。

### Phase 6：模型资产和模型目录边界

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

### Phase 7：持久化表的触发门槛

第一阶段不新增 capability/model/media processing 数据表。原因是当前 Job、Attempt、runtime snapshot、最终 Job result、最终对象存储产物和 AI call ledger 已经覆盖主要事实源；过早建表会制造维护成本和事实源竞争。

不建表时的事实源：

| 事实 | 第一阶段来源 |
|---|---|
| 调用方请求 | `jobs.job_params_ref` / runtime snapshot |
| 选择的 job_type、runtime fields、output target | `runtime_ref` |
| capability source/spec/policy/adapter | runtime fields 内的 `capability_plan_snapshot` |
| capability attempt 执行事实 | attempt 级内部日志 / 未来按需追加的 execution metadata |
| attempt 生命周期 | `job_execution_attempts` |
| 最终结果产物 | Job result + 对象存储 ref |
| AI provider 调用成本 | `ai_call_ledger_entries` |

能力执行形态决策顺序：

| 形态 | 使用条件 | 禁止条件 | 事实源 |
|---|---|---|---|
| inline capability step | 处理时间短，不需要独立调度、独立恢复、独立查询或跨 job 复用 | 需要独立 lease / heartbeat / retry / cancel | 当前 Job attempt + `capability_plan_snapshot` |
| internal child Job / workflow node | 需要独立调度、重试、恢复、取消或并行编排，但不需要独立对外查询表 | 只是为了复用公共工具合同 | 现有 Job / workflow / Attempt / recovery 机制 |
| `capability_runs` | 需要按 capability 维度独立查询、审计、重放、缓存复用或运营统计 | 只有单个 job 内部临时步骤，且 Job/Attempt 已足够排障 | 新增 capability 表和 migration |

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

如果某个处理阶段需要独立可靠性，但不需要独立查询表，优先由 `job_type` / workflow 把该步骤提升为 `visibility="internal"` 的 child Job，复用现有 Job / workflow / Attempt / recovery 机制，而不是新建 capability 专用状态机。

### Phase 8：扩展到其他公共能力

Media Preprocessor 稳定后，其他能力按同一骨架进入：

| 能力 | 可能入口 | 首要边界 |
|---|---|---|
| Image Preprocessor | `app/capabilities/image_preprocessor` | 尺寸、格式、alpha、背景、hash、EXIF 清理 |
| Document Parser | `app/capabilities/document_parser` | 文件类型探测、页数/大小限制、文本抽取、结构化结果 |
| Archive Extractor | `app/capabilities/archive_extractor` | 解压限制、路径穿越防护、文件数/大小上限 |
| Model Input Builder | `app/capabilities/model_input_builder` | 模型输入 tensor / prompt / media bundle 构建，不直接调用 provider |

每个新能力都必须先回答：

- 它是哪个 Job Flow Step 的合同层？
- 是否已有 job 内部逻辑重复出现？
- 是否会被至少两个 job 或一个 job + 一个同步 API 复用？
- 是否需要独立错误码、日志事件和测试？
- 是否需要独立持久化？如果需要，为什么 Job/Attempt/runtime snapshot 不够？

## Acceptance

- 文档和代码都明确：Capability Service 是 Job Flow Step 的合同层，不是独立任务系统；是否串行、并行、扇形或 child Job 化由 `job_type` / workflow 决定。
- Capability Service 不拥有 queue、lease、heartbeat、retry、dispatch、callback 或 Job 状态迁移；需要独立调度和恢复时，由 `job_type` / workflow 升级为 internal child Job。
- `app/jobs/payload_adapters` 的职责保持为 caller payload adapter，不承载媒体转码、模型输入构建或通用工具流水线。
- `app/jobs/payload_adapters` 只产出未冻结的 source/ref candidate；`SourceContract`、`ResolvedSource`、`FetchSpec` 只由 job executor 构造和冻结。
- 新 capability 的 job-facing 入口只接收类型化 source/spec/policy/context，不直接接收 CPP/HTTP payload 形状，也不反向依赖 Job kernel。
- `CanonicalObjectRef` 不被当作完整读取合同；首个 media capability 必须引入可冻结的 `ResolvedSource` / `FetchSpec`。
- 本计划覆盖的可复用媒体、模型输入和公共处理能力，不再让 `job_type` executor 复制通用处理流水线，也不跨 import 其他 job_type 的私有函数；一次性、单 job 私有且无复用价值的局部逻辑不强制抽成 capability。
- 首个 `Media Preprocessor` 落地时，`audio_stem_separation` 和 `audio_stem_separation_triton` 共享同一套输入标准化能力。
- `source`、spec、policy、adapter key/version 和 capability version 必须在创建 Job 时冻结到 `capability_plan_snapshot`，执行期不能按最新配置重新推导。
- `capability_execution_metadata` 必须是 attempt 级执行事实，不能回写或覆盖 `capability_plan_snapshot`。
- runtime snapshot 不能冻结敏感明文、完整 URL、临时 URL token 或易失外部状态。
- content type / source 事实必须按 `SourceContract`、source metadata、observed fact、probe fact 的优先级校验；不一致时 fail-fast，不做隐式兼容。
- 当前 WAV-only 外部合同不能被隐式放宽；多格式输入必须显式修改 schema、API contract、测试和 runbook，或新增字段 / 新 `job_type`。
- 仅内部重构不得改变 `job_params`、Job result、Callback 或 `/models` 对外合同；对外合同变更必须按 expand-contract 处理。
- 第一阶段不新增数据库表；如后续新增 `capability_runs` 或 `capability_materializations`，必须有 Alembic migration、repo/query、状态迁移测试和恢复说明。
- 失败路径必须 fail-fast，返回稳定错误码；不做 silent fallback、隐式降级、截断成功或部分产物伪装成功。
- Capability 内部 `stage` / `source_reason` 默认不进入 public error；对外暴露前必须先定义稳定公开枚举和兼容策略。
- 能力级日志必须使用白名单事件，不记录密钥、完整 URL token、原始媒体内容、base64 或大 payload。
- 至少补齐正常路径、非法 source contract、resolved source/hash 不一致、fetch 超时/超限、redirect 拒绝、adapter 失败、配置缺失、工具缺失、输出校验失败、snapshot freeze、旧合同兼容和两个 audio job 共享输入 parity 测试；涉及 child Job 时再补 workflow/recovery 和公开错误脱敏验证。

## Non-goals

- 不把本仓库改成插件平台，不做 entrypoint 自动发现、插件 manifest 或数据库 capability catalog。
- 不把所有公共函数塞进 `utils`、`helpers` 或继续膨胀 `app/jobs/payload_adapters`。
- 不在第一阶段新增模型资产数据库表、媒体处理运行表或 capability catalog 表。
- 不把 AI provider billing、usage normalizer 或 pricing 逻辑复制进 capability；涉及 AI provider 时继续走 AI facade。
- 不把 Triton model repository、ffmpeg 二进制安装、模型下载和业务 Job 合同混成一个目录或一个配置面。
