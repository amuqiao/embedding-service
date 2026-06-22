# AI Job 服务项目规范与骨架（代码事实版）

本文按原项目规范大纲整理当前仓库已经落地的项目规范。它描述当前代码事实，不把目标态、参考蓝图或尚未实现的能力写成已完成规范。

## 文档职责

本文负责说明：

- 当前 FastAPI AI Job 服务的实际模块骨架和职责边界。
- 当前 HTTP API、统一响应 envelope、错误 envelope 和 OpenAPI 投影方式。
- 当前 Job 创建、查询、执行、Callback 和恢复链路。
- 当前 `job_type` 注册表、schema 注册表、operation 注册表和错误码注册表。
- 当前配置、鉴权、日志、数据库模型、Repository、Taskiq 和验证入口。

本文不负责说明：

- 尚未接入的正式业务 `job_type`。
- 生产部署、K8s、远程数据库、云平台 Secret 或 CI/CD。
- 用户系统、项目管理、前端状态或业务步骤编排。
- 未在代码、测试或脚本中出现的目标规范。

## 代码事实源

当前事实源以代码和可执行检查为准：

| 范畴 | 当前事实源 |
|---|---|
| API 挂载和中间件 | `app/main.py`、`app/api/routes/*.py` |
| HTTP envelope | `app/schemas/envelope.py`、`app/main.py` |
| Job schema | `app/schemas/jobs.py` |
| Callback schema | `app/schemas/callbacks.py` |
| 错误码 | `app/core/error_registry.py`、`app/core/exceptions.py`、`app/schemas/errors.py` |
| Operation registry | `app/api/operations.py` |
| Schema registry | `app/schemas/registry.py` |
| Job type registry | `app/jobs/registry.py`、`app/jobs/types/register.py` |
| Job 执行 | `app/jobs/base.py`、`app/jobs/runner.py`、`app/tasks/jobs.py` |
| 数据库模型 | `app/core/database.py`、`app/models/job.py`、`alembic/versions/` |
| Repository | `app/repositories/job_repo.py` |
| 配置 | `app/core/config.py`、`.env.example` |
| 验证入口 | `scripts/verify.sh`、`scripts/verify/tasks.sh` |

Markdown 文档不是运行时事实源。文档与代码冲突时，以代码和测试为准。

## 服务边界

本仓库当前是 FastAPI AI Job 执行后端模板，负责：

- 受保护的服务级 HTTP API。
- Job 创建、幂等冲突检测、状态查询和调用方隔离。
- Taskiq 异步执行、attempt 领取、执行租约、重试和恢复。
- `job_type` 注册、`job_params` 校验、runtime snapshot、canonical result 和 public result 投影。
- Callback outbox、HMAC 签名投递、ack 校验和补偿重试。
- PostgreSQL 状态权威、Redis Taskiq broker、本地开发对象存储或阿里云 OSS adapter；生产多副本形态应使用外部对象存储。
- 本地开发、compose 部署检查和模板级验证脚本。

本仓库当前不负责：

- 用户系统、角色权限、项目管理和前端页面状态。
- 调用方业务重跑策略、人工审核流程和跨服务业务编排。
- 生产平台部署、远程数据库维护、K8s、云 Secret 和 CI/CD 发布流水线。
- 为旧协议维护兼容层或双协议事实源。

## 当前目录骨架

当前代码采用如下主要骨架：

```text
app/
  main.py
  api/
    operations.py
    routes/
      health.py
      jobs.py
      meta.py
  core/
    callback_security.py
    config.py
    database.py
    error_registry.py
    exceptions.py
    logging.py
    model_registry.py
    prompt_templates.py
    registry_checks.py
  integrations/
    ai_gateway.py
    aliyun_oss.py
    storage.py
  jobs/
    base.py
    factory.py
    registry.py
    runner.py
    types/
      arithmetic.py
      job_test_add.py
      job_test_echo.py
      register.py
  models/
    job.py
  repositories/
    job_repo.py
  schemas/
    callbacks.py
    common.py
    envelope.py
    errors.py
    jobs.py
    meta.py
    registry.py
  services/
    callbacks.py
    executor.py
    job_context.py
    job_lifecycle.py
    job_runtime.py
    jobs.py
  tasks/
    jobs.py
    recovery.py
    recovery_loop.py
    taskiq_app.py
```

