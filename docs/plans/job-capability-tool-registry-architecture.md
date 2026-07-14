# Job Capability 与 Tool Registry 架构地基计划

本文是 Job 能力层的 active plan：目标是在项目早期一次性定清 `Job Type -> Capability -> Tool Registry -> Integration Adapter` 的地基、边界和强制约束，避免后续新增工具和能力时绕过 Job 内核、重复造局部流水线或制造第二套事实源。

## 定位

本服务是 **AI + Job + 异步执行能力层服务**。服务对象是 `Job` 和 `child Job`，不是用户系统、项目系统、业务编排平台或通用后端。

本计划解决的是三件事：

- 把可复用 Job Flow Step 抽成稳定的 `Capability` 合同。
- 把底层 CLI、SDK、runtime、provider adapter 统一纳入 `Tool Registry`。
- 用代码注册、启动校验和测试约束后续开发，而不是只依赖文档约定。

本计划不解决：

- 不新增公开业务 API。
- 不新增数据库表。
- 不把 capability 做成独立任务系统。
- 不把本仓库演进成插件平台、通用工具平台或全能后端项目。
- 不提前实现图片、文档、压缩包等未来能力。

## 心智模型

四层必须分开：

```text
Job Type
  对外 job_params / runtime snapshot / result / error / child Job 编排
        |
        v
Capability
  Job Flow Step 的类型化能力合同、策略、错误和结果模型
        |
        v
Tool Registry
  强制声明工具 tool_ref、kind、entrypoint、配置依赖和错误码
        |
        v
Integration Adapter
  具体 OSS、ffmpeg、Triton、ONNX Runtime、AI provider SDK 或 CLI 调用
```

关键边界：

- `Job Type` 决定一个请求如何成为 Job、如何冻结 runtime、如何编排 child Job、如何投影 public result。
- `Capability` 只表达某个 Job Flow Step 的稳定执行合同，不拥有调度、重试、恢复或 Job 状态迁移。
- `Tool Registry` 是代码层面的准入机制，所有外部工具、SDK、模型 runtime、CLI 和 provider adapter 必须注册后才能被 capability 使用。
- `Integration Adapter` 只封装底层技术调用，不理解调用方 payload，不写 Job 状态，不直接形成 public result。
- 对外稳定资源仍然是 Job；Capability 和 Tool 默认不是对外资源，也不是默认独立查询对象。
- 依赖方向只能是 `jobs/workflow -> capabilities -> tools -> integrations`，禁止反向依赖。

Capability 不是工具，Tool 不是 Job。需要独立可靠性的步骤应升级为 `visibility="internal"` 的 child Job / workflow node，而不是让 capability 自己长出状态机。

## Current Baseline

当前事实源仍以代码和 `docs/current/` 为准。本计划只记录待落地架构。

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

已实现的结构基础：

- `JobExecutor`、job registry 和 `app/services/jobs.py` 已经提供 `job_type` 扩展骨架。
- 当前 `root_job_id` / `workflow_node_key` 已经支持 child Job 作为内部 workflow node。
- `app/jobs/payload_adapters/` 当前只承担调用方 payload 形状适配。
- `app/integrations/` 当前承载 OSS、Triton、ONNX Runtime、AI provider adapter 和媒体工具等技术边界。
- `audio_stem_separation_triton` 复用 `audio_stem_separation.executor` 私有输入函数，证明公共能力确实存在，但边界还没有稳定。

## Remaining Gaps

- `job_type` executor 仍可能直接复制媒体、模型输入或工具调用流水线。
- 新增工具没有强制注册流程，后续开发可以绕过统一错误码、配置校验和边界测试。
- `Capability`、`Tool`、`Integration Adapter` 的术语和代码边界尚未落地。
- `CanonicalObjectRef` 更像对象身份，不是完整读取合同；source、fetch、policy、adapter plan 还没有统一 snapshot 形态。
- `audio_stem_separation` 与 `audio_stem_separation_triton` 的输入准备逻辑仍未通过共享 capability 固化。
- 当前测试主要覆盖 `job_type`、schema、错误码和 workflow，缺少 `job_type -> capability -> tool -> adapter` 引用链校验。

## 架构边界

### Job Type

`app/jobs/types/*` 负责：

- 外部 `job_params` schema。
- `runtime_ref` / runtime fields 冻结。
- Job Flow 和 child Job 编排决策。
- public result / Callback / business error 投影。
- 把调用方 payload candidate 转成 capability plan snapshot。

`app/jobs/types/*` 不负责：

- 直接调用 `ffmpeg`、`Triton`、`ONNX Runtime`、OSS SDK 或 provider SDK。
- 复制通用媒体处理、模型输入构建或外部工具流水线。
- 绕过 capability 直接消费底层 adapter response。

