# `scripts/jobs.sh` 使用与排障手册

本文帮助维护人员用 `scripts/jobs.sh` 快速判断 Job 系统现在发生了什么、下一步查哪里，以及什么时候才需要展开大 payload。

`jobs.sh` 是只读查询和排障入口。它不创建 Job，不取消 Job，不重试 Job，不重放 callback，也不修改数据库。

如果目标是执行一次压测、选择 `example-*` profile、模拟 `poster_title_image` 编排结构或观察 dashboard，先看 [`job-load-testing-runbook.md`](job-load-testing-runbook.md)。本文只负责 `jobs.sh` 的只读诊断命令和证据解释。

## 先建立心智模型

异步 Job 的排障不是从命令名开始，而是从“现在要回答什么问题、证据在哪一层”开始。

```text
外部请求
  -> root Job                 业务请求本身
  -> child Job                workflow 内部执行节点
  -> attempt                  worker 执行尝试
  -> dispatch_outbox          投递 run_attempt 到 broker
  -> callback_outbox          终态回调投递
  -> job_audit_events         状态流转证据
```

先按问题找入口：

```text
系统现在健康吗？
  首选 -> ./scripts/jobs.sh overview --since 1h
  辅助 -> ./scripts/jobs.sh doctor --since 1h
  明细 -> ./scripts/jobs.sh summary --since 1h

真正 active 积压是多少？
  首选 -> ./scripts/jobs.sh gate
  辅助 -> ./scripts/jobs.sh capacity --since 30m

系统是否正在恢复？
  首选 -> ./scripts/jobs.sh observe --interval 60 --samples 5
  辅助 -> ./scripts/jobs.sh drain --since 30m --strict
  明细 -> ./scripts/jobs.sh stuck --older-than 10m

调用方流量和处理吞吐怎样？
  首选 -> ./scripts/jobs.sh ingress --since 30m --bucket 1m
  辅助 -> ./scripts/jobs.sh latency --since 30m

能不能加并发或 pod？
  首选 -> ./scripts/jobs.sh capacity --worker-pods 4 --worker-concurrency 30 --api-pods 2 --db-max-connections 100
  辅助 -> ./scripts/jobs.sh runtime

Redis/Taskiq 和 worker 是否真的在消费？
  首选 -> ./scripts/jobs.sh broker
  首选 -> ./scripts/jobs.sh runtime

失败和 callback 是否集中异常？
  首选 -> ./scripts/jobs.sh failures --since 1h
  首选 -> ./scripts/jobs.sh callbacks-summary --since 1h

单个 Job 卡在哪？
  首选 -> ./scripts/jobs.sh trace <job_id>
  辅助 -> ./scripts/jobs.sh inspect <job_id>
  明细 -> ./scripts/jobs.sh timeline <job_id> --limit 50
  明细 -> ./scripts/jobs.sh attempts <job_id>
  明细 -> ./scripts/jobs.sh callbacks <job_id>
```

命令分级：

```text
一级入口
  overview / observe / broker / runtime / trace

二级诊断
  doctor / gate / capacity / ingress / latency / failures / callbacks-summary / stuck / drain / pressure

明细证据
  summary / list / inspect / diagnose / workflow / timeline / attempts / callbacks / payload
```

也可以把 `jobs.sh` 当成四层只读运维入口：

```text
系统态
  overview / doctor / gate / capacity / pressure / ingress

恢复态
  observe / drain / stuck

运输层和 Pod 运行时
  broker / runtime

单 Job 轨迹
  inspect / trace / diagnose / workflow / attempts / callbacks / timeline
```

`broker` 和 `runtime` 的作用域是当前执行环境。它们适合在 Pod 内运行，用于确认当前 Pod 看到的 Redis/Taskiq、进程、环境变量和 cgroup 资源；它们不会修改 Redis、DB 或 Job 状态。

## 三个最容易混淆的概念

### 时间窗口

