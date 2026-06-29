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
  -> diagnose / inspect / timeline / attempts / callbacks

是否存在卡住的 lease 或 outbox?
  -> stuck

慢在哪里?
  -> latency

当前支持哪些 job_type?
  -> types
```

最常用的排查顺序：

```text
1. pressure 在压测后汇总 HTTP、capacity、latency、stuck、failed 和 API log，先判断瓶颈方向
2. drain 判断压测前后是否已经排空，未排空时不要开始下一档
3. doctor 在非压测场景下先判断当前窗口是否需要处理，以及下一步查什么
4. summary 看 Job、attempt、dispatch、callback 概览
5. capacity 看 MAX_ACTIVE_JOBS 当前水位
6. latency 看 queue/run/lifecycle p95
7. list 找具体异常 Job
8. diagnose 快速判断单个 Job 的 claim / dispatch / callback 风险
9. inspect 深挖单个 Job 原始证据
10. stuck 查 lease/outbox 卡住
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
DB_SSL        可选；false/0/no/off 且 DATABASE_URL 未显式配置 sslmode 时，使用 sslmode=disable
```

在 Pod 内排障时，原则相同：确保当前环境有正确的 `DATABASE_URL`，然后执行同一组命令。

`--json` 适合脚本、AI 或运维平台解析：

```bash
./scripts/jobs.sh summary --since 10m --json
./scripts/jobs.sh doctor --since 10m --json
./scripts/jobs.sh diagnose <job_id> --json
```

默认输出面向人读，会主动压缩 payload、分 section 展示结论和下一步命令。`--json` 输出稳定结构，stdout 只包含 JSON，适合自动化解析；不要用人读表格做脚本解析。

## 命令速查

| 命令 | 用途 | 常用场景 |
| --- | --- | --- |
| `pressure` | 聚合压测窗口并给出瓶颈方向 | 每一档 Locust 压测后首选 |
| `doctor` | 对 summary 结果做诊断并给下一步命令 | 新维护人员或不确定从哪里查时先用 |
| `diagnose` | 诊断单个 Job 的 attempt、dispatch、callback、dead-letter 和 claim 风险 | 已有 `job_id`，需要快速判断卡在哪里 |
| `drain` | 判断当前 scope 是否还有 active 或 stuck 证据 | 压测前后判断是否可以进入下一档 |
| `summary` | 汇总 Job、attempt、dispatch、callback 状态 | 看当前窗口的关键计数和水位 |
| `capacity` | 查看全局 active 水位和窗口估算 | 判断是否接近 `MAX_ACTIVE_JOBS` |
| `latency` | 按维度统计生命周期耗时 | 判断慢在排队、执行还是整体生命周期 |
| `list` | 查看最近 Job 摘要 | 找异常 Job 样本 |
| `show` | 查看单个 Job 权威状态 | 只需要 Job 当前事实 |
| `inspect` | 聚合查看单个 Job、diagnosis、attempt、callback、timeline | 单 Job 深挖首选 |
| `timeline` | 查看 Job 事件流 | 追状态流转 |
| `attempts` | 查看执行 attempt | 查 worker、lease、失败阶段 |
| `callbacks` | 查看 callback outbox | 查 callback 投递 |
| `stuck` | 扫描疑似卡住项 | 查 lease 过期、dispatch 未领取、callback 堵塞 |
| `types` | 查看注册的 `job_type` | 确认 Job 类型和 schema |

## 全局排障

### 1. 压测后先做一键诊断

每一档 Locust 压测结束后，优先用 `pressure`，不要先手工分别查 CSV、日志、failed Job 和 stuck：

```bash
./scripts/jobs.sh pressure \
  --since 10m \
  --caller-id default \
  --max-active-jobs 1000 \
  --locust-prefix .run/load/<run-prefix> \
  --api-log logs/api.log \
  --api-log-tail 2000
```

如果不传 `--max-active-jobs`，脚本会读取环境或 `.env` 中的 `MAX_ACTIVE_JOBS`。

`pressure` 会合并这些证据：

```text
Locust CSV
  POST /jobs 请求数、失败数、p95/p99、HTTP 500/503 失败分布。

capacity
  全局 active 水位、窗口接单数、终态数、估算 active 需求。

latency
  queue wait、run、lifecycle p95 和终态成功率。

