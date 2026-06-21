# Taskiq Job MVP 数据模型设计

```text
Version: 1.2.0
Status: Accepted / Development Baseline
Date: 2026-06-21
Change Policy: 进入开发后，任何 API 合同、状态机、表结构、CAS 条件、重试语义变更都必须升版本并记录变更原因。
Change Reason: 修正发布失败计数、lease 有效期、取消与超时收敛、Callback 安全合同，并裁剪 MVP 数据字段。
```

本文定义移除 Celery、改用 Taskiq 后的 Job MVP 心智模型、数据模型和实现骨架。

核心结论：

- **PostgreSQL 是 Job 的唯一事实源**。
- **Taskiq 只负责异步投递和执行，不承载业务状态、编排状态或对外查询结果**。
- **Job 是第一版的最小执行单元**，不引入 `job_steps`。
- **对外只暴露 Job 状态和结果投影**，Attempt、Lease、Worker、Callback 投递账本都属于服务内部。
- **服务重启不会直接决定 Job 成败**；Job 是否恢复、重试或失败，统一由 PostgreSQL 状态、Attempt lease、CAS 条件和 Reconciler 收敛。

## 一、MVP 边界

### 目标

第一版 Job MVP 要解决以下问题：

- 调用方可以提交一个 AI Job。
- 调用方可以用 `job_id` 轮询 Job 状态和终态结果。
- 调用方可以选择配置终态 Callback。
- 服务可以可靠处理 Taskiq 消息重复、消息丢失、Worker 崩溃、运行超时、Callback 失败和终态软删除。
- 服务可以在不依赖 Taskiq result backend 的情况下恢复 Job。

### 非目标

第一版不做以下能力：

- 不做 `job_steps`、DAG、chain、group、chord 或 step 级恢复。
- 不承诺强杀运行中的外部 LLM 请求。
- 不默认对真实 LLM 调用做立即自动 retry。
- 不把 Taskiq result backend 作为业务状态源。
- 不暴露 Attempt、Worker、Lease、Taskiq message id 给普通调用方。
- 不为了“更稳”吞错、静默降级或返回伪成功；异常应明确暴露并进入可诊断状态。

## 二、心智模型1：调用者视角

调用方把本服务理解为一个异步 AI 能力层，而不是业务流程编排系统。

```text
调用方
  ├─ 自己决定业务流程、权限、项目状态和业务重试策略
  ├─ 提交单个 Job
  ├─ 拿到 job_id
  ├─ 轮询 GET /jobs/{job_id}
  └─ 可选接收终态 Callback
```

调用方只需要理解两个返回：

- **创建返回**：`POST /jobs` 同步返回 `job_id` 和当前 Job 投影。
- **结果返回**：`GET /jobs/{job_id}` 或 Callback 返回终态结果。

### 调用者侧全情况枚举

| 阶段       | 情况                                 | 对调用方的结果                                          |
| -------- | ---------------------------------- | ------------------------------------------------ |
| 创建前      | 鉴权失败                               | 401 / 403，不创建 Job                                |
| 创建前      | `job_type` 不存在                     | 400 / 422，不创建 Job                                |
| 创建前      | `job_params` 校验失败                  | 422，不创建 Job                                      |
| 创建前      | `callback_url` 非法或不满足安全策略        | 422，不创建 Job                                      |
| 创建时      | 无 `idempotency_key`                | 每次创建新 Job                                        |
| 创建时      | 有 `idempotency_key`，fingerprint 相同 | 返回已有 Job                                         |
| 创建时      | 有 `idempotency_key`，fingerprint 不同 | 409 conflict                                     |
| 创建后      | 调用方 HTTP 超时，但服务已提交事务               | 调用方用同一个 `idempotency_key` 重试并拿回同一 Job            |
| 创建后      | 调用方 HTTP 超时且没有 `idempotency_key`   | 再次提交可能创建重复 Job                                   |
| 查询中      | `queued`                           | Job 已接受，等待发布或执行                                  |
| 查询中      | `running`                          | active attempt 已被 Worker claim                   |
| 查询终态     | `succeeded`                        | 返回 `result` / `result_ref`                       |
| 查询终态     | `failed`                           | 返回稳定 `error`                                     |
| 查询终态     | `cancelled`                        | 返回取消信息；第一版取消默认只走内部 API                           |
| Callback | 未配置 callback                       | 调用方只能轮询                                          |
| Callback | 配置 callback，但事件不在订阅范围              | Job 终态不变，callback 聚合状态为 `skipped`                |
| Callback | 首次投递或重试中                           | Job 终态不变，callback 聚合状态为 `pending` / `delivering` |
| Callback | 投递成功                               | callback 聚合状态为 `delivered`                       |
| Callback | 超过最大重试次数                           | callback 聚合状态为 `failed`，Job 终态不变                 |
| 过期后      | Job 已 soft delete                  | API 应返回 404 或 410；具体由 API 合同确认                   |

### 调用方 API 合同

`POST /jobs` 请求：

```text
job_type
job_params
idempotency_key optional
callback_url optional
callback_events optional
priority optional
```

`POST /jobs` 请求默认值：

```text
callback_events:
  未配置 callback_url 时规范化为空数组。
  配置 callback_url 但未传 callback_events 时，默认订阅 ['job.succeeded', 'job.failed']。
  显式传空数组表示不订阅任何终态事件；Job 终态后仍写 callback_outbox(status='skipped') 用于审计。
  MVP 只允许 'job.succeeded'、'job.failed'。`job.cancelled` 只写 skipped 审计，不对外投递。

priority:
  未传时规范化为 'normal'。
  MVP 枚举为 'low'、'normal'、'high'。
```

### `job_type` registry 最小合同

每个可执行 `job_type` 必须在服务端 registry 中显式注册。未注册或缺少必填配置时，`POST /jobs` 必须返回 400 / 422 且不创建 Job。

```text
name:
  对外 job_type。

params_schema:
  job_params 校验和规范化规则。

canonical_job_params:
  request_fingerprint 使用的参数规范化规则。

executor:
  Worker 侧实际执行入口。

max_attempts:
  最大执行 attempt 数；MVP 基线建议 1，但必须由 registry 显式给出。

timeout_seconds:
  Job 总运行超时；必须大于 0。

retry_policy:
  哪些 error_kind 可重试；未显式配置为可重试的错误不得创建新 attempt。
```

### `callback_url` 安全合同

Callback 是服务端主动发起的 HTTP 请求，必须按 SSRF 风险处理。只要请求配置了 `callback_url`，API 在创建 Job 前必须完成以下校验；校验失败返回 422 且不创建 Job。

```text
scheme:
  MVP 只允许 https。

host:
  必须是可解析的公网域名或公网 IP。
  禁止 localhost、127.0.0.0/8、::1、私网地址、link-local 地址、multicast 地址和云厂商 metadata 地址。

port:
  默认只允许 443。
  如需其它端口，必须通过服务端 allowlist 配置显式放行。

redirect:
  默认不跟随重定向。
  如果未来允许重定向，重定向后的 URL 必须重新执行完整安全校验。

dns:
  投递前解析结果也必须校验 IP 地址范围，防止创建时和投递时解析结果不一致。

http limits:
  必须配置连接超时、总超时、最大响应体字节数和最大重定向次数。
```

Callback 投递失败时只能写入 `callback_outbox.last_error` 和 `jobs.callback_status`，不得把安全校验失败、网络失败或接收方错误伪装成 Job 执行失败。

### `request_fingerprint` 计算规则

`request_fingerprint` 必须由服务端计算，调用方不传入、不计算。服务端应在鉴权、参数校验、默认值填充和字段规范化之后计算 fingerprint，避免不同客户端语言产生不一致结果。

推荐算法：

```text
request_fingerprint = SHA256(canonical_json({
  "job_type": job_type,
  "job_params": canonical_job_params,
  "callback_url": callback_url or null,
  "callback_events": canonical_callback_events,
  "priority": canonical_priority
}))
```

`canonical_json` 规则：

```text
sort_keys = true
separators = compact
unicode = utf-8
number format = stable
null 与字段缺失按请求合同统一规范化
```

fingerprint 只包含影响 Job 业务结果或通知合同的字段。服务端生成字段、时间戳、trace id、request id、日志上下文等非业务字段不得参与 fingerprint。

如果 `job_params` 中的 `seed` 会影响模型输出，它必须参与 fingerprint。如果某个字段只是调用方提交时的本地时间戳或随机 nonce，且不影响业务结果，应在 `canonical_job_params` 中剔除或规范化。每个 `job_type` 必须明确自己的 `canonical_job_params` 规则。

`POST /jobs` 响应：

```text
job_id
job_type
job_status
job_progress
job_result null
job_error null
callback
created_at
updated_at
finished_at null
expires_at
```

`expires_at` 设置策略：

```text
DEFAULT_JOB_TTL = 30 days
expires_at = created_at + DEFAULT_JOB_TTL
```

`DEFAULT_JOB_TTL` 可按合规或产品要求调整，但必须是服务端配置项。API 响应必须返回 `expires_at`，让调用方明确知道 Job 结果保留到什么时候。

`callback` 对象结构：

```json
{
  "url": "https://api.example.com/webhook",
  "events": ["job.succeeded", "job.failed"],
  "status": "pending",
  "delivery_attempt": 0
}
```

字段规则：

```text
url:
  映射 jobs.callback_url；未配置时为 null。

events:
  映射 jobs.callback_events；未配置时为空数组。

status:
  映射 jobs.callback_status。
  枚举为 not_configured, pending, delivering, delivered, failed, skipped。
  delivering 表示内部 callback_outbox 已被投递器 lease，正在执行 HTTP 投递。

delivery_attempt:
  可投影 callback_outbox.delivery_attempt。
  未创建 outbox 时为 0。
```