`--since` 只看 `created_at` 落入最近窗口的 Job。

支持的格式只有正整数加单位：

```text
30s  30 秒
10m  10 分钟
24h  24 小时
7d   7 天
```

不支持：

```text
10min
1.5h
0m
2026-07-01T10:00:00Z
```

`--older-than` 不是统计窗口，它表示“这个状态持续超过多久才算风险候选”。

### Scope

```text
root
  外部业务 Job。
  list、summary、latency、capacity window 默认使用。

child
  workflow internal child Job。
  只想看内部执行节点时使用。

family
  先按 root 条件选业务请求，再包含这些 root 的 children。
  drain、pressure、stuck、workflow 风险排查常用。

all
  不加 lineage 条件。
  只用于明确的底层排查。
```

### 当前全局 active 占用

`global_gate` 是脚本内部使用的口径名。对使用者来说，可以先把它理解成：

```text
当前全局 active 占用
  = 现在还在排队的 Job
  + 现在正在被 worker 执行的 Job
```

它回答的是“系统现在还有多少 Job 正在占用处理名额”，不是“最近 10 分钟创建了多少 Job”。

```text
active_jobs
  = queued
  + running 且 active_attempt_id 非空

不受 --since 影响
不按 job_type 过滤
不按 caller_id 过滤
```

这也是 `gate` 和 `capacity` 的核心区别。

`gate` 输出里几个字段可以这样读：

```text
active_jobs
  现在还在排队或执行的 Job 数量。

max_active_jobs
  当前配置允许同时占用的上限，也就是 MAX_ACTIVE_JOBS。

active_ratio
  active_jobs / max_active_jobs。
  例如 0.8 表示已经用掉 80%。

headroom
  max_active_jobs - active_jobs。
  例如 999 表示距离上限还差 999 个 active Job。
```

## `gate` 和 `capacity` 怎么用

先记住一句话：

```text
gate     看当前还有多少 Job 正在占用处理名额。
capacity 看当前占用 + 最近窗口的容量估算。
```

| 问题 | 用哪个 | 原因 |
| --- | --- | --- |
| 现在全局还有多少 Job 在排队或执行？ | `gate` | 只查当前全局占用，短、快、没有窗口概念 |
| 为什么 overview 说窗口为空但 active_jobs=1？ | `gate` | 这个 Job 可能创建于窗口外，但现在仍未结束 |
| 当前是否接近 `MAX_ACTIVE_JOBS` 上限？ | `gate --max-active-jobs <n>` | 直接看 active_ratio 和 headroom，含义见上面的字段说明 |
| 最近 1 小时的吞吐和生命周期是否支撑当前上限？ | `capacity --since 1h` | 需要窗口 accepted_jobs、lifecycle p95 和估算需求 |
| 按某个 caller/job_type 估算本轮压测容量？ | `capacity --since 20m --caller-id <id>` | 过滤只作用于窗口估算，不作用于当前全局占用 |
| 入口提交速率是否突然变大？ | `ingress --since 30m --bucket 1m` | 看 created、started、terminal、failed 是否同向变化 |
| 是否可以加 worker 并发或 pod？ | `capacity --worker-pods <n> --worker-concurrency <n> --api-pods <n> --db-max-connections <n>` | 同时看处理槽位和 DB 连接预算 |

常用命令：

```bash
./scripts/jobs.sh gate
./scripts/jobs.sh gate --max-active-jobs 1000
./scripts/jobs.sh capacity --since 1h --max-active-jobs 1000
./scripts/jobs.sh capacity --since 20m --caller-id load-cli --max-active-jobs 1000
./scripts/jobs.sh capacity --worker-pods 4 --worker-concurrency 30 --api-pods 2 --db-max-connections 100
./scripts/jobs.sh ingress --since 30m --bucket 1m
```

读 `capacity` 时按这个图理解：

