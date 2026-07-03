# Ops Dashboard

## Purpose

`ops_dashboard` 是可选的只读 internal dashboard，用于把 Job 运维读模型可视化。深度排障入口仍是 `scripts/jobs.sh`；dashboard 只承接常驻总览、失败聚合和单 Job 追踪的只读展示。

## Current Behavior

- 默认关闭，由 `OPS_DASHBOARD_ENABLED` 控制。
- 路由固定在 `/internal/jobs-dashboard`，不进入 OpenAPI。
- 后端只提供数据事实：overview、failures、job trace、health。
- 前端负责图表合同、面板注册、ECharts 渲染和 HTML table 渲染。
- `/internal/jobs-dashboard/examples` 是独立静态图表示例页，只使用 generic chart fixtures，不请求 Job 读模型，也不作为业务 mock 数据源。
- dashboard 不支持业务 mock 数据开关；旧的 `OPS_DASHBOARD_MOCK_DATA_ENABLED` 已废弃，出现在 env 文件或进程环境中都会触发配置加载失败。
- dashboard 不直接读取 Redis broker、Pod runtime、完整 payload 或对象存储内容；这些仍由 `scripts/jobs.sh broker/runtime/payload --full` 等命令承担。

## Chart Contract

图表合同是前端基础设施，定义在 `app/ops_dashboard/static/chart_contract.js`。它不绑定 Job 业务，也不绑定 `jobs.sh` 命令。

当前图表类型：

| chartType | 回答的问题 | 当前 renderer |
| --- | --- | --- |
| `stat_card` | 现在怎么样 | 大数字卡片 |
| `line` | 趋势如何 | ECharts 折线图 |
| `stacked_bar` | 构成随时间怎么变 | ECharts 堆叠柱状图 |
| `horizontal_bar` | 谁最多或哪段最重 | ECharts 横向条形图 |
| `table` | 具体是哪几个 | HTML table |

`CHART_RENDERERS` 是唯一的 renderer 注册表。新增图表类型必须先注册 renderer；未知 `chartType` 会直接报错，不做静默降级。

图表示例页定义在 `app/ops_dashboard/static/examples.html` 和 `app/ops_dashboard/static/examples.js`，用于验证 `stat_card`、`line`、`stacked_bar`、`horizontal_bar`、`table` 五种 chart contract 的基础渲染。示例数据只使用 generic 字段，不能引入 `job_id`、`attempt_id`、业务 job type 或 callback 等 Job 语义。

## Panel Registry

`PANEL_REGISTRY` 定义在 `app/ops_dashboard/static/dashboard.js`，是当前 Job dashboard 的业务绑定层。每个 panel 至少表达：

| 字段 | 含义 |
| --- | --- |
| `key` | panel 稳定标识 |
| `question` | 这个 panel 回答的运维问题 |
| `chartType` | 使用的通用图表类型 |
| `target` | 页面 DOM 目标 |
| `dataPath` / `adapter` | 从 payload 取数或通过命名 adapter 派生 rows |
| `xField` | 时间桶或分类横轴字段，仅用于 `line` / `stacked_bar` |
| `series` | 图表序列字段，仅用于 `line` / `stacked_bar` |
| `labelField` / `valueField` | 标签和值字段，仅用于 `horizontal_bar` |
| `columns` | 表格列定义，仅用于 table panel |
| `emptyText` | 空数据展示文案 |

`jobs.sh` 心智模型只影响 panel 的组织方式：总览优先回答当前健康、趋势和样本；失败页回答错误集中度和失败明细；Job 追踪页回答 attempt、AI call、子任务、timeline 和 callback 证据链。它不进入 `CHART_RENDERERS`。

当前 `stacked_bar` renderer 已注册，但 v1 页面暂未绑定可见 panel。

`PANEL_DATA_ADAPTERS` 存放少量前端行数据整形函数。`PANEL_REGISTRY` 只通过 `adapter` 名称引用它们，不内联 payload 整形逻辑。

## Extension Rules

当前图表骨架已经稳定。后续大多数 dashboard 迭代只需要完成两件事：

1. 后端 read model 在 payload 中提供稳定数据字段。
2. 前端在 `PANEL_REGISTRY` 中新增或调整 panel 配置。

新增常规 panel 时优先复用现有 `chartType`，不要直接改 `chart_contract.js`。例如新增失败趋势堆叠图时，前端只需要声明：

```js
{
  key: "failure_trend",
  question: "失败趋势如何",
  chartType: "stacked_bar",
  target: "failure-trend-chart",
  dataPath: "failure_trend",
  xField: "bucket_at",
  series: [
    { name: "succeeded", field: "succeeded" },
    { name: "failed", field: "failed" },
    { name: "retrying", field: "retrying" },
  ],
  colors: ["#12805c", "#c9342f", "#b76e00"],
}
```

对应 payload 只需要提供同名 rows：

```json
{
  "failure_trend": [
    {
      "bucket_at": "2026-07-03T10:00:00Z",
      "succeeded": 10,
      "failed": 2,
      "retrying": 1
    }
  ]
}
```

只有以下情况才进入 `chart_contract.js`：

- 需要新增通用图表类型，例如 heatmap、scatter。
- 现有图表类型缺少通用能力，例如 `line` 需要阈值 `markLine` 或双 Y 轴。
- 表格需要通用交互能力，例如排序、分页、行展开。

业务数据字段、panel 布局、`jobs.sh` 心智模型映射都留在 `PANEL_REGISTRY` / read model 层，不进入 `CHART_RENDERERS`。

## Runtime Path

```text
GET /internal/jobs-dashboard
  -> static dashboard shell
  -> GET /internal/jobs-dashboard/config
  -> GET /internal/jobs-dashboard/sections/{overview|failures}/data
  -> app/ops_dashboard/read_model.py
  -> app/ops_dashboard/static/chart_contract.js
  -> app/ops_dashboard/static/dashboard.js
  -> CHART_RENDERERS + PANEL_REGISTRY
```

单 Job 追踪：

```text
GET /internal/jobs-dashboard/jobs/{job_id}/data
  -> read_model.job_trace_data()
  -> PANEL_REGISTRY.job_trace table panels
```

图表示例：

```text
GET /internal/jobs-dashboard/examples
  -> static chart example shell
  -> app/ops_dashboard/static/chart_contract.js
  -> app/ops_dashboard/static/examples.js
  -> generic chart fixtures
```

## Verification

- 静态合同测试：`tests/test_ops_dashboard.py::test_ops_dashboard_static_dashboard_js_declares_chart_contract`
- 图表示例测试：`tests/test_ops_dashboard.py::test_ops_dashboard_examples_page_declares_generic_chart_fixtures`
- 路由和 read model 数据源测试：`tests/test_ops_dashboard.py`
- 配置测试：`tests/test_config.py`