当前实现没有 `app/db/` 目录；数据库入口在 `app/core/database.py`，ORM model 在 `app/models/job.py`。当前异步任务使用 Taskiq 和 Redis，不使用 Celery。

当前主要依赖方向：

```text
api/routes -> services -> repositories -> models/core.database
tasks -> jobs.runner/services -> repositories -> models/core.database
services -> integrations
jobs/types -> schemas + services.job_runtime
registry_checks -> operations/error_registry/schema_registry/job_type_registry
schemas -> 不依赖 DB session、HTTP client 或 broker
repositories -> SQLAlchemy model + 查询/状态迁移
```

禁止把 route、Repository、integration 混成同一层职责：

- route 不直接写 SQL，不直接操作 Taskiq，不直接构造持久化状态机。
- Repository 不调用 HTTP client、AI provider、broker 或 Callback sender。
- ORM model 不作为对外响应 schema 返回。
- 具体 `job_type` 不复制通用 Job envelope、HTTP envelope 或 Callback envelope。

## HTTP 标准输出

当前 API 前缀来自 `SERVICE_API_PREFIX`，默认是 `/api/v1/ai-jobs`。

当前路由：

| 路由 | 鉴权 | envelope |
|---|---|---|
| `GET /health` | 否 | 否 |
| `GET /healthz` | 否 | 否 |
| `GET /api/v1/ai-jobs/models` | 是 | 是 |
| `GET /api/v1/ai-jobs/prompt-templates` | 是 | 是 |
| `POST /api/v1/ai-jobs/jobs` | 是 | 是 |
| `GET /api/v1/ai-jobs/jobs/{job_id}` | 是 | 是 |

除健康检查、OpenAPI、Swagger / ReDoc 页面外，API 前缀下的 JSON `200` 响应由 `SuccessEnvelopeMiddleware` 包装为：

```json
{
  "code": "0",
  "msg": "success",
  "data": {},
  "request_id": "4d8a0d3f4c9a4f6ca3d6d4d8360fb5fb",
  "server_time": "2026-06-22T09:00:00+00:00"
}
```

当前事实：

- `code` 是字符串，不是数字。
- `server_time` 是 UTC ISO datetime 字符串，不是 Unix 秒。
- `request_id` 来自合法 `X-Request-ID`，没有传入时由服务生成。
- 合法 `X-Request-ID` 只允许 ASCII 字母、数字、点、下划线、冒号和连字符，长度 `1-128`。
- 非法 `X-Request-ID` 返回注册错误 `REQUEST_ID_INVALID`。

错误响应由 exception handler 和 `build_error_envelope()` 生成：

```json
{
  "code": "100001",
  "msg": "invalid input",
  "data": {
    "errors": []
  },
  "request_id": "4d8a0d3f4c9a4f6ca3d6d4d8360fb5fb",
  "server_time": "2026-06-22T09:00:00+00:00"
}
```

当前错误 envelope 的 `data` 直接承载 details 或 `null`，不是 `{"error": ...}` 结构。

## 输入 Schema

当前通用严格 schema 基类是 `app/schemas/common.py` 的 `StrictBaseModel`，配置为 `extra="forbid"`。Job 和 Callback 的主要 schema 使用该基类。

`CreateJobRequest` 当前顶层字段：

| 字段 | 类型 | 规则 |
|---|---|---|
| `client_request_id` | `string` | 必填，长度 `1-255`；与 `caller_id` 一起参与幂等判断。 |
| `job_type` | `string` | 必填；必须能从 `app.jobs.registry` 找到 executor。 |
| `job_params` | `object` | 默认 `{}`；由对应 `job_type.params_schema` 归一化和校验。 |
| `callback` | `CallbackConfig \| null` | 可选；传入时会校验 URL 安全性和 `job_type.allow_callback`。 |
| `metadata` | `object` | 默认 `{}`；持久化到 `jobs.metadata`。 |
| `options` | `JobOptions \| null` | 可选；控制优先级和幂等模式。 |

`CallbackConfig` 当前字段：

| 字段 | 类型 | 规则 |
|---|---|---|
| `url` | `string` | 最小长度 1；服务层额外执行 URL 安全校验。 |
| `events` | `array \| null` | 允许 `job.succeeded`、`job.failed`；`null` 时默认订阅两个终态事件；空数组被拒绝；传入值会去重并排序。 |

