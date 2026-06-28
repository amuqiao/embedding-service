# Job Kernel 数据模型收口计划

本文记录 Job kernel 表职责、字段职责和事实源边界的上线前重构计划。当前已实现事实仍以 [`../current/job-kernel.md`](../current/job-kernel.md) 和 [`../current/workflow-kernel.md`](../current/workflow-kernel.md) 为准；本文只写目标数据模型、迁移方向和验收标准。

Retry domain 的字段改造以 [`retry-domain-data-model.md`](retry-domain-data-model.md) 为专项计划。本文不重复设计 retry policy，而是定义 Job kernel 数据模型的总边界，确保 retry 改造时不会继续把执行、投递、编排、审计和公开查询职责混在同一张表里。

## Current Baseline

当前 Job kernel 主要表：

| 表 | 当前主要职责 |
|---|---|
| `job_aggregates` | Job 聚合状态、root / child lineage、提交输入、运行时引用、进度、结果、错误、active attempt 指针、执行计数、callback 摘要、清理状态 |
| `job_submission_keys` | 外部提交幂等键和 request fingerprint |
| `job_execution_attempts` | worker 执行尝试、lease、heartbeat、attempt 结果和失败原因 |
| `dispatch_outbox` | DB -> Taskiq 发布意图、publish retry 和 dead-letter |
| `callback_outbox` | root Job 终态 callback payload、delivery retry 和 dead-letter |
| `job_audit_events` | Job / attempt / dispatch / callback 状态变化审计时间线 |

主要问题不是表数量，而是若干字段跨表表达同一个事实：

- `job_aggregates.max_attempts`、`attempt_count`、`execution_attempts` 与 `job_execution_attempts` 的 attempt 事实重叠。
- `job_aggregates.execution_token`、`execution_generation` 与 `job_execution_attempts.lease_token` / `active_attempt_id` 都参与执行 CAS。
- `job_aggregates.callback_*` 摘要字段与 `callback_outbox` delivery 状态重复。
- `job_aggregates.timeout_seconds` 与 `job_execution_attempts.timeout_seconds` 重复表达执行超时。
- `root_job_id`、`parent_job_id`、`is_internal` 同时表达 root / child / visibility，且 public root 允许 `root_job_id = null` 或 `root_job_id = id` 两种语义。
- `job_params` / `job_params_ref`、`result` / `result_ref`、`canonical_result` / `canonical_result_ref` 缺少明确互斥或分工约束。
- `dispatch_outbox` 已有 publish retry 字段，但 publish failure path 和 max publish attempts 需要在实现阶段闭合；未接入的 retry 字段不能作为“已可靠”的事实源。
- `callback_outbox` 缺少与 dispatch / attempt 同级别的状态枚举和计数约束，容易让 callback delivery 状态只能靠代码约定。

## Target Principles

目标数据模型遵守这些原则：

1. 每个业务事实只能有一个 authoritative owner。
2. `job_aggregates` 只保存 Job 聚合对外可见的当前状态和不可变提交输入，不保存 attempt retry、dispatch retry 或 callback delivery 账本。
3. worker 执行权、lease、heartbeat、attempt retry 决策只属于 `job_execution_attempts`。
4. publish retry 只属于 `dispatch_outbox`；callback delivery retry 只属于 `callback_outbox`。
5. `job_audit_events` 永远是审计 projection，不能驱动状态机。
6. 不保留未接入完整读写合同的顶层 ref 字段；需要对象存储引用时，把 ref 放进明确 schema 的 payload 内部。
7. root / child lineage 只能有一种表达方式，不保留 self-or-null、derived-or-explicit 的双语义。

## Target Table Ownership

### `job_aggregates`

`job_aggregates` 是 Job 聚合根当前状态事实源。它保留这些职责：

| 职责 | 目标说明 |
|---|---|
| Job 身份 | `id`、`caller_id`、`client_request_id`、`job_type` |
| root / child membership | public root 与 workflow child 的唯一归属关系 |
| caller visibility | 外部只能按 public root 查询；internal child 只供内核和 workflow 查询 |
| 聚合状态 | `status`、`progress_*`、`queued_at`、`started_at`、`finished_at`、`error` |
| 提交输入快照 | `job_params_ref`、`job_params_hash`、metadata、runtime snapshot |
| 结果投影 | public result 与 canonical result JSON；大文件引用只出现在 result 内部 artifact ref |
| callback 配置 | root Job 创建时的 immutable `callback_url` 和 `callback_events` |
| active execution pointer | `active_attempt_id` 指向当前仍可写入 Job 状态的 attempt |
| retention / deletion | `expires_at`、`delete_requested_at`、`deleted_at`、`deleted_reason` |

