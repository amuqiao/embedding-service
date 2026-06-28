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

## 先建立几个心智模型

读这份文档时，先把 Job kernel 拆成几个机制，而不是把所有表当成一条长链路。

| 心智模型 | 要点 |
|---|---|
| Job 是对外资源 | 调用方提交、查询、callback 和 billing 都以 public root Job 为入口 |
| Attempt 是执行权 | worker 不直接消费 Job，而是消费 `attempt_id`；只有领取当前 active attempt lease 的 worker 才能推进执行 |
| Outbox 是副作用意图 | Taskiq publish 和 Callback delivery 都先落库，再由 publisher / recovery 投递 |
| Retry 跟随 attempt purpose | 谁的 attempt 失败，就只按该 attempt 的 `purpose` 和 retry policy snapshot 判断是否创建下一次 attempt |
| Retry 分三类 | 执行 attempt retry、dispatch publish retry、callback delivery retry 是三套机制，互不等价 |
| Recovery 是补偿扫描 | recovery 不重新定义业务状态，只根据数据库事实补发、补建、补终结或标记失活 |

这几个机制的边界比具体数值更重要。`MAX_ACTIVE_JOBS`、timeout、retry delay 等数值可以调整；但“先落库、再发布、worker 持 lease、失败由状态机收敛、recovery 只补偿事实缺口”是当前 Job kernel 的基本工作方式。

## 主流程一图

先看主流程，再看 workflow 分支。普通 non-workflow Job 当前由 public root Job 自己执行；workflow root Job 的 root attempt 只负责编排，真实业务执行落到 internal child Job 的 attempt。

```text
提交阶段
  POST /jobs
    -> client_request_id 幂等检查
    -> MAX_ACTIVE_JOBS 容量门禁
    -> 创建 public root Job + active attempt + dispatch_outbox

发布阶段
  dispatch_outbox
    -> publisher 发布 jobs.run_attempt(attempt_id)
    -> publish 失败或长期未被 claim 时由 recovery 重发同一 attempt_id

执行阶段
  worker 收到 attempt_id
    -> claim 当前 active attempt lease
    -> non-workflow: 直接执行 root Job executor / model call
    -> workflow: root attempt 只创建 ready child Jobs
    -> child attempt 执行 leaf / root_or_leaf executor

终态阶段
  Job succeeded / failed
    -> 写 result 或 error
    -> 如配置 callback，写 callback_outbox
    -> callback 投递失败只影响 callback 状态，不改变 Job 终态
```

## 核心表分层

一图流中的持久化事实按职责分层：

```text
job_submission_keys      提交幂等
job_aggregates           Job 聚合事实和 root/child lineage
job_execution_attempts   单次 worker 执行尝试、lease、heartbeat、retry state
dispatch_outbox          Taskiq worker task 发布意图和 publish retry
callback_outbox          root Job 终态 callback 投递意图和 delivery retry
job_audit_events         排障时间线，不参与状态推进
```

Transactional Outbox 本体只要求业务事实和待发布消息意图在同一个数据库事务提交。当前 Job kernel 还有其它核心表，是因为异步 Job 还必须处理重复提交、重复消费、worker 崩溃、heartbeat、重试、callback 和恢复。

## 表职责

| 表 | 角色 | 是否核心 | 不承担 |
|---|---|---|---|
| `job_aggregates` | Job 聚合事实源，保存状态、进度、结果、错误和 root/child lineage | 是 | 不保存每次执行尝试、dispatch publish 或 callback delivery 的 retry 状态 |
| `job_submission_keys` | 提交幂等键，保证同一 caller 的 `client_request_id` 可拒重或返回已有 Job | 是 | 不表示执行状态，不发布消息 |
| `job_execution_attempts` | 单次执行尝试，持有 lease、worker、heartbeat、attempt 状态和失败原因 | 是 | 不是审计历史表，不能从核心流程移除 |
| `dispatch_outbox` | 从数据库事务可靠发布 Taskiq 任务的 outbox | 是 | 不发布 callback，不表达 Job 业务终态 |
| `callback_outbox` | Job 终态 Callback 的投递账本和重试队列 | 是 | 不改变 Job 终态，不发布 worker 任务 |
| `job_audit_events` | 内部审计事件和排障时间线 | 辅助 | 不作为恢复、幂等或状态推进依据 |

## 关键机制速查

### 容量门禁

`MAX_ACTIVE_JOBS` 是提交阶段的容量门禁，检查对象是当前 active Job 数：`queued` Job 加上仍持有 active attempt 的 `running` Job。这个计数不按 caller、`job_type` 或 root/child 分组；active internal child 也会计入。workflow root 已完成 orchestration、正在等待 child 且 `active_attempt_id=null` 时，不计入这个门禁。检查在创建新 Job、initial attempt 和 dispatch outbox 之前执行。

