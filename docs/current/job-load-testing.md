# Job 压测当前事实

本文只记录当前已经落地的 Job 压测能力、入口合同、内置 case/profile、产物和指标语义。一次压测怎么执行、怎么观察 dashboard、怎么判断是否进入下一档，见 [`../runbooks/job/job-load-testing-runbook.md`](../runbooks/job/job-load-testing-runbook.md)。

本文不维护压测操作流水账、生产容量采购结论、Grafana/k6/JMeter 平台方案或真实模型供应商压测结果。

## 当前边界

稳定入口是 `./scripts/load.sh`。`scripts/load/locustfile.py` 是内部 Locust runner，不作为用户直接调用合同。

当前压测覆盖的是本服务的 Job 创建、查询和异步完成链路：

```text
POST /api/v1/ai-jobs/jobs
GET  /api/v1/ai-jobs/jobs/{job_id}
```

默认接口前缀来自 `SERVICE_API_PREFIX`，当前默认是 `/api/v1/ai-jobs`。`load.sh` 默认读取仓库根目录 `.env`，运行时环境变量优先；`API_URL` 优先于 `API_HOST` / `API_PORT`。

`load.sh` 使用 Locust 作为负载发生器。当前没有独立压测结果数据库，也没有接入 Grafana/k6/JMeter 这类统一压测平台。仓库内的 `ops_dashboard` 是 Job 运维只读 dashboard，不是压测结果平台；它的当前事实见 [`ops-dashboard.md`](ops-dashboard.md)。

## Case 与 Profile

`case` 表达压哪条链路，`profile` 表达用哪个 `job_type` 和默认 `job_params`。新增业务 Job 时，优先新增 profile，不修改 Locust runner。

当前 case 由 `scripts/load/cases.py` 注册，并可通过 `./scripts/load.sh cases --json` 机器读取；命令输出是 case 清单事实源，本文只保留分类摘要：

| case | 默认 job_type | 作用 |
|---|---|---|
| `job-submit` | `example_sleep` | 只创建 Job，观察 API、DB 写入、幂等键和 Taskiq publish |
| `job-query` | 无 | 只查询已有 Job，观察查询接口、DB 读、索引和响应序列化 |
| `job-flow` | `example_sleep` | 创建 Job 并轮询到终态，观察完整 Job 生命周期 |
| `workflow-flow` | `example_workflow` | 创建 workflow demo Job 并轮询到终态，观察 root/child/finalize 链路 |
| `api-health` | 无 | 压 `/health` 基础 HTTP 路径 |

当前内置 profile 由 `scripts/load/profiles.py` 注册，并可通过 `./scripts/load.sh profiles --json` 机器读取；命令输出是 profile 清单事实源，本文只保留当前内置 profile 摘要：

| profile | job_type | case | 作用 |
|---|---|---|---|
| `example-sleep` | `example_sleep` | `job-flow` | 普通单 Job 低成本压测 |
| `example-workflow-single` | `example_workflow` | `workflow-flow` | root + 单 child |
| `example-workflow-chain` | `example_workflow` | `workflow-flow` | 串行 child |
| `example-workflow-group` | `example_workflow` | `workflow-flow` | 并行 child |
| `example-workflow-chord` | `example_workflow` | `workflow-flow` | 并行 child 完成后执行 join/body |
| `example-workflow-map` | `example_workflow` | `workflow-flow` | item 列表展开 |
| `example-workflow-starmap` | `example_workflow` | `workflow-flow` | 多参数 item 解包展开 |
| `example-workflow-chunks` | `example_workflow` | `workflow-flow` | 长列表分块 |
| `example-workflow-chord-slow` | `example_workflow` | `workflow-flow` | 并行 child + join，child 人为变慢 |
| `example-workflow-chord-child-fail` | `example_workflow` | `workflow-flow` | 并行 child + join，child 人为失败 |
| `example-workflow-chord-join-fail` | `example_workflow` | `workflow-flow` | 并行 child + join，join 节点人为失败 |
| `example-workflow-chord-timeout` | `example_workflow` | `workflow-flow` | 并行 child + join，整体终态等待超时压力 |

`example_*` 都是 `visibility="demo"` 的低成本示例类型，不调用真实模型、不访问对象存储、不发起外部 HTTP。正式业务 `job_type` 需要通过业务 profile 接入同一 runner，并显式传 `--allow-real-job`。

内置示例 `job_type` 支持用于压测的低成本模拟参数：

| 参数 | 适用范围 | 含义 |
|---|---|---|
| `sleep_seconds` | `example_sleep`、`example_pair`、`example_collect`、`example_workflow` | 模拟执行耗时，当前上限 600 秒 |
| `fail` | `example_sleep`、`example_pair`、`example_collect` | 人为让该节点失败 |
| `fail_after_seconds` | `example_sleep`、`example_pair`、`example_collect`、`example_workflow` | 失败前先等待一段时间 |
| `result_size_bytes` | `example_sleep`、`example_workflow` | 让 `example_sleep` 结果携带指定大小的字符串 payload |
| `fail_node_key` | `example_workflow` | 指定 workflow 中哪个 node 人为失败 |

这些参数只用于示例 Job 的形状、耗时、结果大小和失败注入，不模拟 LLM、对象存储、外部 HTTP 或真实业务副作用。

## Profile 合同

JSON profile 顶层字段：

