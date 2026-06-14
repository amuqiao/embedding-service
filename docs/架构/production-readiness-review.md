# 生产就绪性评审报告

本文基于当前代码、部署脚本和验证入口，给出面向上线决策的证据口径、前置条件和剩余风险。

评审日期：2026-06-14

## 文档职责

本文回答三个问题：

- 当前代码是否具备生产运行的基础控制。
- 哪些结论已有代码或脚本证据，哪些仍只是配置推演。
- 上线前必须补齐哪些环境、验证、安全和容量证据。

本文不替代生产发布方案、K8s 配置、云平台 Secret 管理、压测报告或事故预案。仓库当前维护边界仍是本地开发、compose 运行形态和 AI 能力层服务本身。

## 一、核心结论

**结论：当前服务达到“有条件生产就绪”，不应表述为“已完成生产验证”。**

代码层面已经具备异步 Job、幂等创建、Celery 投递补偿、Worker 多实例竞争保护、终态 Callback、DB/Redis readiness、对象存储抽象和本地/compose 验证入口。若生产环境满足本文列出的前置条件，可以进入测试环境或灰度发布。

但以下能力尚未被仓库内证据证明：

- 30 并发真实压测通过。
- 无限队列在目标资源容量下可长期稳定运行。
- K8s Deployment、Probe、Secret、滚动发布和回滚链路已接入。
- 目标环境真实模型、OSS、Callback 接收方验签和出站网络策略已端到端验证。

### 上线判断

| 维度 | 结论 | 说明 |
| --- | --- | --- |
| API 横向扩展 | 有条件支持 | API 无内存态；多 Pod 依赖共享 PostgreSQL、Redis 和对象存储。生产必须使用 `STORAGE_BACKEND=aliyun_oss` 或等价共享存储。 |
| Worker 横向扩展 | 有条件支持 | Job claim、终态写入、recovery 有 CAS / advisory lock 保护；仍需按 DB 连接数、模型并发额度和 Celery 参数做容量核算。 |
| 30 并发 Job | 可配置，不等于已验证 | `WORKER_POOL=threads` + `WORKER_CONCURRENCY` + 多 Worker Pod 可达到并发配置；仓库无压测脚本或结果。 |
| 无限队列 | 不建议这样表述 | `MAX_ACTIVE_JOBS=0` 只是关闭入口软限制；Redis、DB、Job TTL、对象存储和模型额度仍是硬约束。 |
| 安全基线 | 部分就绪 | Bearer token、常量时间比较、HTTPS callback、HMAC callback、request id 白名单已有；多调用方授权、callback hostname SSRF、生产 Secret 和网络策略仍需确认。 |
| 发布验证 | 本地和 compose 较完整 | `check`、`smoke`、`workflow-smoke`、`e2e`、`deploy check` 可覆盖本地/compose；缺目标环境 e2e、K8s 和压测证据。 |

## 二、系统运行模型

本服务是小说本地化 AI 能力层，不是业务流程系统。调用方提交单个 Job，本服务异步执行模型任务、写入对象存储产物，并通过查询和 Callback 交付终态。

```text
业务后端 / BFF
  │
  │ POST /api/v1/novel-localization-ai/jobs
  ▼
FastAPI API
  ├─ Bearer token 鉴权
  ├─ Prompt / model / callback / OSS 输入校验
  ├─ client_request_id 幂等保护
  ├─ MAX_ACTIVE_JOBS 入口门控
  ├─ 创建 ai_jobs(status=queued)
  ├─ 预生成 celery_task_id 并提交
  └─ apply_async 投递 jobs.process
        │
        ▼
Celery Worker
  ├─ 按 celery_task_id claim queued Job
  ├─ 读取对象存储输入
  ├─ 调用 LiteLLM / OpenAI 兼容模型
  ├─ 写回大文本 artifacts
  ├─ 标记 succeeded / failed
  └─ 补偿投递 Callback

Worker recovery loop
  ├─ queued + celery_task_id IS NULL：重新分配 task_id 并投递
  ├─ queued + celery_task_id 非空 + celery_published_at IS NULL：替换 task_id 并投递
  ├─ stale running：CAS 标记 failed 并补发 callback
  ├─ due callbacks：补偿投递 callback
  └─ expired jobs：清理过期记录
```

