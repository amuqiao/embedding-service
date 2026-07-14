# AI Cost Boundary 后续计划

> Archived: 本文已合并进 [`../../plans/ai-capability-long-term.md`](../../plans/ai-capability-long-term.md)，不再作为独立活动计划维护。

本文只记录 AI cost boundary 仍未完成的计划。已经落地的当前事实见 [`../../current/ai-billing.md`](../../current/ai-billing.md)。

## Remaining Gaps

- 当前没有 `job_cost_summary` 表；如未来需要，只能作为可由 ledger 重建的 read model。
- workflow root billing 已覆盖 child AI 调用；当前没有 node / child cost attribution 专用字段。
- 当前多模态成本路径只有 pricing rule 和 usage record 基础类型，没有真实 provider usage 进入 ledger 的完整链路。
- 当前没有公开 scope 通用 billing route；稳定公开入口仍是 Job billing。

## Planned Work

1. 只有出现明确查询性能瓶颈或 root projection 稳定性需求时，才评估 `job_cost_summary`。
2. workflow node / child Job 成本归因落地前，先决定是在 `ai_call_ledger_entries` 加 attribution 字段，还是新增等价辅助表。
3. 接入多模态 provider 时，确保 provider raw usage 先标准化为 typed usage，再进入 pricing 和 ledger。
4. 如果需要持久化 `usage_kind` 或 `usage_schema_version`，通过 Alembic migration 增加列，不只写入 JSON 后在文档中宣称为表字段。

## Acceptance

- 新增 cost projection 不能成为新的成本事实源，必须能由 `ai_call_ledger_entries` 重建。
- provider raw usage 不得在 billing service 中临时解析；进入 billing 的单位必须来自 `usage_units`。
- `pricing_ref`、`pricing_version`、`currency` 和 `cost_amount` 的历史解释不能被 Job result 或 callback 覆盖。
- ledger 未收敛、usage 缺失或 pricing 失败时，公开 billing 必须表达 `incomplete` 或 `failed`，不能伪造 0 成本成功。
- 所有新增成本路径都有 billing service、AI facade failure path 和 registry/pricing 校验测试。

## Non-goals

- 不新增 wallet / balance / credit ledger。
- 不把 `ai_call_ledger_entries` 复用为资金账本。
- 不公开 provider raw usage、价格矩阵、token 明细或内部 pricing ref 给普通调用方。
