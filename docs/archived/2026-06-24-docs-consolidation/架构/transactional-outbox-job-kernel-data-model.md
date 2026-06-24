# Transactional Outbox Job Kernel 数据模型意见

```text
Status: Target Opinion / Plan
Owner: architecture
Scope: new-project Job kernel data model, queue reliability, callback delivery, audit and billing boundary
Current truth: code, tests, docs/current/job-kernel.md
```

本文重新设计新项目目标数据模型，不要求兼容当前实现的表名、字段或迁移历史。当前代码事实仍以代码、测试和 [`project-standards-code-facts.md`](project-standards-code-facts.md) 为准；本文是后续重构和新项目建模的架构意见。TaskIQ broker、dispatch publisher、worker claim、callback publisher 和 recovery 行为目标见 [`taskiq-queue-behavior-target.md`](taskiq-queue-behavior-target.md)。

## 真实需求

表面诉求是重新划分 Job 相关表。底层架构问题是：异步 Job 服务必须在 DB 事务、TaskIQ 队列、Worker 执行、Callback 投递和 AI provider 调用之间建立清晰事实源，避免“DB 已提交但消息丢失”、重复消息覆盖状态、长任务崩溃不可恢复、Callback 失败不可见，以及计费事实混入 Job 状态。

本设计必须满足：

- 严格采用 `Transactional Outbox`：业务状态和待发布消息意图在同一个 DB transaction 内写入。
- Submit 幂等是公开入口合同，必须有独立事实源：重复请求、请求指纹冲突和 retention 不能依赖 Job 主行顺带表达。
- Worker 必须是 idempotent consumer：重复 broker message 不能重复完成同一 Job。
- 长任务必须有 lease、heartbeat 和 stale recovery。
- Callback 是独立外部副作用，必须有持久投递账本和 dead letter。
- 审计事件不能成为状态真源。
- AI usage / cost 不能写进 Job 主状态作为事实源。

## 成熟模式

本模型采用以下成熟模式组合：

| 问题 | 模式 | 采用方式 |
|---|---|---|
| DB commit 后队列 publish 不能丢 | Transactional outbox | `job_aggregates` / `job_execution_attempts` 与 `dispatch_outbox` 同事务写入。 |
| broker at-least-once / 重复消息 | Idempotent consumer + CAS | Worker 只能 claim 当前 active attempt；重复消息返回 skipped。 |
| 长任务 Worker 崩溃 | Lease + heartbeat | `job_execution_attempts` 保存 worker、lease token、lease deadline、heartbeat。 |
| Callback 投递失败可见 | Transactional outbox + retry + dead letter | Job 终态事务内写入 `callback_outbox`，独立投递和重试。 |
| stuck 状态恢复 | Sweeper / reconciler | 只扫描权威表，不从日志推导状态。 |
| 状态可追踪 | Audit log | `job_audit_events` 只追加审计事件，不反向驱动业务。 |
| 计费和 usage | Append-only ledger rows | `ai_call_ledger_entries` 保存真实 provider call 事实。 |

## 不采用的设计

不采用 execution attempt 同时承担消息 outbox。执行 attempt 和消息发布是两个不同事实：attempt 表示“这次执行尝试是否被 worker claim 并完成”，dispatch outbox 表示“某条 broker publish 意图是否已发布、重试或死信”。把 publish attempts、next publish time、last publish error 放在 attempt 行里，会让执行状态和消息状态互相污染。

不采用一个过宽的通用 outbox 表同时承载 TaskIQ dispatch 和 webhook callback。两者都属于 Transactional Outbox，但失败语义不同：broker publish 关注 task name、broker ACK、重复消费；webhook callback 关注签名、HTTP status、ACK payload、下游兼容、接收方乱序和投递耗尽。共用一张表会让 `payload`、`last_response`、重试策略和运维查询逐步变成混合语义。

不把 `job_audit_events` 做成 event sourcing。当前需求只需要 audit log，不需要通过事件回放生成当前状态。状态真源必须是当前状态表和 outbox 表。

不把 AI cost summary 写入 Job aggregate 作为事实源。Job 可以展示 billing 投影，但 provider usage、cost estimate、pricing version 和 billable status 的事实源只能是 AI call ledger。