### Capability

`app/capabilities/*` 负责：

- Job Flow Step 的 Spec、Policy、Plan Snapshot、Result 和 Error。
- 调用已注册 tool。
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

### Tool Registry

`Tool Registry` 负责：

- 所有工具的代码级注册。
- 启动期完整性校验。
- 测试期边界校验。
- 工具配置依赖声明。
- 工具错误码声明。
- tool entrypoint 引用路径声明。

`Tool Registry` 不负责：

- 动态插件发现。
- 数据库存储。
- 运行期启停工具。
- 调度、租约、重试、恢复。
- public API 投影。

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

## Registry 设计

首阶段 registry 是代码事实源，不入库。

建议目录：

```text
app/capabilities/
  registry.py
  definitions.py
  <capability>/
    specs.py
    service.py
    errors.py

app/tools/
  registry.py
  definitions.py
```

### Capability Registry

首版 `CapabilityDefinition` 至少表达：

```python
class CapabilityDefinition(StrictBaseModel):
    key: str
    version: str
    plan_schema_path: str
    result_schema_path: str
    service_entrypoint: str
    allowed_tool_refs: tuple[str, ...]  # "<tool_key>:<tool_version>"
    error_owner: str
    error_codes: tuple[str, ...]
```

`CapabilityDefinition` 不能包含：

- `retry_policy`
- `lease`
- `heartbeat`
- `visibility`
- `callback`
- `queue`
- `dispatch`
- `recovery`

这些都是 Job / workflow / Job kernel 语义；如果放进 capability registry，能力层会滑向独立任务平台。

### Tool Registry

首版 `ToolDefinition` 至少表达：

```python
class ToolDefinition(StrictBaseModel):
    key: str
    version: str
    kind: str
    entrypoint_path: str
    request_schema_path: str | None = None
    result_schema_path: str | None = None
    required_settings: tuple[str, ...] = ()
    startup_validators: tuple[str, ...] = ()
    error_family: str
    error_codes: tuple[str, ...]
```

普通纯函数 helper 不注册成 tool。只有跨 capability 复用、触达外部系统/SDK/命令行/模型 runtime、需要版本冻结或启动前校验的执行边界，才进入 Tool Registry。

`tool_ref` 的唯一格式是 `<tool_key>:<tool_version>`，例如 `ffmpeg:1`。Capability 只引用 `tool_ref`，runtime snapshot 只冻结 `tool_ref`；`entrypoint_path` 是 registry 内部实现入口，不能进入 Job public result 或 Callback。

注册规则：

- 新增 tool 必须注册 `key/version/kind/entrypoint_path/required_settings/error_codes`。
- 新增 capability 必须注册 `key/version/plan_schema/result_schema/service_entrypoint/allowed_tool_refs/error_codes`。
- capability 只能引用已注册 tool。
- `job_type` 只能引用已注册 capability。
- tool key 或 capability key 的语义变化必须提升 version，不能让旧 Job snapshot 在执行期读到新语义。
- registry 只能表达稳定工程合同，不能成为业务配置中心。

启动期校验：

- 所有 registered capability 的 `allowed_tool_refs` 必须存在。
- 所有 registered tool 的 `required_settings` 必须能被当前配置面识别。
- 所有 `job_type` 声明引用的 capability 必须存在。
- 所有 error code 必须进入对应白名单或 registry。
- service entrypoint、tool entrypoint 和 schema path 必须可导入，缺失时 fail-fast。
- required binary、required env 或启动探测缺失时 fail-fast，不做 silent fallback。

测试期校验：

- 禁止 `app/jobs/types/*` 直接 import 底层工具 adapter，除非测试中明确列为豁免。
- 验证 `job_type -> capability -> tool -> adapter` 引用链完整。
- 验证 registry 中不存在重复 key/version。
- 验证 capability error 不会直接透传为 public error。

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
| `CapabilityPlanSnapshot` | capability key/version、tool ref、source/fetch/spec/policy snapshot | 执行中间态、临时文件路径、provider raw response、tool entrypoint path |

冻结规则：

- `SourceContract`、`ResolvedSource`、`FetchSpec`、capability key/version 和 tool ref 必须在创建 Job 时冻结。
- 执行期只能读取 frozen snapshot，不得按最新配置重新推导策略。
- snapshot 不得冻结敏感明文、完整 URL token、临时 URL 或易失外部状态。
- `public_url` / `internal_url` 只是读取入口或调用方兼容字段，不能单独作为权威对象身份。
- `(provider, bucket, region, key, content_hash)` 是对象身份和审计身份。
- hash 不一致必须 fail-fast，不能 silent fallback。