`JobOptions` 当前字段：

| 字段 | 允许值 | 默认 |
|---|---|---|
| `priority` | `low`、`normal` | `normal` |
| `idempotency_mode` | `reject_duplicate`、`return_existing` | `reject_duplicate` |

## Job HTTP 合同

当前 Job HTTP 合同是：

```text
POST /api/v1/ai-jobs/jobs -> ResponseEnvelope[JobResponseData]
GET /api/v1/ai-jobs/jobs/{job_id} -> ResponseEnvelope[JobResponseData]
```

`JobResponseData` 只包含一个字段：

```json
{
  "job": {}
}
```

`JobEnvelope` 当前字段：

| 字段 | 类型 | 规则 |
|---|---|---|
| `job_id` | `UUID` | 服务生成主键。 |
| `client_request_id` | `string \| null` | 调用方幂等键。 |
| `job_type` | `string` | 注册表中的 `job_type`。 |
| `job_status` | enum | `queued`、`running`、`succeeded`、`failed`。 |
| `job_progress` | `JobProgress` | 包含 `stage`、`percent`、`message`。 |
| `job_result` | `object \| null` | 成功终态公开结果；排队、运行中和失败时必须为 `null`。 |
| `job_error` | `JobErrorDetail \| null` | 失败终态必填；非终态和成功终态必须为 `null`。 |
| `callback` | `CallbackState` | 总是存在；未配置时 `status=not_configured`。 |
| `status_url` | `string` | 当前 Job 查询路径。 |
| `created_at` | `datetime` | 创建时间。 |
| `updated_at` | `datetime` | 最近更新时间。 |
| `finished_at` | `datetime \| null` | 终态时间。 |

`JobEnvelope` 自带终态一致性校验：

- `queued` / `running` 不允许有 `job_result` 或 `job_error`。
- `succeeded` 不允许有 `job_error`。
- `failed` 必须有 `job_error`，且不允许有 `job_result`。

当前 `ProgressStage` 枚举是：

```text
accepted
fetching_input
planning
calling_model
merging
writing_result
completed
failed
```

当前 `CallbackState.status` 枚举是：

```text
not_configured
pending
delivering
delivered
retrying
failed
```

## Callback 合同

当前 Callback 使用独立 envelope，不套 HTTP `code/msg/data`：

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

当前事实：

- `event` 只允许 `job.succeeded` 或 `job.failed`。
- `event` 必须和 `job.job_status` 匹配。
- `job` 必须是终态 `JobEnvelope`。
- `event_id` 使用 `uuid5(NAMESPACE_URL, "ai-job-callback:{job.id}:{event}")` 生成，保证同一 Job 同一终态事件稳定。
- Callback 请求体使用 `CALLBACK_SIGNING_SECRET` 生成 `X-Callback-Timestamp` 和 `X-Callback-Signature`。
- Callback 接收方必须返回 JSON ack：至少包含布尔字段 `accepted`。
- `204`、非 JSON、空 body、非对象、缺少 `accepted` 或 `accepted` 非布尔都会被视为 ack 合同不合法。

当前 Callback response schema：

```text
CallbackResponseEnvelope
  accepted: bool = true
  msg: string | null = null
  details: object = {}
```

## Job Type 注册

当前 `register_all_job_types()` 注册三个内置示例 `job_type`：

| job_type | ParamsSchema | RuntimeFieldsSchema | ResultSchema | 执行方式 |
|---|---|---|---|---|
| `arithmetic` | `ArithmeticParams` | `ArithmeticRuntimeFields` | `ArithmeticResult` | 自定义 Python executor，返回加减乘除。 |
| `job_test_add` | `JobTestAddParams` | `JobTestAddRuntimeFields` | `JobTestAddResult` | 自定义 Python executor，返回加法结果。 |
| `job_test_echo` | `JobTestEchoParams` | `JobTestEchoRuntimeFields` | `JobTestEchoResult` | 自定义 Python executor，返回重复消息。 |

当前 `JobExecutor` 约定：

