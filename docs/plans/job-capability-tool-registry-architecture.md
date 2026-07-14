# Job Capability 与 Tool Registry 架构地基计划

本文定义 Job 能力层后续演进计划。`Job Type -> Capability -> Tool -> Integration Adapter` 的最小注册骨架、Source/Snapshot 合同和首个音频 media capability 已落地；当前事实以 [`../current/registry-governance.md`](../current/registry-governance.md) 为准。本文只保留尚未完成的能力层治理事项。

## 定位

本服务是 **AI + Job + 异步执行能力层服务**。服务对象是 `Job` 和 `child Job`，不是用户系统、项目系统、通用 workflow 平台、插件平台或全能后端项目。

本文解决：

- `Job Type`、`Capability`、`Tool`、`Integration Adapter` 的责任分层。
- capability 如何作为 Job Flow Step 的稳定合同。
- tool 如何作为 capability 调用底层执行边界的注册对象。
- source/fetch/spec/policy 如何在创建 Job 时冻结到 runtime snapshot。
- 首个 media capability 如何消除 audio job 之间的私有函数复用。

本文不解决：

- 不定义统一 registry kernel 的全部元模型；见 [`registry-governance-architecture.md`](registry-governance-architecture.md)。
- 不新增数据库表。
- 不新增公开业务 API。
- 不把 capability 做成独立任务系统。
- 不提前实现图片、文档、压缩包等未来能力。

## 心智模型

运行时调用链：

```text
Job Type / Workflow
  对外 job_params / runtime snapshot / result / error / child Job 编排
        |
        v
Capability
  Job Flow Step 的类型化合同、策略、错误和结果模型
        |
        v
Tool
  已注册的底层执行边界，使用 tool_ref 冻结版本
        |
        v
Integration Adapter
  具体 OSS、ffmpeg、Triton、ONNX Runtime、AI provider SDK 或 CLI 调用
```

治理链路：

```text
Unified Registry Governance
  收集 JobTypeDefinition / CapabilityDefinition / ToolDefinition / ErrorDefinition
  构建 registry graph
  启动期 fail-fast 校验引用、schema、error、settings、entrypoint 和依赖方向
```

关键边界：

- 对外稳定资源仍然是 Job；Capability 和 Tool 默认不是对外资源，也不是默认独立查询对象。
- `Job Type` 决定一个请求如何成为 Job、如何冻结 runtime、如何编排 child Job、如何投影 public result。
- `Capability` 只表达某个 Job Flow Step 的稳定执行合同，不拥有调度、重试、恢复或 Job 状态迁移。
- `Tool` 只表达 capability 可调用的底层执行边界，不拥有 Job 状态、不暴露 caller payload、不决定 retry 语义。
- `Integration Adapter` 只封装底层技术调用，不理解调用方 payload，不写 Job 状态，不直接形成 public result。
- 依赖方向只能是 `jobs/workflow -> capabilities -> tools -> integrations`，禁止反向依赖。

Capability 不是工具，Tool 不是 Job。需要独立可靠性的步骤应升级为 `visibility="internal"` 的 child Job / workflow node，而不是让 capability 自己长出状态机。

## Current Baseline

当前事实源仍以代码和 `docs/current/` 为准。本计划只记录后续待硬化事项和新增能力准入规则。

已实现的 Job 数据面：

| 事实 | 当前来源 |
|---|---|
| Job 聚合、root/child lineage、外部请求、runtime snapshot、result/error | `job_aggregates` |
| 幂等提交键 | `job_submission_keys` |
| attempt、lease、heartbeat、retry policy、retry decision | `job_execution_attempts` |
| Taskiq dispatch 的可靠发布和 dead letter | `dispatch_outbox` |
| Callback 投递、签名、重试和 dead letter | `callback_outbox` |
| Job / attempt / callback 审计事件 | `job_audit_events` |
| AI provider 调用、usage、cost、billing 状态 | `ai_call_ledger_entries` |

已实现的注册基础：

