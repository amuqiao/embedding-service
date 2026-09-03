# MAX_ACTIVE_JOBS 估算与生产调优

本文说明生产环境中如何估算 `MAX_ACTIVE_JOBS`，以及在 K8s 上遇到瓶颈时应优先调 `MAX_ACTIVE_JOBS`、worker 执行槽位、worker Pod 数量，还是 PostgreSQL/Redis 容量。

本文不是生产部署规范，也不替代压测报告。本文负责把单 API、单 worker 的压测方法扩展成生产调优判断框架。

如果目标是执行压测、选择内置 `example-*` profile、模拟 `poster_title_image` 编排结构或压后观察 dashboard，先看 [`job-load-testing-runbook.md`](job-load-testing-runbook.md)。本文只负责容量阈值和生产调优判断。

## 核心模型

`MAX_ACTIVE_JOBS` 是接单保护阈值，不是吞吐配置。它限制的是系统里同时处于 active 状态的 Job 数量。

```text
active jobs ~= queued jobs + running jobs with active attempt

active jobs >= MAX_ACTIVE_JOBS
  -> POST /jobs 返回 503

active jobs < MAX_ACTIVE_JOBS
  -> 接收 Job，写 DB，发布 Taskiq attempt
```

实现上，`MAX_ACTIVE_JOBS` 是全库全局计数，不按 caller、`job_type`、API Pod 或 worker Pod 分组。`running` 只有在 `active_attempt_id IS NOT NULL` 时才计入；编排型 root Job 如果处于等待子任务状态但没有 active attempt，不会占用 active 水位。`MAX_ACTIVE_JOBS=0` 表示跳过这道门禁。

估算业务需要的 active 容量：

```text
active_jobs_needed ~= accepted_submit_rps * p95_job_active_seconds
```

其中：

| 变量 | 含义 | 主要受什么影响 |
| --- | --- | --- |
| `accepted_submit_rps` | 每秒真正成功接收多少 Job | API Pod 数、入口流量、DB 写入、Redis/Taskiq publish、限流策略 |
| `p95_job_active_seconds` | Job 从进入 active 到终态的 p95 时长 | queued 等待、worker 并发、Job 执行耗时、CPU/内存、外部模型、DB/Redis |

CPU、内存、Pod 资源、worker 执行槽位、外部模型耗时，大多会反映到 `p95_job_active_seconds` 上。但 PostgreSQL 连接数、API 连接池、Redis/broker 容量也可能先形成环境硬上限，导致 HTTP 500、超时或进程重启。这部分不能只靠公式算，必须靠阶梯压测验证。

当前 submit 门禁会通过 PostgreSQL advisory lock 做全局 active count 保护。也就是说，当 `MAX_ACTIVE_JOBS > 0` 时，`POST /jobs` 的部分路径不是简单随 API Pod 数线性扩展；API Pod 加多后，可能先放大 PostgreSQL 锁竞争和连接竞争。

最终建议值应同时满足业务需要和环境安全上限：

```text
业务所需安全值 = ceil(active_jobs_needed * 1.2)
环境安全上限 = floor(环境硬上限 * 0.7)

建议 MAX_ACTIVE_JOBS = min(业务所需安全值, 环境安全上限)
```

## 生产调优总顺序

生产上不要先猜旋钮。先按现象分类，再一次只改一个变量。

```text
1. 看错误类型
   503 QUEUE_FULL / 500 / timeout / OOM / restart

2. 看 Job 状态
   queued 是否持续增长
   running 是否长期不下降
   stuck 是否增加

3. 看组件指标
   API CPU/内存/p95/错误率
   worker CPU/内存/执行耗时
   PostgreSQL 连接数/CPU/锁/慢查询
   Redis CPU/内存/队列延迟

4. 只调一个旋钮
   MAX_ACTIVE_JOBS
   WORKER_PROCESSES
   WORKER_MAX_ASYNC_TASKS
   WORKER_MAX_PREFETCH
   API replicas
   worker replicas
   Pod CPU/内存
   DB/Redis 容量或连接池

5. 用同一条压测命令复测
```

## 什么时候调哪个

