# Job 接口压测指导

本文给出本服务 `POST /jobs` 和 `GET /jobs/{job_id}` 的项目级压测入口，从工具选型、场景设计、执行命令、指标门禁到结果评估形成一条完整流程。稳定入口是 `./scripts/load.sh`；`scripts/load/locustfile.py` 只是内部 Locust runner，不作为用户直接调用合同。

本文只覆盖本模板服务的 Job 发布、查询和异步完成链路压测；不覆盖生产压测平台建设、真实模型供应商压测、跨地域压测或容量采购决策。

## 心智模型

Job 压测不是为了得到一个单独的 QPS 数字，而是回答三个问题：

| 问题 | 对应 case | 典型结论 |
|---|---|---|
| 服务能不能接住创建请求 | `job-submit` | API、DB 写入、幂等键和 Taskiq publish 是否成为瓶颈 |
| 查询接口能不能承受轮询 | `job-query` | DB 读、索引和响应序列化是否成为瓶颈 |
| Job 能不能按预期完成 | `job-flow` | worker、队列、Job 执行耗时和终态成功率是否满足目标 |

Job 服务和普通同步 HTTP 接口不同。`POST /jobs` 返回快，只代表“接单快”；真正的业务结果要等 worker 执行完成后，才能通过 `GET /jobs/{job_id}` 查询到终态。因此压测要同时看 HTTP 延迟、队列积压和 Job 端到端耗时。

```text
Locust 用户并发
  |
  |  POST /jobs
  |  观察：RPS、错误率、p95/p99
  v
FastAPI API
  |
  |  写入 PostgreSQL
  |  发布 Taskiq 消息
  v
Redis / Taskiq 队列
  |
  |  观察：queued 是否持续增长
  |  worker 消费并执行 job_type
  v
Worker
  |
  |  观察：CPU、内存、外部 IO、执行耗时、失败率
  |  更新 Job 状态和结果
  v
PostgreSQL
  ^
  |
  |  GET /jobs/{job_id}
  |  观察：查询 RPS、错误率、p95/p99
  |
Locust 轮询
```

Locust 只是负载发生器和观察窗口，不是系统瓶颈判断本身。浏览器里的 RPS、失败率和响应时间只能说明 Locust 观察到的 HTTP 结果；判断瓶颈还要结合服务侧的 API、worker、PostgreSQL、Redis 和 Job 状态证据。

### 指标怎么读

本文统一使用 Locust 页面和 CSV 中的 `RPS`。如果团队口头说 `QPS`，在本压测语境下通常也是指每秒 HTTP 请求数；为了避免歧义，文档和报告里建议写成 `RPS`，并在需要时注明“即每秒 HTTP 请求数”。

| 指标 | 含义 | 读法 |
|---|---|---|
| RPS | 每秒完成的 HTTP 请求数 | 看吞吐是否随并发增加而增加；不再增加通常说明已有瓶颈 |
| QPS | 口头常用的每秒请求或查询数 | 本文不单独使用该名称；如果引用 QPS，应说明它等同于本轮 HTTP RPS，还是数据库查询 QPS |
| 错误率 | 请求失败比例 | 压测结果首先看错误率；错误率高时，p95/p99 的参考价值会下降 |
| p95 / p99 | 95% / 99% 请求的响应时间上界 | 看尾部延迟；平均值容易掩盖少量慢请求 |
| `JOB flow terminal latency` | 从创建 Job 到查询到终态的耗时 | 看端到端完成能力；它比 `POST /jobs` 延迟更接近用户感知 |
| queued/running | Job 队列和执行中数量 | 持续增长说明 worker、broker 或 Job 执行路径跟不上 |
| stuck | 长时间未完成的 Job | 压测后仍存在 stuck，需要优先排查 worker timeout、异常和外部依赖 |

还要区分 HTTP 吞吐和 Job 完成吞吐：

| 名称 | 看什么 | 不代表什么 |
|---|---|---|
| `POST /jobs` RPS | API 每秒能接多少创建请求 | 不代表每秒能完成多少 Job |
| `GET /jobs/{job_id}` RPS | 查询接口每秒能承受多少轮询请求 | 不代表 worker 处理能力 |
| Job 完成吞吐 | 单位时间内进入终态的 Job 数 | 不等同于 Locust HTTP RPS |

