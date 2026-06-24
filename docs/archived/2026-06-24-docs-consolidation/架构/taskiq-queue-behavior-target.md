# TaskIQ 队列行为目标设计

```text
Status: Target Opinion / Plan
Owner: architecture
Scope: TaskIQ broker behavior, dispatch publisher, worker claim, callback delivery, recovery boundary
Current truth: code, tests, docs/current/job-kernel.md
Pairs with archived historical design: docs/archived/2026-06-24-docs-consolidation/架构/transactional-outbox-job-kernel-data-model.md
```

本文定义生产级 Job kernel 第一阶段的 TaskIQ 队列行为目标。当前代码事实仍以代码、测试和 [`project-standards-code-facts.md`](project-standards-code-facts.md) 为准；本文不改变当前实现事实。

当前生命周期状态权威仍以 [`job-lifecycle-state-model.md`](job-lifecycle-state-model.md) 为准。对新项目和大重构目标而言，本文与 [`transactional-outbox-job-kernel-data-model.md`](transactional-outbox-job-kernel-data-model.md) 在 dispatch 权威上覆盖当前实现：目标模型不再让 `job_execution_attempts` 承担 publish ledger，TaskIQ publish 事实归属独立 `dispatch_outbox`。

第一阶段只考虑 7 张表：

```text
job_submission_keys
job_aggregates
job_execution_attempts
dispatch_outbox
callback_outbox
job_audit_events
ai_call_ledger_entries
```

本设计与 [`transactional-outbox-job-kernel-data-model.md`](transactional-outbox-job-kernel-data-model.md) 配套使用。数据模型文档回答“事实源放在哪张表”；本文回答“TaskIQ broker、publisher、worker 和 recovery 如何使用这些事实源”。

## 真实需求

表面诉求是选择 Redis 还是 RabbitMQ。底层架构问题是：在严格 `Transactional Outbox` 下，队列只是执行触发通道，不能成为 Job 状态、执行权、回调投递或计费事实源。

生产级要求不是“TaskIQ 可以投递消息”，而是必须能解释这些失败窗口：

- DB 已提交但 broker publish 失败。
- broker publish 成功但 `dispatch_outbox` 未标记成功。
- broker 重复投递同一 `attempt_id`。
- Worker 已 claim 后崩溃。
- broker ACK 语义不足导致消息丢失。
- callback 投递失败或重复投递。
- AI provider 已调用成功但 Job 后续失败。

## 当前基线

当前实现使用 `taskiq-redis` 的 `ListQueueBroker` 和 `REDIS_URL`。这是本地模板默认实现，不是本文的生产目标。

TaskIQ 官方文档列出 `taskiq-aio-pika` 的 `AioPikaBroker`、`taskiq-redis` 和 `taskiq-nats` 等 broker；官方 getting started 也建议生产 broker 优先考虑 `taskiq-aio-pika` 或 `taskiq-nats`，Redis 更适合作为 result backend。`taskiq-redis` README 明确说明 `ListQueueBroker` 不支持 acknowledgements，Worker 处理期间被杀时消息可能丢；`RedisStreamBroker` 支持 acknowledgements，才适合有数据耐久要求的场景。RabbitMQ 文档则把 consumer acknowledgements 和 publisher confirms 作为消息数据安全的重要机制。

## 成熟模式

| 问题 | 成熟模式 | 本设计采用方式 |
|---|---|---|
| DB 事务与队列 publish 不一致 | Transactional Outbox | `dispatch_outbox` 同事务记录待发布意图；publisher 异步发布。 |
| broker at-least-once / 重复消息 | Idempotent Consumer | Worker 只按 `attempt_id` claim 当前 active attempt。 |
| Worker 崩溃 | Lease + Heartbeat + Reconciler | `job_execution_attempts` 是执行权和恢复权威。 |
| publish 不确定窗口 | Retryable Outbox + CAS | publish 成功但标记失败时允许重复 publish，Worker 幂等收敛。 |
| callback 副作用 | Separate Callback Outbox | `callback_outbox` 独立投递、重试和 dead letter。 |
| AI usage / cost | Append-only Ledger | `ai_call_ledger_entries` 记录 provider call 事实，不靠 Job 状态推导。 |

## Broker 裁决

目标裁决：

