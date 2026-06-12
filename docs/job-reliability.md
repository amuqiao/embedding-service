# Job 消费保障设计

本文记录本服务 Job/Celery 的运行模式、所有已知失效场景、防护机制与关键环境变量。

## 本服务的 Job/Celery 模式

```
POST /jobs
  → 校验请求
  → DB 写入 job（status=queued）
  → db.commit()
  → process_job_task.delay(job_id)  →  Redis LIST "celery" LPUSH
  → DB 记录 celery_task_id

Worker（BRPOP 取出任务）
  → DB 更新 status=running
  → 调用 OpenAI（同步阻塞，最长 MODEL_CALL_TIMEOUT_SECONDS）
  → DB 更新 status=succeeded / failed
  → 发送 Callback（异步，不阻塞 ACK）
  → Celery ACK（acks_late=True，此时才从 Redis 移除消息）
```

**关键设计约束：**

- **DB 是真相**，Redis 是传递通道。Job 状态以 DB 为准，不依赖 Celery result backend。
- `task_acks_late=True`：任务执行完成后才 ACK，Worker 崩溃时消息不会丢失。
- `task_reject_on_worker_lost=True`：Worker 进程意外终止时，未 ACK 的消息回到队列。
- `worker_prefetch_multiplier=1`：Worker 每次只取 1 个任务，不提前锁定队列。
- Worker 层使用 `NullPool`：每个任务独立创建 DB 连接，避免连接池跨进程复用问题。
- Callback 发送是异步通知，失败不影响 Job 终态，也不阻塞 Celery ACK。

---

## 所有失效场景枚举

### 场景 A：API 进程在 `db.commit()` 之后、`delay()` 之前崩溃

| 项 | 内容 |
|---|---|
| 触发条件 | API 进程 OOM、强杀或部署重启，恰好在两行代码之间 |
| DB 状态 | `status=queued`，`celery_task_id=NULL` |
| Redis 状态 | 无对应任务 |
| 结果 | Job 永久卡在 queued，上游轮询永远拿到 queued，不会失败也不会完成 |
| 防护 | **启动恢复扫描**：Worker 启动时扫描 `celery_task_id IS NULL` 的孤儿 Job 并重新投递；**原子性抢占**：`claim_orphan_for_dispatch` 确保多 Worker 并发扫描时同一 Job 只被投递一次 |

### 场景 B：`delay()` 调用时 Redis 不可达（网络抖动、Redis 重启）

| 项 | 内容 |
|---|---|
| 触发条件 | Redis 临时不可达，`delay()` 抛出异常 |
| DB 状态 | `status=queued`，`celery_task_id=NULL`（事务已提交） |
| Redis 状态 | 无对应任务 |
| 结果 | API 返回 500，Job 在 DB 中卡住。与场景 A 结果相同 |
| 防护 | **启动恢复扫描**（同场景 A） |

### 场景 C：Worker 取出任务后、执行 `mark_running` 之前崩溃

| 项 | 内容 |
|---|---|
| 触发条件 | Worker 进程启动后立即被杀 |
| DB 状态 | `status=queued` |
| Redis 状态 | 消息已取出但未 ACK |
| 结果 | `task_reject_on_worker_lost=True` → 消息回到队列，下次 Worker 重新消费 |
| 防护 | ✅ 已有防护，无需额外处理 |

### 场景 D：Worker 在 AI 调用过程中被 SIGKILL / OOM 强杀

| 项 | 内容 |
|---|---|
| 触发条件 | Worker 进程被操作系统强杀（OOM、`kill -9`），任务尚未完成 |
| DB 状态 | `status=running` |
| Redis 状态 | 消息未 ACK，因 `task_reject_on_worker_lost=True` 回到队列 |
| 结果 | 消息不丢，任务被重新消费 |
| 防护 | ✅ 消息正常重投递；**终态幂等守卫**：若软超时已先将 Job 标记为 `failed`，重新消费时守卫检测到终态直接跳过，不覆盖状态 |