```text
capacity
  Current Global Active
    当前全局 active 占用
    不受 --since / --job-type / --caller-id 影响

  Window Capacity Estimate
    窗口估算值
    受 --since / --scope / --job-type / --caller-id 影响

  Estimated
    active_ratio/headroom 来自 Current
    active_jobs_needed_upper_bound 来自 Window

  DB Connection Budget
    来自 --api-pods / --worker-pods / --worker-concurrency / --db-max-connections
    未显式传 --worker-concurrency / --db-pool-size / --db-max-overflow 时，会读取当前环境或 .env，并在 input_sources 中标出来源
```

`capacity` 的 DB 连接预算是估算，不直接查询 PostgreSQL 当前连接数。公式是：

```text
api_pods * (DB_POOL_SIZE + DB_MAX_OVERFLOW)
+ worker_pods * WORKER_CONCURRENCY
<= db_max_connections * db_usable_ratio
```

这个结果用于回答“能不能继续加 `WORKER_CONCURRENCY` 或 pod”。如果 `risk=critical`，最终建议会优先阻止继续升并发；如果 `risk=unknown`，说明缺少 pod 数、并发、pool 或 PostgreSQL `max_connections` 这类输入。读预算时同时看 `input_sources`，确认关键值来自 `cli`、`environment` 还是 `.env`。

`ingress` 不是单纯按 `created_at` 查窗口。它按事件发生时间分别统计：

```text
created   created_at 落入时间桶
started   started_at 落入时间桶
terminal  finished_at 落入时间桶
failed    finished_at 落入时间桶且 status=failed
```

所以它适合看“入口是否还在变大、worker 是否跟得上、终态是否开始恢复、失败是否同步升高”。

如果只是想知道“现在还有没有 Job 在排队或执行”，不要用 `capacity` 绕一圈，直接用：

```bash
./scripts/jobs.sh gate
```

## 常用排障路径

### 1. 不知道系统现在怎样

先跑默认 overview：

```bash
./scripts/jobs.sh
```

它等同于：

```bash
./scripts/jobs.sh overview --since 10m
```

一屏里会看到三类信息：

```text
最近窗口 Root Job 汇总
  只看 --since 窗口内创建的 root Job。

当前全局 active 占用
  现在仍在排队或执行的 Job 数量，不受 --since 影响。

最近窗口 Family 风险样本
  以 root 窗口为入口，看 root + children 的 stuck 风险。
```

如果窗口为空但 `gate` 显示仍有 active_jobs，优先查：

```bash
./scripts/jobs.sh gate
./scripts/jobs.sh list --status queued,running --scope family --limit 20
./scripts/jobs.sh overview --since 1h
```

### 2. 找具体异常 Job

找 active 样本：

```bash
./scripts/jobs.sh list --status queued,running --scope family --limit 20
```

找失败样本：

```bash
./scripts/jobs.sh list --status failed --since 1h --limit 20
```

按调用方或业务请求找：

```bash
./scripts/jobs.sh list --caller-id <caller_id> --since 1h --limit 20
./scripts/jobs.sh list --client-request-id <client_request_id>
```

`list` 默认是 root。怀疑 workflow child 卡住时，用 `--scope family`。

### 3. 已经拿到 `job_id`

轻量看状态：

```bash
./scripts/jobs.sh job <job_id>
```

聚合看单 Job 证据：

```bash
./scripts/jobs.sh inspect <job_id>
```

怀疑 workflow root/child 问题：

```bash
./scripts/jobs.sh workflow <job_id>
```

只看诊断结论：

```bash
./scripts/jobs.sh diagnose <job_id>
```

只看某类证据：

```bash
./scripts/jobs.sh timeline <job_id> --limit 50
./scripts/jobs.sh attempts <job_id>
./scripts/jobs.sh callbacks <job_id>
```

### 4. 压测后判断能不能进入下一档

