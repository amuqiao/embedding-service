# Job 压测执行计划

本文给出一套可重复执行的 Job 压测命令，用于压到本服务的 active Job 容量边界、定位瓶颈，并反推 `MAX_ACTIVE_JOBS` 的安全值。

本文只覆盖本地或单环境的 Locust 压测执行计划；指标含义、场景设计和 Locust 参数说明见 [`job-load-testing.md`](job-load-testing.md)。

## 当前本地前提

本仓库本地开发入口统一从根目录 `.env` 读取应用配置、本地端口和 worker 启动参数。压测前先确认当前状态：

```bash
./scripts/dev.sh status
rg -n '^(DISABLE_HTTP_AUTH_HEADER|DISABLE_CALLER_ID_HEADER|MAX_ACTIVE_JOBS|API_PORT|WORKER_CONCURRENCY)=' .env
```

如果 `.env` 中是以下本地联调配置：

```text
DISABLE_HTTP_AUTH_HEADER=true
DISABLE_CALLER_ID_HEADER=true
```

则压测请求不会发送 `Authorization` 和 `X-AI-Service-Caller-ID`，服务端统一使用 `caller_id=default`。后续所有 `jobs.sh` 查询都应使用：

```bash
./scripts/jobs.sh list --caller-id default --since 10m --limit 20
```

只有在压测命令显式使用下面两项时，才用 `--caller-id locust-load` 查询：

```bash
DISABLE_CALLER_ID_HEADER=false
LOAD_CALLER_ID=locust-load
```

## 压测目标

本计划按四个层次执行：

| 阶段   | 场景                 | 主要观察                                            |
| ---- | ------------------ | ----------------------------------------------- |
| 冒烟   | `flow`             | 发任务、轮询、终态统计是否可用                                 |
| 拆分压测 | `submit` / `query` | `POST /jobs` 和 `GET /jobs/{job_id}` 各自的 HTTP 能力 |
| 容量触顶 | `submit`           | `MAX_ACTIVE_JOBS` 是否先触发，API 是否仍健康                 |
| 完整压测 | `flow`             | worker 消费、队列积压和端到端完成耗时                          |

`flow` 场景会先 `POST /jobs`，再循环 `GET /jobs/{job_id}`，最后上报 `JOB flow terminal latency`。默认压测任务是 `job_test_echo`，Locust 默认传入 `LOAD_ECHO_SLEEP_SECONDS=15`，用于模拟 15 秒执行耗时。需要压 root/child workflow 链路时，切换到 `job_test_workflow`，并用 `LOAD_WORKFLOW_MODE` 和 `LOAD_WORKFLOW_SLEEP_SECONDS` 控制固定示例模式。

## 判定方法

`MAX_ACTIVE_JOBS` 不是吞吐配置，而是接单保护阈值。压测时先判断失败类型，再决定是调入口速率、调 worker，还是调 `MAX_ACTIVE_JOBS`。

| 现象 | 判定 | 下一步 |
| --- | --- | --- |
| `POST /jobs` 返回 503，响应体含 `active_jobs` 和 `limit` | 命中 active Job 门禁 | 记录该压力档为容量触顶档 |
| 503 出现后 API `/health` 仍 200，`queued/running/stuck` 可排空 | 保护生效，服务未崩 | 不把这类 503 当脚本异常 |
| 提高 `MAX_ACTIVE_JOBS` 后出现 HTTP 500、API 退出或健康检查失败 | 超过当前环境硬承载 | 查 `logs/api.log` 或 PostgreSQL 日志确认根因，回退到上一个稳定值 |
| 压测结束后长期存在 `running` 或 `stuck` | worker 或 Job 生命周期存在瓶颈 | 先查 worker 日志和 Job stuck 原因 |

估算 active 容量需求：

```text
active_jobs_needed ~= accepted_submit_rps * p95_job_active_seconds
```

完成阶梯探索后，安全值按两条线取较小值：

```text
业务所需安全值 = ceil(active_jobs_needed * 1.2)
环境安全上限 = floor(环境硬上限 * 0.7)
建议 MAX_ACTIVE_JOBS = min(业务所需安全值, 环境安全上限)
```

