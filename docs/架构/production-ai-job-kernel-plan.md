# 生产级 AI Job Kernel 重构计划

```text
Status: Plan
Owner: architecture
Scope: contract boundary, lifecycle model, Job kernel, AI gateway/runtime adapter, AI ledger/billing, migration verification
Current truth: code, tests, docs/架构/project-standards-code-facts.md
```

本文是后续生产级重构计划，不是当前实现事实。当前实现事实以代码、测试和 [`project-standards-code-facts.md`](project-standards-code-facts.md) 为准。

## 最终目标

把本项目收敛成一个生产级 AI Job 生命周期内核：

- 对调用方，只暴露稳定 HTTP envelope、Job envelope、Callback envelope、Billing envelope 和少量可版本化状态。
- 对内部，实现可靠 submit、dispatch、claim、run、retry、terminal、callback、recovery 和 cleanup 生命周期。
- 对新增 `job_type`，提供注册式、可验证、可演进的 Job plugin 合同，而不是散落的自定义解析和执行分支。
- 对 AI 能力，保持 AI gateway / runtime adapter 与 Job kernel 解耦；Job 是 AI gateway 的消费者，不是 billing 或 provider 调用事实源。
- 对计费，使用 AI call ledger rows + billing read model；Job billing 只是 `scope_type="job"` 的投影，不进入 Job 主状态。

## 架构选择

本轮采用成熟的 `state machine + transactional boundary + outbox/lease/reconciler + registry plugin + ledger rows + read model` 组合。

| 关注点 | 成熟模式 | 本项目取舍 |
|---|---|---|
| Job 状态 | 显式状态机和受控迁移 | 外部状态保持 `queued/running/succeeded/failed`，内部细化 attempt、dispatch、callback 和 recovery。 |
| 可靠投递 | Transactional outbox 或等价提交后发布账本 | 当前已有 `job_attempts` publish 字段和 recovery；Phase 2 先冻结 dispatch 状态权威和 outbox 归属，再进入实现硬化。 |
| Worker 执行权 | Lease、heartbeat、CAS 写回、幂等 consumer | 继续以 attempt lease、execution token 和 active attempt 约束保护终态写回。 |
| 新增 Job 类型 | Registry / plugin contract | 保留 `JobExecutor`，先升级 metadata、runtime、retry 和 side effect 声明；AI usage / cost 归属声明等 gateway 和 ledger invariant 冻结后再进入 plugin 合同。 |
| AI 调用 | Gateway facade + provider adapter | Gateway 只处理模型能力、provider 调用、usage 和 ledger，不控制 Job 状态机。 |
| Billing | Ledger rows + read model | `ai_call_logs` 是事实源；当前行先创建 `pending` 再原地更新为 terminal；`BillingEnvelope` 是投影；不新增独立财务事实表。 |
| 迁移 | Versioned contracts + compatibility tests | 先冻结合同和生命周期，再改 kernel；每阶段保留公开 API 兼容。 |

不采用的方案：

- 不把本项目改成通用 DAG / workflow engine；首轮不做 `job_steps`、chain/group/chord 或 step-level recovery。
- 不把 billing 写入 `jobs` 表顶层字段；`JobEnvelope` 不默认携带 usage/cost。
- 不用某个 `job_type` 私有字段临时定义 cost、usage、error envelope 或 callback envelope。
- 不把 LiteLLM Proxy 的 virtual key、team budget、fallback chain 暴露为本服务公共合同。
- 不为了“成熟”新增宽泛 `app/domain/` 或双套接口适配层。

## 当前事实基线

本节只摘录 current truth 作为后续重构背景，不自建新的事实源；如果与代码、测试或 `project-standards-code-facts.md` 冲突，以 current truth 为准。

已落地事实：

- HTTP 成功响应由中间件包装为 `code/msg/data/request_id/server_time` envelope；route `response_model` 仍声明裸 `DataSchema`。
- 当前公开 Job 状态是 `queued`、`running`、`succeeded`、`failed`。
- `POST /api/v1/ai-jobs/jobs` 以 `caller_id + client_request_id` 做幂等语义，创建 `jobs` 和初始 `job_attempts` 后提交事务，再发布 Taskiq attempt。
- Taskiq worker 消息只携带 `attempt_id`；worker 领取 active attempt，写入 lease、heartbeat 和 running 状态。
- Job 成功 / 失败通过 repository 受控迁移写回；callback 使用 outbox 和投递 lease。
- `JobExecutor` 和 `job_type` registry 已存在，当前强制校验 params、runtime fields、canonical result 和 public result schema；callback、错误码和日志 metadata 已在 `JobTypeSpec` 中表达，但治理强度仍需在后续阶段继续硬化。
- `ai_call_logs`、AI gateway facade、pricing cost estimate、`GET /api/v1/ai-jobs/jobs/{job_id}/billing` 和 `BillingEnvelope` 已落地首个 Job scope 计费路径。
- `scripts/real-flow.sh` 是手动真实 LLM 流程入口，必须显式 `--confirm-cost`。

