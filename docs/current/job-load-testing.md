# Job 接口压测指导

本文给出本服务 `POST /jobs` 和 `GET /jobs/{job_id}` 的 Locust 压测方法，从工具选型、场景设计、执行命令、指标门禁到结果评估形成一条完整流程。

本文只覆盖本模板服务的 Job 发布、查询和异步完成链路压测；不覆盖生产压测平台建设、真实模型供应商压测、跨地域压测或容量采购决策。

## 心智模型

Job 压测不是为了得到一个单独的 QPS 数字，而是回答三个问题：

| 问题 | 对应场景 | 典型结论 |
|---|---|---|
| 服务能不能接住创建请求 | `submit` | API、DB 写入、幂等键和 Taskiq publish 是否成为瓶颈 |
| 查询接口能不能承受轮询 | `query` | DB 读、索引和响应序列化是否成为瓶颈 |
| Job 能不能按预期完成 | `flow` | worker、队列、Job 执行耗时和终态成功率是否满足目标 |

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

| 目标 | 先用场景 | 参数重点 |
|---|---|---|
| 测接单能力 | `submit` | 逐步增加 `-u` 和 `-r`，观察 `POST /jobs` 错误率、p95/p99 和 queued 增长 |
| 测查询能力 | `query` | 准备足够 Job ID，逐步增加 `-u` 和 `-r`，观察 `GET /jobs/{job_id}` p95/p99 |
| 测完整生命周期 | `flow` | 设定符合业务预期的 `LOAD_FLOW_TIMEOUT_SECONDS` 和 `LOAD_POLL_INTERVAL_SECONDS`，观察终态成功率和端到端耗时 |

Locust 常用参数可以按以下方式理解：

| 参数 | 控制什么 | 设定思路 |
|---|---|---|
| `-u` | 同时运行的 Locust 用户数 | 表示并发压力，不等同真实用户数；在 `flow` 中，Job 执行越久，同样 `-u` 下每秒能创建的新 Job 越少 |
| `-r` | 每秒启动多少 Locust 用户 | 用来控制爬坡速度；太快会把启动瞬间和稳定压测混在一起 |
| `-t` | 压测持续多久 | 要覆盖启动、稳定运行和观察积压变化；太短只能看冒烟结果 |
| `LOAD_WAIT_MIN_SECONDS` / `LOAD_WAIT_MAX_SECONDS` | 单个 Locust 用户两次任务之间的等待 | 等待越短，同样 `-u` 下压力越大 |
| `LOAD_POLL_INTERVAL_SECONDS` | `flow` 轮询间隔 | 越短查询压力越大，也越接近实时观察；越长会拉高终态观测延迟 |
| `LOAD_FLOW_TIMEOUT_SECONDS` | `flow` 单个 Job 等待终态的最长时间 | 应大于预期 Job 执行时间；太短会把慢 Job 统计为失败 |

调参顺序建议是：先小并发确认场景正确，再逐步提高 `-u`，再调整 `-r` 和持续时间。不要一开始就把 `-u` 设很大，否则很难判断是 API、worker、数据库、Redis 还是负载机先到瓶颈。

### Job 执行时间影响什么

Job 执行时间会直接影响 worker 吞吐和队列积压。一个 Job 如果主要是 CPU 计算，会消耗 worker CPU；如果主要是文件、对象存储、数据库或外部模型调用，会消耗 IO、连接池、网络等待和外部服务额度。执行时间越长，同样 worker 并发下单位时间能完成的 Job 越少，queued 越容易增长。

```text
Job 完成能力 ≈ worker 并发 / 单个 Job 平均执行时间
```

这是估算，不是容量承诺。真实吞吐还会受 DB 写入、Redis/Taskiq、回调、结果大小、外部服务限流和错误重试影响。因此第一轮默认使用 `job_test_echo`，先排除真实模型供应商延迟、限流和费用，再评估模板服务自身链路。

### 如何形成结论

| 层次 | 本文对应内容 | 主要回答的问题 |
|---|---|---|
| 负载入口 | `LOAD_SCENARIO`、`-u`、`-r`、`-t` | 要压哪条链路、压多少、压多久 |
| 观察方式 | `--headless`、`--autostart`、默认 web UI、`--csv`、`--html` | 在终端、浏览器还是文件里看结果 |
| 服务证据 | `scripts/jobs.sh`、API/worker/DB/Redis 指标 | 慢在接口、队列、worker 还是存储 |
| 评估结论 | 指标门禁和瓶颈判断 | 这次结果是否有效，下一步该调哪里 |