关键约束：

```text
MODEL_CALL_TIMEOUT_SECONDS < CELERY_SOFT_TIME_LIMIT < CELERY_TIME_LIMIT < JOB_STALE_RUNNING_SECONDS
```

该超时链已在配置加载阶段校验，配置错误会启动失败。

## 三、已具备的生产控制

| 控制项 | 当前证据 | 评审结论 |
| --- | --- | --- |
| Job 幂等 | `JobRepo.advisory_lock_for_client_request()` 对 `caller_id + client_request_id` 加事务级 advisory lock；24 小时内返回已有 Job。 | 单调用方场景可用。多调用方场景需先改造 `caller_id` 来源。 |
| 投递一致性 | `POST /jobs` 先写入 `celery_task_id` 并提交，再 `apply_async`，成功后记录 `celery_published_at`；recovery 覆盖未发布窗口。 | 能覆盖 API commit 后、publish 前崩溃等常见窗口。 |
| Worker 双执行保护 | `mark_running_if_queued()` 使用 `WHERE status='queued' AND celery_task_id=:task_id` 抢占；终态写入也校验 running + task_id。 | 支持多 Worker 并发竞争下避免同一 task_id 双终态。 |
| stale running 处理 | recovery 使用 `mark_failed_if_running()` 后再补发 Callback。 | 可避免多 Worker recovery 重复终态通知。 |
| Callback 补偿 | `callback_status`、`callback_attempts`、`callback_next_retry_at` 和 recovery due callback 扫描已建模。 | 具备失败重试与补偿框架。 |
| 健康检查 | `/health` 返回基础存活；`/healthz` 实际检查 DB `SELECT 1` 和 Redis `ping`，失败返回 503。 | 生产 readiness 应接 `/healthz`，liveness 可接 `/health`。 |
| DB 连接池 | API 侧有 `DB_POOL_SIZE`、`DB_MAX_OVERFLOW`、`DB_POOL_RECYCLE`；Worker 侧使用 `NullPool`。 | 可估算横向扩容连接数，但需要目标 PG 配额确认。 |
| Worker 扩展入口 | `docker-compose.yml` 使用 `/app/start-worker.sh`；脚本支持 `WORKER_POOL`、`WORKER_CONCURRENCY`、`WORKER_MAX_TASKS_PER_CHILD`。 | 扩展参数已从硬编码中释放。 |
| 对象存储 | `local` 和 `aliyun_oss` 后端已抽象；Aliyun OSS 模式校验 bucket / region。 | 多节点生产必须使用共享对象存储。 |
| 安全基线 | Bearer token 使用 `secrets.compare_digest()`；Callback 支持 HMAC；API `X-Request-ID` 有长度和格式白名单。 | 基线可用，但仍有生产前置安全项。 |

## 四、生产前置条件

上线前必须把以下事项作为发布准入条件，而不是上线后的观察项。

### 1. 明确调用方信任边界

当前 `require_service_auth()` 验证单个 `SERVICE_API_KEY` 后返回固定 `caller_id="default"`。`GET /jobs/{job_id}` 按 UUID 查询，不按真实租户或调用方隔离。

因此只能按 **单调用方、单信任域、服务端到服务端** 模式上线。若未来多个业务方、多个租户或多个环境共用同一服务，必须先改造鉴权和 `caller_id` 派生规则，并在查询路径加入调用方隔离。

### 2. 生产必须使用共享对象存储

`local` 存储只适合本地或单机 compose。API Pod 和 Worker Pod 横向扩展时，输入和输出产物必须位于共享对象存储。

生产配置至少需要确认：

```bash
STORAGE_BACKEND=aliyun_oss
OSS_BUCKET=<目标 bucket>
OSS_REGION=<目标 region>
OSS_PROJECT_ROOT=<服务隔离前缀>
OSS_OUTPUT_PREFIX=novel-localization/jobs
```

若任何生产形态仍使用 `STORAGE_BACKEND=local`，不应判定为多节点生产就绪。

### 3. Callback 安全必须由配置和平台共同闭环

代码已要求生产 callback 默认使用 HTTPS，且会拒绝字面量私网、环回、链路本地和 unspecified IP；但 hostname 会在运行时解析，当前代码没有对解析后的 IP 做私网判断，也没有域名 allowlist。

