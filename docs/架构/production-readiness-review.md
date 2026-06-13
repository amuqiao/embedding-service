# 生产就绪性评审报告

## 评审目标

- 是否可以上生产
- 是否支持 API Pod 和 Worker Pod 横向扩展
- 是否支持 30 并发 Job 执行
- 是否支持无限队列

评审日期：2026-06-13

---

## 一、系统心智模型

### 对象边界

本服务是 **无状态 AI 执行网关**，不持有用户状态、不做业务编排，只负责：接收 Job 请求 → 异步执行 AI 模型 → 写入产物 → 回调通知。

### 主干结构

```
调用方
  │ POST /jobs
  ▼
FastAPI（无状态）
  ├─ 幂等性保护（advisory lock + client_request_id）
  ├─ 积压门控（MAX_ACTIVE_JOBS）
  ├─ 写 DB（status=queued）
  └─ dispatch → Celery（Redis broker）
           │
           ▼
      Celery Worker
           ├─ mark_running（DB）
           ├─ asyncio.wait_for → LiteLLM → AI 模型
           ├─ 写产物（OSS / 本地存储）
           ├─ mark_succeeded（DB）
           └─ deliver_callback（HTTP + HMAC 签名，最多 4 次重试）

Worker 启动时 recovery.py：
  ├─ 孤儿 Job（queued + celery_task_id=NULL + 超时）→ CAS re-dispatch
  └─ 僵死 Job（running + 超时）→ mark_failed（⚠️ 缺 callback，见 P1-5）
```

### 超时五层约束（强制校验）

```
MODEL_CALL_TIMEOUT(600s) < CELERY_SOFT(1800s) < CELERY_HARD(1860s) < JOB_STALE(2460s)
```

### 关键变体

| 维度 | 当前配置 | 生产目标配置 |
|------|---------|------------|
| Worker 池 | solo（1并发/pod） | threads |
| Worker 并发 | 1 | 10（可调） |
| Worker Pod 数 | 1 | N（水平扩展） |
| 存储后端 | local（单机） | aliyun_oss（多节点） |
| API Pod 数 | 1 | N（已支持） |

---

## 二、核心结论

### 是否可上生产

**当前状态：不建议直接上生产。** 存在 6 个 P1 功能 Bug 和 4 个 P1 安全缺陷，修复后可上。

### 横向扩展支持

| 组件 | 结论 | 前置条件 |
|------|------|---------|
| **API Pod** | ✅ 可直接横向扩展 | 切换 `STORAGE_BACKEND=aliyun_oss` |
| **Worker Pod** | ⚠️ 需修复后可扩展 | 修复 docker-compose 硬编码（P1-8）+ CAS 状态保护（P1-2、P1-3） |

### 30 并发支持

✅ **纯配置变更即可达到**（修复 docker-compose 后）：

```bash
WORKER_POOL=threads
WORKER_CONCURRENCY=10
# 部署 3 个 Worker Pod → 3 × 10 = 30 并发
MAX_ACTIVE_JOBS=0  # 或根据需求设置
```

DB 连接：Worker 侧 NullPool，30 并发 = 30 个并发 DB 连接，PG 默认 `max_connections=100` 可承受。

### 无限队列支持

✅ **Redis List 无长度上限，支持无限队列。**

- `MAX_ACTIVE_JOBS=0` 可禁用软限制
- 注意：长队列场景需调大 `JOB_ORPHAN_TIMEOUT_SECONDS`，避免排队中的 Job 被误判为孤儿重复 dispatch

---

## 三、问题列表

### P1 — 必须修复（阻塞生产）

---

**P1-1：`mark_running` 无 CAS 保护，多 Worker 竞争时可能双执行**

- **位置**：`app/tasks/jobs.py:69-72`
- **问题**：`job.status in ("succeeded", "failed")` 软检查与 `mark_running` 之间没有行锁。多 Worker 同时领取同一任务时，各自通过检查，导致双重 AI 调用和双重 callback。
- **修复**：`mark_running` 改为 CAS UPDATE：
  ```sql
  UPDATE ai_jobs SET status='running' WHERE id=:id AND status='queued' RETURNING id
  ```
  rowcount=0 时 skip。

