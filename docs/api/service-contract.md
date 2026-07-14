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

`request_id` 是请求追踪 ID。调用方可以通过 `X-Request-ID` 传入 1 到 128 个字符的 ASCII 字母、数字、点号、下划线、冒号或连字符；服务会在响应 envelope 和 `X-Request-ID` 响应头中返回同一个值。调用方不传时，服务端为本次请求生成 32 位小写 UUID hex。传入非法值时返回 `REQUEST_ID_INVALID` 错误 envelope，并使用服务端生成的 `request_id` 标记该错误响应。

错误响应使用 `ErrorEnvelope`。错误码运行时查询入口是 `app/core/error_registry.py`；通用错误由 core 维护，业务错误由所属模块声明后注册到同一个全局 registry。

## 认证

除 `/health` 和 `/healthz` 外，请求默认需要 Bearer token：

```http
Authorization: Bearer <service-key>
```

`X-AI-Service-Caller-ID` 是可选调用方标识；不传时使用 `default` caller，传入非法格式会返回未授权错误。当前合同假设本服务只接收一个可信上游，`X-AI-Service-Caller-ID` 不是多租户安全边界；如果未来同一服务密钥下接入多个互不信任 caller，`caller_id` 必须改为由服务端校验后的凭证派生。

本地可以通过 `DISABLE_HTTP_AUTH_HEADER=true` 关闭 Bearer 校验；可以通过 `DISABLE_CALLER_ID_HEADER=true` 忽略 `X-AI-Service-Caller-ID` 并统一使用 `default` caller。`Settings` 会要求 DB/Redis 指向 loopback；本地 `dev.sh` / `start-api.sh` 启动入口还会要求 `API_HOST` 是 loopback。绕过这些启动入口时，调用方必须自行保证 API 不绑定公开地址。

`APP_ENV=test` 和 `APP_ENV=prd` 是发布模式，启动时会拒绝本地绕过认证、insecure callback、本地对象存储和明显占位的密钥。`TASKIQ_BROKER_KIND` 可显式选择 `redis_stream` 或 `redis_list`；其中 `redis_stream` 需要 Redis 6.2+ 的 `XAUTOCLAIM` 命令，Redis 5 环境应使用 `redis_list`。`.env.dev`、`.env.test` 和 `.env.prd` 不属于项目维护文件；是否使用这些本地自管文件由 `ENV_FILE` 或平台环境变量显式决定，服务不会根据 `APP_ENV` 自动加载。

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

外部提交准入由 `job_type.visibility` 和 `APP_ENV` 共同决定：

| APP_ENV | 可外部提交的 job_type |
|---|---|
| `local` / `dev` | `visibility="public"` 或 `visibility="demo"` |
| `test` / `prd` | 仅 `visibility="public"` |

`visibility="internal"` 的 `job_type` 只供服务内部 workflow child 使用，任何环境都不能被外部直接提交。不允许提交的 `job_type` 返回 `INVALID_JOB_TYPE`。

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

`JobEnvelope` 状态字段规则：

| `job_status` | `job_result` | `job_error` | `cost` | `usage` |
|---|---|---|---|---|
| `queued` | 必须为 `null` | 必须为 `null` | 必须为 `null` | 必须为 `null` |
| `running` | 默认必须为 `null`；只有 `running` 属于具体 `job_type` 的 `result_snapshot_statuses` 时才允许非空 | 必须为 `null` | 必须为 `null` | 必须为 `null` |
| `succeeded` | 按具体 `job_type` 的公开结果 schema 返回 | 必须为 `null` | 可返回 Job 级费用快照 | 可返回 Job 级用量摘要 |
| `failed` | 默认必须为 `null`；只有 `failed` 属于具体 `job_type` 的 `result_snapshot_statuses` 时才允许非空 | 必须非空 | 可返回 Job 级费用快照 | 可返回 Job 级用量摘要 |

`result_snapshot_statuses` 是 `job_type` 的能力声明，默认是空集合；当前只允许声明 `running` 和 `failed`。支持运行中或失败结果快照的 `job_type` 必须复用同一个公开 `job_result` schema，不暴露 internal child Job、workflow node、attempt 或 worker 细节。快照只表示当前已经可公开展示的业务结果；调用方仍必须以 `job_status` 判断 Job 是否终态。