| 环境 | 推荐 broker | 裁决 |
|---|---|---|
| 本地开发 / 模板 smoke | Redis `ListQueueBroker` 可保留 | 只用于本地便利；不得声明为生产可靠队列。 |
| 第一阶段生产基线 | Redis `RedisStreamBroker` | 推荐默认；保留现有 Redis 运维面，同时升级到支持 ack 的 Stream broker。 |
| 生产升级项 | RabbitMQ `AioPikaBroker` | 只有出现 broker-native 多队列 QoS、routing、priority、delayed delivery、publisher confirm 运维控制点等硬需求时再升级。 |

这个裁决不是否定 RabbitMQ 的生产价值，而是避免第一阶段过早引入第二套运维面。七张表已经把 retry、delay、dead letter、幂等消费、stale recovery 和 callback delivery 的业务权威收敛到 PostgreSQL；因此第一阶段最小生产可信路径是保留 Redis 依赖但升级到支持 ACK 的 Stream broker。RabbitMQ 是后续 QoS 和 routing 能力升级，不是 Job kernel 正确性的前置条件。

不建议：

- 不把 Redis `ListQueueBroker` 作为生产目标。
- 不用 TaskIQ result backend 保存业务结果。
- 不把 broker message id、delivery tag、RabbitMQ DLQ 或 Redis Stream pending entry 作为 Job 状态真源。
- 不使用 broker retry 表达业务 retry；业务 retry 必须创建新的 `job_execution_attempts` 和新的 `dispatch_outbox`。
- 不把 RabbitMQ 作为第一阶段强制依赖；七张表已经承载 retry、delay、dead letter、幂等消费和 stale recovery 的权威。

## 七张表职责边界

| 表 | 队列行为中的职责 | 禁止承担 |
|---|---|---|
| `job_submission_keys` | 外部 submit 幂等入口；重复提交返回已有 Job 或幂等冲突。 | 不发布消息；不参与 worker retry；不保存 broker 状态。 |
| `job_aggregates` | 对外 Job 投影；保存 `public_status`、`active_attempt_id`、公开进度、结果、错误、callback 摘要。 | 不保存 broker message id、consumer tag、delivery tag、provider usage 明细。 |
| `job_execution_attempts` | Worker claim、lease、heartbeat、attempt 终态和 retry decision。 | 不保存 publish attempts、next publish time 或 callback delivery 状态。 |
| `dispatch_outbox` | TaskIQ publish 意图、publisher lease、retry、dead letter、最小 dispatch payload。 | 不表达 Worker 是否已经执行；不保存 callback HTTP 投递状态。 |
| `callback_outbox` | Job 终态 callback payload、签名版本、delivery lease、retry、dead letter。 | 不改变 Job 业务终态；不复用 `dispatch_outbox`。 |
| `job_audit_events` | 追加记录 submit、dispatch、claim、terminal、callback、ledger 关键事件。 | 不反向驱动任何状态迁移。 |
| `ai_call_ledger_entries` | AI provider call、usage、cost estimate、pricing ref、billable status 事实。 | 不决定 Job 是否 succeeded；不触发 broker retry 重放 provider call。 |

## 队列与状态关系

系统状态权威顺序：

```text
PostgreSQL rows > TaskIQ broker message > worker log > TaskIQ result backend
```

具体规则：

- `dispatch_outbox` 是“应发布”的权威。
- TaskIQ broker 是“尝试触发执行”的通道。
- `job_execution_attempts` 是“谁有执行权”的权威。
- `job_aggregates.active_attempt_id` 防止旧消息、重复消息和晚到 Worker 覆盖新状态。
- `callback_outbox` 是“终态通知副作用”的权威。
- `ai_call_ledger_entries` 是“已经发生的模型调用和成本”的权威。

## Dispatch Publisher 行为

Publisher 只处理 `dispatch_outbox`，不直接读取 `job_submission_keys` 或 callback 表。

```text
loop
  -> DB transaction
       select due dispatch_outbox rows
       where status in (pending, retrying)
       and next_attempt_at <= now
       for update skip locked
       mark leased with lease_token and lease_expires_at
     commit

  -> publish TaskIQ task jobs.run_attempt(attempt_id)

  -> DB transaction
       if publish succeeded and lease token matches:
         mark dispatch_outbox published
         append job_audit_events(dispatch.published)
       if publish failed and lease token matches:
         increment publish_attempts
         set retrying + next_attempt_at
         or mark dead_letter when exhausted
         append job_audit_events(dispatch.publish_failed/dead_lettered)
     commit
```

关键约束：

- 不在 DB transaction 内执行网络 publish。
- 不持有 DB row lock 等待 broker。
- `leased` 必须有短 lease；publisher 崩溃后由 lease 到期恢复。
- publish payload 只允许包含 `attempt_id`、dedupe key、routing label 等最小信息。
- 如果 publish 已进入 broker 但标记 `published` 失败，允许后续重复 publish；Worker 幂等处理。
- `dispatch_outbox.dead_letter` 是应用 dead letter；RabbitMQ DLQ 或 Redis pending 只能作为 broker 侧运维信号。

