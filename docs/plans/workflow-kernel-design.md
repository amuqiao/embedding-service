# Workflow Kernel 设计计划

本文定义未来在当前 Job kernel 之上增加最小 durable workflow kernel 的目标设计。它是计划文档，不描述当前已实现能力；当前事实仍以 `docs/current/job-kernel.md` 和 `docs/api/service-contract.md` 为准。

## Current Baseline

- 当前服务已经有单 Job 可靠执行内核：
  - `job_aggregates` 保存对外 Job 状态、进度、结果、错误和 callback 汇总状态。
  - `job_execution_attempts` 保存单次执行尝试、lease、heartbeat、worker 和 retry 事实。
  - `dispatch_outbox` 在数据库事务后可靠发布 Taskiq 消息。
  - `callback_outbox` 保存终态 callback 的投递账本。
  - `job_audit_events` 只做排障时间线，不驱动主流程。
- 当前公开 Job 状态只有 `queued`、`running`、`succeeded`、`failed`。
- 当前 `job_progress` 包含 `stage`、`percent`、`message`；外部合同还没有收敛为纯 `percent`。
- 当前 `ai_call_ledger_entries` 是 provider usage / cost 的事实源；Job 和 callback 不能成为 cost truth source。
- 当前 `POST /jobs` 表达的是一个 root Job，一次 active attempt，一条主要执行线。

## Requirement Translation

### 表面需求

`poster-title-image` 这类新业务需要一个 Job 生成多张图片，多张图片之间可能独立执行、独立失败、独立重试，并在最后汇总结果和总费用。

### 真实需求

服务需要一个可复用的内部编排层，让后续 LLM Job 能用同一套规范表达串行、并行、扇出、扇入、批量展开和分片执行，同时继续继承当前 Job kernel 的可靠性：

```text
Process Manager
+ Explicit State Machine
+ DAG / Fork-Join
+ Transactional Outbox
+ Idempotent Consumer
+ Lease / Heartbeat
+ Reconciler
```

### 非目标

- 不为 `poster-title-image` 写业务特例。
- 不复制 Celery Canvas 的 API 或运行时实现。
- 不允许外部调用方提交任意 DAG。
- 不把 workflow 内部节点状态直接升级成公共 API 合同。
- 不让 `job.cost`、callback payload 或结果 summary 成为 usage / cost 事实源。
- 不在第一版引入 Temporal、Step Functions 或其它新基础设施。

## Design Position

推荐方案是在当前 Job kernel 上方增加内部 `Workflow Kernel`：

```text
Root Job
  -> Workflow Instance
      -> Workflow Node
          -> Child Job for leaf task
              -> JobAttempt
              -> DispatchOutbox
              -> Taskiq worker
```

Root Job 仍然是外部查询、callback 和幂等提交的入口。Workflow 只负责内部编排，不能绕过 Job kernel 自己发任务、自己重试或自己写 provider cost。

本文的 workflow 设计是新增内部编排内核范围，不推翻 `docs/plans/ai-capability-enhancement.md` 中 AI gateway、usage/pricing、Prompt 和 model catalog 的边界。AI capability plan 仍然负责模型调用能力增强；workflow kernel 只在多步骤、多节点 Job 成为基础需求时负责可靠编排。

## Architecture Principles

### Root Job 是对外聚合根

- 外部调用方只看到 root Job 的 `job_id`、`job_status`、`job_progress`、`job_result`、`job_error`、callback 和 billing 查询。
- Workflow 内部节点不直接暴露为外部 Job 查询资源。
- Internal child Job 默认不应通过公共 `GET /jobs/{job_id}` 查询暴露给调用方；公共查询入口只接受 root Job，除非未来单独发布内部/运维查询合同。
- Root Job 的终态必须由 workflow instance 的终态投影而来。
- 外部 callback 只由 root Job 发送一次终态事件；child Job 不触发调用方 callback。

