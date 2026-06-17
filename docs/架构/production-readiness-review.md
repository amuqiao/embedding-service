# MVP Job 流程生产就绪性评审与调参指南

本文评审当前服务作为 MVP 上生产时，异步 Job 生命周期是否稳定、健壮、可恢复、可横向扩展，并说明上线后如何通过环境变量控制吞吐量和超时边界。

评审日期：2026-06-14

## 文档职责

本文聚焦一个问题：**这个 AI Job 服务能否以最小但可控的方式上生产**。

本文负责：

- 评审 API Pod、Celery Worker Pod 横向扩展和重启时，Job 生命周期是否稳定。
- 说明哪些环境变量控制吞吐量、积压、超时、恢复和 Callback 重试。
- 给出 MVP 推荐参数和常见调参方式，确保上线后优先通过“改 env + 重启服务”处理运行问题。
- 区分当前 MVP 必须具备的 Job 骨架能力，以及后续可按需增强的能力。

本文不追求完整生产平台清单，不把多租户、严格队列配额、全量告警平台、callback 域名 allowlist、分块 workflow 生产化、30+ 并发压测等能力作为当前 MVP 的前置阻塞项。

## 一、核心结论

**结论：当前服务具备“有条件 MVP 生产就绪”的 Job 流程骨架。**

当前代码已经形成稳定的异步 Job 生命周期闭环：

```text
调用方
  │
  │ POST /api/v1/ai-jobs/jobs
  ▼
FastAPI API
  ├─ Bearer token 鉴权
  ├─ 校验 job_type / job_params / runtime fields / callback
  ├─ client_request_id 幂等保护
  ├─ MAX_ACTIVE_JOBS 入口软背压
  ├─ 创建 ai_jobs(status=queued)
  ├─ 写入 celery_task_id 并提交 DB
  └─ apply_async 投递 jobs.dispatch，成功后记录 celery_published_at
        │
        ▼
Celery Worker
  ├─ 按 job_id + celery_task_id claim queued Job
  ├─ 规划或复用 execution_plan / work items
  ├─ 按 canvas pattern 投递 work item，并把 execution_generation 传给 finalize
  ├─ 执行模型调用
  ├─ 写回大文本 artifact
  ├─ CAS 标记 succeeded / failed
  └─ 投递终态 Callback

Worker recovery loop
  ├─ queued + celery_task_id IS NULL：补 task_id 并重投递
  ├─ queued + celery_task_id 非空 + celery_published_at IS NULL：替换 task_id 并重投递
  ├─ stale running：低于上限时整 Job 重投递；达到上限后 failed(JOB_TIMEOUT) 并补 Callback
  ├─ due callback：重试终态 Callback
  └─ expired settled jobs：清理已终态且 Callback 已 delivered/skipped 的过期记录
```

MVP 上线前必须确认的边界：

- 当前只服务单调用方 / 单信任域。
- API Pod 和 Worker Pod 共享同一组 PostgreSQL、Redis 和 OSS。
- 生产使用共享对象存储，例如 `STORAGE_BACKEND=aliyun_oss`。
- Worker 吞吐通过 `Worker Pod 数 × WORKER_CONCURRENCY` 控制。
- API 接单上限通过 `MAX_ACTIVE_JOBS` 控制。
- 模型调用、Celery、stale running 和 Callback 超时链按本文约束派生。
- 目标环境完成一次主链路 smoke/e2e、OSS 读写、Callback 验签、Worker 重启恢复验证。

不应在当前 MVP 承诺：

- 无限队列。
- 已验证 30+ 并发。
- 多调用方 / 多租户隔离。
- 分块 workflow 已按目标环境完成生产级验证。
- Redis 已发布消息极端丢失后自动恢复。
- 完整生产平台成熟度。

## 二、Job 骨架健壮性评审