- `app/jobs/registry.py` 已经提供 `job_type -> JobExecutor` 显式注册。
- `app/jobs/types/register.py` 已经是 job type、workflow 和业务 error 注册的 composition root。
- `app/core/error_registry.py` 已经提供全局 error registry 和 freeze 机制。
- `app/core/registry_checks.py` 已经提供 `validate_all_registries()`，覆盖 error、operation、job_type、schema、prompt、log event 和 route operation 校验。
- `app/workflows/registry.py`、`app/schemas/registry.py`、`app/integrations/ai_adapters/registry.py` 已经存在分散 registry。

已落地的 capability/tool 事实：

- `CapabilityDefinition` 和 `ToolDefinition` 已落地。
- `media.audio_input:1` 已作为首个 capability 注册。
- `object_storage_read:1` 已作为首个 tool 注册。
- `AudioWavInputPlanSnapshot` 已在创建 Job 时冻结到 audio job runtime fields。
- `audio_stem_separation` 和 `audio_stem_separation_triton` 已共同使用 `media.audio_input:1`，不再跨 import 对方 executor 私有输入函数。
- capability 执行期只消费 frozen snapshot，不直接解析调用方 payload。
- error projection 和 import direction guard 已接入 registry / 结构性测试。

当前剩余缺口：

- 需要独立调度、恢复或取消的能力步骤仍需按 child Job 决策规则单独评审。
- 未来新增 Image / Document / Archive capability 时，需要复用当前注册准入，而不是复制音频私有实现。

## 架构边界

### Job Type

`app/jobs/types/*` 负责：

- 外部 `job_params` schema。
- `runtime_ref` / runtime fields 冻结。
- Job Flow 和 child Job 编排决策。
- public result / Callback / business error 投影。
- 声明 `allowed_capability_refs`，并把调用方 payload candidate 转成 capability plan snapshot。

`app/jobs/types/*` 不负责：

- 直接调用 `ffmpeg`、`Triton`、`ONNX Runtime`、OSS SDK 或 provider SDK。
- 复制通用媒体处理、模型输入构建或外部工具流水线。
- 绕过 capability 直接消费底层 adapter response。

### Capability

`app/capabilities/*` 负责：

- Job Flow Step 的 Spec、Policy、Plan Snapshot、Result 和 Error。
- 声明 `allowed_tool_refs` 并调用已注册 tool。
- 能力级 fail-fast 校验、错误归一、结构化日志字段。
- 进程内执行结果返回给当前 Job Flow。

`app/capabilities/*` 不负责：

- queue / dispatch。
- lease / heartbeat。
- retry / recovery。
- Callback。
- Job / Attempt / outbox 状态迁移。
- provider billing。
- 解析 CPP / HTTP 调用方 payload 形状。
- 暴露 provider raw response 或内部 stage 作为 public contract。

### Tool

`app/tools/*` 负责：

- 所有底层执行边界的代码级注册。
- tool entrypoint 引用路径声明。
- 配置依赖和启动探测声明。
- tool error family / error codes 声明。
- 将 capability request 转给 integration adapter。

`app/tools/*` 不负责：

- 动态插件发现。
- 数据库存储。
- 运行期启停工具。
- 调度、租约、重试、恢复。
- public API 投影。
- Job 状态迁移。

### Integration Adapter

`app/integrations/*` 负责：

- OSS、ffmpeg、Triton、ONNX Runtime、AI provider SDK、CLI 等底层技术调用。
- 将底层异常转换成 adapter 层安全错误。
- 不记录密钥、完整 URL token、原始媒体内容或大 payload。

`app/integrations/*` 不负责：

- Job result 结构。
- Callback payload。
- Job 状态迁移。
- 业务错误码投影。
- capability plan snapshot 构造。

## Registry 依赖

本文不单独定义一套 registry 规则。所有 registry 都必须接入统一治理计划：

| 层 | 本文关注 | 统一治理文档关注 |
|---|---|---|
| Job Type Registry | `allowed_capability_refs`、runtime snapshot、public error 投影 | ref 规范、definition 元模型、graph 校验、error projection 校验 |
| Capability Registry | plan/result schema、allowed tools、capability error | ref 规范、schema 引用、tool 引用、error 可见性 |
| Tool Registry | tool entrypoint、settings、startup validator、tool error | ref 规范、entrypoint 可导入、settings 可识别、反向依赖禁止 |
| Error Registry | Job/capability/tool error 分层投影 | owner、scope、visibility、retryable、public projection 规则 |

