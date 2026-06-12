# Job 超时机制指南

本文完整讲解本服务的 Job 超时机制：为什么这样设计、每一层如何工作、重试如何运转、以及如何根据业务场景正确配置环境变量。

读完本文应能：独立判断超时配置是否合理、知道各层触发后 Job 的状态变化、在不改代码的情况下通过环境变量调整超时行为。

---

## 全景：一个 Job 的超时防线

```
Job 进入 running
│
│  ① asyncio.wait_for ──────────── MODEL_CALL_TIMEOUT_SECONDS（默认 300s）
│       │ 触发 asyncio.TimeoutError
│       ↓
│  [有剩余重试次数？]
│       │ 是 → self.retry(countdown=CELERY_RETRY_DELAY)
│       │       新 task 启动，所有计时器从 0 重置
│       │ 否 → mark_failed(JOB_TIMEOUT) → Callback ✓
│
│  ② Celery SIGALRM ─────────────── CELERY_SOFT_TIME_LIMIT（默认 1800s）
│       │ 触发 SoftTimeLimitExceeded（L1 失效时的兜底）
│       ↓ 同上，进入重试 or mark_failed
│
│  ③ Celery SIGKILL ─────────────── CELERY_TIME_LIMIT（默认 1860s）
│       │ 进程强杀，task_reject_on_worker_lost → 消息回队重新执行
│       │ 若 L1/L2 已标记 failed → 幂等守卫跳过 ✓
│       │ 若尚未标记 → 重新执行（新一轮超时计时）
│
│  ④ stale running 扫描 ─────────── JOB_STALE_RUNNING_SECONDS（默认 2460s）
│       Worker 重启时触发，强制将超时的 running Job 标记 failed
│
└─ 成功路径：AI 在 ① 触发前返回 → mark_succeeded → Callback ✓
```

---

## 第一层：asyncio.wait_for（主截断，最重要）

### 为什么需要它

OpenAI API 使用 chunked transfer encoding：服务端生成时持续发送小块数据，而不是等全部生成完再返回。

```
客户端                          OpenAI 服务端
  │                                  │
  │── POST /v1/chat/completions ────→│ 开始生成
  │                                  │ 生成 chunk 1（~2s）
  │←── chunk 1 ──────────────────────│
  │                                  │ 生成 chunk 2（~2s）
  │←── chunk 2 ──────────────────────│
  │          ...持续传输...           │
  │                                  │ 生成结束
  │←── 结束标记 ──────────────────────│
```

httpx 的 `read_timeout` 是**单次 socket read** 的等待时长，不是总时长。只要每隔几秒有一个 chunk 到达，read_timeout 就永远不会触发。配置 `timeout=600` 对长文本生成实际无效。

### 实现方式

```python
# app/infrastructure/ai_gateway.py
response = await asyncio.wait_for(
    litellm.acompletion(
        model=model.litellm_model,
        messages=messages,
        timeout=settings.MODEL_CALL_TIMEOUT_SECONDS,  # ← 泄漏线程的退出上限
        ...
    ),
    timeout=settings.MODEL_CALL_TIMEOUT_SECONDS,      # ← 主截断，协程级取消
)
```

`asyncio.wait_for` 是协程级取消，与 chunked 无关，是唯一可靠的总时长截断。

### 关于线程泄漏

`litellm.acompletion` 不是原生 async，底层是 `loop.run_in_executor(None, litellm.completion)`，即把同步调用丢入线程池。`asyncio.wait_for` 取消的是协程/Future，**不能终止已在运行的线程**。

触发后线程仍在运行，但受两层约束退出：

```
asyncio.wait_for 触发（T秒）
  → 协程取消，DB 连接正常释放（NullPool 保证）
  → 背景线程继续运行
      → litellm timeout（同样 T 秒）提供线程级退出上限
      → Celery SIGALRM（CELERY_SOFT_TIME_LIMIT）作为最终兜底
```

线程不持有 DB 连接（NullPool 每次操作独立连接），泄漏代价低。

---

## 第二层：litellm timeout（线程泄漏上限）

```python
litellm.acompletion(timeout=settings.MODEL_CALL_TIMEOUT_SECONDS, ...)
```

传给 litellm 的 `timeout` 最终作用于 httpx 的 `read_timeout`。对 chunked 响应无效，**不是主截断**。

