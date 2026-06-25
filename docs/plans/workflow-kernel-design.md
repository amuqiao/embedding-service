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
  -> frozen fan-out plan
  -> internal Child Job for each execution item
      -> JobAttempt
      -> DispatchOutbox
      -> Taskiq worker
```

Root Job 仍然是外部查询、callback 和幂等提交的入口。Workflow 只负责内部编排，不能绕过 Job kernel 自己发任务、自己重试或自己写 provider cost。Taskiq 仍然是执行通道；耗时 item 由 internal child Job 通过 Taskiq 执行，root Job 不应在一个 worker slot 中同步处理所有 item。

本文的 workflow 设计是新增内部编排内核范围，不推翻 `docs/plans/ai-capability-enhancement.md` 中 AI Capability Kernel 的边界，也不接管 `docs/plans/ai-capability-cost-boundary-design.md` 中的成本事实源。AI Capability Kernel 仍负责模型调用能力；Cost Boundary 仍负责成本估算和 ledger 投影；Workflow Kernel 只在多步骤、多节点 Job 成为基础需求时负责可靠编排。

### Simple Job 与 Workflow Job

不是所有 `job_type` 都必须走 child Job 模式。第一版按执行形态区分：

```text
simple job_type
  root Job = execution Job

workflow job_type
  root Job = orchestration / public aggregate
  child Job = execution unit