---

**P1-2：dispatch 与 `celery_task_id` 写入之间存在窗口期，可能触发孤儿恢复重复执行**

- **位置**：`app/services/jobs.py`（create_job 调用链）
- **问题**：DB commit 后 API 进程 crash，`celery_task_id` 未写入，Recovery 把该 Job 当孤儿重新 dispatch，原任务还在 Redis 中，导致双执行。另外 `db.begin()` 嵌套可能导致 `celery_task_id` 永远写不进 DB（SQLAlchemy 2.x asyncio 下会抛 `InvalidRequestError`）。
- **修复**：先在本地生成 `task_id = str(uuid.uuid4())`，写入 DB 并 commit，再用 `apply_async(task_id=task_id)` dispatch。

---

**P1-3：stale running Job 强制 fail 无 CAS，多 Worker 并发启动时可能重复 deliver callback**

- **位置**：`app/tasks/recovery.py:38-48`
- **问题**：孤儿 Job 有 CAS 保护，但 stale Job 的 `mark_failed` 没有。多 Worker 同时启动时，可能对同一 Job 多次 deliver callback。
- **修复**：`mark_failed` 前加 CAS，仅 rowcount=1 时 deliver callback。

---

**P1-4：`MAX_ACTIVE_JOBS` 积压计数存在 TOCTOU 竞争**

- **位置**：`app/services/jobs.py:106-114`
- **问题**：N 个并发请求同时通过 count 检查，全部 insert，轻微超限。
- **修复**：接受小幅超限并文档说明（软限制有 ±N 误差）；或加 advisory lock 序列化。

---

**P1-5：stale running Job 恢复后未触发 Callback，调用方静默失败**

- **位置**：`app/tasks/recovery.py:41-48`
- **问题**：`mark_failed` 后没有调用 `deliver_callback`，调用方永远收不到失败通知。
- **修复**：在 commit 后补 `await deliver_callback(job)`。

---

**P1-6：`/health` 和 `/healthz` 不检查 DB/Redis 连通性**

- **位置**：`app/api/routes/health.py:6-9`
- **问题**：只返回硬编码 `"status": "ok"`，DB 断连时实例仍接收流量但所有请求都 500，Kubernetes readiness probe 无法感知。
- **修复**：`/healthz` 做 `SELECT 1` 和 Redis `PING`，失败返回 503。

---

**P1-7：`CALLBACK_SIGNING_SECRET` 可为空，签名退化为无效 HMAC**

- **位置**：`app/infrastructure/config.py:36`，`app/services/callbacks.py:18-24`
- **问题**：空 key 下 HMAC 仍可计算，攻击者用空 key 即可伪造签名，签名保护完全失效。
- **修复**：`model_validator` 中检查非空；或在 `_sign` 调用前 guard 并记录 warning。

---

**P1-8：docker-compose.yml 硬编码 `--pool=solo --concurrency=1`，绕过 start-worker.sh 扩展逻辑**

- **位置**：`docker-compose.yml:88-97`
- **问题**：即使设置 `WORKER_POOL=threads`，compose 的 `command` 直接覆盖，扩容配置完全无效。
- **修复**：将 worker command 改为 `["/app/start-worker.sh"]`，通过 `environment` 传入 `WORKER_POOL` 和 `WORKER_CONCURRENCY`。

---

**P1-9（安全）：Bearer Token 使用 `==` 比较，存在时序攻击风险**

- **位置**：`app/core/security.py:22`
- **修复**：改为 `not secrets.compare_digest(credentials.credentials, settings.SERVICE_API_KEY)`。

---

**P1-10（安全）：`callback_url` SSRF 防护不覆盖私有网段**

- **位置**：`app/services/jobs.py:85-93`，`app/services/callbacks.py:27-33`
- **问题**：仅拦截 localhost/127.0.0.1，`10.x`/`192.168.x`/`172.16.x`/`0.0.0.0` 及 HTTPS 方案均可绕过。
- **修复**：对 hostname 做私有网段黑名单检查；或明确文档化"信任内部调用方"的威胁模型。

