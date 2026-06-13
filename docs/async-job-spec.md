# AI 异步 Job 系统设计规范

**版本**：v1.1 · 最后更新：2026-06-13

| 版本 | 日期 | 说明 |
|------|------|------|
| v1.1 | 2026-06-13 | 补充 API 创建三步流程（4.4）、水平扩展约束与参考配置（4.5）、MAX_ACTIVE_JOBS 软限制声明、孤儿扫描双层防线说明、Checklist 扩容项 |
| v1.0 | 2026-06-13 | 初始版本，基于内部项目实践提炼 |

本规范定义在 FastAPI + Celery + PostgreSQL + Redis 栈上构建可靠 AI 异步 Job 系统的核心模式，解决 LLM 调用超时、任务幂等、消息可靠性和服务恢复问题。

**技术栈**：FastAPI / Celery / PostgreSQL / Redis，LLM 调用使用 litellm 或原生 async SDK（openai-python、anthropic 等）。

**部署环境**：测试和生产环境使用 Kubernetes（K8s）。API 服务和 Worker 服务各自作为独立 Deployment 部署，均支持水平扩展；Celery Beat（定期扫描，可选）须作为独立 Deployment 单实例运行（`replicas: 1`），不得与 Worker Deployment 合并。

---

## 文档定位

**适用场景**：
- Job 执行时间 10s~30min 的 AI 工作负载
- 调用方需要 callback 推送 + 轮询两种感知方式
- 技术栈：FastAPI / Celery / PostgreSQL / Redis
- LLM 调用：litellm 或原生 async SDK（openai-python、anthropic 等）

**不适用**：
- 亚秒级任务（用同步接口即可）
- 不使用 Celery 的项目
- 流式/SSE 响应（本规范聚焦非流式 Job）

**参考实现**：本规范已在内部项目中完整落地验证。接口契约由各项目自行定义，不在本规范范围内。

---

## 零、系统全景图

一图理解系统全貌，再按章节深入。

### 数据流与组件关系

```
                         调用方
               ┌─────────────────────────────────┐
               │  POST /jobs → 202               │
               │  GET  /jobs/{id}                │
               │  ← Callback（终态推送）           │
               └─────────────────────────────────┘
                           ↕ HTTP
     ┌──────────────────────────────────────────────────────────────┐
     │  API 服务（无状态，可水平扩展）                                 │
     │                                                               │
     │  创建守卫：count(queued+running) ≥ MAX_ACTIVE_JOBS → 503      │
     │  DB 写入 AIJob(status=queued, celery_task_id=NULL)           │
     │  Redis LPUSH → Celery 队列                                    │
     └──────────────────────────────────────────────────────────────┘
                     ↓ LPUSH                  ↑ DB 读写
         ┌───────────────────────┐   ┌────────────────────┐
         │      Redis 队列        │   │    PostgreSQL       │
         │  （消息投递通道）        │   │  （状态权威，非 Redis）│
         └───────────────────────┘   └────────────────────┘
                     ↓ BRPOP (prefetch=1)          ↑ 状态读写
     ┌──────────────────────────────────────────────────────────────┐
     │  Worker（Pod 数 × CELERY_WORKER_CONCURRENCY = 总并行槽数）     │
     │                                                               │
     │  ① 终态幂等守卫（succeeded/failed → 跳过）                     │
     │  ② mark_running                                               │
     │  ③ asyncio.wait_for(call_llm, L1) ← MODEL_CALL_TIMEOUT       │
     │         └─→ LLM API（chunked transfer，SDK timeout 无效）      │
     │  ④ mark_succeeded / mark_failed                               │
     │  ⑤ deliver_callback                                           │
     │  ⑥ ACK ← 消息此时才从 Redis 移除                               │
     │                                                               │
     │  超时保险（不依赖 LLM 侧 timeout）                              │
     │  L3 SIGALRM → CELERY_SOFT_TIME_LIMIT → 重试或 mark_failed     │
     │  L4 SIGKILL → CELERY_TIME_LIMIT      → 进程强杀，消息回队      │
     │  L5 stale 扫描 → JOB_STALE_RUNNING_SECONDS → 强制 failed      │
     └──────────────────────────────────────────────────────────────┘

恢复扫描（Worker 启动触发 + 可选定期扫描）
  孤儿 queued（celery_task_id IS NULL，超过 JOB_ORPHAN_TIMEOUT）→ 重投递
  僵死 running（started_at 超过 JOB_STALE_RUNNING_SECONDS）      → 强制 failed
```

### 关键配置速查

| 参数 | 控制位置 | 控制内容 | 约束关系 |
|------|---------|---------|---------|
| `MAX_ACTIVE_JOBS` | API 创建守卫 | queued+running 总积压上限，超出返回 503；0=禁用 | 建议 ≥ 总并行槽数 × 2 |
| `CELERY_WORKER_CONCURRENCY` | 每个 Worker Pod | 单 Pod 同时执行的任务数 | AI 任务建议 2~4 |
| Worker Pod 数 | K8s Deployment | 水平扩展执行容量 | 总槽数 = Pod 数 × CONCURRENCY |
| `MODEL_CALL_TIMEOUT_SECONDS` | Worker asyncio.wait_for | L1 主截断，AI 调用总时长 | < SOFT_TIME_LIMIT，差值 ≥ 300s |
| `CELERY_SOFT_TIME_LIMIT` | Celery SIGALRM | L3 进程级超时 | < TIME_LIMIT，差值 ≥ 60s |
| `CELERY_TIME_LIMIT` | Celery SIGKILL | L4 进程强杀 | < STALE_RUNNING，差值 ≥ 600s |
| `JOB_STALE_RUNNING_SECONDS` | 恢复扫描 | L5 僵死任务清理阈值 | ≥ TIME_LIMIT + 600s |
| `CELERY_MAX_RETRIES` | Worker 超时处理 | L1/L3 超时重试次数，0=不重试 | — |

---

## 一、核心概念

### 1.1 调用方视角 vs 内部实现

理解这个对比是读懂本规范的起点。调用方看到的是一个简单的异步接口，内部实现则涉及多个组件的协作。