`环境硬上限` 是提高 `MAX_ACTIVE_JOBS` 后，API、DB、Redis、worker 仍健康且 Job 可排空的最高探测档。未完成阶梯探索前，不要用当前默认值反推更高安全上限。

如果业务所需安全值高于环境安全上限，不要继续只放大 `MAX_ACTIVE_JOBS`，应先扩 worker、DB、Redis、API 进程或降低入口速率。

## 前置准备

```bash
export HOST="${HOST:-http://127.0.0.1:18200}"
export UV_CACHE_DIR="${UV_CACHE_DIR:-.uv-cache}"
export RUN_ID="$(date +%Y%m%d-%H%M%S)"
export MAX_ACTIVE_JOBS="$(awk -F= '/^MAX_ACTIVE_JOBS=/{print $2}' .env)"
mkdir -p .run/load
```

如果服务未启动，先启动本地依赖、API 和 worker：

```bash
./scripts/dev.sh restart
./scripts/dev.sh status
```

确认 Locust 能加载压测脚本：

```bash
uv run --group load locust -f scripts/load/locustfile.py --list
```

## 浏览器实时查看

用 `--autostart` 启动 Locust Web UI，并自动开始一轮小流量 `flow`：

```bash
LOAD_SCENARIO=flow \
LOAD_ECHO_SLEEP_SECONDS=15 \
LOAD_POLL_INTERVAL_SECONDS=0.5 \
LOAD_FLOW_TIMEOUT_SECONDS=45 \
uv run --group load locust -f scripts/load/locustfile.py \
  --host "$HOST" \
  --autostart -u 10 -r 2 -t 1m \
  --web-host 127.0.0.1 --web-port 8089 \
  --csv ".run/load/${RUN_ID}-flow-ui" \
  --html ".run/load/${RUN_ID}-flow-ui.html"
```

浏览器打开：

```text
http://127.0.0.1:8089
```

`--host` 是被压测 API 地址；浏览器访问的是 Locust UI 地址。压测结束后，HTML 报告会写入 `.run/load/${RUN_ID}-flow-ui.html`。

## 冒烟压测

先跑短时间完整链路，确认发任务、轮询和终态统计都正常：

```bash
LOAD_SCENARIO=flow \
LOAD_ECHO_SLEEP_SECONDS=15 \
LOAD_POLL_INTERVAL_SECONDS=0.5 \
LOAD_FLOW_TIMEOUT_SECONDS=45 \
uv run --group load locust -f scripts/load/locustfile.py \
  --host "$HOST" \
  --headless -u 4 -r 1 -t 60s \
  --csv ".run/load/${RUN_ID}-flow-smoke" \
  --html ".run/load/${RUN_ID}-flow-smoke.html"
```

查看结果：

```bash
sed -n '1,20p' ".run/load/${RUN_ID}-flow-smoke_stats.csv"
sed -n '1,50p' ".run/load/${RUN_ID}-flow-smoke_failures.csv"
sed -n '1,50p' ".run/load/${RUN_ID}-flow-smoke_exceptions.csv"
```

如果 `JOB flow terminal latency` 有失败，先看是否超过 `LOAD_FLOW_TIMEOUT_SECONDS` 仍未到 `succeeded`。这类失败表示 Job 流程超时，不等同于 `GET /jobs/{job_id}` HTTP 请求失败。

本地 `job_test_echo` 使用 `asyncio.sleep` 模拟 15 秒等待，因此即使 `WORKER_CONCURRENCY=1`，也可能看到多个任务在同一 worker 事件循环中重叠等待。不要仅凭 `WORKER_CONCURRENCY=1` 推断端到端吞吐一定是 `1 / 15s`。

## Workflow 模式压测

需要覆盖 root orchestration、internal child 创建、child attempt 执行、downstream advance 和 root finalize 时，使用 `job_test_workflow`：