- `name` 是 `job_type`。
- `params_schema` 负责 `job_params` 校验和归一化。
- `runtime_job_fields()` 必须返回 runtime snapshot 中需要的字段。
- `_execute()` 返回 `dict` 时走自定义运行时。
- `_execute()` 返回 `None` 时走内置 LLM 文本运行时，需要 runtime fields 中有 `model_id` 和 `prompt_payload`。
- `canonical_result_schema` 校验内部结果事实。
- `public_result_schema` 校验对外 `job_result`。
- `allow_callback` 控制该 `job_type` 是否接受 callback。
- `large_artifact_keys` 控制哪些 artifact content 需要持久化到对象存储。

新增 `job_type` 至少需要同步：

- `app/jobs/types/<name>.py`
- `app/jobs/types/register.py`
- `app/schemas/jobs.py` 或专属 schema 模块
- `app/schemas/registry.py`
- 相关 tests
- 如使用 LLM runtime，还需要 `app/core/prompts.yaml` 和模型可用性配置。

## Job 创建与执行流程

当前 `POST /jobs` 主流程：

1. route 调用 `submit_job_request()`。
2. 服务读取 `caller_id`，校验 `job_type`、`job_params`、callback 和模型可用性。
3. 对 `caller_id + client_request_id` 申请 PostgreSQL advisory transaction lock。
4. 查找 24 小时内同一调用方的同一 `client_request_id`。
5. 如果请求 fingerprint 不同，返回 `CLIENT_REQUEST_ID_CONFLICT`。
6. 如果 fingerprint 相同且 `idempotency_mode=reject_duplicate`，返回冲突。
7. 如果 fingerprint 相同且 `idempotency_mode=return_existing`，返回已存在 Job。
8. 检查 `MAX_ACTIVE_JOBS`；超限返回 `QUEUE_FULL`。
9. 创建 `jobs` 记录、runtime refs、`job_attempts` 初始 attempt。
10. 提交 DB 事务。
11. 通过 Taskiq 发布 `jobs.run_attempt`。
12. 返回 `JobResponseData(job=JobEnvelope)`。

当前 `jobs.run_attempt` 主流程：

1. Worker 重新注册所有 `job_type`。
2. 使用独立 `NullPool` async SQLAlchemy engine 创建 session。
3. 根据 `attempt_id` 领取当前 active attempt，并写入 worker、lease token 和 running 状态。
4. 调用 `app/jobs/runner.py` 的 `execute_job()`。
5. 执行进度更新、custom executor 或 LLM runtime、canonical result 校验、public result 投影。
6. 成功时将 Job 标记为 `succeeded`，写入 result 和 canonical_result，并创建 callback outbox。
7. 失败时将 attempt 标记失败；满足重试条件时创建下一次 attempt，否则 Job 进入 `failed` 并创建 callback outbox。
8. 终态后尝试投递 Callback。

当前状态迁移依赖 `with_for_update()`、`skip_locked`、`execution_token`、`execution_generation`、attempt lease 和 active attempt 约束保护，不依赖普通日志反推状态。

## Schema 组合

当前公共 schema 组合：

| Schema | 当前归属 |
|---|---|
| `HttpEnvelope[T]` / `ResponseEnvelope[T]` | `app/schemas/envelope.py` |
| `ErrorEnvelope` | `app/schemas/envelope.py` |
| `JobErrorDetail` / `CallbackErrorDetail` | `app/schemas/errors.py` |
| `JobResponseData` | `app/schemas/jobs.py` |
| `JobEnvelope` | `app/schemas/jobs.py` |
| `CallbackEnvelope` | `app/schemas/callbacks.py` |
| `CallbackResponseEnvelope` | `app/schemas/callbacks.py` |

当前 `app/schemas/registry.py` 只登记 registry check 需要消费的 schema 名称。新增 schema 如果被 operation registry 或 job type registry 引用，必须进入该注册表。

## 注册表真源

当前有四类注册表：

| 注册表 | 文件 | 当前用途 |
|---|---|---|
| Operation registry | `app/api/operations.py` | 声明 operation id、路径、方法、鉴权边界、schema、错误码、副作用和日志事件。 |
| Error registry | `app/core/error_registry.py` | 声明 reason、字符串 code、公开 msg、HTTP status、retryable、scope 和 owner。 |
| Schema registry | `app/schemas/registry.py` | 为 registry check 提供可引用 schema 名称集合。 |
| Job type registry | `app/jobs/registry.py` | 运行时查找 executor，并导出 `JobTypeSpec`。 |

`scripts/verify/registry_check.py` 会执行 `validate_all_registries(app)`，当前检查内容包括：

