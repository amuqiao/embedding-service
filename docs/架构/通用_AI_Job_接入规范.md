# 通用 AI Job 接入规范

本文定义本项目要收口的通用 AI Job 合同：调用方只依赖稳定 Job 骨架，具体能力只通过 `job_type`、`job_params` 和 `result` 扩展。

## 文档职责

本文负责说明：

- 调用方如何创建异步 Job、查询状态、接收终态 Callback。
- 服务如何保持统一的 Job 生命周期、幂等、错误、结果和回调结构。
- 新增 `job_type` 时，能力实现方必须遵守哪些最小边界。

本文不负责定义某个具体业务能力的参数、Prompt、产物键、业务步骤、内部执行计划或领域结果。任何具体能力都只是 `job_type` 的一个实现，不应反向污染通用 Job 合同。

## 一、核心边界

通用 Job 层只回答一个问题：**如何把一次能力调用收敛成可创建、可追踪、可回调、可复用的异步任务。**

```text
调用方
  ├─ 选择 job_type
  ├─ 组装 job_params
  ├─ 提交 Job
  ├─ 保存 job_id / client_request_id
  ├─ 轮询 JobView
  └─ 接收 CallbackEnvelope

AI Job 服务
  ├─ 校验鉴权和 caller_id
  ├─ 校验稳定 Job 骨架
  ├─ 根据 job_type 找到能力实现
  ├─ 校验和归一化 job_params
  ├─ 创建 queued Job
  ├─ 执行任务
  ├─ 写入 result 或 error
  └─ 发送终态 Callback
```

稳定层和扩展层必须分开：

| 层级 | 归属 | 稳定性 |
|---|---|---|
| Job 骨架 | `client_request_id`、`job_type`、`job_params`、`callback`、`metadata`、`options` | 长期稳定 |
| 生命周期 | `queued`、`running`、`succeeded`、`failed` | 长期稳定 |
| 查询视图 | `JobView` | 长期稳定 |
| 回调包装 | `CallbackEnvelope` | 长期稳定 |
| 任务入参 | `job_params` 内部结构 | 由 `job_type` 定义 |
| 任务结果 | `result.artifacts`、`result.signals` 的具体内容 | 由 `job_type` 定义 |

通用顶层不得新增具体能力字段。模型、输入源、Prompt、业务 ID、领域选项、执行细节都必须放在 `job_params` 或 `metadata` 中，并由对应 `job_type` 解释。

## 二、API 总览

默认 API 前缀：

```text
/api/v1/ai-jobs
```

端点：

| 方法 | 路径 | 说明 | 鉴权 |
|---|---|---|---|
| `GET` | `/health` | liveness | 否 |
| `GET` | `/healthz` | readiness | 否 |
| `GET` | `/api/v1/ai-jobs/models` | 模型能力发现 | 是 |
| `GET` | `/api/v1/ai-jobs/prompt-templates` | Prompt 模板发现 | 是 |
| `POST` | `/api/v1/ai-jobs/jobs` | 创建异步 Job | 是 |
| `GET` | `/api/v1/ai-jobs/jobs/{job_id}` | 查询 JobView | 是 |

`/models` 和 `/prompt-templates` 是相关能力发现接口，不属于 Job 创建骨架。某个 `job_type` 是否需要模型、Prompt 或其他运行时字段，由该 `job_type` 自己决定。

## 三、鉴权与调用方身份

除健康检查外，请求必须携带：

```http
Authorization: Bearer <SERVICE_API_KEY>
```

调用方可选携带：

```http
X-AI-Service-Caller-ID: <caller-id>
```

`caller_id` 用于隔离 Job 查询和幂等命名空间。未传时默认为：

```text
default
```

`caller_id` 必须匹配：

```text
^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,63}$
```

同一个 `job_id` 只能由创建它的 `caller_id` 查询。跨 `caller_id` 查询会被视为不存在。

请求可选携带：

```http
X-Request-ID: <request-id>
```

`X-Request-ID` 只接受 ASCII 字母、数字、连字符和下划线，最长 128 字符。不符合规则时服务生成新的 UUID，并在响应头返回最终使用的 `X-Request-ID`。

## 四、CreateJobRequest

创建 Job：

```http
POST /api/v1/ai-jobs/jobs
Content-Type: application/json
```

请求顶层只允许以下字段：

```json
{
  "client_request_id": "optional-idempotency-key",
  "job_type": "capability.name",
  "job_params": {},
  "callback": {
    "url": "https://caller.example.com/ai-job-callback",
    "events": ["job.succeeded", "job.failed"]
  },
  "metadata": {},
  "options": {
    "priority": "normal",
    "timeout_seconds": 300
  }
}
```

字段规则：