stuck / failed samples
  lease、dispatch、callback 卡住证据，以及 failed Job 分组。

API log
  在 --api-log-tail 范围内按日志时间过滤到 --since 窗口后，扫描 TooManyConnectionsError、900500、Traceback、容量门禁日志签名。
```

常见信号：

```text
status=ok
  -> 当前窗口没有明显瓶颈信号；仍需 drain --strict 通过后才能进入下一档。

signal=http_503_gate_hit
  -> 主要是 503 容量门禁；如果 health 仍 200 且可排空，说明保护生效。

signal=http_5xx
  -> 出现 HTTP 500 或非 503 的 5xx；不要继续升压，先查 API/DB/Redis/日志。

signal=http_failures_db_mismatch
  -> Locust 大量失败但 DB 接单数对不上，常见于服务不可达、路径/前缀错误、认证错误或响应体不是 Job JSON。

signal=api_log_db_connection_pressure / db_connection_pressure
  -> 日志或 failed Job 明确出现数据库连接耗尽；先治理连接池、PostgreSQL 连接上限、API/worker 并发，不要继续放大 MAX_ACTIVE_JOBS。

signal=window_not_terminal
  -> 压测窗口尚未全部到终态；当前 lifecycle p95 和容量估算只能当中间态。

signal=published_dispatch_not_claimed
  -> dispatch 已发布但 worker 未领取；先查 worker/broker 消费。
```

注意：API log 过滤依赖应用日志行首的 `YYYY-MM-DD HH:MM:SS,mmm` 时间戳，Traceback 的续行会归到前一个有时间戳的日志事件。连续压多档时，优先给每档使用独立日志或收窄 `--since` / `--api-log-tail`，避免上一档的异常污染本档判断。

`pressure` 返回 `critical` 时，本档直接判定不通过。下一步通常是：

```bash
./scripts/jobs.sh drain --since 10m --caller-id default --older-than 1m --strict
./scripts/jobs.sh list --status failed --since 10m --caller-id default --limit 20
./scripts/jobs.sh inspect <job_id> --events-limit 50
./scripts/dev.sh status
```

### 2. 非压测场景先让脚本判断下一步

```bash
./scripts/jobs.sh doctor --since 10m
```

`doctor` 是给人读的诊断入口。它使用 `summary` 同一组只读数据，但会把结果转成：

```text
当前状态是否需要关注
关键发现是什么
下一步建议执行哪些命令
```

空窗口会直接说明当前 `--since` 范围内没有 Job，并提示扩大窗口、查看最近列表或确认服务状态。维护人员不需要先对照字段表才能判断 `total=0` 是什么含义。

需要机器解析时使用：

```bash
./scripts/jobs.sh doctor --since 10m --json
```

### 3. 判断是否排空

压测前和每一档压测后，先用 `drain` 判断是否还有 active Job 或疑似 stuck 证据：

```bash
./scripts/jobs.sh drain --since 30m --caller-id default
./scripts/jobs.sh drain --since 30m --caller-id default --strict --json
```

`--strict` 适合脚本化压测；只要当前 scope 未排空就返回非 0。不要只用 `list --status queued,running --since 30m` 判断排空，因为这个命令按 `created_at` 窗口筛选，可能漏掉窗口外创建但仍占全局 active 门禁的老 Job。

关键字段：

```text
current.active_jobs
  当前 scope 的实时 active 计数。

window.active_jobs
  当前 --since 窗口内创建的 active Job。

stuck.total
  当前 --since 和 --older-than 条件下发现的疑似 stuck 数量。
```

判断方式：

```text
status=drained
  -> 当前 scope 没有 active、running_inactive、failed 或 stuck 证据，可以进入下一档压测或结束本轮观察。

status=not_drained 且 stuck.total=0
  -> 还有 queued/running/running_inactive 或 failed，先等待排空或 inspect 失败样本，再复查 latency 和 capacity。

status=not_drained 且 stuck.total>0
  -> 先看 stuck.sample，再 inspect 对应 job_id。
