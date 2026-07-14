# 统一注册治理架构计划

本文定义本项目代码注册机制的统一治理方式。第一阶段只服务新增的 `Job Type -> Capability -> Tool -> Error` 能力链路；已有 operation、schema、prompt、workflow、model、pricing 和 adapter registry 作为基线与参照，不在本计划中重做。

## 定位

Registry 在本项目中不是插件系统，也不是数据库 catalog。它是 **代码事实源 + 启动期合同校验系统**。

本文解决：

- 统一 ref 规范。
- 统一 definition 元模型边界。
- 统一 registry graph 和 `validate_all_registries()` 演进方向。
- 统一 error reason owner / visibility / projection 规则。
- 统一配置依赖、entrypoint、schema 和引用链校验规则。

本文不解决：

- 不实现运行时动态发现。
- 不实现 entrypoint 自动加载。
- 不新增数据库表。
- 不提供后台管理、开关面板或远程配置中心。
- 不替代 Job kernel 的调度、retry、lease、heartbeat、callback 和 recovery。
- 不替代 `docs/api/` 对外合同。

## 当前基线

当前已有多处 registry 或白名单机制：

| 机制 | 当前入口 | 当前职责 |
|---|---|---|
| Job Type Registry | `app/jobs/registry.py`、`app/jobs/types/register.py` | 注册 `JobExecutor`，暴露 `JobTypeSpec` |
| Workflow Registry | `app/workflows/registry.py` | 注册 workflow definition |
| Error Registry | `app/core/error_registry.py` | 注册 error reason/code/scope/owner/retryable，支持 freeze |
| Operation Registry | `app/api/operations.py` | 注册 HTTP operation contract |
| Schema Registry | `app/schemas/registry.py` | 注册 Pydantic schema name |
| Prompt Registry | `app/core/prompt_templates.py` | 校验 prompt ref、job_type 和输出 schema |
| Model Registry | `app/core/model_registry.py` | 校验模型公开投影与运行时配置 |
| Pricing Registry | `app/core/pricing_registry.py` | 校验 pricing ref 和成本配置 |
| AI Adapter Registry | `app/integrations/ai_adapters/registry.py` | 注册 provider adapter |
| Log Event 白名单 | `app/core/logging.py` | 限制结构化业务日志事件 |
| Registry Checks | `app/core/registry_checks.py` | 当前统一校验入口 |

当前问题不是没有 registry，而是 registry governance 还不完整：

- registry 分散存在，但缺少统一 ref / graph / owner / projection 语言。
- `validate_all_registries()` 已经存在，但还不是显式 registry graph。
- 计划中的 capability/tool registry 如果各自实现校验，会形成新的分裂。
- error reason 已经有 owner/scope/retryable，但还缺 visibility 和 projection rule 的统一表达。
- 工具、能力、模型 adapter、外部 CLI 的边界还没有统一准入规则。

## 核心原则

```text
各层注册，不分裂治理。
各层声明，中央校验。
局部 owner，统一依赖图。
代码事实源，不做数据库 catalog。
启动期 fail-fast，运行期只读使用。
```

Registry Governance 必须保持轻量：

- 使用普通 Python definition / dataclass / Pydantic model。
- 使用显式 import / register，不使用自动扫描。
- 复用现有 `validate_all_registries()` 演进，不引入依赖注入容器。
- 只校验合同、引用、配置和边界，不执行真实外部调用。
- 不把 registry 变成业务配置中心。

统一生命周期：

```text
declare -> register -> freeze -> validate -> consume
```

含义：

- `declare`：各模块声明本层 definition。
- `register`：composition root 显式注册。
- `freeze`：注册完成后冻结，运行期不可变。
- `validate`：启动期统一校验 shape、引用、entrypoint、settings、error projection 和依赖方向。
- `consume`：运行期只读使用 registry，不动态追加定义。

## 目标模型

统一治理的目标结构：

```text
app/core/registries/
  refs.py          # ref 解析和格式校验
  definitions.py   # 跨 registry 的校验辅助类型；不强制统一基类
  graph.py         # registry graph 快照
  checks.py        # graph validation

app/core/registry_checks.py
  validate_all_registries()
```

第一阶段可以不立即创建 `app/core/registries/` 目录；如果改动太大，可先在 `app/core/registry_checks.py` 内演进。但目标必须是一个统一治理入口，而不是每层 registry 自己维护闭环。

逻辑图：

```text
JobTypeDefinition
  -> CapabilityDefinition
    -> ToolDefinition
      -> Integration Adapter

OperationSpec
  -> Schema
  -> ErrorDefinition
  -> LogEvent

PromptSpec
  -> PromptTemplate
  -> Schema

ModelDefinition
  -> PricingDefinition
  -> AdapterDefinition

RegistryGraph
  validates all references, owners, visibility, entrypoints and settings
```

