# Job Observability Governance Plan

本文记录 Job 日志与链路排查规范的治理方案。当前已经实现的日志事实以 [`../current/observability.md`](../current/observability.md) 为准；Job 状态、Attempt、Dispatch outbox、Callback outbox 和 audit timeline 事实以 [`../current/job-kernel.md`](../current/job-kernel.md) 为准；AI 调用账本事实以 [`../current/ai-billing.md`](../current/ai-billing.md) 为准。

本文不把未来方案写成当前事实，也不要求马上引入 OpenTelemetry、Loguru 或新的 tracing 平台。目标是先把新 `job_type` 接入时必须遵守的日志、stage、adapter 和排查字段收口成一套稳定标准，避免每接一个业务都重新治理一次日志。

## Mental Model

本项目已经有单 Job 排障入口。默认排查顺序应先拿 `job_id`：

```text
job_id
  -> ./scripts/jobs.sh trace <job_id>
  -> attempts / dispatch / running / ai-calls / callbacks / timeline / workflow children
  -> 必要时再结合平台日志里的 stage / adapter 摘要
```

`ops_dashboard` 的 Job Trace 也是同一思路：围绕单个 Job 汇总请求、响应、attempt、AI call、child 和 callback 证据链。

只有没有 `job_id` 时，才需要从其它 ID 反查：

| 手上只有 | 推荐动作 | 说明 |
| --- | --- | --- |
| API 响应 | 先取响应中的 `job_id` | `request_id` 用于找入口请求日志，排查 Job 仍以 `job_id` 为主 |
| 平台 API 日志 `request_id` | 找创建 Job 的日志，再拿 `job_id` | `request_id` 表示当前 HTTP 请求，不表示整个异步 Job 生命周期 |
| worker 日志 `attempt_id` | 先查 attempt，再定位 `job_id` | worker 当前日志上下文的 `request_id` 可能就是 `attempt_id` |
| callback 问题 | 先查 `job_id` 的 callback outbox | 需要关联创建请求时再看 `trigger_request_id` |
| workflow child | 用 child `job_id` 查，再通过 `root_job_id` 回到 public root | 当前实现中 root Job 的 `root_job_id` 为 `null` |
| 调用方提交记录 | 用 `caller_id + client_request_id` 查提交幂等记录，再拿 `job_id` | 这是调用方侧定位键，不是 Job 执行链路主键 |

日志只记录单次运行事实和可查询索引；数据库、对象存储、AI ledger、callback outbox 和 audit timeline 才是状态、结果、成本和投递的事实源。

治理后的理想状态是：

- 维护人员拿到 `job_id` 后，优先使用 `./scripts/jobs.sh trace <job_id>` 或 ops dashboard Job Trace 查看基础证据链。
- 只有缺少 `job_id` 时，才通过 `request_id`、`trigger_request_id` 或 `attempt_id` 补链到 `job_id`。
- 新 `job_type` 不需要临时设计日志字段，只需要在关键 stage 和外部 adapter 边界打标准事件。
- 日志、AI ledger、audit timeline、callback outbox 和 dashboard 各司其职，不互相替代。
- 日志默认只包含摘要、计数、耗时、状态、错误分类和可查询 ID，不记录完整 payload、模型响应、签名 URL 或二进制内容。

## Current Baseline

当前已经具备的骨架：

