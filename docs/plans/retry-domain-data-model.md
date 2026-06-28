# Retry Domain 数据模型重构计划

本文记录 Job kernel retry 语义和数据模型的重构计划。当前已实现事实仍以 [`../current/job-kernel.md`](../current/job-kernel.md) 和 [`../current/workflow-kernel.md`](../current/workflow-kernel.md) 为准；本文只写目标方案和验收标准。

## Current Baseline

- `job_execution_attempts` 是 worker 执行权、lease、heartbeat 和 attempt 状态事实源。
- `dispatch_outbox` 是 Taskiq `jobs.run_attempt(attempt_id)` 发布意图和 publish retry 账本。
- `callback_outbox` 是 root Job 终态 callback 投递账本。
- workflow root orchestration 和 business execution 当前都复用 `job_execution_attempts`，但 attempt 没有显式 `purpose`。
- `job_aggregates.max_attempts`、`attempt_count` 和 `execution_attempts` 当前把不同 retry 语义混在 Job 聚合根上。
- 当前内置 `job_type` 的执行 retry 默认是 `max_attempts=1` 和 `no_platform_retry`，因此业务执行默认不会自动重跑。

## Remaining Gaps

- retry domain 没有被建模：dispatch publish、callback delivery、workflow orchestration 和 business execution 的失败语义混在文档、配置和代码路径里。
- workflow root orchestration retry 和 leaf / model business execution retry 共享 `max_attempts` 语义，容易让开发者误以为 root terminal failed 会触发整单 workflow retry。
- `job_aggregates` 上的 retry 计数字段无法表达不同 `purpose` 的 attempt 次数，也容易和 callback / dispatch attempt 混淆。
- retry policy 没有在 attempt / outbox 创建时固化快照，后续默认值或 `job_type` 元数据变化会降低历史排障可解释性。
- `.env.example` 暴露了 dispatch / callback retry 参数，但这些参数更像 kernel policy，不应作为普通业务部署旋钮随意调。

## Target Mental Model

retry 分成四个 domain。每个 domain 有自己的事实源、默认策略和覆盖边界。

| Domain | 事实源 | 默认策略 | 是否重跑业务 |
|---|---|---|---|
| `dispatch_publish` | `dispatch_outbox` | 默认可靠重试 | 否，只补发同一个 `attempt_id` |
| `callback_delivery` | `callback_outbox` | 默认可靠重试 | 否，只补发 callback |
| `workflow_orchestration` | `job_execution_attempts.purpose="workflow_orchestration"` | 默认可靠重试 | 否，只补齐 child / dispatch 缺口 |
| `business_execution` | `job_execution_attempts.purpose="business_execution"` | 默认不重试 | 是，只有 `job_type` 显式允许才重跑 |

```text
attempt failed
  -> read attempt.purpose
  -> read attempt policy_* columns and retry policy snapshot
  -> decide next attempt for the same job_id and same purpose

dispatch publish failed
  -> read dispatch publish policy snapshot
  -> retry publishing same attempt_id or dead_letter

callback delivery failed
  -> read callback delivery policy snapshot
  -> retry callback HTTP delivery or dead_letter
```

## Target Data Model

### `job_aggregates`

`job_aggregates` 继续保存 Job 聚合状态、root / child lineage、runtime snapshot、result / error、callback URL 配置和当前 active attempt 指针。

必须删除这些字段，不保留兼容语义：

| 字段 | 目标处理 | 原因 |
|---|---|---|
| `max_attempts` | 删除 | retry policy 属于 attempt purpose / outbox domain，不属于 Job 聚合根 |
| `attempt_count` | 删除 | attempt 次数必须按 `job_id + purpose` 从 `job_execution_attempts` 计算 |
| `execution_attempts` | 删除 | 含义和 `attempt_count` 重叠，且不能区分 workflow orchestration 与 business execution |

继续保留：

| 字段 | 目标含义 |
|---|---|
| `active_attempt_id` | 当前仍可被 worker claim / heartbeat / terminal write 的 active attempt |

必须同步删除这些当前执行 CAS / callback 摘要字段，不保留可写摘要语义：

