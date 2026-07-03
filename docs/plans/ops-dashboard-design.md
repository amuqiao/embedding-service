# Ops Dashboard 设计计划

本文记录 `ops_dashboard` 横切模块的计划设计。它不是当前实现事实，当前 Job、Attempt、Dispatch outbox、Callback outbox 和日志事实仍以 `docs/current/` 为准。

目标是在不改变 Job 业务流程的前提下，为 `scripts/jobs.sh` 的高频只读排障能力提供一个可视化 companion。实现前应先确认本文的模块边界、页面布局和阶段范围。

## 背景

当前 `scripts/jobs.sh` 已经承担 Job 只读查询和排障入口，覆盖系统态、恢复态、运输和运行时、单 Job 轨迹等场景。它适合 CLI、Pod 内排障和深度 forensic debugging，但日常排障存在这些摩擦：

- 命令和参数多，维护人员需要记住 `dashboard`、`observe`、`ingress`、`latency`、`failures`、`callbacks-summary`、`trace`、`inspect`、`workflow` 等入口。
- 多条命令结果分散，无法一屏关联 active、stuck、callback backlog、latency 和 failure groups。
- 表格输出不适合观察趋势、drain 方向和 workflow children 关系。
- `--json` 适合机器处理，但人工浏览、复制和对比效率不高。

## 目标

- 提供一个进程内、可开关、可删除的 `app/ops_dashboard/` 横切模块。
- 页面只做只读观测，解决 `jobs.sh` 高频查询的可视化效率问题。
- 保留 `scripts/jobs.sh` 作为 CLI 权威排障入口，保留 `scripts/job-ops.sh` 作为写操作运维入口。
- 支持 dashboard macro view 和单 Job drill-down。
- 使用 section / panel registry 组织页面，方便后续像 `jobs.sh` command 一样增量迭代。

## 非目标

- 不创建、取消、重试、删除、恢复或修改 Job。
- 不 replay dispatch，不 replay callback。
- 不默认展示 full payload；第一版只展示 payload summary / runtime summary / result summary / error summary。
- 不引入独立 exporter、独立 frontend service、Prometheus、Grafana、Loki、Tempo 或新的部署形态。
- 不替代 `scripts/jobs.sh` 的深度排障能力。
- 不把 `ops_dashboard` route 写入公开 API contract。
- 不把 child Job 升级为默认公共查询资源。

## 模块边界

计划目录：

```text
app/ops_dashboard/
  __init__.py
  router.py
  registry.py
  config.py
  read_model.py
  schemas.py
  health_rules.py
  sections/
    overview.py
    throughput.py
    bottlenecks.py
    failures.py
    job_trace.py
  static/
    index.html
    dashboard.css
    dashboard.js
    sections/
      overview.js
      throughput.js
      bottlenecks.js
      failures.js
      job_trace.js
    vendor/
      echarts.min.js
```

主应用只保留一个 optional 挂载点：

```python
def include_optional_ops_dashboard(application: FastAPI) -> None:
    if not settings.ops_dashboard.enabled:
        return

    from app.ops_dashboard.router import router as ops_dashboard_router

    application.include_router(ops_dashboard_router)
```

删除安全边界：

- `OPS_DASHBOARD_ENABLED=false` 时不 import `app.ops_dashboard`。
- 删除 `app/ops_dashboard/` 后，主业务代码仍可启动，前提是 dashboard 处于 disabled 状态。
- 业务 route、Job service、worker runner、callback service 不 import `app.ops_dashboard`。
- `app.ops_dashboard` 可以引用 `app.core.config`、`app.core.database`、`app.core.security` 等基础设施模块，但不引用业务写 service。

禁止依赖：

```text
app.services.jobs.submit_job_request
app.jobs.runner.execute_job
scripts/job-ops.sh 写操作
任何会投递消息、修改状态或触发 callback 的函数
```

## 路由计划