### Workflow 是 Process Manager

Workflow kernel 负责推进流程，不直接执行业务模型调用：

- 根据 frozen workflow plan 找到 ready nodes。
- 为 leaf task node 创建 child Job。
- 在 child Job 终态后幂等推进对应 node。
- 在 join 条件满足后推进 barrier node。
- 根据 failure policy 决定 root Job 最终 outcome。
- 通过 reconciler 修复 stuck、orphaned 和 missed-event 状态。

Workflow instance 需要自己的 owner / lease。不能用 root Job 的 `JobAttempt` 覆盖整条 DAG 生命周期，因为 fan-out / fan-in 过程中没有一个长期 worker 应该持有整条 workflow 的执行租约。Workflow lease 只保护 process manager 的短事务推进，例如调度 ready nodes、应用 child terminal、执行 join 和投影 root terminal。

Workflow orchestrator wakeup 不能复用现有 `dispatch_outbox`，因为当前 `dispatch_outbox` 的边界是发布某个 Job attempt 的 Taskiq 消息。Workflow 需要单独的 `workflow_wakeup_outbox` 或等价 due-scan 机制来唤醒 process manager；child Job 执行消息仍通过现有 `dispatch_outbox` 发布。

### Leaf Node 复用现有 Job Kernel

第一版 leaf task node 不新增独立 `node_attempt` 和 `node_outbox`。它应创建 child Job，并复用现有：

- `job_execution_attempts`
- `dispatch_outbox`
- worker lease / heartbeat
- attempt terminal token 校验
- Job recovery
- AI call ledger 写入路径

这样避免出现两套执行事实源。只有当未来出现“节点不是 Job，但也需要独立 worker lease / heartbeat / retry”的明确压力后，才评估 `workflow_node_attempts`。

同一 leaf node 在任何重复调度、重复消息或 reconciler 重跑下最多只能绑定一个 child Job。这个规则必须由数据库唯一约束或等价幂等约束保证，不能只依赖应用层判断。

### Canvas 原语是 Planner Macro

`chain`、`group`、`chord`、`map`、`starmap`、`chunks` 不应各自成为一套 runtime 机制。它们统一编译为 node 和 dependency：

| 原语 | 编译结果 |
|---|---|
| `chain` | `B` depends on `A` |
| `group` | 多个没有相互依赖的 ready nodes |
| `chord` | `group` + join / barrier node |
| `map` | planner 按输入数组生成同构 nodes |
| `starmap` | `map` 的参数展开形式 |
| `chunks` | planner 按 chunk size 生成分片 nodes |

Runtime 只理解：

```text
node
node dependency
node status
join / barrier rule
ready-node scheduling
terminal child-job event applying
workflow wakeup outbox
reconciliation
```

### Workflow Plan 必须 Frozen

提交 Job 后，workflow plan 必须落库为 frozen plan。worker 和 reconciler 不能依赖运行时内存里的 Python 对象来恢复流程。

Frozen plan 至少要保证：

- `workflow_type` 和 `workflow_version` 固定。
- `node_key` 在同一个 workflow 内唯一。
- dependency DAG 无环。
- 每个 node 有明确 kind、输入引用、输出引用、timeout、retry policy 和 cost scope。
- fan-out 数量和 chunk size 已经通过配置或能力规则限制。
- 每个 leaf node 可生成稳定幂等键。

## Planned Data Model

以下是目标模型列表，字段为设计边界，不代表当前数据库已经存在。

### `workflow_instances`

Workflow root state 和 frozen plan 的实例记录。