> **场景 D 与 F 的区别**：D 是正常重投递（Job 仍在 `running` 或已被超时标记 `failed`），重新消费后守卫判断终态跳过；F 专门描述软超时 + SIGKILL 的组合导致的无限循环风险及其阻断机制。

### 场景 E：SoftTimeLimitExceeded（Celery 软超时）

| 项 | 内容 |
|---|---|
| 触发条件 | 任务执行超过 `CELERY_SOFT_TIME_LIMIT`（默认 1800s） |
| 处理过程 | 收到 SIGALRM → 代码捕获 → 若 `CELERY_MAX_RETRIES > 0` 且未耗尽重试次数，进入重试流程（见场景 Q 重试机制）；否则 `_mark_timeout()` → DB 标记 `status=failed`（JOB_TIMEOUT）→ 发 Callback |
| 结果 | 重试耗尽后 Job 进入 failed 终态，Celery ACK 任务 |
| 防护 | ✅ 与场景 Q（asyncio.TimeoutError）共用统一重试策略 |

### 场景 F：SoftTimeLimitExceeded + SIGKILL 循环风险

| 项 | 内容 |
|---|---|
| 触发条件 | 软超时后 60 秒内未完成清理，Celery 发送 SIGKILL |
| DB 状态 | 软超时已将 Job 标记为 `failed` |
| Redis 状态 | SIGKILL 导致消息未 ACK → 回到队列 |
| 无守卫的结果 | 任务被重新消费，`mark_running` 将 `failed` 覆盖为 `running`，Job 无限循环执行 |
| 防护 | **终态幂等守卫**：`_process()` 先读取 Job 当前状态，已是 `failed` / `succeeded` 则直接返回，阻断循环 |

### 场景 G：Redis 无持久化，Redis 服务重启

| 项 | 内容 |
|---|---|
| 触发条件 | 生产 Redis 未开启 AOF/RDB，Redis 进程重启 |
| DB 状态 | 有 `status=queued` 和 `status=running` 的 Job |
| Redis 状态 | 队列清空 |
| 结果 | 这些 Job 永远不会被消费（对于 queued）或不会完成（对于 running） |
| 防护 | **运维侧**：Redis 必须开启 AOF 持久化；**应用侧**：启动恢复扫描兜底 |

### 场景 H：Worker 正常重启（Graceful Shutdown）

| 项 | 内容 |
|---|---|
| 触发条件 | `celery worker --stop`、部署更新 |
| 处理过程 | Worker 完成当前任务后停止，Redis 队列不受影响 |
| 结果 | 新 Worker 启动后继续消费队列 |
| 防护 | ✅ 无需额外处理 |

### 场景 I：Worker 强制重启，有任务正在执行

| 项 | 内容 |
|---|---|
| 触发条件 | 部署时 SIGKILL Worker 进程 |
| 结果 | 同场景 D，消息回队列，新 Worker 重新消费 |
| 防护 | **终态幂等守卫**（同场景 D） |

### 场景 J：长时间卡在 running，Worker 已死且未触发 reject

| 项 | 内容 |
|---|---|
| 触发条件 | 极端情况：Worker 进程异常但 Redis 连接未断开，消息既未 ACK 也未 reject |
| DB 状态 | `status=running`，`started_at` 已超过 `CELERY_TIME_LIMIT` |
| 结果 | Job 无限期停留在 running，直到下次 Worker 重启 |
| 防护 | **Worker 启动扫描**：`worker_ready` 信号触发 stale running 扫描，`started_at` 超过 `JOB_STALE_RUNNING_SECONDS` 则强制标记 `failed`；**清理窗口**：等同于 Worker 下次重启时间，无 Beat 时无自动周期清理 |

### 场景 K：API 服务完全不可用（重启 / 宕机）

| 项 | 内容 |
|---|---|
| 触发条件 | API Pod 重启、OOM、部署更新导致短暂不可用 |
| 对已有 Job 的影响 | **无**。Worker 继续消费 Redis 队列，进行中的任务正常完成 |
| 对新请求的影响 | 上游收到 502/503，无法提交新 Job |
| 恢复流程 | API 无状态，重启后直接重连 DB 和 Redis，恢复接受请求 |
| 防护 | ✅ 无需额外处理。API 挂掉不影响已有 Job 的生命周期 |

