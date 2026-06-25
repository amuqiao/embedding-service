# Implementation Terminal Acceptance

本文定义 workflow、AI capability 和 cost boundary 开发前后的终态验收门禁。它是计划文档，不描述当前已经实现的能力；当前事实仍以 `docs/current/`、`docs/api/` 和代码为准。

本文的目的不是新增一套架构，而是在开发过程中防止三类偏移：

- workflow 实现绕过 Job kernel。
- AI provider 调用绕过 AI Capability Kernel。
- cost summary、callback 或 Job result 变成新的成本事实源。

## Scope

本文覆盖以下计划的共同验收边界：

| 计划 | 本文验收重点 |
|---|---|
| [`workflow-kernel-design.md`](workflow-kernel-design.md) | root / child Job、DAG、outbox、reconciler、progress/result projection |
| [`ai-capability-enhancement.md`](ai-capability-enhancement.md) | AI facade、provider adapter、usage normalizer、model / prompt / pricing registry 交接点 |
| [`ai-capability-cost-boundary-design.md`](ai-capability-cost-boundary-design.md) | ledger attribution、cost estimate、billing / cost summary projection |
| [`hardening.md`](hardening.md) | 运维硬化 backlog，不阻塞主干但不能与主干合同冲突 |

本文不负责：

- 定义新的公开 API 字段。
- 定义真实扣费、余额、钱包或财务账本。
- 替代 `docs/api/service-contract.md`。
- 替代具体实现阶段的测试文件。

## Phase 0 Readiness

进入 workflow 或 poster title image 实现前，以下合同必须先冻结。

| Gate | 验收要求 |
|---|---|
| Root / child Job visibility | Root Job 是唯一公共查询、callback 和幂等入口；child Job 第一版是内部执行资源；公共 `GET /jobs/{job_id}` 不能让调用方查询到不属于其公共合同的 child Job |
| Ledger attribution | 冻结 descendant AI call 写入 root Job scope 的方式；明确 workflow / node / child Job / attempt 的最小归因字段或等价 scope |
| Job progress | Root Job 对外 progress 是 Job 级单调展示值；child attempt、node retry 和 reconciler 不能让调用方看到 percent 回退 |
| Job result | `allow_partial` 不新增公共 `partially_succeeded`；部分成功只通过 root result summary / item status 表达 |
| Cost projection | terminal polling、terminal callback 和 future `job.cost` 必须使用同一 ledger 聚合逻辑；summary 不是事实源 |
| Callback | Root Job 只发送一次终态 callback；child Job 不发送调用方 callback；callback delivery 失败不改变 Job 终态 |
| Retention | Root Job 查询期、workflow/node 排障期、descendant ledger 保留期、callback outbox 保留期之间的关系已冻结 |
| Hardening scope | `hardening.md` 只保留 operational backlog，不重新打开主干计划的 non-goals |

任一 Gate 未冻结时，不应开始 workflow kernel 或多模态业务接入实现。

## Terminal Acceptance

### Job / Workflow

- Root Job、workflow instance、workflow nodes、dependencies 和 first wakeup intent 在同一事务提交。
- Leaf node 创建 child Job 时，child Job、child attempt、dispatch outbox 和 node `execution_job_id` 在同一事务提交。
- Workflow terminal、root Job terminal、root result projection、terminal cost projection intent 和 root callback outbox 在同一终态投影路径中收敛。
- 重复 orchestrator tick、重复 child terminal apply、重复 reconciler run 不会重复创建 child Job、重复推进 node 或重复发送 root callback。
- Join / finalize node 在并发触发下只执行一次。
- Reconciler 能修复 ready node 卡住、running node 已终态未应用、join 条件满足未执行、workflow 已终态但 root Job 未终态等状态。
- Child Job 第一版不作为外部调用方可查询资源；如果实现无法阻止公共查询命中 child Job，不允许发布 workflow kernel。

### AI Capability

- 业务 Job 和 workflow node 只通过 AI facade 调用 provider。
- Provider adapter 不写数据库，不拼 Job、Callback 或 Billing envelope。
- Provider raw usage 必须先经 UsageNormalizer 转成 typed usage，再交给成本边界。
- Model catalog、prompt refs、pricing refs 和 capability constraints 在启动、worker 启动或 `./scripts/verify.sh check` 中 fail-fast。
- 当前文本路径保持兼容；多模态能力只能在真实 adapter、usage normalizer 和 pricing rule 准备好后启用。