如果 HTTP 指标正常但 queued 持续增长，优先看 worker 和 Job 执行路径。如果 `GET /jobs/{job_id}` p95 升高，优先看 DB 读、索引和序列化。如果 `JOB flow terminal latency` 增长但 HTTP p95 仍低，说明用户等待主要花在异步队列和 worker 执行上，而不是 API 接口本身。

## 快速开始

第一次使用时，先跑一轮小流量 `flow`。它会创建 Job、轮询状态并统计端到端完成耗时，最适合确认压测链路是否可用。

```bash
./scripts/dev.sh bootstrap
./scripts/dev.sh start
./scripts/dev.sh status
mkdir -p .run/load
```

确认 Locust 能加载场景：

```bash
uv run --group load locust -f scripts/load/locustfile.py --list
```

跑一轮 2 分钟的小流量端到端压测：

```bash
LOAD_SCENARIO=flow \
uv run --group load locust -f scripts/load/locustfile.py \
  --host http://127.0.0.1:8100 \
  --headless -u 10 -r 2 -t 2m \
  --csv .run/load/quick-flow \
  --html .run/load/quick-flow.html
```

这轮命令的含义：

| 参数 | 含义 |
|---|---|
| `LOAD_SCENARIO=flow` | 创建 Job 并轮询到终态 |
| `--host http://127.0.0.1:8100` | 被压测的本地 API |
| `--headless` | 不打开 Web UI，直接执行 |
| `-u 10` | 最高 10 个 Locust 用户并发 |
| `-r 2` | 每秒启动 2 个 Locust 用户 |
| `-t 2m` | 持续 2 分钟 |
| `--csv .run/load/quick-flow` | 生成 `quick-flow_*` CSV 结果文件 |
| `--html .run/load/quick-flow.html` | 生成单文件 HTML 报告 |

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

需要边跑边看页面时，把 `--headless` 换成 `--autostart`，并打开 `http://127.0.0.1:8089`：