```
调用方视角              内部实现
──────────────          ────────────────────────────────
POST /jobs ──→          DB 写入 AIJob（status=queued）
job_id                  → Redis LPUSH Celery task
GET /jobs/{id}          Worker: BRPOP → 执行 → DB 更新
Callback 推送           → deliver_callback（异步）
                        → ACK（此时才从 Redis 移除）

单任务模式: 1 个 Celery task → 1 次 AI 调用
Canvas 模式: N 个 WorkItem task + 1 个 finalize task
            （对调用方不可见，仍是一个 Job）
```

**关键设计原则：DB 是状态权威，Redis 是消息投递通道。** Job 状态以 DB 为准，不依赖 Celery result backend。Redis 宕机或清空后，通过恢复扫描重投递即可，不会丢失 Job 状态。

### 1.2 Job 状态机

```
queued → running → succeeded
  │          │
  └──────────┴──→ failed
                       ↑
                  canceled（可选扩展）
```

| 状态 | 含义 | 终态 |
|------|------|------|
| queued | 已入库，等待 Worker 消费 | 否 |
| running | Worker 正在执行 | 否 |
| succeeded | 执行成功，result_payload 已写入 | 是 |
| failed | 执行失败，error_payload 已写入 | 是 |
| canceled | 主动取消（可选，本规范不强制） | 是 |

**终态约束**：succeeded / failed 为不可变终态，写入后不得再次覆盖。消费层必须实现终态幂等守卫（见第六章），防止消息重投递时意外写入已终态的 Job。

---

## 二、执行模式

执行模式是基础架构选择，影响 Celery 任务组织方式、WorkItem 数据模型和 finalize 逻辑，应在项目启动时确定。

### 2.1 单任务模式（Single，默认）

```
process_job_task(job_id)
  → mark_running
  → asyncio.wait_for(call_llm(), timeout=T)
  → mark_succeeded
  → deliver_callback
```

适用绝大多数场景。一个 Job = 一次 AI 调用。

### 2.2 Canvas 模式（可选，按需引入）

当单次任务需要拆分为多个子步骤时使用。调用方视角不变（仍是一个 Job），内部通过 WorkItem 追踪子任务状态。

三种 Canvas 组合：

| 业务需求 | Canvas 原语 |
|---------|-----------|
| 顺序多步（A 完成后才执行 B） | `chain(task_A, task_B, ..., finalize)` |
| 并行分块 + 汇总 | `chord(group(chunk_tasks), finalize_task)` |
| 先串后并（前置映射 + 并行执行） | `chain(mapping_task, fanout_task)` |

Canvas 增加的复杂度：
- WorkItem 状态管理（每个子任务有独立 queued/running/succeeded/failed）
- finalize task 负责聚合结果并触发 Callback
- 错误传播：任一 WorkItem 失败 → finalize 检测到 → Job 整体 failed

**选择原则**：优先 Single。引入 Canvas 前，逐项确认以下条件：

- 子任务之间存在真实的并行执行收益（数量 ≥ 2，且相互独立）
- 存在明确的 finalize 合并逻辑（不是简单拼接）
- 串行分步或多个独立 Job 无法满足业务需求
- 团队具备 Celery Canvas 的运维经验

以上条件全部成立时才引入 Canvas。不应仅为追求架构复杂度而引入。

**WorkItem 数据模型**（仅 Canvas 模式需要）：

```
AIJobWorkItem
  ├─ job_id          FK → AIJob
  ├─ name / kind / chunk_index
  ├─ status          queued/running/succeeded/failed
  ├─ celery_task_id
  ├─ input_payload / result_payload / error_payload
  └─ started_at / finished_at
```

---

## 三、接口契约参考模式

本章给出的是参考模式，不是固定模板。不同项目可调整字段名，但核心交互结构——POST 创建 → 轮询/callback 感知——应保持不变。

### 3.1 创建 Job

```
POST /jobs → 202 Accepted
{
  "job_id": "uuid",
  "status": "queued",
  "status_url": "/jobs/{job_id}",
  "created_at": "datetime"
}
```

规则：
- 成功返回 **202**，不是 200（任务尚未完成）
- 业务侧校验失败（非法参数）返回 422
- 队列满返回 503
- 支持 `client_request_id` 幂等键（可选字段，见第六章）

### 3.2 查询 Job

```
GET /jobs/{job_id}
{
  "job_id": "uuid",
  "job_type": "...",
  "status": "running | succeeded | failed",
  "progress_percent": 42,      // 0~100
  "progress_text": "正在处理",
  "result": {...} | null,      // succeeded 时填充
  "error": {...} | null,       // failed 时填充
  "created_at": "datetime",
  "started_at": "datetime | null",
  "finished_at": "datetime | null"
}
```

字段约束：
- `status=succeeded` 时 `progress_percent` 必须为 100
- `status=failed` 时必须有 `error`（包含 `code`、`message`、`details`）
- `status=queued/running/succeeded` 时 `error` 必须为 null

### 3.3 Callback 通知

Job 进入终态后，向调用方配置的 URL 发送 POST 通知。Callback body 结构与 `GET /jobs/{id}` 终态核心字段保持一致。

规则：
- Callback 是**补充通知**，不替代轮询。调用方必须保留对 `GET /jobs/{id}` 的轮询查询作为备用手段
- Callback 失败不改变 Job 状态
- Callback 签名机制见第八章

### 3.4 幂等键 client_request_id

调用方可在创建 Job 时传 `client_request_id`。24 小时内相同调用方 + 相同 key，返回首次创建的 job_id，不重复创建。实现细节见第六章。

---

## 四、Celery 可靠性配置

### 4.1 必须配置项

以下五项是**非协商的必须配置**，缺少任何一项都会导致消息丢失或状态不一致。

```python
celery_app.conf.update(
    task_acks_late=True,               # 执行完成后才 ACK，Worker 崩溃时消息不丢
    task_reject_on_worker_lost=True,   # 进程意外终止，未 ACK 消息回队列
    worker_prefetch_multiplier=1,      # 每次只取 1 个任务，不预取
    task_serializer="json",
    accept_content=["json"],
)
```

- `acks_late=True`：Celery 默认 acks_early——Worker 取出消息就立即 ACK。Worker 崩溃后消息已从 Redis 移除，Job 永远不会完成。`acks_late` 改为执行完成后才 ACK，崩溃时消息仍在队列。
- `reject_on_worker_lost=True`：配合 `acks_late` 使用。进程意外终止时，Celery 将未 ACK 消息重投回队列，而不是丢弃。
- `prefetch_multiplier=1`：Worker 不提前锁定多个任务。AI 任务耗时长，预取会导致消息被锁定在单个 Worker 而未执行，队列长度虚增，其他 Worker 无法调度新任务。

