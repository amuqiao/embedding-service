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
  └─ 僵死 Job（running + 超时）→ mark_failed + deliver_callback（✅ P1-5 已修复）
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

**当前状态：P1 全部已修复，可上生产。** 原 11 项 P1 全部修复完成（最后修复 P1-2、P1-4、P1-7）。

### 横向扩展支持

| 组件 | 结论 | 前置条件 |
|------|------|---------|
| **API Pod** | ✅ 可直接横向扩展 | 切换 `STORAGE_BACKEND=aliyun_oss` |
| **Worker Pod** | ✅ 可直接横向扩展 | ~~P1-8~~、~~P1-3~~、~~P1-2~~（已全部修复）|

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

**P1-1：`mark_running` 无 CAS 保护，多 Worker 竞争时可能双执行** ✅ 已修复

- **位置**：`app/tasks/jobs.py`（`_process` → `JobRepo.mark_running_if_queued`）
- **问题**：`job.status in ("succeeded", "failed")` 软检查与 `mark_running` 之间没有行锁。多 Worker 同时领取同一任务时，各自通过检查，导致双重 AI 调用和双重 callback。
- **修复**：`mark_running_if_queued` 使用 CAS UPDATE（`WHERE status='queued'`），rowcount=0 时直接 skip。

---

**P1-2：dispatch 与 `celery_task_id` 写入之间存在窗口期，可能触发孤儿恢复重复执行** ✅ 已修复

- **位置**：`app/api/routes/jobs.py`、`app/repositories/job_repo.py`、`app/tasks/recovery.py`
- **问题**：原来两次 commit 之间（dispatch 后、celery_task_id 写入前）crash，task 在 Redis 但 celery_task_id=NULL，recovery 把该 Job 当孤儿重新 dispatch，导致双执行。
- **修复**：pre-generate `task_id = str(uuid.uuid4())`，在 job 创建事务内写入 celery_task_id，单次 commit 后再用 `apply_async(task_id=task_id)` dispatch，消除双执行窗口。补充 recovery 扫描"stuck-dispatched"场景（queued + celery_task_id 非 NULL + 超时），用 CAS 替换 task_id 并重新 dispatch；`mark_running_if_queued` CAS 保证即使原 task 仍在 Redis 也不会双执行。

---

**P1-3：stale running Job 强制 fail 无 CAS，多 Worker 并发启动时可能重复 deliver callback** ✅ 已修复

- **位置**：`app/tasks/recovery.py:48-57`
- **问题**：孤儿 Job 有 CAS 保护，但 stale Job 的 `mark_failed` 没有。多 Worker 同时启动时，可能对同一 Job 多次 deliver callback。
- **修复**：改用 `mark_failed_if_running`（CAS UPDATE，`WHERE status='running'`），rowcount=0 时 skip，callback 仅在 CAS 成功时触发。

---

**P1-4：`MAX_ACTIVE_JOBS` 积压计数存在 TOCTOU 竞争** ✅ 已修复

- **位置**：`app/services/jobs.py`（create_job）
- **问题**：N 个并发请求同时通过 count 检查，全部 insert，轻微超限。
- **修复**：在 count_active_jobs 调用前加 `pg_advisory_lock('max_active_jobs_gate')`，count 返回后立即 `pg_advisory_unlock`（try/finally 保证），锁范围仅覆盖 count check，不串行化整个 job 创建事务。

---

**P1-5：stale running Job 恢复后未触发 Callback，调用方静默失败** ✅ 已修复

- **位置**：`app/tasks/recovery.py:48-57`
- **问题**：`mark_failed` 后没有调用 `deliver_callback`，调用方永远收不到失败通知。
- **修复**：`mark_failed_if_running` CAS 成功后，`get_job_or_404` 刷新状态再调用 `deliver_callback`；callback 异常仅 log，不阻断 recovery 流程。

---

**P1-6：`/health` 和 `/healthz` 不检查 DB/Redis 连通性** ✅ 已修复