### 场景 L：Worker 服务完全不可用（重启 / 宕机）

| 项 | 内容 |
|---|---|
| 触发条件 | Worker Pod 重启、OOM、部署更新 |
| 对已有 Job 的影响 | 正在执行的任务：消息因 `task_reject_on_worker_lost` 回到 Redis 队列，`status` 保持 `running` |
| 对新请求的影响 | API 正常接受，Job 入库并推入 Redis，队列持续积压 |
| 积压副作用 | `status=queued` 的 Job 累积，`count_active_jobs` 持续增长，达到 `MAX_ACTIVE_JOBS` 后 API 开始返回 503。**这是符合预期的保护行为**，防止无限堆积 |
| 恢复流程 | Worker 重启 → `worker_ready` 信号触发恢复扫描 → 孤儿 Job 重投递、僵死 running 强制 fail → 正常消费恢复 |
| 防护 | ✅ 消息不丢（Redis AOF）；启动恢复扫描处理异常状态 |

### 场景 M：API 与 Worker 同时不可用

| 项 | 内容 |
|---|---|
| 触发条件 | 全量部署、基础设施故障、Redis / DB 故障导致两个服务同时崩溃 |
| 期间状态 | DB 保留所有 Job 记录；Redis 队列在 AOF 开启时完整保留 |
| 恢复顺序 | **无强依赖**。先起 API 还是先起 Worker 均可正常恢复 |
| Worker 先起 | 恢复扫描运行，孤儿 Job 重投递到 Redis；API 起来后继续接受新请求 |
| API 先起 | 接受新请求，Job 正常入库推 Redis；Worker 起来后消费积压队列并触发扫描 |
| 两者同时起 | Worker 扫描与 API 接受新请求并发进行，无冲突 |
| 防护 | ✅ 整体可恢复；关键前提：Redis 必须开启 AOF，否则重启期间推入 Redis 的任务丢失（退化为场景 G） |

### 场景 N：PostgreSQL 不可用（重启 / 宕机）

**API 侧**

| 项 | 内容 |
|---|---|
| 触发条件 | DB 连接失败或超时 |
| 结果 | `POST /jobs` DB 写入失败 → 返回 500，Job 从未入库，**无孤儿** |
| 恢复 | DB 恢复后 `pool_pre_ping=True` 自动重连，API 恢复正常 |
| 防护 | ✅ API 侧干净失败，无需额外处理 |

**Worker 侧（DB 在任务执行期间不可用）**

| 项 | 内容 |
|---|---|
| 触发条件 | DB 在任务执行期间不可用 |
| 处理过程 | `_process()` DB 操作失败 → 进入 `except Exception` → `_fail()` 尝试写 DB → 同样失败 → 任务带异常退出 |
| 关键行为 | `task_acks_on_failure_or_timeout` 默认为 `True`，即任务抛出异常时 Celery 仍会 ACK 消息，**消息从 Redis 移除，但 Job 状态未更新**，卡在 `running` 或 `queued` |
| 当前选择 | 保留默认行为不设 `task_acks_on_failure_or_timeout=False`，因为强制 NACK 会导致 DB 故障期间任务无限重投递、Worker 持续报错，收益有限 |
| 恢复 | DB 恢复后，stale running 扫描将超时的 `running` Job 强制标记 `failed` |
| 防护 | ⚠️ 靠 stale running 扫描兜底；DB 故障窗口内的 Job 会在恢复后被强制 fail，不会静默卡住 |

**Worker 侧子场景：AI 调用成功但 DB 写入结果失败**

| 项 | 内容 |
|---|---|
| 触发条件 | `mark_succeeded()` 写 DB 失败（DB 短暂不可用或连接超时） |
| 代价 | AI 推理已完成（token 已消耗），结果丢失；Job 卡在 `running` |
| 结果 | 与 Worker 侧主场景相同：ACK 消息，Job 由 stale running 扫描强制 fail |
| 防护 | ⚠️ 同 Worker 侧主场景。此为代价最高的子场景，但发生概率极低（仅在 DB 恰好在 AI 完成后瞬间故障） |