上线前需要完成：

- `CALLBACK_SIGNING_SECRET` 必须非空。
- Callback 接收方必须强制验签，不只记录签名头。
- `ALLOW_INSECURE_CALLBACKS=false`。
- 平台出站网络策略阻断 metadata 地址、私网地址和不允许的域名。
- 若 callback 域名集合固定，优先增加 allowlist。

### 4. K8s / 运行平台探针必须实际接入

仓库提供 `/healthz` 和 `check-worker-health.sh`，但这不是平台接入证据。

生产平台应至少接入：

```text
API livenessProbe  -> GET /health
API readinessProbe -> GET /healthz
Worker livenessProbe.exec -> /app/check-worker-health.sh
```

当前 `docker-compose.yml` 的 API healthcheck 仍访问 `/health`，只能证明进程存活，不能证明 DB/Redis readiness。

### 5. 容量必须按目标环境核算

30 并发不能只看 Worker 数量，还必须同时核算：

```text
API pods × (DB_POOL_SIZE + DB_MAX_OVERFLOW)
+ Worker pods × WORKER_CONCURRENCY
≤ PostgreSQL max_connections - 运维预留连接
```

还需要确认：

- Redis broker 内存和持久化策略。
- 模型服务并发额度、速率限制和超时配置。
- OSS 吞吐、对象大小和生命周期规则。
- Callback 接收方吞吐和重试承受能力。

## 五、剩余风险清单

| 优先级 | 风险 | 影响 | 建议 |
| --- | --- | --- | --- |
| P0 | 多调用方 / 多租户场景下只有共享 Bearer token 和固定 `caller_id`。 | 可能跨调用方查询 Job，幂等空间也会共享。 | 单调用方上线可接受；多调用方上线前必须改造鉴权和查询隔离。 |
| P0 | Callback hostname SSRF 未在代码内闭环。 | 持有 API key 的调用方可提交 HTTPS 域名，由 Worker 出站访问。 | 增加 allowlist 或运行时 DNS 解析后私网拦截，并配合平台 egress policy。 |
| P0 | 目标环境 e2e、K8s 探针、Secret 注入和压测证据缺失。 | 无法证明生产发布链路和 30 并发真实可用。 | 灰度前补齐目标环境验证附件。 |
| P1 | `MAX_ACTIVE_JOBS` 门控只锁住 count，未覆盖后续 insert。 | 高并发建单时可能轻微超过软限制。 | 若该限制用于严格容量保护，应把 count + create 放在同一锁范围，或使用数据库约束/配额表。 |
| P1 | `MAX_ACTIVE_JOBS=0` 不是无限队列能力。 | 长队列会受到 Redis 内存、DB TTL、清理任务、模型额度影响。 | 文档和配置中避免使用“无限队列”承诺，改为容量规划后的有限队列。 |
| P1 | `cleanup_expired_jobs()` 删除所有过期 Job，不区分 queued / running。 | 排队超过 24 小时的 Job 可能被清理。 | 长队列场景需调整 TTL 或只清理终态 Job。 |
| P1 | `queued + celery_task_id 非空 + celery_published_at 非空` 且 Redis 消息丢失时无扫描恢复。 | 极端 broker 丢消息可能导致 Job 长期 queued。 | 增加 published queued 超时扫描，或启用更强 broker 持久化与监控告警。 |
| P1 | 本地存储路径校验使用字符串前缀判断。 | `local` 生产形态下仍需复核路径逃逸边界。 | 改为 `Path.is_relative_to()`；生产禁用 `local` 后端。 |
| P2 | recovery 日志未统一绑定 request id。 | 补偿路径排障关联性较弱。 | 在 recovery 循环中按 job_id 设置日志上下文。 |
| P2 | `workflow-smoke` 能验证长文本链路，但分块 workflow 不应视作已生产化开关。 | 打开 `NOVEL_LOCALIZATION_CHUNKING_ENABLED` 可能遇到未覆盖路径。 | 分块/merge 作为独立上线项补设计和回归。 |

## 六、30 并发配置口径

如果目标是 30 个同时运行的 Job，可以采用以下配置作为起点：

```bash
WORKER_POOL=threads
WORKER_CONCURRENCY=10
# 部署 3 个 Worker Pod，理论执行并发为 3 × 10 = 30
```