## 目标核心表

核心表是 Job kernel 正确运行所必需的事实源。删除任一核心表都会迫使另一个表承担不属于自己的状态职责。

本目标明确采用五张核心表。`job_submission_keys` 不是为了让幂等组合键“自动可改”，而是把 submit idempotency 作为稳定入口账本：同一调用方下的 canonical submit key、请求指纹、最终 `job_id` 绑定关系必须独立于 Job 生命周期状态存在。组合键如果要演进，仍然需要版本化合同、迁移和唯一索引调整，不能靠这张表静默兼容。

| 表 | 核心职责 | 不是它的职责 |
|---|---|---|
| `job_submission_keys` | Submit 幂等事实；`caller_id + key_kind + key_value + request_fingerprint -> job_id`，并裁决相同 key 不同 fingerprint 的冲突。 | 不保存 Job 当前状态、进度、结果、执行细节或 callback 结果。 |
| `job_aggregates` | Job aggregate root；调用方、job type、公开状态、进度、输入快照引用、最终结果 / 错误、当前 active attempt、TTL / soft delete。 | 不承担 submit 幂等唯一性；不保存队列发布状态、worker lease、callback 投递明细、provider usage / cost 明细。 |
| `job_execution_attempts` | 单次执行尝试；attempt number、状态、worker claim、lease、heartbeat、执行开始 / 结束、失败分类、retry decision。 | 不保存 TaskIQ publish attempts；不保存公开 Job result；不负责 Callback 投递。 |
| `dispatch_outbox` | TaskIQ broker publish 意图；每个 execution attempt 的 dispatch message、publish lease、retry、dead letter。 | 不表达 Job 当前状态；不保存 Worker heartbeat；不投递 HTTP Callback。 |
| `callback_outbox` | Job 终态 webhook / callback 投递账本；签名 payload、HTTP delivery attempts、ACK 校验、retry、dead letter。 | 不改变 Job 终态；不保存 broker publish 状态。 |

核心关系：

```text
job_aggregates 1 <- 0..N job_submission_keys
job_aggregates 1 <- 1..N job_execution_attempts
job_execution_attempts 1 <- 1 dispatch_outbox
job_aggregates 1 <- 0..N callback_outbox
```

外部 submit 默认创建一条 `job_submission_keys` 和一条 `job_aggregates`，二者在同一事务内绑定。重复 submit 命中已有 submission key 时只返回既有 Job，不创建新 Job。内部 retry、worker recovery、dispatch replay、callback replay 和人工补偿不得创建新的 submission key，因为它们不是新的外部提交入口。

`job_submission_keys` 指向 `job_aggregates`，不是 `job_aggregates` 反向拥有 submission key。这样 Job 查询不依赖幂等表保留期；如果公开 `JobEnvelope` 需要回显 `client_request_id`，应由 `job_aggregates.client_request_id` 这类 immutable projection 承载，但它不参与幂等冲突判定。

## 目标辅助表

辅助表支持审计、运维、计费或扩展能力，但不是 Job kernel 状态推进所需的最小事实源。首版不应默认创建所有辅助表；只有当对应能力成为合同或运维要求时才落地。

| 表 | 类型 | 落地裁决 | 职责 | 说明 |
|---|---|---|---|---|
| `job_audit_events` | Audit | 首版建议 | 记录 Job、attempt、dispatch、callback 相关状态迁移和重要原因。 | 只追加，不参与状态判断；缺失不能影响 Job 正确性。 |
| `ai_call_ledger_entries` | Billing domain core / Job auxiliary | AI 计费能力必需 | 每次真实 AI provider call、usage、cost estimate、pricing ref、billable status。 | 对 Job kernel 是辅助；对 billing 是核心。 |
| `dispatch_publish_attempts` | Ops / Audit | 延后可选 | 追加保存每次 broker publish 尝试的错误、耗时、节点和响应诊断。 | 不拥有 `status`、`next_attempt_at`、lease 或 retry decision；首版可只保存 `dispatch_outbox.last_error`、`publish_attempts` 和 `next_attempt_at`。 |
| `callback_delivery_attempts` | Ops / Audit | 延后可选 | 追加保存每次 callback HTTP 投递尝试的 status、响应摘要、错误分类和耗时。 | 不拥有 `status`、`next_attempt_at`、lease 或 retry decision；首版可只保存 `callback_outbox.last_error`、`last_http_status` 和 `delivery_attempts`。 |
| `job_runtime_snapshots` | Runtime snapshot | 条件可选 | 冻结 input snapshot 和 runtime snapshot 的引用、hash、resolved model、output target。 | 入参大、敏感、需要复现或 attempt 间参数会变化时再建。 |
| `job_artifact_refs` | Artifact index | 条件可选 | 多产物索引、对象存储引用、hash、content type、artifact role 和 produced attempt。 | 单结果可由 `job_aggregates.result_ref` 承载；不承载状态机语义。 |