| 字段 | 必填 | 规则 |
|---|---:|---|
| `client_request_id` | 否 | 调用方幂等键，最长 255 字符。 |
| `job_type` | 是 | 能力路由键，必须已注册。 |
| `job_params` | 否 | 任务入参对象，默认 `{}`。内部结构由 `job_type` 定义。 |
| `callback` | 否 | 终态回调配置。 |
| `metadata` | 否 | 调用方透传元信息，默认 `{}`。服务不解释业务语义。 |
| `options` | 否 | 通用执行选项。当前支持 `priority` 和 `timeout_seconds` 的结构校验。 |

`options.priority` 取值：

```text
low | normal | high
```

`options.timeout_seconds` 必须大于 0。当前它属于合同字段和入参校验项，不表示所有 runtime 都已经按该值覆盖内部超时。

`callback.events` 为空或未传时默认：

```json
["job.succeeded", "job.failed"]
```

`POST /jobs` 顶层只接受本节声明的字段。任何能力专属参数都必须放入 `job_params`，并由对应 `job_type` 的参数 schema 校验。

## 五、CreateJobResponse

创建成功或命中幂等时返回 `202 Accepted`：

```json
{
  "job_id": "7b5c2c62-9a3a-41b7-bd41-f24a5d34a099",
  "client_request_id": "optional-idempotency-key",
  "job_type": "capability.name",
  "status": "queued",
  "status_url": "/api/v1/ai-jobs/jobs/7b5c2c62-9a3a-41b7-bd41-f24a5d34a099",
  "created_at": "2026-06-15T10:00:00Z"
}
```

创建响应只表示服务已经接单，不包含执行结果。调用方必须通过 `status_url` 轮询，或等待 Callback 获取终态。

## 六、幂等规则

幂等范围：

```text
caller_id + client_request_id + 最近 24 小时
```

当请求包含 `client_request_id` 时，服务会计算请求指纹。指纹内容包括：

- `job_type`
- 归一化后的 `job_params`
- `callback`
- `metadata`
- `options`

处理规则：

| 场景 | 结果 |
|---|---|
| 同一 `caller_id + client_request_id`，请求指纹一致 | 返回已有 Job，不重新创建、不重新投递。 |
| 同一 `caller_id + client_request_id`，请求指纹不一致 | 返回 `409 CLIENT_REQUEST_ID_CONFLICT`。 |
| 不传 `client_request_id` | 每次请求创建新的 Job。 |

调用方应把 `client_request_id` 设计为一次业务能力调用的稳定键，而不是每次 HTTP 请求生成的新随机值。

## 七、JobView

查询 Job：

```http
GET /api/v1/ai-jobs/jobs/{job_id}
```

返回统一 `JobView`：

```json
{
  "job_id": "7b5c2c62-9a3a-41b7-bd41-f24a5d34a099",
  "client_request_id": "optional-idempotency-key",
  "job_type": "capability.name",
  "status": "running",
  "progress": {
    "percent": 30,
    "message": "正在执行 chunk",
    "stage": null
  },
  "result": null,
  "error": null,
  "callback": {
    "status": "pending",
    "attempts": 0,
    "next_retry_at": null,
    "last_error": null
  },
  "metadata": {},
  "created_at": "2026-06-15T10:00:00Z",
  "started_at": "2026-06-15T10:00:10Z",
  "finished_at": null
}
```

状态：

| 状态 | 含义 |
|---|---|
| `queued` | Job 已创建，等待 worker 领取或恢复投递。 |
| `running` | Job 正在规划、执行、合并或写入结果。 |
| `succeeded` | Job 成功结束，`result` 非空，`error=null`。 |
| `failed` | Job 失败结束，`result=null`，`error` 非空。 |

当前对外合同没有取消接口，也不产生 `canceled` 状态。

运行中 Job 的结果规则：

```text
queued/running: result = null, error = null
succeeded:      result != null, error = null
failed:         result = null, error != null
```

`callback` 字段表示当前回调投递状态。它可能处于待投递、投递中、已投递、失败或跳过。即使没有配置 Callback，`JobView` 仍保留该结构，调用方不需要处理两套视图。

## 八、JobResult

成功结果统一放在 `result`：

```json
{
  "artifacts": [
    {
      "key": "output",
      "type": "json",
      "label": "Output",
      "apply_mode": null,
      "storage": null,
      "oss_bucket": null,
      "oss_key": null,
      "oss_region": null,
      "content_hash": null,
      "content_size_bytes": null,
      "content": {}
    }
  ],
  "signals": {}
}
```

`artifacts` 是产物列表，适合承载文本、JSON、文件引用或其他可消费输出。

Artifact 字段：