- **位置**：`app/api/routes/health.py`
- **问题**：只返回硬编码 `"status": "ok"`，DB 断连时实例仍接收流量但所有请求都 500，Kubernetes readiness probe 无法感知。
- **修复**：`/healthz` 已实现 DB `SELECT 1` + Redis TCP 握手，任一失败返回 503 + `{"status": "degraded"}`。

---

**P1-7：`CALLBACK_SIGNING_SECRET` 可为空，签名退化为无效 HMAC** ✅ 已修复

- **位置**：`app/services/callbacks.py`（`_sign`、`deliver_callback`）
- **问题**：空 key 下 HMAC 仍可计算，攻击者用空 key 即可伪造签名，签名保护完全失效。
- **修复**：`_sign` 空 key 时返回 `None`，`deliver_callback` 中仅在签名非 None 时才添加 `X-AI-Service-Signature` header，不发出可伪造的签名头；`config.py` 启动时已有 warning 日志提示未配置。

---

**P1-8：docker-compose.yml 硬编码 `--pool=solo --concurrency=1`，绕过 start-worker.sh 扩展逻辑** ✅ 已修复

- **位置**：`docker-compose.yml:84`
- **问题**：即使设置 `WORKER_POOL=threads`，compose 的 `command` 直接覆盖，扩容配置完全无效。
- **修复**：worker `command` 已改为 `["/app/start-worker.sh"]`，`WORKER_POOL` 和 `WORKER_CONCURRENCY` 通过 `environment` 注入。

---

**P1-9（安全）：Bearer Token 使用 `==` 比较，存在时序攻击风险** ✅ 已修复

- **位置**：`app/core/security.py:23`
- **修复**：已改为 `not secrets.compare_digest(credentials.credentials, settings.SERVICE_API_KEY)`。

---

**P1-10（安全）：`callback_url` SSRF 防护不覆盖私有网段** ✅ 已修复

- **位置**：`app/services/jobs.py`（`_is_private_host`）
- **问题**：仅拦截 localhost/127.0.0.1，`10.x`/`192.168.x`/`172.16.x`/`0.0.0.0` 均可绕过。
- **修复**：`_is_private_host` 使用 `ipaddress.ip_address` 检查 `is_private / is_loopback / is_link_local / is_unspecified`，覆盖全部私有网段 IP。注意：hostname 形式的内网地址（DNS rebinding）仍不覆盖，需在威胁模型中明确说明。

---

**P1-11（安全）：LocalObjectStorage 路径穿越未检查** ✅ 已修复

- **位置**：`app/infrastructure/storage.py`（`LocalObjectStorage._path`）
- **问题**：`key.lstrip("/")` 不阻止 `../` 片段，可逸出存储根目录。
- **修复**：`_path()` 已 resolve 路径后检查 `str(resolved).startswith(str(root_resolved))`，路径逸出时抛 422 `INVALID_INPUT`。

---

### P2 — 建议修复（上线前可延后）