```bash
LOAD_SCENARIO=flow \
LOAD_JOB_TYPE=job_test_workflow \
LOAD_WORKFLOW_MODE=group \
LOAD_WORKFLOW_SLEEP_SECONDS=15 \
LOAD_POLL_INTERVAL_SECONDS=0.5 \
LOAD_FLOW_TIMEOUT_SECONDS=90 \
uv run --group load locust -f scripts/load/locustfile.py \
  --host "$HOST" \
  --headless -u 4 -r 1 -t 60s \
  --csv ".run/load/${RUN_ID}-workflow-group-smoke" \
  --html ".run/load/${RUN_ID}-workflow-group-smoke.html"
```

常用模式：

| 模式 | 压测重点 |
|---|---|
| `single` | root + one child 的 workflow 形态 |
| `group` | 并行 fan-out child 创建和完成 |
| `chord` | 并行 child 完成后的 join 依赖 |
| `chain` | 线性 `depends_on` 推进 |
| `chunks` | 分块 child 创建 |
| `starmap` | 参数展开到多个算术 child |

这些模式是固定开发者示例，不是任意 DAG 压测入口。需要失败率、大结果或可变 fan-out 时，应新增压测专用 `job_type`。

## 发布接口压测

只压 `POST /jobs`，用于观察 API 接单、数据库写入、幂等键和 Taskiq publish：

```bash
LOAD_SCENARIO=submit \
LOAD_ECHO_SLEEP_SECONDS=15 \
uv run --group load locust -f scripts/load/locustfile.py \
  --host "$HOST" \
  --headless -u 20 -r 10 -t 30s \
  --csv ".run/load/${RUN_ID}-submit-u20" \
  --html ".run/load/${RUN_ID}-submit-u20.html"
```

查看结果和积压：

```bash
sed -n '1,20p' ".run/load/${RUN_ID}-submit-u20_stats.csv"
sed -n '1,50p' ".run/load/${RUN_ID}-submit-u20_failures.csv"
./scripts/jobs.sh pressure \
  --caller-id default \
  --since 10m \
  --max-active-jobs "${MAX_ACTIVE_JOBS}" \
  --locust-prefix ".run/load/${RUN_ID}-submit-u20" \
  --api-log logs/api.log \
  --api-log-tail 2000
./scripts/jobs.sh drain --caller-id default --since 10m --older-than 1m --json
./scripts/jobs.sh list --caller-id default --status failed --since 10m --limit 20
./scripts/jobs.sh stuck --caller-id default --since 10m --older-than 1m
```

如果失败响应包含下面结构，说明接单容量门禁生效：

```json
{"code":"900503","msg":"service unavailable","data":{"active_jobs":51,"limit":50}}
```

这不是 API 崩溃，而是 `MAX_ACTIVE_JOBS` 正在阻止继续接单。此时瓶颈是当前允许的 active job 容量，下一步应在“提高容量”和“降低入口速率”之间选择。

## 容量极限探索

容量探索只改一个变量：`MAX_ACTIVE_JOBS`。每一档都先跑稳定档，再跑触顶档，最后确认 API 健康和 Job 是否排空。

### 1. 稳定档

保持当前 `MAX_ACTIVE_JOBS`，先验证低入口速率可以稳定接单：

```bash
LOAD_SCENARIO=submit \
LOAD_ECHO_SLEEP_SECONDS=15 \
LOAD_WAIT_MIN_SECONDS=1.5 \
LOAD_WAIT_MAX_SECONDS=2.0 \
uv run --group load locust -f scripts/load/locustfile.py \
  --host "$HOST" \
  --headless -u 4 -r 2 -t 60s \
  --csv ".run/load/${RUN_ID}-max${MAX_ACTIVE_JOBS:-50}-submit-paced-u4" \
  --html ".run/load/${RUN_ID}-max${MAX_ACTIVE_JOBS:-50}-submit-paced-u4.html"
```

验收：

```bash
sed -n '1,20p' ".run/load/${RUN_ID}-max${MAX_ACTIVE_JOBS:-50}-submit-paced-u4_stats.csv"
sed -n '1,50p' ".run/load/${RUN_ID}-max${MAX_ACTIVE_JOBS:-50}-submit-paced-u4_failures.csv"
./scripts/jobs.sh drain --caller-id default --since 10m --strict
./scripts/jobs.sh list --caller-id default --status failed --since 10m --limit 20
./scripts/jobs.sh stuck --caller-id default --since 10m --older-than 10m --limit 20
curl -s -i "$HOST/health"
```