```text
active_jobs < MAX_ACTIVE_JOBS
  -> 允许创建 Job

active_jobs >= MAX_ACTIVE_JOBS
  -> 拒绝本次创建
  -> 返回 QUEUE_FULL / HTTP 503
  -> 不创建 job_aggregates、job_execution_attempts 或 dispatch_outbox
```

`MAX_ACTIVE_JOBS=0` 表示禁用这个检查。这个开关只控制接单上限，不会杀掉已经存在的 Job，也不会改变 worker 并发。

### 执行重试

执行重试以 `job_execution_attempts` 为单位，不是简单重跑同一条消息。重试作用域由 `attempt.job_id` 决定：失败 attempt 属于哪条 Job，就只判断并重试那条 Job。

```text
attempt running for Job X
  -> 执行失败或 lease 超时
  -> mark current attempt failed
  -> 如果 retry_eligible 且 attempt policy snapshot 未耗尽
       为 Job X 创建 next attempt
       Job X 回到 queued
       写新的 dispatch_outbox
     否则
       Job X failed
       如配置 callback，写 callback_outbox
```

是否允许执行重试由 attempt 自己持有的 retry policy snapshot 决定：

| 层 | 当前事实 |
|---|---|
| `attempt.purpose` | 区分 `workflow_orchestration` 与 `business_execution` |
| `attempt.policy_max_attempts` | 控制该 purpose chain 最多有几次 attempt |
| `attempt.policy_retryable_error_codes` | 控制哪些错误可自动进入下一 attempt |
| `attempt.retry_chain_id` / `previous_attempt_id` | 记录同一 purpose retry chain |

当前默认策略是：`business_execution` 不自动业务重跑，`policy_max_attempts=1`；`workflow_orchestration` 默认 `policy_max_attempts=3`，用于补偿编排自身的可靠性缺口。dispatch publish retry 和 callback delivery retry 不消耗 execution attempt policy。

按运行形态展开后，当前语义是：

| 失败位置 | 失败对象 | 是否进入 execution attempt retry 判断 | 可重试时重试谁 | 是否自动重跑整个 workflow |
|---|---|---|---|---|
| 普通 non-workflow Job 执行失败 | public root Job 的业务 attempt | 是 | 这条 public root Job | 不适用 |
| workflow root 编排失败 | public root Job 的 orchestration attempt | 是 | root orchestration attempt；用于补齐 ready child 创建和 dispatch 缺口 | 否 |
| workflow child 执行失败 | internal child Job 的业务 attempt | 是 | 失败的 child Job | 否 |
| child 最终 failed 后 root 被标记 failed | workflow root terminal projection | 否 | 不自动重试 | 否 |

因此 workflow root 的 `failed` 需要区分两种来源：

```text
Root orchestration attempt failed
  root 的 active attempt 仍在编排阶段失败
  -> 如果该 root orchestration attempt 的 policy snapshot 未耗尽，只重试 root 编排 attempt
  -> 编排重试依赖 child lineage / workflow_node_key 幂等补齐缺口
  -> 不表示重建所有 child，也不表示重跑已成功 child

Workflow terminal failed
  root orchestration attempt 已成功，root 正在等待 child
  某个 required child 最终 failed
  -> workflow reconciler 把 root 投影为 failed
  -> 这是聚合终态收敛，不触发 root execution attempt retry
  -> 不自动重跑 root、全部 child 或已成功 child
```

换句话说，retry budget 属于具体 attempt purpose chain；worker 处理的是 attempt；attempt 失败时只看该 attempt 的 `purpose` 和 policy snapshot。workflow 的 root 终态失败不是一个新的 root 执行 attempt 失败信号。

### 恢复机制

Recovery 是周期性补偿扫描。它不替代 worker，也不重放已成功的业务结果；它只根据核心表里的状态缺口继续推进。

| 扫描对象 | 修复的问题 | 收敛动作 |
|---|---|---|
| due / orphan dispatch | Taskiq 发布失败、发布后长期无人 claim、dispatch lease 过期 | 重新发布同一个 `attempt_id` |
| stale running attempt | worker 崩溃、heartbeat 停止、attempt lease 过期 | 标记 attempt failed；可重试时创建下一 attempt，否则 Job failed |
| workflow root | child 终态后 root 没有继续推进、ready child 缺失、root terminal projection 漏执行 | 调用 workflow reconciler 创建 child 或终结 root |
| missing callback outbox | root Job 已终态但 callback 投递意图缺失 | 补建 callback outbox |
| due callback | callback 失败后到达下次重试时间，或 callback lease 过期 | 重新投递 callback |
| stale AI ledger pending | AI 调用账本长期 pending | 标记为 failed / unknown，避免 billing read model 长期不收敛 |