| 现象 | 优先判断 | 该调什么 | 不该先调什么 |
| --- | --- | --- | --- |
| `POST /jobs` 返回 503，响应体包含 `active_jobs` 和 `limit`，API/DB/Redis 健康，压测后可排空 | `MAX_ACTIVE_JOBS` 保护先触发 | 如果业务需要更大接单窗口，逐步增大 `MAX_ACTIVE_JOBS` | 不要直接加 worker；此时 worker 未必是瓶颈 |
| `POST /jobs` 503，同时 queued 持续增长、排空很慢 | 接单速度大于完成速度 | 先增大 worker 消费能力：worker 执行槽位、worker Pod 数、Job 执行优化 | 不要只增大 `MAX_ACTIVE_JOBS`，否则只是允许更多积压 |
| `POST /jobs` p95 升高或出现 500 | API、DB 写入、全局 active gate 锁竞争或 Taskiq publish 路径瓶颈 | 看 API CPU、PostgreSQL 连接数、锁等待、DB p95、Redis publish；按瓶颈扩 API/DB/Redis | 不要先增大 `MAX_ACTIVE_JOBS` |
| `GET /jobs/{job_id}` p95 升高 | 查询接口或 DB 读瓶颈 | 扩 API 读能力、优化 DB 索引/连接池、降低轮询频率 | 不要调 `MAX_ACTIVE_JOBS` |
| queued 增长，API `POST` p95 正常 | worker/broker 消费跟不上 | 增大 worker Pod 数或 worker 执行槽位，检查 Redis/Taskiq lag | 不要只扩 API Pod |
| running 长时间不下降，Job active p95 变长 | Job 执行慢或外部依赖慢 | 优化 executor、外部模型并发/超时、worker 资源；IO 等待型可提高 `WORKER_MAX_ASYNC_TASKS` | 不要先增大 `MAX_ACTIVE_JOBS` |
| worker CPU 接近打满 | CPU 型 Job 或 worker CPU 不足 | 增大 worker Pod CPU 或 worker Pod 数；CPU 型任务优先横向扩 Pod | 不要盲目提高 `WORKER_MAX_ASYNC_TASKS` |
| worker 内存接近限制或 OOM | 单 Job 内存高或并发过高 | 增大 worker 内存、降低 worker 执行槽位、拆小任务 | 不要增大 `WORKER_MAX_ASYNC_TASKS` |
| API CPU 打满，DB/Redis 正常，且不是 submit 锁竞争 | API Pod 数或 CPU 不足 | 增大 API Pod CPU 或 API replicas | 不要先扩 worker |
| PostgreSQL `TooManyConnectionsError`、连接数打满 | DB 连接预算先到顶 | 治理连接池、降低 API/worker 总连接数、引入 PgBouncer、提高 DB 连接上限 | 不要增大 `MAX_ACTIVE_JOBS` 或 API/worker Pod 数 |
| PostgreSQL CPU 打满、慢查询增加 | DB 执行能力不足 | 优化查询/索引、提高 DB CPU、降低轮询、拆读写压力 | 不要只加 API Pod |
| Redis CPU/内存或队列延迟升高 | broker 成为瓶颈 | 提高 Redis 规格、降低 publish/claim 压力、检查 Taskiq 消费 | 不要只加 API 或 worker |

## K8s 旋钮之间的关系

### MAX_ACTIVE_JOBS

作用：控制全局接单水位。

```text
提高 MAX_ACTIVE_JOBS
  -> 允许更多 queued/running Job
  -> 给突发流量更大缓冲
  -> 同时增加 DB、Redis、worker 的积压压力
```

适合调大的前提：

- 失败主要是可预期的 503 容量保护。
- API、PostgreSQL、Redis、worker 都健康。
- 压测停止后 queued/running 能按预期下降。
- 业务确实需要更大的排队窗口。

不适合调大的情况：

- 已经出现 HTTP 500、连接错误、OOM、Pod restart。
- queued 长时间不下降。
- PostgreSQL/Redis 已经接近上限。
- Job active p95 已经超过业务 SLO。

### Worker 执行槽位

作用：提高单个 worker Pod 内的并发执行能力。

```text
单 Pod 执行槽位 = WORKER_PROCESSES * WORKER_MAX_ASYNC_TASKS

提高 WORKER_PROCESSES
  -> 增加 Taskiq 子进程数
  -> 进程隔离和多核利用更直接

提高 WORKER_MAX_ASYNC_TASKS
  -> 每个 Taskiq 子进程同时执行更多 async task
  -> IO 等待型 Job 的吞吐可能提高

提高 WORKER_MAX_PREFETCH
  -> 每个 Taskiq 子进程最多预取更多 task
  -> 影响 broker 消息滞留和消费公平性
  -> 不等于真实执行并发

提高 worker 执行槽位
  -> 单 Pod 同时执行更多 Job
  -> queued 可能下降
  -> CPU、内存、DB/Redis/外部模型连接压力增加
```