| # | 问题 | 位置 | 建议 |
|---|------|------|------|
| ~~P2-1~~ | ~~stale Job `mark_failed` 自身异常可能覆盖原始异常~~ | ~~`tasks/jobs.py:59-61`~~ | ✅ 已修复：P1 修复轮中 `_fail()` 调用已用 try/except 包裹，保留原始 raise |
| ~~P2-2~~ | ~~callback URL 非法静默返回无日志~~ | ~~`callbacks.py:55-58`~~ | ✅ 已修复：`except ValueError` 补充 `logger.warning` 后返回 |
| ~~P2-3~~ | ~~Worker request_id 永远是 `-`，日志链路断链~~ | ~~`core/logging.py:7`~~ | ✅ 已修复：`process_job_task` 入口已调用 `set_request_id(job_id)` |
| ~~P2-4~~ | ~~cleanup 每月执行一次，TTL 24h 数据整月积累~~ | ~~`celery_app.py:33-38`~~ | ✅ 已修复：`beat_schedule` 已改为 `crontab(hour=2, minute=0)` 每天执行 |
| ~~P2-5~~ | ~~`cleanup_expired_jobs` 内部 commit，破坏分层一致性~~ | ~~`job_repo.py:280-285`~~ | ✅ 已修复：repo 层改为 `flush`，commit 由 task 层统一控制 |
| ~~P2-6~~ | ~~DB 连接池无显式配置，横向扩展时可能连接耗尽~~ | ~~`database.py:14`~~ | ✅ 已修复：新增 `DB_POOL_SIZE=5`、`DB_MAX_OVERFLOW=10`、`DB_POOL_RECYCLE=1800`；估算公式写入 config 注释和 `.env.example` |
| ~~P2-7~~ | ~~`cleanup_expired_jobs_task` 失败时 return，Beat 无法感知~~ | ~~`tasks/jobs.py:163-170`~~ | ✅ 已修复：当前实现无 silent return，异常自然向上抛出 |
| ~~P2-8~~ | ~~Worker 无健康检查机制~~ | ~~`start-worker.sh`~~ | ✅ 已修复：新增 `check-worker-health.sh`，使用 `celery inspect ping` 检查当前 Pod，可直接配为 K8s `livenessProbe.exec` |
| ~~P2-9~~ | ~~迁移 0001 中 `metadata_payload` 列 ORM 无映射，孤儿列~~ | ~~`0001_create_ai_jobs.py:34`~~ | ✅ 已修复：新增迁移 `0004_drop_metadata_payload.py` 删除该列 |
| ~~P2-10~~ | ~~`caller_id` 单列索引与复合索引冗余~~ | ~~`models/job.py:15`~~ | ✅ 已修复：去掉 `index=True`，依赖已有复合索引 `ix_ai_jobs_client_request(caller_id, client_request_id)` |
| ~~P2-11~~ | ~~`X-Request-ID` 无长度/格式校验，存在日志注入~~ | ~~`main.py:38`~~ | ✅ 已修复：正则 `^[a-zA-Z0-9\-_]{1,128}$` 校验，不符合时回退为随机 UUID |
| ~~P2-12~~ | ~~`.env.example` 写入了弱默认 key 值~~ | ~~`.env.example:4,37`~~ | ✅ 已修复：改为 `<替换为随机 token>` 和 `<替换为随机 32 字节 hex>` 占位符 |

---

## 四、生产配置推荐（P1 + P2 全部修复后）

```bash
# Worker 扩展配置（docker-compose 或 K8s env）
WORKER_POOL=threads
WORKER_CONCURRENCY=10         # 每 Pod 10 线程
# 部署 3 个 Worker Pod → 30 并发

# 队列门控
MAX_ACTIVE_JOBS=0             # 无限队列，或设为 worker数 × concurrency × 缓冲系数

# 孤儿超时（长队列场景需调大）
JOB_ORPHAN_TIMEOUT_SECONDS=600   # 默认 300，长队列建议 600+

# 数据库连接池（env 变量配置，API 侧）
# 公式：API pods × (pool_size + max_overflow) + Worker pods × concurrency ≤ PG max_connections(100)
# 示例：3 × (5+10) = 45 + 30 = 75，留余量
DB_POOL_SIZE=5
DB_MAX_OVERFLOW=10
DB_POOL_RECYCLE=1800

# 存储后端（多节点必须）
STORAGE_BACKEND=aliyun_oss

# 安全配置
CALLBACK_SIGNING_SECRET=<随机 32 字节 hex>
SERVICE_API_KEY=<随机 token>

# K8s Worker liveness probe
# livenessProbe.exec.command: ["/app/check-worker-health.sh"]
```

---

## 五、评分汇总

| 维度 | 评分 | 说明 |
|------|------|---------|
| 生产就绪性 | 9/10 | 所有 P1/P2 已修复；Worker 健康检查、DB 连接池均已到位 |
| 横向扩展 | 9/10 | API/Worker 均可横向扩展；DB 连接池估算公式已文档化 |
| 数据库层 | 9/10 | 连接池显式配置、孤儿列清理、分层 commit 一致性均已修复 |
| 安全性 | 9.5/10 | P1 安全项全修复；X-Request-ID 注入防护和 .env 占位符已补 |
| **综合** | **9/10** | 所有 P1（11 项）+ 所有 P2（12 项）均已修复，可上生产 |