存在的意义：在 asyncio.wait_for 触发后，为泄漏的背景线程提供同量级的退出上限，避免线程无限运行。

---

## 第三层：Celery SIGALRM（软超时兜底）

当 L1（asyncio.wait_for）因极端情况未能触发时（如 litellm 内部 bug、asyncio 异常），Celery 通过 Unix SIGALRM 信号中断进程内所有线程的阻塞 I/O。

```
CELERY_SOFT_TIME_LIMIT 到达
  → SIGALRM 发送给 worker 进程
  → 抛出 SoftTimeLimitExceeded（Python 信号处理）
  → 与 asyncio.TimeoutError 进入同一重试/终态逻辑
```

`SoftTimeLimitExceeded` 和 `asyncio.TimeoutError` 在代码中统一处理：

```python
# app/tasks/jobs.py
except (SoftTimeLimitExceeded, asyncio.TimeoutError) as exc:
    if self.request.retries >= settings.CELERY_MAX_RETRIES:
        asyncio.run(_mark_timeout(job_id))
        raise
    raise self.retry(exc=exc, countdown=settings.CELERY_RETRY_DELAY, ...)
```

---

## 第四层：Celery SIGKILL（硬超时）

```
CELERY_TIME_LIMIT 到达（= CELERY_SOFT_TIME_LIMIT + 60s）
  → 进程被强杀
  → task_reject_on_worker_lost=True → 消息回队
  → 两种结果：
      a. L1/L3 已标记 failed → 幂等守卫跳过，Job 安全
      b. 未来得及标记       → 消息重新消费，新一轮执行
```

SIGKILL 与 SIGALRM 之间的 60s 间隔是给 L3 处理善后（写 DB、发 Callback）留的时间。

---

## 重试机制

### 默认行为（不重试）

`CELERY_MAX_RETRIES=0`（默认）：超时后直接进入 failed 终态，立即发 Callback。

### 启用重试后的执行流程

```
第 1 次执行
  ├─ 成功 → mark_succeeded → Callback
  └─ 超时（MODEL_CALL_TIMEOUT_SECONDS）
       ├─ retries(0) < MAX_RETRIES(N)？是
       └─ 等待 CELERY_RETRY_DELAY 秒
            ↓
第 2 次执行（全新 Celery task）
  ├─ asyncio.wait_for 从 0 重新计时
  ├─ CELERY_SOFT_TIME_LIMIT 从 0 重新计时
  ├─ 成功 → mark_succeeded → Callback
  └─ 超时
       ├─ retries(1) < MAX_RETRIES(N)？是 → 继续重试
       └─ retries(N) >= MAX_RETRIES(N)？是
            → mark_failed(JOB_TIMEOUT) → Callback（唯一一次）
```

**关键特性：**

- 每次重试超时时间完整重置，不累计
- Job 在重试期间保持 `running` 状态，`started_at` 随每次执行刷新
- Callback 只在最终结果（成功或最终失败）时发出一次
- 调用方总等待上限公式：

```
总等待上限 = (MODEL_CALL_TIMEOUT_SECONDS + CELERY_RETRY_DELAY) × (CELERY_MAX_RETRIES + 1)
```

---

## 环境变量参考

| 变量 | 代码默认值 | 作用层 | 说明 |
|------|-----------|--------|------|
| `MODEL_CALL_TIMEOUT_SECONDS` | `300` | L1 主截断 | 单次 AI 调用的总时长上限；同时传给 litellm 作为线程泄漏退出上限 |
| `CELERY_SOFT_TIME_LIMIT` | `1800` | L3 兜底 | Celery SIGALRM 触发时间；必须 > `MODEL_CALL_TIMEOUT_SECONDS` |
| `CELERY_TIME_LIMIT` | `1860` | L4 强杀 | Celery SIGKILL 触发时间；必须 > `CELERY_SOFT_TIME_LIMIT`，差值建议 ≥ 60s |
| `CELERY_MAX_RETRIES` | `0` | 重试控制 | 超时后最大重试次数；0 表示不重试；同时控制 L1 和 L3 |
| `CELERY_RETRY_DELAY` | `60` | 重试间隔 | 两次重试之间等待秒数；仅 `CELERY_MAX_RETRIES > 0` 时有意义 |
| `JOB_STALE_RUNNING_SECONDS` | `2460` | L5 扫描 | Worker 重启时，started_at 超过此秒数的 running Job 被强制标记 failed |

