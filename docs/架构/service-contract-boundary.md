# AI Job 服务合同边界

```text
Status: Current Contract
Scope: HTTP envelope, Job envelope, Error envelope, Callback envelope, Billing envelope
Current truth: code, tests, docs/架构/project-standards-code-facts.md
```

本文定义当前对外合同和内部事实 owner 的边界。它不重复所有字段细节；字段事实以代码、测试和 [AI Job 服务项目规范与骨架（代码事实版）](project-standards-code-facts.md) 为准。

## 合同原则

- Route handler 返回裸 `DataSchema`，不手写 `code/msg/data/request_id/server_time`。
- API 前缀下的 JSON `200` 响应由 `SuccessEnvelopeMiddleware` 包装为 `HttpEnvelope[TData]`。
- HTTP 错误响应由 exception handler 和 error registry 生成 `ErrorEnvelope`。
- Job、Callback 和 Billing 各自回答不同问题，不能互相嵌入未版本化字段。
- 当前实现事实只以 current truth 为准；Target Design、Candidate 和 Plan 文档不能覆盖当前合同。
- `callback-job-unified-envelope-design.md` 当前仍是 Candidate；Job / Callback 的 current contract 以本文和 current truth 为准。
- `ai-gateway-layer-design.md` 是 Target Design Baseline；当前公开 Billing 合同以本文、`project-standards-code-facts.md` 和代码为准。

## HTTP 合同

当前公开 HTTP 路由以默认 `SERVICE_API_PREFIX=/api/v1/ai-jobs` 表达：

| 完整路径 | Data schema | owner |
|---|---|---|
| `GET /api/v1/ai-jobs/models` | `ModelsResponse` | `app/api/routes/meta.py`、`app/core/model_registry.py` |
| `GET /api/v1/ai-jobs/prompt-templates` | `PromptTemplatesResponse` | `app/api/routes/meta.py`、`app/core/prompt_templates.py` |
| `POST /api/v1/ai-jobs/jobs` | `JobResponseData` | `app/api/routes/jobs.py`、`app/services/jobs.py` |
| `GET /api/v1/ai-jobs/jobs/{job_id}` | `JobResponseData` | `app/api/routes/jobs.py`、`app/services/jobs.py` |
| `GET /api/v1/ai-jobs/jobs/{job_id}/billing` | `JobBillingResponseData` | `app/api/routes/jobs.py`、`app/services/billing.py` |

`app/api/operations.py` 使用去掉 `SERVICE_API_PREFIX` 的相对 path 作为 operation registry 的 path；公开文档描述外部 URL 时必须使用完整前缀路径。

当前 HTTP 成功外壳：

```text
HttpEnvelope[TData]
  code: string
  msg: string
  data: TData | null
  request_id: string
  server_time: string
```

当前规则：

- 成功 `code` 固定为 `"0"`。
- `server_time` 是 UTC ISO datetime 字符串。
- `X-Request-ID` 合法时透传，未传时服务生成。
- 健康检查、OpenAPI、Swagger / ReDoc 不套业务 HTTP envelope。
- 非 `200`、非 JSON 或非 API 前缀响应不由 `SuccessEnvelopeMiddleware` 包装。

## 鉴权与调用方边界

当前安全合同：

- `/health` 和 `/healthz` 不需要鉴权。
- API 前缀下的公开业务路由需要 `Authorization: Bearer <SERVICE_API_KEY>`。
- `X-AI-Service-Caller-ID` 参与调用方隔离；未传时当前使用 `default`。
- `caller_id` 只允许 `^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,63}$`。
- `POST /api/v1/ai-jobs/jobs` 的幂等边界是 `caller_id + client_request_id`。
- `GET /api/v1/ai-jobs/jobs/{job_id}` 和 `GET /api/v1/ai-jobs/jobs/{job_id}/billing` 只能读取同一 `caller_id` 下的未删除 Job。
- 本地开发可以通过配置关闭 Bearer header 或 caller header 校验；该模式只允许 DB / Redis 指向 loopback，不是生产合同。

## 错误合同

当前 HTTP 错误外壳：

```text
ErrorEnvelope
  code: string
  msg: string
  data: object | null
  request_id: string
  server_time: string
```

owner：

- 错误 reason、字符串 code、HTTP status、公开 msg、retryable、scope 和 owner 来自 `app/core/error_registry.py`。
- `AppError`、`RequestValidationError` 等由 `app/main.py` 中的 exception handler 转换为 `ErrorEnvelope`。
- `app/schemas/errors.py` 中的 `JobErrorDetail` / `CallbackErrorDetail` 是 Job 和 Callback 内部投影错误，不是当前 HTTP error envelope 的 `data.error` 外壳。

当前规则：

- `ErrorEnvelope.data` 直接承载 details 或 `null`。
- HTTP status 以 error registry 的 `ErrorSpec.http_status` 为准，不由 route、service 或 exception handler 调用点临时覆盖。
- 当前 HTTP 错误不使用 `{"error": ...}` 嵌套结构。
- 如果未来升级统一 `ErrorDetail` 嵌套结构，必须作为独立合同迁移处理，并同步 OpenAPI、测试和调用方文档。

## Job 合同

`JobEnvelope` 回答“这个异步 Job 当前是什么状态，是否有公开结果或公开错误”。

owner：

- 对外字段 schema 在 `app/schemas/jobs.py`。
- 视图构造和 job_type 结果投影在 `app/services/jobs.py`、`app/jobs/registry.py` 和对应 `JobExecutor`。
- 状态事实来自 `jobs`、`job_attempts`、`callback_outbox` 和 `JobRepo` 受控迁移。