因此 recovery 的边界是“补偿已落库事实”，不是“根据外部世界猜测业务是否成功”。如果 provider 实际已经完成但本服务没有可信 terminal 事实，recovery 不会凭空补出成功结果。

### Timeout / Lease 边界

当前已生效的执行等待边界分成两条：AI 调用等待由 `MODEL_CALL_TIMEOUT_SECONDS` 控制；attempt 执行权由 lease / heartbeat 和派生的 stale running threshold 控制。

```text
AI 调用
  MODEL_CALL_TIMEOUT_SECONDS
    -> 截断单次 provider 调用等待

Attempt 执行权
  worker claim / heartbeat
    -> lease_expires_at
    -> recovery 扫描 stale running attempt
    -> mark attempt failed / retry / Job failed
```

`worker_soft_time_limit`、`worker_hard_time_limit` 和 `job_stale_running_seconds` 由 `MODEL_CALL_TIMEOUT_SECONDS` 派生，用于保持配置不变量和 stale running 接管窗口单调递增。当前运行路径实际用到的是 `job_stale_running_seconds` 作为 attempt lease window；soft / hard time limit 不是独立 env，也不要当作单独运维旋钮配置。

| 边界 | 当前作用 |
|---|---|
| `MODEL_CALL_TIMEOUT_SECONDS` | 截断单次 AI provider 调用等待 |
| attempt lease / heartbeat | 让多 worker 和 recovery 判断谁仍有执行权 |
| `job_stale_running_seconds` | attempt lease window；lease 过期后 recovery 才接管 stale running attempt |
| `worker_soft_time_limit` / `worker_hard_time_limit` | 派生保护窗口和配置不变量；当前不作为独立 env 配置 |

### Callback 重试边界

Callback retry 与 Job 执行 retry 是两套机制。Job 到达 `succeeded` 或 `failed` 后，业务终态已经确定；Callback 失败只更新 `callback_outbox`，不会回写 Job 业务终态。

```text
Job terminal
  -> callback_outbox pending
  -> delivery succeeds
       callback delivered
     delivery fails and attempts remain
       callback retrying / pending
     delivery attempts exhausted
       callback failed / dead letter

Job status 不回退，不因为 callback 失败变成 failed
```

## Job 聚合根

公开调用方提交得到的是 public root Job。调用方查询、callback、billing 和最终结果都以 root Job 为入口。

当前已经落地两种执行形态：

| 形态 | 当前行为 |
|---|---|
| 普通 non-workflow Job | public root Job 直接持有 active attempt 并由 worker 执行 |
| workflow Job | public root Job 持有 frozen `workflow_plan`，root attempt 只做 orchestration，实际执行由 internal child Jobs 完成 |

注意：普通 non-workflow Job 当前还不是 `root + one child`。把所有对外 Job 统一成 root 聚合根、实际执行统一落到 internal leaf child，是后续计划，不是 current fact。

## 运行时身份速查

`role` 描述 `job_type` 的设计用途；某条 Job 实例运行时到底是 root 还是 child，仍看 `job_aggregates` 的 lineage 字段。

```text
job_type role = 设计用途
  root          对外入口 / 聚合根
  leaf          workflow 内部执行节点
  root_or_leaf  可直提，也可被 workflow 复用

Job instance = 运行时身份
  root_job_id=null + workflow_node_key=null      public root Job
  root_job_id=R    + workflow_node_key=node_key  internal child Job

job_execution_attempts = 某条 Job 的一次执行尝试
  job_id 可能指向 public root Job
  job_id 也可能指向 internal child Job
```

因此直接提交 `root_or_leaf` job_type 时，它是 public root Job；被 workflow 创建时，它是 internal child Job。`root_or_leaf` 不表示“只有一个 Job”，也不表示自动创建 root + leaf。

## Job Type 目录元数据

`JobTypeSpec` 当前包含 `visibility` 和 `role` 两个目录元数据，用于开发者、运维和脚本理解 job_type 的入口性质：

| 字段 | 当前取值 | 说明 |
|---|---|---|
| `visibility` | `public` / `demo` / `internal` | `public` 是正式业务入口；`demo` 是模板示例、smoke 或压测入口；`internal` 预留给只供服务内部使用的 helper job_type |
| `role` | `root` / `leaf` / `root_or_leaf` | `root` 是聚合根入口；`leaf` 是 workflow child executor；`root_or_leaf` 表示可直提也可被 workflow 复用 |

这两个字段是 registry/catalog intent；当前外部提交准入由 `visibility` 决定，不由 `role` 决定。`APP_ENV=local/dev` 允许外部提交 `public` 和 `demo`；`APP_ENV=test/prd` 只允许外部提交 `public`。`internal` 只供服务内部 workflow child 使用，任何环境都不能被外部直接提交。运行时 root/child lineage 只由 `job_aggregates.root_job_id` 和 `workflow_node_key` 表达。

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
| `poster_title_image_style_probe` | `internal` | `leaf` |
| `poster_title_image_generate_item` | `internal` | `leaf` |
| `poster_title_image_join` | `internal` | `leaf` |

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
- **重试事实**：每次尝试有独立 `purpose_attempt_no`、状态、错误、开始/结束时间、policy snapshot 和 retry decision。