- 错误码 reason 和 spec 一致。
- 错误码 code 不重复。
- operation 引用的错误码、日志事件和 schema 存在。
- job type 引用的错误码、日志事件和 schema 存在。
- job type 必须声明 params、runtime fields、canonical result 和 public result schema。
- mounted route 的 `operation_id` 必须登记。
- registered operation 必须挂载到 route。
- route 方法、路径、OpenAPI request schema 和 response data schema 必须与 operation registry 一致。

## 错误码与异常

当前错误码以 `reason` 为运行时入口，`code` 是字符串项目码。示例：

| reason | code | HTTP status | retryable |
|---|---:|---:|---|
| `INVALID_INPUT` | `100001` | 400 | false |
| `REQUEST_ID_INVALID` | `100002` | 400 | false |
| `INVALID_JOB_TYPE` | `100011` | 400 | false |
| `CLIENT_REQUEST_ID_CONFLICT` | `100409` | 409 | false |
| `UNAUTHORIZED` | `200001` | 401 | false |
| `FORBIDDEN` | `200003` | 403 | false |
| `JOB_NOT_FOUND` | `300004` | 404 | false |
| `INTERNAL_ERROR` | `900500` | 500 | false |
| `JOB_STATE_TRANSITION_CONFLICT` | `900506` | 500 | true |
| `AI_PROVIDER_FAILED` | `900502` | 502 | true |
| `TASKIQ_PUBLISH_FAILED` | `900526` | 502 | true |
| `QUEUE_FULL` | `900503` | 503 | true |
| `MODEL_CALL_TIMEOUT` | `900504` | 504 | true |
| `JOB_TIMEOUT` | `900541` | 504 | true |

当前异常类型：

```text
AppError
UnauthorizedError
ForbiddenError
ValidationAppError
NotFoundAppError
InternalAppError
```

当前 exception handler：

- `AppError` 按 `exc.code` 反查 error registry。
- `RequestValidationError` 转为 `INVALID_INPUT`。
- `StarletteHTTPException` 中 `404` 转为 `NOT_FOUND`，`405` 转为 `METHOD_NOT_ALLOWED`，其他转为 `HTTP_ERROR`。
- 未处理异常转为 `INTERNAL_ERROR`，并记录 exception log。

当前限制：错误 envelope 的 `data` 不是统一 `ErrorDetail` 结构；它直接输出 details 或 `null`。

## 配置模块

当前配置入口是单个 `Settings` 类，位于 `app/core/config.py`，使用 `pydantic-settings` 从 `.env` 读取，`extra="ignore"`。

主要配置组：

- 服务身份：`TEMPLATE_NAME`、`SERVICE_NAME`、`SERVICE_TITLE`、`SERVICE_API_PREFIX`。
- 数据库：`DATABASE_URL`、`DB_SSL`、`DB_POOL_SIZE`、`DB_MAX_OVERFLOW`、`DB_POOL_RECYCLE`。
- 鉴权和调用方：`SERVICE_API_KEY`、`DISABLE_HTTP_AUTH_HEADER`、`DISABLE_CALLER_ID_HEADER`。
- Redis / Taskiq：`REDIS_URL`、`TASKIQ_MAX_RETRIES`、`TASKIQ_RETRY_DELAY`。
- 对象存储：`STORAGE_BACKEND`、`LOCAL_OBJECT_STORAGE_PATH`、`OSS_*`。
- Callback：`CALLBACK_SIGNING_SECRET`、`ALLOW_INSECURE_CALLBACKS`、`CALLBACK_TIMEOUT_SECONDS`、`CALLBACK_MAX_DELIVERY_ATTEMPTS`、`CALLBACK_RETRY_DELAY_SECONDS`。
- 模型：`OPENAI_API_KEY`、`OPENAI_BASE_URL`、`DEFAULT_MODEL_ID`、`MODEL_CONFIG_PATH`、`MODEL_CALL_TIMEOUT_SECONDS`。
- 容量和恢复：`MAX_ACTIVE_JOBS`、`OSS_INPUT_MAX_BYTES`、`JOB_ORPHAN_TIMEOUT_SECONDS`、`JOB_RECOVERY_INTERVAL_SECONDS`、`JOB_RECOVERY_BATCH_SIZE`、`JOB_RECOVERY_CALLBACK_BATCH_SIZE`、`JOB_MAX_EXECUTION_ATTEMPTS`。
- 日志：`LOG_LEVEL`。