`client_request_id` 是 root Job 的外部回显输入，不是幂等账本事实源。提交幂等是否命中、是否同一请求、是否过期，以 `job_submission_keys` 为准。

`job_aggregates` 明确不保存这些职责：

| 不属于 `job_aggregates` 的职责 | 目标 owner |
|---|---|
| attempt 次数、retry policy、retry decision | `job_execution_attempts` |
| worker lease、heartbeat、执行 CAS token | `job_execution_attempts` |
| Taskiq publish attempt、publish lease、publish dead-letter | `dispatch_outbox` |
| callback delivery 状态、投递次数、HTTP 响应、dead-letter | `callback_outbox` |
| AI usage / cost 明细 | AI call ledger / billing 聚合 |
| workflow dependency DAG | `runtime_ref.workflow_plan` 或后续独立 workflow plan 表 |
| 状态变化历史 | `job_audit_events` |

### `job_submission_keys`

`job_submission_keys` 只负责提交幂等。

目标字段：

```text
id
caller_id
key_kind
key_value
request_fingerprint
job_id
created_at
expires_at
```

约束：

```text
unique(caller_id, key_kind, key_value)
index(job_id)
index(expires_at)
```

它不能保存 Job 状态、attempt 状态、callback 状态或执行参数。`request_fingerprint` 只用于判断同一个幂等键是否对应同一份提交输入。

`job_aggregates.client_request_id` 只用于公开查询和响应回显；不得用它替代 `job_submission_keys` 做幂等判断。

### `job_execution_attempts`

`job_execution_attempts` 是 worker 执行权事实源。它负责：

- attempt 身份和 purpose。
- worker claim / lease / heartbeat。
- attempt timeout。
- attempt terminal 结果和失败原因。
- 同一 Job、同一 purpose 下的 retry 链路和 retry decision。

目标字段和 retry policy 细节由 [`retry-domain-data-model.md`](retry-domain-data-model.md) 进一步定义。本文只规定：`job_execution_attempts` 是执行 attempt 的唯一 owner，`job_aggregates` 不再保存 attempt counter、attempt retry policy、worker lease 或 worker CAS token。

### `dispatch_outbox`

`dispatch_outbox` 是“把某个 pending attempt 发布给 Taskiq”的可靠投递账本。它负责：

- publish intent。
- publish lease。
- publish attempt counter。
- publish retry / dead-letter。
- 发布同一个 `attempt_id` 的幂等约束。

目标上 `dispatch_outbox` 只通过 `attempt_id` 关联 execution attempt，并删除 `job_id` 字段，避免出现 `dispatch_outbox.job_id` 与 `job_execution_attempts.job_id` 不一致。需要按 Job 查询 dispatch 时，通过 `attempt_id -> job_execution_attempts.job_id` join；查询优化只能新增只读 projection 或物化视图，不能把第二份 Job owner 放回 outbox 事实表。

`dispatch_outbox.status = published` 只能表示“消息已成功交给 broker”。后续 orphan recovery、worker 未领取、worker 崩溃等问题，必须通过 attempt lease / recovery 机制判断，不能把 `published + next_attempt_at` 混成新的业务 retry 语义。

目标字段和约束：

```text
id
event_id
attempt_id
task_name
payload
status
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

status in ('pending', 'leased', 'published', 'retrying', 'dead_letter')
publish_attempts >= 0
unique(event_id)
unique(attempt_id, task_name)
next_attempt_at is null when status = 'dead_letter'
lease_token is not null when status = 'leased'
lease_expires_at is not null when status = 'leased'
published_at is not null when status = 'published'
dead_lettered_at is not null when status = 'dead_letter'
```

### `callback_outbox`

`callback_outbox` 是 root Job 终态 callback delivery 账本。它负责：

- 终态时复制出的 `event_type`、`callback_url` 和 `payload` 快照。
- delivery lease。
- delivery attempt counter。
- HTTP 响应、投递错误、retry / dead-letter。