一个常见误判是：`POST /jobs` RPS 高、p95 低，就认为系统能承受该流量。对异步 Job 服务来说，正确判断应同时满足：`POST` 和 `GET` 错误率低、HTTP p95/p99 可接受、`JOB flow terminal latency` 可接受、压测停止后 queued/running 能下降。

### 参数怎么设

先明确本轮要回答的问题，再设参数：

| 目标 | 先用 case | 参数重点 |
|---|---|---|
| 测接单能力 | `job-submit` | 逐步增加 `--users` 和 `--spawn-rate`，观察 `POST /jobs` 错误率、p95/p99 和 queued 增长 |
| 测查询能力 | `job-query` | 准备足够 Job ID，逐步增加 `--users` 和 `--spawn-rate`，观察 `GET /jobs/{job_id}` p95/p99 |
| 测完整生命周期 | `job-flow` | 设定符合业务预期的 `--flow-timeout-seconds` 和 `--poll-interval-seconds`，观察终态成功率和端到端耗时 |

Locust 常用参数可以按以下方式理解：

| 参数 | 控制什么 | 设定思路 |
|---|---|---|
| `--users` | 同时运行的 Locust 用户数 | 表示并发压力，不等同真实用户数；在 `job-flow` 中，Job 执行越久，同样并发下每秒能创建的新 Job 越少 |
| `--spawn-rate` | 每秒启动多少 Locust 用户 | 用来控制爬坡速度；太快会把启动瞬间和稳定压测混在一起 |
| `--time` | 压测持续多久 | 要覆盖启动、稳定运行和观察积压变化；太短只能看冒烟结果 |
| `--wait-min-seconds` / `--wait-max-seconds` | 单个 Locust 用户两次任务之间的等待 | 等待越短，同样并发下压力越大 |
| `--poll-interval-seconds` | `job-flow` 轮询间隔 | 越短查询压力越大，也越接近实时观察；越长会拉高终态观测延迟 |
| `--flow-timeout-seconds` | `job-flow` 单个 Job 等待终态的最长时间 | 应大于预期 Job 执行时间；太短会把慢 Job 统计为失败 |

调参顺序建议是：先小并发确认 case 正确，再逐步提高 `-u`，再调整 `-r` 和持续时间。不要一开始就把 `-u` 设很大，否则很难判断是 API、worker、数据库、Redis 还是负载机先到瓶颈。

### Job 执行时间影响什么

Job 执行时间会直接影响 worker 吞吐和队列积压。一个 Job 如果主要是 CPU 计算，会消耗 worker CPU；如果主要是文件、对象存储、数据库或外部模型调用，会消耗 IO、连接池、网络等待和外部服务额度。执行时间越长，同样 worker 并发下单位时间能完成的 Job 越少，queued 越容易增长。

```text
Job 完成能力 ≈ worker 并发 / 单个 Job 平均执行时间
```

这是估算，不是容量承诺。真实吞吐还会受 DB 写入、Redis/Taskiq、回调、结果大小、外部服务限流和错误重试影响。因此第一轮默认使用 `example_sleep`，先排除真实模型供应商延迟、限流和费用，再评估模板服务自身链路。

### 如何形成结论

| 层次 | 本文对应内容 | 主要回答的问题 |
|---|---|---|
| 负载入口 | `./scripts/load.sh` case、profile、`--users`、`--spawn-rate`、`--time` | 要压哪条链路、用哪个 Job、压多少、压多久 |
| 观察方式 | `run`、`ui`、`report`、`manifest`、CSV、HTML | 在终端、浏览器还是文件里看结果 |
| 服务证据 | `scripts/jobs.sh`、API/worker/DB/Redis 指标 | 慢在接口、队列、worker 还是存储 |
| 评估结论 | 指标门禁和瓶颈判断 | 这次结果是否有效，下一步该调哪里 |