统一约束：

- `capability_ref` 和 `tool_ref` 的语法由 [`registry-governance-architecture.md`](registry-governance-architecture.md) 定义。
- Job runtime snapshot 只冻结 `capability_ref`、`tool_ref` 和稳定 plan snapshot，不冻结 tool entrypoint 或 provider raw config。
- 未注册 capability 不能被 job type 引用。
- 未注册 tool 不能被 capability 使用。
- Tool error 不能直接 public；Capability error 默认 internal；Job business error 才能进入对外合同。

## Source 与 Snapshot 合同

Capability 不直接消费调用方 payload，也不只消费裸 `CanonicalObjectRef`。Job executor 应在创建 Job 时构造并冻结 capability plan snapshot。

首版 source 分层：

```text
payload adapter
  -> source/ref candidate
job executor
  -> SourceContract / ResolvedSource / FetchSpec / CapabilityPlanSnapshot
capability
  -> 按 frozen snapshot 执行
```

首版字段意图：

| 类型 | 最少表达 | 不表达 |
|---|---|---|
| `SourceContract` | schema version、source kind、accepted content types、allowed buckets、allowed regions | 下载 URL、临时 token、调度状态、工具配置 |
| `CanonicalObjectRefSnapshot` | provider、bucket、region、key、content type、content hash | 临时 URL、认证信息、读取超时 |
| `ResolvedSource` | source contract、canonical ref、observed content type、observed size | 外部 payload 歧义、Job 状态、public result |
| `FetchSpec` | read mode、endpoint key、max bytes、timeout、redirect policy | 完整 URL token、业务结果、是否 child Job |
| `CapabilityPlanSnapshot` | capability ref、tool refs、source/fetch/spec/policy snapshot | 执行中间态、临时文件路径、provider raw response、tool entrypoint path |

冻结规则：

- `SourceContract`、`ResolvedSource`、`FetchSpec`、`capability_ref` 和 `tool_ref` 必须在创建 Job 时冻结。
- 执行期只能读取 frozen snapshot，不得按最新配置重新推导策略。
- snapshot 不得冻结敏感明文、完整 URL token、临时 URL 或易失外部状态。
- `public_url` / `internal_url` 只是读取入口或调用方兼容字段，不能单独作为权威对象身份。
- `(provider, bucket, region, key, content_hash)` 是对象身份和审计身份。
- hash 不一致必须 fail-fast，不能 silent fallback。

执行事实边界：

- capability execution metadata 默认不持久化。
- prepared media metadata 默认作为进程内结果传给当前 Job Flow 后续步骤，并通过结构化日志记录。
- 如果执行事实需要独立恢复、重试或查询，优先建模为 child Job attempt，而不是新增 capability 表。

## 后续准入规则

### child Job 决策规则

默认决策顺序：

| 形态 | 使用条件 | 禁止条件 | 事实源 |
|---|---|---|---|
| inline capability step | 当前 Job attempt 内的短步骤；不需要独立调度、恢复、取消或查询 | 需要独立 lease / heartbeat / retry / cancel | 当前 Job attempt + runtime snapshot + 日志 |
| internal child Job / workflow node | 需要独立调度、重试、恢复、取消或并行编排 | 只是为了复用工具合同 | 现有 Job / Attempt / workflow / recovery 机制 |
| 新持久化表 | Job / Attempt / child Job 明确无法表达，并经过单独方案评审 | 只有中间态、临时结果或局部排障需求 | 单独设计，不在本计划预设 |

### 持久化门槛

默认不新增 capability/model/media processing 数据表。

只有同时满足以下条件，才允许提出新表设计：

