# Job Kernel 当前模型

本文解释当前 Job 可靠执行内核和表设计边界。它不是对外 HTTP 合同；对外字段以 [`../api/service-contract.md`](../api/service-contract.md) 和 `app/schemas/` 为准。

## 一句话结论

当前 Job kernel 的事实源按“不可替代的运行事实”组织，不按概念名拆表。

```text
Transactional Outbox
+ Lease
+ Heartbeat
+ Idempotent Consumer
+ Retry Attempt State
+ Root / Internal Child Lineage
```

因此核心不只是 `job_aggregates` 和 `dispatch_outbox`。`job_execution_attempts`、`job_submission_keys`、`callback_outbox` 也分别承载提交幂等、执行互斥、恢复、重试和副作用投递所需的事实。

## 整体分层

```text
POST /jobs
  |
  |  caller_id + client_request_id 去重
  v
job_submission_keys
  |
  |  创建 public root Job
  v
job_aggregates
  |
  |  创建一次可执行 attempt
  v
job_execution_attempts
  |
  |  DB 事务内保存待发布任务消息
  v
dispatch_outbox
  |
  |  publisher 发布到 Taskiq
  v
worker executes attempt
  |
  |  root Job 终态后保存 callback 投递意图
  v
callback_outbox

job_audit_events 只记录排障时间线，不参与状态推进
```

Transactional Outbox 本体只要求业务事实和待发布消息意图在同一个数据库事务提交。当前 Job kernel 还有其它核心表，是因为异步 Job 还必须处理重复提交、重复消费、worker 崩溃、heartbeat、重试、callback 和恢复。

## 表职责

| 表 | 角色 | 是否核心 | 不承担 |
|---|---|---|---|
| `job_aggregates` | Job 聚合事实源，保存状态、进度、结果、错误、callback 汇总状态和 root/child lineage | 是 | 不保存每次执行尝试的 lease / heartbeat 细节 |
| `job_submission_keys` | 提交幂等键，保证同一 caller 的 `client_request_id` 可拒重或返回已有 Job | 是 | 不表示执行状态，不发布消息 |
| `job_execution_attempts` | 单次执行尝试，持有 lease、worker、heartbeat、attempt 状态和失败原因 | 是 | 不是审计历史表，不能从核心流程移除 |
| `dispatch_outbox` | 从数据库事务可靠发布 Taskiq 任务的 outbox | 是 | 不发布 callback，不表达 Job 业务终态 |
| `callback_outbox` | Job 终态 Callback 的投递账本和重试队列 | 是 | 不改变 Job 终态，不发布 worker 任务 |
| `job_audit_events` | 内部审计事件和排障时间线 | 辅助 | 不作为恢复、幂等或状态推进依据 |

## Job 聚合根

公开调用方提交得到的是 public root Job。调用方查询、callback、billing 和最终结果都以 root Job 为入口。

当前已经落地两种执行形态：

| 形态 | 当前行为 |
|---|---|
| 普通 non-workflow Job | public root Job 直接持有 active attempt 并由 worker 执行 |
| workflow Job | public root Job 持有 frozen `workflow_plan`，root attempt 只做 orchestration，实际执行由 internal child Jobs 完成 |

注意：普通 non-workflow Job 当前还不是 `root + one child`。把所有对外 Job 统一成 root 聚合根、实际执行统一落到 internal leaf child，是后续计划，不是 current fact。

## Job Type 目录元数据

`JobTypeSpec` 当前包含 `visibility` 和 `role` 两个目录元数据，用于开发者、运维和脚本理解 job_type 的入口性质：

| 字段 | 当前取值 | 说明 |
|---|---|---|
| `visibility` | `public` / `demo` / `internal` | `public` 是正式业务入口；`demo` 是模板示例、smoke 或压测入口；`internal` 预留给只供服务内部使用的 helper job_type |
| `role` | `root` / `leaf` / `root_or_leaf` | `root` 是聚合根入口；`leaf` 是 workflow child executor；`root_or_leaf` 表示可直提也可被 workflow 复用 |

这两个字段是 registry/catalog intent，同时参与外部提交准入。`APP_ENV=local/dev` 允许外部提交 `public` 和 `demo`；`APP_ENV=test/prd` 只允许外部提交 `public`。`internal` 只供服务内部 workflow child 使用，任何环境都不能被外部直接提交。运行时 root/child lineage 仍由 `job_aggregates.root_job_id`、`parent_job_id`、`is_internal` 和 `workflow_node_key` 表达。

当前内置标记：

| job_type | visibility | role |
|---|---|---|
| `poster_title_image` | `public` | `root` |
| `arithmetic` | `demo` | `root` |
| `job_test_workflow` | `demo` | `root` |
| `job_test_echo` | `demo` | `root_or_leaf` |
| `job_test_add` | `demo` | `root_or_leaf` |
| `job_test_collect` | `demo` | `leaf` |
| `job_real_llm_echo` | `demo` | `root_or_leaf` |
| `job_real_llm_double_echo` | `demo` | `root_or_leaf` |

## Attempt 解决什么

`job_execution_attempts` 解决的是“消息已经被 worker 拿到，但 worker 执行过程中可能崩溃、超时、重复消费或需要重试”的问题。

```text
Taskiq message(attempt_id)
  worker claims attempt with lease_token
  attempt pending -> running
  heartbeat updates lease window
  executor runs
  terminal write requires current active_attempt_id + lease_token
```

它承担三类职责：