```

简单单步任务如果没有 fan-out、partial success、root 汇总和 child visibility 需求，可以继续由 root Job attempt 直接执行。`poster_title_image` 属于 workflow job_type；即使某次只提交 1 个 item，也应按 child Job 模式执行，保证单 item 和多 item 使用同一套生命周期、计费、callback、恢复和结果汇总逻辑。

## Architecture Principles

### Root Job 是对外聚合根

- 外部调用方只看到 root Job 的 `job_id`、`job_status`、`job_progress`、`job_result`、`job_error`、callback 和 billing 查询。
- Workflow 内部节点不直接暴露为外部 Job 查询资源。
- Internal child Job 默认不应通过公共 `GET /jobs/{job_id}` 查询暴露给调用方；公共查询入口只接受 root Job，除非未来单独发布内部/运维查询合同。
- Root Job 的终态必须由 frozen plan、child Job 终态和 failure policy 投影而来。
- 外部 callback 只由 root Job 发送一次终态事件；child Job 不触发调用方 callback。

### Workflow 是 Process Manager

Workflow kernel 负责推进流程，不直接执行业务模型调用：

- 根据 frozen fan-out plan 找到尚未创建的 child nodes。
- 为 leaf task node 创建 internal child Job。
- 在 child Job 终态后幂等推进 root finalize。
- 在所有 required child Job 或 success criteria 满足后投影 root terminal。
- 根据 failure policy 决定 root Job 最终 outcome。
- 通过 reconciler 修复 stuck、orphaned 和 missed-event 状态。

MVP 阶段不新增独立 workflow owner / lease 表。Root Job 自身就是 workflow instance；child Job 自身就是 leaf node。Process manager 只做短事务推进：创建 child Jobs、根据 child terminal 汇总 root、投影 root terminal。它不能用一个长期 root attempt 持有整条 fan-out / fan-in 生命周期。

MVP 阶段不新增 `workflow_wakeup_outbox`。当前 `dispatch_outbox` 继续只发布 Job attempt 执行消息，不能混入 workflow tick。Root 汇总推进优先由 child terminal 后的短事务和 reconciler 扫描修复完成。只有未来证明“workflow tick 需要事务内可靠唤醒且扫描延迟不可接受”时，才评估独立 `workflow_wakeup_outbox`。

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

### Canvas 原语是未来 Planner Macro

MVP 只支持固定 fan-out / finalize，不实现通用 Canvas runtime。未来如果需要 `chain`、`group`、`chord`、`map`、`starmap`、`chunks`，这些原语也不应各自成为一套 runtime 机制；它们应统一编译为 node 和 dependency：

| 原语 | 编译结果 |
|---|---|
| `chain` | `B` depends on `A` |
| `group` | 多个没有相互依赖的 ready nodes |
| `chord` | `group` + join / barrier node |
| `map` | planner 按输入数组生成同构 nodes |
| `starmap` | `map` 的参数展开形式 |
| `chunks` | planner 按 chunk size 生成分片 nodes |

未来通用 runtime 只理解：

```text
node
node dependency
node status
join / barrier rule
ready-node scheduling
terminal child-job event applying
reconciliation
```

### Workflow Plan 必须 Frozen

提交 Job 后，workflow plan 必须落库为 frozen plan。worker 和 reconciler 不能依赖运行时内存里的 Python 对象来恢复流程。

MVP frozen plan 至少要保证：

- `workflow_type` 和 `workflow_version` 固定。
- `node_key` 在同一个 workflow 内唯一。
- 每个 node 有明确 kind、输入引用、输出引用、timeout、retry policy 和 cost scope。
- fan-out 数量和 chunk size 已经通过配置或能力规则限制。
- 每个 leaf node 可生成稳定幂等键。

## Planned Data Model

本文按 MVP-first 方式定义表设计。MVP 只支持 root Job fan-out 到一组 internal child Jobs，再由 root 汇总终态；不实现通用 DAG runtime。表是否新增必须由不可替代的事实源决定，不因为概念上存在 workflow / node / event 就建表。

### MVP Table Decision

MVP 阶段新增 workflow 专属物理表数量为 0。唯一必须修改的核心表是 `job_aggregates`。

| 分类 | 表 / 修改 | MVP 决策 | 事实依据 |
|---|---|---|---|
| 现有核心表修改 | `job_aggregates` | 增加 root / child lineage 和 node key | Root Job 作为 workflow instance；child Job 作为 leaf node；需要阻止 child Job 进入公共查询 |
| 现有核心表复用 | `job_submission_keys` | 不改结构 | 继续只服务 root Job 提交幂等；child Job 由 node key 幂等创建 |
| 现有核心表复用 | `job_execution_attempts` | 不改结构 | 继续作为 root orchestration step 和 child leaf execution 的 attempt 事实源 |
| 现有核心表复用 | `dispatch_outbox` | 不改结构 | 继续只发布 Job attempt 执行消息；child Job dispatch 复用现有 outbox |
| 现有核心表复用 | `callback_outbox` | 不改结构 | 继续只保存 root Job 终态 callback；child Job 不写 caller callback |
| 现有核心表复用 | `ai_call_ledger_entries` | MVP 不改结构 | child AI call 通过 root Job scope 聚合 billing；node/child 归因字段后置 |
| 辅助表复用 | `job_audit_events` | 不改结构 | MVP 排障时间线先复用现有 Job audit；不新增 workflow audit 表 |

MVP 阶段新增字段建议：

| 表 | 字段 | 说明 |
|---|---|---|
| `job_aggregates` | `root_job_id` | 当前 Job 所属 root Job；root Job 可为空或指向自身，internal child Job 必须指向 root Job |
| `job_aggregates` | `parent_job_id` | 直接父 Job；MVP child Job 指向 root Job |
| `job_aggregates` | `is_internal` | 是否内部执行 Job；公共 `GET /jobs/{job_id}` 只能返回 `false` |
| `job_aggregates` | `workflow_node_key` | root Job 内稳定 leaf node key，用于 child Job 幂等创建 |

这是自索引聚合设计：`job_aggregates` 既保存每个 Job 的状态事实，也通过 root / parent / node key 字段表达 root -> child 的查询路径。MVP 不新增 child Job 映射表，避免把 child Job 状态复制成第二套事实源。

约束建议：

- `unique(root_job_id, workflow_node_key)` where `workflow_node_key is not null`，保证同一 root 下一个 leaf node 最多创建一个 child Job。
- `is_internal=false` 的 Job 才能被公共 Job 查询返回。
- child Job 不创建 caller callback intent；root terminal projection 才能写 `callback_outbox`。
- `root_job_id`、`parent_job_id` 和 `workflow_node_key` 是 orchestration / visibility 事实，不是 billing 事实源。

索引建议：

- `index(root_job_id)`：从 root Job 枚举所有 internal child Jobs。
- `index(parent_job_id)`：按直接父 Job 查询 child Jobs。
- `unique(root_job_id, workflow_node_key)` where `workflow_node_key is not null`：保证 leaf node 幂等创建。
- `index(root_job_id, status)`：reconciler 和排障快速查找 running / failed / terminal child Jobs。

MVP 核心恢复路径只能依赖：

```text
job_aggregates
job_submission_keys
job_execution_attempts
dispatch_outbox
callback_outbox
ai_call_ledger_entries
```

`job_audit_events`、日志、metrics 和未来 read model 都不能成为恢复前提。

### MVP Frozen Plan Authority

MVP 不新增 `workflow_plans`、`workflow_instances`、`workflow_nodes` 或 `workflow_node_dependencies`。Frozen plan 的权威来源是 root Job 已持久化字段：

- root Job 的 `job_type` 固定 workflow 类型。
- root Job 的 `job_params`、`metadata` 或 `runtime_ref` 保存 frozen fan-out plan、`workflow_version`、failure policy、success criteria、node keys 和 node input refs。
- child Job 的 `workflow_node_key` 保存 leaf node 身份。

MVP reconciler 恢复时不能依赖内存中的 Python `WorkflowSpec` 重新展开已提交 workflow；它必须读取 root Job 上已经冻结的 plan，再结合 child Jobs 当前状态推进 root。

### Deferred Tables

以下表不是 MVP 必需表。只有出现明确需求压力时才评估：

| 表 | 后置条件 |
|---|---|
| `workflow_instances` | 一个 root Job 同时承载多个 workflow instance，或 workflow 生命周期、lease、状态已经不能由 root Job 表达 |
| `workflow_nodes` | 需要通用 DAG、node 级状态查询、node 级 retry/skip/join 状态，且 child Job 行无法表达 |
| `workflow_node_dependencies` | 需要运行时恢复任意 DAG edge，而不是 MVP 固定 fan-out / finalize |
| `workflow_wakeup_outbox` | child terminal 后必须低延迟、事务内可靠唤醒 process manager，且 reconciler 扫描不可接受 |
| `workflow_events` | `job_audit_events` 已无法满足 workflow 排障时间线，且仍只作为辅助表 |
| `workflow_child_jobs` | 从 root 枚举 child Job 的查询出现明确性能瓶颈，且可作为可重建 read model |
| `workflow_node_attempts` / `workflow_node_outbox` | 出现不适合建模为 Job 的 node 执行单元，但它仍需要独立 lease / heartbeat / retry |
| `job_cost_summary` | ledger 聚合查询成为性能瓶颈，且 summary 明确是可重建 read model |

## State Machines

### MVP Root Workflow State

```text
root job queued
  -> running
  -> succeeded
  -> failed