| 字段 | 说明 |
|---|---|
| `id` | workflow instance id |
| `root_job_id` | 对外 root Job |
| `caller_id` | 与 root Job 对齐，用于隔离和审计 |
| `client_request_id` | 原始提交幂等键，便于排障 |
| `workflow_type` | 例如 `poster_title_image`、`multi_step_llm` |
| `workflow_version` | frozen planner 版本 |
| `status` | `pending`、`running`、`succeeded`、`failed`、`canceled` |
| `outcome` | `success`、`partial_success`、`failure`，只表示 workflow outcome |
| `failure_policy` | `fail_fast`、`allow_partial` |
| `success_criteria` | frozen 的终态成功判定规则，例如 `all_required`、`at_least_one_item` 或 `min_success_count` |
| `plan_ref` / `plan_hash` | frozen plan 的对象引用或 hash |
| `total_node_count` | 节点总数 |
| `completed_node_count` | 已完成节点数 |
| `failed_node_count` | 已失败节点数 |
| `ready_node_count` | 当前 ready 节点数，可作为派生缓存 |
| `lease_token` | process manager 当前推进租约 |
| `leased_by` | 当前推进者标识 |
| `lease_expires_at` | workflow owner lease 过期时间 |
| `created_at` / `started_at` / `finished_at` / `updated_at` | 生命周期时间 |

### `workflow_nodes`

Workflow 内部节点。节点只表达编排状态；leaf node 的实际执行由 child Job 承担。

| 字段 | 说明 |
|---|---|
| `id` | node id |
| `workflow_id` | 所属 workflow instance |
| `node_key` | workflow 内稳定唯一 key |
| `node_kind` | `task`、`join`、`map_shard`、`chunk`、`finalize` |
| `status` | `pending`、`ready`、`running`、`succeeded`、`failed`、`skipped` |
| `job_type` | leaf task node 对应 child Job type；非 leaf 可为空 |
| `execution_job_id` | leaf task node 创建的 child Job |
| `is_public_child` | 第一版固定为 `false`；表示 child Job 不进入公开查询合同 |
| `input_ref` / `input_hash` | 输入 payload 引用与校验 |
| `output_ref` / `output_hash` | 输出 payload 引用与校验 |
| `error` | 终态错误摘要 |
| `retry_policy` | node 级重试策略声明，最终映射到 child Job max attempts |
| `timeout_seconds` | node 超时 |
| `weight` | root progress 聚合权重 |
| `map_index` | map / starmap 展开序号 |
| `chunk_index` | chunks 分片序号 |
| `created_at` / `started_at` / `finished_at` / `updated_at` | 生命周期时间 |

约束建议：

- `unique(workflow_id, node_key)`
- `unique(workflow_id, execution_job_id)` where `execution_job_id is not null`
- `unique(workflow_id, idempotency_key)` for leaf node dispatch if `idempotency_key` is stored on node
- `status` 必须由显式状态机转移，不能任意覆盖。

### `workflow_node_dependencies`

DAG edge 表。

| 字段 | 说明 |
|---|---|
| `id` | dependency id |
| `workflow_id` | 所属 workflow |
| `from_node_id` | 上游节点 |
| `to_node_id` | 下游节点 |
| `edge_kind` | `success`、`failure`、`always` |

约束建议：

- `unique(workflow_id, from_node_id, to_node_id, edge_kind)`
- planner 落库前必须校验 DAG 无环。

### `workflow_events`

Workflow audit timeline，不驱动主流程。

| 字段 | 说明 |
|---|---|
| `id` | event id |
| `workflow_id` | 所属 workflow |
| `node_id` | 可选 node |
| `root_job_id` | root Job |
| `child_job_id` | 可选 child Job |
| `event_type` | 状态变化、调度、join、recovery、failure policy decision |
| `payload` | 排障上下文 |
| `created_at` | 事件时间 |

`workflow_events` 的定位与 `job_audit_events` 一致：它是审计、排障和运维时间线，不是 event sourcing runtime。流程恢复必须能仅依赖 `workflow_instances`、`workflow_nodes`、child Job 当前状态和 outbox 状态完成。

### `workflow_wakeup_outbox`

Workflow process manager 的可靠唤醒 outbox。它只发布 workflow orchestrator tick，不发布 leaf task 执行消息。