- **执行互斥**：多个 worker 收到重复消息时，只有成功领取 lease 的 worker 可以推进 attempt。
- **活性判断**：heartbeat 与 `lease_expires_at` 让 recovery 判断 running attempt 是否失活。
- **重试事实**：每次尝试有独立 `attempt_no`、状态、错误、开始/结束时间和 retryable 判断。

所以它不只是历史记录。如果把它降级为审计表，worker 多副本、重复消息、超时恢复和按 attempt 重试都会缺少事实源。

## Outbox 边界

`dispatch_outbox` 和 `callback_outbox` 都属于 outbox 模式，但它们不能合并为一个含混的事件表。

```text
dispatch_outbox
  source: DB transaction
  target: Taskiq broker
  payload: worker task message
  recovery: publisher / dispatch recovery

callback_outbox
  source: root Job terminal transaction
  target: caller callback URL
  payload: signed callback envelope
  recovery: callback delivery retry
```

两者分开不是因为 outbox 模式天然需要两张表，而是因为副作用目标、重试策略、payload 合同和恢复责任不同。Callback 投递失败不改变 Job 终态；调用方应以 `GET /jobs/{job_id}` 轮询作为兜底。

## Workflow Root / Child 表达

当前 workflow 不新增 `workflow_instances`、`workflow_nodes`、`workflow_node_dependencies` 或 `workflow_wakeup_outbox`。它用 `job_aggregates` 自索引表达 root/child lineage：

```text
job_aggregates
  id
  root_job_id
  parent_job_id
  is_internal
  workflow_node_key
  status
  result
  error
```

字段含义：

| 字段 | 当前含义 |
|---|---|
| `root_job_id` | internal child 归属的 public root Job |
| `parent_job_id` | 当前 workflow-created child 的 lineage / owner link；实际创建时直接指向 root |
| `is_internal` | 是否为内部 child Job；公开查询不把 internal child 作为调用方资源 |
| `workflow_node_key` | root 内 leaf node 的幂等身份 |

`parent_job_id` 不表达 DAG 执行依赖。当前 workflow child 都直接挂在 root 下；`chain`、`chord` 等顺序关系只存在 frozen `workflow_plan.nodes[].depends_on` 中。

```text
Lineage:
root
  |- child[a]
  |- child[b]
  |- child[c]

Dependencies:
a -> b -> c
```

因此 `chain(a, b, c)` 的含义是 `b depends_on a`、`c depends_on b`，不是 `b` 是 `a` 的 child job。

## 自索引聚合

Workflow 使用自索引聚合，而不是新增 child Job 映射表。`job_aggregates` 仍然是 Job 状态事实源；`root_job_id`、`parent_job_id` 和 `workflow_node_key` 只是让同一张事实表可以表达 root -> child 查询。这样不会出现 `child_jobs.status` 和 `job_aggregates.status` 两套状态互相冲突。

关键索引：

```text
index(root_job_id)
index(parent_job_id)
unique(root_job_id, workflow_node_key) where workflow_node_key is not null
index(root_job_id, status)
```

其中 `unique(root_job_id, workflow_node_key)` 是幂等约束，不只是查询优化。它保证重复 root orchestration、重复 reconciler 或并发进程不会为同一个 root node 创建多个 child Job。

常见排查查询：

```sql
select
  id,
  workflow_node_key,
  status,
  progress_percent,
  error,
  created_at,
  started_at,
  finished_at
from job_aggregates
where root_job_id = :root_job_id
  and is_internal = true
order by workflow_node_key;
```

只有当这类查询已经有明确性能瓶颈，或需要一个可重建的运维 read model 时，才评估新增 child Job 索引表。即使未来新增，它也不能成为 Job 状态事实源。

## 多进程影响

多 API Pod：

- `job_submission_keys` 的唯一约束保证提交幂等。
- `dispatch_outbox.event_id` 和 `(attempt_id, task_name)` 唯一约束避免重复 dispatch 意图。

多 worker Pod：

- `job_execution_attempts.lease_token` 与行锁保证同一 attempt 只有一个有效执行者。
- worker 写终态时必须仍持有当前 lease。
- recovery 只处理 lease 过期或 stale 的 attempt。

多 publisher / callback worker：

- outbox 行领取依赖状态、lease token 和 lease 过期时间。
- 失败后按 `next_attempt_at` 重试，超过上限进入失败或 dead letter 状态。

## 新增表判断规则

新增表前必须先确认它保存的是不可替代事实，而不是现有事实的重复投影。

至少回答四个问题：

1. 这张表保存的事实，现有核心表是否已经能表达？
2. 如果没有这张表，崩溃恢复、幂等、执行互斥、重试或副作用投递会缺失哪一步？
3. 这张表是否会驱动状态推进？如果不会，它是否只是辅助表或 read model？
4. 这张表能否从现有核心事实重建？如果能，默认不要让核心流程依赖它。

不应新增表的典型情况：

- 只是为了把已有字段换个名字重新组织。
- 只是为了查询更顺手，但没有明确性能瓶颈。
- 只是为了记录日志、排障或统计，却让核心流程依赖它。
- 只是因为概念上存在一个对象，例如 workflow、node、event、summary。

只有答案指向一个不可替代的事实源时，才应该新增核心表。

## 保留边界

可以按需裁剪的是外围能力，例如：

- 是否启用 callback。
- 是否开放 billing 查询。
- 是否保留长期 audit event。
- 是否增加更细的监控和 dead letter 运维界面。

不应按需关闭的是：

- Job 提交幂等。
- Dispatch outbox。
- Attempt lease / heartbeat。
- Worker 终态写入的 attempt token 校验。
- Workflow child 的 root lineage 和 `workflow_node_key` 幂等约束。