```

MVP 不新增公共 `job_status`。Root Job 继续使用现有 `queued`、`running`、`succeeded`、`failed`。Workflow outcome 只进入 root `job_result` summary 或内部 metadata：

- `job_status=succeeded, outcome=success`：全部 required child Job 成功。
- `job_status=succeeded, outcome=partial_success`：允许部分失败，且满足 frozen `success_criteria`。
- `job_status=failed, outcome=failure`：失败策略要求整体失败，或没有任何可用结果。

第一版不把 `partial_success` 提升为公共 `job_status`。

### MVP Child Node State

```text
not_created
  -> running
  -> succeeded
  -> failed
```

关键规则：

- `not_created` 是 frozen plan 中存在 node key，但还没有对应 child Job。
- child Job 创建后，node 状态由 child Job 的 `job_aggregates.status` 表达。
- MVP 不保存独立 node status，不保存 skipped node 行。被 failure policy 跳过的 node 只进入 root result summary 或 audit。
- 状态推进必须依赖 `workflow_node_key` 唯一约束和 Job terminal compare-and-set，避免重复创建 child Job 或重复完成 root Job。

## Main Runtime Flow

### Submit Root Job

```text
POST /jobs
  -> validate job_type and job_params
  -> resolve WorkflowSpec
  -> compile MVP frozen fan-out plan
  -> transaction:
       claim root job_submission_keys idempotency key
       insert root job with frozen plan in job metadata / runtime_ref
       insert root job attempt
       insert dispatch_outbox for root orchestration step
  -> commit
```

### Create Child Jobs

```text
root orchestration step or reconciler
  -> load root job frozen fan-out plan
  -> for each node key without child job:
       transaction:
         create child job with root_job_id, parent_job_id, is_internal=true, workflow_node_key
         create child job attempt
         insert dispatch_outbox for child job
       commit
```

同一 root 下的 `workflow_node_key` 唯一约束保证重复 root attempt、重复 reconciler 和并发进程不会创建多个同义 child Job。

### Apply Child Job Terminal

```text
child job reaches terminal state
  -> terminal hook or reconciler finds root by child.root_job_id
  -> transaction:
       load root frozen plan and all child jobs
       if terminal criteria are met:
         apply failure_policy / success_criteria
         project root result / error / progress
         complete root job
         maybe insert callback_outbox for root job terminal
  -> commit