稳定档要求：HTTP 失败率为 0，API 健康检查 200，压测后 `drain --strict` 通过，即没有 active、failed 或 stuck 证据。

### 2. 触顶档

保持同一个 `MAX_ACTIVE_JOBS`，提高入口速率，确认保护阈值是否先触发：

```bash
LOAD_SCENARIO=submit \
LOAD_ECHO_SLEEP_SECONDS=15 \
uv run --group load locust -f scripts/load/locustfile.py \
  --host "$HOST" \
  --headless -u 8 -r 4 -t 30s \
  --csv ".run/load/${RUN_ID}-max${MAX_ACTIVE_JOBS:-50}-submit-u8" \
  --html ".run/load/${RUN_ID}-max${MAX_ACTIVE_JOBS:-50}-submit-u8.html"
```

验收：

```bash
sed -n '1,20p' ".run/load/${RUN_ID}-max${MAX_ACTIVE_JOBS:-50}-submit-u8_stats.csv"
sed -n '1,50p' ".run/load/${RUN_ID}-max${MAX_ACTIVE_JOBS:-50}-submit-u8_failures.csv"
./scripts/jobs.sh drain --caller-id default --since 10m --older-than 1m --json
./scripts/jobs.sh list --caller-id default --status failed --since 10m --limit 20
./scripts/jobs.sh stuck --caller-id default --since 10m --older-than 1m --limit 20
curl -s -i "$HOST/health"
```

如果失败主要是 `HTTP 503` 且响应体包含 `active_jobs=<limit>`，这一档说明 `MAX_ACTIVE_JOBS` 先到边界。只要 API 健康且 Job 可排空，这就是容量保护生效，不是压测脚本异常。

### 3. 提高 active 上限

只有当触顶档主要是 503、且 API/DB/Redis/worker 都保持健康时，才小步提高 `.env`：

```bash
perl -0pi -e 's/^MAX_ACTIVE_JOBS=.*/MAX_ACTIVE_JOBS=75/m' .env
export MAX_ACTIVE_JOBS=75
./scripts/dev.sh restart api
./scripts/dev.sh status api
```

然后重复“稳定档”和“触顶档”。低容量从小档开始，高容量从当前值附近二分收敛：

```text
低容量: 50 -> 75 -> 100 -> 150 -> 200
高容量: 600 -> 700 -> 750 -> 775 -> 800 -> 1000
```

不要从 `50` 直接跳到数百或数千。一次过大的容量放开会让 API、DB、broker 和 worker 同时承受更多 active Job，可能把“503 容量保护”变成进程退出或资源耗尽。

### 4. 记录结论

每一档记录：

```text
MAX_ACTIVE_JOBS:
WORKER_CONCURRENCY:
LOAD_ECHO_SLEEP_SECONDS:
稳定档 accepted RPS / 失败率 / p95:
触顶档 accepted RPS / 失败率 / 主要失败:
压测后 queued/running/stuck:
API health:
结论:
```

最终只把“最后一个稳定且可排空的 `MAX_ACTIVE_JOBS` 探测档”作为环境硬上限，再按 70% 计算建议安全值。

## 入口速率调优复测

优先先做入口速率调优，不要直接把 `MAX_ACTIVE_JOBS` 大幅放大。下面命令保持 `MAX_ACTIVE_JOBS=50`，降低 Locust 用户数并拉长用户间等待：

```bash
LOAD_SCENARIO=submit \
LOAD_ECHO_SLEEP_SECONDS=15 \
LOAD_WAIT_MIN_SECONDS=1.5 \
LOAD_WAIT_MAX_SECONDS=2.0 \
uv run --group load locust -f scripts/load/locustfile.py \
  --host "$HOST" \
  --headless -u 4 -r 2 -t 60s \
  --csv ".run/load/${RUN_ID}-submit-paced-u4" \
  --html ".run/load/${RUN_ID}-submit-paced-u4.html"
```

复测后看失败率和 active 状态：