执行事实边界：

- capability execution metadata 默认不持久化。
- prepared media metadata 默认作为进程内结果传给当前 Job Flow 后续步骤，并通过结构化日志记录。
- 如果执行事实需要独立恢复、重试或查询，优先建模为 child Job attempt，而不是新增 capability 表。

## Planned Work

### Phase 0：术语和边界落地

- 在后续实现前确认 `Job Type`、`Capability`、`Tool Registry`、`Integration Adapter`、`child Job` 的代码边界。
- 明确 registry 首阶段只作为代码事实源。

### Phase 1：Registry 骨架

- 新增 `ToolDefinition` 和 `CapabilityDefinition`。
- 新增 tool registry 和 capability registry。
- 增加启动期校验入口。
- 增加 registry 单元测试。
- 增加 import 边界测试，防止 `job_type` 直接调用底层 adapter。

### Phase 2：Source / Snapshot 合同

- 定义 `SourceContract`、`CanonicalObjectRefSnapshot`、`ResolvedSource`、`FetchSpec`。
- 定义首个 capability plan snapshot 模型。
- 让 job executor 在创建 Job 时冻结 snapshot。
- 执行期只读取 snapshot，不重新推导策略。

### Phase 3：首个能力落地

首个 capability 仍建议落在音频输入准备，因为它解决当前真实问题：

- `audio_stem_separation` 与 `audio_stem_separation_triton` 不应再跨 import 私有 executor 函数。
- capability 只支持当前外部合同允许触达的 WAV 输入。
- 不在本阶段悄悄放宽 MP3/M4A/FLAC/视频输入。
- 不改变 `job_params`、Job result、Callback 或 `/models`。
- prepared media 只落 per-attempt 临时目录，不写对象存储，不进入 public result。

### Phase 4：错误投影和日志

- Capability 内部错误必须有稳定枚举。
- Job business error 由 `job_type` 显式投影。
- provider raw error、adapter request 字段、临时文件路径、child id、workflow node key 不进入 public schema。
- 日志只记录白名单字段，例如 `capability_key`、`stage`、`job_id`、`attempt_id`、`request_id`、hash、size、duration、error_code。

### Phase 5：child Job 决策规则

默认决策顺序：

| 形态 | 使用条件 | 禁止条件 | 事实源 |
|---|---|---|---|
| inline capability step | 当前 Job attempt 内的短步骤；不需要独立调度、恢复、取消或查询 | 需要独立 lease / heartbeat / retry / cancel | 当前 Job attempt + runtime snapshot + 日志 |
| internal child Job / workflow node | 需要独立调度、重试、恢复、取消或并行编排 | 只是为了复用工具合同 | 现有 Job / Attempt / workflow / recovery 机制 |
| 新持久化表 | Job / Attempt / child Job 明确无法表达，并经过单独方案评审 | 只有中间态、临时结果或局部排障需求 | 单独设计，不在本计划预设 |

### Phase 6：持久化门槛

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
- 它的 `key/version/kind` 是什么？
- 它需要哪些配置项；非法或缺失配置如何启动期 fail-fast？
- 它可能产生哪些稳定 adapter error？
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

## Acceptance

- 文档和后续代码都明确：本服务服务对象是 Job / child Job，不是通用后端平台。
- `Job Type -> Capability -> Tool Registry -> Integration Adapter` 分层在代码中有对应目录、注册入口和测试。
- 所有新增 tool 必须注册；未注册 tool 不能被 capability 或 job executor 使用。
- 所有新增 capability 必须注册；未注册 capability 不能被 job_type 引用。
- 启动期校验能发现缺失 tool、缺失 capability、缺失 required settings、重复 key/version 和不可导入 tool entrypoint。
- 测试能发现 `job_type` 绕过 capability 直接 import 底层 adapter 的行为。
- Capability 不拥有 queue、lease、heartbeat、retry、dispatch、callback 或 Job 状态迁移。
- 需要独立可靠性的步骤优先建模为 internal child Job / workflow node。
- 第一阶段不新增数据库表。
- capability execution metadata 不回写或覆盖 plan snapshot。
- runtime snapshot 不冻结敏感明文、完整 URL token、临时 URL 或易失外部状态。
- content type、source 事实、probe 事实和 hash 校验不一致时 fail-fast，不做 silent fallback。
- 当前 WAV-only 外部合同不被隐式放宽。
- 首个音频 capability 落地后，`audio_stem_separation` 和 `audio_stem_separation_triton` 不再跨 import 对方私有 executor 函数。
- 对外合同变更必须同步 `docs/api/`、schema、route、测试和 Callback 兼容策略。
