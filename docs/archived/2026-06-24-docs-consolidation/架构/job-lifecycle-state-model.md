# Job 生命周期状态模型与 Dispatch 权威

```text
Status: Current Internal Contract
Owner: job-kernel
Scope: Job, Attempt, Dispatch, Callback outbox, Recovery, AI call ledger projection
Current truth: code, tests, docs/current/job-kernel.md
Historical lifecycle designs are not maintained as current authority.
```

本文冻结当前代码已经落地的 Job 生命周期状态权威。公开 HTTP、Job、Callback、Billing envelope 的对外合同以 [AI Job 服务合同边界](service-contract-boundary.md) 为准；本文只定义内部状态机、dispatch 归属、恢复入口和故障矩阵。

如果本文与代码、测试或 [AI Job 服务项目规范与骨架（代码事实版）](project-standards-code-facts.md) 冲突，以 current truth 为准。`Plan`、`Candidate` 和早期设计文档不能覆盖本文的 current internal contract。

本文中的 `JobEnvelope`、`Callback` 和 `BillingEnvelope` 行只用于说明内部状态到公开投影的映射；公开字段、错误和兼容性语义仍由 [AI Job 服务合同边界](service-contract-boundary.md) 拥有。

## 架构决策

当前采用成熟的 `state machine + transactional dispatch outbox + idempotent worker claim + lease/CAS + callback outbox + AI call ledger rows + billing read model` 组合。

当前 dispatch 权威冻结在 `dispatch_outbox`。一个 execution attempt 对应一条 `jobs.run_attempt` dispatch intent；同一 `attempt_id` 可以因为初次发布、publish failure recovery 或 published orphan recovery 被多次 publish 成物理 broker message，但这些消息都指向同一个 attempt 语义。`job_execution_attempts` 只表达执行权、lease、heartbeat 和执行终态，不保存 publish attempts、next publish time 或 publish error。

## 状态权威

| 事实源 | 权威职责 | 非权威边界 |
|---|---|---|
| `job_submission_keys` | Submit 幂等事实和 request fingerprint 绑定。 | 不控制 Job 生命周期状态。 |
| `job_aggregates` | 对外 Job 状态、进度、结果、错误、callback 摘要和 active attempt。 | 不保存 Taskiq publish ledger 或 AI usage 明细。 |
| `job_execution_attempts` | Worker claim、lease、heartbeat、执行终态和执行 retry。 | 不保存 Taskiq publish attempts。 |
| `dispatch_outbox` | Taskiq publish 意图、publisher lease、publish retry 和应用级 dead letter。 | 不表达 Worker 是否已经执行。 |
| `callback_outbox` | 终态 Callback 投递账本、投递 lease、retry、dead letter、幂等 `event_id`。 | 不改变 Job 业务终态；`job_aggregates.callback_*` 只是摘要投影。 |
| `job_audit_events` | 状态迁移和排障审计。 | 不作为状态真值源。 |
| `ai_call_ledger_entries` | 每次 AI provider call 的 ledger、usage、cost estimate、scope 归属和诊断状态。 | 不控制 Job 状态；`BillingEnvelope` 只是读取投影。 |

`job_audit_events` 当前不能作为状态真值源；事件只用于审计，不用于驱动迁移或生成公开状态。

## 状态集合

| 对象 | 当前代码实际产生状态 | DB / schema 兼容状态 | 说明 |
|---|---|---|---|
| `Job.status` / `JobEnvelope.job_status` | `queued`、`running`、`succeeded`、`failed` | DB check 与公开 schema 对齐。 | 对外只暴露小集合。取消不是当前公开合同。 |
| `JobAttempt.status` | `pending`、`running`、`succeeded`、`failed` | DB check 与当前迁移集合对齐。 | `pending` 表示 attempt 已创建、等待对应 dispatch message 被 worker 消费；publish 状态在 `dispatch_outbox`。 |
| `DispatchOutbox.status` | `pending`、`leased`、`published`、`retrying`、`dead_letter` | 与 DB check 对齐。 | `published` 表示 broker publish 成功，不表示 Worker 已执行。 |
| `CallbackOutbox.status` | `pending`、`leased`、`delivered`、`retrying`、`dead_letter`、`skipped` | 与 DB check 对齐。 | `skipped` 是内部 outbox 状态，不直接作为公开 callback status。 |
| `JobEnvelope.callback.status` | `not_configured`、`pending`、`delivering`、`delivered`、`retrying`、`failed` | schema 当前不包含 `skipped`。 | 服务视图会把内部 `skipped` 映射为 `not_configured` 或 `failed`。 |
| `AiCallLog.status` | `pending`、`succeeded`、`failed` | 与 DB check 对齐。 | ORM 类名暂保留 `AiCallLog`，物理表为 `ai_call_ledger_entries`。 |
| `AiCallLog.billable_status` | `pending`、`billable`、`unknown` | DB 还允许 `not_billable`。 | 当前无 AI call 的 scope 不创建 ledger 行，由 billing read model 返回 `not_billable`。 |
| `AiCallLog.cost_calculation_status` | `pending`、`estimated`、`failed`、`not_applicable` | 与 DB check 对齐。 | 失败调用可能是 `not_applicable` 或 `failed`。 |
| `BillingEnvelope.status` | `estimated`、`not_billable`、`incomplete`、`failed` | 与 schema 对齐。 | 只读投影，不反向修改 ledger 或 Job。 |

