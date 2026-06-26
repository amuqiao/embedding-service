# Workflow Kernel 当前模型

本文只记录当前已经落地的 DAG-lite workflow 行为。公开 HTTP 合同仍以 [`../api/service-contract.md`](../api/service-contract.md) 为准。

## 当前行为

- root Job 是公共提交、查询、callback 和 billing 入口。
- internal child Job 是 workflow executable node，复用现有 Job attempt、dispatch outbox、lease、heartbeat、retry 和 worker 执行路径。
- `job_aggregates` 通过 `root_job_id`、`parent_job_id`、`is_internal` 和 `workflow_node_key` 表达 root / child 关系。
- `workflow_plan` 固化在 root Job runtime snapshot 中，当前 planner 支持 `task`、`chain`、`group`、`chord`、`map`、`starmap` 和 `chunks`；`job_test_workflow` 额外提供 `single` 示例模式，用一个 `task` 表达 one-child workflow。
- child Job 终态后由 workflow orchestrator 推进 downstream node 或 root terminal projection。
- root Job 只发送一次调用方 callback；child Job 不发送调用方 callback。
- child Job 的 AI 调用使用 root Job billing scope，ledger 行仍保留实际 child `job_id`、`attempt_id` 和 `job_type` 作为诊断归因。
- workflow child node 当前复用注册的 `job_type` executor；目录上应优先引用 `role="leaf"` 或 `role="root_or_leaf"` 的类型，但运行时 child 事实仍由 Job 实例 lineage 字段表达。

## 任务模型

workflow 的聚合归属和执行依赖是两条正交关系：

```text
Lineage:
root
  |- child[a]
  |- child[b]
  |- child[c]

Dependencies:
a -> b -> c
```

`root + children` 表示这些 child 都属于同一个 public root Job。`depends_on` 表示执行顺序，不表示 `parent_job_id` 层级。当前 `chain(a, b, c)` 编译后是 `b depends_on a`、`c depends_on b`；不会形成 `a` 的 child 是 `b`、`b` 的 child 是 `c` 的嵌套树。

| 模型 | 形态 | 示例用途 |
|---|---|---|
| 单叶子任务 | `root + child A` | 单节点 workflow 示例；普通 non-workflow Job 当前还不是这种形态 |
| 并行 fan-out | `root + children A/B/C` | 多语言、多图片、多分片 |
| fan-out + join | `root + children A/B/J; J depends_on A,B` | 批量生成后合并结果 |
| 串行 chain | `root + children A/B/C; B depends_on A; C depends_on B` | 先分析、再生成、再后处理 |
| chunks/map | `root + children chunk.0...chunk.N` | 大批量分块处理 |
| partial/fail-fast | 子任务失败策略 | 容错批处理和严格批处理 |

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
- 普通 non-workflow Job 当前仍由 public root Job 直接执行；“所有 Job 都统一成 `root + child`”是后续计划。

## 验证

- `tests/test_job_workflow.py`
- `tests/test_workflow_compiler.py`
- `tests/test_recovery.py`
- `tests/test_billing_service.py`
- `./scripts/verify.sh workflow-smoke`
- `./scripts/verify.sh workflow-modes-smoke`