压测主流程以 [`job-load-testing-runbook.md`](job-load-testing-runbook.md) 为准。这里仅保留 `jobs.sh pressure` / `drain` 的诊断含义；通过 `load.sh` 运行的压测默认 caller 是 `load-cli`。

判断规则：

```text
pressure critical
  不进入下一档，先处理失败、DB、worker、broker 或 callback 问题。

drain drained
  当前 scope 没有 active、running_inactive、failed 或 stuck 证据。

drain not_drained
  不进入下一档，按 next checks 查样本。
```

### 5. 怀疑 stuck

```bash
./scripts/jobs.sh stuck --since 1h --older-than 10m --limit 50
```

压测刚结束想快速看本轮残留：

```bash
./scripts/jobs.sh stuck --since 20m --older-than 1m --caller-id load-cli --limit 20
```

常见 issue：

| issue | 含义 | 下一步 |
| --- | --- | --- |
| `dispatch_due_not_published` | dispatch 到期但未发布 | 查 broker/outbox 发布路径 |
| `published_dispatch_not_claimed` | dispatch 已发布但 worker 未领取 | 查 worker、broker 消费、timeline |
| `running_attempt_lease_expired` | running attempt lease 过期 | 查 worker 心跳和 recovery |
| `callback_lease_expired` | callback lease 过期 | 查 callback worker |
| `terminal_callback_not_settled` | Job 已终态但 callback 未沉淀 | 查 callback outbox |

## Payload 输出边界

为了避免 Job 入参或结果过大导致排障输出不可用，`jobs.sh` 采用这个边界：

```text
job / show / inspect / diagnose / workflow
  只输出状态、attempt、callback、timeline、workflow child 状态和诊断结论。
  不输出完整 job_params、runtime_ref、result、canonical_result。

payload
  默认输出 payload 结构摘要。

payload --full
  才输出完整入参、runtime、结果和错误 payload。
```

默认摘要：

```bash
./scripts/jobs.sh payload <job_id>
./scripts/jobs.sh payload <job_id> --json
```

完整内容：

```bash
./scripts/jobs.sh payload <job_id> --full
./scripts/jobs.sh payload <job_id> --include-children --full
./scripts/jobs.sh payload <job_id> --json --full
```

使用原则：

```text
先 inspect / diagnose 定位问题在哪。
只有确认需要看原始入参、runtime、结果或错误 payload 时，才用 payload --full。
```

## 每个子命令怎么理解