`job_error` 是公开错误投影。workflow root 由 internal child 失败投影为 `WORKFLOW_CHILD_FAILED` 时，不暴露 child job id、workflow node key、provider 原始错误、adapter 内部错误或堆栈细节；这些内部诊断只属于 child Job、运维查询、日志或审计事件。

`job_progress.percent` 是当前唯一保证返回的进度字段，取值为 `0` 到 `100`。服务当前可能同时返回 `stage` 和 `message`，但调用方不能依赖这两个字段一定存在，也不能用它们判断 Job 是否成功或失败。

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
- `job` 必须是终态 Job snapshot，且与 `GET /api/v1/ai-jobs/jobs/{job_id}` 成功响应中的 `data.job` 使用同一 `JobEnvelope` 结构。
- `event_id` 是 Callback 事件幂等键，调用方应按它去重。
- `attempt` 当前是 Callback 事件 payload 中的尝试序号快照，不表示 Job 执行 attempt 编号，也不能作为重试次数事实源；调用方去重应使用 `event_id`。
- Callback 投递成功或失败不改变 Job 终态。
- 服务会发送 `X-Callback-Timestamp` 和 `X-Callback-Signature` header。
- `X-Callback-Signature` 当前格式为 `sha256=<hex>`，签名内容是 `timestamp + "." + raw_body` 的 HMAC-SHA256。
- 调用方应使用双方约定的 Callback 签名密钥校验签名，并结合 `X-Callback-Timestamp` 与 `event_id` 做重放防护。
- 同一 `event_id` 重复投递但已成功处理时，调用方仍应返回 `accepted=true`；`accepted=false` 表示拒收，会触发重试直到成功或达到最大尝试次数。

调用方接受 Callback 时应返回 `2xx`、`Content-Type: application/json`，且 body 必须是 `CallbackResponseEnvelope`：

```text
CallbackResponseEnvelope
  accepted: true
  msg?
  details
```

`204`、空 body、非 JSON body、缺少 `accepted`、`accepted` 不是 boolean，或 `accepted=false` 都会被视为未接受。非 2xx、超时、网络错误或未接受响应会触发 Callback 重试，直到成功或达到最大尝试次数。

## 模型、语种与 Prompt 元信息

```http
GET /api/v1/ai-jobs/models
GET /api/v1/ai-jobs/models?job_type=poster_title_image
GET /api/v1/ai-jobs/languages
GET /api/v1/ai-jobs/prompt-templates?job_type=poster_title_image
```

模型运行时配置来自 `MODEL_CONFIG_PATH`，但 `GET /models` 只返回其中 `public` 块声明的调用方可见投影。顶层 `adapter`、`provider_model`、`adapter_model`、`pricing_ref`、`requires_env` 和 `generation` 等运行时字段不属于 `/models` 合同，更新这些字段不应改变调用方看到的模型信息。

`GET /models` 未传 `job_type` 时返回服务级公开模型投影；传入 `job_type` 时，如果该 `job_type` 目录下存在 `models.yaml`，响应会按该任务允许调用方选择的模型列表过滤同一套公开投影。没有 `models.yaml` 的已注册 `job_type` 使用服务级公开模型投影；未知 `job_type` 返回 `INVALID_JOB_TYPE`。

语种目录来自 `app/core/language_catalog.py`，Prompt 配置来自 `PROMPT_CONFIG_PATH` 和各 `job_type` 垂直目录下的 `prompts.yaml`。这些接口只暴露当前服务允许调用方看到的元信息，不暴露 provider 密钥或内部 pricing 明细。

`GET /prompt-templates` 支持可选 query 参数 `job_type`。未传时默认使用 `poster_title_image`，响应只返回该 `job_type` 的模板；传入未知 `job_type` 会返回 `INVALID_JOB_TYPE`。

`GET /prompt-templates` 成功响应：

```text
HttpEnvelope[PromptTemplateResponseData]
  data.version
  data.job_type
  data.name
  data.description
  data.prompt_blocks[]
```

`PromptTemplateResponseData.prompt_blocks[]` 的单个提示词块包含：

```text
PromptBlockTemplate
  key
  role
  label
  default_content
```

`key` 是稳定提示词块标识，调用方可按对应 `job_type` 的任务创建合同回填到 `prompt_overrides`。`role`、`label` 和 `default_content` 只描述默认模板展示和覆盖入口；服务端仍会在执行时把模板块、任务参数和固定业务编排拼成最终模型请求。