```

压测刚结束时可以把 stuck 判定窗口调短，快速定位本轮残留：

```bash
./scripts/jobs.sh drain --since 20m --caller-id default --older-than 1m --json
./scripts/jobs.sh stuck --since 20m --caller-id default --older-than 1m --json
```

### 4. 看当前 10 分钟整体状态

```bash
./scripts/jobs.sh summary --since 10m
```

`summary` 默认输出人读摘要，适合快速确认关键计数。需要完整字段时使用：

```bash
./scripts/jobs.sh summary --since 10m --json
```

JSON 中常看字段：

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

### 5. 看 MAX_ACTIVE_JOBS 水位

```bash
./scripts/jobs.sh capacity --since 10m --caller-id default --max-active-jobs <当前值>
```

如果不传 `--max-active-jobs`，脚本会尝试读取环境或 `.env` 里的 `MAX_ACTIVE_JOBS`：

```bash
./scripts/jobs.sh capacity --since 10m --caller-id default
```

关键字段：

```text
current.active_jobs
  全局实时门禁口径：queued + running 且 active_attempt_id 非空。

window.accepted_jobs
  估算窗口内创建的 Job 数。

window.accepted_submit_rps
  accepted_jobs / effective_window_seconds。
  effective_window_seconds 优先使用 first_created_at 到 newest_created_at 的实际跨度；没有跨度时退回 --since 秒数。

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

如果传了 `--caller-id` 或 `--job-type`，过滤只作用于 `capacity.window` 的估算口径；`capacity.current` 仍然是全局 active 门禁口径。这样既能按本轮压测 caller 估算 RPS，又不会误读全局门禁水位。

压测刚结束且 `terminal_jobs < accepted_jobs` 时，不要过早采信 lifecycle p95 和容量上界。先等 `drain` 排空，或至少明确当前估算只代表“尚未完全终态”的中间状态。

### 6. 看延迟分布

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

`list` 适合找样本，不适合深挖。拿到 `job_id` 后先用 `diagnose` 判断风险，再用 `inspect` 深挖原始证据。

### 2. 先看单 Job 诊断结论

已经拿到 `job_id` 时，先用 `diagnose` 看脚本给出的风险分类：

```bash
./scripts/jobs.sh diagnose <job_id>
./scripts/jobs.sh diagnose <job_id> --include-children
./scripts/jobs.sh diagnose <job_id> --older-than 1m
./scripts/jobs.sh diagnose <job_id> --json
```

`diagnose` 是单 Job 入口，和窗口级 `doctor` 不同：

| 命令 | 作用域 | 适合读者 |
|---|---|---|
| `doctor --since 10m` | 一个时间窗口内的整体 Job / dispatch / callback 计数 | 人工巡检、压测外的全局判断 |
| `diagnose <job_id>` | 单个 Job 的 attempt、dispatch、callback、claim 风险 | 已有异常 Job 样本后的快速定位 |

默认情况下，`diagnose` 和 `inspect` 一样只看 root job 的证据。排查 workflow root 等待 child 的问题时，显式加 `--include-children`。

`--older-than` 控制“刚发布 / 刚到期”的状态什么时候从 `info` 升为 `warning`，默认是 `1m`。这能避免刚入队、刚 dispatch、刚到期重试的正常瞬时窗口被当成异常。

人读输出会显示：

```text
Job Diagnosis
  severity / area / signal / message

Next Checks
  下一步建议命令
```

JSON 输出会保留结构化字段：

```text
diagnosis.status
diagnosis.findings[].severity
diagnosis.findings[].area
diagnosis.findings[].signal
diagnosis.findings[].evidence
diagnosis.next_checks[]
```

常见 `signal`：

| signal | 含义 | 下一步 |
|---|---|---|
| `published_dispatch_not_claimed` | dispatch 已发布，但 attempt 还没被 worker claim | 短时间内看作 `info`；超过 `--older-than` 后看 worker 日志、timeline、`stuck --older-than 1m` |
| `dispatch_due` | dispatch 到期但仍未发布 | 短时间内看作 `info`；超过 `--older-than` 后查 outbox 发布和 broker |
| `dispatch_dead_letter` | Taskiq 发布路径已 dead-letter | 查 `dispatch_last_error`，先不要重跑业务 |
| `running_attempt_lease_expired` | running attempt lease 已过期 | 查 worker 心跳和 recovery |
| `callback_due` | callback 到期等待投递或重试 | 短时间内看作 `info`；超过 `--older-than` 后查 callback worker 和目标服务 |
| `callback_dead_letter` | callback 投递已 dead-letter | Job 终态不受影响，但回调没有送达 |
| `job_waiting_children` | workflow root 正在等待 child | 用 `inspect --include-children` 查看 child |
| `workflow_child_failed` | workflow child 已 failed | inspect failed child 的 attempt/error |