```bash
sed -n '1,20p' ".run/load/${RUN_ID}-submit-paced-u4_stats.csv"
sed -n '1,50p' ".run/load/${RUN_ID}-submit-paced-u4_failures.csv"
./scripts/jobs.sh list --caller-id default --status queued --since 10m --limit 20
./scripts/jobs.sh list --caller-id default --status running --since 10m --limit 20
```

如果业务确实需要更大接单窗口，再小步提高 `.env` 中的 `MAX_ACTIVE_JOBS`，重启 API 后复测同一条 submit 命令：

```bash
./scripts/dev.sh restart api
./scripts/dev.sh status api
```

不要从 `50` 直接跳到数百或数千。一次过大的容量放开会让 API、DB、broker 和 worker 同时承受更多 active Job，可能把“503 容量保护”变成进程退出或资源耗尽。

## 查询接口压测

先准备同一个 caller 下的 Job ID：

```bash
./scripts/jobs.sh list --caller-id default --since 30m --limit 200 --json \
  | python3 -c 'import json,sys; print("\n".join(j.get("job_id") or str(j.get("id")) for j in json.load(sys.stdin)["jobs"]))' \
  > .run/load/query-job-ids.txt
```

只压 `GET /jobs/{job_id}`：

```bash
LOAD_SCENARIO=query \
LOAD_QUERY_JOB_IDS_FILE=.run/load/query-job-ids.txt \
uv run --group load locust -f scripts/load/locustfile.py \
  --host "$HOST" \
  --headless -u 100 -r 10 -t 5m \
  --csv ".run/load/${RUN_ID}-query-u100" \
  --html ".run/load/${RUN_ID}-query-u100.html"
```

查看结果：

```bash
sed -n '1,20p' ".run/load/${RUN_ID}-query-u100_stats.csv"
sed -n '1,50p' ".run/load/${RUN_ID}-query-u100_failures.csv"
```

如果 ID 文件为空，先跑 `submit` 或 `flow` 生成一批 `default` caller 的 Job。

## 完整链路压测

中等流量压完整异步链路：

```bash
LOAD_SCENARIO=flow \
LOAD_ECHO_SLEEP_SECONDS=15 \
LOAD_POLL_INTERVAL_SECONDS=0.5 \
LOAD_FLOW_TIMEOUT_SECONDS=45 \
uv run --group load locust -f scripts/load/locustfile.py \
  --host "$HOST" \
  --headless -u 30 -r 5 -t 10m \
  --csv ".run/load/${RUN_ID}-flow-u30" \
  --html ".run/load/${RUN_ID}-flow-u30.html"
```

更高压力只调整 `-u` 和 `-r`，不要同时修改多个变量：

```bash
LOAD_SCENARIO=flow \
LOAD_ECHO_SLEEP_SECONDS=15 \
LOAD_POLL_INTERVAL_SECONDS=0.5 \
LOAD_FLOW_TIMEOUT_SECONDS=60 \
uv run --group load locust -f scripts/load/locustfile.py \
  --host "$HOST" \
  --headless -u 60 -r 10 -t 10m \
  --csv ".run/load/${RUN_ID}-flow-u60" \
  --html ".run/load/${RUN_ID}-flow-u60.html"
```

查看最终证据：

```bash
sed -n '1,20p' ".run/load/${RUN_ID}-flow-u30_stats.csv"
sed -n '1,50p' ".run/load/${RUN_ID}-flow-u30_failures.csv"
sed -n '1,50p' ".run/load/${RUN_ID}-flow-u30_exceptions.csv"
./scripts/jobs.sh list --status running --since 30m --limit 20
./scripts/jobs.sh stuck --older-than 5m
```

## 本轮本地结果

以下结果来自本地 `20260626` 压测：

```text
API: http://127.0.0.1:18200
DISABLE_HTTP_AUTH_HEADER=true
DISABLE_CALLER_ID_HEADER=true
WORKER_CONCURRENCY=1
LOAD_ECHO_SLEEP_SECONDS=15
LOAD_SCENARIO=submit
Locust: -u 20 -r 10 -t 30s
```