适合调大的前提：

- queued 增长，但 worker CPU/内存还有余量。
- Job 主要是 IO 等待、外部模型等待、网络等待。
- PostgreSQL、Redis、外部模型和 worker 侧连接占用允许更高并发。

不适合调大的情况：

- worker CPU 已经接近打满。
- worker 内存紧张或 OOM。
- 真实 Job 是 CPU 密集型。
- 外部模型或数据库已经限流。

注意：worker 执行路径的 DB 使用方式不等同于 API 请求路径的固定 SQLAlchemy pool。提高 `WORKER_PROCESSES` 或 `WORKER_MAX_ASYNC_TASKS` 时，不能只看 API 的 `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` 是否够用，还要看 PostgreSQL 实际连接数、连接创建频率和 worker 执行期间的 DB 写入压力。

### worker Pod 数量

作用：横向增加 Job 消费能力。

```text
增加 worker replicas
  -> 集群总消费能力提高
  -> 每个 Pod 资源隔离更清楚
  -> DB/Redis/外部服务连接数同步增加
```

适合调大的前提：

- queued 持续增长。
- 单 Pod CPU 或内存已接近合理上限。
- 需要更稳定的横向扩展，而不是把单 Pod 并发堆得很高。

对 CPU 密集型 Job，优先增加 worker Pod 数或 CPU request/limit；对 IO 等待型 Job，可以先小步提高 `WORKER_MAX_ASYNC_TASKS`，再扩 Pod。

### API Pod 数量

作用：提高 HTTP 接单和查询能力。

```text
增加 API replicas
  -> POST /jobs 和 GET /jobs/{job_id} 并发能力提高
  -> 每个 API Pod 都可能增加 DB 连接需求
  -> submit 路径可能遇到全局 active gate 锁竞争
  -> 如果 DB 或锁竞争已经是瓶颈，扩 API 会更糟
```

适合调大的前提：

- API CPU 打满或 API p95 升高。
- DB/Redis 仍健康。
- `GET /jobs/{job_id}` 或非锁竞争的 API 处理成为瓶颈，而不是 worker 消费。
- `POST /jobs` 慢时，已确认不是 PostgreSQL 连接、慢查询、锁等待或 Taskiq publish 瓶颈。

不适合调大的情况：

- PostgreSQL 连接数已满。
- `POST /jobs` 已经出现 DB 相关 500。
- submit 路径已经出现 PostgreSQL advisory lock 竞争。
- queued 增长但 API p95 正常；这通常是 worker 消费问题。

### Pod CPU 和内存

作用：提高单 Pod 的稳定执行空间。

```text
CPU 不足
  -> p95/p99 升高
  -> worker 执行变慢
  -> active 时间变长

内存不足
  -> GC/换页/容器 OOM
  -> running/stuck 增加
  -> 进程重启或任务失败
```

CPU 和内存不会自动改变 `MAX_ACTIVE_JOBS`，但会改变环境硬上限和 `p95_job_active_seconds`。资源不足时，先修资源瓶颈，再重新估算 `MAX_ACTIVE_JOBS`。

### PostgreSQL

PostgreSQL 是 `MAX_ACTIVE_JOBS` 调优里最容易被误伤的组件。

```text
更多 API Pod
更多 worker Pod
更高 worker 执行槽位
更高 MAX_ACTIVE_JOBS
更高轮询 RPS
  -> 都可能增加 PostgreSQL 连接和查询压力
```

如果出现以下现象，优先把它判定为 DB 容量或连接治理问题：

- `TooManyConnectionsError`
- PostgreSQL `max_connections` 接近上限
- DB CPU 高
- lock wait 增加
- 慢查询增加
- `POST /jobs` 或 `GET /jobs/{job_id}` p95 同时升高

此时不要继续放大 `MAX_ACTIVE_JOBS`。先处理：

- 应用 DB pool 上限。
- API Pod 数、worker Pod 数和 worker 执行槽位带来的总连接压力。
- worker 侧连接创建频率和任务执行期间的 DB 写入压力。
- PostgreSQL `max_connections` 与实例规格是否匹配。
- 是否需要 PgBouncer。
- Job 查询和轮询是否过密。

## 生产排障决策图

