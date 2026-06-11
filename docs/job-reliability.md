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
  → 发送 Callback
  → Celery ACK（acks_late=True，此时才从 Redis 移除消息）
```

**关键设计约束：**

- **DB 是真相**，Redis 是传递通道。Job 状态以 DB 为准，不依赖 Celery result backend。
- `task_acks_late=True`：任务执行完成后才 ACK，Worker 崩溃时消息不会丢失。
- `task_reject_on_worker_lost=True`：Worker 进程意外终止时，未 ACK 的消息回到队列。
- `worker_prefetch_multiplier=1`：Worker 每次只取 1 个任务，不提前锁定队列。
- Worker 层使用 `NullPool`：每个任务独立创建 DB 连接，避免连接池跨进程复用问题。

---

## 所有失效场景枚举

### 场景 A：API 进程在 `db.commit()` 之后、`delay()` 之前崩溃

| 项 | 内容 |
|---|---|
| 触发条件 | API 进程 OOM、强杀或部署重启，恰好在两行代码之间 |
| DB 状态 | `status=queued`，`celery_task_id=NULL` |
| Redis 状态 | 无对应任务 |
| 结果 | Job 永久卡在 queued，上游轮询永远拿到 queued，不会失败也不会完成 |
| 防护 | **启动恢复扫描**：Worker 启动时扫描 `celery_task_id IS NULL` 的孤儿 Job 并重新投递 |

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

### 场景 D：Worker 在 AI 调用过程中崩溃（SIGKILL / OOM）

| 项 | 内容 |
|---|---|
| 触发条件 | Worker 进程被操作系统强杀（OOM、`kill -9`） |
| DB 状态 | `status=running` |
| Redis 状态 | 消息未 ACK |
| 结果 | `task_reject_on_worker_lost=True` → 消息回队列，任务被重新消费 |
| 附加风险 | 重新消费时如果 Job 已被软超时标记为 `failed`，`mark_running` 会把状态覆盖回 `running`，导致已终止的任务重新执行 |
| 防护 | ✅ 消息不丢；**需加终态幂等守卫**：重新消费时先检查状态，已是终态则跳过 |

### 场景 E：SoftTimeLimitExceeded（30 分钟软超时）

| 项 | 内容 |
|---|---|
| 触发条件 | 任务执行超过 `CELERY_SOFT_TIME_LIMIT`（默认 1800s） |
| 处理过程 | 收到 SIGALRM → 代码捕获 → `_mark_timeout()` → DB 标记 `status=failed`（JOB_TIMEOUT）→ 发 Callback → 抛出异常 |
| 结果 | Job 正确进入 failed 终态，Celery ACK 任务 |
| 防护 | ✅ 已有防护 |

### 场景 F：HardTimeLimitExceeded（31 分钟 SIGKILL）

| 项 | 内容 |
|---|---|
| 触发条件 | 软超时后 60 秒内未完成清理，Celery 发送 SIGKILL |
| DB 状态 | 软超时已将 Job 标记为 `failed` |
| Redis 状态 | SIGKILL 导致消息未 ACK → 回到队列 |
| 结果 | 任务被重新消费，`mark_running` 将 `failed` 覆盖为 `running`，Job 重新开始执行（无限循环风险） |
| 防护 | **终态幂等守卫**（同场景 D）：已是 `failed` 则跳过，不再执行 |

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

### 场景 J：长时间卡在 running，Worker 已死且未触发 reject

| 项 | 内容 |
|---|---|
| 触发条件 | 极端情况：Worker 进程异常但 Redis 连接未断开，消息既未 ACK 也未 reject |
| DB 状态 | `status=running`，`started_at` 已超过 `CELERY_TIME_LIMIT` |
| 结果 | Job 无限期停留在 running |
| 防护 | **定期僵死 running 扫描**：`started_at` 超过阈值则强制标记 `failed` |

---

## 防护机制汇总

| 场景 | 机制 | 实现位置 |
|------|------|---------|
| A / B：孤儿 queued Job | 启动恢复扫描 + 定期扫描 | `tasks/recovery.py` + Worker 启动信号 |
| C：取出前 Worker 崩溃 | `task_reject_on_worker_lost` | `celery_app.py`（已有） |
| D / F / I：重新消费已终态 Job | 终态幂等守卫 | `tasks/jobs.py` `_process()` |
| E：软超时 | SoftTimeLimitExceeded 捕获 | `tasks/jobs.py`（已有） |
| F：SIGKILL 循环 | 终态幂等守卫阻断 | `tasks/jobs.py` `_process()` |
| G：Redis 无持久化 | AOF 持久化 + 启动恢复扫描兜底 | 运维配置 + `tasks/recovery.py` |
| J：僵死 running | 定期扫描强制 fail | `tasks/recovery.py` + Beat |
| K：API 完全不可用 | 无状态，重启即恢复；已有 Job 不受影响 | 无需额外代码 |
| L：Worker 完全不可用 | 队列积压至 MAX_ACTIVE_JOBS 触发 503 保护；重启后扫描恢复 | `MAX_ACTIVE_JOBS` + `worker_ready` 扫描 |
| M：API + Worker 同时不可用 | 恢复无顺序依赖；关键前提 Redis AOF | 运维配置 + 启动恢复扫描 |

---

## 关键环境变量

| 变量 | 默认值 | 说明 | 风险 |
|------|--------|------|------|
| `CELERY_SOFT_TIME_LIMIT` | `1800` | 软超时（秒），触发 JOB_TIMEOUT | 必须小于 `CELERY_TIME_LIMIT` |
| `CELERY_TIME_LIMIT` | `1860` | 硬超时（秒），触发 SIGKILL | 与软超时差值建议 ≥ 60s |
| `MODEL_CALL_TIMEOUT_SECONDS` | `300` | 单次 AI 调用超时 | 应远小于 `CELERY_SOFT_TIME_LIMIT` |
| `CELERY_MAX_RETRIES` | `0` | 任务重试次数 | 设为 0 时软超时后不重试 |
| `JOB_ORPHAN_TIMEOUT_SECONDS` | `300` | queued 且无 celery_task_id 超过此秒数视为孤儿，触发恢复投递 | 过小会误判正常排队的 Job |
| `JOB_STALE_RUNNING_SECONDS` | `2460` | running 超过此秒数视为僵死，触发强制 fail | 建议 ≥ `CELERY_TIME_LIMIT` + 600 |
| `MAX_ACTIVE_JOBS` | `50` | API 层队列深度上限，超出返回 503 | 应与 Worker 并发数和预期排队量匹配 |
| `REDIS_URL` | — | Broker 和 Result Backend | 生产环境必须指向开启 AOF 的 Redis |

---

## 恢复机制设计

### 启动恢复（Worker 启动时自动触发）

Worker 进程就绪后通过 `worker_ready` 信号触发一次扫描：

1. **孤儿 Job 重投递**：`status=queued AND celery_task_id IS NULL AND created_at < now - JOB_ORPHAN_TIMEOUT_SECONDS`
2. **僵死 running 强制 fail**：`status=running AND started_at < now - JOB_STALE_RUNNING_SECONDS`

### 定期扫描（需运行 Celery Beat）

Beat 每 30 分钟触发一次 `jobs.recovery` 任务，逻辑同启动恢复，持续兜底。

### 终态幂等守卫

`_process()` 在执行 `mark_running` 前检查 Job 当前状态，若已是 `succeeded` 或 `failed` 则直接跳过，不再重新执行。防止场景 D / F / I 中重新消费覆盖终态。