本轮目标是从初始 `MAX_ACTIVE_JOBS=1000` 向下收敛，找到当前本地单 API、单 worker、PostgreSQL 本地容器配置下的接单上界。`750` 是历史按 HTTP 接单结果统计的最高无失败值；`775` 已经出现 500 和 queued 残留。当前 `.env` 可以是 `MAX_ACTIVE_JOBS=1000`，但这个值在本地形态下必须重新压测证明，不能直接视为安全值。

后续使用 `scripts/jobs.sh drain/inspect/stuck` 复测发现，HTTP 0 失败不等于 Job 全链路通过。后续再认定某个档位为安全档时，必须同时满足：

```text
Locust HTTP failures = 0
./scripts/jobs.sh drain --strict 通过
summary.jobs.failed = 0
drain.stuck.total = 0，pressure.stuck.sample_count = 0
/health = 200
```

### 复现命令

每一档只改 `.env` 中的 `MAX_ACTIVE_JOBS`。开始下一档前，必须先确认上一档已经排空；否则 active Job 计数会污染后续结果。

空载基线检查：

```bash
./scripts/jobs.sh drain --caller-id default --since 30m --strict
./scripts/jobs.sh stuck --caller-id default --since 30m --older-than 10m --limit 20
curl -s -i http://127.0.0.1:18200/health
```

`drain --strict` 通过、`stuck=0`、`/health=200` 后再开始该档压测。

切换档位并重启 API：

```bash
perl -0pi -e 's/^MAX_ACTIVE_JOBS=.*/MAX_ACTIVE_JOBS=750/m' .env
./scripts/dev.sh restart api
./scripts/dev.sh status api
```

压测命令：

```bash
uv run --group load locust -f scripts/load/locustfile.py \
  --host http://127.0.0.1:18200 \
  --headless -u 20 -r 10 -t 30s \
  --csv .run/load/20260626-max750-submit-u20 \
  --html .run/load/20260626-max750-submit-u20.html
```

每档压测后检查：

```bash
sed -n '1,20p' .run/load/20260626-max750-submit-u20_stats.csv
sed -n '1,50p' .run/load/20260626-max750-submit-u20_failures.csv
./scripts/jobs.sh pressure \
  --caller-id default \
  --since 20m \
  --max-active-jobs 750 \
  --locust-prefix .run/load/20260626-max750-submit-u20 \
  --api-log logs/api.log \
  --api-log-tail 2000
curl -s -i http://127.0.0.1:18200/health
./scripts/jobs.sh drain --caller-id default --since 20m --older-than 1m --json
./scripts/jobs.sh list --caller-id default --status failed --since 20m --limit 20
./scripts/jobs.sh stuck --caller-id default --since 20m --older-than 1m --limit 20
```

`pressure` 的 API log 扫描会按日志时间过滤到 `--since` 窗口，但仍只扫描 `--api-log-tail` 指定的尾部行数。连续压多档时，优先收窄 `--since` 或为每档保留独立日志，避免旧异常污染新档判断。

如果本地工具环境里 `./scripts/dev.sh restart api` 后 `status api` 显示 `stale`，用独立终端执行 `./scripts/dev.sh start api`，再继续压测；不要把这个工具进程保活问题当作 API 压测失败。

### 阶梯结果

| `MAX_ACTIVE_JOBS` | 命令后缀 | 请求数 | 失败数 | CSV RPS | p95 | 失败证据 | `/health` | 排空结果 | 判定 |
| ---: | --- | ---: | ---: | ---: | ---: | --- | --- | --- | --- |
| 1000 | `max1000-submit-u20-held` | 562 | 2 | 19.36 | 730ms | HTTP 500；`pressure` 命中 `http_5xx`、`api_log_db_connection_pressure`、`db_connection_pressure` | 200 | `drain --strict` 不通过，剩余 2 个 queued、2 个 failed，且出现 `published_dispatch_not_claimed` | 不安全 |
| 1000 | `max1000-submit-u20-net` | 773 | 773 | 26.60 | 3ms | Locust `Expecting value`，failures.csv 无 HTTP 状态码，DB 接单数不匹配 | down/stale | 本轮不是有效容量压测 | 无效运行 |
| 800 | `max800-submit-u20` | 723 | 2 | 25.05 | 570ms | HTTP 500；API 日志有 `TooManyConnectionsError` | 200 | 可恢复 | 不安全 |
| 775 | `max775-submit-u20` | 647 | 8 | 22.28 | 820ms | HTTP 500；API 日志有 `TooManyConnectionsError` | 200 | 短时出现 3 条 queued/pending，随后可排空 | 不安全 |
| 750 | `max750-submit-u20` | 753 | 0 | 26.02 | 490ms | 无 | 200 | 可排空 | 当前已测最高无失败档 |
| 700 | `max700-submit-u20` | 751 | 0 | 25.88 | 440ms | 无 | 200 | 可排空 | 稳定档 |
| 600 | `max600-submit-u20` | 784 | 91 | 27.03 | 460ms | HTTP 503，`active_jobs=600`，`limit=600` | 200 | 可排空 | 保护阈值生效 |