| 字段 | 说明 |
|---|---|
| `id` | outbox id |
| `event_id` | 幂等事件 id |
| `workflow_id` | 目标 workflow instance |
| `wakeup_reason` | `created`、`child_terminal`、`join_ready`、`reconcile` |
| `status` | `pending`、`leased`、`published`、`retrying`、`dead_letter` |
| `payload` | 最小唤醒上下文，不承载 workflow 真相 |
| `publish_attempts` | 发布尝试次数 |
| `next_attempt_at` | 下次可发布时间 |
| `lease_token` / `lease_expires_at` | outbox publisher 租约 |
| `created_at` / `published_at` / `updated_at` | 生命周期时间 |

约束建议：

- `unique(event_id)`
- 对同一 `workflow_id`、`wakeup_reason` 和幂等 key 去重，避免 repeated tick 风暴。
- `payload` 只用于快速定位；process manager 必须重新读取数据库当前状态。

如果不想新增 workflow wakeup outbox，替代方案是仅由周期性 scheduler 扫描 due workflow instances。该方案更轻，但唤醒延迟更高。第一版若要求低延迟和可靠唤醒，推荐 `workflow_wakeup_outbox`。

### `workflow_child_jobs`

可选辅助表，用于明确 root Job、workflow node 和 child Job 的归属关系。也可以先由 `workflow_nodes.execution_job_id` 承担。

| 字段 | 说明 |
|---|---|
| `workflow_id` | 所属 workflow |
| `node_id` | leaf task node |
| `root_job_id` | 对外 root Job |
| `child_job_id` | 实际执行 Job |
| `child_job_type` | child Job type |
| `idempotency_key` | node dispatch 幂等键 |
| `is_public` | 第一版固定为 `false` |

如果只保留 `workflow_nodes.execution_job_id`，必须保证查询和 recovery 能高效从 child Job 找回 node。

## State Machines

### Workflow Instance State

```text
pending
  -> running
  -> succeeded
  -> failed
  -> canceled
```

`outcome` 与 `status` 分离：

- `status=succeeded, outcome=success`：全部要求成功。
- `status=succeeded, outcome=partial_success`：允许部分失败，root Job 仍可对外成功完成。
- `status=failed, outcome=failure`：失败策略要求整体失败，或没有任何可用结果。

第一版不建议把 `partial_success` 直接提升为公共 `job_status`。它应先进入 `job_result` summary 或内部 workflow outcome。

### Workflow Node State

```text
pending
  -> ready
  -> running
  -> succeeded
  -> failed
  -> skipped
```

关键规则：

- `pending` 表示依赖未满足。
- `ready` 表示依赖满足，但尚未创建 child Job 或执行 join。
- `running` 表示 child Job 已创建且未终态，或 join / finalize 正在执行。
- `succeeded`、`failed`、`skipped` 是 node 终态。
- 状态推进必须使用 compare-and-set 条件，避免重复消息和并发 reconciler 覆盖。

## Main Runtime Flow

### Submit Root Job

```text
POST /jobs
  -> validate job_type and job_params
  -> resolve WorkflowSpec
  -> compile frozen workflow plan
  -> transaction:
       insert root job
       insert workflow_instance
       insert workflow_nodes
       insert workflow_node_dependencies
       mark entry nodes ready
       insert workflow_wakeup_outbox for workflow orchestrator tick
  -> commit
```

### Schedule Ready Nodes

```text
orchestrator tick
  -> lease due workflow_instance or ready nodes
  -> select ready nodes for update skip locked
  -> for each leaf task node:
       transaction:
         create child job
         create child job attempt
         insert dispatch_outbox for child job
         node ready -> running
       commit
  -> for each join/finalize node:
       transaction:
         verify barrier condition
         apply deterministic aggregation
         node -> succeeded or failed
         unlock downstream nodes
       commit
```

### Apply Child Job Terminal

