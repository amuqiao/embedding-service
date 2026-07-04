# Ops Dashboard MVP Navigation Plan

本文规划 `ops_dashboard` 下一阶段的页面骨架、视图归属规则和分阶段实施路线。它是待确认计划，不描述当前已实现页面。当前实现事实以 [`../current/ops-dashboard.md`](../current/ops-dashboard.md) 为准。

`jobs.sh` 排障能力和命令口径以 [`../../scripts/jobs.sh`](../../scripts/jobs.sh) 和 [`../../scripts/jobs/cli.py`](../../scripts/jobs/cli.py) 为唯一真源；[`../runbooks/jobs使用与排障手册.md`](../runbooks/jobs使用与排障手册.md) 只作为辅助理解材料，若与脚本不一致，以脚本为准。

本文中的 dashboard 指 Web `ops_dashboard`；`./scripts/jobs.sh dashboard` 指 CLI 子命令。两者同属只读排障入口，但 Web dashboard 不替代 CLI。

## What This Plan Is

这份计划不是单纯的 UI 改版，也不是把 `scripts/jobs.sh` 全部搬进网页。它要稳定的是观测模块的增量开发骨架：

```text
先稳定注册和导航合同
  -> 再完成最小只读排障闭环
  -> 最后按真实使用频率补表格、图表和深排障能力
```

后续新增视图时，应按以下流程判断：

```text
它回答哪个排障问题
  -> 属于哪个 tab
  -> 需要哪个 page-level data source
  -> 需要哪些 page-local controls
  -> 复用哪个 renderer
  -> 通过哪些测试固化引用闭环
```

## Source Boundaries

- 当前实现事实只进入 `docs/current/ops-dashboard.md`。
- `scripts/jobs.sh` 和 `scripts/jobs/cli.py` 是 Job 只读排障命令真源。
- 本文只保存实施路线、阶段状态、未实现计划和验收标准。
- MVP 完成后，已落地事实应移动到 `docs/current/ops-dashboard.md`，本文只保留剩余计划。

Dashboard 的边界：

- 只读，不创建、取消、重试、删除、恢复、callback replay 或 dispatch replay。
- 不替代 `scripts/job-ops.sh` 的写操作。
- 不展示完整 payload；只展示 payload summary / runtime summary / result summary / error summary。
- 不把 `broker`、`runtime`、`pressure`、`payload --full` 搬进 dashboard MVP。
- 不新增 `heatmap`；当前 `10m / 1h / 24h` 运维窗口优先使用 line、stacked bar、horizontal bar 和 table。
- 不把 `status` 做成全局过滤器；`status` 只属于 `Recent Jobs` 页面内筛选。
- 不在 `Recent Jobs` 引入 `scope=family/all`；MVP 只列 public root Jobs，child / family 证据进入 `Job Trace` 或 CLI。

## Current Baseline

- `ops_dashboard` 已是可选只读 internal dashboard，路由固定在 `/internal/jobs-dashboard`，不进入 OpenAPI。
- 前端已有 `data source / widget / layout / renderer` 分层注册骨架。
- 当前 renderer 已覆盖 MVP 所需基础表达：`status_line`、`metric_cards`、`echarts.line`、`echarts.stacked_bar`、`echarts.horizontal_bar`、`html.table`、`html.signal_list`、`html.summary_table`、`html.json_block`。
- 后端当前 data source 已进入目标 key：`overview`、`recent_jobs`、`flow_capacity`、`failures_callbacks`、`job_trace`。
- `recent_jobs` 和 `flow_capacity` 已接入 DB read model。
- 当前页面已经能展示总览成功闭环、最近任务、吞吐容量方向、失败与 callback 闭环、单 Job 追踪。

## Roadmap Summary