`dispatch_publish_attempts` 和 `callback_delivery_attempts` 只能是 append-only 观测明细。`dispatch_outbox` / `callback_outbox` 才拥有 `status`、`next_attempt_at`、`lease_*`、`dead_letter`、`last_error` 和 operator replay 状态。辅助明细表不得反向驱动主 outbox 状态。

## 核心字段建议

### `job_submission_keys`

```text
id
caller_id
key_kind                client_request_id | idempotency_key | workflow_submission_id
key_value
request_fingerprint
job_id
created_at
expires_at
```

设计理由：

- Submit 幂等是入口合同，不是 Job 生命周期状态。
- 该表允许幂等 retention 独立于 Job 软删除和归档策略；Job 被归档后，幂等键仍可在保留期内阻止重复提交。
- `request_fingerprint` 不一致时返回冲突；一致时返回同一 `job_id`。
- `key_kind + key_value` 是 canonical submit key。当前公开合同若使用 `client_request_id`，则写入 `key_kind=client_request_id`；未来若支持 `Idempotency-Key` header，应作为新 `key_kind` 版本化引入。
- 建议唯一约束以 `caller_id + key_kind + key_value` 为入口锁；冲突后读取 `request_fingerprint` 判断返回已有 Job 还是幂等冲突。
- 不使用两个可空字段同时表达 `client_request_id` 和 `idempotency_key`，避免出现互斥约束、优先级和迁移歧义。

### `job_aggregates`

```text
id
caller_id
client_request_id
job_type
public_status           queued | running | succeeded | failed
priority
progress_percent
progress_stage
progress_text
input_ref
input_hash
runtime_ref
result
result_ref
error
active_attempt_id
attempt_count
max_attempts
callback_summary
created_at
queued_at
started_at
finished_at
expires_at
delete_requested_at
deleted_at
updated_at
```

设计理由：

- `client_request_id` 如果保留，只是公开查询回显的 immutable projection；幂等唯一性和冲突判定只看 `job_submission_keys`。
- `public_status` 是对外投影的小状态集合，不暴露 outbox / lease / provider 细节。
- `active_attempt_id` 表示当前有效执行尝试；旧 attempt 的晚到消息不能写回 Job。
- `callback_summary` 是查询投影，不是 Callback 投递事实源。它只能由 callback outbox 投递结果派生更新。
- `result_ref` 和 `input_ref` 允许大 payload 走对象存储，避免主表膨胀。

### `job_execution_attempts`

```text
id
job_id
attempt_no
status                  pending | running | succeeded | failed
worker_id
lease_token
lease_expires_at
heartbeat_at
started_at
finished_at
timeout_seconds
error
failure_phase
retryable
created_at
updated_at
```

设计理由：

- `pending` 表示 attempt 已创建，等待对应 TaskIQ message 被 worker 消费；它不是 publish 状态。
- `running` 必须持有 lease token；进度和终态写回必须校验 active attempt 和 lease token。
- retry 通过创建新 `job_execution_attempts` 行表达，不覆盖旧 attempt 的失败事实。
- attempt 表不保存 `published_at`、`dispatch_attempts`、`next_dispatch_at`、`last_dispatch_error`；这些属于 `dispatch_outbox`。

### `dispatch_outbox`

```text
id
event_id
job_id
attempt_id
task_name
payload
status                  pending | leased | published | retrying | dead_letter
publish_attempts
next_attempt_at
lease_token
lease_expires_at
last_error
created_at
leased_at
published_at
dead_lettered_at
updated_at
```