### 4.2 Worker 数据库连接

Worker 数据库连接必须使用 NullPool：

```python
engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
```

Celery 用 fork 创建 Worker 子进程，SQLAlchemy 连接池不跨进程安全。NullPool 让每次 task 执行时独立建连、完成后立即关闭，无跨进程连接复用问题。

### 4.3 吞吐控制：三个旋钮

控制 AI Job 系统的并发能力，需要分清三个独立旋钮，它们的作用层次不同。

**旋钮 A — 执行槽数**（同时能执行多少任务）

```
总并行槽数 = Worker Pod 数 × CELERY_WORKER_CONCURRENCY

示例：3 Pod × 4 concurrency = 12 个任务可同时执行
```

两个子旋钮的分工：
- **CELERY_WORKER_CONCURRENCY**：单 Pod 同时跑几个任务。AI 任务是 I/O 密集型，进程 99% 的时间在等 LLM API 响应，CPU 接近空闲——因此这个值**不受 CPU 核数约束**，4 核机器设为 8 是完全合法的配置（CPU 密集型任务才需要对齐核数）。真正的约束是：①每个 worker 进程约 150~300 MB 内存，开 8 需要 Pod 有足够的内存限制；②NullPool 每任务独立建连，Concurrency × Pod 数不能超过 DB 连接预算（见 4.5 节）；③有效并发不超过 LLM API 配额。建议从 2~4 起步，确认内存和连接预算后再按需调高；**扩容优先靠加 Pod，单 Pod 调高 Concurrency 是次选**。
- **Worker Pod 数**：水平扩展总槽数的主要手段，通过 K8s HPA 按队列长度动态伸缩（见 11.3）。

**旋钮 B — 入口守卫**（是否接受新 Job 创建）

`MAX_ACTIVE_JOBS` 是 API 层的**背压机制**，在 `POST /jobs` 时检查 DB 中 `status IN (queued, running)` 的总数，超过上限返回 503，告诉调用方"队列已满，稍后重试"。它控制的是**接单**，不控制**执行速度**。

同时执行的任务数永远由 Worker 槽位（旋钮 A）物理约束。无论 MAX_ACTIVE_JOBS 设为多少——哪怕设为 0（禁用）——执行中的任务也不会超过 `Pod 数 × CELERY_WORKER_CONCURRENCY`。

关键区别：
- 总并行槽数 = 同时在**执行**的任务数（执行容量，由 Worker 物理决定）
- MAX_ACTIVE_JOBS = API 接单门槛（排队中 + 执行中 超过此值时拒绝新创建）

```
MAX_ACTIVE_JOBS 必须 ≥ 总并行槽数。

反例：总槽数 = 12，MAX_ACTIVE_JOBS = 10
→ 即便 queued=0，running=10 时新 Job 创建也会触发 503
→ 系统还有 2 个空闲槽，却拒绝了新任务
```

建议 `MAX_ACTIVE_JOBS` = 总并行槽数 × 2~3，为排队任务预留缓冲，避免峰值时调用方立即收到 503。

**多 API Pod 时的软限制特性**：`MAX_ACTIVE_JOBS` 是 best-effort 软限制，不是精确上限。多 API Pod 并发时，count() 检查和 INSERT 之间没有全局锁，高峰期实际积压数可能短暂超出设定值。这是预期行为——背压机制不需要精确，短暂超出不影响系统正确性。如需精确限制，应在 DB 层使用 `SELECT ... FOR UPDATE` 或 Redis 原子计数器，但 AI Job 场景通常没有必要。

**取消队列深度上限**（内部系统、或有外部限速层时）：

```python
MAX_ACTIVE_JOBS = 0  # 0 或不配置 → 禁用创建守卫，队列可无限积压
```

**旋钮 C — 单任务时长**（一次 AI 调用允许跑多久）

`MODEL_CALL_TIMEOUT_SECONDS` 控制 L1 主截断时长。调大时必须同步提高 `CELERY_SOFT_TIME_LIMIT`，约束关系见 5.5 节。

### 4.4 API 创建 Job 的三步流程与可靠性

API 收到 `POST /jobs` 后，内部执行三步，不是原子操作：

```
① DB write  : INSERT AIJob(status=queued, celery_task_id=NULL)
② Redis LPUSH: apply_async() → 拿到 celery_task_id
③ DB update : UPDATE AIJob SET celery_task_id=<id>
```

**celery_task_id 的设置职责**：由 API 在 `apply_async()` 返回后立即执行 ③，不由 Worker 设置。`celery_task_id IS NULL` 是孤儿检测的唯一信号——若改为由 Worker 在 `mark_running` 时设置，孤儿检测窗口会扩大到整个任务等待期，误判风险大幅上升。

**各步失败命运**：

| 崩溃点 | DB 状态 | Redis 有无消息 | 恢复路径 |
|--------|---------|--------------|---------|
| ① 后崩溃（未 LPUSH） | queued, celery_task_id=NULL | 无 | 孤儿扫描超时后重投递（正常路径） |
| ② 后崩溃（LPUSH 成功，③ 未完成） | queued, celery_task_id=NULL | 有 | Worker 正常消费原始消息；孤儿扫描超时后再次投递 → 双重投递，终态守卫兜底 |
| ③ 完成 | queued, celery_task_id=<id> | 有 | 正常路径 |

**双重投递场景（② 后崩溃）**：`JOB_ORPHAN_TIMEOUT_SECONDS` 到期后，孤儿扫描认为 LPUSH 未发生而再次投递，此时 Redis 中可能存在同一 Job 的两条消息。Worker 先消费任一条执行完毕并写入终态后，后消费的 Worker 在执行入口看到终态直接跳过（终态幂等守卫），不会重复执行。

**结论**：三步中任意一步失败均可恢复，不会有 Job 永久丢失，也不会有重复执行。`JOB_ORPHAN_TIMEOUT_SECONDS` 不应低于 60s（需为 ③ 的写入留出足够时间）；默认 300s 已远超安全阈值。

### 4.5 水平扩展约束与参考配置

在约束范围内，**API Pod 和 Worker Pod 的水平扩缩容是纯运维操作，不需要修改任何应用代码**——调整 K8s Deployment `replicas` 或 HPA 配置即可。本节说明约束来源、有效上限公式和参考配置值。