```text
child job reaches terminal state
  -> orchestrator or reconciler finds linked workflow_node
  -> transaction:
       if node still running:
         copy child terminal summary to node
         node -> succeeded or failed
         apply failure_policy
         unlock downstream nodes or mark skipped
         maybe complete workflow_instance
         maybe complete root job
         maybe insert callback_outbox for root job terminal
  -> commit
```

这个流程必须幂等。重复收到 child Job 终态、重复触发 orchestrator tick 或 reconciler 重跑，都只能得到同一个最终状态。

## Transaction Boundaries

### 创建 root workflow

Root Job、workflow instance、workflow nodes、dependency 和第一次 orchestrator tick 的 wakeup intent 必须在同一个事务内提交。否则会出现 root Job 已存在但 workflow 永远不会启动的 orphan state。

这里的 orchestrator tick 必须写入 `workflow_wakeup_outbox`，不能写入现有 `dispatch_outbox`。现有 `dispatch_outbox` 继续只服务 Job attempt 发布。

### 创建 child Job

Leaf node 从 `ready` 进入 `running` 时，child Job、child attempt、child dispatch outbox 和 node `execution_job_id` 必须在同一个事务内提交。否则会出现 node 以为已派发但消息丢失，或 child Job 已创建但 node 无法关联。

### 完成 root Job

Workflow terminal、root Job terminal、root callback outbox 和 root result projection 必须在同一个事务内提交。Callback 投递失败不改变 root Job 终态。

### Cost Finalization

Cost 聚合不能改变 cost 的事实源。终态投影只能从 `ai_call_ledger_entries` 聚合：

```text
ai_call_ledger_entries
  -> workflow_id / workflow_node_id / root_job_id / child_job_id / attempt_id
  -> billing read model or terminal cost summary projection
```

如果 callback 或终态轮询需要返回总费用，只能返回从 ledger 派生的 summary。不能把 `job.cost` 当成新的事实源。

Root Job billing 查询必须覆盖 descendant AI calls。推荐写入 ledger 时同时保存 root scope 和 node/child attribution：

```text
root_job_id
workflow_id
workflow_node_id
child_job_id
attempt_id
scope_type="job"
scope_id=<root_job_id>
```

这样 `GET /jobs/{root_job_id}/billing` 仍按 root Job scope 聚合，同时内部可以按 workflow、node、child Job 和 attempt 排障。若未来需要 child Job 独立 billing，只能作为内部/运维查询或新合同发布，不能改变 root Job billing 的事实源。

## Progress Contract

Root Job 对外 `percent` 应采用 Job 级单调展示合同：

- 调用方不能看到 node retry 或 child Job attempt 的内部回退。
- 只有 workflow process manager 可以投影 root Job progress；child Job worker 不能直接写 root Job progress。
- `stage` 和 `message` 在公开合同未变更前也只能由 workflow process manager 投影，必须保持 root Job 级语义，并继续受当前公共枚举和 schema 约束。
- 同一 root Job 对外 `percent` 不下降。
- 非终态 root Job 的 `percent` 范围是 `0..99`。
- 终态 root Job 的 `percent` 是 `100`。
- `percent` 不能作为状态判断依据，只能用于 UI 展示。

建议从 node weight 派生：

```text
calculated_percent = floor(completed_weight / total_weight * 100)
public_percent = max(previous_public_percent, min(calculated_percent, 99))
```

Root Job terminal 时再置为 `100`。

## Failure Policy

### `fail_fast`

- 任一 required node 失败后，workflow 进入失败收敛。
- 未开始的下游节点标记为 `skipped`。
- root Job 终态为 `failed`。
- 已经成功的 child result 可保留为内部排障或 result draft，但当前公共合同下 failed Job 不返回 `job_result`。

### `allow_partial`