设计理由：

- `event_id` 必须唯一。建议使用 `job_attempt:{attempt_id}:dispatch` 这类稳定 dedupe key。
- `next_attempt_at` 是下次可发布时间；publisher 用 `FOR UPDATE SKIP LOCKED` claim 到期行。
- `leased` 防止多个 publisher 同时处理同一 dispatch intent；lease 过期后可重试。
- `published` 表示 broker publish 成功，不表示 worker 已执行。
- `dead_letter` 是运维可见终态，不能静默吞掉。

### `callback_outbox`

```text
id
event_id
job_id
event_type
callback_url
payload
signature_version
status                  pending | leased | delivered | retrying | dead_letter | skipped
delivery_attempts
next_attempt_at
lease_token
lease_expires_at
last_http_status
last_response
last_error
created_at
leased_at
delivered_at
dead_lettered_at
updated_at
```

设计理由：

- `event_id` 必须唯一。建议使用 `job:{job_id}:callback:{event_type}`。
- Callback payload 是对外 webhook 合同，需要冻结签名版本和投递内容。
- HTTP ACK、响应摘要和签名语义与 broker publish 不同，因此独立表更清晰。
- Callback 失败只改变 callback outbox 和 `job_aggregates.callback_summary`，不改变 Job 终态。

## 辅助表字段边界

这些表即使落地，也不能拥有核心状态机语义。

### `dispatch_publish_attempts`

```text
id
dispatch_outbox_id
attempt_no
publisher_id
started_at
finished_at
duration_ms
broker_message_id
error_code
error_message
response_summary
created_at
```

设计边界：

- 只能 append-only 记录每次 publish 的观测值。
- 不保存 `status`、`next_attempt_at`、`lease_token`、`dead_lettered_at` 或 retry decision。
- publisher 成败和下一次重试时间仍由 `dispatch_outbox` 主行决定。

### `callback_delivery_attempts`

```text
id
callback_outbox_id
attempt_no
delivery_worker_id
started_at
finished_at
duration_ms
http_status
ack_valid
error_code
response_summary
created_at
```

设计边界：

- 只能 append-only 记录每次 HTTP delivery 的观测值。
- 不保存 `status`、`next_attempt_at`、`lease_token`、`dead_lettered_at` 或 retry decision。
- callback 是否 delivered、retrying 或 dead letter 仍由 `callback_outbox` 主行决定。

### `job_runtime_snapshots`

```text
id
job_id
attempt_id
input_ref
input_hash
runtime_ref
runtime_hash
resolved_model
output_target_ref
created_at
```

设计边界：

- `input_ref/input_hash` 表示调用方提交内容的 canonical input。
- `runtime_ref/runtime_hash` 表示平台解析后的 immutable execution plan。
- 它不保存公开 Job 状态、callback payload、provider usage / cost 或多产物列表。
- 如果首版不建表，`job_aggregates.input_ref/input_hash/runtime_ref` 也必须指向同一类不可变事实。

### `job_artifact_refs`

```text
id
job_id
produced_by_attempt_id
artifact_role           primary_result | secondary_result | checkpoint | attachment
storage_ref
content_type
content_hash
size_bytes
created_at
```

设计边界：

- 只索引对象存储中的大对象、多产物、checkpoint 或附件。
- `job_aggregates.result_ref` 指向主结果或 artifact manifest；`job_artifact_refs` 负责一对多明细。
- 不保存公开 Job 状态、runtime plan、provider usage / cost 或 callback payload。

## Payload、入参、中间数据和结果归属

核心原则：核心表保存状态、引用、hash 和小型公开投影；大 payload、敏感原文、provider 原始响应和多产物不要堆进 Job aggregate 主行。

这里区分两类快照：

- `input snapshot`：调用方提交内容的 canonical input，用于证明“这个请求是什么”。
- `runtime snapshot`：平台解析后的 immutable execution plan，包括 normalized params、resolved model、output target 和 runtime fields。

`job_aggregates` 只保存这些快照的 ref/hash 和必要公开回显；完整快照可以物理存储在 object storage，也可以在复现、审计或合规要求出现后由 `job_runtime_snapshots` 建索引。无论是否首版建表，这两类事实都不能散落到 aggregate、artifact 和 callback payload 三处各存一份。