普通调用方不应看到 callback outbox 的 `event_id`、`lease_token`、`last_error`、`next_attempt_at`、`last_http_status` 等内部投递账本字段。

`GET /jobs/{job_id}` 响应：

```text
job_id
job_type
job_status
job_progress
job_result
job_error
callback
created_at
updated_at
finished_at
expires_at
```

普通调用方不应看到：

```text
attempt_id
attempt_no
worker_id
lease_token
lease_expires_at
heartbeat_at
```

## 三、心智模型 2：Job 服务视角

Job 服务把一次外部 Job 拆成四条内部账本：

```text
jobs
  对外聚合根，保存当前状态、请求快照、结果、错误、callback 聚合状态、软删除。

job_attempts
  一次完整 Job 执行尝试，保存投递、claim、lease、heartbeat、超时和执行错误。

callback_outbox
  终态事件的可靠投递账本，保存投递租约、重试次数、HTTP 状态、错误和死信状态。

job_events
  append-only 生命周期事件，用于排障、审计和回放，不作为当前状态源。
```

Taskiq 消息只携带：

```text
attempt_id
```

Worker 收到消息后必须回到 PostgreSQL 加载 Attempt 和 Job，并通过 CAS 判断自己是否仍有执行权。

Taskiq 执行语义：

```text
1. Taskiq broker-level retry 不作为业务执行 retry。
2. run_job_attempt 必须捕获可预期的业务异常和基础设施异常，并按状态机写入 PostgreSQL。
3. Worker 进程崩溃、Pod 被杀、解释器退出等无法捕获的异常，只能依靠 lease 过期后由 Reconciler 收敛。
4. Taskiq result backend 不保存业务结果，不参与 API 查询。
5. Taskiq message id 不进入 MVP 数据模型；attempt_id 才是发布、claim 和恢复的唯一关联键。
```

### 服务侧全情况枚举

| 阶段                        | 情况                                         | 服务侧收敛方式                                                         |
| ------------------------- | ------------------------------------------ | --------------------------------------------------------------- |
| API 校验                    | 请求非法                                       | 不写 `jobs`                                                       |
| API 幂等                    | 命中同 fingerprint Job                        | 返回已有 `jobs` 投影                                                  |
| API 幂等                    | fingerprint 冲突                             | 返回 409                                                          |
| DB 创建                     | 新 Job                                      | 同事务写 `jobs=queued`、`job_attempts=queued`、`active_attempt_id`    |
| 发布前失败                     | DB 事务未提交                                   | 没有 Job                                                          |
| 发布失败                      | DB 已提交但 Taskiq publish 失败                  | 记录 `dispatch_attempts/last_dispatch_error/next_dispatch_at`，由 Reconciler 重发或超限失败 |
| 发布未知                      | Taskiq publish 成功但 mark published 失败       | Reconciler 可重复 publish 同一 `attempt_id`                          |
| 发布后无人消费                   | `attempt=published` 长时间未 claim             | Reconciler 重发同一 `attempt_id`                                    |
| 重复消息                      | 同一 `attempt_id` 被多个 Worker 收到              | 只有一个 CAS claim 成功                                               |
| 旧消息晚到                     | attempt 不是 active attempt                  | Worker 跳过                                                       |
| 终态后消息晚到                   | Job 已 `succeeded/failed/cancelled`         | Worker 跳过                                                       |
| Worker claim              | Job 可运行且 attempt active                    | `attempt=running`，`jobs=running`                                |
| Worker 执行成功               | attempt 仍持有未过期 lease 且 Job 非终态            | 同事务写 Job 成功、Attempt 成功、Callback 聚合/outbox、Event              |
| Worker 业务失败               | 不可重试或达到上限                                  | 同事务写 Job 失败、Attempt 失败、Callback 聚合/outbox、Event              |
| Worker 可重试失败              | 未达到上限                                      | 当前 Attempt 失败，创建新 Attempt，Job 回到 `queued`，发布新消息                 |
| Worker 崩溃                 | 没有写终态且 lease 过期                            | Reconciler 标记旧 Attempt stale；如可重试则创建新 Attempt 且 Job 回到 `queued` |
| Worker 外部调用已完成但 DB 未写入就崩溃 | 服务不知道外部结果                                  | 按 Attempt 失败/超时恢复；具体 job\_type 应尽量使用外部幂等能力                      |
| 运行中取消                     | cancel 标记先于终态写入                            | Worker 在安全点收敛 cancelled                                         |
| 取消晚到                      | Job 已终态                                    | 不改变终态                                                           |
| 超时与成功竞态                   | Reconciler 已换 active attempt，旧 Worker 写成功  | CAS 失败，旧 Worker 不得覆盖 Job                                        |
| Callback 创建               | Job 进入终态且订阅事件匹配                            | 写 `callback_outbox=pending`                                     |
| Callback 未配置              | 无 callback\_url                            | `jobs.callback_status=not_configured`，不创建 outbox                |
| Callback 不订阅该事件           | 有 callback\_url 但事件不匹配                     | 创建 `callback_outbox=skipped`，`jobs.callback_status=skipped`     |
| Callback 投递成功             | HTTP 2xx                                   | outbox `delivered`，Job 终态不变                                     |
| Callback 投递失败             | HTTP 非 2xx / 超时 / 网络错误                     | outbox `failed`，设置 `next_attempt_at`                            |
| Callback 超限               | 达最大次数                                      | outbox `dead_letter`，Job 终态不变，聚合状态 `failed`                     |
| 过期清理                      | Job 终态且 callback 收敛且 `expires_at <= now()` | soft delete                                                     |

## 四、心智模型 3：架构视角

架构上把整个 Job 系统理解成一条“可恢复流水线”：

```text
API Pod
  只负责校验、写入 PostgreSQL、返回 job_id、尝试发布 Taskiq 消息。

PostgreSQL
  保存 Job 当前状态、Attempt 执行权、Callback 投递账本和审计事件。

Broker
  只保存待消费消息；消息内容只包含 attempt_id。

Worker Pod
  消费 attempt_id，但必须回 PostgreSQL 通过 CAS claim 才能执行。

Reconciler
  周期扫描 PostgreSQL，把卡住的 queued / published / running / callback 状态显式收敛。
```

因此系统的恢复能力不依赖某个进程一直活着，也不依赖 Taskiq result backend 保存业务结果。只要 PostgreSQL 中的 Job / Attempt 账本还在，API、Worker、Broker 或 Reconciler 的短暂重启都应表现为“状态暂时停在某个中间点，随后被 Worker 或 Reconciler 继续推进”。

Reconciler 的运行位置不决定正确性。它可以运行在独立 Pod、Kubernetes CronJob、API lifespan、Worker 进程或 Worker Pod sidecar 中；这些只是进程承载方式不同。只要所有实例共享 `reconciler_leases`，并且所有修改都遵守状态机 CAS，它们在数据正确性层面等效。

运行位置只影响运维边界：

```text
独立 Reconciler Pod:
  运维边界最清楚，便于单独扩缩容、观测和发布。

API lifespan 内嵌:
  部署最少，但 API 副本数和 uvicorn worker 数会影响 Reconciler 实例数。

Worker 内嵌或 sidecar:
  更贴近后台执行链路，但 Worker 扩缩容会影响 Reconciler 实例数。

Kubernetes CronJob:
  不常驻，适合分钟级恢复，不适合秒级恢复。
```

无论选择哪种运行位置，`reconciler_leases` 都是必需基础设施，不能用 `replicas=1`、单独 Pod、CronJob `concurrencyPolicy` 或进程内单例替代。

### 正常主流程

```text
1. API 接收 POST /jobs。
2. API 在 PostgreSQL 同事务写 jobs=queued、job_attempts=queued、active_attempt_id。
3. API 提交事务后发布 Taskiq 消息 run_job_attempt(attempt_id)。
4. Worker 收到 attempt_id。
5. Worker 回 PostgreSQL 通过 CAS claim active attempt。
6. claim 成功后写 jobs=running、attempt=running、lease_token、lease_expires_at。
7. Worker 执行业务逻辑并 heartbeat 延长或刷新 lease；heartbeat 不得绕过 Job 总运行超时。
8. Worker 成功或失败时，在同一 DB 事务写 Job 终态、Attempt 终态、Callback 聚合状态或 outbox 和 job_events。
9. Callback 投递器按 outbox 投递终态事件。
10. Reconciler 持续处理未发布、未消费、lease 过期、callback lease 过期和过期软删除。
```

这条链路里的关键不变式是：

```text
Taskiq 消息不是执行权。
Worker 进程不是执行权。
Broker 中存在消息也不是执行权。

只有 PostgreSQL 中 active_attempt_id + attempt.status + lease_token + lease_expires_at + CAS 条件共同确认后，Worker 才拥有执行权。
```

### 重启与恢复矩阵