```bash
LOAD_SCENARIO=flow \
uv run --group load locust -f scripts/load/locustfile.py \
  --host http://127.0.0.1:8100 \
  --autostart -u 10 -r 2 -t 2m \
  --web-host 127.0.0.1 --web-port 8089 \
  --csv .run/load/quick-flow-ui
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

Locust 的场景文件是 Python，容易复用本仓库现有 `.env`、认证头和 envelope 校验方式。第一版不引入 Typer CLI、JMeter 测试计划、Grafana dashboard 或独立压测结果数据库；Locust 自带的 headless、CSV 和 web UI 已经能覆盖本服务当前压测目标。

只有在以下情况才升级方案：

- 公司已有 k6/Grafana/Kubernetes 压测平台，且压测结果必须进入统一平台。
- 公司已有 JMeter 中台，且需要接入现有审批、调度和报表流程。
- 需要多机、跨地域或百万级流量发生器。
- 压测场景已经稳定到需要统一 CLI 包装、跑前检查和阈值报告。

## 压测对象

默认接口前缀是 `/api/v1/ai-jobs`，由 `SERVICE_API_PREFIX` 配置。

核心接口：

```text
POST /api/v1/ai-jobs/jobs
GET  /api/v1/ai-jobs/jobs/{job_id}
```

第一版默认使用内置 `job_test_echo`。它不调用真实模型，适合压测 API、数据库、Taskiq 发布、worker 消费和 Job 查询路径。需要压 workflow root / internal child / root finalize 链路时，使用内置 `job_test_workflow`。这些压测类型在 registry 中标记为 `visibility="demo"`，查看完整目录时使用 `./scripts/jobs.sh types --all`。不要一开始压真实模型 `job_type`，否则模型供应商延迟、限流和费用会掩盖服务自身瓶颈。

## 场景结构

`scripts/load/locustfile.py` 支持三个场景，通过 `LOAD_SCENARIO` 选择：

| 场景 | 作用 | 适用问题 |
|---|---|---|
| `submit` | 只创建 Job | API 和 DB 写入、幂等键、Taskiq publish 能承受多少接单流量 |
| `query` | 只查询已有 Job | 查询接口、DB 读、索引和响应序列化能承受多少轮询流量 |
| `flow` | 创建 Job 并轮询到终态 | 端到端 Job 生命周期、worker 消费能力、队列积压和终态成功率 |

真实业务评估应按顺序执行：先 `submit`，再 `query`，最后 `flow`。只看 `POST /jobs` QPS 不代表系统能完成 Job。

## 准备环境

安装依赖：

```bash
./scripts/dev.sh bootstrap
```

验证 Locust 依赖组和场景文件可加载：

```bash
uv run --group load locust -f scripts/load/locustfile.py --list
```

启动本地依赖、API 和 worker：

```bash
./scripts/dev.sh start
./scripts/dev.sh status
```

本地默认 `SERVICE_API_KEY=dev-service-key`。如果 `.env` 中没有关闭认证，Locust 会自动读取 `.env` 并发送：

```http
Authorization: Bearer <SERVICE_API_KEY>
X-AI-Service-Caller-ID: locust-load
```

如需跳过本地认证，与服务保持同一套配置：

```bash
DISABLE_HTTP_AUTH_HEADER=true
```

## 执行压测

压测输出建议写到 `.run/load/`，该目录已被 git 忽略：

```bash
mkdir -p .run/load
```

Locust 有三种常用执行模式：

| 模式 | 是否启动 web UI | 是否自动开始 | 适用场景 |
|---|---|---|---|
| `--headless` | 否 | 是 | 脚本、CI、后台压测，只看终端、CSV 或 HTML 报告 |
| `--autostart` | 是 | 是 | 需要自动开跑，同时在浏览器实时观察 RPS、失败率和响应时间 |
| 默认 web UI | 是 | 否 | 先打开页面，手动填写并启动压测 |

如果只是执行压测并保存结果，使用 `--headless`。如果需要实时观察，使用 `--autostart`，并指定 web UI 监听地址：

```bash
--autostart --web-host 127.0.0.1 --web-port 8089
```

如果需要自己在页面上操作，不要带 `--headless` 或 `--autostart`，只启动 web UI：

```bash
LOAD_SCENARIO=submit \
uv run --group load locust -f scripts/load/locustfile.py \
  --host http://127.0.0.1:8100 \
  --web-host 127.0.0.1 --web-port 8089 \
  --csv .run/load/job-submit
```

然后打开 `http://127.0.0.1:8089`，在页面里填写用户数和启动速率，再点击开始。`LOAD_SCENARIO`、`LOAD_JOB_TYPE`、`LOAD_CALLER_ID` 等环境变量在启动 Locust 进程时确定，不能在 web UI 里切换；页面主要用于控制并发、启动、停止、重置和下载结果。

`--host` 是被压测 API 地址，不是 Locust web UI 地址。打开 web UI 时访问的是 `--web-host` 和 `--web-port`，例如 `http://127.0.0.1:8089`。

### 发布接口

```bash
LOAD_SCENARIO=submit \
uv run --group load locust -f scripts/load/locustfile.py \
  --host http://127.0.0.1:8100 \
  --headless -u 100 -r 10 -t 10m \
  --csv .run/load/job-submit
```

需要实时查看时，把 `--headless` 换成 `--autostart`：

```bash
LOAD_SCENARIO=submit \
uv run --group load locust -f scripts/load/locustfile.py \
  --host http://127.0.0.1:8100 \
  --autostart -u 100 -r 10 -t 10m \
  --web-host 127.0.0.1 --web-port 8089 \
  --csv .run/load/job-submit
```

浏览器打开：

```text
http://127.0.0.1:8089
```

需要自己在页面上启动时，去掉 `--autostart -u 100 -r 10 -t 10m`，在页面中填写：

| 页面字段 | 示例值 | 对应命令参数 |
|---|---:|---|
| 用户数 | `100` | `-u 100` |
| 启动速率 | `10` | `-r 10` |
| 被测地址 | `http://127.0.0.1:8100` | `--host http://127.0.0.1:8100` |

### 查询接口

查询压测需要先准备一批已有 Job ID。`GET /jobs/{job_id}` 会按 caller 隔离数据，因此 Job ID 必须来自同一个 `X-AI-Service-Caller-ID`。默认 Locust caller 是 `locust-load`；如果 Job ID 来自 `workflow-smoke`，查询压测也要显式使用 `LOAD_CALLER_ID=verify-workflow-smoke`。

