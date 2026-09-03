# Job 压测 Runbook

本文是 Job 压测的唯一操作手册：负责说明一次压测怎么跑、怎么选择 profile、怎么观察 dashboard、压后怎么判断结果。当前工具事实以 [`../../current/job-load-testing.md`](../../current/job-load-testing.md) 为准；Job 查询排障命令细节见 [`jobs使用与排障手册.md`](jobs使用与排障手册.md)；生产容量调优框架见 [`MAX_ACTIVE_JOBS 估算与生产调优.md`](MAX_ACTIVE_JOBS%20估算与生产调优.md)。

本文不维护 `load.sh` 的完整参数合同、不复制 dashboard data source 字段表、不记录某次临时压测报告。

## 先理解压测路径

压测 Job 服务时，不是只看一个 `POST /jobs` QPS。异步 Job 至少有三段：

```text
接单
  POST /jobs
  -> API / DB / Taskiq publish

执行
  Redis / Taskiq
  -> worker
  -> executor / workflow child

查询和收口
  GET /jobs/{job_id}
  -> terminal status
  -> root finalize / callback / billing projection
```

所以压测结论要同时看 Locust、dashboard 和 `jobs.sh` 证据。Locust 负责制造流量和记录 HTTP 观察值；dashboard 看服务侧趋势；`jobs.sh` 做压后诊断和样本定位。

## 选择 Profile

先用 `example_*` 建基线，确认模板链路稳定，再决定是否压真实业务。

| 目标 | 使用 profile |
|---|---|
| 普通单 Job 耗时 | `example-sleep` |
| root + 单 child | `example-workflow-single` |
| 串行步骤 | `example-workflow-chain` |
| 多个 child 并行 | `example-workflow-group` |
| 并行生成 + join/body 边界 | `example-workflow-chord` |
| 批量 item 展开 | `example-workflow-map` |
| 多参数展开 | `example-workflow-starmap` |
| 分块处理 | `example-workflow-chunks` |
| child 慢执行 | `example-workflow-chord-slow` |
| child 人为失败 | `example-workflow-chord-child-fail` |
| join 人为失败 | `example-workflow-chord-join-fail` |
| 终态等待超时压力 | `example-workflow-chord-timeout` |

`poster_title_image` 的当前编排形状接近：

```text
root job
  -> style_probe / generate_item children
  -> join
  -> root finalize
```

如果目标是模拟它的编排结构，优先使用：

```bash
./scripts/load.sh run --profile example-workflow-chord \
  --users 4 \
  --spawn-rate 1 \
  --time 60s \
  --run-id poster-title-image-shape-baseline
```

这只模拟 root/child/fan-out/join/body/root finalize 结构，不模拟 LLM、生图、OSS、真实 payload、图片数量、结果大小、真实汇总结果或模型供应商延迟，也不模拟 `poster_title_image` 里去重后的 `probe.*`、多个 `item.*` 以及 `join` 同时依赖 probe 和 item 的完整图结构。

如果目标是先确认失败和排障链路是否闭环，可以用示例故障 profile：

```bash
./scripts/load.sh run --profile example-workflow-chord-child-fail \
  --users 1 \
  --spawn-rate 1 \
  --time 20s \
  --run-id poster-title-image-shape-child-fail

./scripts/load.sh pressure --run-id poster-title-image-shape-child-fail
```

这类 profile 预期会让 Job 失败，作用是验证 failures、Job Trace、dashboard 和 `jobs.sh` handoff 是否能定位到失败节点，不表示服务压测失败。

真实压 `poster_title_image` 时，应使用业务 profile，并显式确认真实业务风险：

```bash
./scripts/load.sh run --profile .run/load/profiles/poster-title-image.json \
  --allow-real-job
```

## 本地基线流程

启动本地依赖、API 和 worker。需要同时看 dashboard 时，开启 dashboard：

```bash
OPS_DASHBOARD_ENABLED=true ./scripts/run.sh up dev
./scripts/run.sh status dev
```

`status` 会显示 API 和 dashboard URL。dashboard 默认路径：

```text
http://127.0.0.1:8100/internal/jobs-dashboard
```

查看当前 case 和 profile：

```bash
./scripts/load.sh cases
./scripts/load.sh profiles
```

先跑普通单 Job 基线：

```bash
./scripts/load.sh run --profile example-sleep \
  --users 4 \
  --spawn-rate 1 \
  --time 60s \
  --run-id example-sleep-baseline
```

再跑 workflow 编排基线：

```bash
./scripts/load.sh run --profile example-workflow-chord \
  --users 4 \
  --spawn-rate 1 \
  --time 60s \
  --run-id example-workflow-chord-baseline
```

如果需要实时看 Locust 页面，用 `ui`：

```bash
./scripts/load.sh ui --profile example-workflow-chord \
  --users 4 \
  --spawn-rate 1 \
  --time 60s \
  --run-id example-workflow-chord-ui
```