| 阶段 | 目标 | 完成后解决的问题 |
| --- | --- | --- |
| Foundation / 稳定阶段 | 固化导航、data source、page-local controls、widget/layout/renderer 引用合同 | 新增视图不再靠 JS 特判或临时摆放 |
| MVP Closed Loop / MVP 闭环 | 覆盖最常用 DB read model 只读排障路径 | 维护人员能从总览定位方向、选择 Job、进入追踪、回到 CLI 深排障 |
| Future Optimization / 未来优化 | 按使用频率补表格、图表、CLI handoff、权限和高级排障能力 | 避免 MVP 膨胀，同时保留后续演进入口 |

## Foundation Stage

稳定阶段优先于页面功能完整性。它的目标是让后续新增 tab、视图、图表和后端 data source 有统一路径。

### Contracts

新的 navigation contract：

```text
tab key
  == layout key
  == page-level dataSource key
  == backend section route key
```

页面级数据源：

```text
overview
recent_jobs
flow_capacity
failures_callbacks
job_trace
```

前端 layout 继续保持一个 layout 对应一个 `dataSource`。跨 data source 拼页面不是 MVP 目标。

### Page-Local Controls

页面级控件需要成为一等合同，不能继续靠 JS 特判。每个 tab 可以声明自己的 page-local controls，但必须满足：

```text
control key
  -> control type
  -> default value
  -> allowed values or validation rule
  -> binding: route param or query param
  -> target dataSource key
```

MVP 中需要前置的页面级控件：

| tab | controls | 说明 |
| --- | --- | --- |
| `Recent Jobs` | `status`、`client_request_id`、`limit` | `status` 是页内筛选，不进入全局过滤器 |
| `Job Trace` | `job_id`、`limit` | `job_id` 搜索迁移到统一页面级控件机制 |

### Foundation Deliverables

```text
backend data source registry 目标 key
frontend layout registry 目标 key
page-local controls registry / schema / query serialization
route key / dataSource key / layout key 对齐
tests 固化 data source / widget / renderer / layout 引用闭环
```

### Foundation Acceptance

- 新增 tab 不需要新增一段专用 JS 查询特判。
- widget 的 `dataSource` 必须与所在 layout 的 `dataSource` 一致。
- 未注册 renderer、data source、adapter 或 widget 引用必须 fail-fast。
- `/internal/jobs-dashboard/config` 能表达目标 data source。
- tests 覆盖 route、registry、widget、layout、renderer 引用闭环。

## MVP Closed Loop

MVP 的目标不是把 `scripts/jobs.sh` 做成网页版本，而是覆盖最常用的 DB read model 只读排障闭环：

```text
现在是否健康
  -> 最近任务具体是哪几个
  -> 系统是在恢复、积压还是容量不足
  -> 失败和 callback 是否闭环
  -> 单个 Job 卡在哪一步
```

Dashboard 应该让维护人员先完成方向判断，再把深排障交回 `jobs.sh` 或 `job-ops.sh`：

```text
dashboard 完成 DB read model 内的方向判断和 Job 选择
  -> 点击 job_id 进入 Job Trace
  -> 需要 Redis/Taskiq/Pod/payload full 证据时回到 scripts/jobs.sh
  -> 需要写操作时回到 scripts/job-ops.sh
```

它不表示 dashboard 在页面内覆盖 `jobs.sh` 的所有排障层级。

## MVP Navigation Model

MVP 使用 5 个 tab：

```text
Overview
  当前系统是否健康，下一步看哪里

Recent Jobs
  选择一个代表性 public root Job 进入追踪

Flow & Capacity
  系统是在恢复、积压，还是被容量或延迟卡住

Failures & Callbacks
  失败集中在哪，终态是否通知调用方

Job Trace
  单个 Job 的证据链和卡点
```

不建议拆成更多一级 tab。`Recovery`、`Throughput`、`Bottlenecks`、`Capacity` 在 MVP 中合并为 `Flow & Capacity`，避免页面过碎导致维护人员不知道从哪里开始。