### 场景 O：Redis 不可用（重启 / 宕机）

**API 侧**

| 项 | 内容 |
|---|---|
| 触发条件 | `delay()` 调用时 Redis 不可达 |
| 结果 | Job 已入库（`celery_task_id=NULL`），`delay()` 抛异常，API 返回 500 |
| 恢复 | 与场景 B 相同，启动恢复扫描重新投递孤儿 Job |
| 防护 | ✅ 孤儿扫描覆盖 |

**Worker 侧**

| 项 | 内容 |
|---|---|
| 触发条件 | Worker 与 Redis 之间连接中断 |
| 对消费的影响 | Worker 丢失 broker 连接，停止取新任务 |
| 对进行中任务的影响 | AI 调用继续执行；完成后尝试 ACK 失败，消息保持**未 ACK 状态** |
| Redis 重启（AOF 开启） | 未 ACK 消息保留；Worker 重连后消息自动回队，继续消费 |
| Redis 重启（无 AOF） | 队列清空，退化为场景 G；DB 中 `queued`/`running` Job 由恢复扫描处理 |
| 防护 | ✅ AOF 开启时消息不丢；无 AOF 时靠恢复扫描兜底 |

### 场景 P：Callback 发送失败（所有重试耗尽）

| 项 | 内容 |
|---|---|
| 触发条件 | 上游 Callback URL 不可达、返回 HTTP 非 2xx、连接超时，4 次重试（延迟 0/10/30/60s）全部失败 |
| Job 状态 | 已是终态（`succeeded` / `failed`），**不受影响** |
| Callback 状态 | 所有重试耗尽后记录 `ERROR` 日志，上游不会收到事件推送 |
| 关键设计 | Callback 是异步通知，在 `mark_succeeded` / `mark_failed` **之后**执行，失败不阻塞 Celery ACK，不导致任务重新消费 |
| 重试参数 | 重试延迟序列（0/10/30/60s，共 4 次）硬编码于 `services/callbacks.py`；单次连接超时由 `CALLBACK_TIMEOUT_SECONDS`（默认 5s）控制 |
| 上游影响 | 上游无法通过 Callback 感知终态，需依赖主动轮询 `GET /jobs/{id}` 发现结果 |
| 防护 | ⚠️ 无自动补偿；上游应结合轮询兜底；`ERROR` 日志应接入告警（如日志系统错误率告警），否则 Callback 静默失败时无人感知 |

### 场景 Q：AI 调用超时（模型响应时间过长）

#### 背景：调用链路与 timeout 的真实行为

```
asyncio.wait_for(timeout=MODEL_CALL_TIMEOUT_SECONDS)      ← 主动截断层（可靠）
  └─ litellm.acompletion(timeout=MODEL_CALL_TIMEOUT_SECONDS)
       └─ loop.run_in_executor(None, litellm.completion)  ← 跑在独立线程
            └─ AsyncOpenAI(timeout=resolved)
                 └─ httpx read_timeout                    ← 对 chunked 响应无效
                      └─ POST api.openai.com
```

`litellm.acompletion` **不是原生 async**，底层是把同步 `litellm.completion` 丢进 `run_in_executor` 线程池执行。

**为什么 `litellm.completion(timeout=600)` 对大模型长响应无效：**

OpenAI 非流式 API 使用 chunked transfer encoding，服务端生成时持续发送小块数据。httpx 的 `read_timeout` 是单次 `socket.recv()` 的等待时长，不是总时长。只要每隔几秒有一个 chunk 到达，600s read_timeout 就永远不会触发，导致任务实际跑完整个 30 分钟软超时才被 Celery 中断。