如果 HTTP 指标正常但 queued 持续增长，优先看 worker 和 Job 执行路径。如果 `GET /jobs/{job_id}` p95 升高，优先看 DB 读、索引和序列化。如果 `JOB flow terminal latency` 增长但 HTTP p95 仍低，说明用户等待主要花在异步队列和 worker 执行上，而不是 API 接口本身。

## 快速开始

第一次使用时，先跑一轮小流量 `job-flow`。它会创建 Job、轮询状态并统计端到端完成耗时，最适合确认压测链路是否可用。

```bash
./scripts/dev.sh bootstrap
./scripts/dev.sh start
./scripts/dev.sh status
mkdir -p .run/load
```

查看当前注册的压测 case 和内置 profile：

```bash
./scripts/load.sh cases
./scripts/load.sh profiles
```

跑一轮 2 分钟的小流量端到端压测：

```bash
./scripts/load.sh run job-flow \
  --api-url http://127.0.0.1:8100 \
  --users 10 \
  --spawn-rate 2 \
  --time 2m \
  --run-id quick-flow
```

这轮命令的含义：

| 参数 | 含义 |
|---|---|
| `run job-flow` | 创建 Job 并轮询到终态 |
| `--api-url http://127.0.0.1:8100` | 被压测的本地 API |
| `--users 10` | 最高 10 个 Locust 用户并发 |
| `--spawn-rate 2` | 每秒启动 2 个 Locust 用户 |
| `--time 2m` | 持续 2 分钟 |
| `--run-id quick-flow` | 结果写入 `.run/load/quick-flow/` |

跑完后先看三件事：

| 看什么 | 期望 |
|---|---|
| 终端或 HTML 报告中的失败数 | 没有失败，或失败原因明确 |
| `POST /jobs`、`GET /jobs/{job_id}` p95/p99 | 没有明显长尾 |
| `JOB flow terminal latency` | 能反映 Job 从创建到终态的端到端耗时 |

再看服务侧是否有积压或卡住的 Job：

```bash
./scripts/jobs.sh list --status running --since 10m --limit 20
./scripts/jobs.sh stuck --older-than 5m
```

需要边跑边看页面时，使用 `ui` 子命令，并打开 `http://127.0.0.1:8089`：

```bash
./scripts/load.sh ui job-flow \
  --api-url http://127.0.0.1:8100 \
  --users 10 \
  --spawn-rate 2 \
  --time 2m \
  --run-id quick-flow-ui
```

压测结束后停止本地服务：

```bash
./scripts/dev.sh stop
```

## 选型结论

本项目默认使用 Locust。原因是 Job 接口压测不是单 URL benchmark，而是有状态业务流：

```text
POST /jobs 创建 Job
-> GET /jobs/{job_id} 轮询状态
-> 按 job_status 判断终态
-> 统计 HTTP 延迟和 Job 端到端耗时
```

Locust 的内部 runner 是 Python，容易表达有状态业务流；`./scripts/load.sh` 作为 Typer CLI 入口负责 case 注册、profile 套用、安全确认、结果归档和压后诊断联动。第一版不引入 JMeter 测试计划、Grafana dashboard 或独立压测结果数据库；Locust 自带的执行引擎、CSV 和 web UI 已经能覆盖本服务当前压测目标。

只有在以下情况才升级方案：

- 公司已有 k6/Grafana/Kubernetes 压测平台，且压测结果必须进入统一平台。
- 公司已有 JMeter 中台，且需要接入现有审批、调度和报表流程。
- 需要多机、跨地域或百万级流量发生器。
- 压测 case 已经稳定到需要多机调度、统一报表存储或自动阈值门禁。

## 压测对象

默认接口前缀是 `/api/v1/ai-jobs`，由 `SERVICE_API_PREFIX` 配置。

核心接口：

```text
POST /api/v1/ai-jobs/jobs
GET  /api/v1/ai-jobs/jobs/{job_id}
```

第一版默认使用内置 `example_sleep`。它不调用真实模型，适合压测 API、数据库、Taskiq 发布、worker 消费和 Job 查询路径。需要压 workflow root / internal child / root finalize 链路时，使用内置 `example_workflow`。这些压测类型在 registry 中标记为 `visibility="demo"`，查看完整目录时使用 `./scripts/jobs.sh types --all`。不要一开始压真实模型 `job_type`，否则模型供应商延迟、限流和费用会掩盖服务自身瓶颈。