所以它不只是历史记录。如果把它降级为审计表，worker 多副本、重复消息、超时恢复和按 attempt 重试都会缺少事实源。

## Retry / Timeout 配置边界

Job 执行重试不是全局 `.env` 开关。当前重试事实由 `job_execution_attempts` 保存；是否创建下一次 attempt，由当前 attempt 的 `purpose` 和 retry policy snapshot 决定。默认 `business_execution` 不自动业务重跑；`workflow_orchestration` 有独立可靠性重试预算。

当前 `.env.example` 暴露的是少量稳定控制意图：

| 配置 | 当前含义 |
|---|---|
| `MODEL_CALL_TIMEOUT_SECONDS` | AI 调用主 timeout；代码由它派生 worker timeout 链和 stale running 阈值 |
| `MAX_ACTIVE_JOBS` | active Job 接单上限；超出时创建请求返回繁忙 |
| `CALLBACK_TIMEOUT_SECONDS` | Callback 单次 HTTP 请求超时 |

这些派生项不作为生产配置暴露，也不接受 env 覆盖：

| 配置 | 不暴露原因 |
|---|---|
| `WORKER_SOFT_TIME_LIMIT` | 由 `MODEL_CALL_TIMEOUT_SECONDS` 加内部 buffer 派生，避免调用 timeout 和 worker timeout 倒挂 |
| `WORKER_HARD_TIME_LIMIT` | 由 soft timeout 加内部 buffer 派生 |
| `JOB_STALE_RUNNING_SECONDS` | 由 hard timeout 加内部 buffer 派生，保证 recovery 晚于 worker 硬超时 |
| dispatch publish retry 参数 | 存入 `dispatch_outbox` policy snapshot，不作为通用 env 旋钮 |
| callback delivery retry 参数 | 存入 `callback_outbox` policy snapshot，不作为通用 env 旋钮 |

这些旧键或不支持的通用旋钮不属于当前应用配置合同；出现在 `.env` 或 `ENV_FILE` 这类应用配置文件时会 fail-fast，而不是静默降级：

| 配置 | 拒绝原因 |
|---|---|
| `JOB_MAX_EXECUTION_ATTEMPTS` | 全局执行重试会绕过 job_type 幂等性、成本和副作用差异；当前按 job_type 声明 |
| `MODEL_CALL_MAX_RETRIES` | provider 调用重试会影响成本、账本和幂等边界；当前不是通用配置合同 |
| `JOB_ORPHAN_TIMEOUT_SECONDS` / `JOB_DISPATCH_MAX_PUBLISH_ATTEMPTS` | dispatch publish retry 是可靠性内部策略，落到 outbox policy snapshot，不进入通用 env 模板 |
| `CALLBACK_MAX_DELIVERY_ATTEMPTS` / `CALLBACK_RETRY_DELAY_SECONDS` | callback delivery retry 是可靠性内部策略，落到 outbox policy snapshot，不进入通用 env 模板 |
| `JOB_RECOVERY_INTERVAL_SECONDS` / `JOB_RECOVERY_BATCH_SIZE` / `JOB_RECOVERY_CALLBACK_BATCH_SIZE` | recovery 扫描节奏和批大小是代码默认内部参数，不进入通用 env 模板 |

新增或调整 Job 配置时，应优先暴露业务可理解的主控变量；worker timeout、stale running、callback claim window 等联动值由 `Settings` 统一派生并做 fail-fast 校验。

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
  workflow_node_key
  status
  result
  error
```

字段含义：

| 字段 | 当前含义 |
|---|---|
| `root_job_id` | `NULL` 表示 public root；非 `NULL` 表示 child 归属的 public root Job |
| `workflow_node_key` | child 在 root 内的 leaf node 幂等身份；public root 必须为 `NULL` |

`root_job_id` 不表达 DAG 执行依赖。当前 workflow child 都直接挂在 root 下；`chain`、`chord` 等顺序关系只存在 frozen `workflow_plan.nodes[].depends_on` 中。workflow 任务模型和依赖语义以 [`workflow-kernel.md`](workflow-kernel.md) 为准。

## 自索引聚合

Workflow 使用自索引聚合，而不是新增 child Job 映射表。`job_aggregates` 仍然是 Job 状态事实源；`root_job_id` 和 `workflow_node_key` 只是让同一张事实表可以表达 root -> child 查询。这样不会出现 `child_jobs.status` 和 `job_aggregates.status` 两套状态互相冲突。

关键索引：

```text
index(root_job_id)
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