- Job / Attempt / child Job / runtime snapshot 无法表达该事实。
- 该事实需要跨 Job 查询、重放、审计、恢复或运营统计。
- 该事实不能只作为 Job result、child Job result、对象存储 ref、审计事件或结构化日志存在。
- 已有明确 repo/query、状态迁移、清理策略、恢复路径和测试方案。
- 已单独形成设计文档或方案评审结论。

本计划不预置 `capability_runs`、`capability_materializations` 或模型资产表结构，避免把未来表设计误读成自然演进目标。

## 新增能力准入

新增 capability 前必须回答：

- 它服务哪个 Job Flow Step？
- 是否已有至少两个 job，或一个 job 加一个明确同步入口，存在真实复用压力？
- 是否需要独立 Spec、Policy、Result、Error 和测试？
- 它引用哪些 registered tools？
- 它的结果是当前 attempt 内部事实、child Job result，还是 public Job result？
- 为什么不能留在具体 `job_type` executor 内？
- 是否需要持久化；如果需要，为什么 Job / Attempt / child Job / runtime snapshot 不够？

一次性、单 job 私有、无复用价值的局部逻辑不强制抽成 capability。

## 新增工具准入

新增 tool 前必须回答：

- 它是 CLI、SDK、模型 runtime、对象存储、provider adapter，还是本地库封装？
- 它的 `tool_ref` 是什么？
- 它需要哪些配置项；非法或缺失配置如何启动期 fail-fast？
- 它可能产生哪些稳定 tool / adapter error？
- 哪些 capability 可以引用它？
- 它是否会触发真实费用、外部副作用、文件系统写入或网络调用？
- 它的日志脱敏边界是什么？

未经注册的 tool 不允许被 capability 或 job executor 直接调用。

## Non-goals

- 不把本仓库改成插件平台。
- 不做 entrypoint 自动发现、插件 manifest 或数据库 capability catalog。
- 不新增 capability/model/media processing 数据表。
- 不提前实现 Image Preprocessor、Document Parser、Archive Extractor 等未来能力。
- 不把 AI provider billing、usage normalizer 或 pricing 逻辑复制进 capability；涉及 AI provider 时继续走 AI facade 和 AI ledger。
- 不把 Triton model repository、ffmpeg 二进制安装、模型下载和业务 Job 合同混成一个目录或一个配置面。
- 不在旧 WAV-only 外部合同下隐式接受 MP3/M4A/FLAC 或视频输入。

## 已满足的基础验收

- 文档和当前代码都明确：本服务服务对象是 Job / child Job，不是通用后端平台。
- `Job Type -> Capability -> Tool -> Integration Adapter` 分层在代码中有对应目录、注册入口和测试。
- Capability / Tool 注册接入统一 registry graph，不各自为战。
- 所有新增 capability 必须注册；未注册 capability 不能被 job type 引用。
- 所有新增 tool 必须注册；未注册 tool 不能被 capability 使用。
- 启动期校验能发现缺失 capability、缺失 tool、缺失 required settings、重复 ref、不可导入 entrypoint 和未注册错误码。
- Capability 不拥有 queue、lease、heartbeat、retry、dispatch、callback 或 Job 状态迁移。
- 需要独立可靠性的步骤优先建模为 internal child Job / workflow node。
- 当前地基不新增数据库表。
- capability execution metadata 不回写或覆盖 plan snapshot。
- runtime snapshot 不冻结敏感明文、完整 URL token、临时 URL 或易失外部状态。
- content type、source 事实、probe 事实和 hash 校验不一致时 fail-fast，不做 silent fallback。
- 当前 WAV-only 外部合同不被隐式放宽。
- 首个音频 capability 落地后，`audio_stem_separation` 和 `audio_stem_separation_triton` 不再跨 import 对方私有 executor 函数。
- 结构性测试能发现 `job_type` 或 capability 绕过注册边界直接 import / 调用底层 adapter / tool 的行为。
- 错误投影校验能发现 capability/tool internal error 被误放入 public API 或 Callback 合同。
- Job 失败落库前会执行 public error 投影，未声明或 internal reason 不会原样进入 `GET /jobs` / Callback。
- 后续对外合同变更必须同步 `docs/api/`、schema、route、测试和 Callback 兼容策略。
