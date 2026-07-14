# Ops Dashboard 当前模型

`ops_dashboard` 是可选的只读 internal dashboard，用于把 Job 运维读模型可视化。深度排障入口仍是 `scripts/jobs.sh`，写操作仍由 `scripts/job-ops.sh` 承担。

## 当前边界

- 默认关闭，由 `OPS_DASHBOARD_ENABLED` 控制。
- 路由固定在 `/internal/jobs-dashboard`，不进入 OpenAPI。
- `OPS_DASHBOARD_REQUIRE_AUTH` 默认开启；本地隔离排查时才显式关闭。
- Dashboard 会展示完整 Job 输入/输出 JSON，不应开放给不可信角色。
- 不支持业务 mock 数据开关；旧 `OPS_DASHBOARD_MOCK_DATA_ENABLED` 出现在 env 文件或进程环境中会 fail-fast。
- 不直接读取 Redis broker、Pod runtime 或对象存储内容；这些仍由 `scripts/jobs.sh` 和对应 runbook 承担。
- 不提供 dispatch replay、callback replay、delete/restore 或其他写操作。

## 分层模型

当前 dashboard 按“数据、视图、渲染、摆放”分层：

```text
Backend Data Source
  -> Frontend Widget
  -> Renderer
  -> Layout
```

| 层 | 当前文件 | 拥有内容 | 不拥有 |
|---|---|---|---|
| Backend Data Source | `app/ops_dashboard/registry.py` / `router.py` | data source key、route、refresh、read model payload | ECharts 类型、DOM 位置 |
| Page-local Control | `registry.py` / `dashboard.js` | 控件 key、绑定方式、query/route param | SQL 和 renderer 实现 |
| Widget | `app/ops_dashboard/static/dashboard.js` | `rendererType`、`dataSource`、`dataPath` / adapter、字段映射 | DOM target 和页面顺序 |
| Layout | `dashboard.js` | 页面、分区、顺序、panel chrome | 数据查询和 renderer 实现 |
| Renderer | `chart_contract.js` | 通用 ECharts / HTML / JSON 渲染 | Job 业务语义 |

一个 layout 对应一个 data source；该 layout 内 widget 必须声明同一个 data source。运行时会 fail-fast 校验，避免 widget 被放进不匹配的 payload 上下文。

## Data Sources

后端 data source 由 `DASHBOARD_DATA_SOURCES` 注册，并通过 `/internal/jobs-dashboard/config` 暴露给前端。

| key | route | 当前用途 |
|---|---|---|
| `overview` | `/internal/jobs-dashboard/sections/overview/data` | 总览健康、容量、趋势、延迟、成功率、stuck 样本 |
| `recent_jobs` | `/internal/jobs-dashboard/sections/recent_jobs/data` | root Job 选择器，支持页内 `status/job_id/limit` |
| `flow_capacity` | `/internal/jobs-dashboard/sections/flow_capacity/data` | 吞吐、drain、容量、延迟、job_type 热点和 CLI handoff |
| `failures_callbacks` | `/internal/jobs-dashboard/sections/failures_callbacks/data` | 失败聚合、失败样本、callback summary 和 callback 样本 |
| `job_trace` | `/internal/jobs-dashboard/jobs/{job_id}/data` | 单 Job 证据链追踪 |

`/internal/jobs-dashboard/examples` 是独立静态 renderer 示例页，只使用 generic fixtures，不请求 Job 读模型，也不是业务 mock 数据源。

## 过滤合同

除 `job_trace` 外，section data source 复用同一个 `DashboardFilters`：

| 过滤 | 当前语义 |
|---|---|
| `window` | 相对时间窗口，默认 `1h`，受 `OPS_DASHBOARD_MAX_WINDOW_SECONDS` 限制 |
| `caller_id` | root scope caller 过滤 |
| `job_type` | root scope job type 过滤 |
| `run_id` | 压测 run id，来自 Job metadata |
| `resolved_bucket` | 服务端按 `window` 派生，只出现在 payload / CLI handoff 中 |

`bucket`、`from` 和 `to` 不是 dashboard query 合同；传入会返回 400。`read_model` 使用半开区间 `[now - window, now)`，`generated_at` 是展示生成时间，不是 query upper bound。

## 当前页面

Dashboard 当前有 5 个一级 tab，按运维问题划分：

| tab | 回答的问题 |
|---|---|
| `Overview` | 当前是否健康，下一步看哪里 |
| `Recent Jobs` | 最近 root Job 是哪些，如何进入单 Job Trace |
| `Flow & Capacity` | 系统是在恢复还是恶化，容量和延迟卡在哪 |
| `Failures & Callbacks` | 失败集中在哪，callback 是否完成外部通知 |
| `Job Trace` | 单个 Job 的请求、响应、attempt、AI call、child 和 callback 证据链 |

`Recent Jobs` 固定为 root 视角，不提供 `scope` 控件。child / family 证据从 `Job Trace`、`Failures & Callbacks` 或 `./scripts/jobs.sh list --scope family` 进入。

## Renderer 类型

当前 renderer 类型包括：

```text
status_line
metric_cards
echarts.line
echarts.stacked_bar
echarts.horizontal_bar
html.table
html.signal_list
html.summary_table
html.json_block
```

Renderer 是通用展示能力，不知道 Job 业务语义。新增 renderer 只有在现有 renderer 无法表达新视图时才考虑；普通新增观测视图优先复用已有 renderer。

## Runtime Path

```text
GET /internal/jobs-dashboard
  -> static HTML / JS / CSS
  -> GET /internal/jobs-dashboard/config
  -> load section data
  -> render widget layout

GET /internal/jobs-dashboard/jobs/{job_id}/data
  -> root/family/job request/callback/attempt/timeline read model
```

Job Trace 为展示完整 Job 请求 JSON，会通过 `job_params_ref` 展开创建 Job 时保存的 runtime JSON；引用缺失或损坏会直接暴露查询错误。

## 扩展边界

- 调整页面位置优先改 layout。
- 新增观测视图优先改 widget + layout。
- 新增 data source 必须注册后端 route、read model 和前端 data source。
- 写操作不得进入 dashboard；继续由 `job-ops.sh` 承担。
- 长窗口分析、table pagination、timeline/tree renderer、环境诊断和更强安全边界属于 [`../plans/ops-dashboard-post-mvp.md`](../plans/ops-dashboard-post-mvp.md)。

## 验证

- `tests/test_ops_dashboard.py`
- `./scripts/verify.sh check`