Locust UI 默认地址：

```text
http://127.0.0.1:8089
```

## 压测时观察什么

Locust 页面主要看：

| 视图 | 看什么 |
|---|---|
| Statistics | `POST /jobs`、`GET /jobs/{job_id}`、`JOB flow terminal latency` |
| Failures | HTTP 4xx/5xx、超时和响应错误 |
| Exceptions | Locust runner 自身异常 |

dashboard 主要看：

| tab | 看什么 |
|---|---|
| Overview | 当前健康、趋势、延迟、成功率、stuck 样本 |
| Recent Jobs | 本轮 Job 样本，必要时进入 Job Trace |
| Flow & Capacity | created/started/terminal 趋势、drain、容量、延迟、job_type 热点 |
| Failures & Callbacks | 失败聚合、失败样本、callback 是否异常 |
| Job Trace | 单个异常 Job 的 root/child/attempt/timeline 证据 |

如果本轮压测传了 `--run-id`，dashboard 顶部 `run_id` 也填同一个值。这样 Overview、Recent Jobs、Flow & Capacity、Failures & Callbacks 会只看本轮压测相关 Job，避免被历史数据干扰。

进入 Job Trace 后，优先看：

| 视图 | 看什么 |
|---|---|
| Load Summary | 这个 Job 是否来自目标 `run_id/profile/case` |
| Summary | root/child、状态、生命周期和错误摘要 |
| Workflow Summary | root/children/finalize 的编排结构是否符合预期 |
| Result Summary | 结果结构大小和关键字段是否符合预期 |
| Callback Summary | callback 是否 due、delivered、failed 或 dead_letter |
| Attempts / Timeline | worker 执行尝试和状态流转证据 |

本地压测还要看：

```bash
./scripts/run.sh status dev
tail -n 200 logs/api.log
tail -n 200 logs/worker.log
```

## 压后诊断

先看本轮 manifest 和 Locust 产物摘要：

```bash
./scripts/load.sh report --run-id example-workflow-chord-baseline
```

再从 manifest 透传压后诊断：

```bash
./scripts/load.sh pressure --run-id example-workflow-chord-baseline
./scripts/load.sh drain --run-id example-workflow-chord-baseline --strict
```

判断顺序：

```text
1. Locust 是否有 HTTP 失败或 runner exception
2. Job 终态成功率是否符合预期
3. JOB flow terminal latency 是否符合目标
4. drain 是否 drained
5. pressure 是否 critical
6. dashboard 是否显示 queued/running/stuck 持续增长
```

常见结论：

| 现象 | 下一步 |
|---|---|
| `pressure critical` | 不进入下一档，先处理失败、DB、worker、broker 或 callback 问题 |
| `drain drained` | 当前 scope 已排空，可以结合指标决定是否进入下一档 |
| `drain not_drained` | 不进入下一档，按输出的 next checks 查样本 |
| `POST /jobs` p95 高 | 查 DB 写入、幂等键、事务、Taskiq publish |
| `GET /jobs/{job_id}` p95 高 | 查 DB 读、索引、响应序列化和轮询频率 |
| HTTP 正常但 queued 增长 | 查 worker 并发、broker 消费和 Job 执行耗时 |
| running 长时间不下降 | 查 executor、worker timeout、外部依赖或 child failure |

需要展开 `jobs.sh` 明细时：

```bash
./scripts/jobs.sh gate
./scripts/jobs.sh capacity --since 20m --run-id <run_id>
./scripts/jobs.sh stuck --since 20m --older-than 1m --run-id <run_id> --limit 20
./scripts/jobs.sh failures --since 20m --run-id <run_id>
```

单个 Job 样本：

```bash
./scripts/jobs.sh trace <job_id>
./scripts/jobs.sh inspect <job_id>
./scripts/jobs.sh workflow <job_id>
```

## 升压顺序

不要一开始就用大并发。推荐顺序：

```text
1. example-sleep 小流量，确认 API/DB/worker/查询闭环
2. example-workflow-chord 小流量，确认 root/child/join/finalize 闭环
3. 固定 profile，逐步提高 --users
4. 再调整 --spawn-rate 和 --time
5. 每档都跑 report / pressure / drain
6. 只有 drained 且非 critical，才进入下一档
```

每档至少记录：

```text
被测 commit
profile / case / run_id
users / spawn-rate / time
POST /jobs 错误率和 p95/p99
GET /jobs/{job_id} 错误率和 p95/p99
JOB flow terminal latency p95/p99
Job 终态成功率
drain / pressure 结论
dashboard 异常样本
```

`run_id` 是本轮压测的主关联键。用 `load.sh pressure/drain/report --run-id <run_id>` 优先于手写 `jobs.sh` 参数；需要手工展开时，再把 `--run-id <run_id>` 带到 `jobs.sh list/failures/stuck/capacity/latency/ingress/callbacks-summary`。

