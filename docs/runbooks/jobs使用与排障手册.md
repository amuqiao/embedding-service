# `scripts/jobs.sh` 使用与排障手册

本文说明如何用 `scripts/jobs.sh` 查询 Job 状态、定位异步任务问题，并理解需要修改脚本时应改哪里。

本文不负责生产容量模型的完整推导。`MAX_ACTIVE_JOBS` 的估算、K8s 扩容顺序、PostgreSQL/Redis 瓶颈判断属于生产调优方法论，不在本文重复维护。

## 心智模型

`jobs.sh` 是只读排障入口，不是 Job 管理后台。

```text
API 接单
  -> job_aggregates
  -> job_execution_attempts
  -> dispatch_outbox
  -> worker 执行
  -> callback_outbox
  -> job_audit_events
```

排障时先判断问题属于哪一层：

```text
接单是否被挡住?
  -> capacity / summary

Job 是否积压?
  -> summary / list

单个 Job 卡在哪里?
  -> inspect / timeline / attempts / callbacks

是否存在卡住的 lease 或 outbox?
  -> stuck

慢在哪里?
  -> latency

当前支持哪些 job_type?
  -> types
```

最常用的排查顺序：

```text
1. summary 看全局状态
2. capacity 看 MAX_ACTIVE_JOBS 当前水位
3. latency 看 queue/run/lifecycle p95
4. list 找具体异常 Job
5. inspect 深挖单个 Job
6. stuck 查 lease/outbox 卡住
```

## 使用前提

本地开发环境通常直接执行：

```bash
./scripts/dev.sh status
./scripts/jobs.sh --help
```

`jobs.sh` 需要能连接 PostgreSQL：

```text
DATABASE_URL  必填，可来自运行环境或根目录 .env
DB_SSL        可选；false/0/no/off 时追加 sslmode=disable
```

在 Pod 内排障时，原则相同：确保当前环境有正确的 `DATABASE_URL`，然后执行同一组命令。

`--json` 适合脚本、AI 或运维平台解析：

```bash
./scripts/jobs.sh summary --since 10m --json
```

## 命令速查

| 命令 | 用途 | 常用场景 |
| --- | --- | --- |
| `summary` | 汇总 Job、attempt、dispatch、callback 状态 | 第一眼看是否积压、失败、callback 堵塞 |
| `capacity` | 查看全局 active 水位和窗口估算 | 判断是否接近 `MAX_ACTIVE_JOBS` |
| `latency` | 按维度统计生命周期耗时 | 判断慢在排队、执行还是整体生命周期 |
| `list` | 查看最近 Job 摘要 | 找异常 Job 样本 |
| `show` | 查看单个 Job 权威状态 | 只需要 Job 当前事实 |
| `inspect` | 聚合查看单个 Job、attempt、callback、timeline | 单 Job 深挖首选 |
| `timeline` | 查看 Job 事件流 | 追状态流转 |
| `attempts` | 查看执行 attempt | 查 worker、lease、失败阶段 |
| `callbacks` | 查看 callback outbox | 查 callback 投递 |
| `stuck` | 扫描疑似卡住项 | 查 lease 过期、dispatch 未领取、callback 堵塞 |
| `types` | 查看注册的 `job_type` | 确认 Job 类型和 schema |

## 全局排障

### 1. 看当前 10 分钟整体状态

```bash
./scripts/jobs.sh summary --since 10m
```

常看字段：

```text
jobs.active_jobs      当前窗口内 queued + running_active
jobs.queued           已接单但未执行
jobs.running          running 总数
jobs.running_active   running 且 active_attempt_id 非空
jobs.running_inactive workflow root 等待子任务等不占 active 门禁的 running
jobs.failed           失败数量
dispatch.due          到期但待处理的 dispatch
callbacks.due         到期但待处理的 callback
by_job_type           哪类任务贡献了积压
```

按调用方或任务类型过滤：

```bash
./scripts/jobs.sh summary --since 30m --caller-id locust-load
./scripts/jobs.sh summary --since 30m --job-type job_test_echo
```

判断方式：

```text
queued 高、running_active 不高
  -> worker/broker 消费可能不足

running_active 高、run p95 高
  -> Job 执行慢、worker 资源不足或外部依赖慢

dispatch.due 高
  -> dispatch 发布、领取或 broker 路径异常

callbacks.due 高
  -> callback 投递或目标服务异常
```

### 2. 看 MAX_ACTIVE_JOBS 水位