#### API Pod 扩展约束

API Pod 无状态，理论上可无限水平扩展，实际受 PostgreSQL 连接数约束：

```
API Pod 有效上限 = floor(PG 可用连接数 × 40% / pool_size_per_pod)
（建议预留 50% 给 Worker，10% 给 Beat / 管理工具 / 监控）
```

典型值（`pool_size_per_pod = 5`）：

| PG max_connections | API Pod 有效上限 |
|-------------------|----------------|
| 100（默认） | ~8 |
| 200 | ~16 |
| 500 | ~40 |

Redis 连接数通常不是瓶颈（Redis 默认上限 10,000）。

#### Worker Pod 扩展约束

Worker 使用 NullPool，每个并发任务独占一条 DB 连接，约束更严：

```
Worker Pod 有效上限 = min(
    floor(PG 可用连接数 × 50% / CELERY_WORKER_CONCURRENCY),
    floor(LLM API 并发上限 / CELERY_WORKER_CONCURRENCY)
)
```

**LLM API 并发上限是多数场景下真正的瓶颈**，超出后模型返回 429 限速错误，任务失败而非加速。例如：

| LLM 账号等级 | RPM 上限 | 单任务平均耗时 | 有效并发约 | 有效 Worker 槽 | Pod 上限（Concurrency=4） |
|------------|---------|------------|---------|-------------|------------------------|
| OpenAI Tier 1 | 500 | 2 min | ~16 | 16 | 4 |
| OpenAI Tier 2 | 5,000 | 2 min | ~166 | 166 | 41 |
| OpenAI Tier 3+ | 10,000+ | 2 min | ~333 | 333 | 83 |

#### 参考部署场景

以下以 `CELERY_WORKER_CONCURRENCY=4`、`API pool_size=5` 为基准（需结合实际 LLM 账号上限，取两者较小值）：

| 场景 | PG max_conn | API Pod | Worker Pod | 总并行槽 | 峰值 DB 连接 | MAX_ACTIVE_JOBS 建议 |
|------|-------------|---------|-----------|---------|------------|-------------------|
| 开发 / 测试 | 100 | 1 | 1 | 4 | ~9 | 10~20 |
| 小型生产 | 200 | 2 | 4 | 16 | ~26 | 30~50 |
| 中型生产（推荐起点） | 500 | 4 | 8 | 32 | ~52 | 60~100 |
| 大型生产 | 1,000 | 8 | 20 | 80 | ~120 | 150~250 |
| 超大型（需 PgBouncer） | 不受 Pod 限制 | 按需 | 按 LLM 上限 | 由 LLM 限速决定 | PgBouncer 管理 | LLM 并发 × 3 |

#### 为什么不能随意设置大 Pod 数

以 `Worker Pod = 100`、`CELERY_WORKER_CONCURRENCY = 4`（总槽 400）为例：

- 峰值需要 400 条 DB 并发连接，PostgreSQL 默认 `max_connections=100` → DB 连接立即耗尽，所有任务报错
- LLM 账号有效并发 = 30（Tier 1）→ 400 槽中 370 槽空转，实际吞吐等于 30 个槽
- 每个多余 Pod 消耗 100~300 MB 内存（Celery worker 进程），集群资源浪费

**结论：有效并行上限 = min(DB 连接上限, LLM API 并发上限) / CELERY_WORKER_CONCURRENCY。超出此值增加 Pod 不增加吞吐，只增加资源消耗和故障风险。**

#### 突破 DB 连接上限：引入 PgBouncer

当 Worker Pod 数 × Concurrency 超过 PG 连接上限时，引入 PgBouncer（transaction pooling 模式）而不是修改应用代码：

```
API / Worker → PgBouncer（transaction pooling）→ PostgreSQL
（大量应用连接复用为少量真实 PG 连接）
```

引入 PgBouncer 后，DB 连接约束解除，Worker 扩容上限由 LLM API 并发限制决定。

#### HPA maxReplicas 必须设置上限

HPA 不设上限时，队列突增可能触发扩出超限数量的 Pod，导致 DB 连接耗尽。`maxReplicas` 必须配置：

```
HPA maxReplicas ≤ min(
    floor(PG 可用连接数 × 50% / CELERY_WORKER_CONCURRENCY),
    floor(LLM API 并发上限 / CELERY_WORKER_CONCURRENCY)
)
```

---

## 五、超时链路设计

超时是 AI Job 系统最容易出错的地方。本章先解释核心陷阱，再给出完整的分层方案。

### 5.1 核心陷阱：为什么 SDK timeout 参数不够

OpenAI 和大多数 LLM API 使用 chunked transfer 传输响应：

```
客户端                          OpenAI 服务端
  │── POST /v1/chat/completions ─→│ 开始生成
  │                               │ 生成 chunk 1（~2s）
  │←── chunk 1 ───────────────────│
  │                               │ 生成 chunk 2（~2s）
  │←── chunk 2 ───────────────────│
  │         ...持续传输...         │
  │←── 结束标记 ───────────────────│
```

SDK 的 `timeout=600` 参数（无论是 litellm 还是 openai-python）最终作用于 httpx 的 `read_timeout`，即**单次 socket.recv() 的等待时长**，不是总时长。只要每隔几秒有 chunk 到达，600s read_timeout 永远不会触发。

**结论：`asyncio.wait_for` 是唯一可靠的总时长截断机制。**

### 5.2 有 litellm 时的完整超时链路

使用 litellm 前，必须理解它的内部机制：`litellm.acompletion()` **不是原生 async**，底层是 `loop.run_in_executor(None, litellm.completion)`，即将同步调用提交至线程池。

```
asyncio.wait_for(litellm.acompletion(), timeout=T)      ← L1 主截断（可靠）
  └─ litellm.acompletion(timeout=T)
       └─ run_in_executor(None, litellm.completion)       ← 跑在独立线程
            └─ httpx read_timeout=T                       ← 对 chunked 无效
                 └─ POST api.openai.com
```

完整五层：