### 3. 聚合查看单个 Job 原始证据

```bash
./scripts/jobs.sh inspect <job_id>
./scripts/jobs.sh inspect <job_id> --include-children
./scripts/jobs.sh inspect <job_id> --events-limit 50 --json
```

`inspect` 一次返回：

```text
job        Job 当前状态
diagnosis  单 Job 风险摘要；人读输出中展示为 Diagnosis section，JSON 输出中是 diagnosis 字段
attempts   执行尝试
callbacks  callback outbox
timeline   按 created_at 升序返回的 JobEvent，受 events-limit 限制
children   只有传入 --include-children 时返回 workflow internal child jobs
```

默认人读输出适合维护人员扫读，会隐藏或压缩长 payload。`--json` 会保留完整 `job`、`attempts`、`callbacks`、`timeline` 和 `diagnosis`，适合复制给 AI 或自动化工具继续分析。`inspect` 的 `diagnosis` 也遵循 root-only 默认；只有加 `--include-children` 时才把 child Job 状态纳入诊断。

### 4. 只查某一类证据

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
./scripts/jobs.sh stuck --older-than 30m --caller-id default --json
./scripts/jobs.sh stuck --older-than 1m --since 20m --caller-id default --json
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

压测排障时优先加 `--caller-id` 和 `--since`，避免历史数据或其他调用方污染本轮判断。`published_dispatch_not_claimed` 表示 dispatch 已发布但 attempt 仍未被 worker 领取，通常下一步看 worker 日志、broker 消费和 `inspect <job_id>` 的 timeline。

## 典型排障路径

### 压测后出现 HTTP 500

先不要继续提高 `MAX_ACTIVE_JOBS` 或 Locust 用户数，用 `pressure` 直接聚合本轮证据：

```bash
./scripts/jobs.sh pressure \
  --since 10m \
  --caller-id default \
  --max-active-jobs <当前值> \
  --locust-prefix .run/load/<run-prefix> \
  --api-log logs/api.log \
  --api-log-tail 2000
```

如果输出同时包含下面信号，瓶颈已经可以定位到数据库连接预算，而不是 `MAX_ACTIVE_JOBS` 门禁：

```text
critical http     http_5xx
critical database api_log_db_connection_pressure
critical execution job_failures
critical database db_connection_pressure
```

继续确认失败样本：

```bash
./scripts/jobs.sh list --caller-id default --status failed --since 10m --limit 20
./scripts/jobs.sh inspect <job_id> --events-limit 50
```

如果 `failure_phase=execute` 且错误类型是 `TooManyConnectionsError`，本档压测不通过。下一步是降低入口速率、降低 `MAX_ACTIVE_JOBS`，或治理 PostgreSQL 连接池和 API/worker 总连接数。

本地 `MAX_ACTIVE_JOBS=1000` 的一次 `submit -u 20 -r 10 -t 30s` 复测中，Locust 记录 `POST /jobs` 562 次请求、2 次 HTTP 500。`pressure` 给出的关键结果是：

```text
status=critical
http.post_jobs.failure_count=2
api_log.matches.too_many_connections=9
failure_groups[0].detail_type=TooManyConnectionsError
stuck.sample_count=1
stuck.sample[0].issue=published_dispatch_not_claimed
capacity.current.active_jobs=2
```

这类结果应立即停止升压。即使 `/health` 仍是 200，也不能把这一档视为通过；需要先处理 DB 连接压力和 worker/broker 领取残留。

### POST /jobs 返回 503

```bash
./scripts/jobs.sh pressure --since 10m --caller-id default --max-active-jobs <当前值> --locust-prefix .run/load/<run-prefix>
./scripts/jobs.sh capacity --since 10m --max-active-jobs <当前值>
./scripts/jobs.sh summary --since 10m
./scripts/jobs.sh latency --since 30m --group-by job_type
```

判断：