已知差距：

- 部分设计文档仍混有目标态和旧实现描述，需要持续按 current / contract / plan 分层对账。
- 生命周期状态权威已经冻结在 [`job-lifecycle-state-model.md`](job-lifecycle-state-model.md)；Phase 3 仍需把其中的剩余硬化项落到代码和测试，例如 uncertain publish 全链路、stale terminal write 和 retry policy。
- submit publish 可靠性已经确定由 active `job_attempts` 承载 attempt-backed dispatch ledger；Phase 3 继续硬化 publish / recovery 测试，不再把 dispatch outbox 归属作为开放架构选择。
- `JobExecutor` metadata 已初步覆盖 execution mode、platform retry policy 和 side effect policy；resource profile、compatibility version、AI/provider usage attribution 与 cost policy 必须等出现真实 consumer 或 AI gateway / ledger 语义冻结后再进入 plugin 合同。
- AI gateway / runtime adapter 当前内部边界已经冻结在 [`ai-gateway-runtime-boundary.md`](ai-gateway-runtime-boundary.md)；provider 成功后的 ledger terminal 更新失败已冻结为不可自动重试。usage normalization 和 provider error taxonomy 仍属后续工作。
- Billing 已有 Job scope read model 和最小 stale pending ledger recovery；retention/export 规则和非 Job scope 公开合同仍未开放。
- metrics 和全量结构化日志仍未落地。

## 合同边界

重构前先冻结这些边界，避免后续把内部实现泄漏给调用方。

| 边界 | 对外合同 | 内部 owner |
|---|---|---|
| HTTP envelope | `code/msg/data/request_id/server_time` | `app/main.py` middleware 和 exception handlers |
| Job envelope | `data.job`，只表达 Job 当前投影 | `jobs`、`job_attempts`、`JobRepo`、`JobExecutor.public_result()` |
| Error envelope | 注册错误码、HTTP status、公开 msg、details | `app/core/error_registry.py`、`app/core/exceptions.py` |
| Callback envelope | 不套 HTTP envelope，携带终态 `JobEnvelope` | `callback_outbox`、`app/services/callbacks.py` |
| Billing envelope | `data.billing`，从 ledger 聚合 | `ai_call_logs`、`app/services/billing.py` |
| AI runtime result | 模型输出、usage、provider error normalized | AI gateway facade、provider adapter |

合同原则：

- `JobEnvelope` 不承诺 attempt、worker、lease、provider 原始响应或 billing 明细。
- `BillingEnvelope` 不承诺 Job 生命周期状态，也不决定 Job 成功或失败。
- `ErrorEnvelope.data` 当前直接承载 details 或 `null`；统一 `ErrorDetail` 嵌套结构属于后续合同升级，不在本阶段偷改。
- Callback 是否携带 billing 不是默认能力；未来若需要必须与轮询合同同步升级。
- 通用 scope billing 查询、caller 时间窗口聚合、同步 AI chat/API 都是独立 HTTP 合同，不因已有 ledger 自动开放。

## 生命周期模型

生命周期需要拆成六条可验证子链，而不是只看 `job_status`：

| 子链 | 状态事实 | 成熟要求 |
|---|---|---|
| Submit | request validation、idempotency、capacity、initial attempt | 事务内创建权威事实，提交后发布，重复请求可解释。 |
| Dispatch | attempt publish pending/published/failed | 发布失败必须可恢复，不丢 Job，不重复创造语义不同的 Job。 |
| Execution | claim、running、heartbeat、terminal write | 只有持有 lease/token 的 worker 可写进度和终态。 |
| Retry | retryable classification、attempt count、next attempt | 默认不重试真实 LLM；只按 job_type 显式策略和错误分类重试。 |
| Callback | outbox claim、delivery attempt、retry/dead letter | Callback 是副作用投递，不反向改变 Job 终态。 |
| Recovery | stale running、publish failed、callback pending | Reconciler 只按权威表修复状态，不从日志回放事实。 |

