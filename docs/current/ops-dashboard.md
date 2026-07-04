# Ops Dashboard

`ops_dashboard` 是可选的只读 internal dashboard；当前实现把后端数据源、前端 widget、renderer 和页面布局拆成独立注册层，方便后续新增观测视图、替换页面结构和扩展 ECharts 图表能力。

## Purpose

`ops_dashboard` 用于把 Job 运维读模型可视化。它承接常驻总览、失败聚合和单 Job 追踪的只读展示；深度排障入口仍是 `scripts/jobs.sh`。

本模块不负责生产部署、业务 mock 数据、对象存储内容查看、完整 payload 展示或 Redis broker / Pod runtime 的深度排查。

## Mental Model

当前 dashboard 的核心设计是“数据、视图、渲染、摆放”分层：

```text
Backend Data Source
  负责 route、refresh、read model payload
        |
        v
Frontend Widget
  负责声明 dataSource、dataPath / adapter、rendererType、字段映射
        |
        v
Renderer
  负责把 widget 输入渲染成 ECharts、HTML table、指标卡片或诊断块
        ^
        |
Layout
  负责 widget 放在哪个页面、哪个区域、什么顺序和外层 chrome
```

关键边界：

- **widget 不拥有 DOM 位置**。widget 只描述“这个观测视图是什么、吃什么数据、用什么 renderer”。
- **layout 拥有位置**。页面结构、分区、顺序、宽度和 panel 外壳都由 layout 决定。
- **renderer 不知道业务语义**。renderer 只消费 rows、columns、series、cards 等通用输入。
- **后端不声明 ECharts 细节**。后端只提供 data source 和稳定 payload。

当前页面按 section 级 data source 加载：一个 layout 对应一个 `dataSource`，该 layout 内所有 widget 必须声明同一个 `dataSource`。运行时会做 fail-fast 校验，避免 widget 被放进不匹配的 payload 上下文。

因此，在同一个 data source 内移动视图位置只改 layout；新增一个观测视图通常只改 widget + layout；新增一种图表能力才改 renderer。

## Current Behavior

- 默认关闭，由 `OPS_DASHBOARD_ENABLED` 控制。
- 路由固定在 `/internal/jobs-dashboard`，不进入 OpenAPI。
- 后端提供 data source config、overview、recent_jobs、flow_capacity、failures_callbacks、job trace、health。
- 前端负责 renderer contract、widget registry、layout registry、ECharts 渲染和 HTML 渲染。
- `recent_jobs` 已接入 public root Job 读模型，支持页内 `status/client_request_id/limit` 控件；`flow_capacity` 已接入 DB read model，用于吞吐、drain、容量、延迟和 job_type 热点方向判断；`failures_callbacks` 已接入失败聚合、失败样本、callback summary 和 callback 样本。
- `/internal/jobs-dashboard/examples` 是独立静态 renderer 示例页，只使用 generic fixtures，不请求 Job 读模型，也不作为业务 mock 数据源。
- dashboard 不支持业务 mock 数据开关；旧的 `OPS_DASHBOARD_MOCK_DATA_ENABLED` 已废弃，出现在 env 文件或进程环境中都会触发配置加载失败。
- dashboard 不直接读取 Redis broker、Pod runtime、完整 payload 或对象存储内容；这些仍由 `scripts/jobs.sh broker/runtime/payload --full` 等命令承担。

## Registry Layers

当前实现有五个稳定注册层。

```text
app/ops_dashboard/registry.py
  DASHBOARD_DATA_SOURCES
        |
        v
GET /internal/jobs-dashboard/config
  data_sources / sections

app/ops_dashboard/static/dashboard.js
  DATA_SOURCE_REGISTRY
  PAGE_CONTROL_REGISTRY
  WIDGET_REGISTRY
  LAYOUT_REGISTRY
  WIDGET_DATA_ADAPTERS

app/ops_dashboard/static/chart_contract.js
  RENDERER_TYPES
  RENDERERS
  renderWidgetLayout()
```