### 瓶颈结论

当前本地瓶颈不是 `job_test_echo` 任务本身，也不是 Locust 脚本异常，而是 `MAX_ACTIVE_JOBS` 放得过大后，`POST /jobs` / publish 路径会先打爆 PostgreSQL 连接预算。`20260626-max1000-submit-u20-held` 复测中，`scripts/jobs.sh pressure` 一次性给出以下关键证据：

```text
critical http      http_5xx
critical database  api_log_db_connection_pressure
critical execution job_failures
critical database  db_connection_pressure
warning lifecycle  window_not_terminal
```

失败档在客户端表现为 HTTP 500：

```text
{"code":"900500","msg":"internal error","data":null}
```

API 日志中的根因是：

```text
asyncpg.exceptions.TooManyConnectionsError: sorry, too many clients already
```

`MAX_ACTIVE_JOBS=600` 时失败主要是 503 容量保护，API 仍健康，说明保护阈值有效。继续提高到 `775`、`800` 或 `1000` 后出现 500，说明容量保护已经放得过大，DB 连接成为先坏掉的资源。`775` 压测结束后还短时出现 3 条 queued/pending，虽然后续可排空，但该档已经不能作为安全值。`1000` 复测时 `drain --strict` 明确不通过，窗口内有 failed Job，且仍有 active Job 需要继续排空，因此应停止升压。

`max1000-submit-u20-net` 这轮是工具环境下服务已 stale 后产生的无效压测：Locust 收到的不是 Job JSON，`pressure` 应报告 `http_failures_db_mismatch`，用于提示先查 API 存活、路径/前缀、认证和响应体。它不能作为容量上限证据；容量结论以 `max1000-submit-u20-held` 这轮服务保持运行的复测为准。

### 安全值判断

本轮历史已测最高 HTTP 无失败档：

```text
MAX_ACTIVE_JOBS=750
```

当前本地 `.env` 若配置为：

```text
MAX_ACTIVE_JOBS=1000
```

这表示当前配置正在验证更高上限，不表示它已经通过。按本轮复测结果，`1000` 在当前本地形态下不安全。如果要给长期本地开发或更接近生产的配置留余量，可以先用 `700`；历史最大通过档位是 `750`，但它也需要用当前 `pressure + drain --strict` 门槛复测后才能重新确认为安全档。继续提高 `MAX_ACTIVE_JOBS` 前，先调大或治理 PostgreSQL 连接容量、应用 DB pool、API 进程数和 worker 消费能力；否则 active Job 上限放大只会把 503 保护变成 500 内部错误。

本轮结果只用于当前本地形态的上界判断。若要用前文公式反推业务所需 `MAX_ACTIVE_JOBS`，需要另跑稳定长压并记录 accepted submit RPS、端到端 active 时长和 p95/p99，而不是直接把这张阶梯表当业务容量模型。

## 历史小容量样例

以下结果来自本地 `20260625-233343` 压测，仅用于说明低容量门禁如何表现：

```text
API: http://127.0.0.1:18200
DISABLE_HTTP_AUTH_HEADER=true
DISABLE_CALLER_ID_HEADER=true
MAX_ACTIVE_JOBS=50
WORKER_CONCURRENCY=1
LOAD_ECHO_SLEEP_SECONDS=15
```