| 生命周期环节 | 当前机制 | MVP 结论 |
| --- | --- | --- |
| 建单幂等 | `caller_id + client_request_id` 使用 PostgreSQL advisory lock；24 小时内返回已有 Job。 | 单调用方 MVP 可用。 |
| 入口背压 | `MAX_ACTIVE_JOBS` 使用 advisory lock 串行化 active count 检查 queued + running 总数，达到上限返回 `QUEUE_FULL`。 | 可控，但仍是接单保护，不是严格容量配额。 |
| 投递一致性 | API 先写 `celery_task_id` 并提交 DB，再投递 Celery，投递成功后写 `celery_published_at`。 | 覆盖“DB 已提交、Celery 未投递”的常见窗口。 |
| Worker 抢占 | Worker 校验 DB 中 `celery_task_id`，并用 `status='queued' AND celery_task_id=:task_id` 抢占。 | 支持多 Worker Pod 并发竞争。 |
| 终态写入 | succeeded / failed 写入要求 Job 仍为 running 且 task_id 匹配；finalize 前关键进度和成功前副作用要求当前 `task_id + execution_generation` claim。 | 避免旧 task 或旧代 canvas 覆盖当前终态。 |
| 模型超时 | `MODEL_CALL_TIMEOUT_SECONDS` 截断模型调用，Celery soft/hard time limit 兜底。 | 可通过 env 控制。 |
| Worker 崩溃 | Celery `acks_late` + `task_reject_on_worker_lost` 配合 running claim；stale running 由 recovery 按整 Job 重跑、提升 `execution_generation` 并最终收敛。 | 可恢复到明确终态。 |
| API 崩溃 | 已落库但未投递、未确认发布的 queued Job 由 recovery 重投递。 | 可恢复。 |
| Callback 失败 | 终态后立即尝试；失败后通过 `callback_next_retry_at` 和 recovery loop 补偿。 | 可控重试。 |
| 过期清理 | recovery loop 只清理 `expires_at <= now()`、已终态且 Callback 已 `delivered/skipped` 的 Job。 | MVP 可用，但必须保证排队 + 执行 + Callback 收敛小于 24 小时 TTL。 |

结论：Job 主流程骨架是稳定的。当前最大约束不是代码结构，而是上线时必须正确配置吞吐、超时、共享存储和目标环境容量。

## 三、横向扩展与重启稳定性

### API Pod

API Pod 不持有内存态，横向扩展的前提是共享 PostgreSQL、Redis 和对象存储。

API Pod 重启影响：

- 已提交 DB 的 Job 不会丢。
- API 在 commit 前崩溃，调用方应重试；如果带 `client_request_id`，重复提交会命中幂等。
- API 在 commit 后、Celery publish 前崩溃，recovery 会扫描 queued orphan / unpublished Job 并重投递。

API 侧主要调参：

```bash
DB_POOL_SIZE=5
DB_MAX_OVERFLOW=10
DB_POOL_RECYCLE=1800
MAX_ACTIVE_JOBS=5000
```

`MAX_ACTIVE_JOBS` 是生产接单上限，不是执行并发。小流量灰度可以按“总执行槽位 × 2~5”设置较小值；业务希望允许较深排队、且外部系统已有限流时，可以使用类似 `.env.example` 的 `5000` 作为生产初始目标。设为 `0` 表示禁用创建守卫，只应在调用方具备可靠外部限速时使用。

### Worker Pod

Worker Pod 是实际吞吐来源。总执行槽位为：

```text
总执行槽位 = Worker Pod 数 × WORKER_CONCURRENCY
```

Worker Pod 重启影响：

- 正在执行的任务可能被中断。
- Celery 会根据 `acks_late` / `task_reject_on_worker_lost` 让消息回队。
- 如果消息未能正常回队，DB 中长期 running 的 Job 会被派生值 `job_stale_running_seconds` 扫描出来；低于 `JOB_MAX_EXECUTION_ATTEMPTS` 时整体重新投递，达到上限后 failed 并补 Callback。
- Worker 启动后会立即跑一次 recovery，并启动周期 recovery loop。

Worker 侧主要调参：

```bash
WORKER_POOL=threads
WORKER_CONCURRENCY=4
WORKER_MAX_TASKS_PER_CHILD=100
```

生产滚动重启建议：

```text
terminationGracePeriodSeconds >= celery_time_limit + 60
```

如果派生后的 `celery_time_limit=960`，建议 `terminationGracePeriodSeconds >= 1020`。否则 Pod 被强杀太早，可能增加任务重跑和 stale running 的概率。

## 四、吞吐量控制模型

吞吐量由三个旋钮共同决定：

| 旋钮 | 环境变量 / 动作 | 控制对象 | 说明 |
| --- | --- | --- | --- |
| 执行槽位 | `Worker Pod 数 × WORKER_CONCURRENCY` | 同时执行的 Job 数 | 真正决定模型调用并发。 |
| 接单上限 | `MAX_ACTIVE_JOBS` | queued + running 总积压 | 达到上限后 API 返回 503。 |
| 上游容量 | DB 连接、Redis、模型额度、OSS 吞吐 | 实际可承载上限 | 扩 Worker 前必须核算。 |

连接数估算：