| 字段 | 规则 |
|---|---|
| `key` | 产物稳定键，由 `job_type` 定义。 |
| `type` | 产物类型，由 `job_type` 定义。 |
| `label` | 展示名。 |
| `apply_mode` | 可选，仅允许 `replace`、`append` 或 `null`。 |
| `storage` | 大产物写入对象存储时为 `oss_object`；内联产物为 `null`。 |
| `oss_bucket` | 对象存储 bucket。 |
| `oss_key` | 对象存储 key。 |
| `oss_region` | 对象存储 region。 |
| `content_hash` | 对象内容 hash。 |
| `content_size_bytes` | 对象字节数。 |
| `content` | 内联内容；大产物持久化后通常为 `null`。 |

`signals` 是机器可读的结构化信号对象。调用方需要做自动判断时，应依赖 `signals`，不要解析自然语言 artifact。

`result` 的具体 artifact key、type、signals 字段都由 `job_type` 定义。通用 Job 合同不规定它们的业务含义。

## 九、大产物存储

能力实现方可以把大内容 artifact 声明为对象存储产物。服务会把该 artifact 的 `content` 写入对象存储，并在 `JobView.result` 中返回对象引用：

```json
{
  "key": "output",
  "type": "text",
  "label": "Output",
  "storage": "oss_object",
  "oss_bucket": "bucket",
  "oss_key": "ai-jobs/<job_id>/output.txt",
  "oss_region": "region",
  "content_hash": "sha256:<64 lowercase hex>",
  "content_size_bytes": 12345,
  "content": null
}
```

调用方应根据 `oss_bucket`、`oss_key`、`oss_region` 读取大产物，并按 `content_hash` 做必要校验。

## 十、CallbackEnvelope

Callback 是终态通知，不替代轮询。

触发事件：

```text
job.succeeded
job.failed
```

请求体：

```json
{
  "event": "job.succeeded",
  "event_id": "6f13b3d8-0065-4bfd-bd11-5197fcd1de9a",
  "attempt": 1,
  "sent_at": "2026-06-15T10:01:01Z",
  "job": {
    "job_id": "7b5c2c62-9a3a-41b7-bd41-f24a5d34a099",
    "client_request_id": "optional-idempotency-key",
    "job_type": "capability.name",
    "status": "succeeded",
    "progress": {
      "percent": 100,
      "message": "已完成",
      "stage": null
    },
    "result": {
      "artifacts": [],
      "signals": {}
    },
    "error": null,
    "callback": {
      "status": "delivering",
      "attempts": 0,
      "next_retry_at": "2026-06-15T10:04:01Z",
      "last_error": null
    },
    "metadata": {},
    "created_at": "2026-06-15T10:00:00Z",
    "started_at": "2026-06-15T10:00:10Z",
    "finished_at": "2026-06-15T10:01:00Z"
  }
}
```

`job` 字段与 `GET /jobs/{job_id}` 的 `JobView` 同形。调用方应按 `event_id` 或 `job.job_id + event + attempt` 做幂等消费，并在处理成功后返回任意 `2xx`。

Callback 请求头：

```http
Content-Type: application/json
X-AI-Service-Job-Id: <job_id>
X-AI-Service-Event: job.succeeded
X-AI-Service-Timestamp: 2026-06-15T10:01:01Z
X-AI-Service-Signature: sha256=<hmac>
```

当服务配置 `CALLBACK_SIGNING_SECRET` 时，会发送 `X-AI-Service-Signature`。签名内容：

```text
timestamp + "." + raw_body
```

算法：

```text
HMAC-SHA256
```

最终 header 格式：

```text
sha256=<hex digest>
```

Callback URL 规则：

- 默认必须是 HTTPS。
- 默认禁止指向私有网段 IP。
- 仅当 `ALLOW_INSECURE_CALLBACKS=true` 时，允许本地开发使用 `http://127.0.0.1` 或 `http://localhost`。

## 十一、错误结构

HTTP 错误统一返回：

```json
{
  "error": {
    "code": "INVALID_INPUT",
    "message": "Request validation failed",
    "details": {}
  }
}
```

`JobView.error` 也使用同一错误结构：

```json
{
  "code": "string",
  "message": "string",
  "details": {}
}
```

常见错误：