- `RequestIDMiddleware` 负责处理 `X-Request-ID`，写入 `request.state.request_id`、响应头、HTTP envelope 和日志上下文。
- 创建 Job 时，route 会把同一个 request id 写入 `runtime_fields._system.trigger_request_id`。
- worker 处理 `jobs.run_attempt` 时，当前日志上下文的 `request_id` 会设置为 `attempt_id`；原始 HTTP 创建请求 ID 需要通过 `trigger_request_id` 字段关联。
- Callback payload 会携带 `trigger_request_id`，用于把外部通知和原始创建请求关联起来。
- `app.core.logging.log_event()` 和 `LogEvent` 白名单是业务事件日志入口；新增事件需要同步 registry 引用和测试。
- 服务日志输出到 stdout/stderr；本地 `logs/api.log` 和 `logs/worker.log` 只是 `./scripts/dev.sh` local 模式的重定向结果。
- `ai_call_ledger_entries` 保存 AI provider call 的 `request_id`、`trace_id`、`job_id`、`attempt_id`、`job_type`、`operation`、`step_name`、模型、provider、耗时、usage、cost 和失败字段。
- `job_audit_events`、`job_execution_attempts`、`dispatch_outbox` 和 `callback_outbox` 保存 Job 推进、执行尝试、投递和 callback 证据。
- `./scripts/jobs.sh trace|diagnose|timeline|attempts|callbacks|ai-calls` 和 `ops_dashboard` 的 Job Trace 已经能从数据库事实源查看单 Job 证据链。

这些能力已经能支撑基础排查，但还没有形成统一的 Job stage / adapter 观测合同。

## Remaining Gaps

当前缺口主要是规范化和接入一致性，不是缺少日志量。

| 缺口 | 影响 |
| --- | --- |
| HTTP `request_id`、worker 日志上下文 `request_id` 和 `trigger_request_id` 的语义容易混淆 | 排查时可能把 attempt id 当成入口请求 ID，或反向漏查创建请求 |
| 现有 `jobs.sh trace` 能解释 Job 内核事实，但不能解释每个业务 executor 的内部关键阶段 | 新 `job_type` 的模型前处理、解析、落库等业务边界仍依赖零散日志 |
| `LogEvent` 里没有通用 stage / adapter 事件 | 新 `job_type` 容易新增业务专属事件，长期难以聚合 |
| `stage_id`、`adapter_id`、`error_category` 没有统一命名规则 | 排查时只能看自然语言事件名，难以跨业务比较 |
| AI ledger 有 `operation` / `step_name`，但与 Job stage 命名没有固定对齐规则 | 模型调用能查，模型调用前后的业务阶段不一定能对齐 |
| 新正式业务接入时没有最低观测清单 | 容易出现“Job 能跑，但失败后只能看完整 payload 或临时加日志”的情况 |
| 缺少 executor 视角的业务 timing summary | `jobs.sh trace` 能看队列、attempt、callback 等内核阶段，但业务内部耗时拆解不稳定 |
| 没有 drift gate 检查新增 `job_type` 的 log events / stage 规范 | 容易在 code review 后才发现日志字段不一致 |
| `trace_id` 目前只是 AI ledger 可选字段 | 还不是全局 tracing 语义，不应误写成完整链路追踪能力 |

## Design Boundaries

本治理借鉴两类经验，但不直接复制：

- pipeline stage contract 的 `stage_id`、`adapter_id`、事件 schema、错误分类和摘要规则。
- worker diagnostics 的 `ContextVar` 上下文绑定思想。

不直接采用的内容：

- 不重建 `./scripts/jobs.sh trace` 或 ops dashboard Job Trace；它们是当前单 Job 排障主入口。
- 不引入 Loguru；本项目继续使用标准 `logging` 和 `app.core.logging`。
- 不在本阶段把日志格式从 `key=value` 切成 JSON；这是采集合同变化，单独评估。
- 不把 OpenTelemetry、span、exporter 或 tracing backend 作为第一阶段目标。
- 不新增自由事件入口；业务事件仍必须通过 `LogEvent` 白名单。
- 不让日志替代 DB 事实源；Job 状态、成本、callback 和产物仍由现有表、对象存储和 API response 承担。
- 不要求所有日志都带全量 ID；默认以 `job_id`、`job_type`、`attempt_id` 和必要的反查 ID 为主。
- 不改变现有 lineage 语义：public root Job 的 `root_job_id` 仍为 `null`，internal child Job 的 `root_job_id` 指向 public root Job。
- 不把 `family_root_job_id` 做成 DB、API 或 callback 字段；它最多是未来日志 helper 派生出的检索辅助键。
- 不把内部观测 `stage_id` 升级为对外 `job_progress.stage` 合同；对外进度字段仍以 [`../api/service-contract.md`](../api/service-contract.md) 为准。

