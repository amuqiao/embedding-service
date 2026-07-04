# Ops Dashboard Post-MVP Plan

本文只记录 `ops_dashboard` MVP 已完成后的后续优化，不描述当前实现事实。当前事实以 [`../current/ops-dashboard.md`](../current/ops-dashboard.md) 为准。

## Current Baseline

当前 MVP 已完成 5 个一级 tab：

```text
Overview
Recent Jobs
Flow & Capacity
Failures & Callbacks
Job Trace
```

已落地的稳定骨架：

- 后端 data source 注册：`overview`、`recent_jobs`、`flow_capacity`、`failures_callbacks`、`job_trace`。
- 前端注册层：`DATA_SOURCE_REGISTRY`、`PAGE_CONTROL_REGISTRY`、`WIDGET_REGISTRY`、`LAYOUT_REGISTRY`、`WIDGET_DATA_ADAPTERS`。
- renderer 类型：`status_line`、`metric_cards`、`echarts.line`、`echarts.stacked_bar`、`echarts.horizontal_bar`、`html.table`、`html.signal_list`、`html.summary_table`、`html.json_block`。
- MVP 排障链路：总览健康、最近任务、吞吐容量、失败回调、单 Job 证据链。

## Remaining Gaps

MVP 已覆盖基础查询排障链路。剩余问题主要是效率、长窗口分析、运行环境诊断和权限边界，不阻塞当前 dashboard 使用。

| 能力 | 类别 | 暂缓原因 | 触发条件 |
| --- | --- | --- | --- |
| table pagination / sorting / row expansion | Table platform | MVP 只需要 `limit`，过早平台化会放大实现面 | Recent Jobs 或 samples 表格高频使用 |
| copy job_id / quick status chips | Table UX | 属于效率优化，不影响排障闭环成立 | 维护人员频繁复制 `job_id` 或按状态切换 |
| `heatmap` | ECharts renderer | 当前窗口短，解释成本高 | 需要 7d / 30d 历史密度 |
| timeline visual | Renderer / Job Trace | table 已能表达 MVP 证据链 | 单 Job timeline 成为高频排障入口 |
| workflow tree | Renderer / Job Trace | tree renderer 需要额外交互和空态处理 | child workflow 层级复杂到 table 不够用 |
| CLI handoff polish | Ops workflow | MVP 先给文本提示即可 | broker/runtime/capacity budget/payload full 回跳频繁 |
| `broker` 页面 | Transport diagnostics | 依赖运行环境，适合 Pod 内 CLI | 确认 dashboard 要承接运输层排障 |
| `runtime` 页面 | Runtime diagnostics | Pod / cgroup 语义强 | 确认 dashboard 运行环境就是排障环境 |
| `pressure` 页面 | Load-test report | 包含 Locust CSV、API log 和压测上下文，更像压测后报告 | 压测 workflow 固化后再设计 |
| full payload drawer | Sensitive payload | 敏感且容易膨胀 | 不建议进入 dashboard |
| auth / audit / masking | Security boundary | 当前是 internal read-only dashboard | dashboard 给更多角色或环境访问 |
| `deleted-*` / `types` | Admin / audit | 审计或注册表查询，不属于常规排障闭环 | 需要后台管理页时再评估 |
| 写操作 | Ops mutation | 破坏只读边界 | 继续由 `job-ops.sh` 承担 |

## Planned Work

后续工作按触发条件进入独立小阶段，不一次性大而全实现。

### Table Usability

进入条件：

- `Recent Jobs`、`Failed Samples` 或 `Callback Samples` 被高频使用。
- 现有 `limit` 无法满足定位需求。

候选交付：

```text
server-side pagination
server-side sorting
row expansion
copy job_id
quick status chips
```

### Long-Window Analysis

进入条件：

- 运维需要看 7d / 30d 的历史密度，而不是 10m / 1h / 24h 的当前窗口。

候选交付：

```text
echarts.heatmap renderer
hour x job_type / error_code density
long-window backend aggregation
```

### Job Trace Visualization

进入条件：

- 单 Job timeline 或 workflow child 层级复杂到 table 不够扫读。

候选交付：

```text
timeline visual renderer
workflow tree renderer
trace layout refinement
```

### Environment Diagnostics

进入条件：

- 团队确认 dashboard 要承接部分 Redis broker、worker runtime 或压测报告诊断。

候选交付：

```text
broker read-only tab
runtime read-only tab
pressure report tab
```

默认仍由 `scripts/jobs.sh broker`、`scripts/jobs.sh runtime` 和压测脚本承担这些诊断。

### Security Boundary

进入条件：

- dashboard 开放给更多角色、更多环境或更长时间保留。

候选交付：

```text
auth policy tightening
audit log
field masking
payload redaction review
```

## Acceptance

每个后续小阶段必须满足：

- 不破坏当前 `data source / widget / layout / renderer` 解耦骨架。
- 新增事实同步进入 [`../current/ops-dashboard.md`](../current/ops-dashboard.md)。
- 只在确有需要时新增 renderer；优先复用现有 renderer。
- 只读边界默认不变；写操作仍由 `job-ops.sh` 承担。
- 至少运行 `uv run pytest tests/test_ops_dashboard.py` 和相关静态检查。