| 命令 | 先问的问题 | 输出重点 | 注意 |
| --- | --- | --- | --- |
| 无参 / `overview` | 最近窗口整体怎样？ | root 窗口汇总、当前全局 active 占用、family 风险样本、next checks | 默认 `--since 10m` |
| `gate` | 现在全局还有多少 Job 在排队或执行？ | active_jobs、queued、running_active、active_ratio、headroom | 无窗口、无业务过滤 |
| `summary` | 某个窗口内计数怎样？ | jobs、attempts、dispatch、callbacks、by_job_type | 窗口统计，不是全局实时 |
| `doctor` | 窗口汇总说明什么？ | summary 诊断和下一步命令 | 适合不确定下一步时使用 |
| `observe` | 系统是否正在恢复？ | 多次采样 queued、active、failed、callback due、stuck 和 verdict | 默认会等待采样间隔 |
| `broker` | Redis/Taskiq 运输层是否有积压或 key 类型错配？ | Redis ping、key type、length、pending、consumer groups、verdict | 只读；不会清理队列 key |
| `runtime` | 当前 Pod 内 worker/API runtime 证据是什么？ | 环境变量、Taskiq/recovery 进程、cgroup CPU/内存 | 只代表当前 Pod |
| `capacity` | 当前占用、窗口容量和 DB 连接预算怎样？ | current、window estimate、estimated、db_connection_budget | current 是全局，window 才受过滤 |
| `ingress` | 调用方流量和处理吞吐趋势怎样？ | 每个时间桶的 created、started、terminal、failed | 默认 root scope；按事件时间聚合 |
| `latency` | 慢在哪里？ | queue/run/lifecycle p95、success_rate | 先按 `job_type` 分组看 |
| `failures` | 失败集中在哪类错误？ | error_code、error_kind、failure_phase 聚合 | 默认 `family` scope |
| `callbacks-summary` | callback 是否闭环？ | callback status、due、oldest age、HTTP/error 样本 | 宏观 callback outbox |
| `list` | 哪些 Job 值得看？ | Job 样本列表 | 默认 root；child 问题用 family |
| `job` | 单个 Job 当前状态是什么？ | 轻量状态 | 不展开 payload |
| `show` | 单个 Job 权威状态是什么？ | 与 `job` 类似的兼容入口 | 优先使用 `job` |
| `inspect` | 单个 Job 证据集中看是什么？ | job summary、diagnosis、attempts、callbacks、timeline | 不输出完整 payload |
| `trace` | 单个 Job 各阶段耗时和当前卡点是什么？ | accepted、dispatch_wait、claim_wait、running、callback 阶段 | 看阶段摘要，不替代 timeline 原始事件 |
| `diagnose` | 单个 Job 卡在哪里？ | findings、signal、next checks | 判断 attempt/dispatch/callback/claim |
| `workflow` | root 和 children 关系怎样？ | root、children、root attempts/callbacks/timeline | 可传 root 或 child job_id |
| `timeline` | 状态怎么流转的？ | audit events | `--limit` 控制事件数 |
| `attempts` | worker 执行尝试怎样？ | attempt status、lease、worker、failure phase | 查执行路径 |
| `callbacks` | callback 投递怎样？ | callback status、HTTP、last_error | 查回调路径 |
| `payload` | 需要看 payload 结构或原文吗？ | 默认摘要，`--full` 完整 | 唯一完整 payload 出口 |
| `stuck` | 哪些记录疑似卡住？ | stuck issue 样本 | 用 `--older-than` 控制阈值 |
| `drain` | 当前是否排空？ | drained/not_drained、current/window/stuck | 压测前后使用 |
| `pressure` | 压测瓶颈方向是什么？ | HTTP、capacity、latency、failed、stuck、API log | 压测后首选 |
| `types` | 当前支持哪些 job_type？ | 注册类型列表 | 接入新业务时确认 |

## 常见信号怎么读

| signal / 状态 | 意味着什么 | 先做什么 |
| --- | --- | --- |
| `empty_window` | 当前 `--since` 窗口内没有 root Job | 扩大窗口或查 `gate` |
| `window_empty_but_global_active` | 窗口为空，但仍有 Job 在排队或执行 | 查 `gate` 和无窗口 active list |
| `published_dispatch_not_claimed` | dispatch 已发布但 worker 未领取 | 查 worker/broker 和 timeline |
| `dispatch_dead_letter` | run_attempt 发布路径失败 | 查 dispatch error，不要盲目重试 |
| `running_attempt_lease_expired` | worker 心跳或 recovery 有风险 | 查 worker 日志和 recovery |
| `callback_dead_letter` | 回调没有送达 | 查 callback error 和目标服务 |
| `job_waiting_children` | workflow root 在等 child | 用 `workflow <job_id>` |
| `workflow_child_failed` | workflow child 失败 | 找 child 后 inspect/diagnose |
| `http_503_gate_hit` | active_jobs 达到上限，系统开始用 503 拒绝新请求 | 确认能排空后再讨论容量 |
| `http_5xx` | 服务或依赖异常 | 停止升压，查日志和 failed Job |
| `db_connection_pressure` | 数据库连接压力 | 先治理连接池/并发，不要先调大 MAX_ACTIVE_JOBS |

## 判断是否正在恢复

单次 `overview` 是快照，不能证明系统正在变好。需要连续观察：

```bash
./scripts/jobs.sh observe --interval 60 --samples 5
```

