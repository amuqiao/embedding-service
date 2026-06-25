# Job Kernel 表设计意图

本文解释当前 Job kernel 为什么需要这些表，以及哪些表是核心事实源、哪些表只是辅助信息。它用于防止后续在 workflow、callback、重试或审计扩展时因为概念相似而盲目新增表。

## 一句话结论

表不是按概念名创建，而是按不可替代的事实创建。

```text
提交幂等层
  job_submission_keys

Job 执行控制层
  job_aggregates
  job_execution_attempts

Transactional Outbox 层
  dispatch_outbox
  callback_outbox

审计层
  job_audit_events
```

`job_audit_events` 是辅助表，不驱动主流程。其余表是否核心，取决于它们是否承载恢复、幂等、执行互斥、重试或副作用投递所需的事实。

## 分层视图

```text
POST /jobs
  |
  |  caller_id + client_request_id 去重
  v
job_submission_keys
  |
  |  创建对外 Job 聚合根
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

这个结构不是“Transactional Outbox 需要很多表”。Transactional Outbox 本体只要求业务事实和待发布消息意图在同一个数据库事务提交。当前 Job kernel 之所以还有其它表，是因为还要解决提交幂等、执行租约、heartbeat、重试、callback 投递和审计。

## 各层职责

| 层 | 表 | 核心原因 | 不承担 |
|---|---|---|---|
| 提交幂等层 | `job_submission_keys` | 防止同一 caller 的重复提交创建多个 root Job | 不表示执行状态，不发布消息 |
| Job 执行控制层 | `job_aggregates` | 保存对外 Job 状态、进度、结果、错误和 callback 汇总状态 | 不保存每次执行尝试的 lease / heartbeat 细节 |
| Job 执行控制层 | `job_execution_attempts` | 保存单次 attempt、lease、heartbeat、worker、retry 和失败原因 | 不是审计历史表，不能从核心流程移除 |
| Transactional Outbox 层 | `dispatch_outbox` | 保存 DB -> Taskiq broker 的可靠发布意图 | 不发布 callback，不表达 Job 业务状态 |
| Transactional Outbox 层 | `callback_outbox` | 保存 DB -> caller callback URL 的可靠投递意图 | 不改变 Job 终态，不发布 worker 任务 |
| 审计层 | `job_audit_events` | 保存排障时间线 | 不作为恢复、幂等或状态推进依据 |

## 为什么不是更少的表

`job_aggregates + dispatch_outbox` 能完成最小 Transactional Outbox：

```text
transaction:
  insert job_aggregates
  insert dispatch_outbox
commit
```

但当前 Job kernel 还需要处理更多事实：

- 客户端重复提交：需要 `job_submission_keys` 的唯一约束。
- worker 重复消费：需要 `job_execution_attempts` 的 attempt identity 和 lease token。
- worker 崩溃或超时：需要 `job_execution_attempts.heartbeat_at` 和 `lease_expires_at`。
- 执行失败后重试：需要 attempt 级 `attempt_no`、状态和失败原因。
- callback 可重试投递：需要 `callback_outbox`，因为它的副作用目标不是 Taskiq broker。
- 排障时间线：可以用 `job_audit_events`，但它不影响恢复正确性。

所以这些表不是为了“看起来完整”而存在，而是分别对应不同的故障模式。

## 为什么不是更多的表

新增表前必须先确认它保存的是不可替代事实，而不是现有事实的重复投影。

不应新增表的典型情况：

- 只是为了把已有字段换个名字重新组织。
- 只是为了查询更顺手，但没有明确性能瓶颈。
- 只是为了记录日志、排障或统计，却让核心流程依赖它。
- 只是因为概念上存在一个对象，例如 workflow、node、event、summary。

如果现有核心表可以通过字段、唯一约束和索引表达事实，应优先扩展现有表，而不是新增一张语义重复的表。

## Outbox 边界

`dispatch_outbox` 和 `callback_outbox` 都属于 outbox 模式，但它们不能合并为一个含混的“事件表”。

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

两者分开不是因为 outbox 模式天然需要两张表，而是因为副作用目标、重试策略、payload 合同和恢复责任不同。

## Workflow MVP 的含义

这个表设计原则直接影响 workflow MVP：

```text
root Job
  = workflow instance

internal child Job
  = leaf workflow node

workflow_node_key
  = root Job 内的 leaf node 幂等身份
```

因此 MVP 可以先不新增 `workflow_instances`、`workflow_nodes`、`workflow_node_dependencies` 或 `workflow_wakeup_outbox`。如果 root fan-out / child execution / root finalize 能由现有 Job kernel 表和 `job_aggregates` 的少量 lineage 字段表达，就不应为了 workflow 概念本身新增表。

MVP 真正需要补的事实是 child Job 归属和公开可见性：

```text
job_aggregates.root_job_id
job_aggregates.parent_job_id
job_aggregates.is_internal
job_aggregates.workflow_node_key
```

这些字段分别解决：

- root Job 如何找到 internal child Jobs。
- child Job 如何知道自己的父 Job。
- 公共 `GET /jobs/{job_id}` 如何拒绝暴露 internal child Job。
- 同一 root 下同一 leaf node 如何避免重复创建 child Job。

### 自索引聚合

Workflow MVP 使用自索引聚合，而不是新增子 Job 映射表。

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

`job_aggregates` 仍然是 Job 状态事实源；`root_job_id`、`parent_job_id` 和 `workflow_node_key` 只是让同一张事实表可以高效表达 root -> child 查询。这样不会出现 `child_jobs.status` 和 `job_aggregates.status` 两套状态互相冲突。

建议索引：

```text
index(root_job_id)
index(parent_job_id)
unique(root_job_id, workflow_node_key) where workflow_node_key is not null
index(root_job_id, status)
```

其中 `unique(root_job_id, workflow_node_key)` 是幂等约束，不只是查询优化。它保证重复 root orchestration step、重复 reconciler 或并发进程不会为同一个 root node 创建多个 child Job。

常见排查查询：

```sql
-- 查看一个 root Job 下的所有 internal child Jobs
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

```sql
-- 查看一个 root Job 下失败的 child Jobs
select
  id,
  workflow_node_key,
  error
from job_aggregates
where root_job_id = :root_job_id
  and is_internal = true
  and status = 'failed';
```

只有当这类查询已经有明确性能瓶颈，或需要一个可重建的运维 read model 时，才评估新增 child Job 索引表。即使未来新增，它也不能成为 Job 状态事实源。

## 新增表判断规则

新增表前至少回答四个问题：

1. 这张表保存的事实，现有核心表是否已经能表达？
2. 如果没有这张表，崩溃恢复、幂等、执行互斥、重试或副作用投递会缺失哪一步？
3. 这张表是否会驱动状态推进？如果不会，它是否只是辅助表或 read model？
4. 这张表能否从现有核心事实重建？如果能，默认不要让核心流程依赖它。

只有答案指向一个不可替代的事实源时，才应该新增核心表。