| 项 | 内容 |
|---|---|
| 触发条件 | 输入文本较长，模型 chunked 响应持续输出，总耗时超过 `MODEL_CALL_TIMEOUT_SECONDS` |
| 根本原因 | httpx `read_timeout` 是单次 socket read 超时，对持续吐 chunk 的 LLM 响应无效 |
| **截断方案** | `asyncio.wait_for(litellm.acompletion(), timeout=MODEL_CALL_TIMEOUT_SECONDS)` 是唯一可靠的总时长截断 |
| Thread leakage | `asyncio.wait_for` 取消的是 coroutine/Future，不是线程；线程继续运行，但有界：litellm 自身 timeout + Celery SIGALRM 兜底。线程不持有 DB 连接，泄漏代价低 |
| 超时后行为 | `asyncio.TimeoutError` → `process_job_task` 捕获 → 进入统一重试策略（见下方） |
| 防护 | ✅ `asyncio.wait_for` 可靠截断；重试策略统一管理终态转换；Celery SIGALRM 兜底线程泄漏 |

#### 重试机制

`asyncio.TimeoutError`（L1）和 `SoftTimeLimitExceeded`（L3）统一使用相同的重试策略，由 `CELERY_MAX_RETRIES` 和 `CELERY_RETRY_DELAY` 控制：

```
超时触发
  → retries < CELERY_MAX_RETRIES？
      是 → 记录警告日志 → self.retry(countdown=CELERY_RETRY_DELAY)
             → 新 Celery task 启动，asyncio.wait_for 和 CELERY_SOFT_TIME_LIMIT 均从 0 重新计时
             → Job 保持 running 状态，started_at 刷新
      否 → _mark_timeout() → status=failed(JOB_TIMEOUT) → 发 Callback
```

**关键约束：**

- `CELERY_MAX_RETRIES=0`（默认）：超时直接进入 failed，不重试
- 每次重试是独立的完整执行周期，超时计时重置
- Callback 只在所有重试耗尽后发出一次，调用方不会收到重复通知
- 调用方总等待上限：`(MODEL_CALL_TIMEOUT_SECONDS + CELERY_RETRY_DELAY) × (CELERY_MAX_RETRIES + 1)`

**配置示例（1 次重试）：**

| 变量 | 值 | 说明 |
|------|-----|------|
| `MODEL_CALL_TIMEOUT_SECONDS` | 600 | 单次执行超时 |
| `CELERY_RETRY_DELAY` | 60 | 重试等待间隔 |
| `CELERY_MAX_RETRIES` | 1 | 最多重试 1 次 |
| 调用方最长等待 | 1320s | (600 + 60) × 2 |

#### 超时层级与生效顺序

| 层级 | 机制 | 时长 | 截断对象 | 可靠性 |
|------|------|------|----------|--------|
| L1 | `asyncio.wait_for` | `MODEL_CALL_TIMEOUT_SECONDS`（600s） | coroutine/Future | ✅ 可靠，与 chunked 无关 |
| L2 | `litellm.acompletion(timeout=N)` → httpx | 600s | 线程内 HTTP 读取 | ⚠️ 对 chunked 响应无效，作为线程泄漏退出上限 |
| L3 | Celery SIGALRM（软超时） | `CELERY_SOFT_TIME_LIMIT`（1800s） | 进程内所有线程的阻塞 I/O | ✅ 可靠（Unix signal） |
| L4 | Celery SIGKILL（硬超时） | `CELERY_TIME_LIMIT`（1860s） | 进程强杀 | ✅ 绝对可靠 |
| L5 | stale running 扫描 | `JOB_STALE_RUNNING_SECONDS`（2460s） | DB 中僵死 running job | ✅ Worker 重启后触发 |

正常情况（`CELERY_MAX_RETRIES=0`）：L1 触发 → mark_failed → Callback → Worker 继续接新任务。

L3 是 L1 失效时的兜底；L3 触发后同样进入统一重试策略，行为与 L1 一致。

> **配置约束**：`MODEL_CALL_TIMEOUT_SECONDS` < `CELERY_SOFT_TIME_LIMIT` < `CELERY_TIME_LIMIT` < `JOB_STALE_RUNNING_SECONDS`。启用重试时，还需确认调用方能接受 `(MODEL_CALL_TIMEOUT_SECONDS + CELERY_RETRY_DELAY) × (CELERY_MAX_RETRIES + 1)` 的总等待时长。