所有 route 使用 internal namespace，不挂在 `SERVICE_API_PREFIX` 下。

```text
GET /internal/jobs-dashboard
GET /internal/jobs-dashboard/config
GET /internal/jobs-dashboard/data
GET /internal/jobs-dashboard/sections/{section}/data
GET /internal/jobs-dashboard/jobs/{job_id}
GET /internal/jobs-dashboard/jobs/{job_id}/data
GET /internal/jobs-dashboard/jobs/{job_id}/json
GET /internal/job-health
```

第一版可以只实现：

```text
GET /internal/jobs-dashboard
GET /internal/jobs-dashboard/config
GET /internal/jobs-dashboard/sections/overview/data
GET /internal/jobs-dashboard/sections/failures/data
GET /internal/jobs-dashboard/jobs/{job_id}/data
GET /internal/job-health
```

所有 route 只允许 `GET`。

## 页面布局

整体布局：

```text
Left nav
  Overview
  Throughput
  Bottlenecks
  Failures
  Job Trace

Top filters
  window
  bucket
  caller_id
  job_type
  refresh
  job search

Content
  current section panels
```

Dashboard 页面默认打开 `Overview`。`Job Trace` 只有在用户输入 `job_id` 或点击 sample 行时加载。

### 整体页面草图

```text
+----------------------+---------------------------------------------------------------+
| Job Ops              | Job Monitoring                                  [10m][1h][24h] |
| FastAPI + Taskiq     | window: 1h  bucket: 1m  caller: all  job_type: all  refresh |
|----------------------|---------------------------------------------------------------|
| > Overview           | Health: WARNING  reasons: callback_due, stuck_jobs            |
|   Throughput         | generated_at: 2026-07-03T03:20:00Z  refresh_after: 15s        |
|   Bottlenecks        |---------------------------------------------------------------|
|   Failures           | [Active] [Queued] [Running] [Failed 1h] [Stuck] [Callback Due] |
|   Job Trace          |---------------------------------------------------------------|
|                      | +-----------------------------+ +---------------------------+ |
|                      | | Job Ingress                 | | Bottlenecks / Doctor      | |
|                      | | created / terminal / failed | | callback_due, stuck, ...  | |
|                      | | line chart                  | | signal list               | |
|                      | +-----------------------------+ +---------------------------+ |
|                      | +-----------------------------+ +---------------------------+ |
|                      | | Latency p95                 | | Failure Groups            | |
|                      | | queue / run / lifecycle     | | grouped table             | |
|                      | | bar chart                   | +---------------------------+ |
|                      | +-----------------------------+ +---------------------------+ |
|                      | | Stuck Samples                                             | |
|                      | | issue | job_id | status | job_type | since | detail       | |
|                      | +-----------------------------------------------------------+ |
+----------------------+---------------------------------------------------------------+
```

### Section 内容草图

每个 section 独立加载和刷新。`Overview` 只放当前判断需要的核心信号；更重的趋势、瓶颈、失败和单 Job 证据放到独立 section。

```text
Overview
  + Health verdict
  + Stat cards
  + Active gate ratio
  + Next checks

Throughput
  + Ingress line chart
  + Drain trend
  + Active samples

Bottlenecks
  + Latency p95 chart
  + Capacity cards
  + Broker depth
  + Runtime summary

Failures
  + Failure groups
  + Callback summary
  + Stuck samples
  + Dead-letter signals

Job Trace
  + Search job_id / client_request_id
  + Job summary
  + Attempts
  + Callbacks
  + Timeline
  + Workflow children
  + Raw JSON drawer
```

### Job Trace 页面草图