```

这个流程必须幂等。重复收到 child Job 终态、重复运行 root finalizer 或 reconciler 重跑，都只能得到同一个最终 root 状态。

## Transaction Boundaries

### 创建 root workflow

Root Job、root attempt、root dispatch outbox 和 frozen fan-out plan 必须在同一个事务内提交。否则会出现 root Job 已存在但 orchestration step 永远不会启动的 orphan state。

这里复用现有 `dispatch_outbox` 发布 root orchestration step，因为它仍然是一个 Job attempt 执行消息；不能把 workflow tick 或其它非 Job attempt 副作用塞进 `dispatch_outbox`。

### 创建 child Job

Child Job、child attempt 和 child dispatch outbox 必须在同一个事务内提交。否则会出现 child Job 已创建但消息丢失，或 dispatch intent 指向不存在的执行事实。

### 完成 root Job

Root Job terminal、root callback outbox 和 root result projection 必须在同一个事务内提交。Callback 投递失败不改变 root Job 终态。

### Cost Finalization

Cost 聚合不能改变 cost 的事实源。终态投影只能从 `ai_call_ledger_entries` 聚合：

```text
ai_call_ledger_entries
  -> scope_type="job" / scope_id=<root_job_id>
  -> billing read model or terminal cost summary projection
```

如果 callback 或终态轮询需要返回总费用，只能返回从 ledger 派生的 summary。不能把 `job.cost` 当成新的事实源。

MVP 阶段 Root Job billing 查询必须覆盖 descendant AI calls。Child Job 调用 AI 时必须写 root Job scope：

```text
attempt_id
scope_type="job"
scope_id=<root_job_id>
```

这样 `GET /jobs/{root_job_id}/billing` 仍按 root Job scope 聚合。`ai_call_ledger_entries` 的 root / workflow / node / child 归因列不是 MVP 必需项；只有需要高效按 node/child 排障或查询时再通过 migration 增加。

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

- root Job `running`，但 frozen plan 中的 node key 还没有对应 child Job。
- child Job 已创建，但 child attempt 或 child dispatch outbox 缺失。
- child Job 已终态，但 root Job 没有重新计算 terminal decision。
- 所有 child Job 已终态，但 root Job 未终态。
- root Job 已终态但 callback outbox 未创建。

所有 reconciler 动作都必须幂等，并记录结构化日志或 `job_audit_events`。MVP 不新增 `workflow_events`。

Recovery 顺序必须先收敛数据库状态，再补发外部动作。不能依赖“某条事件一定会被再次投递”才能恢复。

## Visibility and Retention

### Child Job Visibility

第一版 child Job 是内部执行资源，不是外部 API 资源：

- 公共 `GET /jobs/{job_id}` 只返回 root Job。
- child Job 不发送调用方 callback。
- child Job 的 result 和 error 只通过 workflow node 汇总后投影到 root Job result。
- 如需排查 child Job，应增加内部/运维查询能力，而不是复用公开 Job 查询合同。

如果实现上暂时无法阻止 child Job 被公共查询命中，则不能发布 workflow kernel；必须先落地 `job_aggregates.root_job_id`、`job_aggregates.parent_job_id` 和 `job_aggregates.is_internal`，并让公开查询只读取 root Job。

### Retention

Root Job、internal child Job、attempt、callback 和 AI call ledger 的保留期不能互相破坏：

- root Job 查询期内，workflow summary 和 terminal result 必须可用。
- root billing 查询期内，所有 descendant ledger 行必须可用。
- child Job 不能早于 root workflow 所需的排障窗口被硬删除。
- 保留期关系必须满足：root billing retention >= descendant ledger retention >= child Job 排障 retention。
- cleanup 只能删除已经不影响公开查询、billing 聚合和运维恢复的记录。

## Developer Contract

业务开发者不应直接发 Taskiq 消息或手写 child Job 编排。新增 workflow 应通过注册 `WorkflowSpec` 完成。

`WorkflowSpec` 至少声明：

- `workflow_type`
- `workflow_version`
- `root_job_type`
- `input_schema`
- `failure_policy`
- `success_criteria`
- `nodes`
- `node retry / timeout`
- `node weight`
- `cost attribution scope`
- `result projection`

Planner 必须在提交前校验：

- node key 唯一。
- fan-out 数量、chunk size 和总 node 数不超过配置限制。
- 每个 leaf node 能生成稳定 child Job idempotency key。
- 每个 node 的输入和输出引用策略明确。
- failure policy 与 result projection 匹配。
- `success_criteria` 已冻结，且 reconciler 不依赖进程内临时函数做终态判定。

## Observability

最小观测面：

- root Job id、workflow node key、child job id、attempt id 的 correlation id。
- root Job status、child Job status、created/running/succeeded/failed child counts。
- root orchestration step 次数、成功数、失败数、重复跳过数。
- reconciler 修复类型和次数。
- child Job terminal apply 延迟。
- finalize 等待时间。
- fan-out 数量、chunk 数量和每个 root workflow 的总 provider 调用数。
- ledger incomplete / failed cost 行与 root Job / child Job 的关联。

## Implementation Phases

### Phase 0: Contract Readiness

开发 workflow kernel 前必须先通过 [`implementation-terminal-acceptance.md`](implementation-terminal-acceptance.md) 中的 Phase 0 readiness。本文只保留 workflow 专属增量：planner、runtime state machine、child dispatch、join/finalize、root progress projection 和 reconciler。

### Phase 1: Internal Kernel Skeleton

- 不新增 workflow 专属表。
- 修改 `job_aggregates`，增加 root / child lineage、internal visibility 和 `workflow_node_key`，确保 child Job 不进入公共查询合同。
- 增加 MVP `WorkflowSpec` 注册、编译和 frozen fan-out plan 校验。
- 支持 root Job 提交时把 frozen fan-out plan 写入 root Job 已有持久化字段。
- 支持 root orchestration step 幂等创建 internal child Jobs。
- 暂不新增公共 API 状态。

### Phase 2: Orchestrator and Child Job Dispatch

- child Job 复用现有 JobAttempt 和 DispatchOutbox。
- child Job 终态或 reconciler 可幂等推进 root finalize。
- root finalize 支持 `fail_fast` 和 `allow_partial`。
- root progress 从 frozen node weights 和 child Job 终态派生。

### Phase 3: Recovery, Cost and Progress Projection

- 增加 workflow reconciler。
- child AI call 使用 `scope_type="job"`、`scope_id=<root_job_id>` 写入现有 ledger。
- terminal callback 和 terminal polling 可返回从 ledger 派生的 root cost summary。
- 只有当 node/child 归因查询成为明确需求时，才评估给 `ai_call_ledger_entries` 增加归因列。

### Phase 4: Future Generalization Review

- 根据 MVP 运行结果评估是否需要 `workflow_instances`、`workflow_nodes`、`workflow_node_dependencies` 或 `workflow_wakeup_outbox`。
- 只有当固定 fan-out/finalize 无法承载真实业务，才推进通用 DAG runtime。

### Phase 5: First Business Adoption

- 选择 `poster_title_image` 作为第一个 workflow spec。
- 不为该业务新增特殊调度机制。
- 验证多 item、allow_partial、fail_fast、成本聚合、callback 和轮询终态。

## Acceptance

- [`implementation-terminal-acceptance.md`](implementation-terminal-acceptance.md) 中的 Phase 0 readiness 已经通过。
- MVP `WorkflowSpec` 编译产物可写入 root Job 持久化字段，进程重启后能继续执行。
- MVP 不新增 workflow 专属表，只修改 `job_aggregates` 并复用现有 Job kernel 表。
- child Job 创建和 dispatch intent 在同一事务提交。
- child Job 终态重复应用不会重复创建 child Job 或重复完成 root Job。
- root finalize 在并发触发下只执行一次。
- workflow reconciler 能修复 missing child、missing dispatch、child terminal 和 root terminal projection 的 stuck 状态。
- root Job 对外 `percent` 在 retry、reconciler 和 child Job 失败后不下降。
- root Job 对外 `stage` 和 `message` 不泄漏 child node 或 attempt 级内部阶段。
- `allow_partial` 的 workflow outcome 和成功判定来自 frozen `success_criteria`，重启后可重复计算。
- `./scripts/verify.sh check` 覆盖 planner、state machine、幂等推进和 workflow projection 的单元测试。
- 涉及真实 worker/outbox/recovery 的变更通过 `./scripts/verify.sh workflow-smoke`。

## Explicit Non-goals

- 不支持外部用户自定义 DAG。
- 不暴露 node 查询 API。
- 不在第一版做可视化 workflow designer。
- 不实现 Saga 补偿框架；当前场景主要是单服务 AI 子任务编排，不是跨服务业务事务。
- 不把 Taskiq 当成 workflow engine；Taskiq 只作为任务执行通道。
- 不在 workflow kernel 内直接调用 provider；provider 调用仍通过 Job executor 和 AI gateway。
- 不吞掉 planner、dispatch、ledger 或 terminal projection 错误；错误必须进入明确状态、事件或验证失败。