```text
压测或生产告警
  |
  v
是否大量 503 QUEUE_FULL?
  |
  +-- 是 --> API/DB/Redis/worker 是否健康且可排空?
  |          |
  |          +-- 是 --> 可以小步提高 MAX_ACTIVE_JOBS，复测
  |          |
  |          +-- 否 --> 先处理排空瓶颈：worker、DB、Redis、外部依赖
  |
  +-- 否 --> 是否有 500/timeout/restart/OOM?
             |
             +-- 是 --> 不调 MAX_ACTIVE_JOBS，先查硬瓶颈
             |          |
             |          +-- DB 连接/慢查询 --> 治理 PostgreSQL 和连接池
             |          +-- API CPU 高 --> 扩 API CPU/replicas
             |          +-- worker CPU/内存高 --> 扩 worker 资源或 Pod
             |          +-- Redis/broker lag --> 治理 Redis/Taskiq
             |
             +-- 否 --> 看 queued/running
                        |
                        +-- queued 增长 --> 扩 worker 消费能力
                        +-- running 变慢 --> 优化 Job 执行或外部依赖
                        +-- GET p95 高 --> 优化查询路径或轮询策略
```

## 生产估算步骤

### 1. 先测业务真实 active 时长

用接近真实业务的 `flow` 压测或生产观测，记录：

```text
accepted_submit_rps
JOB flow terminal latency p95/p99
queued 等待 p95
running 执行 p95
Job 终态成功率
```

如果只有 `POST /jobs` RPS，没有 Job 终态耗时，就不能估算 `MAX_ACTIVE_JOBS`。

### 2. 算业务需要值

示例：

```text
accepted_submit_rps = 20
p95_job_active_seconds = 40

active_jobs_needed = 20 * 40 = 800
业务所需安全值 = ceil(800 * 1.2) = 960
```

这表示业务希望系统至少能安全容纳约 `960` 个 active Job。

### 3. 找环境硬上限

固定业务 payload 和压测命令，只逐步提高 `MAX_ACTIVE_JOBS`。生产阶梯不要照抄本地 `750` 这一类数字，应从当前生产默认值或业务估算值附近小步探测。

```text
示例:
当前值 -> 当前值 * 1.2 -> 当前值 * 1.5 -> 当前值 * 2

或:
业务估算值 * 0.5 -> 业务估算值 * 0.75 -> 业务估算值 -> 业务估算值 * 1.25
```

每档都要求：

```text
API health 正常
POST/GET 错误率可接受
没有 500 / OOM / restart
PostgreSQL/Redis 未触顶
压测停止后 queued/running/stuck 可恢复
```

最后一个满足条件的档位是环境硬上限。

### 4. 取更保守的值

示例：

```text
业务所需安全值 = 960
环境硬上限 = 1200
环境安全上限 = floor(1200 * 0.7) = 840

建议 MAX_ACTIVE_JOBS = min(960, 840) = 840
```

这表示业务想要 `960`，但当前环境只建议放到 `840`。继续提高前，应扩容或治理瓶颈，而不是强行放大 `MAX_ACTIVE_JOBS`。

## 本地压测结论的边界

本地压测结果只能说明当前本地形态下的接单上界，不作为长期容量承诺，也不能直接外推到生产。生产上如果 API/worker 以 Pod 形式部署，且连接生产 PostgreSQL，安全值必须重新测，因为这些变量都会改变环境硬上限：

- API replicas。
- worker replicas。
- `WORKER_PROCESSES`。
- `WORKER_MAX_ASYNC_TASKS`。
- `WORKER_MAX_PREFETCH`。
- Pod CPU/内存 request/limit。
- PostgreSQL 实例规格、`max_connections`、连接池。
- Redis/Taskiq broker 规格。
- 真实 Job 执行耗时和外部模型限流。
- 轮询频率和查询流量。

## 最短结论

```text
只出现健康 503:
  可以考虑小步提高 MAX_ACTIVE_JOBS。

queued 持续增长:
  先扩 worker 消费能力，不要只提高 MAX_ACTIVE_JOBS。

running 变慢:
  优先优化 Job 执行、外部依赖、worker CPU/内存。

POST/GET p95 高:
  优先看 API、DB、Redis，不要先调 MAX_ACTIVE_JOBS。

PostgreSQL 连接数打满或 TooManyConnectionsError:
  先治理 DB 连接和池化，禁止继续放大 MAX_ACTIVE_JOBS。

CPU/内存打满或 Pod 重启:
  先扩资源或降并发，再重新估算 MAX_ACTIVE_JOBS。
```