文档和测试必须区分“DB 允许集合”和“当前代码实际迁移集合”。未实现状态不得写成当前生效能力。

## Submit 链

Owner：

- `app/services/jobs.py:create_job`
- `app/services/jobs.py:submit_job_request`
- `app/repositories/job_repo.py:create`
- `app/repositories/job_repo.py:create_submission_key`
- `app/repositories/job_repo.py:create_initial_attempt`
- `app/repositories/job_repo.py:create_dispatch_outbox`

当前迁移：

| 步骤 | 事务边界 | 权威写入 | 失败语义 |
|---|---|---|---|
| 鉴权、caller、`job_type`、schema、callback、容量校验 | HTTP 请求内 | 无持久状态 | 校验失败不创建 Job。 |
| 幂等锁与 Job 创建 | DB transaction | `job_submission_keys`、`job_aggregates.status=queued`，请求快照，runtime snapshot，callback 摘要 | 相同 `caller_id + client_request_id` 按幂等模式返回已有 Job 或冲突。 |
| 初始 attempt 创建 | 同一 DB transaction | `job_execution_attempts.status=pending`，`job_aggregates.active_attempt_id`，`job_aggregates.attempt_count=1` | 事务失败时没有可恢复 Job。 |
| dispatch intent 创建 | 同一 DB transaction | `dispatch_outbox.status=pending`，`next_attempt_at=now`，payload 只含 `attempt_id` | DB commit 后即使 API 崩溃，recovery 仍可发布。 |
| 提交后 publish | DB commit 之后 | Taskiq broker message carries `attempt_id` | publish 不在创建事务内。 |
| publish 成功记录 | 独立 DB transaction | `dispatch_outbox.status=published`，`publish_attempts += 1`，`next_attempt_at=orphan deadline` | 记录失败时可能形成 uncertain publish，由 claim/CAS 和 recovery 收敛。 |
| publish 失败记录 | 独立 DB transaction | `dispatch_outbox.status=retrying` 或 `dead_letter`，`publish_attempts += 1`，`last_error`，`next_attempt_at` | 记录成功时创建接口仍返回已创建 Job；recovery 后续重发。 |

`submit_job_request` 当前只在 publish failure 已被记录为 `TaskiqPublishDeferredError` 时返回已创建 Job。若 publish 异常没有成功写入恢复账本，异常会向上暴露，避免 silent success。

## Dispatch 链

Dispatch 是独立 outbox 状态，不是 Job 或 execution attempt 状态。

| Dispatch 状态 | 含义 | `next_attempt_at` 含义 | owner / 入口 |
|---|---|---|---|
| `pending` | dispatch intent 已持久化，尚未确认 broker publish。 | 下次允许 publish 的时间；`null` 或到期可被 recovery 扫描。 | `create_dispatch_outbox`。 |
| `leased` | publisher 已领取该 dispatch intent，正在执行 broker publish。 | 不参与 due 判断；由 `lease_expires_at` 恢复。 | `lease_dispatch_for_publish`。 |
| `published` | broker publish 已成功记录，等待 worker claim。 | orphan recovery deadline；到期表示 published 后长期未被 claim，可重发。 | `mark_dispatch_published`。 |
| `retrying` | publish 失败但未耗尽最大尝试次数。 | 下次允许 publish 的时间。 | `mark_dispatch_publish_failed`。 |
| `dead_letter` | publish 尝试耗尽。 | 不再自动 publish。 | `mark_dispatch_publish_failed`。 |

当前 claim 规则只允许 worker 从 `pending` execution attempt claim：