## Target Contract

### ID Usage Policy

| 字段 | 语义 | 来源 | 使用边界 |
| --- | --- | --- | --- |
| `request_id` | 当前 HTTP 请求 ID | `X-Request-ID` 或服务生成 | HTTP 日志、响应头、envelope |
| `trigger_request_id` | 创建 Job 的入口请求 ID | `runtime_fields._system.trigger_request_id` | worker、callback、Job 业务事件中的反查字段 |
| `job_id` | 当前 Job ID，也是默认排障主键 | `job_aggregates.id` | `jobs.sh trace`、ops dashboard Job Trace、attempt、callback、日志 |
| `root_job_id` | 当前实现 lineage 字段；public root 为 `null`，child 指向 public root Job | `job_aggregates.root_job_id` | 保持 DB/API 现有语义，不作为“始终有值”的日志聚合键 |
| `attempt_id` | 当前执行尝试 ID | `job_execution_attempts.id` | worker 执行、AI ledger、重试排查 |
| `job_type` | 当前 Job 类型 | `Job.job_type` | 聚合、过滤、错误归因 |
| `caller_id` | 调用方身份 | API header / Job record | 多调用方过滤和排障 |
| `client_request_id` | 调用方提交幂等键 | API request / `job_submission_keys` | 调用方侧重复提交定位；拿到 Job 后仍回到 `job_id` 排查 |
| `trace_id` | AI ledger 可选字段，不是当前全局追踪 ID | AI 调用侧传入或生成 | 不作为本阶段排障主键 |

worker 日志上下文中的 `request_id` 若为 `attempt_id`，只能表示当前 worker task 的执行关联键，不能替代 `trigger_request_id`。新增业务事件应显式输出 `trigger_request_id`，避免平台日志里只剩 attempt 维度。

如果后续确认平台日志需要按 workflow family 检索，可以在日志 helper 中派生 `family_root_job_id`：root 事件取自身 `job_id`，child 事件取 `root_job_id`。它不进入本阶段主合同，也不进入 DB、API 或 callback 合同。

### Stage Event

正式业务 `job_type` 的 executor 应至少在关键业务 stage 记录 completed / failed 事件。低价值字段转换、普通循环和纯内存小步骤不需要单独 stage。

建议的通用事件形态：

```text
event=job_stage_completed
event=job_stage_failed
```

建议字段：

```text
job_type
job_id
root_job_id
attempt_id
caller_id
trigger_request_id
stage_id
status
duration_ms
input_count / input_bytes / item_count
output_count / output_bytes
error_code
error_category
retryable
```

可选字段：

```text
provider
model_id
backend
```

`stage_id` 命名规则：

- 使用稳定业务节点名，不使用 backend 名称。
- 使用 snake_case。
- 不使用 `step1`、`process`、`handle`、`do_work` 这类不可聚合名称。
- 不把 provider、模型版本或临时策略写进 `stage_id`。

`stage_id` 只属于内部日志、ledger 对齐和排障聚合，不承诺等于 `job_aggregates.progress_stage` 或公开 `job_progress.stage`。如果后续需要投影到进度展示，只允许从内部 stage 单向归一为更粗的展示阶段，并且允许丢失细节。

推荐 stage 示例：

```text
validate_input
prepare_runtime
prepare_prompt
call_model
parse_model_output
persist_result
store_artifacts
schedule_callback
```

### Adapter Event

外部依赖、可替换实现或耗时明显的边界应记录 adapter call 事件。AI provider 调用优先使用 AI ledger；如果还需要日志，应只补调用摘要，不重复记录完整请求或响应。

建议的通用事件形态：

```text
event=adapter_call_completed
event=adapter_call_failed
```

建议字段：