| 层 | 文件 | 拥有内容 | 不应包含 |
| --- | --- | --- | --- |
| Backend Data Source | `app/ops_dashboard/registry.py` / `router.py` | `key`、`title`、`route`、`refresh_seconds`、read model route | ECharts 类型、DOM 位置、页面布局 |
| Page-local Control | `app/ops_dashboard/registry.py` / `dashboard.js` | `key`、`type`、`binding`、`param`、默认值、选项、目标 data source | 业务 SQL、renderer 实现、跨页面全局过滤语义 |
| Widget | `app/ops_dashboard/static/dashboard.js` | `rendererType`、`dataSource`、`dataPath` / `adapter`、字段映射、运维问题 | DOM `target`、页面区域、panel 顺序 |
| Layout | `app/ops_dashboard/static/dashboard.js` | 页面 title、dataSource、groups、placements、panel chrome、host class、target | 数据查询逻辑、renderer 实现、业务 SQL |
| Renderer | `app/ops_dashboard/static/chart_contract.js` | 通用渲染器、fallback、HTML escape、ECharts 生命周期 | Job 业务字段、section 路由、页面导航 |

## Data Sources

后端 data source 由 `DASHBOARD_DATA_SOURCES` 注册，并通过 `/internal/jobs-dashboard/config` 暴露给前端。

| key | route | refresh | 当前用途 |
| --- | --- | --- | --- |
| `overview` | `/internal/jobs-dashboard/sections/overview/data` | 15s | 总览健康、容量、趋势、延迟、成功率、stuck 样本 |
| `recent_jobs` | `/internal/jobs-dashboard/sections/recent_jobs/data` | 15s | public root Job 选择器；page-local `status/client_request_id/limit` 控件合同 |
| `flow_capacity` | `/internal/jobs-dashboard/sections/flow_capacity/data` | 30s | 吞吐/排空趋势、drain、gate/headroom、状态构成、latency p95、job_type 热点、CLI handoff |
| `failures_callbacks` | `/internal/jobs-dashboard/sections/failures_callbacks/data` | 30s | 失败聚合、失败样本、callback summary、callback composition、callback 样本、CLI handoff |
| `job_trace` | `/internal/jobs-dashboard/jobs/{job_id}/data` | 0 | 单 Job 证据链追踪 |

`sections` 目前与 `data_sources` 保持同一份配置输出；前端使用它生成导航和 data source route。

当前 page-local controls：

| dataSource | control | binding | param | 用途 |
| --- | --- | --- | --- | --- |
| `recent_jobs` | `status` | query | `status` | 页内状态筛选，允许 `all/queued/running/succeeded/failed` |
| `recent_jobs` | `client_request_id` | query | `client_request_id` | 按调用方幂等请求定位 root Job |
| `recent_jobs` | `limit` | query | `limit` | 限制返回行数 |
| `job_trace` | `job_id` | route | `job_id` | 替换 `/jobs/{job_id}/data` route param |
| `job_trace` | `limit` | query | `limit` | 限制 timeline 等明细行数 |

## Flow And Capacity Contract

`flow_capacity` 是 Phase 2 已落地的只读 data source，用于回答“系统是否在恢复、吞吐是否下降、容量是否不足、慢在哪个阶段”。它复用 dashboard 通用 `window/bucket/caller_id/job_type` 过滤，但不同子块有不同统计口径：

| payload path | 统计口径 | 说明 |
| --- | --- | --- |
| `capacity.current` | `global_gate` | 当前全局 active 占用；不受 `window/job_type/caller_id` 过滤；`headroom` 保留负数，用于暴露超额接单 |
| `capacity.window` | root scope + `created_at` window | 估算窗口内 accepted、terminal、lifecycle p95、accepted_submit_rps 和 active_jobs_needed_upper_bound |
| `drain.current` | family scope current | 按 root `job_type/caller_id` 过滤当前 family active，不按窗口裁剪 |
| `drain.window` | family scope + root `created_at` window | 判断窗口内是否还有 active、running_inactive、failed |
| `drain.stuck` | family scope stuck | 返回 `total/sample/truncated`；`total` 是真实 stuck 数，不是样本行数 |
| `ingress` | root event-time buckets | `created/started/terminal/failed` 分别按各自事件时间分桶 |
| `status_composition` | root `created_at` buckets | dashboard 自有状态构成视图，用于观察 queued/running/succeeded/failed 构成 |
| `latency` | root scope + `created_at` window | `queue_wait_p95_seconds/run_p95_seconds/lifecycle_p95_seconds` |
| `job_type_hotspots` | root scope + `created_at` window + `job_type` group | 按 job_type 展示 active、failed 和 p95 热点 |

