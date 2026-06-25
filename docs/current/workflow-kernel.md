# Workflow Kernel 当前模型

本文只记录当前已经落地的 DAG-lite workflow 行为。公开 HTTP 合同仍以 [`../api/service-contract.md`](../api/service-contract.md) 为准。

## 当前行为

- root Job 是公共提交、查询、callback 和 billing 入口。
- internal child Job 是 workflow executable node，复用现有 Job attempt、dispatch outbox、lease、heartbeat、retry 和 worker 执行路径。
- `job_aggregates` 通过 `root_job_id`、`parent_job_id`、`is_internal` 和 `workflow_node_key` 表达 root / child 关系。
- `workflow_plan` 固化在 root Job runtime snapshot 中，当前 planner 支持 `chain`、`group`、`chord`、`map`、`starmap` 和 `chunks`。
- child Job 终态后由 workflow orchestrator 推进 downstream node 或 root terminal projection。
- root Job 只发送一次调用方 callback；child Job 不发送调用方 callback。
- child Job 的 AI 调用使用 root Job billing scope，ledger 行仍保留实际 child `job_id`、`attempt_id` 和 `job_type` 作为诊断归因。

## Runtime Path

```text
POST /jobs
  -> create root Job + frozen DAG-lite workflow_plan
  -> root orchestration attempt
  -> create ready internal child Jobs
  -> child Job attempts execute through Taskiq
  -> child terminal advances workflow
  -> root result / error / callback / billing projection
```

Recovery 会扫描需要补偿的 workflow root，修复 missed child terminal advance、ready child 缺失和 root terminal projection 漏执行等情况。

## 当前边界

- workflow 只用于单个 AI Job 微服务内部编排，不是跨服务 workflow 平台。
- 外部调用方不能提交任意 DAG；正式业务需要通过自己的 `job_type` 和 workflow definition 暴露受控能力。
- public `GET /jobs/{job_id}` 不把 internal child Job 作为调用方合同资源。
- 当前没有独立 `workflow_instances`、`workflow_nodes`、`workflow_events` 或 `workflow_wakeup_outbox` 表。
- 当前没有 node / child 级公开 billing 查询；公开 billing 入口仍是 root Job scope。

## 验证

- `tests/test_job_workflow.py`
- `tests/test_workflow_modes.py`
- `tests/test_recovery.py`
- `tests/test_billing_service.py`
- `./scripts/verify.sh workflow-smoke`
- `./scripts/verify.sh workflow-modes-smoke`