```text
+----------------------+---------------------------------------------------------------+
| Job Ops              | Job Trace                                      [job_id input] |
|----------------------|---------------------------------------------------------------|
|   Overview           | Job: 018f9a7f-...                         status: running     |
|   Throughput         | job_type: poster_title_image  caller_id: default              |
|   Bottlenecks        | created_at / started_at / updated_at / progress               |
|   Failures           |---------------------------------------------------------------|
| > Job Trace          | +------------------------+ +-------------------------------+ |
|                      | | Diagnosis              | | Workflow Children             | |
|                      | | severity / reason list | | node | child_job_id | status   | |
|                      | +------------------------+ +-------------------------------+ |
|                      | +-----------------------------------------------------------+ |
|                      | | Timeline                                                  | |
|                      | | created -> queued -> published -> running -> terminal     | |
|                      | +-----------------------------------------------------------+ |
|                      | +------------------------+ +-------------------------------+ |
|                      | | Attempts               | | Callbacks                     | |
|                      | | no | status | lease     | | status | attempts | next_due   | |
|                      | +------------------------+ +-------------------------------+ |
|                      | +-----------------------------------------------------------+ |
|                      | | Raw JSON / Summary Payload Drawer                         | |
|                      | | copy JSON | expand sections | full payload disabled in v1  | |
|                      | +-----------------------------------------------------------+ |
+----------------------+---------------------------------------------------------------+
```

## Section 设计

### Overview

用途：快速判断系统是否健康。

包含：

- Health verdict: `ok` / `warning` / `critical`
- Stat cards: `active_jobs`、`queued`、`running_active`、`running_inactive`、`failed_1h`、`stuck`、`callback_due`
- Active gate ratio
- Top next checks

对应 `jobs.sh`：

```text
dashboard
overview
gate
summary
doctor
```

### Throughput

用途：判断流量、终态速度和 drain 趋势。

包含：

- Ingress line chart: `created`、`started`、`terminal`、`failed`
- Drain trend
- Latest active samples

对应 `jobs.sh`：

```text
ingress
observe
drain
```

### Bottlenecks

用途：判断瓶颈在接单、排队、执行、DB 连接、broker 还是 runtime。

包含：

- Latency p95 chart: `queue_wait_p95_seconds`、`run_p95_seconds`、`lifecycle_p95_seconds`
- Capacity cards
- Broker depth card
- Runtime evidence summary

对应 `jobs.sh`：

```text
latency
capacity
pressure
broker
runtime
```

### Failures

用途：集中查看失败、stuck 和 callback backlog。

包含：

- Failure groups
- Failed samples
- Callback summary
- Stuck samples
- Dead-letter signals

对应 `jobs.sh`：

```text
failures
callbacks-summary
stuck
deleted-summary
```

### Job Trace

用途：用页面替代高频单 Job 查询。

包含：

- Job summary
- Diagnosis
- Attempt timeline
- Callback outbox
- Lifecycle events
- Workflow children
- Raw JSON viewer

对应 `jobs.sh`：

```text
job <job_id>
trace <job_id>
inspect <job_id>
workflow <job_id>
attempts <job_id>
callbacks <job_id>
timeline <job_id>
payload <job_id>
```

第一版 `payload` 只展示 summary，不展示 `--full` 语义。

## Section Registry

后端 registry 草案：

```python
@dataclass(frozen=True)
class DashboardSection:
    key: str
    title: str
    route: str
    refresh_seconds: int
    default_enabled: bool
    collect: Callable[..., Awaitable[dict]]


DASHBOARD_SECTIONS = [
    overview_section,
    throughput_section,
    bottlenecks_section,
    failures_section,
    job_trace_section,
]
```

前端 registry 草案：

```javascript
export const section = {
  key: 'failures',
  title: 'Failures',
  refreshSeconds: 30,
  load: async (filters) => {},
  render: (data) => {},
}
```

约束：

- 一个 section 对应一组排障问题。
- 一个 panel 只展示一个主要信号。
- section 独立查询、独立刷新、独立失败。
- heavy section 不阻塞 `Overview`。

## 页面文案规则

页面面向维护人员，默认使用中文文案；代码、命令、路径、协议名、库名、route、字段名和状态枚举保持英文原文，不强行翻译。

