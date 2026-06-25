# Workflow Kernel 后续计划

本文只记录 workflow kernel 从模板能力走向正式业务编排前仍未完成的计划。当前已落地事实见 [`../current/workflow-kernel.md`](../current/workflow-kernel.md)。

## Current Baseline

- DAG-lite root / child workflow 已经落地，复用 `job_aggregates`、`job_execution_attempts`、`dispatch_outbox`、Taskiq worker 和 recovery。
- 当前支持 `chain`、`group`、`chord`、`map`、`starmap` 和 `chunks` 模式示例。
- root Job 是公开查询、callback 和 billing 入口；internal child Job 是内部执行资源。
- child AI 调用已聚合到 root Job billing scope。

## Remaining Gaps

- 还没有正式业务 `job_type` 使用 workflow 执行真实业务输入、对象存储产物和 callback mock。
- 还没有公开 running result snapshot、child node 查询或 node / child 级 cost attribution 合同。
- `fail_fast` 当前不承诺取消已经 dispatch 或 running 的 child Job。
- workflow observability 仍是基础排障能力，没有专用运维 UI、dead letter UI 或 per-node metrics。
- 跨服务编排、外部 DAG 提交和通用 workflow designer 仍是非目标。

## Planned Work

1. 接入首个正式业务 workflow `job_type` 时，只新增该业务需要的 workflow definition、schema、executor 和最小 e2e。
2. 如果调用方需要运行中结果或节点明细，先升级 `docs/api/service-contract.md`、schema 和 contract tests，再暴露查询。
3. 如果需要取消语义，单独设计 cancellation 合同；不要把它塞进当前 `fail_fast`。
4. 如果 root billing 无法满足排障或结算分析，再为 node / child attribution 增加持久化字段和查询测试。
5. 如果 recovery 扫描延迟成为真实瓶颈，再评估 workflow wakeup outbox；现在不新增。

## Acceptance

- 正式业务 workflow e2e 覆盖 root create、child execution、root terminal result、callback mock 和 root billing。
- 重复 root orchestration、重复 child terminal advance 和 recovery 重跑不会重复创建 child Job 或重复 finalize root。
- 新增公开字段或 route 必须同步 API contract、schema、contract tests 和文档。
- 任何新增 cost projection 都只能从 `ai_call_ledger_entries` 重建，不能成为成本事实源。

## Non-goals

- 不引入 Temporal、Step Functions、事件总线或 CDC。
- 不开放任意 DAG 提交。
- 不把 child Job 升级为默认公共查询资源。
- 不让 workflow kernel 直接调用 AI provider。