查看默认 Locust caller 创建的 Job：

```bash
./scripts/jobs.sh list --caller-id locust-load --since 10m --limit 20 --json
```

```bash
LOAD_SCENARIO=query \
LOAD_QUERY_JOB_IDS_FILE=.run/load/job-ids.txt \
uv run --group load locust -f scripts/load/locustfile.py \
  --host http://127.0.0.1:8100 \
  --headless -u 100 -r 10 -t 10m \
  --csv .run/load/job-query
```

少量 Job ID 也可以直接用逗号分隔：

```bash
LOAD_SCENARIO=query \
LOAD_CALLER_ID=verify-workflow-smoke \
LOAD_QUERY_JOB_IDS=00000000-0000-0000-0000-000000000000,11111111-1111-1111-1111-111111111111 \
uv run --group load locust -f scripts/load/locustfile.py --host http://127.0.0.1:8100 --headless -u 20 -r 5 -t 2m
```

### 完整 Job 流程

```bash
LOAD_SCENARIO=flow \
LOAD_POLL_INTERVAL_SECONDS=0.5 \
LOAD_FLOW_TIMEOUT_SECONDS=30 \
uv run --group load locust -f scripts/load/locustfile.py \
  --host http://127.0.0.1:8100 \
  --headless -u 50 -r 5 -t 10m \
  --csv .run/load/job-flow
```

`flow` 场景会额外上报 Locust 自定义指标：

```text
JOB flow terminal latency
```

该指标表示从创建 Job 到查询到终态的端到端耗时。终态不是 `succeeded` 时记为失败。

## 可配置参数

| 环境变量 | 默认值 | 说明 |
|---|---:|---|
| `LOAD_SCENARIO` | `flow` | `submit`、`query` 或 `flow` |
| `LOAD_JOB_TYPE` | `job_test_echo` | 压测使用的 `job_type` |
| `LOAD_JOB_PARAMS_JSON` | 无 | 非内置动态压测 job 时使用的 `job_params` JSON |
| `LOAD_ECHO_REPEAT` | `1` | `job_test_echo.repeat` |
| `LOAD_ECHO_SLEEP_SECONDS` | `15` | 压测默认让 `job_test_echo` 模拟执行耗时 |
| `LOAD_WORKFLOW_MODE` | `group` | `job_test_workflow.mode`，可选 `single`、`chain`、`group`、`chord`、`map`、`starmap`、`chunks` |
| `LOAD_WORKFLOW_SLEEP_SECONDS` | `15` | `job_test_workflow` 子任务模拟执行耗时 |
| `LOAD_POLL_INTERVAL_SECONDS` | `0.5` | `flow` 轮询间隔 |
| `LOAD_FLOW_TIMEOUT_SECONDS` | `30` | `flow` 单个 Job 等待终态超时 |
| `LOAD_QUERY_JOB_IDS` | 无 | `query` 场景用逗号分隔 Job ID |
| `LOAD_QUERY_JOB_IDS_FILE` | 无 | `query` 场景用 Job ID 文件 |
| `LOAD_WAIT_MIN_SECONDS` | `0.1` | Locust 用户两次任务之间的最小等待 |
| `LOAD_WAIT_MAX_SECONDS` | `1.0` | Locust 用户两次任务之间的最大等待 |
| `LOAD_CALLER_ID` | `locust-load` | `X-AI-Service-Caller-ID` |

Workflow root / child 链路压测示例：

```bash
LOAD_JOB_TYPE=job_test_workflow \
LOAD_WORKFLOW_MODE=group \
LOAD_WORKFLOW_SLEEP_SECONDS=15 \
LOAD_SCENARIO=flow \
LOAD_FLOW_TIMEOUT_SECONDS=90 \
uv run --group load locust -f scripts/load/locustfile.py --host http://127.0.0.1:8100 --headless -u 10 -r 2 -t 5m
```

其它业务 `job_type` 的压测示例：

```bash
LOAD_JOB_TYPE=your_job_type \
LOAD_JOB_PARAMS_JSON='{"field":"value"}' \
LOAD_SCENARIO=flow \
uv run --group load locust -f scripts/load/locustfile.py --host http://127.0.0.1:8100 --headless -u 10 -r 2 -t 5m
```