第一阶段的 registry graph 范围只覆盖新增能力链路：

```text
Job Type -> Capability -> Tool -> Error
```

Operation、schema、prompt、model、pricing、adapter 等既有 registry 继续由现有校验维护，只作为统一治理的参照样例；不要在第一阶段重做这些 registry。

## Ref 规范

新增跨层引用必须使用稳定 ref，不直接引用实现路径。

| 类型 | 格式 | 示例 | 说明 |
|---|---|---|---|
| `job_type` | `<job_type>` | `audio_stem_separation_triton` | 当前沿用现有 key，不强制版本化 |
| `capability_ref` | `<capability_key>:<version>` | `media_preprocessor:1` | 新增 capability 从 day-1 版本化 |
| `tool_ref` | `<tool_key>:<version>` | `ffmpeg:1` | 新增 tool 从 day-1 版本化 |
| `prompt_ref` | `<job_type>.<step>` 或现有格式 | `poster_title_image.style_probe` | 沿用当前 prompt registry |
| `pricing_ref` | `<provider>:<model>@<version>` | `openai:gpt-image-2@2026-06-24` | 沿用当前 pricing registry |
| `schema_ref` | Pydantic class name | `PosterTitleImageParams` | 沿用当前 schema registry |
| `operation_id` | stable operation id | `create_job` | 沿用当前 operation registry |
| `error_reason` | stable reason string | `AUDIO_STEM_INPUT_INVALID` | error registry 主键；代码内部引用它 |

Error 术语规则：

- `error_reason` 是 registry 主键和代码内部引用值。
- `code` 是对外 envelope / Callback 的兼容码，必须唯一。
- `owner` 表示归属模块或领域。
- `visibility` 决定是否允许进入 public API / Callback。
- `projection` 描述 internal reason 能否被投影成某个 public job business error。

规则：

- Runtime snapshot 只能冻结 ref 和 stable snapshot，不冻结 entrypoint path。
- ref 语义变化必须提升 version，不能让旧 Job 读到新语义。
- 实现路径只存在于 registry definition 内部，用于启动期 import 校验。
- 对外合同只暴露被 API 明确定义的 public 字段，不暴露 registry 内部路径。

## Definition 边界

本节只定义统一治理需要校验的最小字段意图，不替代各领域文档的详细设计。Capability / Tool 的领域边界仍以 [`job-capability-tool-registry-architecture.md`](job-capability-tool-registry-architecture.md) 为准。

### JobTypeDefinition

Job type 层继续以现有 `JobTypeSpec` 为基础演进。新增字段只应表达 Job 合同和允许引用：

- `job_type`
- params/runtime/result schema refs
- visibility / role / execution mode
- public error codes
- log events
- prompt specs
- retry policy snapshot
- `allowed_capability_refs`

不能放入：

- tool entrypoint path
- integration adapter raw config
- provider secret
- capability 内部 stage
- tool error 直接 public 投影

### CapabilityDefinition

Capability 层定义 Job Flow Step 合同：

- `capability_ref`
- plan snapshot schema ref
- result schema ref
- service entrypoint path
- allowed tool refs
- capability error codes
- log events

不能放入：

- retry policy
- lease / heartbeat
- visibility
- callback
- queue / dispatch
- recovery
- Job state transition

这些都属于 Job / workflow / Job kernel。

### ToolDefinition

Tool 层定义底层执行边界：

- `tool_ref`
- kind
- entrypoint path
- request/result schema refs
- required settings
- startup validators
- tool error codes
- log event refs

不能放入：

- Job id / attempt id ownership
- Job status mutation
- public result projection
- callback
- retry decision
- lease / heartbeat

普通纯函数 helper 不注册成 tool。只有跨 capability 复用、触达外部系统/SDK/命令行/模型 runtime、需要版本冻结或启动前校验的执行边界，才注册成 tool。

### ErrorDefinition

现有 `ErrorSpec` 应扩展治理语义，而不是另起一套 error registry：

- reason
- code
- scope
- owner
- retryable
- details schema
- visibility: `public` / `internal`
- projection target: allowed public job errors 或 none

规则：

- Tool error 默认 internal。
- Capability error 默认 internal。
- Job business error 可以 public，但必须由 `job_type` 声明。
- Tool / Capability error 不能直接透传到 public API 或 Callback。
- public error 必须有稳定 message 和兼容策略。

## Registry Graph 校验

`validate_all_registries()` 应演进为统一 graph 校验入口。

至少校验：