| 层 | 机制 | 触发时机 | 截断对象 | 可靠性 |
|----|------|---------|---------|--------|
| L1 | `asyncio.wait_for` | `MODEL_CALL_TIMEOUT_SECONDS` | coroutine/Future 取消 | 可靠 |
| L2 | litellm `timeout=` → httpx | 同 L1 | 线程内 HTTP 读取 | 对 chunked 无效，仅作线程退出上限 |
| L3 | Celery SIGALRM（软超时） | `CELERY_SOFT_TIME_LIMIT` | 进程内所有阻塞 I/O | 可靠（Unix 信号） |
| L4 | Celery SIGKILL（硬超时） | `CELERY_TIME_LIMIT` | 进程强杀 | 绝对可靠 |
| L5 | stale running 扫描 | `JOB_STALE_RUNNING_SECONDS` | DB 中僵死 running job | 可靠（Worker 重启或定期扫描触发） |

**关于线程泄漏**：L1 触发后，`asyncio.wait_for` 取消的是协程/Future，**无法终止已运行的线程**。线程继续存在，但有界退出：L2（litellm timeout 作为线程内上限）+ L3（SIGALRM 中断线程阻塞 I/O）。线程不持有 DB 连接（NullPool 保证），资源消耗有界，属于可接受范围。

### 5.3 无 litellm / 原生 async SDK 时的超时链路

openai-python v1+、anthropic SDK 是**原生 async**，`asyncio.wait_for` 取消时干净，无线程泄漏。

```
asyncio.wait_for(client.chat.completions.create(), timeout=T)   ← L1 主截断
  └─ openai AsyncOpenAI（原生 async）
       └─ httpx read_timeout=T                                   ← 对 chunked 无效（同样的陷阱）
            └─ POST api.openai.com
```

| 层 | 机制 | 与 litellm 的区别 |
|----|------|-----------------|
| L1 | `asyncio.wait_for` | 取消干净，无线程泄漏 |
| L2 | SDK timeout → httpx | 不需要作为"线程退出上限"，但依然无法截断 chunked 总时长 |
| L3~L5 | 同 litellm | 不变 |

**不论是否使用 litellm，L1 asyncio.wait_for 都是必须的。** 区别只是有无线程泄漏风险。

### 5.4 统一超时处理代码模式

```python
@celery_app.task(name="jobs.process", bind=True, acks_late=True)
def process_job_task(self, job_id: str):
    try:
        return asyncio.run(_process(job_id))
    except (SoftTimeLimitExceeded, asyncio.TimeoutError) as exc:
        # L1 asyncio.TimeoutError 和 L3 SoftTimeLimitExceeded 统一处理
        if self.request.retries >= settings.CELERY_MAX_RETRIES:
            asyncio.run(_mark_timeout(job_id))
            raise
        raise self.retry(
            exc=exc,
            countdown=settings.CELERY_RETRY_DELAY,
            max_retries=settings.CELERY_MAX_RETRIES,
        )
    except Exception as exc:
        asyncio.run(_fail(job_id, exc))
        raise


async def _process(job_id: str):
    async def run(db):
        job = await get_job(db, job_id)
        # 终态幂等守卫
        if job.status in ("succeeded", "failed"):
            return  # 已是终态，跳过执行
        await mark_running(db, job)

        # L1 主截断
        result = await asyncio.wait_for(
            call_llm(job),
            timeout=settings.MODEL_CALL_TIMEOUT_SECONDS,
        )
        await mark_succeeded(db, job, result)
        await deliver_callback(job)
    await _with_db(run)
```

**重试期间的状态说明**：Job 在重试期间保持 `running` 状态，`started_at` 随每次执行刷新。Callback 只在最终结果（成功或最终失败）时发出一次，调用方不会收到重复通知。

### 5.5 配置约束

以下约束关系必须在启动时校验，违反则拒绝启动：

```
MODEL_CALL_TIMEOUT_SECONDS < CELERY_SOFT_TIME_LIMIT     （差值建议 ≥ 300s）
CELERY_SOFT_TIME_LIMIT     < CELERY_TIME_LIMIT           （差值建议 ≥ 60s）
CELERY_TIME_LIMIT          < JOB_STALE_RUNNING_SECONDS   （差值建议 ≥ 600s）
```

用 pydantic `model_validator` 实现：配置错误在进程启动时立即抛 `ValidationError`，不会带着错误配置上线。

**三个典型场景的参考配置：**

| 场景 | MODEL_CALL_TIMEOUT_SECONDS | CELERY_SOFT_TIME_LIMIT | CELERY_TIME_LIMIT | JOB_STALE_RUNNING_SECONDS |
|------|---------------------------|----------------------|-----------------|--------------------------|
| 短文本（< 5000 字） | 300 | 900 | 960 | 1800 |
| 标准场景（默认） | 600 | 1800 | 1860 | 2460 |
| 超长文本 / 慢代理 | 1200 | 2400 | 2460 | 3600 |

**重试配置说明**：`CELERY_MAX_RETRIES=0`（默认不重试）。超时触发后若有剩余重试次数，启动全新 Celery task，所有超时计时从 0 重置。调用方总等待上限 = `(MODEL_CALL_TIMEOUT_SECONDS + CELERY_RETRY_DELAY) × (CELERY_MAX_RETRIES + 1)`。注意：重试适合模型临时过载，不适合输入过长导致的超时（重试仍会超时）。

### 5.6 常见误区

**误区 1：以为 SDK `timeout=600` 控制了总时长**

对 chunked 响应无效（见 5.1）。真正的总时长截断只有 `asyncio.wait_for`（L1）。无论使用 litellm 还是原生 SDK，这条规则都成立。

**误区 2：以为 CELERY_SOFT_TIME_LIMIT 是主超时**

L3 是后备保障，正常路径由 L1 负责。两者都会触发，但 L1 先触发（例如 600s vs 1800s）。L3 只在 L1 因极端情况未能触发时才介入。

**误区 3：以为 CELERY_MAX_RETRIES 只控制 SoftTimeLimitExceeded**

`CELERY_MAX_RETRIES` 同时控制 L1（asyncio.TimeoutError）和 L3（SoftTimeLimitExceeded）。两类超时统一进入同一重试策略。

**误区 4：CELERY_SOFT_TIME_LIMIT 设得和 MODEL_CALL_TIMEOUT_SECONDS 一样**

例如都设 600s：L1 触发后进入重试/终态逻辑的 DB 写入和 Callback 发送需要时间，此时 SIGALRM 也触发，两个超时叠加会导致状态写入不完整。`CELERY_SOFT_TIME_LIMIT` 至少要比 `MODEL_CALL_TIMEOUT_SECONDS` 大 300s（即 5.5 节的配置约束）。

