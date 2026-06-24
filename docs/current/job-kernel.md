# Job Kernel 当前模型

本文解释当前 Job 可靠执行内核。它不是对外 HTTP 合同；对外字段以 `docs/api/service-contract.md` 和 `app/schemas/` 为准。

## 一句话结论

当前实现不是只有 Transactional Outbox。它采用的是：

```text
Transactional Outbox
+ Lease
+ Heartbeat
+ Idempotent Consumer
+ Retry Attempt State
```

因此核心不只是 `Job` 和 `DispatchOutbox`。`JobAttempt` 属于执行可靠性核心，不是 outbox 模式本身的一部分。

## 表职责

| 表 | 角色 | 是否核心 |
|---|---|---|
| `job_aggregates` | Job 聚合根，对外状态、进度、结果、错误、Callback 汇总状态 | 是 |
| `job_submission_keys` | 提交幂等键，保证同一 caller 的 `client_request_id` 可拒重或返回已有 Job | 是 |
| `job_execution_attempts` | 单次执行尝试，持有 lease、worker、heartbeat、attempt 状态和失败原因 | 是，属于执行可靠性核心 |
| `dispatch_outbox` | 从数据库事务可靠发布 Taskiq 任务的 outbox | 是，属于 dispatch outbox 核心 |
| `callback_outbox` | Job 终态 Callback 的投递账本和重试队列 | 是，属于 callback 可靠投递核心 |
| `job_audit_events` | 内部审计事件和排障时间线 | 辅助，不驱动主流程 |

## Transactional Outbox 解决什么

Transactional Outbox 解决的是“数据库状态已经提交，但消息没有可靠发布”的问题。

```text
API transaction
  insert job_aggregates
  insert job_execution_attempts
  insert dispatch_outbox(pending)
  commit

publisher
  lease due dispatch_outbox row
  publish Taskiq message
  mark dispatch_outbox published
```

如果 API 提交后进程崩溃，publisher/recovery 仍可从 `dispatch_outbox` 继续发布。

## JobAttempt 解决什么

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

## CallbackOutbox 解决什么

`callback_outbox` 是另一个 outbox。它和 `dispatch_outbox` 的副作用目标不同：

```text
dispatch_outbox
  DB -> Taskiq broker

callback_outbox
  DB -> caller callback URL
```

Callback 投递失败不改变 Job 终态。调用方应以 `GET /jobs/{job_id}` 轮询作为兜底，Callback 只负责终态通知。

## 多 Pod 影响

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

## 保留边界

当前选择是保留方案 B：`JobAttempt` 是执行阶段事实源，不能作为“可选增强表”从核心流程里拿掉。

可以按需裁剪的是外围能力，例如：

- 是否启用 callback。
- 是否开放 billing 查询。
- 是否保留长期 audit event。
- 是否增加更细的监控和 dead letter 运维界面。

不应按需关闭的是：

- Job 提交幂等。
- Dispatch outbox。
- Attempt lease/heartbeat。
- Worker 终态写入的 attempt token 校验。