- ref 格式合法。
- ref 唯一。
- `job_type.allowed_capability_refs` 都存在。
- `capability.allowed_tool_refs` 都存在。
- schema refs 都存在于 schema registry。
- error reasons 都存在于 error registry。
- error codes 不重复。
- public error 只能由 job type 或 operation 对外声明。
- internal error 不进入 public response / Callback。
- log events 都存在于白名单。
- prompt refs 都存在，并且 output schema 匹配。
- model refs 引用的 adapter / pricing / required settings 都存在。
- tool entrypoint 可导入。
- required settings 可被当前配置面识别。
- startup validators 可导入且只做安全本地检查。
- 禁止反向依赖：`integrations` 不能依赖 `tools/capabilities/jobs`，`tools` 不能依赖 `jobs`。

校验失败必须 fail-fast，不允许 silent fallback、自动降级或跳过未知引用。

## Composition Root

注册应集中发生在明确入口，而不是散落在 import 副作用中。

现有入口：

```text
app/jobs/types/register.py
  register_all_job_types()
```

远期可以考虑更明确的统一入口：

```text
app/registrations.py
  register_all_definitions()
    register_core_errors()
    register_job_types()
    register_workflows()
    register_capabilities()
    register_tools()
    register_operations()
```

这不是第一阶段目标。第一阶段优先复用现有 API / worker bootstrap 和 `app/jobs/types/register.py`，但必须保持一个清晰的 composition root 原则：

- 新 definition 必须有显式注册入口。
- 测试和 app startup 使用同一套注册入口。
- registry freeze 发生在所有注册完成之后。
- freeze 后重复注册同一 definition 可以幂等，变更 definition 必须报错。

## 错误投影治理

错误分层：

```text
Tool / Adapter error
  -> CapabilityFailure
    -> Job business error
      -> public ErrorEnvelope / Callback
```

投影规则：

- Tool / Adapter error reason 只描述底层执行失败，不直接 public。
- CapabilityFailure 描述 Job Flow Step 内部失败，不默认 public。
- Job business error 由 job type 显式投影并声明为 public。
- OperationSpec 只能引用 public error。
- Retry policy 只能引用已注册且 retryable 语义明确的 error reason。
- 同一个 public code 不能绑定多个 reason。

## Planned Work

### Phase 1：Governance skeleton

- 实现 `capability_ref`、`tool_ref` parser 和测试。
- 扩展或新增 `CapabilityDefinition`、`ToolDefinition`。
- 在现有 `JobTypeSpec` 上补 `allowed_capability_refs`。
- 在 `ErrorSpec` 上评估增加 `visibility` 和 `projection`。

### Phase 2：Graph validation

- 在现有 `validate_all_registries()` 上扩展 `Job Type -> Capability -> Tool -> Error` 引用图校验。
- 不重做 model / prompt / pricing / operation registry。
- 增加 graph 校验测试。
- 测试未知 capability/tool ref 会 fail-fast。
- 测试 missing settings / missing entrypoint 会 fail-fast。
- 测试 internal error 不能进入 operation public error 列表。
- 测试 `app/jobs/types/*` 不能直接 import 底层 integration adapter。

### Phase 3：First vertical slice

- 为 media preparation 定义首个 `CapabilityDefinition`。
- 为 ffmpeg 或等价执行边界定义首个 `ToolDefinition`。
- 将 audio job 输入准备迁移到 registered capability/tool 链路。

## Non-goals

- 不做插件市场。
- 不做动态发现。
- 不做数据库 catalog。
- 不做运行时注册变更。
- 不做远程配置中心。
- 不用 registry 替代 Job kernel。
- 不用 registry 替代 API contract。
- 不把普通 helper 全部注册成 tool。
- 不把统一治理实现成统一基类、内部 DSL、依赖注入容器或新框架。
- 不把所有现有 registry 一次性重写成单一总 registry。
- 不为 registry 治理新增 public API。
- 不让 registry 承担调度、租约、重试、恢复、回调、计费或状态存储。

## Acceptance

- 新增跨层 registry 共享一套 ref 语言和 graph 校验入口。
- `validate_all_registries()` 能在现有校验基础上接入 `Job Type -> Capability -> Tool -> Error` 引用图。
- 新增 capability/tool 必须通过统一 graph 校验，不允许各自为战。
- registry 仍是代码事实源，不引入数据库表或插件 manifest。
- unknown ref、duplicate ref、missing schema、missing error、missing setting、missing entrypoint 都会 fail-fast。
- Tool / Capability internal error 不会直接进入 public API 或 Callback。
- runtime snapshot 只冻结 ref 和 stable snapshot，不冻结实现路径或 provider raw config。
- 文档和测试都能说明 registry governance 是启动期合同校验系统，不是运行时插件系统。