---

## 防护机制汇总

| 场景 | 机制 | 实现位置 |
|------|------|---------|
| A / B：孤儿 queued Job | 启动恢复扫描 + 定期扫描；原子性抢占防重复投递 | `tasks/recovery.py` + `worker_ready` 信号 |
| C：取出前 Worker 崩溃 | `task_reject_on_worker_lost` | `celery_app.py`（已有） |
| D / F / I：重新消费已终态 Job | 终态幂等守卫（succeeded/failed 时跳过） | `tasks/jobs.py` `_process()` |
| E：软超时 | SoftTimeLimitExceeded 捕获 | `tasks/jobs.py`（已有） |
| F：软超时 + SIGKILL 循环 | 终态幂等守卫阻断再执行 | `tasks/jobs.py` `_process()` |
| G：Redis 无持久化 | AOF 持久化 + 启动恢复扫描兜底 | 运维配置 + `tasks/recovery.py` |
| J：僵死 running | Worker 启动时扫描强制 fail | `tasks/recovery.py` + `worker_ready` 信号 |
| K：API 完全不可用 | 无状态，重启即恢复；已有 Job 不受影响 | 无需额外代码 |
| L：Worker 完全不可用 | 队列积压至 MAX_ACTIVE_JOBS 触发 503 保护；重启后扫描恢复 | `MAX_ACTIVE_JOBS` + `worker_ready` 扫描 |
| M：API + Worker 同时不可用 | 恢复无顺序依赖；关键前提 Redis AOF | 运维配置 + 启动恢复扫描 |
| N：PostgreSQL 不可用 | API 侧干净 500，无孤儿；Worker 侧 ACK 但 DB 未更新，stale running 扫描兜底 | `pool_pre_ping` + stale running 扫描 |
| O：Redis 不可用 | API 侧孤儿扫描覆盖；Worker 侧未 ACK 消息在 Redis 恢复后回队；无 AOF 退化为场景 G | AOF 持久化 + 孤儿扫描 |
| P：Callback 发送失败 | 日志记录 ERROR；不影响 Job 终态；上游应结合轮询兜底 | `services/callbacks.py` |
| Q：AI 调用超时 | `asyncio.wait_for` 硬截断 + `asyncio.TimeoutError` 捕获 → `_mark_timeout()`；Celery 软超时兜底 | `infrastructure/ai_gateway.py` + `tasks/jobs.py` |

---

## 关键环境变量

| 变量 | 默认值 | 说明 | 风险 |
|------|--------|------|------|
| `CELERY_SOFT_TIME_LIMIT` | `1800` | 软超时（秒），触发 JOB_TIMEOUT | 必须小于 `CELERY_TIME_LIMIT` |
| `CELERY_TIME_LIMIT` | `1860` | 硬超时（秒），触发 SIGKILL | 与软超时差值建议 ≥ 60s |
| `MODEL_CALL_TIMEOUT_SECONDS` | `300` | 单次 AI 调用超时 | 应远小于 `CELERY_SOFT_TIME_LIMIT` |
| `CELERY_MAX_RETRIES` | `0` | 超时重试次数；同时控制 L1（asyncio.TimeoutError）和 L3（SoftTimeLimitExceeded）；0 表示不重试，直接进入 failed；调用方总等待上限为 `(MODEL_CALL_TIMEOUT_SECONDS + CELERY_RETRY_DELAY) × (重试次数 + 1)` | 设置过大会显著延长调用方等待时间；输入过长导致的超时重试无效 |
| `JOB_ORPHAN_TIMEOUT_SECONDS` | `300` | queued 且无 celery_task_id 超过此秒数视为孤儿，触发恢复投递 | 过小会误判正常排队等待消费的 Job |
| `JOB_STALE_RUNNING_SECONDS` | `2460` | running 超过此秒数视为僵死，触发强制 fail；推荐 ≥ `CELERY_TIME_LIMIT` + 600（1860 + 600 = 2460），为 SIGKILL 后 reject / requeue 留出充分缓冲 | 设置过小会误杀正常耗时较长的任务 |
| `MAX_ACTIVE_JOBS` | `50` | API 层队列深度软上限，超出返回 503；注意：并发创建时可能短暂超过此值（见下方说明） | 应与 Worker 并发数和预期排队量匹配 |
| `CALLBACK_TIMEOUT_SECONDS` | `5` | 单次 Callback HTTP 请求超时（秒）；重试延迟序列（0/10/30/60s，4 次）硬编码；Callback 异步执行，不占用软超时计时 | 过小易触发超时重试；调大后 Worker 线程阻塞时间增加 |
| `REDIS_URL` | — | Broker 和 Result Backend | 生产环境必须指向开启 AOF 的 Redis |