### 硬性约束（启动时校验，违反则服务拒绝启动）

```
MODEL_CALL_TIMEOUT_SECONDS < CELERY_SOFT_TIME_LIMIT < CELERY_TIME_LIMIT
```

校验在 `app/infrastructure/config.py` 的 `validate_timeout_chain` 中实现，配置错误会在启动时立即暴露，不会静默运行。

---

## 配置场景

### 场景一：短文本（< 5000 字）

```env
MODEL_CALL_TIMEOUT_SECONDS=300
CELERY_SOFT_TIME_LIMIT=900
CELERY_TIME_LIMIT=960
CELERY_MAX_RETRIES=0
```

调用方最长等 300s，超时后立即 failed。

### 场景二：小说章节（当前 `.env.example` 默认）

```env
MODEL_CALL_TIMEOUT_SECONDS=600
CELERY_SOFT_TIME_LIMIT=1800
CELERY_TIME_LIMIT=1860
CELERY_MAX_RETRIES=0
```

调用方最长等 600s。`CELERY_SOFT_TIME_LIMIT` 留出 1200s 余量给 L1 失效时的兜底。

### 场景三：超长文本 / 慢代理

```env
MODEL_CALL_TIMEOUT_SECONDS=1200
CELERY_SOFT_TIME_LIMIT=2400
CELERY_TIME_LIMIT=2460
CELERY_MAX_RETRIES=0
```

调用方最长等 1200s（20 分钟）。

### 场景四：启用重试（模型过载场景）

```env
MODEL_CALL_TIMEOUT_SECONDS=600
CELERY_SOFT_TIME_LIMIT=1800
CELERY_TIME_LIMIT=1860
CELERY_MAX_RETRIES=1
CELERY_RETRY_DELAY=60
# 调用方总等待上限：(600 + 60) × 2 = 1320s
```

**注意**：若超时原因是输入过长，重试会再次超时，对调用方没有价值。重试适合模型服务临时过载的场景。

---

## 配置修改步骤

**第一步：确认场景**

| 如果... | 调整... |
|--------|---------|
| 短文本翻译，需要更快失败 | 降低 `MODEL_CALL_TIMEOUT_SECONDS` |
| 超长文本，当前频繁超时 | 提高 `MODEL_CALL_TIMEOUT_SECONDS` |
| 模型服务不稳定，需要自动重试 | 设置 `CELERY_MAX_RETRIES=1` 并确认总等待时长可接受 |

**第二步：保持约束成立**

调整任意值后，检查：

```
MODEL_CALL_TIMEOUT_SECONDS < CELERY_SOFT_TIME_LIMIT（建议差 ≥ 300s）
CELERY_SOFT_TIME_LIMIT < CELERY_TIME_LIMIT（建议差 ≥ 60s）
CELERY_TIME_LIMIT ≤ JOB_STALE_RUNNING_SECONDS（建议差 ≥ 600s）
```

**第三步：重启服务生效**

环境变量在进程启动时读取，修改后需重启 API 和 Worker：

```bash
./scripts/dev.sh restart
```

---

## 常见误区

**误区 1：以为 litellm `timeout=600` 控制了总时长**

实际上对 chunked 响应无效。真正的总时长截断只有 `asyncio.wait_for`（L1）。

**误区 2：以为 CELERY_SOFT_TIME_LIMIT 是主超时**

L3 是兜底，正常路径由 L1 负责。两者都触发，但 L1 先触发（600s vs 1800s）。

**误区 3：以为 CELERY_MAX_RETRIES 只控制 SoftTimeLimitExceeded**

修复后 `CELERY_MAX_RETRIES` 同时控制 L1（asyncio.TimeoutError）和 L3（SoftTimeLimitExceeded）。

**误区 4：CELERY_SOFT_TIME_LIMIT 设得和 MODEL_CALL_TIMEOUT_SECONDS 一样**

比如都设 600s：L1 触发后进入重试/终态逻辑的 DB 写入和 Callback 发送需要时间，此时 SIGALRM 也触发，两个超时叠加会导致状态写入不完整。`CELERY_SOFT_TIME_LIMIT` 至少要比 `MODEL_CALL_TIMEOUT_SECONDS` 大 300s。

**误区 5：启动后修改环境变量立即生效**

不生效。Settings 在进程启动时通过 `@lru_cache` 缓存，修改后必须重启进程。