## Case 与 Profile

`case` 决定压哪条链路，`profile` 决定用哪个 `job_type` 和默认 `job_params`。这两层分开后，新增业务 Job 时通常只需要新增 profile，不需要修改 Locust runner。

`./scripts/load.sh cases` 维护当前压测 case 注册表：

| case | 作用 | 适用问题 |
|---|---|---|
| `job-submit` | 只创建 Job | API 和 DB 写入、幂等键、Taskiq publish 能承受多少接单流量 |
| `job-query` | 只查询已有 Job | 查询接口、DB 读、索引和响应序列化能承受多少轮询流量 |
| `job-flow` | 创建 Job 并轮询到终态 | 端到端 Job 生命周期、worker 消费能力、队列积压和终态成功率 |
| `workflow-flow` | 创建 workflow demo Job 并轮询到终态 | root orchestration、child fan-out 和 root finalize 是否闭环 |
| `api-health` | 压 `/health` | 基础 HTTP health 路径是否稳定 |

真实业务评估应按顺序执行：先 `job-submit`，再 `job-query`，最后 `job-flow`。只看 `POST /jobs` QPS 不代表系统能完成 Job。

查看内置 profile：

```bash
./scripts/load.sh profiles
```

生成业务 Job profile 模板：

```bash
./scripts/load.sh init poster-title-image \
  --job-type poster_title_image
```

生成后编辑 `.run/load/profiles/poster-title-image.json`，填入 `job_params` 和默认压测参数。使用时可以省略 case，让 profile 的 `case` 字段决定默认链路：

```bash
./scripts/load.sh run --profile .run/load/profiles/poster-title-image.json --allow-real-job
```

profile 只保存压测对象和默认参数，不保存真实业务 Job 的执行确认。非 `example_*` 的 `job_type` 每次运行都必须显式传 `--allow-real-job`。

如果只想临时覆盖，也可以继续用 `--job-type` 和 `--job-params-json-file`，但长期复用的业务压测建议沉淀为 profile。

## 准备环境

安装依赖：

```bash
./scripts/dev.sh bootstrap
```

验证压测入口可用：

```bash
./scripts/load.sh -h
./scripts/load.sh cases
./scripts/load.sh profiles
```

启动本地依赖、API 和 worker：

```bash
./scripts/dev.sh start
./scripts/dev.sh status
```

`load.sh` 默认读取仓库根目录 `.env`，运行时环境变量优先。默认 API URL 由 `API_URL` 或 `API_HOST` / `API_PORT` 推导；也可以显式传：

```bash
./scripts/load.sh run job-flow --api-url http://127.0.0.1:8100
```

如果 `.env` 中没有关闭认证，`load.sh` 会把 `SERVICE_API_KEY` 通过进程环境传给 Locust，并发送 `Authorization: Bearer <SERVICE_API_KEY>`。token 不会写入 `manifest.json`。

不建议使用 `--service-api-key` 传密钥；它会出现在 shell history、`ps` 和 CI 命令日志中。共享机器或 CI 优先用环境变量或 env 文件注入。

远端 API 必须显式确认：

```bash
./scripts/load.sh run job-flow \
  --api-url http://test.example.com \
  --allow-remote-api
```

## 执行压测

压测输出固定写到 `.run/load/<run_id>/`，该目录已被 git 忽略：

```bash
mkdir -p .run/load
```

`load.sh` 有三种常用执行模式：

| 模式 | 是否启动 web UI | 是否自动开始 | 适用场景 |
|---|---|---|---|
| `run` | 否 | 是 | 脚本、CI、后台压测，只看终端、CSV 或 HTML 报告 |
| `ui` | 是 | 是 | 需要自动开跑，同时在浏览器实时观察 RPS、失败率和响应时间 |
| `smoke` | 否 | 是 | 小流量确认压测链路可用 |

每轮执行都会生成：