`flow` 冒烟命令：

```bash
LOAD_SCENARIO=flow \
LOAD_ECHO_SLEEP_SECONDS=15 \
LOAD_POLL_INTERVAL_SECONDS=0.5 \
LOAD_FLOW_TIMEOUT_SECONDS=45 \
uv run --group load locust -f scripts/load/locustfile.py \
  --host http://127.0.0.1:18200 \
  --headless -u 4 -r 1 -t 60s \
  --csv .run/load/20260625-233343-max50-flow-smoke-u4 \
  --html .run/load/20260625-233343-max50-flow-smoke-u4.html
```

结果摘要：

| 指标 | 结果 |
| --- | ---: |
| `POST /jobs` | 16 requests, 0 failures, p95 140ms |
| `GET /jobs/{job_id}` | 449 requests, 0 failures, p95 13ms |
| `JOB flow terminal latency` | 12 samples, 0 failures, p95 16s |
| `queued/running/stuck` | 0 / 0 / 0 |

结论：发任务、轮询、终态统计链路正常；15 秒模拟任务的端到端观测约 16 秒，符合预期。

`submit` 稳定接单档：

```bash
LOAD_SCENARIO=submit \
LOAD_ECHO_SLEEP_SECONDS=15 \
LOAD_WAIT_MIN_SECONDS=1.5 \
LOAD_WAIT_MAX_SECONDS=2.0 \
uv run --group load locust -f scripts/load/locustfile.py \
  --host http://127.0.0.1:18200 \
  --headless -u 4 -r 2 -t 60s \
  --csv .run/load/20260625-233343-max50-submit-paced-u4 \
  --html .run/load/20260625-233343-max50-submit-paced-u4.html
```

结果摘要：

| 指标 | 结果 |
| --- | ---: |
| `POST /jobs` | 128 requests |
| 失败数 | 0 |
| 平均 RPS | 2.19 |
| p95 | 130ms |
| 压测后 `queued/running/stuck` | 0 / 0 / 0 |

结论：在 `MAX_ACTIVE_JOBS=50`、15 秒模拟任务下，约 2.19 req/s 的接单速率可以稳定完成并排空。

`submit` 容量触顶档：

```bash
LOAD_SCENARIO=submit \
LOAD_ECHO_SLEEP_SECONDS=15 \
uv run --group load locust -f scripts/load/locustfile.py \
  --host http://127.0.0.1:18200 \
  --headless -u 8 -r 4 -t 30s \
  --csv .run/load/20260625-233343-max50-submit-u8 \
  --html .run/load/20260625-233343-max50-submit-u8.html
```

结果摘要：

| 指标 | 结果 |
| --- | ---: |
| `POST /jobs` | 388 requests |
| 失败数 | 288 |
| 失败率 | 74.23% |
| 平均 RPS | 13.33 |
| p95 | 110ms |
| 主要失败原因 | HTTP 503, `active_jobs=50`, `limit=50` |
| 压测后 `queued/running/stuck` | 0 / 0 / 0 |
| 压测后 `/health` | 200 OK |

结论：当前本地服务在该 submit 压力下首先撞到 `MAX_ACTIVE_JOBS=50`，而不是 `POST /jobs` 序列化、DB 写入或 Taskiq publish 的尾延迟瓶颈。保护触发后 API 仍健康，Job 也能排空。

## 离线 HTML 报告

直接用浏览器打开 `.run/load/*.html` 通常可用。若本地预览器不执行报告脚本，启动静态服务：

```bash
python3 -m http.server 8765 --directory .run/load
```

浏览器打开对应报告：

```text
http://127.0.0.1:8765/
```

如果 HTML 的 Charts 区域没有曲线，先检查 `_stats_history.csv` 是否只有表头或只有一条 0 用户记录。压测太短时会有最终汇总表，但没有足够的历史采样点可画图。

## 收尾

```bash
./scripts/jobs.sh list --status running --since 30m --limit 20
./scripts/jobs.sh stuck --older-than 5m
```

本地服务不再需要时停止：

```bash
./scripts/dev.sh stop
```