| 字段 | 目标处理 | 替代方式 |
|---|---|---|
| `execution_token` | 删除 | progress/result/terminal write 统一用 `active_attempt_id + job_execution_attempts.lease_token` 做 CAS |
| `execution_generation` | 删除 | stale worker 防护由 attempt lease、active attempt 指针和 terminal write 条件承担 |
| `callback_status` | 删除 | 从 `callback_outbox.status` 投影 |
| `callback_attempts` | 删除 | 从 `callback_outbox.delivery_attempts` 投影 |
| `callback_first_attempt_at` | 删除 | 从 `callback_outbox.first_attempt_at` 投影 |
| `callback_last_attempt_at` | 删除 | 从 `callback_outbox.last_attempt_at` 投影 |
| `callback_next_retry_at` | 删除 | 从 `callback_outbox.next_attempt_at` 投影 |
| `callback_delivered_at` | 删除 | 从 `callback_outbox.delivered_at` 投影 |
| `callback_failed_at` | 删除 | 从 `callback_outbox.dead_lettered_at` 投影 |
| `callback_last_error` | 删除 | 从 `callback_outbox.last_error` 投影 |

Callback URL 和订阅事件属于 root Job 创建输入，继续作为不可变请求配置保留在 `job_aggregates.callback_url` 和 `callback_events`。Job 进入终态时，`callback_outbox` 复制当时的 callback URL、event type 和 payload 作为投递快照；之后 callback 投递状态只看 `callback_outbox`，不再回写 `job_aggregates.callback_*` 摘要字段。

### `job_execution_attempts`

`job_execution_attempts` 继续是 workflow orchestration 和 business execution 的唯一执行权事实源，不新增 `workflow_orchestration_attempts` 或 `job_retry_attempts` 表。

目标字段：

```text
id
job_id
purpose                  workflow_orchestration | business_execution
purpose_attempt_no       per job_id + purpose
status                   pending | running | succeeded | failed
worker_id
lease_token
leased_at
lease_expires_at
heartbeat_at
started_at
finished_at
timeout_seconds
retry_chain_id
previous_attempt_id
created_reason           initial | retry | recovery_retry | manual_replay
policy_max_attempts
policy_retry_delay_seconds
policy_backoff_kind
policy_retryable_error_codes
retry_policy_snapshot
retry_eligible
retry_decision
retry_decision_reason
retry_decided_at
next_attempt_scheduled_at
decision_source
error
error_kind
failure_phase
created_at
updated_at
```

约束和索引：

```text
unique(job_id, purpose, purpose_attempt_no)
unique(previous_attempt_id) where previous_attempt_id is not null
index(job_id, purpose, status)
index(retry_chain_id)
index(status, lease_expires_at)
fk(previous_attempt_id) -> job_execution_attempts.id
```

`retryable` 这种单一布尔值不再保留。目标模型必须拆开两个问题：

| 字段 | 回答的问题 |
|---|---|
| `retry_eligible` | 当前错误按 policy 是否允许 retry |
| `retry_decision` | 系统最终是否真的创建下一次 attempt，或为什么不创建 |

`purpose` 赋值规则：

| 运行形态 | attempt purpose |
|---|---|
| 普通 non-workflow root Job | `business_execution` |
| workflow root Job 初始 attempt | `workflow_orchestration` |
| workflow internal child Job | `business_execution` |

`policy_*` 显式列服务状态机查询和 recovery 决策；`retry_policy_snapshot` 保存完整策略来源、默认值和 backoff 细节，服务审计和排障。attempt 创建后，这些字段不随代码默认值或 `job_type` 元数据变化而变化。示例：

```json
{
  "domain": "workflow_orchestration",
  "max_attempts": 3,
  "retry_delay_seconds": 5,
  "backoff": {"kind": "fixed"},
  "retryable_error_codes": [
    "JOB_STATE_TRANSITION_CONFLICT",
    "DB_TRANSIENT_ERROR",
    "TASKIQ_PUBLISH_DEFERRED"
  ]
}
```

```json
{
  "domain": "business_execution",
  "max_attempts": 1,
  "retry_delay_seconds": null,
  "backoff": {"kind": "none"},
  "retryable_error_codes": []
}
```

`retry_decision` 记录失败后的实际判断结果：

```json
{
  "action": "create_next_attempt",
  "reason": "retryable_error_and_attempts_remaining",
  "next_attempt_id": "..."
}
```

```json
{
  "action": "exhausted",
  "reason": "max_attempts_reached"
}
```

`created_reason` 用于解释 attempt 为什么存在：

| `created_reason` | 含义 |
|---|---|
| `initial` | 初始 root / child attempt |
| `retry` | 前一次同 purpose attempt 失败后按 retry policy 创建 |
| `recovery_retry` | recovery 处理 stale / failed attempt 后，按 retry policy 创建下一次同 purpose attempt |
| `manual_replay` | 未来人工重放能力预留；第一阶段不实现 |