---

**P1-11（安全）：LocalObjectStorage 路径穿越未检查**

- **位置**：`app/infrastructure/storage.py:24-26`
- **问题**：`key.lstrip("/")` 不阻止 `../` 片段，可逸出存储根目录。
- **修复**：`_path()` 中用 `resolved.is_relative_to(self.root.resolve())` 校验。

---

### P2 — 建议修复（上线前可延后）

| # | 问题 | 位置 | 建议 |
|---|------|------|------|
| P2-1 | stale Job `mark_failed` 自身异常可能覆盖原始异常 | `tasks/jobs.py:59-61` | try/except 包裹后 log，保留原始 raise |
| P2-2 | callback URL 非法静默返回无日志 | `callbacks.py:55-58` | 补 warning 日志 |
| P2-3 | Worker request_id 永远是 `-`，日志链路断链 | `core/logging.py:7` | 入口处 `set_request_id(job_id)` |
| P2-4 | cleanup 每月执行一次，TTL 24h 数据整月积累 | `celery_app.py:33-38` | 改为每天凌晨 2 点 |
| P2-5 | `cleanup_expired_jobs` 内部 commit，破坏分层一致性 | `job_repo.py:280-285` | 去掉内部 commit，由 task 层控制 |
| P2-6 | DB 连接池无显式配置，横向扩展时可能连接耗尽 | `database.py:14` | 显式配置 pool_size/max_overflow，记录估算公式 |
| P2-7 | `cleanup_expired_jobs_task` 失败时 return，Beat 无法感知 | `tasks/jobs.py:163-170` | 改为 raise |
| P2-8 | Worker 无健康检查机制 | `start-worker.sh` | 配置 `celery inspect ping` 作为 liveness probe |
| P2-9 | 迁移 0001 中 `metadata_payload` 列 ORM 无映射，孤儿列 | `0001_create_ai_jobs.py:34` | 补迁移删除该列或说明保留意图 |
| P2-10 | `caller_id` 单列索引与复合索引冗余 | `models/job.py:15` | 去掉 `index=True`，依赖复合索引 |
| P2-11 | `X-Request-ID` 无长度/格式校验，存在日志注入 | `main.py:38` | 限制长度 + 格式校验 |
| P2-12 | `.env.example` 写入了弱默认 key 值 | `.env.example:4,37` | 改为 `<替换为随机 token>` 占位符 |

---

## 四、生产配置推荐（修复 P1 后）

```bash
# Worker 扩展配置（docker-compose 或 K8s env）
WORKER_POOL=threads
WORKER_CONCURRENCY=10         # 每 Pod 10 线程
# 部署 3 个 Worker Pod → 30 并发

# 队列门控
MAX_ACTIVE_JOBS=0             # 无限队列，或设为 worker数 × concurrency × 缓冲系数

# 孤儿超时（长队列场景需调大）
JOB_ORPHAN_TIMEOUT_SECONDS=600   # 默认 300，长队列建议 600+

# 数据库连接池（DATABASE_URL 参数）
# pool_size=20&max_overflow=30&pool_recycle=1800

# 存储后端（多节点必须）
STORAGE_BACKEND=aliyun_oss

# 安全配置
CALLBACK_SIGNING_SECRET=<随机 32 字节 hex>
SERVICE_API_KEY=<随机 token>
```

---

## 五、评分汇总

| 维度 | 评分 | 关键缺口 |
|------|------|---------|
| 生产就绪性 | 6.5/10 | P1-5、P1-6、P1-7 |
| 横向扩展 | 5/10 | P1-8（docker-compose 硬编码）、P1-1（CAS） |
| 数据库层 | 6/10 | P1-2（db.begin 嵌套）、P1-3（状态机无硬保护） |
| 安全性 | 7/10 | P1-9、P1-10、P1-11 |
| **综合** | **6/10** | 修复所有 P1 后预计 8.5/10 |