`health.next_checks` 会按当前页面筛选条件生成 CLI handoff，例如保留 `--since`、`--bucket`、`--job-type` 和 `--caller-id`。`flow_capacity` payload 不包含 `broker`、`runtime` 或 `db_connection_budget`；这些仍由 `./scripts/jobs.sh broker`、`./scripts/jobs.sh runtime` 和相关显式命令承担。

## Failures And Callbacks Contract

`failures_callbacks` 是 Phase 3 已落地的只读 data source，用于回答“失败集中在哪、callback 是否完成外部通知、哪些样本要进入 Job Trace”。它复用 dashboard 通用 `window/caller_id/job_type` 过滤，不提供写操作、callback replay 或 dispatch replay。

| payload path | 统计口径 | 说明 |
| --- | --- | --- |
| `failure_summary` | family scope + root `created_at` window | 统计 failed records 和 failed root families |
| `failure_groups` | family scope failed records | 按 `error_code/error_kind/failure_phase/detail_type` 聚合；不返回 raw `detail_message` |
| `failed_samples` | family scope failed rows | 返回 root/child、workflow node、callback/attempt/dispatch 状态和 age/duration；`job_id` 可进入 Job Trace |
| `callbacks` | root scope callback_outbox grouped by status | 对齐 `callbacks-summary` 的 status/count/due/age/http 摘要；只返回 `sample_last_error_code` |
| `callback_summary` | `callbacks` 派生 | 为 metric cards 和 callback composition 提供 due/delivered/dead_letter 等计数 |
| `callback_samples` | root scope due/leased/dead_letter callback rows | 返回 callback 处理样本；不返回 raw `last_error`、`last_response`、payload 或 lease token |
| `stuck` | family scope stuck report | 只作为坏消息辅助信号；stuck 主路径仍在 Overview / Flow & Capacity |

`health.status` 的规则是：存在 callback dead_letter 为 `critical`；否则存在 callback due 或 failed records 为 `warning`；否则为 `ok`。`health.next_checks` 会按当前过滤条件生成 `./scripts/jobs.sh failures`、`./scripts/jobs.sh callbacks-summary` 和 `list --status failed --scope family` handoff。

## Renderer Contract

renderer contract 定义在 `app/ops_dashboard/static/chart_contract.js`。它是前端基础设施，不绑定 Job 业务，也不绑定 `jobs.sh` 命令。

当前 renderer 类型：

| rendererType | 类别 | 回答的问题 / 用途 |
| --- | --- | --- |
| `status_line` | HTML | 页面状态和上下文说明 |
| `metric_cards` | HTML | 现在怎么样 |
| `echarts.line` | ECharts | 趋势如何 |
| `echarts.stacked_bar` | ECharts | 构成随时间怎么变 |
| `echarts.horizontal_bar` | ECharts | 谁最多或哪段最重 |
| `html.table` | HTML | 具体是哪几个 |
| `html.signal_list` | HTML | 有哪些短信号或下一步检查 |
| `html.summary_table` | HTML | 一个对象的关键字段摘要 |
| `html.json_block` | HTML | 结构化诊断摘要 |

`RENDERERS` 是唯一 renderer 注册表。新增 renderer 必须注册；未知 `rendererType` 会直接报错，不做静默降级。

## Widget And Layout

`WIDGET_REGISTRY` 是观测视图模块注册表。widget 只声明数据和渲染合同，例如：

```js
{
  "overview.ingress_trend": {
    title: "Ingress",
    question: "created / terminal / failed",
    rendererType: "echarts.line",
    dataSource: "overview",
    dataPath: "ingress",
    xField: "bucket_at",
    series: [
      { name: "created", field: "created" },
      { name: "terminal", field: "terminal" },
      { name: "failed", field: "failed" },
    ],
  },
}
```

`LAYOUT_REGISTRY` 决定 widget 放在哪里，例如：

```js
{
  overview: {
    dataSource: "overview",
    target: "view-root",
    groups: [
      { key: "summary" },
      { key: "main", className: "panel-grid" },
    ],
    placements: [
      { widgetId: "overview.status", target: "status-line" },
      { widgetId: "overview.current_state", group: "summary", chrome: "bare", hostClass: "stat-grid" },
      { widgetId: "overview.ingress_trend", group: "main", hostClass: "chart" },
    ],
  },
}
```

这两个 registry 的关系是：