```text
Job.status = queued
Job.active_attempt_id = attempt.id
Attempt.status = pending
row lock / active attempt condition passes
```

这是对 at-least-once broker 的幂等消费设计：重复消息、晚到消息和 recovery 重发消息依靠 active attempt、status、lease token 和 execution token 收敛。

## Execution 链

Owner：

- `app/tasks/jobs.py:run_job_attempt`
- `app/jobs/runner.py:execute_job`
- `app/repositories/job_repo.py:claim_attempt_for_execution`
- `app/repositories/job_repo.py:heartbeat_attempt`
- `app/repositories/job_repo.py:update_progress`
- `app/repositories/job_repo.py:mark_succeeded`
- `app/repositories/job_repo.py:mark_failed`
- `app/repositories/job_repo.py:mark_attempt_succeeded`
- `app/repositories/job_repo.py:mark_attempt_failed`

当前迁移：

| 迁移 | CAS / lock 条件 | 权威写入 | 恢复语义 |
|---|---|---|---|
| `pending attempt -> running` | active attempt、`Job.status=queued`、attempt row lock | attempt lease、worker、heartbeat、`Job.status=running`、`execution_token=str(attempt_id)`、`execution_attempts += 1` | 未 claim 的消息可重发；已 claim 的消息由 lease 管理。 |
| running progress update | `job_id + execution_token + execution_generation` | `job_aggregates.progress_*`、`updated_at` | stale generation 返回未更新，不覆盖新 attempt。 |
| running Job -> succeeded | `Job.status=running`、execution token 命中 | `job_aggregates.status=succeeded`、公开 / canonical result、finished_at、terminal callback outbox | Callback 继续独立投递；Job 终态不可被旧消息覆盖。 |
| running attempt -> succeeded | attempt running、active attempt、lease token 命中、Job 已 succeeded | attempt terminal fields，清 lease | 与 Job 成功同一成功路径内提交。 |
| running Job -> failed | `Job.status in queued/running`，可带 execution token | `job_aggregates.status=failed`、公开错误、finished_at、terminal callback outbox | Callback 继续独立投递。 |
| running attempt -> failed, retryable | attempt running、active attempt、可选 lease token 命中、attempt_count 未耗尽 | old attempt failed；new attempt pending；Job 回到 queued；`execution_generation += 1`；创建新 `dispatch_outbox`；清 execution token / error / finished_at | recovery 或 publish 链继续处理新 active attempt。 |
| running attempt -> failed, terminal | attempt running、active attempt、可选 lease token 命中、attempt_count 耗尽或不可重试 | attempt failed；Job failed；terminal callback outbox | Job 终态不可被 callback 或旧 worker 改变。 |

当前 `run_job_attempt` 捕获执行异常后会按 `JobTypeSpec.platform_retry_policy` 判断是否允许创建下一 attempt，最终仍受 `max_attempts` 约束。当前策略只允许显式平台超时类错误进入 retry；更完整的 error classification、provider 特有重试、usage attribution 和真实 LLM 重试边界属于 Phase 4 后续硬化范围。

成功前副作用在 Job 终态成功之前运行。`run_success_side_effect` 失败时，当前路径将 Job 标记为 failed 并触发 terminal callback。已经发生的 AI provider call 不会因此从 billing ledger 中消失。

## Callback 链

Callback 是终态副作用投递账本，不是 Job 终态的一部分。

Owner：

- `app/repositories/job_repo.py:ensure_terminal_callback_outbox`
- `app/repositories/job_repo.py:mark_callback_delivering`
- `app/repositories/job_repo.py:mark_callback_result`
- `app/tasks/jobs.py:deliver_callback_for_job`
- `app/services/callbacks.py:deliver_callback`

当前迁移：

| Outbox 状态 | 含义 | Job 摘要投影 |
|---|---|---|
| `pending` | 终态事件需要投递，尚未被 delivery worker lease。 | `job_aggregates.callback_status=pending`。 |
| `leased` | delivery worker 正在投递，持有 outbox lease。 | `job_aggregates.callback_status=delivering`。 |
| `delivered` | 接收方返回合法 ACK。 | `job_aggregates.callback_status=delivered`。 |
| `retrying` | 本次投递失败但仍可重试。 | `job_aggregates.callback_status=retrying`，并写 `callback_next_retry_at`。公开视图显示 `retrying`。 |
| `dead_letter` | 投递次数耗尽。 | `job_aggregates.callback_status=failed`。 |
| `skipped` | 已配置 callback，但未订阅该终态事件时的审计结果。 | 公开视图不会暴露 `skipped`；按当前映射显示为 `not_configured` 或 `failed`。 |