推荐规则：

- 导航、页面标题、按钮、说明、告警原因和空状态提示使用中文。
- `job_id`、`client_request_id`、`caller_id`、`job_type`、`callback_due`、`active_jobs` 等字段名保持英文。
- `FastAPI`、`Taskiq`、`PostgreSQL`、`Redis`、`ECharts`、`GET`、`JSON`、`HTTP` 等技术名保持英文。
- `scripts/jobs.sh`、`/internal/jobs-dashboard`、`/api/v1/ai-jobs` 等命令和路径保持英文原文。
- Job 状态枚举可以保留英文，并用中文说明辅助理解，例如 `running（执行中）`、`failed（失败）`。

示例：

| UI 位置 | 推荐文案 |
|---|---|
| 页面标题 | `Job 观测面板` |
| 导航 | `总览`、`吞吐`、`瓶颈`、`失败`、`Job 追踪` |
| Filter label | `时间窗口`、`时间桶 bucket`、`调用方 caller_id`、`任务类型 job_type` |
| Stat card | `活跃 Job active_jobs`、`排队 queued`、`执行中 running`、`疑似卡住 stuck`、`待投递 callback_due` |
| Table title | `失败分组 Failure Groups`、`Callback 摘要`、`Stuck 样本` |
| Button | `刷新`、`复制 JSON`、`展开详情`、`查询 Job` |
| Empty state | `当前窗口没有 failed Job`、`未发现 stuck 样本` |
| Error state | `查询失败：请回退到 scripts/jobs.sh 查看证据` |

## UI Components

第一版组件类型：

| Component | 用途 |
|---|---|
| Stat card | active、queued、failed、stuck 等当前值 |
| Line chart | ingress、drain trend |
| Bar chart | latency p95、failure group count |
| Table | stuck samples、failed samples、callbacks、attempts |
| Timeline | lifecycle events、attempt stages |
| Tree / grouped table | workflow children |
| JSON drawer | raw JSON / summary payload |
| Badge | status、severity、callback status、dispatch status |
| Filter controls | window、bucket、caller_id、job_type |
| Search input | `job_id` / `client_request_id` |

图标类型建议使用 `lucide` 风格命名，实际实现可用 inline icon、vendored icon 或纯文本 fallback：

| Icon | 用途 |
|---|---|
| `Activity` | Overview / active |
| `Clock` | queued / latency |
| `PlayCircle` | running |
| `CheckCircle` | succeeded |
| `AlertTriangle` | warning / stuck |
| `XCircle` | failed / critical |
| `RefreshCw` | manual refresh |
| `Search` | job search |
| `Filter` | filters |
| `Copy` | copy JSON / job_id |
| `ChevronDown` | expand / collapse |
| `GitBranch` | workflow |
| `Webhook` | callback |
| `Database` | DB evidence |
| `Server` | runtime |

颜色语义：

| Severity | 用途 |
|---|---|
| `ok` / green | 正常、已终态、容量充足 |
| `warning` / amber | backlog、接近阈值、可恢复异常 |
| `critical` / red | stuck、dead letter、持续失败 |
| `neutral` / gray | 未配置、不适用、无数据 |
| `info` / teal/blue | active、throughput、普通趋势 |

## 数据刷新策略

默认：

```text
Overview: 15s
Throughput: 30s
Bottlenecks: 30s
Failures: 30s
Job Trace: manual refresh
```

查询约束：

```text
min refresh interval: 5s
max window: 24h
min bucket: 1m
max sample limit: 100
query timeout: 1s 到 2s
TTL cache: 5s
```

TTL cache 只用于降低重复查询成本，不作为失败 fallback。查询失败应返回错误并在页面显示 section error，不返回过期数据冒充当前事实。

## 数据源和 Read Model

第一版数据源：

```text
PostgreSQL:
  job_aggregates
  job_execution_attempts
  dispatch_outbox
  callback_outbox
  lifecycle events / timeline tables

Redis:
  Taskiq queue key length / pending evidence
```

