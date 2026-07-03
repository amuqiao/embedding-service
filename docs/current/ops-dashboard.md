# Ops Dashboard

## Purpose

`ops_dashboard` 是可选的只读 internal dashboard，用于把 Job 运维读模型可视化。深度排障入口仍是 `scripts/jobs.sh`；dashboard 只承接常驻总览、失败聚合和单 Job 追踪的只读展示。

## Current Behavior

- 默认关闭，由 `OPS_DASHBOARD_ENABLED` 控制。
- 路由固定在 `/internal/jobs-dashboard`，不进入 OpenAPI。
- `OPS_DASHBOARD_MOCK_DATA_ENABLED=true` 时使用内置模拟数据，响应和页面会标识 `data_source: mock`。
- 后端只提供数据事实：overview、failures、job trace、health。
- 前端负责图表合同、面板注册、ECharts 渲染和 HTML table 渲染。
- dashboard 不直接读取 Redis broker、Pod runtime、完整 payload 或对象存储内容；这些仍由 `scripts/jobs.sh broker/runtime/payload --full` 等命令承担。

## Chart Contract

图表合同是前端基础设施，定义在 `app/ops_dashboard/static/dashboard.js`。它不绑定 Job 业务，也不绑定 `jobs.sh` 命令。

当前图表类型：

| chartType | 回答的问题 | 当前 renderer |
| --- | --- | --- |
| `stat_card` | 现在怎么样 | 大数字卡片 |
| `line` | 趋势如何 | ECharts 折线图 |
| `stacked_bar` | 构成随时间怎么变 | ECharts 堆叠柱状图 |
| `horizontal_bar` | 谁最多或哪段最重 | ECharts 横向条形图 |
| `table` | 具体是哪几个 | HTML table |

`CHART_RENDERERS` 是唯一的 renderer 注册表。新增图表类型必须先注册 renderer；未知 `chartType` 会直接报错，不做静默降级。

## Panel Registry

`PANEL_REGISTRY` 是当前 Job dashboard 的业务绑定层。每个 panel 至少表达：

| 字段 | 含义 |
| --- | --- |
| `key` | panel 稳定标识 |
| `question` | 这个 panel 回答的运维问题 |
| `chartType` | 使用的通用图表类型 |
| `target` | 页面 DOM 目标 |
| `dataPath` / `rows` | 从 payload 取数或派生 rows |
| `series` | 图表序列字段，仅用于图表类 panel |
| `columns` | 表格列定义，仅用于 table panel |
| `emptyText` | 空数据展示文案 |

`jobs.sh` 心智模型只影响 panel 的组织方式：总览优先回答当前健康、趋势和样本；失败页回答错误集中度和失败明细；Job 追踪页回答 attempt、AI call、子任务、timeline 和 callback 证据链。它不进入 `CHART_RENDERERS`。

当前 `stacked_bar` renderer 已注册，但 v1 页面暂未绑定可见 panel。

`PANEL_DATA_ADAPTERS` 存放少量前端行数据整形函数。`PANEL_REGISTRY` 只通过 `adapter` 名称引用它们，不内联 payload 整形逻辑。

## Runtime Path

```text
GET /internal/jobs-dashboard
  -> static dashboard shell
  -> GET /internal/jobs-dashboard/config
  -> GET /internal/jobs-dashboard/sections/{overview|failures}/data
  -> app/ops_dashboard/read_model.py
  -> app/ops_dashboard/static/dashboard.js
  -> CHART_RENDERERS + PANEL_REGISTRY
```

单 Job 追踪：

```text
GET /internal/jobs-dashboard/jobs/{job_id}/data
  -> read_model.job_trace_data()
  -> PANEL_REGISTRY.job_trace table panels
```

## Verification

- 静态合同测试：`tests/test_ops_dashboard.py::test_ops_dashboard_static_dashboard_js_declares_chart_contract`
- 路由和 mock/live 数据源测试：`tests/test_ops_dashboard.py`
- 配置测试：`tests/test_config.py`