```bash
./scripts/jobs.sh capacity --since 10m --max-active-jobs 750
```

如果不传 `--max-active-jobs`，脚本会尝试读取环境或 `.env` 里的 `MAX_ACTIVE_JOBS`：

```bash
./scripts/jobs.sh capacity --since 10m
```

关键字段：

```text
current.active_jobs
  全局实时门禁口径：queued + running 且 active_attempt_id 非空。

window.accepted_jobs
  估算窗口内创建的 Job 数。

window.accepted_submit_rps
  accepted_jobs / window_seconds。

window.lifecycle_p95_seconds
  Job 从 created_at 到 finished_at 的 p95 生命周期时长。

estimated.active_jobs_needed_upper_bound
  accepted_submit_rps * lifecycle_p95_seconds。
  这是保守上界，不是精确 active 门禁占用时长。

estimated.active_ratio
  current.active_jobs / MAX_ACTIVE_JOBS。

estimated.headroom
  MAX_ACTIVE_JOBS - current.active_jobs。
```

注意：`capacity.current` 是全局实时值，不受 `--since` 限制；`capacity.window` 受 `--since` 限制；`estimated.active_jobs_needed_upper_bound` 来自窗口估算，但 `estimated.active_ratio` 和 `estimated.headroom` 来自全局实时 `current.active_jobs`。

### 3. 看延迟分布

```bash
./scripts/jobs.sh latency --since 30m --group-by job_type
```

可用分组：

```bash
./scripts/jobs.sh latency --since 30m --group-by all
./scripts/jobs.sh latency --since 30m --group-by job_type
./scripts/jobs.sh latency --since 30m --group-by caller_id
./scripts/jobs.sh latency --since 30m --group-by status
```

关键字段：

```text
queue_wait_p95_seconds
  started_at - queued_at/created_at。

run_p95_seconds
  finished_at - started_at。

lifecycle_p95_seconds
  finished_at - created_at。

success_rate
  succeeded / terminal。
```

判断方式：

```text
queue_wait_p95_seconds 高
  -> worker 消费、broker 或 dispatch 路径优先排查

run_p95_seconds 高
  -> Job 执行、worker CPU/内存、外部模型或下游依赖优先排查

lifecycle_p95_seconds 高
  -> 整体停留时间高，会推高容量需求上界

success_rate 低
  -> 先看失败原因，不要先扩容
```

## 单 Job 排障

### 1. 找 Job 样本

```bash
./scripts/jobs.sh list --status queued,running --since 30m --limit 20
./scripts/jobs.sh list --status failed --since 24h --limit 20
./scripts/jobs.sh list --caller-id locust-load --since 10m --limit 20
./scripts/jobs.sh list --client-request-id <client_request_id>
```

`list` 适合找样本，不适合深挖。拿到 `job_id` 后用 `inspect`。

### 2. 聚合查看单个 Job

```bash
./scripts/jobs.sh inspect <job_id>
./scripts/jobs.sh inspect <job_id> --events-limit 50 --json
```

`inspect` 一次返回：

```text
job       Job 当前状态
attempts  执行尝试
callbacks callback outbox
timeline  按 created_at 升序返回的 JobEvent，受 events-limit 限制
```

### 3. 只查某一类证据

```bash
./scripts/jobs.sh show <job_id>
./scripts/jobs.sh timeline <job_id> --limit 50
./scripts/jobs.sh attempts <job_id>
./scripts/jobs.sh callbacks <job_id>
```

常见读法：

```text
timeline
  看状态是否按预期流转。

attempts
  看 worker_id、attempt_status、lease_expires_at、failure_phase、error_kind。

callbacks
  看 callback status、delivery_attempts、last_http_status、last_error。
```

## 卡住扫描

```bash
./scripts/jobs.sh stuck --older-than 10m --limit 50
./scripts/jobs.sh stuck --older-than 30m --json
```

`stuck` 用来找明显不该长期停留的状态，例如：

```text
dispatch_due_not_published
published_dispatch_not_claimed
running_attempt_lease_expired
callback_lease_expired
terminal_callback_not_settled
```

如果 `stuck` 有结果，先看 `issue` 和 `detail`，再用 `inspect <job_id>` 追单个 Job。

## 典型排障路径

### POST /jobs 返回 503

```bash
./scripts/jobs.sh capacity --since 10m --max-active-jobs <当前值>
./scripts/jobs.sh summary --since 10m
./scripts/jobs.sh latency --since 30m --group-by job_type
```