```text
.run/load/<run_id>/manifest.json
.run/load/<run_id>/locust_stats.csv
.run/load/<run_id>/locust_failures.csv
.run/load/<run_id>/locust_exceptions.csv
.run/load/<run_id>/report.html
```

### 发布接口

```bash
./scripts/load.sh run job-submit \
  --api-url http://127.0.0.1:8100 \
  --users 100 \
  --spawn-rate 10 \
  --time 10m \
  --run-id job-submit
```

需要实时查看时：

```bash
./scripts/load.sh ui job-submit \
  --api-url http://127.0.0.1:8100 \
  --users 100 \
  --spawn-rate 10 \
  --time 10m \
  --run-id job-submit-ui
```

浏览器打开：

```text
http://127.0.0.1:8089
```

### 查询接口

查询压测需要先准备一批已有 Job ID。`GET /jobs/{job_id}` 会按 caller 隔离数据，因此 Job ID 必须来自同一个 `X-AI-Service-Caller-ID`。默认 `load.sh` caller 是 `load-cli`；如果 Job ID 来自其他 caller，查询压测也要显式传 `--caller-id`。

查看默认 load caller 创建的 Job：

```bash
./scripts/jobs.sh list --caller-id load-cli --since 10m --limit 20 --json
```

```bash
./scripts/load.sh run job-query \
  --api-url http://127.0.0.1:8100 \
  --query-job-ids-file .run/load/job-ids.txt \
  --users 100 \
  --spawn-rate 10 \
  --time 10m \
  --run-id job-query
```

少量 Job ID 也可以直接用逗号分隔：

```bash
./scripts/load.sh run job-query \
  --api-url http://127.0.0.1:8100 \
  --caller-id verify-workflow-smoke \
  --query-job-ids 00000000-0000-0000-0000-000000000000,11111111-1111-1111-1111-111111111111 \
  --users 20 \
  --spawn-rate 5 \
  --time 2m
```

### 完整 Job 流程

```bash
./scripts/load.sh run job-flow \
  --api-url http://127.0.0.1:8100 \
  --users 50 \
  --spawn-rate 5 \
  --time 10m \
  --poll-interval-seconds 0.5 \
  --flow-timeout-seconds 30 \
  --run-id job-flow
```

`job-flow` case 会额外上报 Locust 自定义指标：

```text
JOB flow terminal latency
```

该指标表示从创建 Job 到查询到终态的端到端耗时。终态不是 `succeeded` 时记为失败。

## 可配置参数

入口参数：

| 参数 | 默认值 | 说明 |
|---|---:|---|
| `--profile` | 无 | 内置 profile key 或 JSON profile 文件 |
| `--users` | case/profile 默认值 | Locust 用户并发 |
| `--spawn-rate` | case/profile 默认值 | 每秒启动用户数 |
| `--time` | case/profile 默认值 | 压测持续时间 |
| `--job-type` | case/profile 默认值 | 压测使用的 `job_type` |
| `--job-params-json-file` | 无 | 非内置动态压测 job 时使用的 `job_params` JSON 文件 |
| `--job-params-json` | 无 | 高风险 inline JSON，会出现在 shell history/ps；优先使用 `--job-params-json-file` |
| `--echo-sleep-seconds` | `15` | `example_sleep.sleep_seconds` |
| `--echo-repeat` | `1` | `example_sleep.repeat` |
| `--workflow-mode` | `group` | `example_workflow.mode` |
| `--workflow-sleep-seconds` | `15` | `example_workflow.sleep_seconds` |
| `--poll-interval-seconds` | case/profile 默认值 | flow 轮询间隔 |
| `--flow-timeout-seconds` | case/profile 默认值 | flow 单个 Job 等待终态超时 |
| `--query-job-ids-file` | 无 | `job-query` case 用 Job ID 文件 |
| `--query-job-ids` | 无 | 逗号分隔 UUID job_id；大量输入优先使用 `--query-job-ids-file` |
| `--caller-id` | `load-cli` | `X-AI-Service-Caller-ID` |

Workflow root / child 链路压测示例：