`Recent Jobs` 保留为一级 tab 的前提是：它不是分析页，只是代表性 root Job 选择器。它不承载聚合图、不解释失败、不分析 callback，也不展开 family / child 视角；这些职责分别属于 `Flow & Capacity`、`Failures & Callbacks` 和 `Job Trace`。

`Recent Jobs` 是 MVP 的独立 tab 和独立 page-level data source，不作为 `Overview` 内 hotlist 过渡实现。这样才能保持 `tab key == layout key == dataSource key == route key` 的 Foundation 合同。

## Global Controls

全局控件只表达不改变语义的查询上下文：

| 控件 | 作用域 | 说明 |
| --- | --- | --- |
| `window` | overview / recent jobs / flow / failures | 例如 `10m`、`1h`、`24h` |
| `bucket` | trend widgets | 例如 `1m`、`5m`、`15m` |
| `caller_id` | section data source | 可选调用方过滤 |
| `job_type` | section data source | 可选 Job 类型过滤 |
| refresh | auto refresh sections | 由 data source 的 `refresh_seconds` 控制 |

`status` 不进入全局控件。原因是 Overview 同时包含“当前全局 active 占用”和“窗口统计”；全局 status 会让卡片、趋势和样本的语义混乱。

## MVP Tabs And Views

### Overview

回答：

- 现在系统是否健康？
- 是成功闭环、失败、stuck、callback 还是容量方向的问题？
- 下一步应该进入哪个 tab？

MVP widgets：

| Widget | rendererType | 数据需求 | 对应 `jobs.sh` 命令 |
| --- | --- | --- | --- |
| Health Status | `status_line` | `generated_at`、`health.status`、`health.reasons` | `overview`、`doctor` |
| Current State Cards | `metric_cards` | `active`、`queued`、`running`、`succeeded`、`failed`、`stuck`、`callback_due`、`success_rate` | `overview`、`summary` |
| Ingress Trend | `echarts.line` | `created`、`terminal`、`failed` by bucket | `ingress` |
| Terminal Composition | `echarts.stacked_bar` | `succeeded`、`failed`、`canceled` by bucket | `summary`、`ingress` |
| Next Checks | `html.signal_list` | next check strings | `overview` verdict |
| Stuck Sample Entry | `html.table` | small stuck sample with `job_id` links | `stuck` |

Overview 不放长表格。`Stuck Sample Entry` 只保留少量可点击样本，用于完成“发现 stuck -> 进入 Job Trace”的最短路径；其他具体 Job 样本放到 `Recent Jobs` 或 `Failures & Callbacks`。

### Recent Jobs

回答：

- 我现在应该点哪个代表性 public root Job 进入追踪？
- 最近成功、失败、运行中、排队中的 root Job 具体是哪几个？
- 某个 `caller_id`、`job_type` 或 `client_request_id` 下最近有哪些 Job？

Page-local controls：

| Control | 数据需求 | 对应 `jobs.sh` 命令 |
| --- | --- | --- |
| Status Filter | `all`、`queued`、`running`、`succeeded`、`failed` | `list --status` |
| Client Request Filter | `client_request_id` | `list --client-request-id` |
| Limit | row limit | `list --limit` |

MVP widgets：

| Widget | rendererType | 数据需求 | 对应 `jobs.sh` 命令 |
| --- | --- | --- | --- |
| Result Cards | `metric_cards` | current filter count、newest、oldest、terminal count | `list`、`summary` |
| Recent Jobs Table | `html.table` | job rows | `list` |

Recent Jobs table 字段：

```text
job_id
status
job_type
caller_id
progress
created_at
updated_at
finished_at
duration_or_age
callback_status
```

表格行行为：

```text
click job_id -> Job Trace
```

MVP 只需要 `limit`，不实现分页、排序、行展开。分页和排序留到 `Recent Jobs` 被高频使用后再加。