外部 Job 状态继续保持小集合；内部状态扩展必须通过 schema、repository transition、event 和测试一起落地。

## 分阶段计划

### Phase 0：current / contract / plan 对账

目标：先停止文档漂移，避免在错误事实上继续重构。

范围：

- 更新 `project-standards-code-facts.md` 中已落地的 billing route、`ai_call_logs`、真实 LLM job 类型和 `real-flow.sh`。
- 修正 AI gateway / billing 设计文档中过期的“尚未实现”描述。
- 新增本文作为计划文档，并在文档地图中标注为 `Plan`。

验证：

- `./scripts/verify.sh check`

提交：

- 单独提交文档事实对账和计划，不混入运行时代码。

### Phase 1：合同边界冻结

目标：冻结公开合同和内部 owner，防止重构时破坏调用方语义。

范围：

- 为 HTTP envelope、Job envelope、Error envelope、Callback envelope、Billing envelope 补一份合同边界文档或对现有文档做 current / target 分层。
- 明确哪些字段是公共合同、哪些字段是内部诊断、哪些只能进入日志或 ops 查询。
- 对 `ErrorEnvelope.data` 的现状做显式记录；是否升级为统一 `ErrorDetail` 放入独立迁移计划。
- 确认 OpenAPI、operation registry、schema registry、error registry 和 route contract tests 覆盖当前公开路由。

验收：

- 任一公开 route 都能从 operation registry 反查 schema、错误码和副作用。
- `JobEnvelope`、`CallbackEnvelope`、`BillingEnvelope` 无重复定义。
- 文档中不再把目标态写成当前事实。

### Phase 2：生命周期模型与 dispatch 权威冻结

目标：把 Job 生命周期从“代码流程”提升为可执行的 transition contract，并先冻结 submit / dispatch 的状态权威。

范围：

- 定义 Job、Attempt、Dispatch、Callback、Billing ledger 的状态归属和迁移表。
- 决定 dispatch outbox 归属：继续把 `job_attempts` publish 字段作为等价 dispatch outbox，或拆出独立 `job_dispatch_outbox`；该决策必须先于 Job kernel 实现硬化。
- 明确每个迁移的 owner、DB lock/CAS 条件、失败后状态、可恢复路径和测试入口。
- 明确 publish failure、worker crash、lease expired、terminal write conflict、callback retry、billing incomplete 的故障矩阵。
- 定义对外 `job_status` 与内部状态的映射，不扩散内部状态到公开合同。
- 冻结结果见 [`job-lifecycle-state-model.md`](job-lifecycle-state-model.md)：当前不拆 `job_dispatch_outbox`，dispatch 权威归属 active `job_attempts`；`reconciler_leases` 当前未接入主 recovery 路径。

验收：

- 每个 transition 都有 repository 方法或明确的未来 owner。
- submit / dispatch / publish failure 的事实源和 recovery 入口已经确定，不留到 Phase 3 再做架构选择。
- 每个 terminal 迁移都有“重复消息晚到”测试。
- 每个 recovery 扫描都有不会破坏终态的测试。

### Phase 3：Job kernel 硬化

目标：把 Job 执行内核收敛为稳定平台能力，新增 job 类型只接 plugin contract。

范围：

- 按 Phase 2 决策继续强化 `job_attempts` publish 字段；只有出现 fan-out、多 dispatch channel、独立 replay / dead letter / retention 或独立 publisher 扩缩容需求时，才重新评估独立 `job_dispatch_outbox`。
- 继续升级 `JobTypeSpec`：当前先落地 execution mode、platform retry policy 和 side effect policy；resource profile、contract version 等字段必须等出现真实 consumer 后再进入合同。
- 将 Job kernel retry 从 `max_attempts + catch all` 继续收敛为显式平台错误分类；AI/provider 特有重试、usage attribution 和 cost policy 等 AI gateway / ledger invariant 冻结后再进入 plugin 合同。
- 把成功 side effect 和 callback outbox 的顺序、失败语义和补偿边界写入 kernel contract。
- 增加低基数日志字段和最小 metrics 设计，不把 prompt、provider 原文、job_id 等高基数字段放入指标标签。

验收：

- 新增 `job_type` 不需要修改通用 Job runner 的分支逻辑。
- dispatch publish failure、worker crash、stale running、terminal conflict、callback retry 都可重复验证。
- registry consistency 能检查 job_type metadata 完整性。