- 独立 item node 失败不必导致整个 workflow failed。
- join / finalize node 汇总每个 item 的结果和错误摘要。
- 如果满足 frozen `success_criteria`，workflow 可 `status=succeeded, outcome=partial_success`。
- root Job 第一版固定为 `job_status=succeeded`，并在 `job_result.items[].status` 或 `job_result.summary.outcome="partial_success"` 表达部分成功。

`success_criteria` 必须进入 `WorkflowSpec` 和 frozen plan。第一版可支持少量确定性规则：

- `all_required`：所有 required nodes 成功。
- `at_least_one_item`：至少一个 item 成功。
- `min_success_count`：成功 item 数达到固定阈值。

不允许在进程内临时写 Python 判断函数后不落库；reconciler 重启后必须能做同一个终态决策。

是否增加公共 `partially_succeeded` 状态，应作为单独 API contract 变更处理，不应和 workflow kernel 第一版绑定。

## Reconciler Responsibilities

Workflow reconciler 是生产可靠性的一部分，不是可选脚本。它至少需要修复：

- `workflow_instance=running` 但没有 runnable node、running node 或 terminal decision。
- `workflow_node=ready` 但长期没有 child Job，也没有 join 执行记录。
- `workflow_node=ready` 已落库，但 child Job 和 dispatch outbox 未创建。
- `workflow_node=running` 但 child Job 已终态，node 未应用终态。
- child Job 已终态但 root workflow 没有推进 downstream nodes。
- join 依赖全部满足但 join node 未执行。
- workflow 已终态但 root Job 未终态。
- root Job 已终态但 callback outbox 未创建。

所有 reconciler 动作都必须幂等，并记录 `workflow_events`。

Recovery 顺序必须先收敛数据库状态，再补发外部动作。不能依赖“某条事件一定会被再次投递”才能恢复。

## Visibility and Retention

### Child Job Visibility

第一版 child Job 是内部执行资源，不是外部 API 资源：

- 公共 `GET /jobs/{job_id}` 只返回 root Job。
- child Job 不发送调用方 callback。
- child Job 的 result 和 error 只通过 workflow node 汇总后投影到 root Job result。
- 如需排查 child Job，应增加内部/运维查询能力，而不是复用公开 Job 查询合同。

如果实现上暂时无法阻止 child Job 被公共查询命中，则不能发布 workflow kernel；必须先补 root/child 可见性字段或等价访问控制。

### Retention

Root Job、workflow instance、workflow nodes、child Job、attempt、callback 和 AI call ledger 的保留期不能互相破坏：

- root Job 查询期内，workflow summary 和 terminal result 必须可用。
- root billing 查询期内，所有 descendant ledger 行必须可用。
- child Job 不能早于 root workflow 所需的排障窗口被硬删除。
- 保留期关系必须满足：root billing retention >= descendant ledger retention >= workflow/node 排障 retention。
- cleanup 只能删除已经不影响公开查询、billing 聚合和运维恢复的记录。

## Developer Contract

业务开发者不应直接操作 workflow 表或直接发 Taskiq 消息。新增 workflow 应通过注册 `WorkflowSpec` 完成。

`WorkflowSpec` 至少声明：

- `workflow_type`
- `workflow_version`
- `root_job_type`
- `input_schema`
- `failure_policy`
- `success_criteria`
- `nodes`
- `dependencies`
- `node retry / timeout`
- `node weight`
- `cost attribution scope`
- `result projection`

Planner 必须在提交前校验：

- node key 唯一。
- DAG 无环。
- 每个 dependency 指向存在的 node。
- fan-out 数量、chunk size 和总 node 数不超过配置限制。
- 每个 leaf node 能生成稳定 child Job idempotency key。
- 每个 node 的输入和输出引用策略明确。
- failure policy 与 result projection 匹配。
- `success_criteria` 已冻结，且 reconciler 不依赖进程内临时函数做终态判定。

## Observability

最小观测面：