### Cost Boundary

- Provider 调用前必须先创建 pending ledger；pending 创建失败时不得调用 provider。
- Provider 已被调用后，不能通过重放 provider 调用修复 ledger。
- Ledger terminal update 失败必须进入可诊断 incomplete / failed 状态，并能由 recovery 或人工排障收敛。
- `GET /jobs/{root_job_id}/billing` 能覆盖 descendant AI call ledger。
- Terminal callback、terminal polling 和 future `job.cost` 使用同一 cost summary projection。
- `job.cost`、callback payload、Job result summary、workflow summary 和 `job_cost_summary` 都不是成本事实源。
- Provider usage 缺失、pricing 条件缺失、ledger incomplete 或 multiple currency 时，不允许返回 `final=true` 的 0 成本。

### Public Contract

- 当前公共 `job_status` 仍只使用 `queued`、`running`、`succeeded`、`failed`，除非单独升级 `docs/api/service-contract.md`、schema 和 contract tests。
- 当前公开合同未升级前，非终态 `job_result` 和 `job_error` 仍为 `null`。
- `job_progress.stage`、`job_progress.message` 和 `job_progress.percent` 只能表达 root Job 级语义，不能泄漏 child node、worker attempt 或 provider 内部阶段。
- 不发布公共 `partially_succeeded`。
- Callback event 仍只使用当前公开合同允许的终态事件，除非单独升级 shared callback contract。
- `GET /models` 不暴露 provider raw model、内部 `pricing_ref`、价格矩阵、provider raw usage schema 或内部成本明细。
- 如果引入 running result snapshot、terminal `job.cost` 或其它 vNext 字段，必须同步 `docs/api/`、schema、contract tests 和调用方文档。

### Operations

- Retention 关系保证 root Job 查询期内 result 可用，root billing 查询期内 descendant ledger 可用，child Job 不早于 root workflow 排障窗口被硬删除。
- Dispatch outbox、workflow wakeup outbox、callback outbox 和 stale ledger 至少有可诊断 terminal / dead letter 状态。
- Callback receiver mock 覆盖签名、accepted body、非 2xx、超时和重试路径。
- 最小观测面能串联 root job id、workflow id、node id、child job id、attempt id 和 AI call ledger id。

## Verification Sequence

开发过程按以下顺序收口，不应跳过前置层直接做业务 e2e。

1. **Contract / registry checks**
   - service contract 不变式。
   - model / pricing / prompt registry fail-fast。
   - WorkflowSpec compile、DAG 无环、fan-out limit、node key uniqueness。
   - billing 状态映射。

2. **DB integration checks**
   - root workflow create 原子性。
   - child create + dispatch 原子性。
   - duplicate child terminal apply 幂等性。
   - join 并发一次性。
   - stale pending ledger 和 root terminal recovery。

3. **Runtime smoke**
   - `./scripts/dev.sh start`
   - `./scripts/verify.sh workflow-smoke`
   - `./scripts/dev.sh stop`

4. **Business adoption checks**
   - 正常 fan-out / fan-in。
   - 至少一个 provider / usage / pricing failure path。
   - descendant ledger 聚合到 root billing。
   - root terminal callback mock。

## Stop Conditions

出现以下任一情况，应停止实现并回到计划或合同文档收口：

- 需要让 child Job 进入公共查询合同。
- 需要新增公共 Job 状态。
- 需要让 workflow kernel 直接调用 provider。
- 需要让 business job 直接解析 provider raw usage 或计算 cost。
- 需要把 `job.cost`、callback payload 或 summary 表作为成本事实源。
- 需要让 billing service 解析 provider raw usage。
- 需要通过 silent fallback、默认 0 成本或吞错来让流程继续。
- 需要引入新基础设施，例如外部 workflow engine、事件总线或 CDC。

## Completion Rule

当以上 Gate 和 Terminal Acceptance 都有对应实现、测试和验证命令覆盖后，相关计划才能被移入 `docs/current/` 或关闭。若某项只完成文档设计但没有实现和测试，仍保留在 `docs/plans/`，不能写成当前事实。