实现方式：

- `read_model.py` 使用 async SQLAlchemy `text()` 查询。
- 不直接 import `scripts/jobs/queries.py`；CLI 当前用 `psycopg2` sync connection，API runtime 是 async stack。
- 查询语义应对齐 `jobs.sh` 输出口径。必要时在测试中比较 dashboard read model 与 `jobs.sh --json` 的关键字段。

## 安全边界

第一版可以先不做复杂权限系统，但 route 必须是 internal namespace，并且模块默认关闭。

配置草案：

```text
OPS_DASHBOARD_ENABLED=false
OPS_DASHBOARD_REQUIRE_AUTH=true
OPS_DASHBOARD_REFRESH_SECONDS=15
OPS_DASHBOARD_MAX_WINDOW_SECONDS=86400
OPS_DASHBOARD_QUERY_TIMEOUT_SECONDS=2
```

鉴权选择：

- MVP：复用 `require_service_auth`。
- 后续：增加 `require_ops_auth` 和独立 `OPS_DASHBOARD_TOKEN`。

即使 route 只读，也不应公开暴露，因为页面会展示容量、失败类型、caller_id、job_type、队列压力和内部错误分类。

## 与 `jobs.sh` 的关系

Dashboard 是 `jobs.sh` 的 read-only visual companion，不是替代品。

| `jobs.sh` command | Dashboard section |
|---|---|
| `dashboard` / `overview` / `gate` / `summary` / `doctor` | Overview |
| `ingress` / `observe` / `drain` | Throughput |
| `latency` / `capacity` / `pressure` / `broker` / `runtime` | Bottlenecks |
| `failures` / `callbacks-summary` / `stuck` | Failures |
| `job` / `trace` / `inspect` / `workflow` / `attempts` / `callbacks` / `timeline` | Job Trace |
| `payload --full` | CLI only in v1 |

保留 CLI 的场景：

- Pod 内无浏览器环境。
- 需要脚本化输出或 `--json` pipe。
- 需要 full payload。
- 需要深度 forensic debugging。
- 需要写操作时使用 `scripts/job-ops.sh`。

## 分阶段计划

### Phase 1: Read-only Visual Companion

- 新增 optional `app/ops_dashboard/`。
- 实现 lazy import router。
- 实现 `/internal/jobs-dashboard`。
- 实现 `/internal/jobs-dashboard/config`。
- 实现 `Overview`、`Failures`、`Job Trace` summary。
- 页面使用 static HTML/CSS/JS + vendored chart library。
- 不实现写操作，不实现 full payload。

### Phase 2: Throughput and Bottlenecks

- 增加 `Throughput` section。
- 增加 `Bottlenecks` section。
- 增加 broker depth 和 runtime summary。
- 增加 section-level refresh interval。

### Phase 3: Rich Job Trace

- 增加 workflow tree。
- 增加 timeline chart。
- 增加 collapsible raw JSON viewer。
- 增加 copy job_id / copy JSON。
- 增加从 sample row 跳转到 Job Trace。

### Phase 4: Optional Metrics Export

- 如需要历史趋势和外部告警，再增加 `/internal/metrics` 或 `/metrics`。
- 不作为 dashboard v1 前置条件。

## 验收标准

- `OPS_DASHBOARD_ENABLED=false` 时，`app/ops_dashboard/` 不被 import。
- 删除 `app/ops_dashboard/` 后，在 dashboard disabled 情况下主服务仍可启动。
- 所有 dashboard route 都是 `GET`。
- dashboard 不调用任何 Job 写路径、callback replay 或 dispatch replay。
- dashboard 数据口径与 `jobs.sh --json` 的关键字段一致。
- 单个 section 查询失败只影响该 section，页面其他 section 仍可显示。
- 默认不展示 full payload。
- 文档明确该模块不是公开 API contract。