```text
job_type
job_id
root_job_id
attempt_id
trigger_request_id
stage_id
adapter_id
backend
operation
duration_ms
status
error_code
error_category
retryable
request_bytes / response_bytes
provider
model_id
```

`adapter_id` 命名规则：

- 表示可替换实现或外部依赖，不表示业务 stage。
- 使用稳定名称，例如 `ai_gateway_text_generation`、`aliyun_oss_object_storage`、`callback_http_delivery`。
- backend 可以是字段，不写进 stage_id。

### Error Category

错误分类用于排查、重试判断和聚合，不替代现有 `AppError.code`。

| 分类 | 含义 | 默认可重试 |
| --- | --- | --- |
| `input_contract_error` | 输入不满足 API、Job params 或 stage 前置条件 | 否 |
| `output_contract_error` | 模型、adapter 或 stage 输出不满足下游合同 | 否 |
| `dependency_error` | 外部服务、对象存储、provider、callback 目标不可用或返回错误 | 视情况 |
| `timeout` | stage 或 adapter 超时 | 视情况 |
| `resource_exhausted` | 并发、容量、内存、磁盘、连接、配额不足 | 视情况 |
| `consistency_error` | 幂等、顺序、对齐、lease、状态机不变量被破坏 | 否 |
| `implementation_bug` | 代码 bug、断言失败、未预期异常 | 否 |

不要把所有错误都归为 `internal_error`。错误分类必须能帮助判断是否重试、是否告警、是否需要人工介入。

### Summary Rules

日志记录摘要，不记录大对象。

允许记录：

```text
count
bytes
content_type
schema_version
provider
model_id
operation
duration_ms
status
error_code
error_category
hash
```

禁止默认记录：

```text
完整请求体
完整模型响应
provider raw response
图片/音频/视频二进制或 base64
签名 URL
内部对象存储 URL
完整 callback ack body
密钥、token、密码、完整连接串
```

## New Job Type Checklist

新增正式业务 `job_type` 时，除 schema、registry、prompt、model、pricing、error 和 smoke/e2e 外，还应确认：

- 是否能通过 `./scripts/jobs.sh trace <job_id>` 或 ops dashboard Job Trace 解释该 Job 的队列、执行、AI call、callback 和 workflow child 证据。
- 是否能在只有 `request_id`、`trigger_request_id` 或 `attempt_id` 时反查到 `job_id`。
- 是否定义了最少关键 stage，且 stage_id 稳定、可聚合。
- 是否所有外部依赖都走已有 adapter / facade，或显式定义 adapter_id 和失败分类。
- 是否调用 AI facade，并让 AI ledger 写入 `request_id`、`job_id`、`attempt_id`、`job_type`、`operation` 和 `step_name`。
- 是否在成功、失败、可恢复失败和不可恢复失败路径都留下可查询证据。
- 是否避免在日志中记录完整 payload、模型原文、签名 URL 或二进制内容。
- 是否新增 `LogEvent` 白名单、registry 引用和测试。
- 是否只为业务关键边界增加 stage / adapter 日志，没有给普通函数、循环或字段转换制造噪声。

## Planned Work

### Phase 1: 固定文档和 review 标准

- 保留本文作为 active plan，不把未实现能力写成当前事实。
- 在后续 `docs/current/observability.md` 中补充已确认的 ID 使用规则：`job_id` 是默认排障主键，`request_id` 和 `trigger_request_id` 只负责补链。
- 在后续 `docs/api/extension-guide.md` 中补充新 `job_type` 的 stage、adapter、error category 和敏感字段 review 清单。

### Phase 2: 增强日志上下文 helper

在确认为多个 executor 带来重复字段负担后，再评估在 `app.core.logging` 中增加轻量上下文绑定能力：

```text
bind_log_context(
  job_id=...,
  root_job_id=...,
  attempt_id=...,
  job_type=...,
  caller_id=...,
  trigger_request_id=...,
)
reset_log_context(token)
```

目标是让 worker 执行期间的标准日志自动带上常用 Job 关联字段，减少每个 executor 手动拼字段。该 helper 不是本计划第一阶段前置条件。