```bash
./scripts/load.sh run workflow-flow \
  --api-url http://127.0.0.1:8100 \
  --workflow-mode group \
  --workflow-sleep-seconds 15 \
  --flow-timeout-seconds 90 \
  --users 10 \
  --spawn-rate 2 \
  --time 5m
```

其它业务 `job_type` 的压测示例：

```bash
./scripts/load.sh init your-profile --job-type your_job_type
./scripts/load.sh run --profile .run/load/profiles/your-profile.json \
  --api-url http://127.0.0.1:8100 \
  --allow-real-job \
  --users 10 \
  --spawn-rate 2 \
  --time 5m
```

如果需要模拟失败率、大结果或可变 fan-out，不要把这些逻辑塞进 Locust，也不要把 `example_workflow` 扩展成通用压测 DSL。应按 [`../api/extension-guide.md`](../api/extension-guide.md) 新增明确的压测专用 `job_type`，例如暴露 `sleep_seconds`、`result_size_bytes` 和 `should_fail`。

## 指标门禁

最小指标集：

| 指标 | 建议门禁 | 说明 |
|---|---:|---|
| `POST /jobs` 错误率 | `< 0.1%` | 创建失败通常代表认证、校验、DB 写入或 broker publish 问题 |
| `POST /jobs` p95 | `< 200ms` | 只代表接单延迟，不代表 Job 完成 |
| `GET /jobs/{job_id}` 错误率 | `< 0.1%` | 查询失败通常代表 DB 读、路径参数或 envelope 问题 |
| `GET /jobs/{job_id}` p95 | `< 100ms` | 轮询接口应保持轻量 |
| `JOB flow terminal latency` p95 | 按业务目标设定 | 表示端到端 Job 完成耗时 |
| Job 终态成功率 | `> 99.9%` | 测试 job_type 下失败应视为系统问题 |
| queued/running 积压 | 压测停止后应下降 | 持续增长说明 worker 或 broker 消费不足 |

这些数值是模板本地压测的起点，不是生产容量承诺。正式业务接入后，应按业务 SLO、机器规格、worker 并发和模型耗时重定阈值。

## 结果查看

查看当前 run：

```bash
./scripts/load.sh report --run-id job-flow
```

压后服务侧证据用现有只读脚本，或通过 `load.sh` 从 manifest 透传：

```bash
./scripts/load.sh drain --run-id job-flow --strict
./scripts/load.sh pressure --run-id job-flow
```

压测期间至少同时观察：

- API 进程 CPU、内存和日志。
- worker 进程 CPU、内存和日志。
- PostgreSQL 连接数、CPU、慢查询。
- Redis/Taskiq 队列积压。

## 评估方法

先判断压测是否有效：

- 目标 API、worker、PostgreSQL 和 Redis 都在被测环境运行。
- Locust 机器没有 CPU 打满，负载发生器不是瓶颈。
- 认证、caller、`job_type` 和 `job_params` 与预期一致。
- `job-flow` case 中 Job 能进入终态。

再判断系统瓶颈：

| 现象 | 优先怀疑 |
|---|---|
| `POST /jobs` p95 升高 | DB 写入、幂等键、事务、Taskiq publish |
| `GET /jobs/{job_id}` p95 升高 | DB 读、索引、响应序列化 |
| `POST` 正常但 queued 增长 | worker 并发不足或 broker 消费不足 |
| running 长时间不下降 | executor 耗时、worker timeout 或外部依赖 |
| `flow terminal latency` 增长但 HTTP 正常 | 异步队列或 worker 是瓶颈 |
| 失败集中在 envelope 校验 | API 合同或压测 payload 不匹配 |

最终输出建议包含：

```text
被测版本/commit
环境配置：API/worker 数量、worker 并发、DB/Redis 规格
压测命令和环境变量
job-submit/job-query/job-flow 的 RPS、错误率、p95、p99
JOB flow terminal latency p95/p99
Job 终态成功率和失败样本
压测结束后的 queued/running/stuck 证据
瓶颈判断和下一步动作
```

如果只需要证明本地链路可用，运行 `job-flow` 小流量 2 分钟即可。如果要做容量评估，必须分阶段升压并记录拐点，不要直接用一个大并发数下结论。