当前公开状态：

```text
queued
running
succeeded
failed
```

当前规则：

- `queued` / `running` 不允许携带 `job_result` 或 `job_error`。
- `succeeded` 不允许携带 `job_error`。
- `failed` 必须携带 `job_error`，且不允许携带 `job_result`。
- `JobEnvelope.callback` 总是存在；未配置 callback 时为 `not_configured`。
- `client_request_id` 当前 schema 允许 `null`，正常创建路径会使用调用方传入值；当前公开视图在缺失时可能归一为 `""`，调用方不应依赖 `null` 或空字符串表达业务语义。
- `job_error.details` 当前会直接承载规范化后的错误 details；调用方可以用于诊断展示，但不应依赖其中未在错误合同中冻结的内部键。后续实现不得新增泄漏异常栈、provider 原文、SQL、runtime refs 或对象存储内部路径的 details 键。
- `JobEnvelope` 不承诺 attempt id、worker id、lease token、execution token、provider 原始响应、AI call ledger 行或 billing 明细。
- `JobEnvelope` 不默认携带 `billing`；计费信息通过独立 Job billing 查询获得。

## Callback 合同

`CallbackEnvelope` 回答“某个 Job 终态事件已经投递给调用方”。

owner：

- Callback request schema 在 `app/schemas/callbacks.py`。
- 投递、签名和 ACK 校验在 `app/services/callbacks.py`。
- 投递事实来自 `callback_outbox` 和 `JobRepo`。

当前规则：

- Callback body 不套 HTTP `code/msg/data`。
- Callback URL 在创建 Job 时执行安全校验；非本地默认不允许不安全回调 URL。
- Callback 投递会带 `X-Callback-Timestamp` 和 `X-Callback-Signature`，接收方应按双方共享 secret 验签。
- `event` 只允许 `job.succeeded` 或 `job.failed`，且必须和 `job.job_status` 匹配。
- `job` 必须是终态 `JobEnvelope`。
- Callback 是 at-least-once 投递语义；接收方必须使用稳定 `event_id` 做幂等。
- Callback ACK 必须是 JSON object，并至少包含布尔字段 `accepted`。
- `204`、空 body、非 JSON、非 object、缺少 `accepted` 或 `accepted` 非 bool 都不是合法 ACK。
- `callback.last_error.details` 可用于诊断展示，但调用方不应依赖未冻结内部键；后续实现不得新增泄漏密钥、签名材料、完整上游响应体或内部 outbox lease 信息的 details 键。
- Callback payload 默认不携带 billing；未来如需携带，必须与轮询查询合同同步升级。

## Billing 合同

`BillingEnvelope` 回答“某个 scope 内发生了哪些 AI provider call，usage 和 cost estimate 聚合是什么”。

owner：

- Billing schema 在 `app/schemas/billing.py`。
- Job billing route 在 `app/api/routes/jobs.py`。
- Billing 聚合在 `app/services/billing.py`。
- 调用事实来自 `ai_call_logs`，由 AI gateway facade 和 repository 写入。

当前公开 billing 路由只开放 Job scope：

```text
GET /api/v1/ai-jobs/jobs/{job_id}/billing
  -> HttpEnvelope[JobBillingResponseData]
  -> data.billing: BillingEnvelope
```

当前规则：

- Job billing 只在 Job 到达 `succeeded` 或 `failed` 后可查询。
- 无 AI call 的 Job 返回 `not_billable`，不伪造 `estimated 0`。
- `BillingEnvelope` 是 ledger 的读取投影，不反向修改 Job、attempt、callback 或 provider 调用结果。
- `ai_call_logs` 是 AI provider call 事实源；`jobs` 表不保存 provider usage / cost 明细。
- `diagnostic_reason` 当前是机器可读字符串，但不是已冻结枚举；调用方可以记录和展示，不应在未版本化合同前依赖完整枚举分支。
- `usage_units` 当前是开放 key-value map；新增 usage key 不应视为 breaking change。
- 通用 scope billing、caller 时间窗口聚合、批量导出和同步 AI 能力接口都不是当前公开 HTTP 合同。

## 不进入当前公开合同的内部事实

以下事实可以出现在数据库、日志、内部诊断或后续 ops 能力中，但不得在未版本化情况下进入公开 envelope：

- `job_attempts` 的内部状态、publish status、worker id、lease token、heartbeat 和 timeout 细节。
- `jobs.execution_token`、`execution_generation`、runtime refs、对象存储内部路径和 cleanup 标记。
- `callback_outbox` 的 lease、dead letter、delivery deadline、HTTP 原始响应体。
- `job_events`。
- `ai_call_logs` 的 provider 原始错误、request / response hash、usage detail、pricing snapshot 内部字段。
- Prompt 全文、模型完整输出、密钥、provider 原始响应或高基数诊断字段。

## 演进规则

- 新增 HTTP route 必须更新 operation registry、schema registry、error registry、OpenAPI / contract tests 和必要文档。
- 新增 `job_type` 必须走 `JobExecutor` 和 job type registry，不修改通用 `JobEnvelope` 外壳。
- 新增公开 Job 状态、取消能力、Callback 事件、Billing 查询维度或 ErrorEnvelope 结构，都必须作为合同升级处理。
- Target Design 或 Plan 文档只能提出方向；合同行为变化必须落到代码、测试和 current contract 文档。