```text
峰值 DB 连接数
≈ API Pod 数 × (DB_POOL_SIZE + DB_MAX_OVERFLOW)
  + Worker Pod 数 × WORKER_CONCURRENCY
```

`MAX_ACTIVE_JOBS` 最小建议：

```text
MAX_ACTIVE_JOBS >= 总执行槽位 × 2
```

该公式只给出“不要卡住执行槽位”的下限。真正的生产值应同时考虑调用方可接受排队深度、Job TTL、数据库连接数、模型额度、Redis 内存和 Callback 接收能力。

示例：

```text
API Pod = 2
Worker Pod = 3
WORKER_CONCURRENCY = 4
总执行槽位 = 12
建议 MAX_ACTIVE_JOBS = 24 ~ 60
峰值 DB 连接 ≈ 2 × (5 + 10) + 3 × 4 = 42
```

## 五、关键环境变量

### 吞吐与积压

| 变量 | 推荐 MVP 初始值 | 作用 | 调整方式 |
| --- | --- | --- | --- |
| `WORKER_POOL` | `threads` | 开启单 Worker 多并发。 | 生产不要用 `solo`。 |
| `WORKER_CONCURRENCY` | `2~4` | 单 Worker Pod 同时执行 Job 数。 | 提高吞吐时调大；优先先加 Worker Pod。 |
| `MAX_ACTIVE_JOBS` | 小流量：`总执行槽位 × 2~5`；生产排队目标：可从 `5000` 起评估 | queued + running 上限。 | 队列太容易满时调大；系统压力大时调小；`0` 表示禁用检查。 |
| `JOB_RECOVERY_INTERVAL_SECONDS` | `60` | recovery loop 周期。 | 恢复要更快可调小，但会增加 DB 扫描频率。 |
| `JOB_RECOVERY_BATCH_SIZE` | `100` | 每轮恢复 orphan / stale 的批量。 | 积压大时可调大。 |
| `JOB_RECOVERY_CALLBACK_BATCH_SIZE` | `50` | 每轮 Callback 补偿批量。 | Callback 积压时可调大。 |
| `JOB_MAX_EXECUTION_ATTEMPTS` | `3` | stale running 整 Job 重跑最大次数。 | 提高会增加恢复容忍度，也会增加重复执行成本和外部副作用幂等压力。 |

### API 与数据库

| 变量 | 推荐 MVP 初始值 | 作用 | 注意 |
| --- | --- | --- | --- |
| `DB_POOL_SIZE` | `5` | API Pod 常驻连接池大小。 | API Pod 增多时要核算总连接数。 |
| `DB_MAX_OVERFLOW` | `10` | API Pod 连接池突增余量。 | 太大会放大 DB 压力。 |
| `DB_POOL_RECYCLE` | `1800` | 空闲连接回收秒数。 | 保留默认即可。 |
| `DATABASE_URL` | 目标 PostgreSQL | Job 状态源。 | API / Worker / migrate 必须指向同一库。 |
| `REDIS_URL` | 目标 Redis | Celery broker / result backend。 | API / Worker 必须共享。 |

### 超时链

当前真正接入执行路径的超时链由一个 env 锚点和三个代码常量 buffer 派生：

```text
MODEL_CALL_TIMEOUT_SECONDS
  + _CELERY_SOFT_TIMEOUT_BUFFER
  = celery_soft_time_limit

celery_soft_time_limit
  + _CELERY_HARD_TIMEOUT_BUFFER
  = celery_time_limit

celery_time_limit
  + _JOB_STALE_RUNNING_BUFFER
  = job_stale_running_seconds
```

用户只配置锚点 `MODEL_CALL_TIMEOUT_SECONDS`。三个 buffer 是 `app/core/config.py` 中的结构性安全常量，不是 `.env` 配置项；代码自动计算 Celery 和 recovery 使用的派生值。

注意：代码内置默认值偏向本地安全启动，例如 `MODEL_CALL_TIMEOUT_SECONDS` 默认是 `300` 秒；MVP 生产推荐通过 env 显式覆盖为下表值，`.env.example` 已按该推荐值给出模板。

| 变量 | 推荐 MVP 初始值 | 作用 | 联动要求 |
| --- | --- | --- | --- |
| `MODEL_CALL_TIMEOUT_SECONDS` | `600` | L1，模型调用主超时。 | 调大它后，后续派生值会自动跟随；只需确认 buffer 是否足够。 |
| `app/core/models.yaml` 的 `generation.num_retries` | `0` | 模型 SDK 内部重试次数。 | 默认保持 0，避免单次 Job 因 SDK 自动重试增加费用和耗时。 |
| `CELERY_MAX_RETRIES` | `0` | Celery 超时重试次数。 | 模型费用敏感时保持 0。 |
| `CELERY_RETRY_DELAY` | `60` | Celery 重试间隔。 | 仅 `CELERY_MAX_RETRIES > 0` 时有意义。 |