`callback_outbox` 不改变 Job 业务终态。Job 到达 `succeeded` 或 `failed` 后，callback delivery 只能改变 callback outbox 自身状态。

目标约束：

```text
status in ('pending', 'leased', 'delivered', 'retrying', 'skipped', 'dead_letter')
delivery_attempts >= 0
delivered_at is not null iff status = 'delivered'
dead_lettered_at is not null iff status = 'dead_letter'
next_attempt_at is null when status in ('delivered', 'skipped', 'dead_letter')
lease_token is not null when status = 'leased'
lease_expires_at is not null when status = 'leased'
```

### `job_audit_events`

`job_audit_events` 是非权威审计时间线。它可以保存：

- Job 状态变化。
- attempt claim / heartbeat / terminal 事件。
- dispatch publish 事件。
- callback delivery 事件。
- recovery / reconciler 动作。

它不能作为状态机输入，不能作为 retry decision 输入，也不能作为公开 Job 查询的唯一事实源。

## Target `job_aggregates` Field Decisions

### 彻底删除

| 字段 | 目标处理 | 替代 owner |
|---|---|---|
| `max_attempts` | 删除 | `job_execution_attempts.policy_*` / retry policy snapshot |
| `attempt_count` | 删除 | 按 `job_id + purpose` 查询 `job_execution_attempts` |
| `execution_attempts` | 删除 | `job_execution_attempts` |
| `execution_token` | 删除 | `active_attempt_id + job_execution_attempts.lease_token` |
| `execution_generation` | 删除 | attempt lease、active attempt 指针和 terminal write 条件 |
| `last_execution_at` | 删除 | 从当前或最近 `job_execution_attempts.started_at` 投影 |
| `last_heartbeat_at` | 删除 | `job_execution_attempts.heartbeat_at` |
| `timeout_seconds` | 删除 | `job_execution_attempts.timeout_seconds` 和 attempt policy snapshot |
| `callback_status` | 删除 | `callback_outbox.status` 投影 |
| `callback_attempts` | 删除 | `callback_outbox.delivery_attempts` |
| `callback_first_attempt_at` | 删除 | `callback_outbox.first_attempt_at` |
| `callback_last_attempt_at` | 删除 | `callback_outbox.last_attempt_at` |
| `callback_next_retry_at` | 删除 | `callback_outbox.next_attempt_at` |
| `callback_delivered_at` | 删除 | `callback_outbox.delivered_at` |
| `callback_failed_at` | 删除 | `callback_outbox.dead_lettered_at` |
| `callback_last_error` | 删除 | `callback_outbox.last_error` |
| `parent_job_id` | 删除 | workflow dependency 只存在于 workflow plan；child membership 用 `root_job_id` |
| `is_internal` | 删除 | 由 `root_job_id IS NOT NULL` 推导 child / internal |
| `result_ref` | 删除 | public result 直接保存在 `result`；大文件通过 `result.artifacts[]` 内的对象存储引用表达 |
| `canonical_result_ref` | 删除 | canonical result 直接保存在 `canonical_result`；大文件通过 canonical result 内部 artifact ref 表达 |

如果未来外部 API 需要表达“提交时请求的超时”，新增 `requested_timeout_seconds` 这类不可变提交输入字段；不能复用 `timeout_seconds` 作为 active attempt timeout。

### 保留并加约束

| 字段 | 目标语义 | 约束 |
|---|---|---|
| `id` | Job 主键 | 不变 |
| `root_job_id` | child 所属 public root；public root 固定为 `NULL` | public root 必须 `root_job_id IS NULL`；child 必须 `root_job_id IS NOT NULL` |
| `workflow_node_key` | root 内 child node 幂等身份 | child 必填；`unique(root_job_id, workflow_node_key)` |
| `caller_id` | 调用方隔离字段 | child 继承 root caller；不能作为 child 对外查询授权入口 |
| `client_request_id` | 外部 root 提交请求 ID 的公开回显 / 查询标识 | 只允许 public root 非空；不是幂等事实源 |
| `job_type` | 当前 Job 的 executor 类型 | root orchestration job_type 与 child business job_type 分开表达 |
| `status` | 当前 Job 聚合状态 | 只允许 `queued/running/succeeded/failed` |
| `progress_percent/text/stage` | 对外进度投影 | worker / reconciler 只能通过 active attempt CAS 或 root finalize 写入 |
| `queued_at/started_at/finished_at` | Job 聚合生命周期 | 不表达具体 attempt 的 heartbeat 或 retry 时间 |
| `active_attempt_id` | 当前可写入该 Job 的 active attempt | nullable；非空时必须指向同一 `job_id` 的 attempt；terminal Job 必须为 null；running workflow root 等待 child 时可以为 null |
| `callback_url/callback_events` | public root 创建时 callback 配置 | 只允许 public root 保存；创建后不可变 |
| `created_at/updated_at/expires_at/delete_*` | 创建、更新、清理事实 | 不与 callback delivery 是否完成混用 |