| 数据 | 默认归属 | 说明 |
|---|---|---|
| Submit 幂等键、请求指纹 | `job_submission_keys` | 只表达提交入口幂等，不表达 Job 执行状态。 |
| Input snapshot ref/hash | `job_aggregates.input_ref` / `input_hash` | 不把完整 prompt、文件内容或敏感 provider params 放进 aggregate。 |
| 小型公开入参回显 | `job_aggregates.input_summary` 或公开 schema 字段 | 只放调用方查询需要的脱敏投影，不参与重放。 |
| Runtime snapshot ref/hash | `job_aggregates.runtime_ref` / `job_runtime_snapshots` | 需要复现、审计或 attempt 间参数冻结时再落表；否则用 `runtime_ref` 指向对象存储。 |
| Broker dispatch payload | `dispatch_outbox.payload` | 只放 `attempt_id`、routing key、dedupe key 等最小消息；不放完整 Job 入参。 |
| Worker lease、heartbeat、retry decision | `job_execution_attempts` | 表达“这一次执行尝试”的运行权，不表达 publish 是否成功。 |
| 小型控制面进度 | `job_aggregates.progress_*` | 只表达公开进度，不保存中间产物。 |
| 可恢复 checkpoint / 中间数据 | object storage ref，必要时由 `job_artifact_refs` 记录 `artifact_role=checkpoint` | 不进入 `job_aggregates`、`dispatch_outbox`、`callback_outbox` 或 `job_runtime_snapshots`。 |
| AI provider 调用明细、usage、cost | `ai_call_ledger_entries` | 计费事实源，不由 Job 成功或失败反推。 |
| 小型最终公开结果 | `job_aggregates.result` | 只保存对外可返回的 canonical result。 |
| 大型最终结果 / 主产物 | `job_aggregates.result_ref` | 指向对象存储或 artifact manifest。 |
| 多产物索引 | `job_artifact_refs` | 多文件、多图片、多格式输出时再建；至少要能表达 `artifact_role` 和 `produced_by_attempt_id`，但不承载状态机语义。 |
| 终态 callback payload | `callback_outbox.payload` | 必须冻结投递内容和签名版本，不能在投递时重新从易变 Job 状态拼装。 |

## 写入流程

### Submit

```text
HTTP request
  -> validate auth / caller / job_type / params
  -> DB transaction
       insert job_submission_keys
       insert job_aggregates(client_request_id=<immutable projection>, public_status=queued)
       insert job_execution_attempts(status=pending, attempt_no=1)
       update job_aggregates.active_attempt_id
       insert dispatch_outbox(
         event_id=job_attempt:{attempt_id}:dispatch,
         job_id,
         attempt_id,
         task_name=jobs.run_attempt,
         payload={attempt_id}
       )
       insert job_audit_events(job.created, attempt.created, dispatch.created)
  -> commit
  -> return JobEnvelope
```

如果 `job_submission_keys` 唯一键冲突，必须在创建新 Job 前处理：

```text
same caller_id + key_kind + key_value + same request_fingerprint
  -> return existing JobEnvelope

same caller_id + key_kind + key_value + different request_fingerprint
  -> return idempotency conflict
```

这里没有“commit 后立即 publish 作为可靠路径”。可以允许 API 进程做 best-effort nudge，但正确性必须完全依赖 dispatch outbox publisher。

### Dispatch publisher

```text
loop
  -> select pending/retrying dispatch_outbox due rows FOR UPDATE SKIP LOCKED
  -> mark leased with lease_expires_at
  -> task.kiq(attempt_id)
  -> success: mark dispatch_outbox published
  -> failure: increment publish_attempts, set next_attempt_at with backoff
  -> attempts exhausted: mark dead_letter
```

重复 broker message 必须由 worker 幂等处理，不靠 publisher 保证 exactly-once。

### Worker execution