| 故障或重启点 | 可能停留状态 | 是否自动恢复 | 收敛方式 | 何时最终失败 |
| --- | --- | --- | --- | --- |
| API Pod 在 DB 事务提交前重启 | 没有 Job | 不需要恢复 | 调用方重试；有 `idempotency_key` 时可避免重复创建 | 不产生 Job 失败，因为事实源中没有 Job |
| API Pod 在 DB 提交后、publish 前重启 | `jobs=queued`、`attempt=queued` | 是 | Reconciler 扫描未发布 attempt，重新 publish 同一 `attempt_id` | 发布重试超过 `MAX_DISPATCH_ATTEMPTS` 后 Job `failed` |
| API Pod publish 成功后、标记 published 前重启 | `attempt=queued`，但 broker 可能已有消息 | 是 | Worker 可直接 claim；如果消息丢失，Reconciler 仍会重复 publish | 发布重试超过上限后 `failed` |
| API Pod 重启但 Job 已创建并返回 | `queued/running/terminal` | 是 | API 无本地状态；重启后继续从 PostgreSQL 查询投影 | 不因 API 重启失败 |
| Worker Pod 收到消息前重启 | `attempt=published` | 是 | Broker 重新投递，或 Reconciler 对长时间未 claim 的 attempt 重复 publish | 发布/消费链路超过上限后 `failed` |
| Worker Pod claim 前重启 | `attempt=queued/published` | 是 | 没有 lease；后续 Worker 可重新 claim | 不因该 Worker 重启失败 |
| Worker Pod claim 后、业务执行前重启 | `jobs=running`、`attempt=running`、lease 最终过期 | 是 | Reconciler 标记旧 attempt `timed_out`，可重试则创建新 attempt | `max_attempts` 耗尽后 `failed` |
| Worker Pod 业务执行中重启 | `attempt=running`，可能没有终态 | 是，但外部副作用无法完全回滚 | lease 过期或总运行超时后按 stale running 恢复；具体 `job_type` 应使用外部幂等键 | `max_attempts` 耗尽后 `failed` |
| Worker Pod 已拿到外部结果但写 DB 前重启 | PostgreSQL 不知道结果 | 只能按未完成恢复 | Reconciler 视为超时/失败恢复；不伪造成功 | 重试耗尽后 `failed` |
| Worker 写终态事务已提交后重启 | `succeeded/failed/cancelled` | 已收敛 | Job 终态不可被旧消息覆盖；Callback outbox 继续投递 | 不因 Worker 重启改变终态 |
| Broker 重启且消息未丢 | `published` 或 `running` | 是 | Broker 继续投递；Worker CAS 去重 | 不因 broker 重启直接失败 |
| Broker 重启且消息丢失 | `attempt=queued/published` | 是 | PostgreSQL 仍有 attempt；Reconciler 重复 publish 同一 `attempt_id` | 发布重试超过上限后 `failed` |
| Broker 短暂不可用 | publish 失败或消息积压 | 是 | API/Reconciler 记录 dispatch attempt，后续继续 publish | `MAX_DISPATCH_ATTEMPTS` 耗尽后 `failed` |
| Reconciler Pod 重启 | 中间状态暂时不推进 | 是 | Reconciler 无本地状态；重启后重新扫描 PostgreSQL | 不因 Reconciler 重启直接失败 |
| Callback 投递器重启 | `callback=leased`、`jobs.callback_status=delivering` | 是 | callback lease 过期后恢复为可重试状态 | Callback 超过投递上限后 outbox `dead_letter`，Job 终态不变 |
| PostgreSQL 重启或不可用 | API/Worker/Reconciler 无法确认事实源 | 取决于数据库可用性 | 进程不得伪造状态；数据库恢复后按持久化账本继续 | 若数据库持久化数据丢失，超出本设计恢复边界 |

### 恢复不是“吞错”

Reconciler 不应被理解为兜底机制。它是异步 Job 系统的一等组件，职责是把 PostgreSQL 中已经持久化的中间状态推进到下一个合法状态。

```text
兜底机制 / fallback:
  主流程失败后临时换路径、降级、吞错、返回默认值或伪成功。

Reconciler:
  基于持久化账本、CAS 条件、lease 过期和重试上限显式收敛状态。
```

本设计里的恢复机制不是 fallback，也不是 silent catch。恢复只在状态机允许的路径上发生：

```text
queued / published 卡住:
  只能重复 publish 同一个 attempt_id，或在发布重试耗尽后失败。

running 卡住:
  只能在 lease 过期后把旧 attempt 标记为 timed_out，再按 max_attempts 创建新 attempt 或收敛 failed。

callback 卡住:
  只能恢复 callback_outbox 的投递状态，不改变 Job 终态。

旧消息、重复消息、晚到消息:
  只能通过 CAS 跳过，不允许覆盖 active attempt 或终态 Job。
```

每一次恢复都必须写 `job_events`，并保留 `error.kind`、`failure_phase` 或 `reason`。如果达到明确上限，Job 必须进入稳定失败，而不是无限重试或假装成功。

一句话：Reconciler 兜的是异步系统必然存在的中间态，不兜业务错误。

### 哪些情况任务会直接失败

严格来说，进程重启本身不应让 Job 直接失败。Job 进入 `failed` 只来自明确的状态收敛：

- 发布链路超过 `MAX_DISPATCH_ATTEMPTS`。
- Worker 执行业务失败且不可重试。
- Worker 崩溃、被杀或长时间无 heartbeat，且 `max_attempts` 已耗尽。
- Reconciler 判定 lease 过期或总运行超时，且没有剩余 attempt。
- 参数、权限、资源等业务或基础设施错误被 Worker 明确写入失败。

以下情况不会把 Job 直接标记失败：

- API Pod 重启。
- Worker Pod 重启但 lease 尚未过期。
- Broker 短暂重启或消息重复。
- Reconciler 重启。
- Callback 投递失败；它只影响 `callback_status`，不改变 Job 终态。

### 架构边界

本设计能恢复的是“服务内部可见状态”：

```text
Job 是否创建
Attempt 是否发布
Attempt 是否 claim
Worker 是否仍持有 lease
Job 是否已写入终态
Callback 是否已投递或死信
```

本设计不能凭空恢复“服务外部不可见状态”：

```text
外部 LLM 是否已经扣费
外部 LLM 是否已经完成但结果未写回
外部系统是否已经收到 callback 但投递器写 delivered 前崩溃
```

这些场景必须靠外部幂等键、稳定 `event_id` 和接收方幂等处理降低副作用风险。服务端仍以 PostgreSQL 为事实源：没有写入成功终态，就不能对外声称 Job `succeeded`。

## 五、MVP 组件骨架

第一版只需要以下组件，不引入额外编排层。

```text
FastAPI API
  ├─ POST /jobs
  ├─ GET /jobs/{job_id}
  └─ POST /internal/jobs/{job_id}/cancel

Taskiq Worker
  └─ run_job_attempt(attempt_id)

Reconciler
  ├─ recover_unpublished_attempts
  ├─ recover_unclaimed_published_attempts
  ├─ recover_stale_running_attempts
  ├─ deliver_due_callbacks
  ├─ recover_stale_leased_callbacks
  └─ soft_delete_expired_terminal_jobs

PostgreSQL
  ├─ jobs
  ├─ job_attempts
  ├─ callback_outbox
  ├─ job_events
  └─ reconciler_leases

Broker
  └─ Redis / RabbitMQ / NATS
```

MVP 不需要独立 `dispatch_outbox` 表。发布恢复由 `job_attempts.status`、`published_at`、`dispatch_attempts`、`next_dispatch_at`、`last_dispatch_error` 和 Reconciler 承担。若未来要求更强发布审计，可再拆出独立 dispatch outbox。

## 六、`jobs` 表

`jobs` 是对外 Job 的唯一聚合根。一行代表一个外部 `job_id`。

### 字段

```text
id uuid primary key

caller_id varchar(64) not null
idempotency_key varchar(255) null
request_fingerprint varchar(128) not null

job_type varchar(96) not null
status varchar(24) not null
priority varchar(16) not null default 'normal'

job_params jsonb not null

progress_percent integer not null default 0
progress_stage varchar(64) null
progress_message varchar(255) null

result jsonb null
result_ref jsonb null
error jsonb null

callback_url varchar(2048) null
callback_events jsonb not null default '[]'
callback_status varchar(24) not null default 'not_configured'

active_attempt_id uuid null
attempt_count integer not null default 0
max_attempts integer not null
timeout_seconds integer not null

cancel_requested_at timestamptz null
cancel_requested_by varchar(128) null
cancel_reason varchar(512) null

queued_at timestamptz not null
started_at timestamptz null
finished_at timestamptz null
expires_at timestamptz not null

deleted_at timestamptz null
deleted_reason varchar(255) null

created_at timestamptz not null
updated_at timestamptz not null
```

### `jobs` 字段语义

MVP 不保留“未来可能会用”的占位字段。每个字段必须直接服务创建、查询、执行恢复、取消、Callback 或清理。