## TaskIQ Message 合同

`jobs.run_attempt` 的 TaskIQ message payload 必须保持最小：

```text
attempt_id
```

允许作为 label / header 的信息：

```text
queue_name
priority
dedupe_key
trace_id
```

禁止放入 broker message：

```text
full job params
prompt
provider credentials
callback payload
AI usage / cost
object storage internal secret
```

原因：broker message 不是事实源，可能重复、延迟、丢失或被运维工具截取。完整入参、运行计划、结果和计费事实必须由 DB row、对象存储引用或 ledger 承载。

## Worker Claim 行为

Worker 收到 `attempt_id` 后，必须先 claim，再执行。

```text
TaskIQ message(attempt_id)
  -> DB transaction
       load job_execution_attempts + job_aggregates
       require job_aggregates.active_attempt_id = attempt_id
       require job_aggregates.public_status = queued
       require job_execution_attempts.status = pending
       mark attempt running
       set worker_id, lease_token, lease_expires_at, heartbeat_at
       mark job running
       append job_audit_events(attempt.claimed)
     commit

  -> if claim failed:
       return skipped

  -> execute JobExecutor
  -> heartbeat while running
  -> terminal DB transaction
```

终态写回必须校验：

```text
active_attempt_id == attempt_id
lease_token matches
attempt status == running
job public_status == running
```

Worker 对 broker ACK 的要求：

- 第一阶段生产基线必须使用支持 ACK 的 broker；Redis 场景必须使用 `RedisStreamBroker`，RabbitMQ 场景必须使用 manual ack 或等价可靠消费语义。
- TaskIQ task 只有在 DB claim/terminal 边界完成后才应正常返回。
- Worker 进程崩溃时，broker redelivery 是加分项，不是唯一恢复路径。
- 即使 broker 不 redeliver，也必须依赖 `job_execution_attempts.lease_expires_at` 由 recovery 收敛。

## Broker Retry 与业务 Retry

必须区分两类 retry：

| Retry 类型 | Owner | 行为 |
|---|---|---|
| publish retry | `dispatch_outbox` | broker 不可用、publish timeout、confirm failed 后重试同一 `attempt_id`。 |
| execution retry | `job_execution_attempts` + `job_aggregates` | attempt 执行失败且可重试时，创建新 attempt 和新 dispatch outbox。 |
| callback retry | `callback_outbox` | callback endpoint 失败后重试同一 callback event。 |
| provider retry | AI gateway policy + `ai_call_ledger_entries` | 只在 provider 调用前或明确安全窗口重试；provider 已成功后不得靠队列重放。 |

不允许：

- TaskIQ broker-level retry 自动重跑完整业务逻辑并创建隐式新 attempt。
- 一个 `attempt_id` 执行失败后靠 broker redelivery 表达业务 retry。
- provider 成功但 ledger terminal 写入失败时，靠 TaskIQ retry 重放 provider call。

## Broker 调度能力边界

| 能力 | 第一阶段权威 | Broker 只能做 |
|---|---|---|
| ACK | DB terminal transaction + attempt lease | transport completion；不能推进 Job 状态。 |
| publisher confirm | `dispatch_outbox.status` | 如果 RabbitMQ 启用，只能帮助判断 publish 是否可标记 `published`。 |
| prefetch | DB claim、attempt lease、worker concurrency 配置 | worker 流控；不能表达 `MAX_ACTIVE_JOBS`、SLA 或 retry 上限。 |
| priority | DB due-row 选择顺序和 `job_aggregates.priority` | 可选 one-way projection；不能反向读取 broker 排队结果作为业务真相。 |
| delay | `dispatch_outbox.next_attempt_at` / `callback_outbox.next_attempt_at` | 可选 transport optimization；不能替代 outbox due time。 |
| dead letter | `dispatch_outbox.dead_letter` / `callback_outbox.dead_letter` | broker DLQ 只做 transport poisoning、unroutable、TTL、overflow 等运维信号。 |

## RabbitMQ 升级行为要求

RabbitMQ 不是第一阶段默认基线。只有明确需要 broker-native 多队列 QoS 隔离、routing、priority、delayed routing，或必须用 publisher confirm 作为运维控制点时，才升级为 `AioPikaBroker` + RabbitMQ。

一旦选择 RabbitMQ，至少要冻结这些要求：

