# 服务合同

本文记录当前对外 HTTP、Job、Callback 和 Billing 合同。内部表结构、lease、outbox 和 recovery 以 `docs/current/job-kernel.md` 为准。

## HTTP Envelope

受保护业务接口的成功响应由统一 HTTP envelope 包装：

```text
HttpEnvelope[T]
  code
  msg
  data: T
  request_id
  server_time
```

route 函数只返回内层 data schema；外层 envelope 由应用统一包装。

错误响应使用 `ErrorEnvelope`，错误码事实源是 `app/core/error_registry.py`。

## 认证

除 `/health` 和 `/healthz` 外，请求默认需要 Bearer token：

```http
Authorization: Bearer <service-key>
```

`X-AI-Service-Caller-ID` 是可选调用方标识；不传时使用 `default` caller，传入非法格式会返回未授权错误。

本地可以通过 `DISABLE_HTTP_AUTH_HEADER=true` 关闭 Bearer 校验；可以通过 `DISABLE_CALLER_ID_HEADER=true` 忽略 `X-AI-Service-Caller-ID` 并统一使用 `default` caller。`Settings` 会要求 DB/Redis 指向 loopback；本地 `dev.sh` / `start-api.sh` 启动入口还会要求 `API_HOST` 是 loopback。绕过这些启动入口时，调用方必须自行保证 API 不绑定公开地址。

## 创建 Job

```http
POST /api/v1/ai-jobs/jobs
```

请求体：

```text
CreateJobRequest
  client_request_id
  job_type
  job_params
  callback?
  metadata
  options?
```

`job_params` 由具体 `job_type` 的 Params schema 校验。`options.idempotency_mode` 当前支持：

- `reject_duplicate`
- `return_existing`

成功响应：

```text
HttpEnvelope[JobResponseData]
  data.job -> JobEnvelope
```

## 查询 Job

```http
GET /api/v1/ai-jobs/jobs/{job_id}
```

成功响应：

```text
HttpEnvelope[JobResponseData]
  data.job -> JobEnvelope
```

`JobEnvelope.job_status` 当前只允许：

- `queued`
- `running`
- `succeeded`
- `failed`

非终态 Job 的 `job_result` 和 `job_error` 必须为 `null`。成功终态 Job 不允许携带 `job_error`；失败终态 Job 必须携带 `job_error`，且不允许携带 `job_result`。

## 查询 Job Billing

```http
GET /api/v1/ai-jobs/jobs/{job_id}/billing
```

成功响应：

```text
HttpEnvelope[JobBillingResponseData]
  data.billing -> BillingEnvelope
```

Job billing 从 `ai_call_ledger_entries` 中 `scope_type="job"` 且 `scope_id=job_id` 的 ledger 行聚合。对 workflow root Job，internal child Job 的 AI 调用也写入 root Job scope；ledger 行仍保留 child `job_id`、`attempt_id` 和 `job_type` 作为诊断归因。Billing 查询是 Job scope 的公开投影，不是 `job_type` 自定义 result 字段。

```text
BillingEnvelope
  schema_version
  scope_type
  scope_id
  status
  kind
  currency
  total_cost_amount
  usage_units
  pricing_refs
  ai_call_count
  billable_call_count
  unbillable_call_count
  failed_call_count
  diagnostic_reason
  finalized_at
```

`status` 当前允许 `estimated`、`not_billable`、`incomplete` 和 `failed`。`kind` 当前为 `cost_estimate`。

如果 billing 功能关闭、Job 未终态、ledger 不完整或无可计费调用，应返回明确状态或错误，不返回伪造的空成本事实。

## Callback

Callback 是服务向调用方 `callback.url` 投递的 Job 终态事件。Callback payload 不套 HTTP envelope。

```text
CallbackEnvelope
  event
  event_id
  attempt
  sent_at
  trigger_request_id
  caller_id
  job: JobEnvelope
```

规则：

- `event` 只允许 `job.succeeded` 或 `job.failed`。
- `event` 必须与 `job.job_status` 匹配。
- `job` 必须是终态 Job snapshot。
- `event_id` 是 Callback 事件幂等键，调用方应按它去重。
- `attempt` 当前是 Callback 事件 payload 中的尝试序号快照，不表示 Job 执行 attempt 编号，也不能作为重试次数事实源；调用方去重应使用 `event_id`。
- Callback 投递成功或失败不改变 Job 终态。

调用方接受 Callback 时应返回 `2xx`、`Content-Type: application/json`，且 body 必须是 `CallbackResponseEnvelope`：

```text
CallbackResponseEnvelope
  accepted: true
  msg?
  details
```

`204`、空 body、非 JSON body、缺少 `accepted`、`accepted` 不是 boolean，或 `accepted=false` 都会被视为未接受。非 2xx、超时、网络错误或未接受响应会触发 Callback 重试，直到成功或达到最大尝试次数。

## 模型与 Prompt 元信息

```http
GET /api/v1/ai-jobs/models
GET /api/v1/ai-jobs/prompt-templates
```

模型配置来自 `MODEL_CONFIG_PATH`，Prompt 配置来自 `PROMPT_CONFIG_PATH`。这两个接口只暴露当前服务允许调用方看到的元信息，不暴露 provider 密钥或内部 pricing 明细。

`GET /models` 成功响应：

```text
HttpEnvelope[ModelsResponse]
  data.default_model_id
  data.models[]
  data.billing_enabled?
  data.cost_estimate_available?
```

`ModelsResponse.models[]` 的单个模型条目包含：

```text
ModelOut
  id
  name
  provider
  enabled
  capabilities
  input_media_types
  output_media_types
  context_window
  supports_json_output
  notes
```

`capabilities`、`input_media_types` 和 `output_media_types` 是调用方选择模型需要的公开能力元信息。`capabilities` 使用本服务定义的稳定能力值，不直接透传 provider 原始能力名；`input_media_types` 和 `output_media_types` 使用 MIME type。未来可以新增能力值或媒体类型，调用方应忽略未知值。

`pricing_ref`、价格矩阵、provider raw usage schema、provider key、provider model、LiteLLM model、required env 和 generation 参数不属于公开模型合同。

兼容性说明：Phase 2 为 `ModelOut` 新增 `capabilities`、`input_media_types` 和 `output_media_types`。宽松 JSON 客户端可以忽略新增字段；严格 schema 或生成 SDK 客户端需要同步更新模型定义。