| 字段 | 语义 |
| --- | --- |
| `id` | 对外 `job_id`，也是所有内部账本的聚合根。 |
| `caller_id` | 调用方身份，用于鉴权隔离和幂等范围。 |
| `idempotency_key` | 调用方提供的创建幂等键；为空时每次创建新 Job。 |
| `request_fingerprint` | 服务端计算的请求规范化摘要；无论是否传入幂等键都必须保存，用于审计和并发冲突判断。 |
| `job_type` | 选择具体执行器、参数校验规则、超时和重试策略。 |
| `status` | 对外 Job 状态，只表达 `queued/running/succeeded/failed/cancelled`。 |
| `priority` | 调度优先级输入；MVP 默认 `normal`，不表达业务结果。 |
| `job_params` | 规范化后的执行参数快照；无参数时保存 `{}`，MVP 不拆外部参数引用字段。 |
| `progress_percent` | 对外进度百分比，便于轮询展示。 |
| `progress_stage` | 当前阶段机器可读标签，例如 `validating`、`calling_model`。 |
| `progress_message` | 当前阶段短说明，不承载错误详情。 |
| `result` | 成功结果 JSON；适合小结果。 |
| `result_ref` | 大结果或文件类结果的外部引用；与 `result` 至少一个在成功时非空。 |
| `error` | 失败终态的结构化错误，写入前必须做体积限制。 |
| `callback_url` | 终态 Callback 地址；必须通过安全校验。 |
| `callback_events` | 调用方订阅的终态事件集合；未配置 callback 时保存空数组。 |
| `callback_status` | Callback 聚合状态，对外只展示摘要，不展示 outbox 内部账本。 |
| `active_attempt_id` | 当前拥有执行资格的 attempt。 |
| `attempt_count` | 已创建 attempt 数量，用于执行重试上限判断。 |
| `max_attempts` | 本 Job 最大执行 attempt 数；由 `job_type` registry 显式提供，MVP 通常为 1。 |
| `timeout_seconds` | Job 总运行超时；由 `job_type` 配置显式提供，从 `started_at` 起算，独立于 heartbeat lease。 |
| `cancel_requested_at` | 内部取消请求时间；存在时 Worker/Reconciler 必须优先收敛取消。 |
| `cancel_requested_by` | 取消发起者，用于审计。 |
| `cancel_reason` | 取消原因短文本，用于排障。 |
| `queued_at` | Job 首次进入队列的时间。 |
| `started_at` | Job 首次进入运行态的时间；不随后续 attempt 重置。 |
| `finished_at` | Job 进入终态的时间。 |
| `expires_at` | 结果保留截止时间。 |
| `deleted_at` | soft delete 时间；非空后普通查询返回 404 或 410。 |
| `deleted_reason` | soft delete 原因，例如 `expired`。 |
| `created_at` | 行创建时间。 |
| `updated_at` | 行最后更新时间。 |

### `jobs.status`

`jobs.status` 只表达对外粗粒度状态：

```text
queued
running
succeeded
failed
cancelled
```

| 状态          | 含义                                 |
| ----------- | ---------------------------------- |
| `queued`    | Job 已创建，等待发布、重新发布或执行。              |
| `running`   | 当前 active attempt 已被 Worker claim。 |
| `succeeded` | Job 已产生成功结果，终态不可被旧 attempt 覆盖。     |
| `failed`    | Job 已失败并收敛，包含 `error`。             |
| `cancelled` | Job 在执行前或执行中被取消收敛。                 |

不放进 `jobs.status` 的内部状态：

```text
published
leased
retrying
timed_out
callback_delivering
callback_failed
soft_deleted
```

这些状态分别属于 `job_attempts`、`callback_outbox` 或 `deleted_at`。

### Job 级约束

```text
status in ('queued', 'running', 'succeeded', 'failed', 'cancelled')
priority in ('low', 'normal', 'high')
callback_status in ('not_configured', 'pending', 'delivering', 'delivered', 'failed', 'skipped')
callback_events is a JSON array
progress_percent between 0 and 100
attempt_count >= 0
max_attempts >= 1
timeout_seconds > 0
finished_at is not null only when status in ('succeeded', 'failed', 'cancelled')
status in ('succeeded', 'failed', 'cancelled') requires finished_at is not null
result is not null only when status='succeeded'
result_ref is not null only when status='succeeded'
status='succeeded' requires result is not null or result_ref is not null
error is not null only when status='failed'
status='failed' requires error is not null
deleted_at is null or status in ('succeeded', 'failed', 'cancelled')
```

`callback_status` 初始规则：

```text
callback_url is null:
  callback_status = 'not_configured'

callback_url is not null:
  callback_status = 'pending'
```

`pending` 在 Job 未终态时表示“已配置 callback，等待终态事件”；在 outbox 创建后表示“终态事件等待投递或重试”。

`delivering` 只表示当前终态事件正在 HTTP 投递中。投递失败但仍可重试时，`jobs.callback_status` 必须回到 `pending`，避免调用方把一次失败尝试误解为最终失败。

## 七、`job_attempts` 表

`job_attempts` 是恢复、超时、重复消息隔离和执行重试的核心。

### 字段

```text
id uuid primary key
job_id uuid not null references jobs(id)
attempt_no integer not null

status varchar(24) not null
published_at timestamptz null
dispatch_attempts integer not null default 0
next_dispatch_at timestamptz null
last_dispatch_error jsonb null

worker_id varchar(255) null
lease_token uuid null
leased_at timestamptz null
lease_expires_at timestamptz null
heartbeat_at timestamptz null

started_at timestamptz null
finished_at timestamptz null
timeout_seconds integer not null

error jsonb null
error_kind varchar(32) null
failure_phase varchar(32) null
retryable boolean null

created_at timestamptz not null
updated_at timestamptz not null
```

### `job_attempts` 字段语义

| 字段 | 语义 |
| --- | --- |
| `id` | 内部 attempt ID，也是 Taskiq 消息唯一载荷。 |
| `job_id` | 所属 Job。 |
| `attempt_no` | 同一 Job 下从 1 开始递增的执行尝试序号。 |
| `status` | Attempt 内部状态，表达发布、claim、运行和终态。 |
| `published_at` | 最近一次成功 publish 的时间。 |
| `dispatch_attempts` | publish 尝试次数；成功和失败都必须计数。 |
| `next_dispatch_at` | 下次允许 publish 的时间，用于发布失败 backoff。 |
| `last_dispatch_error` | 最近一次 publish 失败的结构化错误。 |
| `worker_id` | 成功 claim 的 Worker 标识，用于排障。 |
| `lease_token` | Worker 执行租约令牌；写 progress、heartbeat、终态时必须匹配。 |
| `leased_at` | 当前 lease 获取时间。 |
| `lease_expires_at` | 当前 lease 过期时间；过期后 Worker 不得再写 progress 或终态。 |
| `heartbeat_at` | 最近一次 heartbeat 时间。 |
| `started_at` | 本 attempt 开始运行时间。 |
| `finished_at` | 本 attempt 进入终态时间。 |
| `timeout_seconds` | 本 attempt 运行超时；创建 attempt 时复制 `jobs.timeout_seconds`。 |
| `error` | 本 attempt 失败或超时的结构化错误。 |
| `error_kind` | 错误类别，例如 `dispatch_failed`、`timeout`、`worker_error`。 |
| `failure_phase` | 失败阶段，例如 `publish`、`claim`、`execute`、`write_result`。 |
| `retryable` | 本 attempt 错误是否允许创建新 attempt。 |
| `created_at` | 行创建时间。 |
| `updated_at` | 行最后更新时间。 |

### `job_attempts.status`

```text
queued
published
running
succeeded
failed
timed_out
cancelled
```

| 状态          | 含义                               |
| ----------- | -------------------------------- |
| `queued`    | Attempt 已写 DB，但消息未确认发布。          |
| `published` | Attempt 消息已发布，但尚未被 Worker claim。 |
| `running`   | Worker 已通过 CAS claim 并持有未过期 lease。 |
| `succeeded` | 本次 attempt 成功完成。                 |
| `failed`    | 本次 attempt 失败。                   |
| `timed_out` | Reconciler 判定 lease 过期或总运行超时。  |
| `cancelled` | 本次 attempt 因取消请求收敛。              |

### Attempt 级约束

```text
unique(job_id, attempt_no)
attempt_no >= 1
dispatch_attempts >= 0
timeout_seconds > 0
status in ('queued', 'published', 'running', 'succeeded', 'failed', 'timed_out', 'cancelled')
finished_at is not null only when status in ('succeeded', 'failed', 'timed_out', 'cancelled')
started_at is not null only when status in ('running', 'succeeded', 'failed', 'timed_out', 'cancelled')
status='running' requires worker_id is not null
status='running' requires started_at is not null
status='running' requires lease_token is not null
status='running' requires lease_expires_at is not null
lease_token is not null only when status='running'
lease_expires_at is not null only when status='running'
last_dispatch_error is not null only when last publish attempt failed
```

## 八、`callback_outbox` 表

`callback_outbox` 是终态事件的可靠投递账本。Callback 重试不改变 Job 终态。

### 字段

```text
id uuid primary key
job_id uuid not null references jobs(id)

event_id uuid not null
event_type varchar(64) not null
status varchar(24) not null

payload jsonb not null

delivery_attempt integer not null default 0
next_attempt_at timestamptz null

lease_token uuid null
lease_expires_at timestamptz null

last_http_status integer null
last_error jsonb null
first_attempt_at timestamptz null
last_attempt_at timestamptz null
delivered_at timestamptz null
dead_lettered_at timestamptz null

created_at timestamptz not null
updated_at timestamptz not null
```

### `callback_outbox` 字段语义

| 字段 | 语义 |
| --- | --- |
| `id` | 内部 outbox 行 ID。 |
| `job_id` | 所属 Job。 |
| `event_id` | 对外 Callback 事件 ID；同一 `job_id + event_type` 必须稳定。 |
| `event_type` | 终态事件类型，例如 `job.succeeded`、`job.failed`、`job.cancelled`。 |
| `status` | Callback 投递状态。 |
| `payload` | 对外投递的事件快照，创建后不再修改。 |
| `delivery_attempt` | HTTP 投递尝试次数。 |
| `next_attempt_at` | 下次允许投递时间。 |
| `lease_token` | Callback 投递 lease 令牌。 |
| `lease_expires_at` | Callback 投递 lease 过期时间。 |
| `last_http_status` | 最近一次 HTTP 响应状态码。 |
| `last_error` | 最近一次投递失败的结构化错误。 |
| `first_attempt_at` | 首次投递开始时间。 |
| `last_attempt_at` | 最近一次投递开始时间。 |
| `delivered_at` | 投递成功时间。 |
| `dead_lettered_at` | 超过最大投递次数后的死信时间。 |
| `created_at` | 行创建时间。 |
| `updated_at` | 行最后更新时间。 |