| 能力 | 要求 | 应用侧事实源 |
|---|---|---|
| durable queue / message | 队列和消息必须能承受 broker 重启，具体配置需在实现时用 `taskiq-aio-pika` 参数验证。 | `dispatch_outbox` 仍是最终恢复真源。 |
| publisher confirm | publisher 只有在 broker 接受 publish 后才能标记 `dispatch_outbox.published`。 | `dispatch_outbox.status`。 |
| manual ack / redelivery | Worker 崩溃时 broker 应能重新投递未 ack message。 | `job_execution_attempts` claim / lease 仍负责幂等和恢复。 |
| prefetch | 必须限制单 worker 未完成消息数量，避免长任务把内存打满。 | `WORKER_CONCURRENCY` 和 worker lease。 |
| routing / queue | 第一阶段保持少量固定队列，不按 tenant 或 job_id 动态建无界队列。 | `dispatch_outbox.task_name` / label。 |
| priority | 只作为调度优化，不作为业务公平性或 SLA 的唯一事实。 | `job_aggregates.priority` 或后续调度策略。 |
| broker DLQ | 只能作为 broker 运维辅助，不作为应用 dead letter 真源。 | `dispatch_outbox.dead_letter`。 |

## Redis 行为要求

Redis 是第一阶段生产基线，但必须区分两档：

| Broker | 裁决 | 要求 |
|---|---|---|
| `ListQueueBroker` | 本地开发可用，生产不接受 | 不支持 ack，Worker 崩溃时消息可能丢；必须靠 DB recovery 扫描补偿。 |
| `RedisStreamBroker` | 第一阶段生产基线 | 必须启用 ack 语义，监控 pending / consumer group，仍不得把 Redis pending 当 Job 真源。 |

Redis 生产最低要求：

- 使用 `RedisStreamBroker`，不用 `ListQueueBroker`。
- Redis 必须有持久化、高可用、内存上限和 eviction policy 审核。
- recovery 频率必须覆盖消息丢失和 pending 卡住窗口。
- 监控 pending entries、consumer lag、publish failure、worker heartbeat lag。

## Callback 投递行为

`callback_outbox` 不复用 `dispatch_outbox`。Callback 是终态通知副作用，不是 Job execution attempt。

第一阶段必须使用独立 callback publisher loop：

```text
loop
  -> claim due callback_outbox rows with for update skip locked
  -> sign frozen callback payload
  -> POST callback_url
  -> delivered / retrying / dead_letter
  -> update job_aggregates.callback_summary
  -> append job_audit_events(callback.delivered/failed/dead_lettered)
```

不采用“每次 callback delivery 都是一条 TaskIQ 任务”的设计。若为了复用进程池必须借 TaskIQ，也只能发送内部 nudge / scan due callbacks 任务：

- TaskIQ message 只能触发扫描 due `callback_outbox` rows。
- message 不携带 callback payload、retry plan、dead-letter decision 或 callback attempt number。
- `callback_outbox` 仍是 delivery lease、retry、dead letter 真源。
- broker retry 不能替代 `callback_outbox` retry。

## AI Ledger 与队列边界

AI provider 调用必须由 `ai_call_ledger_entries` 记录事实：

```text
before provider call:
  insert ai_call_ledger_entries(status=pending)

provider success:
  update ledger usage/cost/status

provider failure:
  update ledger failed/unbillable/diagnostic
```

队列边界：

- TaskIQ 只触发 attempt execution，不拥有 usage / cost。
- `job_aggregates` 可以展示 billing 摘要投影，但不能成为计费事实源。
- provider 已经返回成功后，TaskIQ retry 不得重放 provider call。
- ledger terminal 更新失败只能由 ledger reconciler 或人工核对处理，不能自动重跑 LLM。

## 故障矩阵

| 故障 | 事实源 | 收敛方式 |
|---|---|---|
| API 写入 Job 后崩溃 | `dispatch_outbox.pending` | publisher 后续发布。 |
| publish 失败 | `dispatch_outbox.retrying` | backoff 后重试；耗尽进入 `dead_letter`。 |
| publish 成功但标记失败 | `dispatch_outbox.leased/retrying` | lease 到期后重复 publish；Worker claim 幂等。 |
| broker message 丢失 | `dispatch_outbox` + `job_execution_attempts` | published orphan recovery 或 lease recovery 重发。 |
| broker 重复投递 | `job_execution_attempts` + `active_attempt_id` | 只有一次 claim 成功，其余 skipped。 |
| Worker claim 前崩溃 | `dispatch_outbox.published` 或 broker redelivery | orphan recovery 重发或 broker redelivery。 |
| Worker claim 后崩溃 | `job_execution_attempts.lease_expires_at` | recovery 标记失败并按策略创建新 attempt。 |
| 旧 Worker 晚到写终态 | active attempt + lease token | terminal CAS 失败，不覆盖新状态。 |
| callback endpoint 失败 | `callback_outbox.retrying/dead_letter` | callback retry 或 dead letter；Job 终态不变。 |
| AI provider 成功但 Job 失败 | `ai_call_ledger_entries` | billing 仍显示已发生 usage。 |