但报告只能把它写成 **可配置到 30 并发**，不能写成 **已验证支持 30 并发**。要把结论升级为已验证，需要至少补齐：

- 30 并发压测脚本和结果。
- 目标环境 PG 连接数、Redis 内存、OSS 吞吐、模型 API 配额记录。
- 任务成功率、P95/P99 耗时、失败重试、Callback 成功率。
- Worker 重启、API 重启、Redis/DB 短暂不可用等扰动下的恢复结果。

## 七、队列容量口径

`MAX_ACTIVE_JOBS=0` 表示关闭入口 active Job 软限制，不表示无限队列。

生产建议使用明确容量目标，例如：

```text
最大排队深度：N 个 queued Job
最大执行并发：M 个 running Job
最长排队时间：T 小时
Job TTL：大于最长排队时间 + 最长执行时间 + 查询保留时间
Redis 内存：可容纳峰值 Celery 消息和 result backend 数据
```

在没有压测和容量模型前，建议保留 `MAX_ACTIVE_JOBS`，并按业务可接受的排队时间设置，而不是直接设为 `0`。

## 八、可执行验证入口

本节列出仓库内已经维护的验证入口。它们能证明本地或 compose 形态的代码和链路表现，但不能替代目标环境发布证据。

### 本地基线

```bash
./scripts/dev.sh check
```

### 本地运行链路

```bash
./scripts/dev.sh start
curl -fsS http://127.0.0.1:8100/health
curl -fsS http://127.0.0.1:8100/healthz
./scripts/dev.sh smoke
./scripts/dev.sh workflow-smoke
./scripts/dev.sh e2e --input-file .data/test_novel.txt
./scripts/dev.sh stop
```

### compose 运行形态

```bash
./scripts/deploy.sh check
ENV_FILE=.env.dev ./scripts/deploy.sh up compose-full
curl -fsS http://127.0.0.1:8100/health
curl -fsS http://127.0.0.1:8100/healthz
BASE_URL=http://127.0.0.1:8100 ./.venv/bin/python scripts/e2e_backend_call.py \
  --input-file .data/test_novel.txt \
  --service-api-key '<目标 env 的 key>' \
  --callback-signing-secret '<目标 env 的 secret>'
./scripts/deploy.sh down compose-full
```

### OSS 连通性

```bash
./.venv/bin/python scripts/check_aliyun_oss.py --env-file .env.dev
```

## 九、目标环境准入证据

目标环境发布前应归档：

- `e2e_backend_call.py` 生成的 `e2e_report.json`。
- 目标环境 Job ID、artifact OSS 路径、Callback 接收日志和验签结果。
- API `/healthz` readiness 截图或日志。
- Worker `check-worker-health.sh` 探针接入证据。
- 30 并发压测报告或明确“不按 30 并发承诺上线”的发布说明。
- Secret 注入、`ALLOW_INSECURE_CALLBACKS=false`、`STORAGE_BACKEND=aliyun_oss` 的配置截图或审计记录。

## 十、评审口径

本文不把修复状态、配置能力和目标环境验证混为同一类结论。

本报告采用以下口径：

- “可上生产”表述为“有条件生产就绪”。
- “30 并发支持”表述为“可配置到 30 并发，尚缺压测证据”。
- “无限队列支持”表述为“关闭软限制不等于无限队列，需要容量模型”。
- “安全已修复”表述为“代码基线可用，仍需多调用方授权、callback egress 和生产 Secret 闭环”。
- “健康检查已完成”表述为“代码有 `/healthz`，平台必须实际接入 readiness”。

## 十一、最终建议

建议按以下路径推进上线：

1. 先以单调用方、单信任域、`aliyun_oss` 存储、HTTPS + HMAC Callback 的方式进入测试环境。
2. 在测试环境补齐真实模型 e2e、OSS artifact、Callback 验签、`/healthz` readiness 和 Worker liveness 证据。
3. 在目标并发下做压测；未完成压测前，不对外承诺 30 并发和无限队列。
4. 若要服务多个调用方或租户，先改造鉴权、`caller_id` 和查询隔离，再重新评审。
5. 灰度上线时保留 `MAX_ACTIVE_JOBS`，按容量模型逐步放量。