### `callback_outbox.status`

```text
pending
leased
delivered
failed
dead_letter
skipped
```

第一版强制规则：

```text
无 callback_url:
  不创建 callback_outbox。
  jobs.callback_status = 'not_configured'。

有 callback_url，且终态事件在 callback_events 订阅范围内:
  创建 callback_outbox(status='pending')。
  next_attempt_at = now()。
  jobs.callback_status = 'pending'。

有 callback_url，但终态事件不在 callback_events 订阅范围内:
  创建 callback_outbox(status='skipped')。
  jobs.callback_status = 'skipped'。
```

只要调用方配置了 callback\_url，Job 进入终态后就必须有一条对应的 callback\_outbox 记录。事件不匹配时也写 `skipped`，用于审计“为什么没有投递”。

Callback 事件 ID 必须稳定：

```text
同一个 job_id + event_type 的所有重试使用同一个 event_id
```

Callback payload 必须是创建 outbox 时的终态快照，后续重试不得重新生成不同 payload。MVP payload 至少包含：

```text
event_id
event_type
job_id
job_type
job_status
job_result 或 result_ref；失败和取消时为 null
job_error
created_at
finished_at
expires_at
```

普通调用方收到同一个 `event_id` 多次时，必须按幂等事件处理。

推荐约束：

```text
unique(job_id, event_type)
unique(event_id)
delivery_attempt >= 0
status in ('pending', 'leased', 'delivered', 'failed', 'dead_letter', 'skipped')
status='leased' requires lease_token is not null and lease_expires_at is not null
lease_token is not null only when status='leased'
lease_expires_at is not null only when status='leased'
status='delivered' requires delivered_at is not null
status='dead_letter' requires dead_lettered_at is not null
```

## 九、`job_events` 表

`job_events` 是 append-only 生命周期事件表，用于审计和排障，不作为查询事实源。

### 字段

```text
id uuid primary key
job_id uuid not null references jobs(id)
attempt_id uuid null references job_attempts(id)
callback_id uuid null references callback_outbox(id)

event_type varchar(96) not null
from_status varchar(24) null
to_status varchar(24) null
reason varchar(128) null
payload jsonb null

created_at timestamptz not null
```

### `job_events` 字段语义

| 字段 | 语义 |
| --- | --- |
| `id` | 事件 ID。 |
| `job_id` | 所属 Job。 |
| `attempt_id` | 关联 attempt；Job 级事件可为空。 |
| `callback_id` | 关联 callback outbox；非 Callback 事件可为空。 |
| `event_type` | 生命周期事件类型。 |
| `from_status` | 状态变更前状态；无状态变更时可为空。 |
| `to_status` | 状态变更后状态；无状态变更时可为空。 |
| `reason` | 状态变更原因或异常收敛原因。 |
| `payload` | 事件补充信息，用于排障，不作为当前状态源。 |
| `created_at` | 事件写入时间。 |

关键事件类型：

```text
job.created
job.publish_requested
job.publish_confirmed
job.running
job.succeeded
job.failed
job.cancel_requested
job.cancelled
job.soft_deleted

attempt.created
attempt.published
attempt.publish_failed
attempt.claimed
attempt.heartbeat
attempt.succeeded
attempt.failed
attempt.timed_out
attempt.cancelled

callback.created
callback.leased
callback.delivered
callback.failed
callback.dead_lettered
callback.skipped
```

写入规则：

- 状态变更成功后写事件。
- Job 终态写入、Attempt 终态写入、Callback 聚合状态或 outbox 创建、终态 Event 写入应处于同一 DB 事务。
- API 查询只读 `jobs` 当前投影，不从 `job_events` 回放状态。

## 十、`reconciler_leases` 表

`reconciler_leases` 是 Reconciler 的必需基础设施，用于提供集群级单飞协调。它不是 Job 业务状态表，不参与 API 对外投影，不改变 Job / Attempt / Callback 的状态语义，也不作为恢复是否成功的事实源。

本设计默认必须使用 `reconciler_leases`。如果未来替换为 PostgreSQL advisory lock、Redis lock 或 Kubernetes Lease API，替代机制也必须提供等价能力：

```text
集群级互斥
owner 可识别
lease 可过期
实例崩溃后可接管
可观测当前 owner 和过期时间
```

单独 Reconciler Pod、`replicas=1`、Kubernetes CronJob `concurrencyPolicy: Forbid`、FastAPI 进程内单例都不能替代 `reconciler_leases`。这些机制只能降低重复运行概率，不能作为集群级唯一性证明。

这张表只回答一个问题：

```text
当前哪一个 Reconciler 实例可以执行某一类扫描任务？
```

真正决定 Job 是否可以被修改的，仍然是：

```text
jobs.status
jobs.active_attempt_id
job_attempts.status
job_attempts.lease_token
job_attempts.lease_expires_at
callback_outbox.status
状态机 CAS 条件
```

### 字段

```text
name varchar(96) primary key
owner_id varchar(255) not null
lease_expires_at timestamptz not null
last_acquired_at timestamptz not null
last_renewed_at timestamptz not null
created_at timestamptz not null
updated_at timestamptz not null
```

字段规则：

```text
name:
  Reconciler 任务名称，例如 recover_stale_running_attempts。

owner_id:
  当前持有 lease 的实例 ID。推荐格式为 pod_name + process_id + boot_uuid。

lease_expires_at:
  lease 过期时间。owner 崩溃、Pod 被杀或网络断开后，其他实例必须等到该时间之后才能接管。

last_acquired_at:
  最近一次抢到 lease 的时间，用于排障和指标。

last_renewed_at:
  最近一次续租时间，用于判断 owner 是否活跃。

created_at:
  lease 行首次创建时间，用于排查任务首次启用时间。

updated_at:
  lease 行最后更新时间，用于排查抢占、续租和接管行为。
```

推荐每类扫描任务使用独立 lease：

```text
recover_unpublished_attempts
recover_unclaimed_published_attempts
recover_stale_running_attempts
deliver_due_callbacks
recover_stale_leased_callbacks
soft_delete_expired_terminal_jobs
```

不要只使用一个全局 `reconciler` lease。Callback 投递慢、软删除慢或某类扫描异常时，不应阻塞其它恢复路径。

### 抢占语义

抢 lease 必须是原子 CAS：

```text
where lease 不存在
or lease_expires_at < now()
or owner_id = current_owner_id
```

成功抢到 lease 才允许执行对应扫描。抢不到 lease 的实例必须跳过该类任务并等待下一轮。

参考 SQL：

```sql
insert into reconciler_leases(
  name,
  owner_id,
  lease_expires_at,
  last_acquired_at,
  last_renewed_at,
  created_at,
  updated_at
)
values (
  :name,
  :owner_id,
  now() + :lease_ttl,
  now(),
  now(),
  now(),
  now()
)
on conflict (name) do update
set owner_id = excluded.owner_id,
    lease_expires_at = excluded.lease_expires_at,
    last_acquired_at = case
      when reconciler_leases.owner_id = excluded.owner_id
      then reconciler_leases.last_acquired_at
      else excluded.last_acquired_at
    end,
    last_renewed_at = excluded.last_renewed_at,
    updated_at = excluded.updated_at
where reconciler_leases.lease_expires_at < now()
   or reconciler_leases.owner_id = excluded.owner_id
returning name;
```

`returning name` 有结果表示当前实例持有 lease；没有结果表示其它实例仍持有有效 lease。

### 正确性边界

`reconciler_leases` 只减少重复扫描和重复执行，不承担业务正确性。即使因为极端时钟漂移、事务超时、部署切换或人工操作导致两个 Reconciler 同时进入同一类扫描，Job 数据仍必须依靠状态机 CAS 保证正确。

因此实现上必须同时满足：

```text
1. 先抢 reconciler_leases。
2. 每批只处理有限数量记录。
3. 修改 Job / Attempt / Callback 前重新加载当前行。
4. 遵守统一加锁顺序。
5. 所有更新必须带 CAS 条件。
6. 所有收敛必须写 job_events。
```

### 运行位置与切换

`reconciler_leases` 让 Reconciler 的代码位置和部署形态解耦。只要所有实例使用同一张 lease 表和同一套 CAS 规则，以下形态可以互相切换：

```text
API lifespan 内嵌 Reconciler loop
Worker 进程内嵌 Reconciler loop
Worker Pod sidecar Reconciler
Kubernetes CronJob run-once Reconciler
独立 Reconciler Deployment
```

这些形态在正确性上等效，差异只在运维复杂度、资源隔离、恢复延迟、观测和发布边界。

从内嵌模式切换到独立 Reconciler Pod 时，不需要改 Job / Attempt / Callback 表结构，也不需要迁移历史 Job。只需要：

```text
1. 保留 reconciler_leases 表。
2. 在 API / Worker 配置中关闭内嵌 Reconciler，例如 ENABLE_RECONCILER=false。
3. 启动独立 Reconciler Pod，例如 ENABLE_RECONCILER=true 或 python -m app.reconciler --loop。
4. 确认独立 Reconciler 使用相同 owner_id 生成规则、lease 名称、lease_ttl、batch limit 和 CAS 逻辑。
5. 观察 metrics，确认旧内嵌实例不再续租，新实例可以正常持有 lease。
```

迁移期间允许短时间新旧 Reconciler 并存。只要它们共享 `reconciler_leases` 并遵守 CAS，最多产生重复抢锁或重复扫描，不应产生错误状态覆盖。