`STORAGE_BACKEND=local` 是本地开发 / 单机 compose 模式；生产或多副本 API / worker 运行形态应使用外部对象存储后端。

当前派生配置包括：

- `worker_soft_time_limit = MODEL_CALL_TIMEOUT_SECONDS + 300`
- `worker_hard_time_limit = worker_soft_time_limit + 60`
- `job_stale_running_seconds = worker_hard_time_limit + 600`
- `callback_delivery_timeout_seconds = CALLBACK_TIMEOUT_SECONDS + 175`
- `sync_database_url`
- `allowed_origins`
- `local_object_storage_path`
- `prompt_config_path`
- `model_config_path`
- OSS endpoint 和 endpoint style。

当前启动校验包括：

- 多数时间、批量、容量字段必须为正数或非负数。
- `STORAGE_BACKEND` 只允许 `local` 或 `aliyun_oss`。
- header disable flags 只接受布尔或字符串 `true` / `false`。
- 关闭鉴权 header 或 caller header 时，`DATABASE_URL` 和 `REDIS_URL` 必须指向 loopback。
- `callback_delivery_timeout_seconds` 必须小于 `CALLBACK_RETRY_DELAY_SECONDS`。
- `CALLBACK_SIGNING_SECRET` 必须配置。

`.env.example` 是可提交配置模板；`scripts/verify/env_config_check.py` 会参与 `./scripts/verify.sh check`。

## 安全边界

当前安全边界：

- `/health` 和 `/healthz` 不需要鉴权。
- `/models`、`/prompt-templates`、`/jobs` 和 `/jobs/{job_id}` 需要 `Authorization: Bearer <SERVICE_API_KEY>`。
- `X-AI-Service-Caller-ID` 可选；未传时使用 `default`。
- `caller_id` 只允许 `^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,63}$`。
- `DISABLE_HTTP_AUTH_HEADER=true` 可跳过 Bearer 校验，但配置层要求 DB / Redis 指向 loopback。
- `DISABLE_CALLER_ID_HEADER=true` 会忽略 caller header 并统一使用 `default`，同样要求 DB / Redis 指向 loopback。
- Job 查询使用 `JobRepo.get_for_caller()`，只能查询同一 `caller_id` 的未删除 Job。
- Callback URL 会通过 `app/core/callback_security.py` 校验；非本地默认不允许不安全回调。
- Callback 投递使用 HMAC 签名。

当前没有用户级 RBAC，也没有浏览器用户会话鉴权。

## 日志与 Request Context

当前日志入口是 `app/core/logging.py`：

- `configure_logging()` 将 root logger 输出到 stdout。
- 日志格式包含 `asctime`、`level`、`logger`、`request_id` 和消息。
- `request_id` 通过 `ContextVar` 保存，由 `RequestIDMiddleware` 设置。
- `LogEvent` 和 `_LOG_EVENTS` 提供可检查的稳定事件名集合。
- `log_event()` 会拒绝未知事件名。

当前稳定事件名集合：

```text
request_completed
request_failed
job_created
job_publish_requested
job_published
job_started
job_progressed
job_succeeded
job_failed
job_recovered
callback_scheduled
callback_delivered
callback_failed
```

当前 request middleware 会记录：

- `request_completed method=<method> path=<path> status=<status> duration_ms=<ms>`
- `request_failed method=<method> path=<path> duration_ms=<ms>`

当前日志不是完整结构化 JSON；它是带 key-value 消息的标准 logging 输出。

## Metrics

当前代码没有发现 metrics 模块、metrics endpoint 或指标导出逻辑。原目标规范中的 HTTP / Job / AI / Callback metrics 仍属于未落地能力。

新增 metrics 时应保持低基数标签，不得把 `request_id`、`job_id`、`client_request_id`、URL query、Prompt、异常消息或供应商原文放入标签。

## DB、ORM 与 Repository

当前数据库使用 SQLAlchemy async ORM 和 Alembic：

- `app/core/database.py` 定义 `Base`、`engine`、`AsyncSessionLocal` 和 FastAPI dependency `get_db()`。
- `app/models/job.py` 定义 ORM model。
- `alembic/versions/` 保存迁移。
- API 请求使用连接池 engine；Taskiq worker / recovery 使用 `NullPool` 创建独立 engine。

