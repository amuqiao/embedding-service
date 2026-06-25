# AI Capability 后续计划

本文只记录当前 AI capability 仍未完成的计划。已经落地的当前事实见 [`../current/ai-capability.md`](../current/ai-capability.md)，成本边界当前事实见 [`../current/ai-billing.md`](../current/ai-billing.md)。

## Remaining Gaps

- image / audio / video 还没有真实 provider adapter、usage normalizer 和业务消费链路。
- Prompt-driven 正式业务 `job_type` 还没有统一接入真实业务 schema、prompt refs、step prompt refs 和 output schema refs。
- workflow root billing 已覆盖 child AI 调用；node / child 级 AI cost attribution 还没有持久化方案。
- `usage_kind`、`usage_schema_version` 当前不是持久化列；如需查询或索引，需要 Alembic migration。
- 真实模型业务 e2e 仍应在接入正式业务 `job_type` 后维护。

## Planned Work

1. 接入首个真实业务 `job_type` 时，只通过 AI facade 调用 provider，不在业务代码里复制 provider、usage 或 pricing 逻辑。
2. 为该业务补齐 model catalog、prompt refs、output schema refs、pricing rule 和 registry 校验。
3. 如业务需要图片、音频或视频，先补真实 provider adapter 和 usage normalizer，再开放对应模型目录条目。
4. 在 workflow child Job 需要 node / child 级成本归因前，选择并实现 attribution 持久化方案。
5. 根据真实业务链路补充最小 e2e 或 `examples/business/` 验证。

## Acceptance

- 业务 `job_type`、workflow node 和 provider adapter 都不能绕过 AI facade 直接写 cost 或解析 provider raw usage。
- 新增模型、Prompt、pricing 配置缺失或不匹配时，应用启动、worker 启动或 `./scripts/verify.sh check` fail-fast。
- 多模态 provider 成功路径能写入标准 `usage_units` 和 cost；失败路径不能伪造 0 成本成功。
- node / child attribution 如落地，必须有 migration、repo/query 更新和 billing 聚合测试。
- `./scripts/verify.sh check` 通过；涉及真实 Job workflow 时，再运行对应 smoke/e2e。

## Non-goals

- 不新增用户钱包、余额、扣费、退款或财务总账。
- 不把 model / pricing / prompt catalog 过早搬进数据库。
- 不新增 capability-specific route，除非统一 `/jobs + job_type` 无法表达能力差异。