## 十一、状态迁移与 CAS 条件

### Job 状态迁移

| from      | to          | 触发者                 | 必要条件                                   |
| --------- | ----------- | ------------------- | -------------------------------------- |
| none      | `queued`    | API                 | 创建 Job 事务成功                            |
| `queued`  | `running`   | Worker              | active attempt CAS claim 成功            |
| `queued`  | `cancelled` | Internal API        | 尚未运行或运行前取消收敛                           |
| `running` | `succeeded` | Worker              | active attempt 持有未过期 lease，Job 非终态    |
| `running` | `failed`    | Worker / Reconciler | 不再重试或达到 `max_attempts`                 |
| `running` | `cancelled` | Worker              | cancel 标记存在，且 Job 非终态                  |
| `running` | `queued`    | Worker / Reconciler | 当前 attempt 失败或超时，但仍可创建新 active attempt |
| `queued`  | `failed`    | Reconciler          | 发布或执行恢复超过上限                            |
| terminal  | terminal    | none                | 终态不可改变                                 |

终态：

```text
succeeded
failed
cancelled
```

### Attempt 状态迁移

| from                       | to          | 触发者              | 必要条件                                  |
| -------------------------- | ----------- | ---------------- | ------------------------------------- |
| none                       | `queued`    | API / Reconciler | 创建 attempt                            |
| `queued`                   | `published` | API / Reconciler | Taskiq publish 成功或被认为已发布              |
| `published`                | `published` | Reconciler       | 长时间未 claim，重复 publish 同一 `attempt_id` |
| `published`                | `running`   | Worker           | active attempt CAS claim 成功           |
| `queued`                   | `running`   | Worker           | 允许处理 publish 标记失败但消息已到达的情况            |
| `running`                  | `succeeded` | Worker           | 持有未过期 lease 且 Job 非终态                |
| `running`                  | `failed`    | Worker           | 执行失败                                  |
| `running`                  | `timed_out` | Reconciler       | lease 过期或 attempt 总运行超时            |
| `queued/published/running` | `cancelled` | API / Worker     | cancel 收敛                             |

### 数据库加锁顺序

所有同时修改 `jobs` 和 `job_attempts` 的事务，必须使用统一加锁顺序：

```text
1. SELECT jobs ... FOR UPDATE
2. SELECT job_attempts ... FOR UPDATE
3. 执行状态变更
```

所有同时修改 `jobs` 和 `callback_outbox` 的事务，必须使用统一加锁顺序：

```text
1. SELECT jobs ... FOR UPDATE
2. SELECT callback_outbox ... FOR UPDATE
3. 执行状态变更
```

禁止在某条路径中先锁 Attempt 再锁 Job，而另一条路径中先锁 Job 再锁 Attempt。Worker、Reconciler、Cancel API、Callback 投递器都必须遵守同一顺序，避免 PostgreSQL 行级锁交叉等待导致死锁。

### 必须使用的 CAS 条件

Worker claim：

```text
attempt.status in ('queued', 'published')
and jobs.status in ('queued', 'running')
and jobs.active_attempt_id = attempt.id
and jobs.deleted_at is null
```

Worker heartbeat：

```text
attempt.status = 'running'
and attempt.lease_token = :lease_token
and attempt.lease_expires_at >= now()
and jobs.active_attempt_id = attempt.id
and jobs.status = 'running'
```

Worker 写 progress：

```text
attempt.status = 'running'
and attempt.lease_token = :lease_token
and attempt.lease_expires_at >= now()
and jobs.active_attempt_id = attempt.id
and jobs.status = 'running'
```

Worker 写成功：

```text
attempt.status = 'running'
and attempt.lease_token = :lease_token
and attempt.lease_expires_at >= now()
and jobs.active_attempt_id = attempt.id
and jobs.status = 'running'
and jobs.deleted_at is null
```

Worker 写失败：

```text
attempt.status = 'running'
and attempt.lease_token = :lease_token
and attempt.lease_expires_at >= now()
and jobs.active_attempt_id = attempt.id
and jobs.status = 'running'
and jobs.deleted_at is null
```

Reconciler 标记 stale：

```text
attempt.status = 'running'
and (
  attempt.lease_expires_at < now()
  or attempt.started_at + (attempt.timeout_seconds * interval '1 second') < now()
)
and jobs.active_attempt_id = attempt.id
and jobs.status = 'running'
```

取消写入：

```text
jobs.status in ('queued', 'running')
and jobs.deleted_at is null
```

Callback lease：

```text
callback.status in ('pending', 'failed')
and callback.next_attempt_at <= now()
and jobs.status in ('succeeded', 'failed', 'cancelled')
and jobs.deleted_at is null
```

Callback 写 delivered / failed / dead_letter：

```text
callback.status = 'leased'
and callback.lease_token = :lease_token
and callback.lease_expires_at >= now()
and jobs.status in ('succeeded', 'failed', 'cancelled')
and jobs.deleted_at is null
```

软删除：

```text
jobs.status in ('succeeded', 'failed', 'cancelled')
and jobs.expires_at <= now()
and callback_status in ('not_configured', 'delivered', 'failed', 'skipped')
and jobs.deleted_at is null
```

## 十二、核心流程骨架

### `POST /jobs`

```text
1. 鉴权，得到 caller_id。
2. 校验 job_type、job_params、callback_url、callback_events，并从 job_type registry 读取 max_attempts、timeout_seconds。
3. 规范化 job_params、callback_events、priority，并由服务端计算 request_fingerprint。
4. 如果提供 idempotency_key：
   4.1 查询 caller_id + idempotency_key 的未删除 Job。
   4.2 fingerprint 相同则返回已有 Job 投影。
   4.3 fingerprint 不同则返回 409。
   4.4 如果并发插入触发 unique(caller_id, idempotency_key) 冲突，
       必须重新加载已有 Job 并重复 4.2 / 4.3，不得把唯一约束冲突暴露为 500。
5. 开启 DB 事务：
   5.1 写 jobs(status='queued')。
       queued_at = now()。
       expires_at = created_at + DEFAULT_JOB_TTL。
       timeout_seconds = job_type.timeout_seconds。
       max_attempts = job_type.max_attempts。
       callback_url 为空时 callback_status='not_configured'。
       callback_url 非空时 callback_status='pending'。
   5.2 写 job_attempts(attempt_no=1, status='queued', timeout_seconds=jobs.timeout_seconds)。
   5.3 更新 jobs.active_attempt_id、attempt_count=1。
   5.4 写 job.created、attempt.created、job.publish_requested 事件。
6. 提交事务。
7. publish run_job_attempt(attempt_id)。
8. publish 成功后标记 attempt.status='published'、published_at、dispatch_attempts+1、next_dispatch_at=null、last_dispatch_error=null，并写 attempt.published、job.publish_confirmed 事件。
9. 如果 publish 失败：
   9.1 不伪造成功发布。
   9.2 同事务写 dispatch_attempts+1、last_dispatch_error、next_dispatch_at，并写 attempt.publish_failed 事件。
   9.3 未超过 MAX_DISPATCH_ATTEMPTS 时保留 attempt='queued'，等待 Reconciler。
   9.4 超过 MAX_DISPATCH_ATTEMPTS 时收敛 Job failed，并按 job.failed 处理 Callback。
10. 返回 Job 投影。
```

### `run_job_attempt(attempt_id)`

```text
1. 加载 attempt + job。
2. CAS claim：
   attempt.status in ('queued', 'published')
   jobs.active_attempt_id = attempt.id
   jobs.status in ('queued', 'running')
3. CAS 失败则跳过。
4. claim 成功：
   attempt.status='running'
   jobs.status='running'
   写 worker_id、lease_token、leased_at、lease_expires_at、attempt.started_at。
   如果 jobs.started_at 为空，写 jobs.started_at=now()。
   写 job.running、attempt.claimed 事件。
5. 检查 cancel_requested_at。
6. 执行 Job：
   6.1 模型调用前检查取消。
   6.2 模型调用后检查取消。
   6.3 写结果前检查取消。
   6.4 周期 heartbeat，刷新 lease_expires_at 并写 attempt.heartbeat 事件。
7. 任一安全点发现 cancel_requested_at 时，必须先确认 lease_token 匹配且 lease_expires_at 未过期，再同事务：
   attempt.status='cancelled'
   attempt.finished_at=now()
   清空 attempt lease 字段
   jobs.status='cancelled'
   jobs.finished_at=now()
   按取消 Callback 规则创建 callback_outbox(status='skipped') 或更新 callback_status
   写 job.cancelled、attempt.cancelled 事件；如创建 skipped outbox，则写 callback.skipped 事件
8. 成功时，必须先确认 lease_token 匹配且 lease_expires_at 未过期，再同事务：
   attempt.status='succeeded'
   attempt.finished_at=now()
   清空 attempt lease 字段
   jobs.status='succeeded'
   jobs.result / result_ref
   jobs.finished_at
   按 callback 规则创建 callback_outbox 或更新 callback_status
   写 job.succeeded、attempt.succeeded 事件；如创建 outbox，则写 callback.created 或 callback.skipped 事件
9. 失败时，必须先确认 lease_token 匹配且 lease_expires_at 未过期，再判断：
   9.1 判断 error_kind / retryable / attempt_count / max_attempts。
   9.2 可重试则同事务：
       当前 attempt='failed'。
       attempt.finished_at=now()。
       清空当前 attempt lease 字段。
       创建 new attempt(status='queued', timeout_seconds=jobs.timeout_seconds)。
       jobs.active_attempt_id = new attempt id。
       jobs.attempt_count += 1。
       jobs.status='queued'。
       写 attempt.failed、attempt.created、job.publish_requested 事件。
       事务提交后 publish new attempt。
   9.3 不可重试则同事务写 Job failed、Attempt failed、attempt.finished_at=now()、清空 attempt lease 字段、Callback 聚合/outbox、Event。
   9.4 写入 error 前必须执行错误体积限制。
```