| HTTP | code | 场景 |
|---:|---|---|
| `401` | `UNAUTHORIZED` | Bearer token 缺失、错误、服务端未配置 API key 或 `caller_id` 非法。 |
| `404` | `JOB_NOT_FOUND` | Job 不存在，或不属于当前 `caller_id`。 |
| `409` | `CLIENT_REQUEST_ID_CONFLICT` | 幂等键在 24 小时内被不同请求复用。 |
| `422` | `INVALID_INPUT` | 请求结构错误、`job_params` 不匹配、Callback URL 非法等。 |
| `422` | `INVALID_JOB_TYPE` | `job_type` 未注册或缺少运行时适配。 |
| `422` | `MODEL_NOT_AVAILABLE` | 能力运行时声明的模型不可用。 |
| `422` | `OSS_FETCH_FAILED` | 读取对象存储输入失败。 |
| `422` | `INPUT_TOO_LARGE` | 输入超过服务限制。 |
| `422` | `INPUT_HASH_MISMATCH` | 输入内容 hash 不一致。 |
| `503` | `QUEUE_FULL` | 活跃 Job 数达到 `MAX_ACTIVE_JOBS`。 |

调用方应以 `error.code` 作为机器判断依据，不应解析 `message`。

## 十二、job_params 扩展原则

`job_params` 是具体能力的唯一入参扩展口。

通用 Job 层对 `job_params` 的职责只有：

1. 接收对象。
2. 根据 `job_type` 找到能力实现。
3. 校验并归一化参数。
4. 保存归一化后的结果。
5. 将归一化参数纳入幂等指纹。

通用 Job 层不解释：

- 输入对象从哪里来。
- 是否需要模型。
- 是否需要 Prompt。
- 是否需要分片。
- 是否需要对象存储。
- 具体业务字段如何命名。
- 具体结果如何消费。

能力实现方必须为自己的 `job_params` 提供明确 schema。非法参数应在创建阶段 fail-fast，不应静默忽略或降级。

## 十三、新增 job_type 的最小边界

新增能力必须遵守通用 Job 合同，而不是扩展顶层 API。

最小边界：

| 边界 | 要求 |
|---|---|
| 能力标识 | 每个能力有稳定 `job_type`。 |
| 入参 schema | 每个能力定义并校验自己的 `job_params`。 |
| 参数归一化 | 创建 Job 前必须把 `job_params` 归一化，归一化结果进入幂等指纹。 |
| 运行时派生 | 能力实现方从 `job_params` 派生内部运行时字段。 |
| 成功结果 | 成功时返回符合 `JobResult` 的 `artifacts` 和 `signals`。 |
| 失败结果 | 失败时写入统一 `JobError`。 |
| 大产物 | 大内容通过 artifact 的对象存储字段交付，不扩展 `JobView` 顶层。 |
| Callback | Callback 的 `job` 字段复用 `JobView`，不定义第二套回调结果模型。 |

新增能力不得：

- 修改 `CreateJobRequest` 顶层字段。
- 把能力专属字段提升到 Job 顶层。
- 为错误参数添加 silent fallback。
- 返回非 `JobResult` 形态的成功结果。
- 让 Callback 使用另一套结果模型。

内部执行计划、分片策略和具体 runtime 属于实现细节，不进入本文的外部接入合同。需要维护这些内容时，应放入单独的实现接入文档。

## 十四、测试要求

新增或修改 `job_type` 至少覆盖：

| 测试类型 | 目标 |
|---|---|
| schema 测试 | `job_params` 合法输入通过，非法输入 fail-fast。 |
| 创建校验 | `_validate_create_request()` 能正确归一化参数并派生运行时字段。 |
| 幂等测试 | 同 `client_request_id` 同请求复用 Job，不同请求返回冲突。 |
| 执行测试 | 能力运行后可以进入正确终态。 |
| finalize 测试 | 最终结果符合 `JobResult`，失败时写入 `JobError`。 |
| artifact 测试 | 大产物写入对象存储，小产物内联返回。 |
| Callback 测试 | Callback 中的 `job` 与轮询 `JobView` 同形。 |

通用合同变化时，还应同步检查 OpenAPI、README、架构文档和 contract tests。

## 十五、调用方接入检查清单

调用方接入前应确认：

- 已拿到 `SERVICE_API_KEY`。
- 已确定 `X-AI-Service-Caller-ID` 命名。
- 已确定目标 `job_type`。
- 已拿到该 `job_type` 的 `job_params` schema。
- 已生成稳定 `client_request_id`。
- 已保存 `job_id`、`client_request_id`、`job_type` 与业务对象的映射。
- 已实现 `GET /jobs/{job_id}` 轮询兜底。
- 已实现 Callback 接收、幂等和签名校验。
- 已按 `result.artifacts` 和 `result.signals` 消费结果。
- 已处理 `failed` 终态和 HTTP 错误码。

联调通过标准：

- `POST /jobs` 返回 `202` 和 `status_url`。
- 同一幂等键、同一请求返回同一 Job。
- 同一幂等键、不同请求返回 `409`。
- Job 最终进入 `succeeded` 或 `failed`。
- 轮询终态和 Callback 中的 `job` 字段一致。
- 大产物 artifact 可按对象存储字段读取并校验 hash。