常用 Locust 命令参数：

| 参数 | 示例 | 含义 |
|---|---|---|
| `-f` | `scripts/load/locustfile.py` | 使用的 Locust 场景文件 |
| `--host` | `http://127.0.0.1:8100` | 被压测服务的基础地址 |
| `-u` | `100` | 峰值并发 Locust 用户数 |
| `-r` | `10` | 每秒启动的用户数 |
| `-t` | `10m` | 压测持续时间 |
| `--csv` | `.run/load/job-submit` | CSV 输出前缀，不是目录或单个文件名 |
| `--html` | `.run/load/job-submit.html` | 跑完后生成单文件 HTML 报告 |
| `--web-host` | `127.0.0.1` | Locust web UI 监听地址 |
| `--web-port` | `8089` | Locust web UI 监听端口 |

如果需要模拟失败率、大结果或可变 fan-out，不要把这些逻辑塞进 Locust，也不要把 `job_test_workflow` 扩展成通用压测 DSL。应按 [`../api/extension-guide.md`](../api/extension-guide.md) 新增明确的压测专用 `job_type`，例如暴露 `sleep_seconds`、`result_size_bytes` 和 `should_fail`。

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

Locust headless 会在终端输出汇总，并生成 CSV：

```text
.run/load/job-flow_stats.csv
.run/load/job-flow_failures.csv
.run/load/job-flow_exceptions.csv
.run/load/job-flow_stats_history.csv
```

`--csv .run/load/job-flow` 只是文件名前缀。Locust 会按这个前缀生成固定文件名，不会为每次点击 Start 自动创建新一轮结果。

结果文件管理建议：

| 场景 | 行为 | 建议 |
|---|---|---|
| 重新启动 Locust，复用同一个 `--csv` 前缀 | 旧 CSV 会被覆盖 | 临时调试可以这样做 |
| 同一个 web UI 进程内多次 Start/Stop | `_stats.csv`、`_failures.csv`、`_exceptions.csv` 持续重写，`_stats_history.csv` 持续追加时间点 | 不要把它当成多轮独立结果 |
| 正式压测或需要对比多轮结果 | 每轮都应保留独立文件 | 使用带时间或轮次的唯一前缀 |

正式压测建议每轮换一个前缀，例如：

```bash
--csv .run/load/job-flow-20260625-1930
```

不需要每次压测前手动清空 `.run/load/`。只有在确认历史结果不再需要，或者为了避免误读旧文件时，才清理该目录。

需要实时观察时启动 web UI：

```bash
LOAD_SCENARIO=flow \
uv run --group load locust -f scripts/load/locustfile.py \
  --host http://127.0.0.1:8100 \
  --autostart -u 50 -r 5 -t 10m \
  --web-host 127.0.0.1 --web-port 8089 \
  --csv .run/load/job-flow
```

需要手动从页面开始时：

```bash
LOAD_SCENARIO=flow \
uv run --group load locust -f scripts/load/locustfile.py \
  --host http://127.0.0.1:8100 \
  --web-host 127.0.0.1 --web-port 8089 \
  --csv .run/load/job-flow
```

只需要跑完后离线查看报告时，保留 `--headless` 并增加 `--html`：

```bash
LOAD_SCENARIO=flow \
uv run --group load locust -f scripts/load/locustfile.py \
  --host http://127.0.0.1:8100 \
  --headless -u 50 -r 5 -t 10m \
  --csv .run/load/job-flow \
  --html .run/load/job-flow.html
```

服务侧证据用现有只读脚本：

```bash
./scripts/jobs.sh list --status running --since 10m --limit 20
./scripts/jobs.sh stuck --older-than 5m
./scripts/jobs.sh inspect <job_id>
./scripts/jobs.sh types --json
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
- `flow` 场景中 Job 能进入终态。

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
submit/query/flow 的 RPS、错误率、p95、p99
JOB flow terminal latency p95/p99
Job 终态成功率和失败样本
压测结束后的 queued/running/stuck 证据
瓶颈判断和下一步动作
```

如果只需要证明本地链路可用，运行 `flow` 小流量 2 分钟即可。如果要做容量评估，必须分阶段升压并记录拐点，不要直接用一个大并发数下结论。