---

## 六、幂等性机制

幂等性需要在两个层面分别保障：请求创建层（防止调用方重试导致重复创建）和消费执行层（防止 Celery 消息重投递导致状态覆盖）。

### 6.1 请求级幂等（client_request_id）

```
请求到达
  → advisory_lock(caller_id + client_request_id)   ← PG 事务级锁，防并发重复创建
  → 查 24h 内相同 caller_id + client_request_id
  → 存在：返回已有 job_id（不重新创建）
  → 不存在：正常创建
```

PostgreSQL advisory lock 适合中低并发。高并发场景可改为 Redis SETNX EX。

### 6.2 消费级幂等（终态守卫）

```python
# Celery task 执行入口
if job.status in ("succeeded", "failed"):
    logger.warning("job_skipped job_id=%s status=%s", job_id, job.status)
    return  # 直接跳过，不执行，不发 callback
```

触发场景：
- 软超时 → mark_failed → 随后 SIGKILL → 消息回队 → 再消费时 Job 已是 failed

**守卫保护范围**：仅 `succeeded` / `failed` 这两个终态。

**`running` 不受守卫保护**：`running` 是执行中状态，不是终态。Worker 崩溃后消息回队，下一个 Worker 读到 `status=running` 时不会跳过，而是正常重新执行——这是设计意图，被中断的任务应当从头重试（见第七章）。

---

## 七、恢复机制

Worker 重启或 Redis 故障后，部分 Job 会卡在非终态（queued 无 celery_task_id，或 running 超时未结束）。本节定义恢复机制的三个层次。

### 7.1 Worker 启动扫描（基础，必须实现）

Worker 进程就绪后通过 `worker_ready` 信号触发一次扫描：

```python
@worker_ready.connect
def on_worker_ready(sender, **kwargs):
    run_recovery()

def run_recovery():
    # 1. 孤儿 queued Job 重投递
    #    status=queued AND celery_task_id IS NULL AND created_at < now - JOB_ORPHAN_TIMEOUT_SECONDS
    # 2. 僵死 running Job 强制 fail
    #    status=running AND started_at < now - JOB_STALE_RUNNING_SECONDS
```

**多 Worker 并发扫描的双层防线**：多个 Worker 同时启动时均会扫到同一批孤儿 Job，两层保障确保安全：

- **第一层（CAS 主动防御）**：`UPDATE AIJob SET celery_task_id=<new_id> WHERE id=? AND celery_task_id IS NULL`，只有一个 Worker 能更新成功，其余 Worker 因 CAS 失败直接跳过，不重复投递。
- **第二层（终态守卫兜底）**：若同一 Job 因 CAS 竞态或 4.4 节 Scenario ②（LPUSH 成功但 celery_task_id 未写回）被多次投入队列，Worker 在执行入口检查终态（succeeded/failed），已是终态则直接跳过，不重复执行。

### 7.2 running 任务的两条恢复路径

Worker 崩溃后，DB 中处于 `running` 的任务有两种命运，取决于 Redis 中消息的状态。

**路径 A（正常路径）：消息成功回队**

```
Worker 进程崩溃
  → task_reject_on_worker_lost=True → 消息回到 Redis 队列
  → DB 中 status 仍 = running（未更新）
  ↓
下一个 Worker 取出消息
  → 读取 Job 状态 = running（非终态，不触发幂等守卫）
  → 正常重新执行，started_at 刷新
  → 任务从头跑一次 AI 调用
  → mark_succeeded / mark_failed
```

这是正常行为：被中断的任务从头重试，任务最终会完成。

**路径 B（极端异常场景）：消息未回队**

极端情况下（Worker 进程异常但 Redis 连接未断开，消息既未 ACK 也未 reject）：

```
DB 中 status = running，Redis 中无对应消息
  → 任务既不执行，也不失败，永久卡住
  ↓
stale running 扫描介入
  → started_at 超过 JOB_STALE_RUNNING_SECONDS
  → 强制标记 failed，发送 Callback
```

路径 A 是预期的正常路径，路径 B 是针对极端异常场景的保障机制。两者均可保证消息不丢失、Job 不永久卡死。

### 7.3 进程内定期扫描（可选，推荐用于长时间不重启的进程）

不依赖 Celery Beat，在 API 或 Worker 进程内嵌入异步后台定时任务：

```python
# FastAPI lifespan 方式
@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_periodic_recovery_loop())
    yield
    task.cancel()

async def _periodic_recovery_loop(interval: int = 1800):
    while True:
        await asyncio.sleep(interval)
        try:
            await run_recovery_async()
        except Exception:
            logger.exception("periodic recovery failed")
```

**多实例部署时防止重复扫描**，二选一：

方案 A — Redis 分布式锁（推荐）：
```python
async def run_recovery_async():
    async with redis_lock("recovery_scan", timeout=300):
        await _do_recovery()
```

方案 B — PostgreSQL advisory lock（不依赖 Redis）：
```python
async def run_recovery_async():
    async with db.begin():
        acquired = await db.scalar(
            text("SELECT pg_try_advisory_xact_lock(hashtext('recovery_scan'))")
        )
        if not acquired:
            return  # 其他实例正在扫描
        await _do_recovery()
```

### 7.4 Celery Beat（可选，运维成本最高但最可靠）

当定期扫描需求明确且重启频率低时，可用 Celery Beat 替代进程内方案。

约束：
- Beat 进程必须**单实例运行**（K8s `replicas: 1`），须与 Worker Deployment 隔离部署
- Beat 不可用时，由 Worker 启动扫描（7.1 节）作为保障

### 7.5 三种方式选择

| 场景 | 推荐方案 |
|------|---------|
| 频繁滚动重启（K8s） | 7.1 已足够（Worker 重启时自动触发扫描） |
| 长时间不重启的进程 | 7.1 + 7.3（进程内定时） |
| 运维能力强，需最可靠保证 | 7.1 + 7.4（Beat） |

**清理窗口说明**：仅使用 7.1 时，路径 B 僵死 Job 的最大等待时间 = Worker 相邻两次重启的时间间隔。路径 A（消息正常回队）不受此约束，任务可立即被下一个 Worker 接管。

---

## 八、Callback 规范

### 8.1 时序

