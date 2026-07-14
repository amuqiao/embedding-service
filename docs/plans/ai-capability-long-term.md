# AI Capability Long-Term Plan

本文只保留 AI capability、provider usage 和 cost boundary 的长期演进触发条件。当前事实见 [`../current/ai-capability.md`](../current/ai-capability.md) 和 [`../current/ai-billing.md`](../current/ai-billing.md)；本文不是当前能力清单，也不是短期实现队列。

## Current Baseline

- AI 调用必须经过 AI facade、model / prompt / pricing registry 和 billing ledger。
- Job billing 的稳定公开入口是 Job 维度聚合；workflow child AI 调用已聚合到 root Job billing scope。
- 成本事实源是 `ai_call_ledger_entries`，派生 projection 不能成为新的成本事实源。

## Trigger Rules

只有满足下列触发条件之一，才把对应条目拆成短期实施计划：

| 触发条件 | 可进入的工作 |
|---|---|
| 接入首个正式业务 `job_type` | 补齐业务 schema、prompt refs、model catalog、pricing rule、output schema refs 和最小 e2e |
| 接入真实 image / audio / video provider | 增加 provider adapter、usage normalizer、typed usage 到 ledger 的完整链路 |
| root Job billing 无法满足排障或结算分析 | 评估 node / child cost attribution 字段或辅助表 |
| Job billing 查询出现明确性能瓶颈 | 评估可由 ledger 重建的 `job_cost_summary` read model |
| 调用方合同需要按 usage 类型查询或索引 | 通过 Alembic migration 持久化 `usage_kind` / `usage_schema_version` |

## Planned Work

1. 正式业务接入时，业务 executor 只调用 AI facade，不直接解析 provider raw usage、不直接写 cost。
2. 新增 provider 前，先定义标准 `usage_units` 和 failure path；pricing / ledger 只消费标准 usage。
3. 成本归因或 summary 进入实现前，先明确 public API 是否需要暴露；内部排障需求优先走 current / runbook，不直接扩展公开合同。
4. 新增持久化字段必须通过 migration、repository/query 更新和 billing 聚合测试落地。
5. 真实业务链路落地后，再维护对应 e2e 或 `examples/business/` 验证。

## Acceptance

- 新增模型、Prompt、pricing 或 usage normalizer 缺失时，应用启动、worker 启动或 `./scripts/verify.sh check` fail-fast。
- 多模态 provider 成功路径能写入标准 `usage_units` 和 cost；失败路径不能伪造 0 成本成功。
- 新增 cost projection 能从 `ai_call_ledger_entries` 重建，并保留 `pricing_ref`、`pricing_version`、`currency` 和 `cost_amount` 的历史解释。
- 公开 billing 在 ledger 未收敛、usage 缺失或 pricing 失败时表达 `incomplete` 或 `failed`，不能用 0 成本吞错。
- 涉及真实 Job workflow 时，除 `./scripts/verify.sh check` 外，还要运行对应 smoke/e2e。

## Non-goals

- 不新增用户钱包、余额、扣费、退款或财务总账。
- 不把 `ai_call_ledger_entries` 复用为资金账本。
- 不公开 provider raw usage、价格矩阵、token 明细或内部 pricing ref 给普通调用方。
- 不把 model / pricing / prompt catalog 过早搬进数据库。
- 不新增 capability-specific route，除非统一 `/jobs + job_type` 无法表达能力差异。