### 改名或收紧写入规则

| 当前字段 | 目标字段 / 规则 | 原因 |
|---|---|---|
| `job_params` | 删除 | 所有 executor 统一通过 runtime helper 读取 `job_params_ref + job_params_hash` |
| `job_params_ref` | 保留为不可变对象存储引用 | Job params 的唯一 payload owner |
| `job_params_hash` | 保留且必填 | 幂等 fingerprint 和 runtime snapshot 校验需要稳定 hash |
| `runtime_ref` | 保留为不可变 runtime snapshot 引用 | 保存 workflow plan、runtime fields、对象存储路径等运行输入快照 |
| `result` | public result JSONB | 公开 `job_result` 的唯一 owner；大文件只通过 `artifacts[]` 内部 ref 表达 |
| `canonical_result` | canonical result JSONB | 内部 canonical result 的唯一 owner；大文件只通过内部 artifact ref 表达 |

目标约束：

```text
root_job_id is null                      => public root
root_job_id is not null                  => internal child
workflow_node_key is null                => public root
workflow_node_key is not null            => internal child
unique(root_job_id, workflow_node_key) where workflow_node_key is not null

job_params_hash is not null
job_params_ref is not null

progress_percent between 0 and 100
status in ('queued', 'running', 'succeeded', 'failed')
priority in ('low', 'normal')
finished_at is not null when status in ('succeeded', 'failed')
finished_at is null when status in ('queued', 'running')

callback_url is null when root_job_id is not null
callback_events is null when root_job_id is not null

active_attempt_id is null when status in ('succeeded', 'failed')
active_attempt_id belongs to the same job_id
```

`active_attempt_id belongs to the same job_id` 不能只靠应用层约定。目标实现应使用复合外键、触发器或等价数据库约束表达；如果数据库约束不可行，repository 写入路径必须有集中断言和覆盖测试。

`root_job_id IS NULL` 是 public root 的唯一表达。目标实现不再允许 public root 写 `root_job_id = id`，也不再用 `is_internal` 同时表达 visibility。

## Workflow Lineage Target

目标 lineage：

```text
public root Job R
  id = R
  root_job_id = null
  workflow_node_key = null

internal child Job C1
  id = C1
  root_job_id = R
  workflow_node_key = "node-a"

internal child Job C2
  id = C2
  root_job_id = R
  workflow_node_key = "node-b"
```

Workflow dependency 不进入 `parent_job_id`：

```text
workflow_plan.nodes:
  node-a depends_on []
  node-b depends_on ["node-a"]

job_aggregates:
  C1.root_job_id = R
  C2.root_job_id = R
```

`root_job_id` 只回答“这个 child 属于哪个 root”。`depends_on` 只回答“这个 node 什么时候可以运行”。二者不能合并，也不能用 `parent_job_id` 伪装 DAG 边。

## Planned Work