### Phase 4：AI gateway / runtime adapter 边界

目标：让 AI runtime 成为 Job kernel 的消费者能力，不反向污染生命周期。

范围：

- 明确 `JobExecutor`、shared LLM runtime、AI gateway facade、LiteLLM provider adapter 的输入输出边界。
- Provider error 归一化为稳定 `AppError` reason，不把 provider 原始错误暴露进公开合同。
- 确认 `scope_type/scope_id/operation` 由上层能力传入，AI gateway 不假设只有 Job。
- 将 model catalog、prompt template、pricing registry 的启动校验和运行期错误语义分清。
- 明确 provider 调用成功但 ledger terminal 更新失败时的处理策略，避免自动重放已发生的模型调用。

验收：

- Job runtime 可以调用 AI gateway，但 AI gateway 不 import Job kernel 迁移逻辑。
- 非 Job scope 能在内部复用 gateway，但不会自动产生公开 HTTP 查询合同。
- 当前已冻结 provider 成功后的 ledger terminal 更新失败为不可自动重试，且不能重放 provider 调用；完整 ledger reconciler 进入 Phase 5。
- 真实 LLM 验证继续通过 `real-flow.sh --confirm-cost` 手动触发。

### Phase 5：AI ledger / billing

目标：把计费路径稳定为 ledger + read model，而不是多个临时 cost 字段。

范围：

- 定义 `ai_call_logs` ledger 状态机：pending、succeeded、failed、cost failed、billable/unbillable。
- 定义 incomplete / failed billing 的 `diagnostic_reason` 枚举和公开语义。
- 明确 pricing snapshot 冻结规则，历史账本不因当前 pricing 文件变化重算。
- 已落地 ledger reconciler 的最小可靠性边界：扫描超时 `pending` 行并收敛为 failed / unknown；真实 provider 调用已经发生时，只修复账本状态，不重放 provider 调用。
- retention policy、只读导出或 materialized read model 可以按证据分阶段设计。
- 只有当真实查询压力证明需要时，才新增派生 summary；summary 不能成为事实源。

验收：

- `BillingEnvelope` 只从 ledger 聚合。
- 失败 Job 内已发生的 LLM 调用仍可出现在 billing 中。
- 无 AI call 的 Job 返回 `not_billable`，不是伪造 `estimated 0`。
- pending / unknown ledger 行有明确 reconciler 处理路径和 `incomplete` 诊断语义。

### Phase 6：迁移验证

目标：用可重复验证关闭迁移风险。

范围：

- 分阶段 Alembic 迁移，默认保持向后兼容；公开合同变化必须有版本或明确 breaking change 说明。
- 补齐 contract tests、repository transition tests、worker/recovery tests、billing tests 和 real-flow 手动证据。
- 对 publish failure、provider failure、ledger terminal failure、callback endpoint failure 做故障注入测试。
- 更新文档地图，旧设计文档若只保留历史意义，必须标注状态。

验收：

- `./scripts/verify.sh check` 通过。
- 修改 Job lifecycle / Taskiq / DB 时，`./scripts/dev.sh start && ./scripts/verify.sh workflow-smoke && ./scripts/dev.sh stop` 通过。
- 修改真实 LLM / billing 路径时，手动执行对应 `./scripts/real-flow.sh ... --confirm-cost` 并记录简短结论。

## 提交策略

- 每个 phase 单独提交，提交前完成最小必要验证。
- 文档对账、合同冻结、生命周期模型、kernel 代码、AI gateway、billing、迁移验证分别保持单一意图。
- 不把重构计划、运行时代码、迁移和真实 LLM 验证输出混在同一个提交。
- 如果某阶段只改文档，仍运行 `./scripts/verify.sh check`，防止文档变更暴露已有测试或脚本问题。

## 下一阶段入口

Phase 2 完成后，进入 Phase 3：Job kernel 硬化。

Phase 3 的第一批文件应优先检查：

- `docs/架构/project-standards-code-facts.md`
- `docs/架构/service-contract-boundary.md`
- `docs/架构/job-lifecycle-state-model.md`
- `docs/架构/job-ai-billing-mental-model.md`
- `docs/接口层/job-type-extension-standard.md`
- `app/repositories/job_repo.py`
- `app/tasks/jobs.py`
- `app/tasks/recovery.py`
- `app/jobs/runner.py`
- `app/jobs/registry.py`