当前不暴露 queued 自动超时和全局 Job SLA 配置；相关能力如需上线，应先接入代码机制后再开放配置。

### Callback

| 变量 | 推荐 MVP 初始值 | 作用 | 联动要求 |
| --- | --- | --- | --- |
| `CALLBACK_SIGNING_SECRET` | 非空随机密钥 | HMAC 签名。 | 接收方必须验签。 |
| `ALLOW_INSECURE_CALLBACKS` | `false` | 是否允许本地 HTTP callback。 | 非本地环境必须关闭。 |
| `CALLBACK_TIMEOUT_SECONDS` | `5` | 单次 HTTP 请求超时。 | 接收端慢时调大。 |
| `CALLBACK_RETRY_DELAY_SECONDS` | `300` | 失败后的补偿重试间隔。 | 接收方不稳定时调大。 |
| `CALLBACK_MAX_DELIVERY_ATTEMPTS` | `12` | 最大投递次数。 | 调大表示更长时间补偿。 |

代码按 `CALLBACK_TIMEOUT_SECONDS + 内部 claim grace` 派生 `callback_delivery_timeout_seconds`，确保一次投递领取窗口覆盖 HTTP 请求和结果记录；启动时校验该窗口早于 `CALLBACK_RETRY_DELAY_SECONDS`。调用方和部署人员不需要配置内部领取窗口。

## 六、推荐 MVP 参数

### 小流量稳态

适合初次上线、人工观察和低并发业务。

```bash
WORKER_POOL=threads
WORKER_CONCURRENCY=2
MAX_ACTIVE_JOBS=20

MODEL_CALL_TIMEOUT_SECONDS=600

JOB_RECOVERY_INTERVAL_SECONDS=60
JOB_RECOVERY_BATCH_SIZE=100
JOB_RECOVERY_CALLBACK_BATCH_SIZE=50
JOB_MAX_EXECUTION_ATTEMPTS=3

CALLBACK_TIMEOUT_SECONDS=5
CALLBACK_RETRY_DELAY_SECONDS=300
CALLBACK_MAX_DELIVERY_ATTEMPTS=12
```

如果部署 2 个 Worker Pod：

```text
总执行槽位 = 2 × 2 = 4
建议 MAX_ACTIVE_JOBS = 20
```

该配置用于灰度观察和保护系统，不代表生产排队目标上限。若调用方希望允许更深排队，应先核算 24 小时 TTL、Callback 收敛时间和下游承载能力，再提高 `MAX_ACTIVE_JOBS`。

### 中等吞吐

适合业务确认稳定后放量。

```bash
WORKER_POOL=threads
WORKER_CONCURRENCY=4
MAX_ACTIVE_JOBS=50  # 灰度放量；生产排队目标可单独提高
```

如果部署 3 个 Worker Pod：

```text
总执行槽位 = 3 × 4 = 12
建议 MAX_ACTIVE_JOBS = 50
```

继续扩容前先确认：

- PostgreSQL 连接数够。
- Redis 内存和 broker 稳定。
- 模型 API 并发额度够。
- OSS 读写吞吐够。
- Callback 接收方能承受终态通知。

## 七、常见调参场景