```text
TaskIQ message(attempt_id)
  -> DB transaction claim
       load job_execution_attempt + job_aggregate
       require job_aggregates.active_attempt_id = attempt_id
       require job_aggregates.public_status = queued
       require job_execution_attempts.status = pending
       mark attempt running with lease
       mark job running
  -> execute JobExecutor
  -> heartbeat while running
  -> terminal DB transaction
       success:
         mark attempt succeeded
         mark job succeeded
         insert callback_outbox if callback enabled
       retryable failure:
         mark old attempt failed
         insert new attempt pending
         update job.active_attempt_id = new_attempt_id
         mark job queued
         insert dispatch_outbox for new attempt
       terminal failure:
         mark attempt failed
         mark job failed
         insert callback_outbox if callback enabled
```

### Callback delivery

```text
terminal Job transaction
  -> insert callback_outbox(event_type=job.succeeded/job.failed)

callback publisher
  -> claim callback_outbox row
  -> sign and POST callback payload
  -> ACK success: mark delivered and update job.callback_summary
  -> transient failure: retrying with backoff
  -> exhausted: dead_letter and update job.callback_summary
```

Callback 失败不改变 `job_aggregates.public_status`。Job 终态是业务执行结果，Callback 是通知副作用。

## 故障矩阵

| 故障 | 事实源 | 收敛方式 |
|---|---|---|
| API 写入 Job 后进程崩溃 | `dispatch_outbox.pending` 已在同事务存在。 | dispatch publisher 后续发布。 |
| API 响应丢失，客户端重试 | `job_submission_keys`。 | 同 fingerprint 返回同一 `job_id`；不同 fingerprint 返回冲突。 |
| TaskIQ publish 成功但标记 outbox 成功失败 | `dispatch_outbox` 仍可重试；worker 幂等处理重复 message。 | 重复 publish 无害；最终 outbox 标记 `published` 或 `dead_letter`。 |
| 多个 dispatch publisher 并发 | `FOR UPDATE SKIP LOCKED` + dispatch lease。 | 同一 outbox row 只被一个 publisher lease。 |
| Worker 收到重复 attempt message | `active_attempt_id` + attempt status CAS。 | 非 pending active attempt 直接 skipped。 |
| Worker 崩溃 | `job_execution_attempts.lease_expires_at` 到期。 | reconciler 标记 attempt failed；可重试则创建新 attempt + dispatch outbox。 |
| 旧 worker 晚到写终态 | active attempt / lease token 不匹配。 | terminal write 失败，不覆盖新状态。 |
| Callback endpoint 持续失败 | `callback_outbox.delivery_attempts` 和 `dead_letter`。 | Job 保持终态；callback summary 显示 failed。 |
| AI provider 调用成功但 Job 后续失败 | `ai_call_ledger_entries` 已记录调用事实。 | billing 投影显示已发生 usage；不靠 Job 成功决定 usage 是否存在。 |
| outbox dead letter 未处理 | `dispatch_outbox.dead_letter` 或 `callback_outbox.dead_letter` 可查询。 | 运维人工 replay、cancel 或标记解决。 |

## 核心表与辅助表裁决

目标核心表：

```text
job_submission_keys
job_aggregates
job_execution_attempts
dispatch_outbox
callback_outbox
```

目标辅助表：

```text
job_audit_events
ai_call_ledger_entries
dispatch_publish_attempts       optional, only if full publish attempt history is required
callback_delivery_attempts      optional, only if full webhook delivery audit is required
job_runtime_snapshots           optional, only if reproducibility/input-plan requirements exist
job_artifact_refs               optional, only if multi-artifact indexing is required
```

`dispatch_outbox` 和 `callback_outbox` 都是 Transactional Outbox，但必须分表。它们共享模式，不共享表。共享模式包括 `pending -> leased -> terminal`、`next_attempt_at`、lease、backoff、dead letter 和 operator replay；分表保留 broker publish 与 webhook delivery 的不同语义。

## 历史旧结构迁移映射

本节只记录早期实现或旧设计中的命名如何映射到目标模型，不描述当前实现事实。当前实现事实以代码、测试和 [`project-standards-code-facts.md`](project-standards-code-facts.md) 为准。