Callback retry、dead letter、lease 过期和 ACK 校验失败都只修改 `callback_outbox` 与 `job_aggregates.callback_*` 摘要，不改变 `job_aggregates.status`、`job_result` 或 `job_error`。

`callback_outbox.delivery_attempts` 只统计已经完成 HTTP 投递调用并进入结果落账的次数。`leased` 只表示 worker 已领取投递权，不消耗投递次数；如果进程在 lease 后、HTTP POST 前崩溃，lease 到期后 recovery 可以重新领取，不会误入 dead letter。

## Recovery 链

Owner：

- `app/tasks/recovery.py`
- `app/repositories/job_repo.py:find_due_dispatches`
- `app/repositories/job_repo.py:find_stale_running_attempts`
- `app/repositories/job_repo.py:find_due_callbacks`
- `app/repositories/job_repo.py:cleanup_expired_jobs`

当前 recovery 使用 PostgreSQL advisory lock `job_recovery_loop` 做单飞协调。恢复正确性依赖权威表、row lock、active attempt、lease 和 CAS 条件，不依赖单实例部署。

当前扫描：

| 扫描 | 查找条件 | 收敛动作 |
|---|---|---|
| dispatch due outbox | `dispatch_outbox.status in pending/retrying/leased` 且到期或 lease 过期，关联 `Job.status=queued`、active pending attempt | 提交当前事务后调用 `publish_job_attempt(attempt_id)` 重发。 |
| stale running attempts | `Job.status=running`、active attempt、attempt running、lease expired | `mark_attempt_failed`，错误码 `JOB_TIMEOUT`，`error_kind=timeout`，`failure_phase=lease`，可重试时创建下一 attempt。 |
| due callbacks | Job 已终态，outbox `pending/retrying/leased` 且到期或 lease 过期 | 提交当前事务后调用 `deliver_callback_for_job(job_id)`。 |
| expired jobs | 已收敛且到期的 Job | 释放对应 `job_submission_keys` 后 soft delete；不硬删执行、dispatch、callback 或 ledger 事实表。 |

Recovery 不拥有业务事实，只按权威表和 repository transition 修复状态。即使 recovery 重复运行，正确性也必须依赖 row lock、active attempt、lease 和 CAS 条件，而不是依赖单实例部署。

## AI Call Ledger 与 Billing 子链

AI gateway / runtime 是 Job kernel 的消费者能力；Job kernel 不拥有 provider usage 或 cost 事实。

Owner：

- `app/services/ai_gateway_facade.py:generate_text_with_ledger`
- `app/repositories/ai_call_log_repo.py`
- `app/services/billing.py`

当前迁移：

| 场景 | Ledger 写入 | Billing 投影 |
|---|---|---|
| 调用 provider 前 | 创建 `ai_call_ledger_entries.status=pending`，保存 scope、model、pricing、request hash。 | 若此状态短期残留，scope billing 为 `incomplete`；超过 `JOB_STALE_RUNNING_SECONDS + 60s` 后由 recovery 收敛。 |
| provider 成功、usage 和 cost 成功 | `status=succeeded`，`billable_status=billable`，`cost_calculation_status=estimated`，冻结 usage / cost。 | `estimated`。 |
| provider timeout / failed | `status=failed`，`billable_status=unknown`，`cost_calculation_status=not_applicable`。 | `incomplete`，因为是否 billable 未知。 |
| usage 缺失或 pricing 计算失败 | `status=failed`，`billable_status=unknown`，`cost_calculation_status=failed`。 | `failed`，`diagnostic_reason=cost_calculation_failed`。 |
| scope 无 AI call rows | 无 ledger 行。 | `not_billable`。 |

Job billing 查询只在 Job 到达 `succeeded` 或 `failed` 后开放。Billing read model 不反向修改 Job、attempt、callback 或 provider 调用结果。

当前已知边界：provider 调用已经成功，但 ledger terminal update 失败时，代码会抛不可自动重试的 `AI_LEDGER_UPDATE_FAILED`。该错误不会触发 Job platform retry；recovery 只能把长期 pending ledger 行收敛为 failed / unknown，不能用“重放真实 provider 调用”修复该缺口或自动补出 cost。

## 故障矩阵