> **MAX_ACTIVE_JOBS 是软限制**：检查队列深度（`count_active_jobs`）和写入新 Job 之间不是原子操作。在极高并发下，多个请求可能同时通过深度检查，导致实际活跃 Job 数短暂超过上限（最多超出并发请求数）。该限制的目的是防止大规模超载，不保证精确的硬上限行为。

---

## 恢复机制设计

### 启动恢复（Worker 启动时自动触发）

Worker 进程就绪后通过 `worker_ready` 信号触发一次扫描：

1. **孤儿 Job 重投递**：`status=queued AND celery_task_id IS NULL AND created_at < now - JOB_ORPHAN_TIMEOUT_SECONDS`
2. **僵死 running 强制 fail**：`status=running AND started_at < now - JOB_STALE_RUNNING_SECONDS`

**多 Worker 并发扫描的原子性**：多个 Worker 同时启动时，均会扫描到同一批孤儿 Job 并分别调用 `process_job_task.delay()`。为防止同一 Job 被重复投递，扫描后通过原子 `UPDATE WHERE celery_task_id IS NULL` 抢占写入权；未抢到的 Worker 放弃该 Job，已额外投入 Redis 的 Celery 任务若被消费，终态幂等守卫负责安全跳过。

### 定期扫描（可选，当前未启用）

如需在 Worker 不重启的情况下持续兜底，可在 `celery_app.py` 中恢复 Beat 配置，每 30 分钟触发一次恢复扫描。

**不启用的权衡**：当前恢复扫描仅在 `worker_ready` 时触发。场景 J（Worker 已死但 Redis 连接未断开导致消息不 reject）的僵死 Job，只能等 Worker 下次重启才被清理；频繁重启（如 K8s 滚动部署）的环境下影响有限，低重启频率的环境则清理窗口不确定。

**若启用 Beat，需注意**：
- Beat 进程必须单实例运行（K8s `replicas: 1`），不与 Worker 混部，否则重复调度同一任务。
- 需在 `celery_app.py` 中重新注册 `recovery_task` 和 `beat_schedule`，并在部署侧单独启动 `celery beat` 进程。
- Beat 自身故障时的降级窗口：最坏等待下一个调度周期（30 分钟）；叠加 `JOB_STALE_RUNNING_SECONDS=2460s`，stale running 总暴露时长最长约 73 分钟（仅 Beat 故障期间适用，正常运行时不适用）。

### 终态幂等守卫

`_process()` 在执行 `mark_running` 前检查 Job 当前状态，若已是 `succeeded` 或 `failed` 则直接跳过，不再重新执行。

**守卫的有效范围与原子性限制**：
- **有效范围**：防止消息重投递（NACK 回队后）对已完成 Job 的状态覆盖，即场景 D / F / I 中"已终态的 Job 被再次消费"的情况。
- **原子性前提**：Celery 使用 Redis BRPOP 取任务，每条消息同一时刻只会被一个 Worker 消费，不存在同一消息被多 Worker 并发执行的情况。守卫依赖"先读状态、再决定是否执行"，两步之间无需加锁。
- **不覆盖的场景**：若同一 Job 因 Bug 或手动操作被二次入队（生成两条不同的 Celery 消息），两条消息的消费存在真实并发，守卫不保证线程安全。这属于外部约束违规，不在当前设计范围内。