MVP 固定为 public root Jobs 选择器，不提供 `scope` 控件。需要 child / family 视角时，从 `Job Trace` 的 workflow children、`Failures & Callbacks` 的样本，或 `./scripts/jobs.sh list --scope family` 进入。

Recent Jobs 不放：

```text
failure group 分析
callback 状态构成
latency 分解
capacity 估算
```

这些内容会让 Recent Jobs 从“选择器”膨胀成分析页，和其他 tab 重叠。

### Flow & Capacity

回答：

- 系统是在恢复还是恶化？
- 当前积压来自入口流量、执行速度、容量上限还是延迟？
- 是否需要回到 CLI 做 worker / API / DB 连接预算判断？

MVP widgets：

| Widget | rendererType | 数据需求 | 对应 `jobs.sh` 命令 |
| --- | --- | --- | --- |
| Capacity Cards | `metric_cards` | `max_active_jobs`、`active_jobs`、`headroom`、`queued`、`running` | `gate`、`capacity` |
| Ingress / Drain Trend | `echarts.line` | `created`、`started`、`terminal`、`failed` | `ingress`、`drain` |
| Status Composition | `echarts.stacked_bar` | `queued`、`running`、`succeeded`、`failed` by bucket | `summary`、`observe` |
| Latency p95 | `echarts.horizontal_bar` | queue p95、run p95、lifecycle p95 | `latency` |
| Job Type Hotspots | `html.table` or `echarts.horizontal_bar` | by `job_type` active / total / failed / p95 | `capacity`、`latency` |

`Flow & Capacity` 只输出方向信号，不直接给扩容建议，也不计算 DB 连接预算。`broker`、`runtime` 和带预算输入的 `capacity` 判断继续回到 CLI；它们可以作为 `Next Checks` 中的提示，例如“查 `./scripts/jobs.sh broker`”或“查 `./scripts/jobs.sh capacity --worker-pods ...`”，但 dashboard MVP 不在页面读取 Redis、进程、cgroup 或部署拓扑。

### Failures & Callbacks

回答：

- 失败是否集中在某类错误？
- callback 是否把终态通知出去？
- 哪些失败或 callback 样本需要进入 Job Trace？

MVP widgets：

| Widget | rendererType | 数据需求 | 对应 `jobs.sh` 命令 |
| --- | --- | --- | --- |
| Failure / Callback Cards | `metric_cards` | failed、retrying、callback_due、delivered、dead_letter | `failures`、`callbacks-summary` |
| Failure Groups Rank | `echarts.horizontal_bar` | count by `error_code` | `failures` |
| Failure Groups Table | `html.table` | error_code、error_kind、failure_phase、count、newest | `failures` |
| Failed Samples | `html.table` | failed job rows | `list --status failed` |
| Callback Composition | `echarts.stacked_bar` | callback status by bucket or window | `callbacks-summary` |
| Callback Samples | `html.table` | due / failed / dead_letter callback rows | `callbacks`、`callbacks-summary` |

这个 tab 负责“坏消息聚合”。成功率和 succeeded 不能只放在这里，必须在 Overview / Recent Jobs 形成成功闭环。

### Job Trace

回答：

- 单个 Job 当前状态是什么？
- 卡在 accepted、dispatch、claim、running、callback 还是 workflow child？
- 有哪些 attempts、AI calls、timeline、callbacks 证据？

Page-local controls：

| Control | 数据需求 | 对应 `jobs.sh` 命令 |
| --- | --- | --- |
| Job Search | `job_id` | `trace <job_id>` |
| Limit | table row limit | `trace <job_id>` related views |

MVP widgets：