### `POST /internal/jobs/{job_id}/cancel`

```text
1. 校验内部权限和 ENABLE_INTERNAL_JOB_CANCEL。
2. 如果 jobs.status 是终态，返回当前 Job，不改变终态。
3. 如果 jobs.status='queued'：
   jobs.status='cancelled'
   active attempt.status='cancelled'
   active attempt.finished_at=now()
   jobs.finished_at=now()
   如果配置了 callback_url，创建 callback_outbox(status='skipped', event_type='job.cancelled')
   如果配置了 callback_url，jobs.callback_status='skipped'
   写 job.cancel_requested、job.cancelled、attempt.cancelled
   不投递外部 callback
4. 如果 jobs.status='running'：
   写 cancel_requested_at、cancel_requested_by、cancel_reason。
   写 job.cancel_requested。
   Worker 在安全点收敛为 cancelled。
   如果 Worker 崩溃或不再 heartbeat，Reconciler 在处理 stale running 时必须优先收敛为 cancelled，而不是创建新 attempt。
   Worker 收敛 cancelled 时，如果配置了 callback_url，创建 callback_outbox(status='skipped', event_type='job.cancelled')。
   Worker 收敛 cancelled 时，如果配置了 callback_url，jobs.callback_status='skipped'。
   不投递外部 callback。
```

### 取消与失败的 Callback 语义

MVP 明确区分主动取消和失败收敛：

```text
status='cancelled':
  人工、运维或内部主动取消。
  默认不触发外部 Callback。
  如果调用方配置了 callback_url，Job 终态时创建 callback_outbox(status='skipped')，event_type='job.cancelled'，用于审计。

status='failed':
  执行失败、超时、Worker 崩溃后重试耗尽、发布恢复超过上限等失败收敛。
  如果调用方订阅 job.failed，必须触发 Callback。
```

换言之，Reconciler 因超时或重试耗尽把 Job 收敛为 `failed` 时，不属于取消，必须按 `job.failed` 事件处理。

### Reconciler

Reconciler 是基于持久化账本的显式状态收敛组件，不是兜底降级逻辑。它只处理状态机允许的中间状态，不吞错、不伪造成功、不绕过 CAS，也必须写审计事件。

Reconciler 必须使用 `reconciler_leases` 或等价集群级 lease 机制保证同一类扫描单飞。独立 Reconciler Pod、`replicas=1`、CronJob 并发策略或进程内单例都不能替代集群级 lease。

#### 异常收敛原则

Reconciler 是显式状态收敛器，不是 silent fallback。所有恢复、重试、超时、死信和软删除动作都必须满足以下原则：

```text
1. 必须有明确 where 条件和 CAS 条件。
2. 必须遵守统一加锁顺序。
3. 必须有明确重试上限、超时阈值或过期条件。
4. 必须写入结构化 error.kind、failure_phase 或 reason，便于诊断。
5. 必须写 job_events 审计事件。
6. 不允许伪造 succeeded。
7. 不允许 silent catch 后吞掉异常。
8. 不允许无审计地修复或删除非终态 Job。
9. 不允许绕过 Job / Attempt / Callback 的状态机直接改最终投影。
```

Reconciler 可以把状态收敛为 `failed`、`timed_out`、`dead_letter`、`skipped` 或 soft delete，但每一次收敛都必须可追溯、可重复执行、可通过验收用例验证。

#### 恢复未发布 attempt

```text
where attempt.status='queued'
and attempt.created_at < now() - unpublished_cutoff
and jobs.status in ('queued', 'running')
and jobs.active_attempt_id = attempt.id
and attempt.dispatch_attempts < MAX_DISPATCH_ATTEMPTS
and (attempt.next_dispatch_at is null or attempt.next_dispatch_at <= now())
```

动作：

```text
publish run_job_attempt(attempt.id)
dispatch_attempts += 1
成功:
  published_at = now()
  status = 'published'
  next_dispatch_at = null
  last_dispatch_error = null
  写 attempt.published、job.publish_confirmed 事件
失败:
  status 保持 queued
  last_dispatch_error = 结构化 publish 错误
  next_dispatch_at = now() + dispatch_backoff(dispatch_attempts)
  写 attempt.publish_failed 事件，reason='publish_error'
  如果 dispatch_attempts >= MAX_DISPATCH_ATTEMPTS，立即按发布超限收敛 failed
```

超过发布重试上限：

```text
where attempt.status in ('queued', 'published')
and jobs.status in ('queued', 'running')
and jobs.active_attempt_id = attempt.id
and attempt.dispatch_attempts >= MAX_DISPATCH_ATTEMPTS
```

动作：

```text
attempt.status = 'failed'
attempt.finished_at = now()
attempt.error_kind = 'dispatch_failed'
attempt.failure_phase = 'publish'
attempt.error = last_dispatch_error 或结构化 dispatch_failed 错误
jobs.status = 'failed'
jobs.error.kind = 'dispatch_failed'
jobs.finished_at = now()
按 callback 规则创建 callback_outbox 或更新 callback_status
写 attempt.failed、job.failed 事件；如创建 outbox，则写 callback.created 或 callback.skipped 事件
```

#### 恢复 published 但无人 claim 的 attempt

```text
where attempt.status='published'
and attempt.published_at < now() - unclaimed_cutoff
and attempt.started_at is null
and jobs.status in ('queued', 'running')
and jobs.active_attempt_id = attempt.id
and attempt.dispatch_attempts < MAX_DISPATCH_ATTEMPTS
and (attempt.next_dispatch_at is null or attempt.next_dispatch_at <= now())
```

动作：

```text
重复 publish 同一 attempt_id
dispatch_attempts += 1
成功:
  published_at = now()
  next_dispatch_at = null
  last_dispatch_error = null
  写 attempt.published、job.publish_confirmed 事件
失败:
  last_dispatch_error = 结构化 publish 错误
  next_dispatch_at = now() + dispatch_backoff(dispatch_attempts)
  如果 dispatch_attempts >= MAX_DISPATCH_ATTEMPTS，立即按发布超限收敛 failed
```

重复消息由 Worker CAS 去重。

#### 恢复 stale 或超时 running attempt

```text
where attempt.status='running'
and (
  attempt.lease_expires_at < now()
  or attempt.started_at + (attempt.timeout_seconds * interval '1 second') < now()
)
and jobs.status='running'
and jobs.active_attempt_id = attempt.id
```

动作：

```text
1. 如果 jobs.cancel_requested_at is not null：
   1.1 old attempt -> cancelled。
   1.2 old attempt.finished_at=now()。
   1.3 清空 old attempt lease 字段。
   1.4 jobs.status='cancelled'。
   1.5 jobs.finished_at=now()。
   1.6 如果配置了 callback_url，创建 callback_outbox(status='skipped', event_type='job.cancelled')。
   1.7 写 attempt.cancelled、job.cancelled 事件；如创建 skipped outbox，则写 callback.skipped 事件。
   1.8 结束本次收敛，不创建新 attempt。
2. 否则 old attempt -> timed_out，old attempt.finished_at=now()，并清空 old attempt lease 字段，写 attempt.timed_out 事件。
3. 如果 jobs.attempt_count < jobs.max_attempts：
   3.1 创建 new attempt(status='queued', timeout_seconds=jobs.timeout_seconds)。
   3.2 jobs.active_attempt_id = new attempt id。
   3.3 jobs.attempt_count += 1。
   3.4 jobs.status='queued'。
   3.5 写 attempt.created、job.publish_requested 事件。
   3.6 事务提交后 publish new attempt。
4. 否则 jobs.status='failed'，error.kind='timeout'。
   按 callback 规则创建 callback_outbox 或更新 callback_status。
   写 job.failed 事件；如创建 outbox，则写 callback.created 或 callback.skipped 事件。
```

#### 投递 due callback

```text
where callback.status in ('pending', 'failed')
and callback.next_attempt_at <= now()
```

动作：

```text
1. CAS lease callback。
2. lease 成功时，callback.status='leased'，jobs.callback_status='delivering'，delivery_attempt += 1，last_attempt_at=now()，first_attempt_at 为空时写 now()。
3. 投递前重新执行 callback_url 安全校验和 DNS 解析结果校验。
4. 投递 HTTP callback。
5. 2xx -> callback.status='delivered'，delivered_at=now()，清空 callback lease，jobs.callback_status='delivered'。
6. URL 安全校验失败 / 非 2xx / 超时 / 网络错误：
   6.1 未超过最大次数 -> callback.status='failed'，清空 callback lease，计算 next_attempt_at，jobs.callback_status='pending'。
   6.2 超过最大次数 -> callback.status='dead_letter'，dead_lettered_at=now()，清空 callback lease，jobs.callback_status='failed'。
```

#### 恢复 stale leased callback

Callback 投递器可能在 lease 成功后、写入 `delivered` 或 `failed` 前崩溃。Reconciler 必须恢复过期 lease，避免 callback 永久停留在 `leased`，同时避免 Job 的 callback 聚合状态永久停留在 `delivering`。

```text
where callback.status='leased'
and callback.lease_expires_at < now()
```

动作：

```text
callback.status='failed'
callback.lease_token=null
callback.lease_expires_at=null
callback.next_attempt_at=now() 或按 callback backoff 计算
jobs.callback_status='pending'
写 callback.failed 事件，reason='callback_lease_expired'
```