| 字段 | 含义 |
|---|---|
| `profile_version` | profile schema 版本 |
| `key` | profile key |
| `title` | 人读标题 |
| `job_type` | 压测目标 `job_type` |
| `case` | 默认 case |
| `job_params` | 该 `job_type` 的默认参数对象 |
| `defaults` | 压测默认参数对象 |

`defaults` 当前支持：

```text
users
spawn_rate
time
poll_interval_seconds
flow_timeout_seconds
wait_min_seconds
wait_max_seconds
```

profile 只保存压测对象和默认参数，不保存真实业务执行确认。非 `example_*` 的 `job_type` 每次运行都必须显式传 `--allow-real-job`。

## Manifest 与产物

每轮压测产物固定写入 `.run/load/<run_id>/`：

```text
.run/load/<run_id>/manifest.json
.run/load/<run_id>/locust_stats.csv
.run/load/<run_id>/locust_failures.csv
.run/load/<run_id>/locust_exceptions.csv
.run/load/<run_id>/report.html
```

manifest 是压测合同的机器可读投影，包含 case、profile、`job_type`、风险确认、caller、API URL、Locust 命令、输出路径和执行状态。manifest 只记录 `job_params_source` 和 profile 的 `job_params_present`，不打印完整业务 payload。

`SERVICE_API_KEY` 只通过环境传给 Locust，不写入 manifest。不建议使用 `--service-api-key` 传密钥，因为它会出现在 shell history、`ps` 或 CI 命令日志中。

## Run ID Traceability

`load.sh run` 会把本轮压测身份写入每个创建出来的 Job metadata：

| metadata 字段 | 含义 |
|---|---|
| `source` | 固定为 `scripts/load.sh` |
| `run_id` | 本轮压测 ID，对应 `.run/load/<run_id>/` |
| `profile` | profile key |
| `case_key` | case key |
| `sequence` | Locust user 内提交序号 |

`run_id` 是压测、dashboard 和 `jobs.sh` 之间的稳定关联键。`load.sh pressure --run-id <run_id>` 和 `load.sh drain --run-id <run_id>` 会读取 manifest，并把同一个 `run_id` 透传给 `jobs.sh`，确保压后诊断只看本轮压测 Job family。

`jobs.sh` 的 `list/drain/pressure/stuck/failures/summary/doctor/callbacks-summary/ingress/latency/capacity` 支持 `--run-id` 过滤。dashboard 的全局过滤也支持 `run_id`，用于把 Overview、Recent Jobs、Flow & Capacity、Failures & Callbacks 收敛到某轮压测。

`run_id` 必须匹配：

```text
[A-Za-z0-9][A-Za-z0-9_-]{0,127}
```

也就是只能使用字母、数字、下划线和中横线，且不能以分隔符开头。这个约束让 `run_id` 可以同时安全用作产物目录名、Job metadata 关联键和 CLI handoff 参数。

## 安全确认

当前安全边界：

| 场景 | 要求 |
|---|---|
| 本机 API | 默认允许 |
| 非本机 API | 必须传 `--allow-remote-api` |
| `example_*` job_type | 默认允许，仍只建议用于 `local/dev` |
| 非 `example_*` job_type | 必须传 `--allow-real-job` |
| inline JSON 参数 | 支持 `--job-params-json`，但高风险；优先用 `--job-params-json-file` |

`APP_ENV=test/prd` 的外部提交准入仍由 Job 服务本身控制：外部只允许提交 `visibility="public"` 的 `job_type`。

## 指标语义

Locust 页面和 CSV 中的 `RPS` 表示每秒 HTTP 请求数。Job 服务是异步系统，`POST /jobs` RPS 不等于 Job 完成吞吐。

| 指标 | 当前含义 |
|---|---|
| `POST /jobs` RPS / p95 / p99 | 接单 HTTP 吞吐和延迟 |
| `GET /jobs/{job_id}` RPS / p95 / p99 | 查询轮询 HTTP 吞吐和延迟 |
| `JOB flow terminal latency` | `job-flow` / `workflow-flow` 中，从创建 Job 到轮询到终态的端到端耗时 |
| Job 终态成功率 | 被压测 Job 是否按预期进入 `succeeded` |
| queued / running / stuck | 服务侧积压和卡住证据，不由 Locust 单独判断 |

压测结论不能只看 HTTP 指标。有效判断至少需要同时看 HTTP 错误率、HTTP p95/p99、Job 终态成功率、`JOB flow terminal latency` 和压测停止后的 queued/running/stuck 是否恢复。

## Dashboard 观测边界

压测期间可以用 `ops_dashboard` 的 `run_id` 全局过滤观察本轮压测 Job。Dashboard 的 data source、过滤和 Job Trace 展示边界以 [`ops-dashboard.md`](ops-dashboard.md) 为准；本文不重复维护 dashboard data source 清单。

Redis broker、Pod runtime 和对象存储内容仍由 `scripts/jobs.sh` 的只读命令承担。

## 验证

压测入口和合同由以下验证覆盖：

```bash
./scripts/load.sh --help
./scripts/load.sh cases --json
./scripts/load.sh profiles --json
./scripts/verify.sh check
```

相关测试：

- `tests/test_load_cli.py`
- `tests/test_example_catalog.py`
- `tests/test_workflow_compiler.py`
- `tests/test_job_workflow.py`