```text
widgetId 是连接点

LAYOUT_REGISTRY.placements[*].widgetId
        |
        v
WIDGET_REGISTRY[widgetId]
        |
        +--> dataSource -> layout.dataSource -> DATA_SOURCE_REGISTRY / backend config
        |
        +--> rendererType -> RENDERERS
```

## Runtime Path

页面初始化：

```text
GET /internal/jobs-dashboard
  -> static shell: index.html
  -> chart_contract.js
  -> dashboard.js
  -> GET /internal/jobs-dashboard/config
  -> render navigation from backend data_sources / sections
  -> load default section
```

section 数据流：

```text
loadSection("overview" | "recent_jobs" | "flow_capacity" | "failures_callbacks")
  -> resolve route from data source config
  -> merge global filters and page-local query controls
  -> GET section data
  -> renderWidgetLayout(layout, widgets, payload, adapters)
  -> rendererType dispatch
  -> ECharts / HTML output
```

单 Job 追踪：

```text
loadJobTrace(job_id)
  -> set page-local route control job_id
  -> GET /internal/jobs-dashboard/jobs/{job_id}/data
  -> render job_trace layout
  -> summary/json/table widgets share the same renderer pipeline
```

图表示例：

```text
GET /internal/jobs-dashboard/examples
  -> static renderer example shell
  -> chart_contract.js
  -> examples.js
  -> generic widget/layout fixtures
```

## Extension Workflows

### 调整页面布局

只改 `LAYOUT_REGISTRY`：

1. 移动 `placements` 中的 `widgetId`。
2. 调整 `group`、`panelClass`、`hostClass`、顺序或 `target`。
3. 确认 widget 的 `dataSource` 与 layout 的 `dataSource` 一致。
4. 不改后端 read model。
5. 不改 `WIDGET_REGISTRY`。
6. 不改 renderer。

### 新增观测 widget

优先复用现有 renderer：

1. 确认已有 `dataSource` 是否能提供所需 payload。
2. 必要时在后端 read model 增加稳定字段。
3. 在 `WIDGET_REGISTRY` 新增 widget。
4. 在 `LAYOUT_REGISTRY` 新增 placement。
5. 更新测试，确保 widget 引用的 `dataSource`、`rendererType` 和 adapter 都存在。

### 新增 ECharts 图表类型

只有现有 renderer 无法表达时才进入 `chart_contract.js`：

1. 在 `RENDERER_TYPES` 增加 `echarts.<name>`。
2. 在 `RENDERERS` 注册 renderer。
3. 实现 renderer 的 ECharts option 和无 ECharts fallback。
4. 在 `/internal/jobs-dashboard/examples` 增加 generic fixture。
5. 更新 renderer contract 测试和本文档。

### 新增后端 data source

新增页面级数据源时：

1. 在 `DASHBOARD_DATA_SOURCES` 注册 `key/title/route/refresh_seconds`。
2. 在 `router.py` 增加只读 route。
3. 在 `read_model.py` 提供稳定 payload。
4. 在前端 `DATA_SOURCE_REGISTRY` 增加对应 key。
5. 新增 widgets 和 layout placements。
6. 增加 route/read model 测试和 registry 引用完整性测试。

不要为了一个 widget 拆一个 HTTP endpoint；当前默认粒度是 section/data source 级 route。

### 新增 page-local control

页面级控件用于只属于某个 data source 的查询参数，例如 `recent_jobs.status` 或 `job_trace.job_id`：

1. 在 `DASHBOARD_DATA_SOURCES` 对应项声明 control schema。
2. 在前端 `PAGE_CONTROL_REGISTRY` 增加同名 control。
3. 明确 `binding` 是 `route` 还是 `query`。
4. 在 route 中做参数校验。
5. 更新 config 和静态 registry 测试。

## Verification

- 静态注册合同测试：`tests/test_ops_dashboard.py::test_ops_dashboard_static_dashboard_js_declares_renderer_widget_layout_contract`
- renderer 示例测试：`tests/test_ops_dashboard.py::test_ops_dashboard_examples_page_declares_generic_renderer_fixtures`
- 路由和 read model 数据源测试：`tests/test_ops_dashboard.py`
- 配置测试：`tests/test_config.py`

验证重点不是截图是否好看，而是引用闭环是否成立：

```text
layout widgetId exists
widget rendererType exists
widget dataSource exists
layout dataSource matches placed widgets
widget adapter exists
page-local controls declare route/query binding
examples cover public renderer types
examples never fetch live data
```