| 故障 | 当前状态事实 | 恢复 / 收敛路径 | 公开结果 |
|---|---|---|---|
| 调用方在创建响应前 HTTP 超时，但 DB commit 已完成 | `job_aggregates=queued`，initial attempt 和 dispatch outbox 已创建 | 调用方用同一 `caller_id + client_request_id` 触发幂等语义 | 返回已有 Job 或冲突，取决于 idempotency mode。 |
| publish 失败且失败记录成功 | `dispatch_outbox=retrying`，写 `last_error` 和 `next_attempt_at` | recovery 到期重发 | Job 仍 `queued`。 |
| Taskiq message 已进入 broker，但 `dispatch_outbox.published` 未写回 | dispatch 可能仍是 `leased` 或 `retrying` | worker 可从 active pending attempt claim；recovery 可能重发；CAS 防止重复终态 | 不扩散内部不确定性。 |
| published 后长期未 claim | `dispatch_outbox=published` 且 orphan deadline 到期 | recovery 重发 publish | Job 仍 `queued`。 |
| 重复 Taskiq message | 同一 `attempt_id` 多次到达 | 只有第一个满足状态和 lock 的 worker claim 成功 | 旧消息跳过或无效。 |
| worker claim 后崩溃 | Job running，attempt running，lease 最终过期 | recovery 把旧 attempt 标记 failed；可重试时创建下一 active attempt | Job 回到 `queued` 或终态 `failed`。 |
| 旧 worker 晚到写 progress / terminal | execution token、generation、active attempt 或 lease 不匹配 | repository transition 返回未更新或抛状态冲突 | 不覆盖新 attempt 或终态。 |
| 成功前副作用失败 | Job running，provider call 可能已发生 | Job 标记 failed，terminal callback outbox 创建 | Job `failed`；billing 仍可能有 billable call。 |
| Callback endpoint 失败 | Job 已终态，outbox failed 或 dead_letter | callback retry / dead letter；Job 终态不变 | Job status 不变，callback 摘要变化。 |
| Callback worker 崩溃 | outbox leased，lease 最终过期 | recovery 重新领取 due callback | Job status 不变。 |
| AI ledger 存在 pending / unknown | `ai_call_ledger_entries` 未收敛或是否 billable 未知 | recovery 把超时 pending 收敛为 failed / unknown；billing read model 显示 incomplete | Job status 不变，billing `incomplete`。 |
| provider 成功但 ledger terminal update 失败 | pending ledger row 可能残留，模型调用已真实发生 | 抛不可自动重试的 `AI_LEDGER_UPDATE_FAILED`；recovery 后仍保持 unknown，不重放 provider call | Job 可能 failed；billing 可能 incomplete。 |

## 验收边界

当前已由测试覆盖的核心语义包括：

- commit 后 publish、publish failure 记录成功时仍返回已创建 Job。
- `mark_dispatch_published` 写 recovery deadline。
- recovery 重发 due dispatch。
- running attempt 失败后按 `max_attempts` 创建下一 active attempt。
- stale running recovery 收敛并避免 peer 已 claim 时覆盖。
- stale execution generation 不写进度。
- Job 成功路径同时终结 attempt。
- Callback delivery failure 不改变 Job 终态。
- Job scope billing 对 pending / unknown / cost failed ledger 行给出 `incomplete` 或 `failed` 投影。
- recovery 会把超时 pending AI call ledger 行收敛为 failed / unknown。

后续应补齐或强化：

- uncertain publish 全链路测试：broker message 已到达但 `dispatch_outbox.published` 未写回时，只能 claim 一次并保持终态幂等。
- stale worker 晚到 terminal write 的显式 repository / workflow 测试。
- `job_audit_events` 仅作审计的测试或文档 guard，避免把事件当状态源。
- job_type retry policy、platform error classification 和真实 LLM retry 边界。

## 实现演进规则

- 新增公开 Job 状态必须先升级 `JobEnvelope` 合同、OpenAPI、测试和调用方文档。
- 新增内部 Attempt 状态必须同步 DB check、repository transition、recovery 查询、event 和测试。
- Dispatch 相关查询只能以 `dispatch_outbox` 为权威；不得把 publish 状态重新引入 execution attempt 或 Job aggregate。
- Callback 投递只能以 `callback_outbox` 为权威；`job_aggregates.callback_*` 仅用于公开摘要。
- Billing 只能以 `ai_call_ledger_entries` 为权威；不得把 usage / cost 写入 `job_aggregates` 作为事实源。
- 未接入主路径的表、字段或早期设计状态不得写成 current contract；需要保留历史背景时只能放在历史设计文档中。