```
mark_succeeded / mark_failed（DB 写入终态）
  ↓
deliver_callback（异步）
  ↓
Celery ACK（此时才从 Redis 移除消息）
```

Callback 在终态写入**之后**执行。Callback 失败不影响 Job 终态，不阻塞 ACK。

### 8.2 签名

```
Header: X-AI-Service-Timestamp: <ISO 8601 UTC>
Header: X-AI-Service-Signature: sha256=<HMAC-SHA256>
签名原文 = timestamp + "." + request_body
密钥    = CALLBACK_SIGNING_SECRET（双方线下配置）
```

接收方从 `X-AI-Service-Timestamp` Header 取 timestamp，与 request_body 拼接后重新计算 HMAC-SHA256，与 `X-AI-Service-Signature` 比对。同时校验 timestamp 与当前时间差不超过 300 秒（防重放窗口，可按需调整）。

### 8.3 重试策略

4 次重试，延迟序列：0s / 10s / 30s / 60s。全部失败记录 ERROR 日志，不改变 Job 终态。

建议接入日志告警（错误率告警），否则 Callback 静默失败时无人感知。

### 8.4 调用方义务

- 必须按 job_id + event 做幂等去重（Callback 可能重复投递）
- 必须保留对 `GET /jobs/{id}` 的轮询查询作为备用手段，不得仅依赖 Callback

---

## 九、数据模型骨架

最小可复用 AIJob 字段。各字段说明见注释，迁移到新项目时按业务裁剪即可。

```python
class AIJob(Base):
    __tablename__ = "ai_jobs"

    # ── 核心字段（所有 AI Job 场景必须实现）──────────────────────
    id: UUID                         # 主键，对外 job_id
    caller_id: str                   # 来源标识（支持多租户），默认 "default"
    client_request_id: str | None    # 幂等键，可选
    job_type: str                    # 业务类型标识
    model_id: str                    # 使用的 AI 模型
    status: str                      # queued/running/succeeded/failed
    progress_percent: int            # 0~100
    progress_text: str | None

    input_payload: JSONB             # 完整输入快照（各业务按需定义结构）
    callback_payload: JSONB          # callback url、events 等
    result_payload: JSONB | None     # 成功时的结果
    error_payload: JSONB | None      # 失败时的 {code, message, details}

    celery_task_id: str | None       # NULL 表示孤儿 Job（恢复扫描信号）
    execution_mode: str | None       # single / canvas
    execution_plan: JSONB | None     # Canvas 时的执行计划

    created_at: datetime(tz)
    started_at: datetime(tz) | None
    finished_at: datetime(tz) | None
    updated_at: datetime(tz)
    expires_at: datetime(tz)         # 数据保留 TTL，用于定期清理

    # ── LLM 文本生成场景扩展字段（非 LLM 场景可移除）────────────
    output_payload: JSONB            # 输出位置元数据（OSS prefix 等）
    prompt_payload: JSONB            # AI prompt 快照（含完整 blocks）
```

**error_payload 结构约定**：

```json
{"code": "JOB_TIMEOUT", "message": "...", "details": {}}
```

**通用错误码（所有场景必须实现）**：

| 错误码 | 含义 |
|--------|------|
| `MODEL_CALL_FAILED` | AI 模型调用失败（非超时） |
| `JOB_TIMEOUT` | 超时（L1 或 L3 触发） |
| `MODEL_OUTPUT_INVALID` | 模型输出格式无法解析 |
| `INPUT_TOO_LARGE` | 输入超出模型上下文限制 |

**场景扩展错误码（按业务按需补充）**：

| 错误码 | 适用场景 | 含义 |
|--------|---------|------|
| `OSS_FETCH_FAILED` | 对象存储场景 | 对象存储读取失败 |
| `OSS_WRITE_FAILED` | 对象存储场景 | 对象存储写入失败 |
| `INPUT_HASH_MISMATCH` | 内容完整性校验场景 | 输入文件哈希校验失败 |

---

## 十、关键环境变量

环境变量按**控制维度**分组，便于出问题时快速定位和调整。

### 吞吐与扩容

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `CELERY_WORKER_CONCURRENCY` | CPU 核数 | 单 Pod 同时执行的任务数；AI 任务建议 2~4，扩容靠加 Pod 而非调高此值 |
| `MAX_ACTIVE_JOBS` | 50 | queued+running 总积压上限，超出返回 503；设为 0 则禁用检查（无上限） |

关系说明：

```
总并行槽数  = Worker Pod 数 × CELERY_WORKER_CONCURRENCY  （执行容量）
MAX_ACTIVE_JOBS                                          （积压上限，含排队+执行）

MAX_ACTIVE_JOBS 必须 ≥ 总并行槽数，否则执行槽有空余时仍会触发 503
建议 MAX_ACTIVE_JOBS = 总并行槽数 × 2~3（预留排队缓冲）
```

### 超时链路

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `MODEL_CALL_TIMEOUT_SECONDS` | 300 | L1 主截断 + L2 线程退出上限 |
| `CELERY_SOFT_TIME_LIMIT` | 1800 | L3 Celery SIGALRM；必须 > MODEL_CALL_TIMEOUT_SECONDS，差值 ≥ 300s |
| `CELERY_TIME_LIMIT` | 1860 | L4 Celery SIGKILL；必须 > CELERY_SOFT_TIME_LIMIT，差值 ≥ 60s |
| `CELERY_MAX_RETRIES` | 0 | 超时重试次数；0 = 不重试，直接 failed；同时控制 L1 和 L3 |
| `CELERY_RETRY_DELAY` | 60 | 重试等待秒数，仅 MAX_RETRIES > 0 时有意义 |

### 恢复与稳定性

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `JOB_STALE_RUNNING_SECONDS` | 2460 | L5 扫描阈值；推荐 ≥ CELERY_TIME_LIMIT + 600s |
| `JOB_ORPHAN_TIMEOUT_SECONDS` | 300 | queued + celery_task_id IS NULL 超过此秒视为孤儿 |
| `CALLBACK_TIMEOUT_SECONDS` | 5 | 单次 Callback HTTP 请求超时 |
| `CALLBACK_SIGNING_SECRET` | — | HMAC 签名密钥，不得为空 |
| `REDIS_URL` | — | 生产环境必须指向开启 AOF 的 Redis |

### 运维速查