| 场景 | 优先动作 | 说明 |
| --- | --- | --- |
| 想提高吞吐 | 增加 Worker Pod 数。 | 优先横向扩展，比盲目调大单 Pod 并发更稳。 |
| Worker Pod 已够但仍慢 | 提高 `WORKER_CONCURRENCY`。 | 从 2 到 4 起步；同步核算 DB、模型额度和内存。 |
| API 返回 `QUEUE_FULL` | 提高 `MAX_ACTIVE_JOBS` 或增加 Worker 执行槽位。 | `MAX_ACTIVE_JOBS` 只控制接单，不提高执行速度。 |
| 队列太深，想保护系统 | 降低 `MAX_ACTIVE_JOBS`。 | 让上游更早收到 503，避免无限积压。 |
| 模型经常超时 | 提高 `MODEL_CALL_TIMEOUT_SECONDS`。 | L3/L4/L5 会按代码常量 buffer 自动派生；无需直接调底层值。 |
| Worker 被误判 stale | 提高 `MODEL_CALL_TIMEOUT_SECONDS`，或调整代码内 `_JOB_STALE_RUNNING_BUFFER` 后走代码发布。 | L5 由 L1 和代码常量派生；生产不暴露单独 stale buffer env。 |
| Callback 接收端慢 | 提高 `CALLBACK_TIMEOUT_SECONDS`；如果启动校验提示重试间隔不足，再提高 `CALLBACK_RETRY_DELAY_SECONDS`。 | 内部领取窗口由代码自动派生，不作为生产旋钮暴露。 |
| Callback 接收端不稳定 | 提高 `CALLBACK_RETRY_DELAY_SECONDS` 或 `CALLBACK_MAX_DELIVERY_ATTEMPTS`。 | 降低对接收方的瞬时压力。 |
| Worker 重启后有 running 卡住 | 等待派生的 `job_stale_running_seconds` 后 recovery 收敛；确需缩短 stale 判断时应作为代码配置策略变更处理。 | 当前不提供临时调小 stale buffer 的 env 旋钮。 |
| 需要暂停放量 | 降低 Worker Pod 数或 `WORKER_CONCURRENCY`，同时降低 `MAX_ACTIVE_JOBS`。 | 让系统进入低吞吐保护模式。 |

## 八、上线前最小检查

| 检查项 | 要求 |
| --- | --- |
| 单调用方边界 | 当前 MVP 只允许一个调用方 / 信任域；多业务方共享服务前必须改鉴权和 `caller_id`。 |
| 共享存储 | 生产必须使用 `STORAGE_BACKEND=aliyun_oss` 或等价共享存储。 |
| OSS 连通性 | 目标环境完成 OSS `PUT` / `GET` / `HEAD` 或等价读写校验。 |
| DB / Redis | API、Worker、migrate 指向同一 PostgreSQL / Redis；连接数预算通过。 |
| 超时链 | L1 锚点和 L3/L4/L5 buffer 配置有效，派生后的 L1 < L3 < L4 < L5。 |
| Callback | `CALLBACK_SIGNING_SECRET` 非空，接收方验签；非本地环境 `ALLOW_INSECURE_CALLBACKS=false`。 |
| 探针 | API readiness 使用 `/healthz`；API liveness 使用 `/health`；Worker 使用 `check-worker-health.sh` 或等价命令。 |
| 重启验证 | 至少验证一次 Worker Pod 重启后 Job 能进入终态或由 recovery 收敛。 |
| TTL 边界 | 最长排队 + 最长执行 + Callback 收敛显著小于 24 小时 Job TTL。 |

## 九、当前 MVP 不做但可按需扩展

以下能力不阻塞当前 MVP Job 流程上线，触发对应业务场景时再单独立项：

| 场景 | 后续扩展 |
| --- | --- |
| 多业务方 / 多租户 | 多 token、真实 `caller_id`、查询隔离、审计日志。 |
| 严格容量配额 | 将 `MAX_ACTIVE_JOBS` 从软限制改为 DB 行锁、配额表或 Redis 原子计数。 |
| 极端 broker 丢消息自动恢复 | 增加 `celery_published_at IS NOT NULL` 且长期 queued 的补偿扫描。 |
| 长时间深队列 | 调整 TTL 和清理策略，只清理终态 Job。 |
| Callback 域名安全策略 | 增加 callback domain allowlist 或 DNS 解析后私网拦截。 |
| 分块 workflow 上生产 | 将对应 `WorkflowHandler.chunking_enabled=true` 作为独立上线项做恢复、成本和回归验证。 |
| 30+ 并发承诺 | 建立目标环境压测脚本、容量模型、成功率和 P95/P99 报告。 |

## 十、验证入口

### 基线检查

```bash
./scripts/verify.sh check
```

### 本地主链路

```bash
./scripts/dev.sh start
curl -fsS http://127.0.0.1:8100/health
curl -fsS http://127.0.0.1:8100/healthz
./scripts/verify.sh smoke
./scripts/dev.sh stop
```

### 本地真实模型和 Callback 自测

该入口会启动 `http://127.0.0.1:<port>` callback receiver，只适合本地开发或明确设置 `ALLOW_INSECURE_CALLBACKS=true` 的 dev 环境，不能替代目标环境 HTTPS Callback 验签。

```bash
./scripts/dev.sh start
./scripts/verify.sh e2e --input-file .data/test_novel.txt
./scripts/dev.sh stop
```

### OSS 连通性

```bash
./scripts/verify.sh oss --env-file .env.dev
```