1. 新增 schema ownership 文档和迁移清单，先冻结目标字段列表、字段 owner 和不变量。
2. 调整 `job_aggregates` lineage：删除 `parent_job_id` 和 `is_internal`；统一 public root 为 `root_job_id IS NULL`，child 为 `root_job_id IS NOT NULL`。
3. 调整公开查询、workflow child 查询和 recovery 查询：外部资源查询只查 `root_job_id IS NULL`；child 查询只查 `root_job_id = :root_id`。
4. 执行 [`retry-domain-data-model.md`](retry-domain-data-model.md) 中的 attempt purpose、retry policy snapshot 和 retry 字段迁移；同时从 `job_aggregates` 删除 attempt counter、执行 CAS 和 callback 摘要字段。
5. 删除 `job_aggregates.timeout_seconds`；attempt 创建时把 timeout 固化到 `job_execution_attempts.timeout_seconds` 和 policy snapshot。
6. 收紧 job params runtime 模型：删除 `job_params`；所有 executor 改为通过 runtime helper 读取 `job_params_ref + job_params_hash`；runtime snapshot 创建后不可变。
7. 收紧 result 模型：删除顶层 `result_ref` 和 `canonical_result_ref`；public result 与 canonical result 都保存 JSONB，外部大文件只通过 result 内部 artifact ref 表达。
8. 调整 `dispatch_outbox` 与 attempt 的关系：删除 `dispatch_outbox.job_id`，按 Job 查询时通过 attempt join。
9. 闭合 dispatch publish retry path：publish failure 必须写入 `dispatch_outbox` 自身的 attempt counter、retry / dead-letter 状态和 policy snapshot；不能只存在配置或未调用方法。
10. 调整 callback 查询和过期清理：公开响应与 cleanup eligibility 从 `callback_outbox` 投影 callback 状态，不再读取 `job_aggregates.callback_*`。
11. 为 `dispatch_outbox` 和 `callback_outbox` 增加 status 枚举、attempt 非负、lease、terminal timestamp 和 next retry 约束。
12. 更新 `docs/current/job-kernel.md`、`docs/current/workflow-kernel.md`、`docs/api/service-contract.md`、schema、migration roundtrip、workflow smoke 和 repository tests。

## Acceptance

- `job_aggregates` 不再包含 attempt counter、retry policy、worker lease/CAS、attempt heartbeat、dispatch retry 或 callback delivery 摘要字段。
- public root 和 internal child 只有一种建模方式：root `root_job_id IS NULL`，child `root_job_id IS NOT NULL`；不再存在 `is_internal` 或 `parent_job_id`。
- `workflow_node_key` 是 child 在 root 内的幂等身份；workflow dependency 只在 workflow plan 表达。
- 外部 caller 查询不可能返回 child Job；child 查询必须显式通过 root scope。
- `job_submission_keys` 是提交幂等的唯一事实源；`job_aggregates.client_request_id` 只作为 root Job 的公开回显输入。
- 非空 `active_attempt_id` 必须指向同一 `job_id` 的 attempt；terminal Job 必须清空 `active_attempt_id`。
- `job_execution_attempts` 是 worker execution attempt 的唯一事实源；`dispatch_outbox` 和 `callback_outbox` 不创建或改变 business execution attempt。
- `dispatch_outbox` publish retry path 闭合；publish failure 会更新 outbox 自身的 retry / dead-letter 字段，不依赖 Job aggregate 字段。
- `dispatch_outbox` 不再包含 `job_id`；保留 `unique(event_id)`、`unique(attempt_id, task_name)`、status 枚举、publish attempt 非负、lease 和 terminal timestamp 约束。
- `callback_outbox` 是 callback delivery 的唯一事实源；callback retry 不改变 Job 终态。
- `callback_outbox` 有 status 枚举、delivery attempt 非负、lease、terminal timestamp 和 next retry 约束；`delivered` / `dead_letter` 状态必须有对应终态时间戳。
- `job_params_hash` 和 `job_params_ref` 对每个 Job 必填；所有 executor 通过 runtime helper 读取参数，不再直接读取 `job_aggregates.job_params`。
- `result_ref` 和 `canonical_result_ref` 不再存在；public result 和 canonical result 的大文件引用只出现在 result JSON 内部 artifact ref。
- `job_audit_events` 只作为审计时间线使用；核心状态机查询不依赖 audit event 推导当前状态。
- migration roundtrip、schema contract、repository tests、workflow smoke、callback delivery tests 和 retry branch tests 全部通过。

## Non-goals

- 不在本计划中实现新的 workflow 引擎或独立 workflow plan 表。
- 不把普通 non-workflow Job 强制改造成 workflow 包装模型。
- 不把 `dispatch_outbox`、`callback_outbox` 或 `job_audit_events` 合并成通用事件表。
- 不新增运行时动态 schema / retry policy 管理后台。
- 不为了兼容旧字段保留双写、shadow column 或 legacy semantic。