判断：

```text
active_ratio 接近 1，queued/running 能排空，组件健康
  -> 可以按 MAX_ACTIVE_JOBS 估算文档小步调大。

active_ratio 接近 1，但 queued 持续增长
  -> 先扩 worker 消费能力，不要只调大 MAX_ACTIVE_JOBS。

出现 500、TooManyConnectionsError、Pod restart、OOM
  -> 先查硬瓶颈，不要调大 MAX_ACTIVE_JOBS。
```

### 任务积压但没有 503

```bash
./scripts/jobs.sh summary --since 10m
./scripts/jobs.sh latency --since 30m --group-by job_type
./scripts/jobs.sh stuck --older-than 10m
```

判断：

```text
queue_wait_p95 高
  -> worker 数量、WORKER_CONCURRENCY、broker/dispatch。

run_p95 高
  -> Job 执行耗时、外部依赖、worker CPU/内存。

stuck 有结果
  -> 先处理 lease/outbox 卡住问题。
```

### 单个 Job 一直 running

```bash
./scripts/jobs.sh inspect <job_id> --events-limit 50
./scripts/jobs.sh attempts <job_id>
./scripts/jobs.sh timeline <job_id> --limit 100
```

判断：

```text
attempt lease 过期
  -> worker 心跳或恢复路径。

timeline 没有 attempt.claimed
  -> dispatch/broker/worker 领取路径。

timeline 有 attempt.claimed 但没有终态
  -> Job 执行路径、worker 日志、外部依赖。
```

### callback 没送到

```bash
./scripts/jobs.sh callbacks <job_id>
./scripts/jobs.sh inspect <job_id>
./scripts/jobs.sh stuck --older-than 10m
```

判断：

```text
callback status pending/failed 且 due
  -> callback worker 或目标服务。

last_http_status 非 2xx
  -> 目标服务响应问题。

last_error 有网络/超时
  -> 网络、DNS、目标服务可用性。
```

## 修改 `jobs.sh` 时改哪里

当前结构很轻，不需要重构：

```text
scripts/jobs.sh
  Shell 入口，只负责找到 Python 并转发。

scripts/jobs/cli.py
  Typer 命令、参数、退出码、输出 envelope。

scripts/jobs/queries.py
  PostgreSQL 只读 SQL。

scripts/jobs/formatters.py
  JSON、表格、人读输出。

tests/test_dev_scripts.py
  CLI smoke、参数解析、JSON 契约测试。
```

新增排障命令时优先按这个顺序做：

```text
1. 在 queries.py 加只读查询函数。
2. 在 cli.py 加 Typer command。
3. 默认提供 --json。
4. 保持 stdout 纯 JSON，错误进 stderr。
5. 在 tests/test_dev_scripts.py 补最小契约测试。
```

不要在 `jobs.sh` 里增加写操作。取消、重试、补偿、callback 重放都不属于这个入口。

## 边界和容易误读的点

`summary --since` 统计的是指定窗口内创建的 Job 及其关联 attempt/outbox 状态；它不是全库实时总览。

`capacity.current` 是全局实时 active 水位；`capacity.window` 才是窗口统计。不要把两者直接当成同一个时间范围。

`lifecycle_p95_seconds` 是 `finished_at - created_at`。它适合做容量上界估算，但不等于精确 active 门禁占用时长；workflow root 等待子任务时可能不占 active 门禁。

`MAX_ACTIVE_JOBS=0` 表示跳过 active 门禁。生产不建议用它做容量保护。

空结果不等于没有问题。窗口太短、过滤条件不对、`caller_id` 被本地开发开关改写，都可能导致查不到记录。

## 快速命令清单

```bash
./scripts/jobs.sh --help
./scripts/jobs.sh types

./scripts/jobs.sh summary --since 10m
./scripts/jobs.sh capacity --since 10m --max-active-jobs 750
./scripts/jobs.sh latency --since 30m --group-by job_type

./scripts/jobs.sh list --status queued,running --since 30m --limit 20
./scripts/jobs.sh list --status failed --since 24h --limit 20

./scripts/jobs.sh inspect <job_id> --events-limit 50
./scripts/jobs.sh timeline <job_id> --limit 100
./scripts/jobs.sh attempts <job_id>
./scripts/jobs.sh callbacks <job_id>

./scripts/jobs.sh stuck --older-than 10m --limit 50
```