当前主要表：

| 表 | ORM | 职责 |
|---|---|---|
| `jobs` | `Job` | Job 状态、调用方、幂等、进度、runtime refs、result、callback 摘要和软删除。 |
| `job_attempts` | `JobAttempt` | 执行 attempt、发布状态、运行租约、heartbeat、失败和重试。 |
| `callback_outbox` | `CallbackOutbox` | 终态 Callback 事件、投递尝试、lease、dead letter 和幂等事件 id。 |
| `job_events` | `JobEvent` | attempt、callback 和状态迁移事件记录。 |
| `reconciler_leases` | `ReconcilerLease` | recovery / reconciler 租约。 |

`jobs` 表是对外查询状态和终态结果的权威。Taskiq 消息、worker 日志和 callback 投递结果都是执行旁证或副作用状态。

当前 Repository 规则：

- `JobRepo` 负责 SQL 查询、锁、状态迁移、outbox 和事件写入。
- 幂等入口使用 PostgreSQL advisory transaction lock。
- attempt 领取使用 `with_for_update()` 和 active attempt 条件。
- running attempt 使用 lease token 和 lease expiry。
- Job 成功 / 失败使用 `execution_token` 防止过期执行写回。
- callback 投递使用 outbox lease 和 `delivery_attempt` 限制。
- cleanup 只软删除已过期且已收敛的终态 Job。

## Integrations

当前 integration 模块：

| 模块 | 职责 |
|---|---|
| `app/integrations/ai_gateway.py` | 通过 LiteLLM 执行文本生成。 |
| `app/integrations/storage.py` | 统一本地开发对象存储 / OSS 存储接口；多副本运行必须使用外部对象存储后端。 |
| `app/integrations/aliyun_oss.py` | 阿里云 OSS 适配。 |
| `app/services/callbacks.py` | Callback HTTP 投递、签名、ack 校验和错误摘要。 |

当前没有 `integrations/rs`。外部写回能力尚未作为稳定模块落地。

## Entrypoints

当前稳定入口：

| 入口 | 职责 |
|---|---|
| `./scripts/dev.sh` | 本地服务生命周期：bootstrap、start、stop、restart、status、logs、migrate、ports。 |
| `./scripts/verify.sh` | 一次性验证：test、workflow-smoke、env-config、check。 |
| `./scripts/deploy.sh` | compose 部署形态检查和管理。 |

当前 `./scripts/verify.sh check` 执行：

1. shell 脚本语法检查。
2. `dev.sh`、`verify.sh`、`deploy.sh` help smoke。
3. Python 验证脚本 `py_compile`。
4. env 配置键检查。
5. registry consistency check。
6. pytest。

当前 `workflow-smoke` 使用内置 `job_test_echo` 验证本地 Job 创建、Taskiq 执行和状态轮询，不调用真实模型或外部对象存储。

## 验收基线

修改代码后优先运行：

```bash
./scripts/verify.sh check
```

修改服务启动、Taskiq、数据库迁移、对象存储、Job 生命周期或 Callback 后，还应运行：

```bash
./scripts/dev.sh start
./scripts/verify.sh workflow-smoke
./scripts/dev.sh stop
```

修改 Dockerfile、docker compose、部署脚本或配置加载规则后，还应运行：

```bash
./scripts/deploy.sh check
```

当前已落地且可检查的验收面：

- FastAPI route 必须能反查 operation registry。
- operation registry 引用的 schema、错误码和日志事件必须存在。
- job type registry 引用的 schema、错误码和日志事件必须存在。
- job type 必须声明 params、runtime fields、canonical result 和 public result schema。
- 错误码 code 不重复。
- HTTP success envelope 和错误 envelope 有 contract tests。
- Job 创建、查询、idempotency、caller 隔离、终态结果、Callback、recovery、repository 和内置 job workflow 有测试覆盖。
- env key 和配置派生约束有测试覆盖。

当前未落地或未完整落地的验收面：

- metrics 采集和导出。
- JSON 结构化日志字段全量覆盖。
- 正式业务 `job_type` 的真实模型 e2e。
- 外部 RS 写回 integration。
- 错误 envelope 中统一 `ErrorDetail` 嵌套结构。
- 数字型错误 code 和 Unix 秒级 `server_time`。