第一阶段如果不实现人工重放，`manual_replay` 只能作为保留枚举写入文档，不能进入可执行路径。

### `dispatch_outbox`

`dispatch_outbox` 保留独立表，不合并进 `job_execution_attempts`。它表达消息发布可靠性，不表达 Job 执行失败。

目标新增 policy snapshot 字段：

```text
max_publish_attempts
orphan_timeout_seconds
publish_retry_delay_seconds
publish_backoff_kind
publish_retry_policy_snapshot
```

保留：

```text
event_id
attempt_id
task_name
status
publish_attempts
next_attempt_at
lease_token
lease_expires_at
last_error
published_at
dead_lettered_at
```

目标约束：

```text
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

`publish_retry_policy_snapshot` 用于记录创建 dispatch 时的默认策略和 backoff 细节；查询和 dead-letter 判断优先使用显式列。

### `callback_outbox`

`callback_outbox` 保留独立表，不合并进 `job_aggregates` 或 `job_execution_attempts`。它表达终态通知投递可靠性，不改变 Job 业务终态。

目标新增 policy snapshot 字段：

```text
max_delivery_attempts
request_timeout_seconds
retry_delay_seconds
delivery_retry_policy_snapshot
```

保留：

```text
event_type
callback_url
payload
status
delivery_attempts
next_attempt_at
lease_token
lease_expires_at
last_http_status
last_response
last_error
delivered_at
dead_lettered_at
```

目标约束：

```text
status in ('pending', 'leased', 'delivered', 'retrying', 'skipped', 'dead_letter')
delivery_attempts >= 0
next_attempt_at is null when status in ('delivered', 'skipped', 'dead_letter')
lease_token is not null when status = 'leased'
lease_expires_at is not null when status = 'leased'
delivered_at is not null iff status = 'delivered'
dead_lettered_at is not null iff status = 'dead_letter'
```

`delivery_retry_policy_snapshot` 用于记录创建 callback outbox 时的默认策略和 backoff 细节；查询和 dead-letter 判断优先使用显式列。

## Retry Policy Ownership

retry policy 不作为普通 `.env` 旋钮暴露。目标配置来源按优先级：

```text
job_type 显式 override
> kernel domain 默认策略
```

默认策略：

| Domain | 默认值 |
|---|---|
| `dispatch_publish.max_publish_attempts` | `12` |
| `dispatch_publish.orphan_timeout_seconds` | `300` |
| `dispatch_publish.retry_delay_seconds` | `60` |
| `dispatch_publish.backoff_kind` | `fixed` |
| `callback_delivery.max_delivery_attempts` | `12` |
| `callback_delivery.request_timeout_seconds` | `5` |
| `callback_delivery.retry_delay_seconds` | `300` |
| `workflow_orchestration.max_attempts` | `3` |
| `workflow_orchestration.retry_delay_seconds` | `5` |
| `business_execution.max_attempts` | `1` |

`job_type` 覆盖示例：

```python
retry_policy = JobRetryPolicy(
    workflow_orchestration=WorkflowOrchestrationRetryPolicy.default_reliable(),
    business_execution=BusinessExecutionRetryPolicy.disabled(),
)
```

```python
retry_policy = JobRetryPolicy(
    business_execution=BusinessExecutionRetryPolicy(
        max_attempts=2,
        retryable_error_codes={"JOB_TIMEOUT"},
        retry_delay_seconds=30,
    ),
)
```

禁止重新引入这些全局旋钮：

```text
JOB_MAX_EXECUTION_ATTEMPTS
MODEL_CALL_MAX_RETRIES
TASKIQ_MAX_RETRIES
TASKIQ_RETRY_DELAY
```

实现时应从 `.env.example` 和 `Settings` 中移除 callback / dispatch retry 普通旋钮，改为代码内 kernel defaults 和 `job_type` override。若未来确实需要运行时动态策略，必须先设计独立的 `retry_policy_definitions` 能力和审计，不在本计划第一阶段实现。

## Planned Work

1. 新增 retry policy 类型：`JobRetryPolicy`、`WorkflowOrchestrationRetryPolicy`、`BusinessExecutionRetryPolicy`、`DispatchPublishRetryPolicy` 和 `CallbackDeliveryRetryPolicy`。
2. 将 `JobExecutor.max_attempts` 和 `platform_retry_policy` 替换为 domain retry policy；registry 校验改为校验 policy schema、默认值和禁止项。
3. 修改 `job_execution_attempts` 表：新增 `purpose`、`purpose_attempt_no`、`retry_chain_id`、`previous_attempt_id`、`created_reason`、`policy_*` 显式列、`retry_policy_snapshot`、`retry_eligible`、`retry_decision`、`retry_decision_reason`、`retry_decided_at`、`next_attempt_scheduled_at` 和 `decision_source`；把唯一约束改为 `(job_id, purpose, purpose_attempt_no)`，并对非空 `previous_attempt_id` 加唯一约束。
4. 修改 attempt 创建路径：普通 Job 创建 `business_execution` attempt；workflow root 创建 `workflow_orchestration` attempt；workflow child 创建 `business_execution` attempt。
5. 从 `job_aggregates` 删除 `max_attempts`、`attempt_count` 和 `execution_attempts`；相关逻辑改为查询 `job_execution_attempts`。
6. 修改 `mark_attempt_failed`：按 `attempt.purpose` 和 `policy_*` 显式列判断是否创建同 purpose 的下一次 attempt，并写入 `retry_decision`；`retry_policy_snapshot` 只作为完整审计，不作为 SQL 状态机唯一输入。
7. 修改 workflow root orchestration 成功路径：root orchestration attempt succeeded 后仍清空 `active_attempt_id`，等待 child terminal projection。
8. 修改 workflow child exhausted 路径：child business execution retry 耗尽后，才由 reconciler 投影 root terminal failed / partial success。
9. 修改 `dispatch_outbox` 和 `callback_outbox`：写入 policy snapshot 和显式 max/timeout/retry delay/backoff 字段；发布和投递逻辑读取 outbox 自身策略。
10. 从 `.env.example` 和 `Settings` 移除 callback / dispatch retry 普通 env 旋钮；保留非 retry 的基础运行配置。
11. 删除 `job_aggregates.execution_token`、`execution_generation` 和 `callback_*` 摘要字段；相关公开响应改为直接从 `callback_outbox` 投影 callback 状态。
12. 更新 `docs/current/job-kernel.md`、`docs/current/workflow-kernel.md`、`docs/api/extension-guide.md` 和相关测试。

## Acceptance

- `job_aggregates` 不再包含 `max_attempts`、`attempt_count` 或 `execution_attempts` 字段。
- `job_aggregates` 不再包含 `execution_token`、`execution_generation` 或 `callback_*` 摘要字段；执行 CAS 只依赖 active attempt + lease，callback 查询只依赖 `callback_outbox`。
- `job_execution_attempts` 明确区分 `purpose="workflow_orchestration"` 与 `purpose="business_execution"`。
- 任一 attempt 的 purpose、purpose 内序号、前驱链路、policy snapshot、显式 policy 列和 retry decision 都能直接从数据库还原，不依赖可变的 handler 或 env。
- 普通 non-workflow Job 只创建 `business_execution` attempt。
- workflow root Job 初始只创建 `workflow_orchestration` attempt；orchestration retry 不创建重复 child，也不重跑已创建 child。
- workflow child Job 只创建 `business_execution` attempt；child retry 耗尽后才允许 root terminal projection。
- `business_execution` 默认 `max_attempts=1`，任何重跑业务的 job_type 都必须显式声明 retry policy、retryable error codes 和测试。
- `dispatch_outbox` 的 retry 只重新发布同一个 `attempt_id`，不会创建新的 Job attempt。
- `callback_outbox` 的 retry 只重新投递 callback，不能改变 Job 终态。
- retry policy 在 attempt / outbox 创建时固化快照；后续默认策略变更不改变历史 Job 的 retry 判断。
- root orchestration failure、child business failure 和 root terminal projection failure 在持久化数据中可直接区分，不依赖日志或文档解释。
- recovery 重跑、worker 重复消息和重复 finalize 不会重复创建 next attempt 或重复创建 outbox。
- `.env.example` 不暴露业务执行 retry、orchestration retry、dispatch publish retry 或 callback delivery retry 的普通全局旋钮。
- schema contract、migration roundtrip、registry contract、workflow smoke 和 retry 分支测试全部通过；retry 分支至少覆盖正常成功、可重试失败后创建 next attempt、不可重试或耗尽后终态失败、crash 后 recovery 补偿。

## Non-goals

- 不实现整单 workflow 自动重试。
- 不新增 `workflow_orchestration_attempts`、`job_retry_attempts` 或通用 `retry_events` 核心表。
- 不把 dispatch publish retry 或 callback delivery retry 合并进 `job_execution_attempts`。
- 不开放运行时动态 retry policy 管理后台。
- 不让 `.env` 一键打开所有业务执行 retry。