| 现象 | 排查方向 | 调整旋钮 |
|------|---------|---------|
| 调用方频繁收到 503 | count_active_jobs 是否达到上限 | 扩 Worker Pod 数，或临时提高 `MAX_ACTIVE_JOBS`；内部系统可设为 0 禁用 |
| Worker 不空闲但任务仍积压 | 并发度是否过低 | 提高 `CELERY_WORKER_CONCURRENCY` 或扩 Pod |
| running 任务长期不结束 | Worker 是否还活着，消息是否在队列 | 重启 Worker Pod 触发启动扫描；或临时降低 `JOB_STALE_RUNNING_SECONDS` |
| 滚动部署时任务频繁重跑 | `terminationGracePeriodSeconds` 是否小于 `CELERY_TIME_LIMIT` | 见第十一章 |
| AI 调用超时频繁 | 输入是否过长，或模型响应慢 | 提高 `MODEL_CALL_TIMEOUT_SECONDS`，同步提高 `CELERY_SOFT_TIME_LIMIT` |
| 超时恢复扫描误杀正常任务 | `JOB_STALE_RUNNING_SECONDS` 是否过小 | 提高 `JOB_STALE_RUNNING_SECONDS`（不能低于 CELERY_TIME_LIMIT + 600s） |

**环境变量修改后必须重启才生效**：Settings 在进程启动时通过 `@lru_cache` 加载并缓存，运行期间修改不会自动重新读取。修改任意参数后需重启 API 和 Worker 两个进程。

---

## 十一、部署配置要点

本章记录将本规范落地到 Kubernetes（K8s）环境时的关键约束，与 Job 系统的设计模式本身无关，但会影响可靠性。

### 11.1 Pod 部署结构

| 组件 | 类型 | replicas | 说明 |
|------|------|----------|------|
| API 服务 | Deployment | ≥ 1 | 处理 HTTP 请求，写入 Job，触发 Celery 投递；无状态，可自由水平扩展 |
| Worker 服务 | Deployment | ≥ 1 | 消费 Celery 队列，执行 AI 任务；可水平扩展 |
| Beat 服务 | Deployment | **固定 1** | 定期扫描孤儿 / 僵死 Job（可选，见第七章）；必须单实例，不可水平扩展，须与 Worker Deployment 隔离部署 |

**Beat 的特殊约束**：Beat 是单例调度器，`replicas` 必须固定为 `1`。多实例运行会导致任务重复投递。Beat 不可用期间，由 Worker 启动扫描（7.1 节）作为保障。

**总并行槽数**：Worker Pod 数 × `CELERY_WORKER_CONCURRENCY`，是系统吞吐上限的核心参数。

**扩缩容操作说明**：在第 4.5 节约束范围内，API Pod 和 Worker Pod 的水平扩缩容是纯运维操作，不需要修改任何代码。HPA `maxReplicas` 必须 ≤ Worker Pod 有效上限，防止自动扩出超出 DB 连接或 LLM 并发限制的 Pod 数量（见 4.5 节）。

### 11.2 terminationGracePeriodSeconds

K8s 发送 SIGTERM 后，等待 `terminationGracePeriodSeconds` 秒未退出则发送 SIGKILL。Worker Pod 收到 SIGTERM 后，Celery 会等待当前任务完成再退出。

约束：

```
terminationGracePeriodSeconds ≥ CELERY_TIME_LIMIT + 60s（buffer）
```

违反此约束时：Pod 被 SIGKILL，任务通过路径 A（第七章 7.2）回队重新执行。不会丢失数据，但任务重跑会浪费 AI API 调用，并增加调用方的整体等待时间。

示例：`CELERY_TIME_LIMIT=1860` 时，建议配置 `terminationGracePeriodSeconds: 1920`。

### 11.3 HPA 指标建议

AI 任务等待模型响应期间 CPU 接近空闲，**不适合用 CPU 利用率作为 HPA 指标**。

推荐指标：Redis 队列长度（`LLEN celery`）

```
触发扩容：队列长度 > Worker Pod 数 × CELERY_WORKER_CONCURRENCY × 1.5
触发缩容：队列持续为 0 超过 N 分钟（缩容时需等待当前任务完成）
```

缩容时 K8s 会向被缩容的 Pod 发送 SIGTERM，遵循 11.2 的 `terminationGracePeriodSeconds` 约束，确保任务完成后再终止。

---

## 十二、接入 Checklist

新项目接入本规范时，按以下清单逐项确认。

### 必须项

- [ ] Celery 五项必须配置已设置（4.1）：`task_acks_late`、`task_reject_on_worker_lost`、`worker_prefetch_multiplier`、`task_serializer`、`accept_content`
- [ ] Worker 数据库连接使用 NullPool（4.2）
- [ ] 超时三项约束关系已通过启动时校验（5.5）：`MODEL_CALL_TIMEOUT_SECONDS < SOFT_TIME_LIMIT < TIME_LIMIT < STALE_RUNNING_SECONDS`
- [ ] 消费入口已实现终态幂等守卫（6.2）：`status in (succeeded, failed)` 时直接跳过
- [ ] Worker 启动时触发恢复扫描（7.1）：孤儿 queued Job 重投递 + 僵死 running Job 强制 fail
- [ ] Callback 签名密钥已配置，接收方已实现签名校验（8.2）
- [ ] K8s Worker Deployment 的 `terminationGracePeriodSeconds` ≥ `CELERY_TIME_LIMIT` + 60s（11.2）
- [ ] HPA `maxReplicas` 已按 4.5 节有效上限公式设置，防止自动扩容超出 DB 连接或 LLM 并发上限

### 推荐项

- [ ] 进程内定期扫描已配置（7.3），或已评估 Worker 重启间隔可接受路径 B 的僵死等待窗口（见 7.5 清理窗口说明）
- [ ] HPA 已配置为使用 Redis 队列长度作为扩缩容指标（11.3）
- [ ] `MAX_ACTIVE_JOBS` 已按实际并行槽数调整（4.3）：建议 ≥ 总并行槽数 × 2
- [ ] 如使用 Beat，已确认其作为独立 Deployment 单实例运行（`replicas: 1`，11.1）
- [ ] 已按 4.5 节参考场景评估当前 PG `max_connections` 和 LLM API 并发上限，确认有效 Worker Pod 上限
- [ ] 大规模部署（Worker Pod ≥ 20）已评估是否需要引入 PgBouncer（见 4.5 节）