验收前提：

- 不引入 Loguru。
- 不改变默认 stdout/stderr 出口。
- 不破坏现有 `request_id` filter。
- 上下文必须用 token reset，避免 worker 复用进程时串上下文。
- `job_id`、`job_type` 和 `attempt_id` 是主字段；workflow family 检索字段只能在后续确认必要时再派生。

### Phase 3: 增加通用 stage / adapter 事件

评估在 `LogEvent` 中增加少量通用事件：

```text
job_stage_completed
job_stage_failed
adapter_call_completed
adapter_call_failed
```

必要时提供小 helper 统一填充 `job_id`、`attempt_id`、`duration_ms`、`status` 和错误分类。helper 只负责日志字段，不负责 Job 状态迁移，也不复制 `jobs.sh trace` 已经能从 DB 推导出的内核阶段。

### Phase 4: 用一个正式业务链路试点

优先选择 `tagged_text_translation` 作为第一条正式业务试点：

- `validate_input`
- `prepare_prompt`
- `call_model`
- `parse_model_output`
- `persist_result`

其中 `call_model` 继续以 AI ledger 为事实源，日志只补 stage 边界和摘要。

试点通过后，再将可复用事实沉淀到 [`../current/observability.md`](../current/observability.md) 和 [`../api/extension-guide.md`](../api/extension-guide.md)。

### Phase 5: 纳入验证和排查入口

- 增加测试，确保新增 `LogEvent` 进入白名单和 registry 校验。
- 对新增正式 `job_type` 增加最小日志合同测试，验证关键 stage 字段和敏感字段不外泄。
- 试点后如果仍无法从日志平台排查业务内部耗时，再单独评估 `job_execution_summary` 或 `./scripts/jobs.sh trace` / ops dashboard Job Trace 的业务 stage summary。默认不新增第二套 summary 视图。

## Acceptance

本计划完成时应满足：

- 新增正式 `job_type` 有统一的日志和 stage 接入清单，不需要重新讨论字段体系。
- 新增正式 `job_type` 能优先通过 `job_id` 使用 `./scripts/jobs.sh trace <job_id>` 或 ops dashboard Job Trace 排查基础链路。
- `request_id`、`trigger_request_id`、`job_id`、`root_job_id`、`attempt_id` 和 `job_type` 在 API、worker、AI ledger、callback 和日志中语义明确，不被混用。
- workflow family 检索字段若后续实现，只作为日志辅助检索键，不改变当前 DB/API lineage 合同。
- 至少一个正式业务 `job_type` 按 stage / adapter 合同落地，并有测试覆盖关键日志字段。
- 日志上下文 helper 不会在 worker 并发或复用场景串上下文。
- `./scripts/jobs.sh trace|diagnose`、ops dashboard Job Trace 和平台日志能围绕 `job_id` 完成排查闭环；缺少 `job_id` 时有清晰反查路径。
- 敏感字段边界有测试或 review gate，完整 payload、模型响应、签名 URL 和二进制内容不会进入默认日志。
- 已实现内容被移入 [`../current/observability.md`](../current/observability.md)，本文只保留未完成计划或触发条件。

## Non-goals

- 不在本阶段引入 OpenTelemetry、Jaeger、Tempo、Datadog tracing 或 exporter。
- 不把日志格式切换为 JSON，除非后续确认日志采集平台需要。
- 不替换或重做 `./scripts/jobs.sh trace`、`diagnose`、`timeline`、`attempts`、`callbacks`、`ai-calls` 等现有排障入口。
- 不把日志作为 Job 状态、成本、callback 或产物事实源。
- 不把 `family_root_job_id` 做成新的数据库字段、API 字段或 callback 合同字段。
- 不把 AI ledger 的 `trace_id` 宣称为当前全局链路追踪 ID。
- 不新增独立 `diagnostics.py` 平行日志体系。
- 不为每个普通函数、字段转换或循环内部步骤新增 stage。