```text
pressure 命中 http_503_gate_hit，failures.csv 响应体含 active_jobs/limit，queued/running 能排空，组件健康
  -> 可以按 MAX_ACTIVE_JOBS 估算文档小步调大。

503 同时 queued 持续增长或 drain 不通过
  -> 先扩 worker 消费能力，不要只调大 MAX_ACTIVE_JOBS。

出现 500、TooManyConnectionsError、Pod restart、OOM
  -> 先查硬瓶颈，不要调大 MAX_ACTIVE_JOBS。
```

### 压测 HTTP 0 失败，但 Job 没排空

先确认 Locust 的 HTTP 结果，再用 `jobs.sh` 查 Job 终态：

```bash
sed -n '1,20p' .run/load/<run>_stats.csv
sed -n '1,50p' .run/load/<run>_failures.csv
./scripts/jobs.sh drain --since 20m --caller-id default --older-than 1m --json
./scripts/jobs.sh latency --since 20m --caller-id default --group-by job_type --json
```

如果 `failures.csv` 为空，但 `drain` 显示 `failed > 0` 或 `stuck.total > 0`，说明 HTTP 接单成功不等于 Job 执行成功。继续找样本：

```bash
./scripts/jobs.sh list --caller-id default --status failed --since 20m --limit 20
./scripts/jobs.sh list --caller-id default --status queued,running --since 20m --limit 20
./scripts/jobs.sh inspect <job_id> --events-limit 50
```

本地 `MAX_ACTIVE_JOBS=750` 的一次 submit 压测中，Locust `POST /jobs` 没有 HTTP 失败，但 `drain` 发现 1 个 failed Job 和 1 个长期 queued Job。`inspect` failed Job 后看到：

```text
failure_phase=execute
error.details.type=TooManyConnectionsError
error.details.message=sorry, too many clients already
```

这类结果应判定为执行侧或数据库连接容量压力，而不是 API 接单失败。`inspect` queued Job 后看到 `dispatch.published` 后没有 `attempt.claimed`，`stuck --older-than 1m --since 20m --caller-id default` 应报告 `published_dispatch_not_claimed`，下一步查 worker/broker 消费。

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
./scripts/jobs.sh diagnose <job_id>
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

diagnose 显示 published_dispatch_not_claimed
  -> 短时间内可能是刚发布后的锁竞争；如果持续存在或 stuck 也命中，查 worker/broker。

timeline 有 attempt.claimed 但没有终态
  -> Job 执行路径、worker 日志、外部依赖。
```

### callback 没送到

```bash
./scripts/jobs.sh diagnose <job_id>
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

`drain.current` 是当前 scope 的实时 active 计数，适合判断是否可以进入下一档压测。`drain.window` 是 `--since` 窗口内创建的 Job 统计，适合复盘本轮压测。

`capacity.current` 是全局实时 active 水位；`capacity.window` 才是窗口统计。不要把两者直接当成同一个时间范围。

`lifecycle_p95_seconds` 是 `finished_at - created_at`。它适合做容量上界估算，但不等于精确 active 门禁占用时长；workflow root 等待子任务时可能不占 active 门禁。

`MAX_ACTIVE_JOBS=0` 表示跳过 active 门禁。生产不建议用它做容量保护。

空结果不等于没有问题。窗口太短、过滤条件不对、`caller_id` 被本地开发开关改写，都可能导致查不到记录。

## 快速命令清单

```bash
./scripts/jobs.sh --help
./scripts/jobs.sh types

./scripts/jobs.sh pressure --since 10m --caller-id default --max-active-jobs <当前值> --locust-prefix .run/load/<run-prefix> --api-log logs/api.log
./scripts/jobs.sh doctor --since 10m
./scripts/jobs.sh drain --since 30m --caller-id default
./scripts/jobs.sh drain --since 30m --caller-id default --strict --json
./scripts/jobs.sh summary --since 10m
./scripts/jobs.sh capacity --since 10m --caller-id default --max-active-jobs <当前值>
./scripts/jobs.sh latency --since 30m --group-by job_type

./scripts/jobs.sh list --status queued,running --since 30m --limit 20
./scripts/jobs.sh list --status failed --since 24h --limit 20

./scripts/jobs.sh inspect <job_id> --events-limit 50
./scripts/jobs.sh timeline <job_id> --limit 100
./scripts/jobs.sh attempts <job_id>
./scripts/jobs.sh callbacks <job_id>

./scripts/jobs.sh stuck --older-than 10m --since 30m --caller-id default --limit 50
```