| Widget | rendererType | 数据需求 | 对应 `jobs.sh` 命令 |
| --- | --- | --- | --- |
| Job Summary | `html.summary_table` | job identity、status、progress、timestamps | `job`、`inspect` |
| Payload Summary | `html.json_block` | metadata/job_params/runtime/result/error summary | `payload` without `--full` |
| Attempts | `html.table` | lifecycle attempts | `attempts` |
| AI Calls | `html.table` | AI call ledger | `ai-calls` |
| Workflow Children | `html.table` | child jobs | `workflow` |
| Timeline | `html.table` | audit events | `timeline` |
| Callbacks | `html.table` | callback delivery evidence | `callbacks` |

`payload --full` 不进 dashboard。需要完整原始 payload 时，页面只提示使用 CLI。

## Data Source Plan

MVP 使用 page-level data source，不为每个 widget 单独拆 endpoint。

| dataSource | route 草案 | filters | 返回范围 |
| --- | --- | --- | --- |
| `overview` | `/internal/jobs-dashboard/sections/overview/data` | window、bucket、caller_id、job_type | health、summary、success loop、ingress trend、next checks |
| `recent_jobs` | `/internal/jobs-dashboard/sections/recent_jobs/data` | window、status、caller_id、job_type、client_request_id、limit | status options、cards、public root job rows |
| `flow_capacity` | `/internal/jobs-dashboard/sections/flow_capacity/data` | window、bucket、caller_id、job_type | gate/capacity signals、ingress/drain、status composition、latency、job_type hotspots |
| `failures_callbacks` | `/internal/jobs-dashboard/sections/failures_callbacks/data` | window、bucket、caller_id、job_type、limit | failure groups、failed samples、callback composition、callback samples |
| `job_trace` | `/internal/jobs-dashboard/jobs/{job_id}/data` | limit | summary、payload summary、attempts、AI calls、children、timeline、callbacks |

新增 5 个 tab 意味着后端和前端都要新增 page-level data source。不要把它误认为“只改 `LAYOUT_REGISTRY`”。

## Widget Placement Rules

新增视图前先按问题归类：

| 问题 | 放入 tab |
| --- | --- |
| 现在是否健康、下一步看哪里 | `Overview` |
| 最近哪些任务成功/失败/运行/排队 | `Recent Jobs` |
| 是否在恢复、吞吐是否下降、gate/headroom 是否不足、慢在哪 | `Flow & Capacity` |
| 失败集中在哪、callback 是否闭环 | `Failures & Callbacks` |
| 单 Job 卡在哪一步、证据链是什么 | `Job Trace` |

归属不清时，不要新增 widget。先判断它回答的是哪个排障问题，而不是先判断它来自哪个 `jobs.sh` 子命令。

## Implementation Phases

### Phase 0: Foundation / 稳定阶段（已落地）

已完成。当前事实以 [`../current/ops-dashboard.md`](../current/ops-dashboard.md) 的 registry、data source 和 page-local controls 章节为准。

### Phase 1: MVP Core Loop / 成功闭环 + Recent Jobs（已落地）

已完成。当前事实以 [`../current/ops-dashboard.md`](../current/ops-dashboard.md) 的 Overview、Recent Jobs 和验证说明为准。

### Phase 2: MVP Flow & Capacity（已落地）

已完成。当前事实以 [`../current/ops-dashboard.md`](../current/ops-dashboard.md) 的 Flow And Capacity Contract、widgets 和 CLI handoff 说明为准。

### Phase 3: MVP Failures & Callbacks（已落地）

已完成。当前事实以 [`../current/ops-dashboard.md`](../current/ops-dashboard.md) 的 Failures And Callbacks Contract、widgets 和 CLI handoff 说明为准。

### Post-MVP: Table Usability / 表格可用性

进入条件：

- `Recent Jobs` 或 samples 表格开始高频使用。

候选能力：

```text
server-side pagination
server-side sorting
row expansion
copy job_id
quick status chips
```

不要在 Phase 1 就做 table 平台化；先验证 Recent Jobs 的实际使用方式。本阶段是 Post-MVP optimization，不属于 MVP 闭环验收。

## Future Optimization Backlog

未来优化不阻塞 MVP。只有达到触发条件后，才进入实施计划。