这不会改变 `jobs.status` 和 Job 终态，只恢复 callback outbox 的投递账本。由于 HTTP 请求可能已经发出但投递器尚未写入 `delivered` 就崩溃，Callback 投递语义是 at-least-once。接收方必须使用稳定 `event_id` 做幂等。

#### 软删除过期终态 Job

```text
where jobs.status in ('succeeded', 'failed', 'cancelled')
and jobs.expires_at <= now()
and jobs.callback_status in ('not_configured', 'delivered', 'failed', 'skipped')
and jobs.deleted_at is null
```

动作：

```text
jobs.deleted_at = now()
jobs.deleted_reason = 'expired'
写 job.soft_deleted
```

## 十三、重试策略

重试分三类，不应混在一个配置里。

### 发布重试

解决 DB 已提交但 Taskiq 消息未可靠进入执行链路的问题。

发布重试不增加业务执行次数，只增加：

```text
job_attempts.dispatch_attempts
```

默认策略：

```text
MAX_DISPATCH_ATTEMPTS = 10
```

超过 `MAX_DISPATCH_ATTEMPTS` 时，当前 attempt 和 Job 必须收敛为失败：

```text
attempt.status = 'failed'
attempt.error_kind = 'dispatch_failed'
attempt.failure_phase = 'publish'
jobs.status = 'failed'
jobs.error.kind = 'dispatch_failed'
```

发布失败属于 `job.failed`，如果调用方订阅 `job.failed`，必须创建并投递 Callback。

### 执行重试

解决 Worker 崩溃、进程被杀、lease 过期和可重试基础设施错误。

执行重试必须创建新的 `job_attempts` 行：

```text
old attempt -> failed / timed_out
new attempt_no = old + 1
jobs.active_attempt_id = new attempt id
jobs.attempt_count += 1
jobs.status = 'queued'
```

MVP 基线策略：

```text
max_attempts = 1
```

每个 `job_type` 都必须在 registry 中显式给出 `max_attempts`。只有明确配置的 job\_type 才允许高于 1。真实 LLM 调用可能产生费用或外部不可见副作用，不应默认立即重试。

### Callback 重试

Callback 重试只改变 `callback_outbox` 和 `jobs.callback_status`，不改变 Job 终态。

```text
delivery_attempt < CALLBACK_MAX_DELIVERY_ATTEMPTS
next_attempt_at <= now()
```

超过上限：

```text
callback_outbox.status='dead_letter'
jobs.callback_status='failed'
```

## 十四、错误体积限制

`jobs.error`、`job_attempts.error`、`job_attempts.last_dispatch_error` 和 `callback_outbox.last_error` 都必须在写入前做体积限制，避免超长 traceback、LLM 响应体或 HTTP 错误页面拖垮数据库写入。

默认限制：

```text
MAX_ERROR_JSON_BYTES = 10KB
ERROR_SUMMARY_CHARS = 200
```

写入规则：

```text
1. 先构造结构化 error：
   error_type
   message
   code
   phase
   retryable
   traceback optional
   raw_response optional

2. 序列化为 canonical JSON。

3. 如果 JSON 字节数 <= MAX_ERROR_JSON_BYTES：
   原样写入。

4. 如果 JSON 字节数 > MAX_ERROR_JSON_BYTES：
   丢弃 traceback、raw_response 等大字段。
   保留 error_type、code、phase、retryable。
   message 保留前 200 字符和后 200 字符。
   写入 truncated=true。
   写入 original_size_bytes。
```

截断不是 silent fallback。截断后的 error 仍必须明确表达失败类型和阶段，并通过 `truncated=true` 告诉排障者原始错误被裁剪。

## 十五、索引建议

### `jobs`

```text
unique(caller_id, idempotency_key)
  where idempotency_key is not null and deleted_at is null

index(status, created_at)
index(status, expires_at)
index(caller_id, created_at)
index(deleted_at)
index(active_attempt_id)
```

如果需要数据库层约束 active attempt 必须属于当前 Job，可考虑：

```text
unique(id, active_attempt_id)
```

并在实现中保证 `active_attempt_id` 指向同一 `job_id` 的 attempt。由于 `jobs` 与 `job_attempts` 存在插入顺序问题，也可以第一版先用应用层事务保证，后续再加 deferrable 约束。

### `job_attempts`

```text
unique(job_id, attempt_no)
index(job_id, attempt_no desc)
index(status, created_at)
index(status, published_at)
index(status, next_dispatch_at)
index(status, lease_expires_at)
```

### `callback_outbox`

```text
unique(job_id, event_type)
unique(event_id)
index(status, next_attempt_at)
index(status, lease_expires_at)
index(job_id, created_at)
```

### `job_events`

```text
index(job_id, created_at)
index(attempt_id, created_at)
index(callback_id, created_at)
index(event_type, created_at)
```

### `reconciler_leases`

```text
primary key(name)
index(lease_expires_at)
index(owner_id)
```

不要默认给所有 JSONB 字段建 GIN 索引。只有当查询路径明确依赖 JSON 内部字段时，再补定向索引。

## 十六、外部副作用与一致性边界

外部 LLM 调用存在一个无法由本服务完全消除的窗口：

```text
外部模型已经处理或扣费
  ↓
Worker 在写 DB 结果前崩溃
  ↓
服务不知道外部调用是否完成
```

MVP 的处理原则：

- 服务以 PostgreSQL 为事实源。
- 未写入 Job 终态就视为未收敛。
- Reconciler 可以按 Attempt 超时或失败恢复。
- 具体 `job_type` 如有外部幂等能力，应把 `job_id` 或 `attempt_id` 作为外部请求幂等键。
- 不为未知外部结果伪造成功。

## 十七、MVP 验收清单

实现完成后，至少应能验证以下路径：

- 无 `idempotency_key` 连续创建两次，得到两个 Job。
- 相同 `idempotency_key` + 相同 fingerprint 重试，返回同一 Job。
- 相同 `idempotency_key` + 不同 fingerprint，返回 409。
- 并发相同 `idempotency_key` 创建触发唯一约束冲突时，reload 后返回同一 Job 或 409，不返回 500。
- `request_fingerprint` 由服务端 canonical JSON 稳定计算。
- 未注册 `job_type` 或缺少 `max_attempts/timeout_seconds` 配置时，不创建 Job。
- `callback_url` 命中 localhost、私网、metadata 地址、非 https 或未放行端口时，不创建 Job。
- 创建 Job 后 publish 失败，Reconciler 能重新 publish。
- publish 失败必须增加 `dispatch_attempts`，写 `last_dispatch_error` 和 `attempt.publish_failed` 事件。
- `published` attempt 长时间无人 claim，Reconciler 能重复 publish。
- 发布重试超过 `MAX_DISPATCH_ATTEMPTS` 后 Job 收敛为 `failed`，并按 `job.failed` 处理 Callback。
- 重复 Taskiq 消息只有一个 Worker claim 成功。
- 旧 attempt 晚到不能覆盖 active attempt。
- Job 成功后，旧 attempt 不能覆盖终态。
- Worker lease 过期后，旧 Worker 不能再写 progress、success 或 failure。
- Worker 持续 heartbeat 但超过 `timeout_seconds` 时，Reconciler 必须按超时收敛。
- 可重试执行失败或 stale running 创建新 attempt 后，Job 状态回到 `queued`，直到新 attempt claim 后再变为 `running`。
- Worker 和 Reconciler 涉及 Job/Attempt 更新时统一先锁 `jobs` 再锁 `job_attempts`。
- 多个 Reconciler 实例同时启动时，同一类扫描只有持有 `reconciler_leases` 的实例执行。
- Reconciler lease 过期后，其它实例能接管对应扫描任务。
- Reconciler 从 API / Worker 内嵌模式切换到独立 Pod 时，不需要修改 Job / Attempt / Callback 数据。
- Worker lease 过期后，Reconciler 创建新 attempt 或收敛 failed。
- cancel 晚于 succeeded 时不改变终态。
- 内部取消收敛为 `cancelled` 时不触发外部 Callback，并写 skipped 审计。
- 超时或重试耗尽收敛为 `failed` 时，订阅 `job.failed` 的调用方必须收到 Callback。
- Callback 失败只影响 callback 状态，不改变 Job 终态。
- Callback outbox 被 lease 时聚合为 `delivering`；投递失败但仍可重试时聚合回 `pending`。
- Callback outbox lease 过期时，Reconciler 能把 `leased/delivering` 收敛回可重试状态，且不改变 Job 终态。
- Callback 投递前必须重新校验 URL 和 DNS 解析结果，校验失败只影响 callback 状态，不改变 Job 终态。
- 配置 callback\_url 但事件未订阅时创建 `callback_outbox=skipped`。
- Callback 超过最大次数进入 dead letter，并聚合为 `jobs.callback_status='failed'`。
- Reconciler 的异常收敛路径必须写 `job_events`，不得 silent catch、伪成功或无审计修改非终态 Job。
- 超长 error 写入前被截断，并带 `truncated=true`。
- 终态 Job 在 callback 收敛且过期后 soft delete。
- 数据模型不包含无 MVP 行为支撑的预留字段；每个保留字段在字段语义表中有明确职责。

## 十八、待确认问题

进入实现前仍需确认：

- 软删除后的查询返回 404 还是 410。
- `idempotency_key` 是否只在 Job 保留期内有效；MVP 默认保留期内有效。
- 哪些 `job_type` 允许 `max_attempts > 1`。
- `jobs.result` 最大 JSON 体积；超过上限必须使用 `result_ref`。
