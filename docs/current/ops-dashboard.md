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
- 后端提供 data source config、overview、failures、job trace、health。
- 前端负责 renderer contract、widget registry、layout registry、ECharts 渲染和 HTML 渲染。
- `/internal/jobs-dashboard/examples` 是独立静态 renderer 示例页，只使用 generic fixtures，不请求 Job 读模型，也不作为业务 mock 数据源。
- dashboard 不支持业务 mock 数据开关；旧的 `OPS_DASHBOARD_MOCK_DATA_ENABLED` 已废弃，出现在 env 文件或进程环境中都会触发配置加载失败。
- dashboard 不直接读取 Redis broker、Pod runtime、完整 payload 或对象存储内容；这些仍由 `scripts/jobs.sh broker/runtime/payload --full` 等命令承担。

## Registry Layers

当前实现有四个稳定注册层。

```text
app/ops_dashboard/registry.py
  DASHBOARD_DATA_SOURCES
        |
        v
GET /internal/jobs-dashboard/config
  data_sources / sections

app/ops_dashboard/static/dashboard.js
  DATA_SOURCE_REGISTRY
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
| Widget | `app/ops_dashboard/static/dashboard.js` | `rendererType`、`dataSource`、`dataPath` / `adapter`、字段映射、运维问题 | DOM `target`、页面区域、panel 顺序 |
| Layout | `app/ops_dashboard/static/dashboard.js` | 页面 title、dataSource、groups、placements、panel chrome、host class、target | 数据查询逻辑、renderer 实现、业务 SQL |
| Renderer | `app/ops_dashboard/static/chart_contract.js` | 通用渲染器、fallback、HTML escape、ECharts 生命周期 | Job 业务字段、section 路由、页面导航 |

## Data Sources

后端 data source 由 `DASHBOARD_DATA_SOURCES` 注册，并通过 `/internal/jobs-dashboard/config` 暴露给前端。

| key | route | refresh | 当前用途 |
| --- | --- | --- | --- |
| `overview` | `/internal/jobs-dashboard/sections/overview/data` | 15s | 总览健康、容量、趋势、延迟、stuck 样本 |
| `failures` | `/internal/jobs-dashboard/sections/failures/data` | 30s | 失败聚合、失败样本、callback outbox |
| `job_trace` | `/internal/jobs-dashboard/jobs/{job_id}/data` | 0 | 单 Job 证据链追踪 |

`sections` 目前与 `data_sources` 保持同一份配置输出；前端使用它生成导航和 data source route。

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
loadSection("overview" | "failures")
  -> resolve route from data source config
  -> GET section data
  -> renderWidgetLayout(layout, widgets, payload, adapters)
  -> rendererType dispatch
  -> ECharts / HTML output
```

单 Job 追踪：

```text
loadJobTrace(job_id)
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
examples cover public renderer types
examples never fetch live data
```