`run_id` 只能使用字母、数字、下划线和中横线，例如 `poster-title-image-shape-baseline-1`。不要使用空格、斜杠、点号或 shell 特殊字符。

容量调优和 `MAX_ACTIVE_JOBS`、worker 执行槽位、API/worker Pod、PostgreSQL/Redis 的关系，不在本文重复展开；进入生产容量判断时看 [`MAX_ACTIVE_JOBS 估算与生产调优.md`](MAX_ACTIVE_JOBS%20估算与生产调优.md)。

## 查询接口压测

`job-query` 需要已有 Job ID，且这些 Job ID 必须属于同一个 caller。默认 `load.sh` caller 是 `load-cli`。

先准备 Job ID 文件，例如从本轮 load 产生的 Job 里取样：

```bash
./scripts/jobs.sh list --run-id <run_id> --since 10m --limit 20 --json
```

把要查询的 ID 写入：

```text
.run/load/query-job-ids.txt
```

再运行：

```bash
./scripts/load.sh run job-query \
  --query-job-ids-file .run/load/query-job-ids.txt \
  --users 20 \
  --spawn-rate 5 \
  --time 2m \
  --run-id job-query-baseline
```

少量 ID 可以用 `--query-job-ids` 逗号分隔，但长期压测优先用文件。

## 业务 Profile

生成业务 profile 模板：

```bash
./scripts/load.sh init poster-title-image \
  --job-type poster_title_image
```

编辑生成的 `.run/load/profiles/poster-title-image.json`，把 `job_params` 填成 `poster_title_image` 的真实有效 payload。下面只展示 profile 结构，不表示 `{}` 可以直接压真实业务：

```json
{
  "profile_version": 1,
  "key": "poster-title-image",
  "title": "poster-title-image",
  "job_type": "poster_title_image",
  "case": "job-flow",
  "job_params": {},
  "defaults": {
    "users": 4,
    "spawn_rate": 1.0,
    "time": "60s",
    "poll_interval_seconds": 0.5,
    "flow_timeout_seconds": 45.0,
    "wait_min_seconds": 0.1,
    "wait_max_seconds": 1.0
  }
}
```

先 dry-run 检查 manifest，不执行 Locust：

```bash
./scripts/load.sh run --profile .run/load/profiles/poster-title-image.json \
  --allow-real-job \
  --dry-run \
  --json
```

再真实执行：

```bash
./scripts/load.sh run --profile .run/load/profiles/poster-title-image.json \
  --allow-real-job \
  --users 2 \
  --spawn-rate 1 \
  --time 60s \
  --run-id poster-title-image-real-baseline
```

真实业务 profile 可能触发 LLM、对象存储、外部 HTTP、callback 和费用。不要用真实业务 profile 替代 `example_*` 基线；先证明服务自身链路稳定，再压真实业务。

## 失败时的最短路径

### HTTP 500

```bash
./scripts/load.sh pressure --run-id <run_id>
./scripts/jobs.sh list --run-id <run_id> --status failed --scope family --since 10m --limit 20
./scripts/jobs.sh inspect <job_id>
```

如果看到 DB 连接、连接池或 PostgreSQL 错误，先治理连接池、PostgreSQL 连接上限、API/worker 并发，不要继续升压。

### HTTP 503

```bash
./scripts/jobs.sh gate --max-active-jobs <当前值>
./scripts/load.sh pressure --run-id <run_id>
./scripts/load.sh drain --run-id <run_id> --strict
```

如果主要是 `QUEUE_FULL` / active gate 保护，且后台能排空、API/DB/Redis/worker 健康，可以按容量调优文小步调整 `MAX_ACTIVE_JOBS`。如果不能排空，先查 worker、DB、Redis 或外部依赖。

### workflow root 一直 running

```bash
./scripts/jobs.sh workflow <job_id>
./scripts/jobs.sh list --scope family --status queued,running --run-id <run_id> --limit 20
./scripts/jobs.sh diagnose <job_id> --include-children
```

如果有 failed child：

```bash
./scripts/jobs.sh inspect <child_job_id>
./scripts/jobs.sh attempts <child_job_id>
```

## compose-full 场景

`compose-full` 下 API 日志在容器内。需要把 API log 交给 `pressure --api-log` 时，先按 compose runbook 导出日志，再传路径：

```bash
docker compose logs --no-color api > .run/load/api.log

./scripts/jobs.sh pressure \
  --since 20m \
  --caller-id load-cli \
  --locust-prefix .run/load/<run_id>/locust \
  --api-log .run/load/api.log
```

compose-full 的启动、容器状态和日志操作见 [`compose-full-dev-operations.md`](../ops/compose-full-dev-operations.md)。