`data.version` 表示本次返回的这个 `job_type` 模板版本。模板由 `job_type` 垂直目录维护时，该版本来自对应目录的 `prompts.yaml`，不等同于全局 `PROMPT_CONFIG_PATH` 文件版本。

`GET /models` 成功响应：

```text
HttpEnvelope[ModelsResponse]
  data.default_model_id
  data.models[]
  data.billing_enabled?
  data.cost_estimate_available?
```

未传 `job_type` 时，`data.default_model_id` 是服务级默认模型；传入 `job_type` 且存在任务级 `models.yaml` 时，`data.default_model_id` 是该任务的 `public_model_selection.default_model_id`，`data.models[]` 只包含该任务 `public_model_selection.allowed_model_ids` 中当前可用模型的公开投影。任务级 `internal_models` 不进入响应。

`ModelsResponse` 的稳定骨架是 `default_model_id` 和 `models[]`。服务可以新增可选字段或新增 `models[]` 内的公开能力值；删除字段、重命名字段、改变字段类型、把内部运行时字段加入响应，或把公开字段改为 provider 原始配置，都属于 breaking change。

`ModelsResponse.models[]` 的单个模型条目包含：

```text
ModelOut
  id
  name
  model_type
  provider
  enabled
  capabilities
  input_media_types
  output_media_types
  limits
  features
  parameters[]
  notes
```

`model_type` 是模型目录粗分类，当前取值为 `text`、`image`、`audio` 或 `video`。调用方可以用 `model_type` 做目录分组或粗筛，但具体可执行任务必须看 `capabilities`。例如后续语音转文本模型可以是 `model_type=audio` 且输出 `text/plain`。

`provider` 是调用方展示用的公开 provider 标签，不表示执行路由、provider 原始模型名或 adapter 选择。调用方不能依赖它推导计费、调用协议或真实 provider 参数。

`capabilities`、`input_media_types`、`output_media_types`、`limits`、`features` 和 `parameters` 是调用方选择模型需要的公开能力元信息。`capabilities` 使用本服务定义的稳定能力值，不直接透传 provider 原始能力名；`input_media_types` 和 `output_media_types` 使用 MIME type。未来可以新增能力值或媒体类型，调用方应忽略未知值。

`limits` 和 `features` 是类型化公开元信息。文本模型当前会在 `limits.context_window` 暴露上下文窗口，并在 `features.supports_json_output` 暴露是否支持 JSON 输出。调用方应读取 `limits` 和 `features`，不要依赖跨类型顶层字段。

`parameters[]` 是模型目录中允许对调用方展示的可配置参数 schema。单个参数包含：

```text
ModelParameterOut
  name
  label
  type
  required
  default
  options?
  min?
  max?
```

`label` 是展示名称。`type` 当前取值为 `string`、`integer`、`number`、`boolean` 或 `select`。`select` 参数使用 `options` 表达允许值；`integer` 和 `number` 参数可以使用 `min` / `max` 表达数值范围。不适用的可选字段会从响应中省略。模型支持某个公开参数不代表所有 `job_type` 都允许提交该参数；最终可提交字段仍由对应 `job_type` 的 `job_params` 合同和服务端校验决定。

`adapter`、`adapter_model`、`pricing_ref`、价格矩阵、provider raw usage schema、provider key、provider model、required env、generation 参数和非公开 provider 参数不属于公开模型合同。

`GET /languages` 成功响应：

```text
HttpEnvelope[LanguagesResponse]
  data.languages[]
```

`LanguagesResponse.languages[]` 的单个语种条目包含：

```text
LanguageOut
  language
  display_name
  native_name
```

`language` 是提交任务时使用的程序化语种代码，当前取值来自 [`业务语种规范.md`](业务语种规范.md) 的三方语种表。`display_name` 和 `native_name` 只用于展示，调用方不应基于展示名称做业务判断。`in` 是三方合同中的印尼语代码，本服务不会在内部目录接口中映射为 `id`。

合同说明：当前 `ModelOut` 使用 `model_type`、`capabilities`、`input_media_types`、`output_media_types`、`limits`、`features` 和 `parameters` 描述模型公开能力；语种目录通过 `GET /languages` 独立暴露。生成 SDK 客户端应以本文字段为准同步模型和语种定义。