- root Job id、workflow id、node id、child job id、attempt id 的 correlation id。
- workflow status、node status、ready/running/failed/skipped counts。
- orchestrator tick 次数、成功数、失败数、重复跳过数。
- reconciler 修复类型和次数。
- child Job terminal apply 延迟。
- join barrier 等待时间。
- fan-out 数量、chunk 数量和每个 workflow 的总 provider 调用数。
- ledger incomplete / failed cost 行与 workflow/node 的关联。

## Implementation Phases

### Phase 1: Internal Kernel Skeleton

- 增加 workflow tables 和 migration。
- 增加 `WorkflowSpec` 注册、编译和 DAG 校验。
- 支持 root Job 提交时创建 frozen workflow plan。
- 增加 `workflow_wakeup_outbox` 或明确采用周期性 due-scan scheduler；不能复用现有 `dispatch_outbox` 发布 workflow tick。
- 支持 `chain`、`group`、`chord`、`map`、`starmap`、`chunks` 编译为 node/dependency。
- 暂不新增公共 API 状态。

### Phase 2: Orchestrator and Child Job Dispatch

- 实现 ready node scheduler。
- leaf task node 创建 child Job，复用现有 JobAttempt 和 DispatchOutbox。
- join / finalize node 支持幂等汇总。
- child Job 终态可幂等推进 node 和 workflow。

### Phase 3: Recovery, Cost and Progress Projection

- 增加 workflow reconciler。
- `ai_call_ledger_entries` 增加 workflow/node/child job 归因字段或等价 scope。
- root Job progress 改为 workflow weight 派生的单调展示值。
- terminal callback 和 terminal polling 可返回从 ledger 派生的 cost summary。

### Phase 4: First Business Adoption

- 选择 `poster_title_image` 作为第一个 workflow spec。
- 不为该业务新增特殊调度机制。
- 验证多 item、allow_partial、fail_fast、成本聚合、callback 和轮询终态。

## Acceptance

- `WorkflowSpec` 编译产物可完全落库，进程重启后能继续执行。
- `chain`、`group`、`chord`、`map`、`starmap`、`chunks` 都只编译成 node/dependency/join，不新增六套 runtime。
- child Job 创建和 dispatch intent 在同一事务提交。
- child Job 终态重复应用不会重复推进 node、重复创建 downstream child Job 或重复完成 root Job。
- join node 在并发触发下只执行一次。
- workflow reconciler 能修复 ready、running、join 和 root terminal projection 的 stuck 状态。
- root Job 对外 `percent` 在 retry、reconciler 和 child Job 失败后不下降。
- root Job 对外 `stage` 和 `message` 不泄漏 child node 或 attempt 级内部阶段。
- `allow_partial` 不要求公共 `partially_succeeded` 状态；部分成功先通过 workflow outcome 和 result summary 表达。
- `allow_partial` 的成功判定来自 frozen `success_criteria`，重启后可重复计算。
- billing / cost 仍从 `ai_call_ledger_entries` 聚合，任何 summary 字段都不是事实源。
- callback 只在 root Job 终态发送；callback 失败不改变 root Job 终态。
- `./scripts/verify.sh check` 覆盖 planner、state machine、幂等推进和 billing projection 的单元测试。
- 涉及真实 worker/outbox/recovery 的变更通过 `./scripts/verify.sh workflow-smoke`。

## Explicit Non-goals

- 不支持外部用户自定义 DAG。
- 不暴露 node 查询 API。
- 不在第一版做可视化 workflow designer。
- 不实现 Saga 补偿框架；当前场景主要是单服务 AI 子任务编排，不是跨服务业务事务。
- 不把 Taskiq 当成 workflow engine；Taskiq 只作为任务执行通道。
- 不在 workflow kernel 内直接调用 provider；provider 调用仍通过 Job executor 和 AI gateway。
- 不吞掉 planner、dispatch、ledger 或 terminal projection 错误；错误必须进入明确状态、事件或验证失败。