重点看：

```text
queued 是否下降
active_jobs 是否下降
failed 是否继续增加
callback_due 是否下降
stuck 是否减少
```

典型判断：

```text
recovering
  queued 或 active_jobs 持续下降，failed/stuck/callback_due 没有继续增加。

backlog_expanding
  queued 或 active_jobs 持续上升，说明入口流量或执行耗时超过当前处理能力。

degrading
  failed、stuck 或 callback_due 增加，先查 failures、stuck、callbacks-summary。
```

常用组合：

```bash
./scripts/jobs.sh observe --interval 60 --samples 5
./scripts/jobs.sh broker
./scripts/jobs.sh runtime
./scripts/jobs.sh failures --since 1h
./scripts/jobs.sh callbacks-summary --since 1h
```

## 典型流程

### overview 看到窗口为空，但 active_jobs 不为 0

```bash
./scripts/jobs.sh gate
./scripts/jobs.sh list --status queued,running --scope family --limit 20
./scripts/jobs.sh overview --since 1h
```

原因通常是：这个 Job 创建时间早于当前 `--since` 窗口，但现在仍在排队或执行。

### 有 failed Job

```bash
./scripts/jobs.sh list --status failed --since 1h --limit 20
./scripts/jobs.sh inspect <job_id>
./scripts/jobs.sh diagnose <job_id>
./scripts/jobs.sh payload <job_id>
```

只有需要看原始错误或业务结果时：

```bash
./scripts/jobs.sh payload <job_id> --full
```

### workflow root 一直 running

```bash
./scripts/jobs.sh workflow <job_id>
./scripts/jobs.sh list --scope family --status queued,running --limit 20
./scripts/jobs.sh diagnose <job_id> --include-children
```

如果发现 failed child，再对 child 执行：

```bash
./scripts/jobs.sh inspect <child_job_id>
./scripts/jobs.sh attempts <child_job_id>
```

### 压测出现 HTTP 500

压测 HTTP 500 的完整处置路径见 [`job-load-testing-runbook.md`](job-load-testing-runbook.md)。本文只补充 `jobs.sh` 侧含义：先用 `pressure` 判断 HTTP、capacity、latency、failed、stuck 和 API log 方向，再用 `list` / `inspect` 定位失败样本。如果看到 DB 连接相关信号，先治理连接池、PostgreSQL 连接上限、API/worker 并发，不要继续升压。

### POST /jobs 返回 503

压测 503 的完整处置路径见 [`job-load-testing-runbook.md`](job-load-testing-runbook.md)。本文只补充 `jobs.sh` 侧含义：`gate` 看当前全局 active 水位，`pressure` 判断是否主要命中 active gate，`drain` 判断后台是否可排空。如果主要是 `http_503_gate_hit`，且后台能排空、健康检查正常，说明 `MAX_ACTIVE_JOBS` 上限保护生效。是否调大这个上限，要结合 `capacity`、`latency`、失败率和环境资源判断。

## JSON 使用边界

`--json` 适合脚本、AI 和运维平台解析，但不是所有 JSON 都会返回完整原始字段。

```text
非 payload 命令的 --json
  返回状态、诊断和证据摘要。
  不返回完整 job_params/runtime/result/canonical_result。

payload --json
  返回 payload 结构摘要。

payload --json --full
  返回完整 payload。
```

不要用人读表格做脚本解析；自动化只读 `--json`。

## 使用前检查

本地：

```bash
./scripts/dev.sh status
./scripts/jobs.sh -h
./scripts/jobs.sh
```

Pod 内：

```bash
./scripts/jobs.sh -h
./scripts/jobs.sh gate
./scripts/jobs.sh overview --since 1h
```

环境变量：

```text
DATABASE_URL  必填，可来自运行环境或根目录 .env。
DB_SSL        可选；false/0/no/off 时为 psycopg2 URL 追加 sslmode=disable。
```