| 能力 | 类别 | 暂缓原因 | 触发条件 |
| --- | --- | --- | --- |
| table pagination / sorting / row expansion | Table platform | MVP 只需要 `limit`，过早平台化会放大实现面 | Recent Jobs 或 samples 表格高频使用 |
| copy job_id / quick status chips | Table UX | 属于效率优化，不影响排障闭环成立 | 维护人员频繁复制 job_id 或按状态切换 |
| `heatmap` | ECharts renderer | 当前窗口短，解释成本高 | 需要 7d / 30d 历史密度 |
| timeline visual | Renderer / Job Trace | table 已能表达 MVP 证据链 | 单 Job timeline 成为高频排障入口 |
| workflow tree | Renderer / Job Trace | tree renderer 需要额外交互和空态处理 | child workflow 层级复杂到 table 不够用 |
| CLI handoff polish | Ops workflow | MVP 先给文本提示即可 | broker/runtime/capacity budget/payload full 回跳频繁 |
| `broker` 页面 | Transport diagnostics | 依赖运行环境，适合 Pod 内 CLI | 确认 dashboard 要承接运输层排障 |
| `runtime` 页面 | Runtime diagnostics | 当前 Pod / cgroup 语义强 | 确认 dashboard 运行环境就是排障环境 |
| `pressure` 页面 | Load-test report | 包含 Locust CSV、API log 和压测上下文，更像压测后报告 | 压测 workflow 固化后再设计 |
| full payload drawer | Sensitive payload | 敏感且容易膨胀 | 不建议进入 dashboard |
| auth / audit / masking | Security boundary | 当前是 internal read-only dashboard | dashboard 给更多角色或环境访问 |
| `deleted-*` / `types` | Admin / audit | 审计或注册表查询，不属于常规排障闭环 | 需要后台管理页时再评估 |
| 写操作 | Ops mutation | 破坏只读边界 | 继续由 `job-ops.sh` 承担 |

## Semantic Alignment Tests

实现阶段需要逐步增加与 `scripts/jobs.sh` 真源对齐的测试或快照。目标不是从 dashboard import CLI，而是避免 dashboard read model 口径漂移。

优先对齐：

```text
status
public root job rows
success_rate
callback_due / delivered / dead_letter
latency p95
gate / headroom
failure group fields
payload summary boundary
```

## Acceptance

MVP 完成后，维护人员应该能完成以下路径：

```text
打开 Overview
  -> 判断当前是否健康
  -> 看到 succeeded / failed / stuck / callback_due / success_rate
  -> 根据 next checks 进入对应 tab

打开 Recent Jobs
  -> 用 status 筛选 succeeded / failed / running / queued
  -> 找到代表性 public root job_id
  -> 点击进入 Job Trace

打开 Flow & Capacity
  -> 判断入口流量、排空方向、gate/headroom 和延迟瓶颈
  -> 需要 broker/runtime/capacity budget 时回到 CLI

打开 Failures & Callbacks
  -> 找到失败聚合、失败样本和 callback 闭环状态
  -> 点击样本进入 Job Trace

打开 Job Trace
  -> 看到 summary、attempts、AI calls、children、timeline、callbacks 和 payload summary
```

测试验收：

- `tests/test_ops_dashboard.py` 覆盖新增 data source route。
- 静态 registry 测试覆盖 `widget -> renderer/dataSource/adapter` 和 `layout -> widget` 引用。
- page-local controls 测试覆盖声明、默认值、query serialization 和目标 data source。
- examples 覆盖新增公开 renderer，但仍不请求 live data。
- `./scripts/verify.sh check` 通过。

文档验收：

- 当前事实只进入 `docs/current/ops-dashboard.md`。
- 本文只保留未实现计划和阶段验收。
- MVP 完成后，将已实现事实移动到 `docs/current/ops-dashboard.md`，本文只保留剩余阶段。