| 旧信息 | 目标归属 |
|---|---|
| `jobs` 中的 Job 对外状态、结果、callback 摘要 | 归入目标 `job_aggregates`。 |
| `jobs` 中的 client request id、idempotency key、fingerprint | 幂等事实迁入目标 `job_submission_keys`；公开 `client_request_id` 回显可作为 immutable projection 保留在 `job_aggregates`。 |
| `job_attempts` 中的 worker lease、heartbeat、attempt error | 归入目标 `job_execution_attempts`。 |
| `job_attempts` 中的 publish attempts、next dispatch、publish error | 迁入目标 `dispatch_outbox`。 |
| `callback_outbox` 中的 delivery attempts、lease、dead letter | 保留为目标 `callback_outbox`，但从旧 Job 摘要中剥离权威职责。 |
| `job_events` | 归入目标 `job_audit_events`。 |
| `ai_call_logs` | 归入目标 `ai_call_ledger_entries` / billing domain core；不进入 Job kernel core。 |

## 最小可信落地路径

1. 先冻结目标合同：五张核心表的状态集合、唯一键、外键和迁移规则。
2. 冻结 payload 归属：Broker message 只携带 attempt 指针；callback payload 终态冻结；大入参、中间数据和多产物只进 object storage ref 或条件辅助表。
3. 新增 `job_submission_keys` 和 `dispatch_outbox` schema，不先改公开 HTTP 合同。
4. 将新 Job submit 改为同事务写入 `job_submission_keys`、`job_aggregates`、`job_execution_attempts` 和 `dispatch_outbox`。
5. 新增 dispatch publisher，所有 TaskIQ publish 只从 `dispatch_outbox` 发起。
6. Worker 改为消费 `attempt_id` 并 claim `job_execution_attempts`，不再读取 publish 字段。
7. Job terminal 时同事务创建 `callback_outbox`。
8. Callback publisher 只从 `callback_outbox` 投递、重试、dead letter。
9. 将 recovery 分为 dispatch recovery、execution lease recovery、callback delivery recovery、AI ledger recovery。
10. 删除或不再创建旧 publish 字段；`jobs.callback_*` 只保留为摘要投影或由 read model 生成。
11. 更新 current docs、schema tests、migration roundtrip、workflow smoke 和故障注入测试。

## 验收标准

- `POST /jobs` 的 DB transaction 内必须同时出现 submission key、Job aggregate、first attempt 和 dispatch outbox row。
- 相同 caller、`key_kind`、`key_value` 和 request fingerprint 必须返回同一 Job；相同 key 不同 fingerprint 必须返回幂等冲突。
- 任何 TaskIQ publish 都必须由 dispatch outbox publisher 发起，不能由 API 请求路径承担可靠发布责任。
- `dispatch_outbox.payload` 不能携带完整 Job 入参；只能携带执行指针和最小 routing 信息。
- Worker 重复收到同一个 `attempt_id` 时只能有一次 claim 成功。
- Worker 崩溃后，lease 到期 recovery 能创建新 attempt 和新 dispatch outbox row，或终态失败。
- Job 终态和 callback outbox row 必须同事务写入。
- `callback_outbox.payload` 必须冻结终态 callback envelope 和签名版本，投递时不能依赖易变 Job 字段重新拼装。
- Callback 失败耗尽后必须进入 `dead_letter`，且 Job 终态不被改变。
- `job_audit_events` 缺失或延迟不能影响 Job、attempt、dispatch、callback 的正确状态。
- Billing read model 只能从 AI call ledger 聚合，不从 Job aggregate 或 outbox 推导 usage。

## 需要验证

- 当前 `job_type` 是否都能用 `attempt_id` 作为唯一 worker 输入，不需要把完整 Job payload 放进 broker message。
- Callback payload 是否能完全从 terminal Job projection 和 callback outbox payload 构造，避免投递时依赖易变状态。
- 哪些入参、运行参数和结果可以安全内联为小型公开投影；哪些必须进入 object storage ref、runtime snapshot 或 artifact index。
- `dispatch_outbox` 是否需要按 `job_id` 还是 `attempt_id` 定义 ordering key；首版建议以 `attempt_id` 为 dispatch dedupe key，以 `job_id + attempt_no` 辅助查询。
- 是否需要保留完整 publish / delivery attempt 历史；如果运维和合规只需要最后错误，首版不建 attempt 明细表。
- idempotency retention 是否必须长于 Job retention；若必须，`job_submission_keys` 的 retention 不能跟随 Job 软删除。