## 不采用的设计

- 不把 TaskIQ result backend 当业务结果存储。
- 不让 broker ACK 状态成为 Job 状态。
- 不把 RabbitMQ DLQ 当 `dispatch_outbox.dead_letter` 的替代品。
- 不使用动态无限队列表达 tenant、caller 或 job_id 隔离。
- 不让 TaskIQ retry 负责业务 retry。
- 不在 broker message 中携带完整 Job payload。
- 不让 callback delivery 使用 `dispatch_outbox`。

## 最小可信落地路径

1. 冻结本队列行为文档和七张表状态集合。
2. 引入 broker factory，当前默认 `redis_stream`；`redis_list` 只允许显式本地便利模式；`rabbitmq` 作为后续可演进目标，不在第一阶段实现。
3. 新增 `dispatch_outbox` publisher loop，所有 `jobs.run_attempt` publish 只从该 loop 发起。
4. Worker 只接受 `attempt_id`，执行前必须 claim `job_execution_attempts`。
5. 新增 callback publisher loop，独立处理 `callback_outbox`。
6. 禁用或收敛 TaskIQ broker-level retry，避免它绕过业务 attempt retry。
7. 增加 recovery：dispatch lease recovery、published orphan recovery、execution lease recovery、callback delivery recovery、ledger pending recovery。
8. 增加故障注入测试和 ops 查询。

## 验收标准

- `POST /jobs` 事务提交后，即使 broker 不可用，也能通过 `dispatch_outbox` 观察到待发布意图。
- dispatch publisher 崩溃在 publish 前、publish 后、mark published 前三个窗口都能收敛。
- 同一 `attempt_id` 被重复投递时，只有一个 Worker claim 成功。
- Worker claim 后崩溃时，lease 到期 recovery 能创建新 attempt + 新 dispatch outbox 或终态失败。
- `callback_outbox` 投递失败耗尽后进入应用 dead letter，Job 业务终态不变。
- 禁用 TaskIQ result backend 后，Job 查询、Callback 查询和 Billing 查询仍然完整。
- `ai_call_ledger_entries` 中 provider success 的 usage / cost 不因 Job 失败而丢失。
- Redis `ListQueueBroker` 不允许出现在生产 profile；Redis Stream 的 ack / redelivery 行为必须有集成测试覆盖。
- 如果启用 RabbitMQ，publisher confirm、manual ack、prefetch、priority、delay 和 DLQ 都必须证明只是 transport 行为，不成为业务状态真源。

## 需要验证

- `taskiq-aio-pika` 当前版本如何配置 durable queue、persistent message、publisher confirm、prefetch、priority 和 delayed delivery。
- `taskiq-redis` 当前版本的 `RedisStreamBroker` consumer group、ack、pending recovery 和队列命名行为。
- TaskIQ worker 对异常、timeout、进程 kill、broker reconnect 的 ack / nack / redelivery 实际语义。
- 当前 `JobExecutor` 是否完全只依赖 `attempt_id` 获取执行上下文。
- 独立 callback publisher 应部署为独立进程、worker side loop，还是后续由内部 nudge task 触发扫描。

## 参考依据

- TaskIQ available brokers：官方列出 `AioPikaBroker`、Redis broker、NATS broker 等 broker，并说明 custom broker 包由 TaskIQ 开发者维护。
- TaskIQ getting started：官方示例使用 `AioPikaBroker` 连接 RabbitMQ，并建议生产 broker 优先考虑 `taskiq-aio-pika` 或 `taskiq-nats`。
- `taskiq-redis` README：`ListQueueBroker` 不支持 acknowledgements；`RedisStreamBroker` 支持 acknowledgements，适合需要数据耐久性的场景。
- `taskiq-aio-pika` README：支持 delayed messages、message priorities、multiple queues 和 custom routing。
- RabbitMQ acknowledgements / publisher confirms 文档：consumer acknowledgements 和 publisher confirms 是消息数据安全机制。
