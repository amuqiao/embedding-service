# Workflow Kernel Long-Term Plan

本文只记录 Job 内部 workflow kernel 的长期演进触发条件。当前 DAG-lite root / child 事实见 [`../current/workflow-kernel.md`](../current/workflow-kernel.md)；本文不把普通 Job 默认改造成 workflow，也不定义业务步骤编排或通用工作流平台。

## Current Baseline

- root Job 是公开查询、callback 和 billing 入口。
- internal child Job 是内部执行资源，不是默认公共查询资源。
- workflow 执行顺序由 root 冻结的 `workflow_plan.nodes[].depends_on` 表达。
- child AI 调用已聚合到 root Job billing scope。

## Trigger Rules

| 触发条件 | 可进入的工作 |
|---|---|
| 正式业务 Job 确实需要内部多节点执行 | 为该 `job_type` 新增 workflow definition、schema、executor 和最小 e2e |
| 调用方需要运行中结果或节点明细 | 先升级 API contract、schema 和 contract tests，再暴露查询 |
| 业务需要取消语义 | 单独设计 cancellation 合同，不扩展 `fail_fast` 含义 |
| root billing 无法满足排障或结算分析 | 转入 AI capability cost attribution 计划 |
| recovery 扫描延迟成为真实瓶颈 | 评估 workflow wakeup outbox |

## Planned Work

1. 首个正式业务 workflow 只实现该 Job 内部所需节点，不引入任意 DAG 提交、designer、业务步骤编排或跨服务编排。
2. running result snapshot、node detail、child query 等公开能力必须先经过 `docs/api/service-contract.md` 合同设计。
3. child 创建竞争、root 汇总重复推进、recovery 补扫 terminal children 必须用测试锁住。
4. 普通 non-workflow Job 保持当前直接执行模型；只有出现明确收益时，才评估 one-node workflow 编译。

## Acceptance

- 正式业务 workflow e2e 覆盖 root create、child execution、root terminal result、callback mock 和 root billing。
- 重复 root orchestration、重复 child terminal advance 和 recovery 重跑不会重复创建 child Job 或重复 finalize root。
- 新增公开字段或 route 同步 API contract、schema、contract tests 和文档。
- 新增 cost projection 只能从 `ai_call_ledger_entries` 重建，不能成为成本事实源。

## Non-goals

- 不引入 Temporal、Step Functions、事件总线或 CDC。
- 不开放任意 DAG 提交。
- 不承担项目管理、用户流程或跨服务业务编排。
- 不把 child Job 升级为默认公共查询资源。
- 不把普通 Job 默认改造成 `root + one child`。
- 不让 workflow kernel 直接调用 AI provider。
