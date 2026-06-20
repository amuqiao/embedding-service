# AI Job 服务项目规范与骨架

本文定义本项目后端规范和目标骨架。后续接口、Job、Workflow、配置、日志、异常、ORM 和测试都必须向本文收口；不通过兼容器保留旧协议，不维护新旧两套事实源。

## 文档职责

本文负责定义：

- 项目目录骨架和模块职责。
- HTTP 输入输出标准 schema。
- Job 输入输出 schema 与 `job_type` 注册要求。
- 异常模块、自定义异常和错误码规范。
- 配置模块、日志模块、metrics、时间字段和 request context 规范。
- ORM model、DB session、Repository 和迁移规范。
- OpenAPI、文档、mock fixture 和 contract tests 的事实源关系。

本文不负责：

- 具体某次 PR 的文件级迁移步骤。
- 生产部署、K8s、远程数据库、云平台 Secret 或 CI/CD 发布流水线。
- 用户系统、项目管理、业务步骤编排或前端状态。

## Blueprint 覆盖

本项目规范覆盖 `claude_blueprint/rules/backend` 的以下规则族：

| Blueprint 规则 | 本文落点 |
|---|---|
| `architecture/layering.md` | 服务边界、依赖方向、Repository / Service / API 职责。 |
| `architecture/project-skeleton.md` | 目标目录骨架、模块职责、新接口和新 `job_type` 落点。 |
| `architecture/ai-service-mvp-baseline.md` | 最小真源、最小接口、最小进程和最小验证。 |
| `contracts/service-contract.md` | HTTP envelope、错误结构、JobEnvelope、Callback、日志关联字段。 |
| `contracts/schema-composition.md` | `ResponseEnvelope[T]`、`JobEnvelope[T]`、`CallbackEnvelope[T]`、公共 schema 组合。 |
| `contracts/api-operation-template.md` | 新接口 operation 定义、字段表、示例和验收。 |
| `contracts/registry-source.md` | 错误码、operation、当前 schema、`job_type` 注册表真源。 |
| `fastapi/configuration/settings.md` | Settings 子对象、env key 映射、派生配置、启动校验和机器检查。 |
| `fastapi/observability/logging.md` | 结构化日志、request_id、业务摘要和异常日志。 |
| `fastapi/observability/metrics.md` | HTTP / Job / AI / Callback / 对象存储最小指标。 |
| `fastapi/security/access-boundary.md` | 调用方身份、鉴权、授权边界、错误暴露和日志脱敏。 |
| `persistence/database.md` | ORM、DB session、Repository、CAS、迁移、索引和 Job 状态权威。 |
| `jobs/async-job.md` | Job 状态机、可靠投递、运行时快照、恢复和进程边界。 |
| `jobs/workflow-handler.md` | `job_type` handler、params/result/callback schema、执行计划和副作用边界。 |
| `jobs/executors/celery.md` | Celery task id、acks、重试、canvas、Worker session 和状态权威映射。 |
| `integrations/artifact-storage.md` | 对象存储引用、hash、大小、权限、过期和存储边界。 |
| `integrations/external-service.md` | Callback / RS 写回 / 外部服务 adapter、幂等、重试和错误转换。 |
| `ai/capability-service.md` | 模型 provider adapter、Prompt、结构化输出、runtime snapshot 和成本摘要。 |
| `deployment/service-deployment.md`、`deployment/ci-dockerfile.md` | local / compose 部署配置、迁移执行、Dockerfile 和发布前检查边界。 |
| `entrypoints/project-entrypoints.md`、`entrypoints/script-topology.md`、`entrypoints/runtime-troubleshooting.md` | `dev.sh`、`verify.sh`、`deploy.sh` 入口边界、脚本职责和只读排障信号。 |

Typer 规则暂不作为必须实现的运行入口。本项目当前稳定入口是 `scripts/dev.sh`、`scripts/verify.sh` 和 `scripts/deploy.sh`；如后续引入 Typer，只能作为只读排障入口，不能替代现有脚本或绕过 Repository / Service 边界。

## 服务边界

本项目是 AI Job 能力层服务。

负责：

- 受保护 HTTP API。
- AI Job 创建、查询、状态机、异步执行和恢复。
- `job_type` 注册、参数校验、runtime snapshot、结果投影和 Callback data。
- 模型调用、对象存储产物、Callback、RS 写回等能力层集成。
- PostgreSQL 状态权威、Redis/Celery 执行旁证、本地/compose 运行入口。

不负责：

- 用户系统、角色权限、项目管理、业务步骤编排。
- 调用方业务重跑策略、人工审核状态或前端页面状态。
- 生产部署平台、K8s、云平台 Secret、远程数据库维护。
- 为旧协议保留兼容器、双写协议或 silent fallback。

## 目标骨架

目标目录按职责组织，而不是按参考仓库机械搬迁。

```text
app/
  main.py
  api/
    router.py
    dependencies.py
    exception_handlers.py
    routes/
      health.py
      meta.py
      jobs.py
      mock_interfaces.py
  schemas/
    envelope.py
    errors.py
    common.py
    jobs.py
    callbacks.py
  core/
    settings.py
    logging.py
    metrics.py
    time.py
    security.py
  db/
    base.py
    session.py
    models/
      job.py
  repositories/
    job_repo.py
  application/
    jobs/
      submission.py
      query.py
      publishing.py
  services/
    job_runtime.py
    job_outputs.py
    callbacks.py
    executor.py
  jobs/
    registry.py
    lifecycle.py
    publisher.py
    recovery.py
    planner.py
    canvas.py
    work_items.py
    finalizer.py
  workflows/
    generic/
    novel_localization/
    short_drama_tagging/
  integrations/
    ai/
    storage/
    callback/
    rs/
  tasks/
    celery_app.py
    jobs.py
    recovery.py
```

允许的依赖方向：

```text
api -> application/services/jobs -> repositories -> db/models
tasks -> services/jobs -> repositories -> db/models
services/jobs -> integrations
workflows -> jobs/services/integrations/schemas
schemas -> 不依赖业务执行、DB session 或外部 client
repositories -> 只依赖 db/models 和 SQLAlchemy
```

禁止：

- API route 直接写 SQL、返回 ORM 对象或编排 Job 生命周期。
- Service 读取 FastAPI `Request` / `Response` / header。
- Repository 调用 HTTP client、AI provider、broker、callback sender。
- Workflow 复制通用 Job route、Job 表、Callback envelope 或状态机。
- ORM model 泄漏为对外响应 schema。

## HTTP 标准输出

除 `/health`、`/healthz`、OpenAPI、metrics、下载流和框架生态页面外，所有受保护业务 HTTP 接口必须返回统一 envelope。

成功响应：

```json
{
  "code": 0,
  "msg": "success",
  "data": {},
  "request_id": "req-20260620-0001",
  "server_time": 1781753745
}
```

错误响应：

```json
{
  "code": 422001,
  "msg": "invalid job_type",
  "data": {
    "error": {
      "reason": "INVALID_JOB_TYPE",
      "details": {
        "job_type": "unknown.type"
      },
      "retryable": false
    }
  },
  "request_id": "req-20260620-0001",
  "server_time": 1781753745
}
```

规则：

- HTTP status 通过真实 HTTP 状态码返回，不在响应体中替代或伪装。
- `code` 是数字项目错误码；成功固定为 `0`。
- 错误 `code` 必须来自错误码注册表，推荐按 `http_status * 1000 + sequence` 分配，例如 `422001`。
- `msg` 来自错误码注册表，只做人读说明，不作为机器判断依据。
- `data` 承载业务数据；错误时承载 `error`。
- `error.reason` 是稳定英文枚举，供代码分支、日志检索和排障使用。
- `request_id` 来自可信 `X-Request-ID` 或服务生成。
- `server_time` 使用 Unix 秒级整数。
- HTTP status 保持真实语义，不为了统一 envelope 全部返回 `200`。
- 现有裸响应接口必须改为标准 envelope，不通过兼容器同时支持新旧响应。

错误码分配规则：

| 范围 | 含义 | 示例 |
|---|---|---|
| `0` | 成功 | `0` |
| `400001-400999` | 请求语法、格式或基础参数错误 | `400001 malformed json` |
| `401001-401999` | 未认证或凭证无效 | `401001 missing api key` |
| `403001-403999` | 已认证但无权限 | `403001 caller forbidden` |
| `404001-404999` | 资源不存在 | `404001 job not found` |
| `409001-409999` | 幂等、状态冲突或重复提交 | `409001 duplicate client_request_id` |
| `422001-422999` | 语义校验失败 | `422001 invalid job_type`、`422002 invalid job_params` |
| `429001-429999` | 限流或容量保护 | `429001 rate limited` |
| `500001-500999` | 服务内部错误 | `500001 internal error` |
| `502001-502999` | 外部依赖失败 | `502001 ai provider failed` |
| `503001-503999` | 服务暂不可用或依赖不可用 | `503001 broker unavailable` |
| `504001-504999` | 超时 | `504001 model call timeout` |

错误码可以由工具辅助分配和校验，但运行时不得临时拼接未登记错误码。

## 输入 Schema

所有请求 schema 必须显式定义顶层字段，默认拒绝未声明字段。

规则：

- Pydantic schema 使用 `extra="forbid"` 或等价约束。
- 字段必须定义类型、必填/可选、长度、范围、枚举、格式和大小限制。
- 文档必须说明 `null`、省略、空字符串、空数组的差异。
- 大文本、文件、URL、OSS 引用必须说明大小、hash、content type 或安全限制。
- `metadata` 只能承载调用方排查信息，不承载服务执行必需参数。
- `options` 只放通用执行选项，不放业务字段。
- 具体能力字段进入 `job_params`，不得提升到通用 Job 顶层。

新增接口必须有 `operation_id`，并在 operation registry 或等价可检查真源中声明 request schema、response data schema、错误码和日志字段。

## Job Schema

通用 Job 壳保持少量顶层字段：

```text
client_request_id
job_type
job_params
callback
metadata
options
```

每个 `job_type` 必须声明：

- `ParamsSchema`。
- `RuntimeFieldsSchema`。
- `CanonicalResultSchema`。
- `JobResultSchema`；允许显式为 `null`。
- 是否允许 callback、外部写回和大产物。
- 支持的执行计划类型。

`JobEnvelope` 统一定义一次，并由 `JobResponseData.job` 承载。Job 自身失败时，HTTP 查询仍是成功查询，Job 失败原因进入 `JobEnvelope.job_error`。

## Job HTTP 合同

Job HTTP 合同分为三类稳定外壳：

```text
POST /jobs -> ResponseEnvelope[JobResponseData[null]]
GET /jobs/{job_id} -> ResponseEnvelope[JobResponseData[JobResult]]
callback -> CallbackEnvelope[JobEnvelope[JobResult]]
```

HTTP 接口始终遵循 [HTTP 标准输出](#http-标准输出)。Job 不是另一套 HTTP 输出协议；`ResponseEnvelope.data` 只放一个 `job` key，由 `data.job` 承载统一 `JobEnvelope`，避免 Job 字段污染通用 `data` 层级。

具体能力只能定义 `job_params` 和 `job_result` 的业务 schema，不得扩展通用 Job 顶层字段。文件、对象存储引用、大 JSON 或第三方写回摘要如果需要对调用方可见，也必须作为具体能力的 `job_params` 输入引用或 `job_result` 输出字段表达，不得在通用 Job envelope 或 callback envelope 中新增独立产物顶层字段。

### Job 标准请求

`CreateJobRequest` 顶层字段固定：

| 字段 | 类型 | 必填 | 规则 |
|---|---|---:|---|
| `client_request_id` | `string` | 是 | 调用方幂等键。同一 `caller_id + client_request_id` 不得创建多个语义不同的 Job。 |
| `job_type` | `string enum` | 是 | 来自 `job_type` registry。未知值返回 `422001 INVALID_JOB_TYPE`。 |
| `job_params` | `object` | 是 | 由对应 `job_type` 的 `ParamsSchema` 校验，默认拒绝未知字段。 |
| `callback` | `CallbackConfig \| null` | 否 | 终态通知配置；不传表示只轮询。该 `job_type` 不支持 callback 时必须拒绝。 |
| `metadata` | `object` | 否 | 调用方排查元数据，不参与执行语义，不得承载业务必需字段。 |
| `options` | `JobOptions` | 否 | 通用执行选项，只能放跨 `job_type` 的控制意图。 |

`CallbackConfig` 字段：

| 字段 | 类型 | 必填 | 规则 |
|---|---|---:|---|
| `url` | `string uri` | 是 | Callback 投递地址，必须通过 URL 安全校验。 |
| `events` | `array enum` | 否 | 允许值：`job.succeeded`、`job.failed`；省略时订阅两个终态事件。 |
| `secret_ref` | `string` | 否 | 签名密钥引用，不在请求、响应和日志中暴露明文 secret。 |

`JobOptions` 枚举和字段：

| 字段 | 类型 | 允许值 | 规则 |
|---|---|---|---|
| `priority` | `enum` | `normal`、`low` | 首版默认 `normal`；不承诺强实时调度。 |
| `idempotency_mode` | `enum` | `reject_duplicate`、`return_existing` | 默认 `reject_duplicate`。 |

请求正例：

```json
{
  "client_request_id": "cpp-20260620-book-2042-tagging",
  "job_type": "short_drama.tagging",
  "job_params": {
    "book_id": "2042",
    "title": "Example Story",
    "source_language": "zh-CN",
    "target_language": "en-US",
    "content_ref": {
      "kind": "oss_object",
      "uri": "oss://bucket/input/book-2042.txt",
      "sha256": "b4f9d1c4e7a1"
    }
  },
  "callback": {
    "url": "https://caller.example.com/ai-job-callback",
    "events": ["job.succeeded", "job.failed"],
    "secret_ref": "caller-a-callback-secret"
  },
  "metadata": {
    "caller_trace_id": "trace-cpp-2042"
  },
  "options": {
    "priority": "normal",
    "idempotency_mode": "reject_duplicate"
  }
}
```

### Job Envelope 输出

`POST /jobs` 成功只表示服务已接单并持久化 Job，`job_result` 必须为 `null`。`GET /jobs/{job_id}` 返回同一套 `JobEnvelope`，仅状态、进度、错误和 `job_result` 随执行推进变化。

`JobEnvelope[JobResult]` 字段：

| 字段 | 类型 | 规则 |
|---|---|---|
| `job_id` | `string` | 服务生成的 Job 主键。 |
| `client_request_id` | `string` | 调用方幂等键。 |
| `job_type` | `string` | 来自 `job_type` registry。 |
| `job_status` | `enum` | `queued`、`running`、`succeeded`、`failed`、`canceled`。 |
| `job_progress` | `JobProgress` | 进度摘要，不作为终态判断依据。 |
| `job_result` | `JobResult \| null` | 具体任务公开输出。创建、运行中、失败或取消时为 `null`。 |
| `job_error` | `ErrorDetail \| null` | Job 失败原因；HTTP 查询成功时也可能非 `null`。 |
| `callback` | `CallbackState \| null` | Callback 投递状态摘要。 |
| `status_url` | `string` | 轮询地址。 |
| `created_at` | `string date-time` | 创建时间，RFC 3339 UTC。 |
| `updated_at` | `string date-time` | 最近状态更新时间，RFC 3339 UTC。 |
| `finished_at` | `string date-time \| null` | 终态时间。 |

输出枚举：

| 枚举 | 允许值 |
|---|---|
| `JobStatus` | `queued`、`running`、`succeeded`、`failed`、`canceled` |
| `ProgressStage` | `accepted`、`fetching_input`、`planning`、`calling_model`、`merging`、`writing_result`、`delivering_callback`、`completed`、`failed` |
| `CallbackStatus` | `not_configured`、`pending`、`delivering`、`delivered`、`retrying`、`failed` |

创建输出正例：

```json
{
  "code": 0,
  "msg": "success",
  "data": {
    "job": {
      "job_id": "job_01JZ8Q7Y4W7X2Z6M8N9P0Q1R2S",
      "client_request_id": "cpp-20260620-book-2042-tagging",
      "job_type": "short_drama.tagging",
      "job_status": "queued",
      "job_progress": {
        "stage": "accepted",
        "percent": 0,
        "message": "accepted"
      },
      "job_result": null,
      "job_error": null,
      "callback": {
        "status": "pending",
        "attempt": 0,
        "last_error": null,
        "next_retry_at": null
      },
      "status_url": "/api/v1/ai-jobs/jobs/job_01JZ8Q7Y4W7X2Z6M8N9P0Q1R2S",
      "created_at": "2026-06-20T10:00:01Z",
      "updated_at": "2026-06-20T10:00:01Z",
      "finished_at": null
    }
  },
  "request_id": "req-20260620-0001",
  "server_time": 1781753745
}
```

轮询成功终态正例：

```json
{
  "code": 0,
  "msg": "success",
  "data": {
    "job": {
      "job_id": "job_01JZ8Q7Y4W7X2Z6M8N9P0Q1R2S",
      "client_request_id": "cpp-20260620-book-2042-tagging",
      "job_type": "short_drama.tagging",
      "job_status": "succeeded",
      "job_progress": {
        "stage": "completed",
        "percent": 100,
        "message": "completed"
      },
      "job_result": {
        "book_id": "2042",
        "accepted": true,
        "tags": [
          {
            "tag_id": "theme.love",
            "name": "Love",
            "confidence": 0.94
          }
        ],
        "output_ref": {
          "kind": "oss_object",
          "uri": "oss://bucket/output/job_01JZ8Q7Y4W7X2Z6M8N9P0Q1R2S/result.json",
          "sha256": "6c4f9d1b8e22"
        },
        "summary": {
          "tag_count": 1,
          "language": "en-US"
        }
      },
      "job_error": null,
      "callback": {
        "status": "delivered",
        "attempt": 1,
        "last_error": null,
        "next_retry_at": null
      },
      "status_url": "/api/v1/ai-jobs/jobs/job_01JZ8Q7Y4W7X2Z6M8N9P0Q1R2S",
      "created_at": "2026-06-20T10:00:01Z",
      "updated_at": "2026-06-20T10:03:01Z",
      "finished_at": "2026-06-20T10:03:00Z"
    }
  },
  "request_id": "req-20260620-0002",
  "server_time": 1781753921
}
```

轮询失败终态正例：

```json
{
  "code": 0,
  "msg": "success",
  "data": {
    "job": {
      "job_id": "job_01JZ8Q7Y4W7X2Z6M8N9P0Q1R2S",
      "client_request_id": "cpp-20260620-book-2042-tagging",
      "job_type": "short_drama.tagging",
      "job_status": "failed",
      "job_progress": {
        "stage": "failed",
        "percent": 100,
        "message": "failed"
      },
      "job_result": null,
      "job_error": {
        "reason": "AI_PROVIDER_FAILED",
        "details": {
          "provider": "openai",
          "operation": "structured_output"
        },
        "retryable": true
      },
      "callback": {
        "status": "delivered",
        "attempt": 1,
        "last_error": null,
        "next_retry_at": null
      },
      "status_url": "/api/v1/ai-jobs/jobs/job_01JZ8Q7Y4W7X2Z6M8N9P0Q1R2S",
      "created_at": "2026-06-20T10:00:01Z",
      "updated_at": "2026-06-20T10:01:31Z",
      "finished_at": "2026-06-20T10:01:31Z"
    }
  },
  "request_id": "req-20260620-0003",
  "server_time": 1781753891
}
```

### Callback Envelope 输出

Callback 使用独立事件 envelope，但其中的 `job` 必须复用同一套 `JobEnvelope[JobResult]`。

```text
CallbackEnvelope[JobEnvelope[JobResult]]
```

Callback 不使用 HTTP `code/msg/data` envelope，不设置 HTTP 标准输出字段。

`CallbackEnvelope` 顶层字段：

| 字段 | 规则 |
|---|---|
| `event` | 终态事件，首版至少包含 `job.succeeded`、`job.failed`。 |
| `event_id` | 单次投递事件 id，不作为业务幂等键。 |
| `attempt` | 本次投递尝试次数，从 `1` 开始。 |
| `sent_at` | RFC 3339 UTC 时间。 |
| `trigger_request_id` | 触发本 Job 或本次终态副作用的入口 request id。 |
| `caller_id` | 调用方身份摘要。 |
| `job` | 与轮询接口 `data` 同形的 `JobEnvelope[JobResult]`，必须包含 `job_result`。 |

Callback 成功正例：

```json
{
  "event": "job.succeeded",
  "event_id": "evt_01JZ8Q9V3C4D5E6F7G8H9J0K1L",
  "attempt": 1,
  "sent_at": "2026-06-20T10:03:01Z",
  "trigger_request_id": "req-20260620-0001",
  "caller_id": "cpp-service",
  "job": {
    "job_id": "job_01JZ8Q7Y4W7X2Z6M8N9P0Q1R2S",
    "client_request_id": "cpp-20260620-book-2042-tagging",
    "job_type": "short_drama.tagging",
    "job_status": "succeeded",
    "job_progress": {
      "stage": "completed",
      "percent": 100,
      "message": "completed"
    },
    "job_result": {
      "book_id": "2042",
      "accepted": true,
      "tags": [
        {
          "tag_id": "theme.love",
          "name": "Love",
          "confidence": 0.94
        }
      ],
      "output_ref": {
        "kind": "oss_object",
        "uri": "oss://bucket/output/job_01JZ8Q7Y4W7X2Z6M8N9P0Q1R2S/result.json",
        "sha256": "6c4f9d1b8e22"
      },
      "summary": {
        "tag_count": 1,
        "language": "en-US"
      }
    },
    "job_error": null,
    "callback": {
      "status": "delivered",
      "attempt": 1,
      "last_error": null,
      "next_retry_at": null
    },
    "status_url": "/api/v1/ai-jobs/jobs/job_01JZ8Q7Y4W7X2Z6M8N9P0Q1R2S",
    "created_at": "2026-06-20T10:00:01Z",
    "updated_at": "2026-06-20T10:03:01Z",
    "finished_at": "2026-06-20T10:03:00Z"
  }
}
```

Callback 失败正例：

```json
{
  "event": "job.failed",
  "event_id": "evt_01JZ8Q9V3C4D5E6F7G8H9J0K1M",
  "attempt": 1,
  "sent_at": "2026-06-20T10:01:31Z",
  "trigger_request_id": "req-20260620-0001",
  "caller_id": "cpp-service",
  "job": {
    "job_id": "job_01JZ8Q7Y4W7X2Z6M8N9P0Q1R2S",
    "client_request_id": "cpp-20260620-book-2042-tagging",
    "job_type": "short_drama.tagging",
    "job_status": "failed",
    "job_progress": {
      "stage": "failed",
      "percent": 100,
      "message": "failed"
    },
    "job_result": null,
    "job_error": {
      "reason": "AI_PROVIDER_FAILED",
      "details": {
        "provider": "openai",
        "operation": "structured_output"
      },
      "retryable": true
    },
    "callback": {
      "status": "delivered",
      "attempt": 1,
      "last_error": null,
      "next_retry_at": null
    },
    "status_url": "/api/v1/ai-jobs/jobs/job_01JZ8Q7Y4W7X2Z6M8N9P0Q1R2S",
    "created_at": "2026-06-20T10:00:01Z",
    "updated_at": "2026-06-20T10:01:31Z",
    "finished_at": "2026-06-20T10:01:31Z"
  }
}
```

### Job Result 一致性

`job_result` 是调用方看到的公开业务结果，轮询和 callback 必须同源：

- `ResponseEnvelope.data.job.job_result` 和 `CallbackEnvelope.job.job_result` 必须来自同一份 `JobResultSchema` 投影。
- 同一个 Job 的轮询成功终态和成功 callback 中，`job_result` 必须同字段语义、同业务结论。
- `callback` 发送时必须读取已持久化的 Job envelope，不得在 callback adapter 中重新计算业务结果。
- 大文件、对象存储引用、大 JSON 或第三方写回摘要如需对外暴露，必须作为具体任务的 `job_result` 字段表达；输入引用必须放在 `job_params`。通用 Job envelope 不提供独立产物字段。
- `queued`、`running`、`failed`、`canceled` 状态下 `job_result` 字段仍必须存在；无公开结果时值为 `null`。
- 如果某个 `job_type` 明确声明成功终态 `job_result = null`，轮询和 callback 都必须返回 `job_result: null`，不能一边为空、一边给出另一套业务结果。

## Schema 组合

公共 schema 只能定义一次：

| Schema | 归属 |
|---|---|
| `ResponseEnvelope[TData]` | HTTP 成功/失败通用外壳。 |
| `ErrorDetail` | 错误细节，包含 `reason/details/retryable`；HTTP status 和数字 `code` 由错误码注册表提供。 |
| `JobResponseData[TJobResult]` | Job HTTP 响应的 `data` 外壳，只包含 `job`。 |
| `JobEnvelope[TJobResult]` | Job 创建和查询的统一 Job 外壳，由 `JobResponseData.job` 承载。 |
| `CallbackEnvelope[TJob]` | Callback 事件外壳；`job` 必须复用 `JobEnvelope[TJobResult]`。 |
| `Page[T]` | 如启用列表接口，统一分页结构。 |

禁止在 route、workflow 或具体接口里复制 envelope 字段。OpenAPI、README、mock fixture、接口文档都是 schema 投影，不是第二套事实源。

## 注册表真源

注册表必须是 Python 类型、枚举、结构化配置、生成脚本输入或测试可消费 fixture，不能只存在于 Markdown。

operation registry 至少包含：

| 字段 | 规则 |
|---|---|
| `operation_id` | 全局稳定唯一。 |
| `channel` | `http`、`callback`、`external_write`、`internal_service` 或未来 CLI。 |
| `auth_boundary` | 调用方、鉴权方式、授权边界和豁免条件。 |
| `request_schema` | 当前输入 schema 名称；无 body 时显式为 `null`。 |
| `response_data_schema` | HTTP envelope 中 `data` 的当前 schema 名称。 |
| `job_result_schema` | `JobEnvelope.job_result` 对应的当前 schema 名称。 |
| `error_codes` | 允许返回或记录的错误码集合，必须能反查错误码注册表。 |
| `idempotency_key` | 幂等键来源，例如 `caller_id + client_request_id`。 |
| `side_effects` | 可能产生的 DB、broker、对象存储写入、callback 或外部写回副作用。 |
| `log_events` | 必须出现的结构化日志事件名。 |
| `metrics` | 必须维护的低基数 metrics 名称。 |
| `change_policy` | 当前阶段不做旧版本兼容；破坏性变更必须同步修改 schema、文档、示例和 contract tests。 |

当前 schema registry 覆盖范围：

| Schema 类别 | 规则 |
|---|---|
| HTTP request schema | 当前请求结构唯一有效。 |
| HTTP response data schema | 当前 `data` 结构唯一有效。 |
| `job_params_schema` | Job 创建、持久化和 Worker 读取使用同一当前 schema。 |
| `runtime_fields_schema` | runtime snapshot 使用当前 schema 和 hash 校验。 |
| `canonical_result_schema` | 内部结果事实源使用当前 schema 和 hash 校验。 |
| `job_result_schema` | `JobEnvelope.job_result` 使用当前 schema；显式 `null` 也要声明。 |
| `callback_envelope_schema` | Callback 事件外壳使用当前 schema；其中 `job` 复用 `JobEnvelope`。 |

注册表检查必须覆盖 route inventory、OpenAPI、README 示例、mock fixture、异常 handler、`job_type` registry 和 Callback 示例。

## 错误码与异常

必须建立错误码注册表。错误码表是数字项目码、HTTP status、公开消息和排障语义的事实源，不允许只在 route、异常或 Markdown 中临时维护。

错误码注册表至少包含：

| 字段 | 规则 |
|---|---|
| `code` | 数字项目错误码；成功固定为 `0`，错误码必须大于 `0`。 |
| `http_status` | 实际 HTTP 状态码；handler 必须用它设置响应状态。 |
| `reason` | 稳定英文枚举，例如 `INVALID_JOB_TYPE`；便于代码判断、日志检索和跨语言调用。 |
| `msg` | 公开人读消息模板；响应体顶层 `msg` 必须来自这里。 |
| `retryable` | 调用方默认重试语义。 |
| `details_schema` | 对外允许暴露的 `error.details` 字段。 |
| `log_level` | 默认日志级别。 |
| `log_fields` | 日志必须记录的排障字段。 |
| `owner` | 归属模块或维护边界，例如 `api/jobs`、`integrations/ai`。 |

推荐错误码基线：

| code | HTTP status | reason | msg |
|---:|---:|---|---|
| `400001` | `400` | `MALFORMED_JSON` | malformed json |
| `401001` | `401` | `MISSING_API_KEY` | missing api key |
| `401002` | `401` | `INVALID_API_KEY` | invalid api key |
| `403001` | `403` | `CALLER_FORBIDDEN` | caller forbidden |
| `404001` | `404` | `JOB_NOT_FOUND` | job not found |
| `409001` | `409` | `DUPLICATE_CLIENT_REQUEST_ID` | duplicate client_request_id |
| `409002` | `409` | `JOB_STATE_CONFLICT` | job state conflict |
| `422001` | `422` | `INVALID_JOB_TYPE` | invalid job_type |
| `422002` | `422` | `INVALID_JOB_PARAMS` | invalid job_params |
| `422003` | `422` | `INVALID_CALLBACK_CONFIG` | invalid callback config |
| `429001` | `429` | `RATE_LIMITED` | rate limited |
| `500001` | `500` | `INTERNAL_ERROR` | internal error |
| `502001` | `502` | `AI_PROVIDER_FAILED` | ai provider failed |
| `502002` | `502` | `EXTERNAL_SERVICE_FAILED` | external service failed |
| `503001` | `503` | `BROKER_UNAVAILABLE` | broker unavailable |
| `504001` | `504` | `MODEL_CALL_TIMEOUT` | model call timeout |

异常模块分两层：

```text
app/core/exceptions.py
  AppError
  ValidationAppError
  AuthAppError
  NotFoundAppError
  ConflictAppError
  RateLimitAppError
  DependencyAppError
  InternalAppError

app/api/exception_handlers.py
  AppError -> ResponseEnvelope[ErrorDetail]
  RequestValidationError -> 422002 / INVALID_JOB_PARAMS 或更具体校验码
  StarletteHTTPException -> 已注册的 4xx 错误码
  Exception -> 500001 / INTERNAL_ERROR
```

规则：

- 自定义异常必须携带注册错误码或可映射到注册错误码。
- 对外错误不得暴露堆栈、密钥、token、完整供应商响应、完整 Prompt、隐私文本或大载荷。
- handler 统一写入 `request_id`、HTTP status、数字 `code`、`msg`、`error.reason` 和 `retryable`。
- 不允许 route 临时拼错误响应。

## 配置模块

配置入口必须使用 Pydantic Settings，并拆成子对象：

```text
AppSettings
  DatabaseSettings
  RedisSettings / BrokerSettings
  JobSettings
  AIProviderSettings
  StorageSettings
  CallbackSettings
  SecuritySettings
  ObservabilitySettings
```

规则：

- API、Worker、Recovery、脚本任务使用同一配置语义。
- `.env.example` 是应用配置键集合的单向真源。
- 字段必须归类为 `env-driven`、`tunable constants`、`derived`。
- 派生字段不得进入 `.env`、`.env.example` 或部署模板。
- 废弃 key 必须进入拒绝清单，出现即失败。
- 未知应用配置 key 必须失败或进入明确允许清单。
- 非法配置在 Settings 初始化或启动阶段 fail-fast。
- 敏感字段不得出现在 repr、`model_dump()`、日志或错误上报中。

配置检查必须进入 `./scripts/verify.sh check`。

## 安全边界

本服务没有用户系统和 RBAC，但所有非豁免业务接口仍必须有调用方级访问边界。

| 场景 | 规范 |
|---|---|
| 健康检查 | `/health`、`/healthz` 可豁免鉴权，只返回运行状态，不暴露配置、依赖详情或密钥。 |
| 业务 HTTP API | 默认使用服务级 API Key 或调用方级 API Key；鉴权失败返回 401。 |
| 调用方授权 | `caller_id` 必须从可信凭证或可信网关透传中解析，不从请求 body 自报；无权限访问资源时返回 403。 |
| Job 查询 | 调用方只能查询自己创建或被授权访问的 Job；不得只凭 `job_id` 公开读取。 |
| Callback 投递 | Callback URL、签名密钥、事件集合和重试策略来自请求 schema 与配置校验；投递失败不改变 Job 业务终态。 |
| 对象存储引用访问 | 输入引用放在 `job_params`，输出引用放在 `job_result`；下载或临时 URL 必须受权限、过期时间、content type 和大小限制约束。 |
| 外部写回 | RS 写回和其他外部副作用通过 integration adapter 执行，必须声明幂等键和错误转换。 |
| 浏览器接入 | CORS 不是鉴权；前端不得持有生产服务密钥。 |

错误暴露规则：

- 401 表示未认证或凭证无效。
- 403 表示认证成功但无权限。
- 鉴权和授权日志必须包含 `request_id`、`caller_id` 摘要、`operation_id`、失败分类和错误码。
- 日志不得记录完整 API Key、token、签名 secret 或含敏感字段的请求体。

## 日志与 Request Context

日志输出到 stdout/stderr，使用结构化字段。

HTTP 请求日志至少包含：

- `timestamp`
- `service`
- `env`
- `level`
- `event`
- `request_id`
- `trace_id`，如启用
- `trigger_request_id`，后台阶段关联原始入口请求时必填
- `caller_id`
- `operation_id`
- `http_method`
- `http_route`
- `http_status`
- `response_code`
- `error_code`
- `error_reason`
- `duration_ms`

Job 日志至少包含：

- `job_id`
- `job_type`
- `job_status`
- `execution_generation`
- `celery_task_id`
- `stage`
- `attempt`
- `error_code`
- `error_reason`

外部调用日志至少包含：

- `external_service`
- `operation`
- `http_status`，如适用
- `duration_ms`
- `error_code`
- `error_reason`

稳定事件名基线：

| 类别 | 事件名 |
|---|---|
| HTTP | `request_started`、`request_completed`、`request_failed`。 |
| Job | `job_created`、`job_publish_requested`、`job_published`、`job_started`、`job_progressed`、`job_succeeded`、`job_failed`、`job_recovered`。 |
| Callback / 外部写回 | `external_call_started`、`external_call_succeeded`、`external_call_failed`、`callback_scheduled`、`callback_delivered`、`callback_failed`。 |
| 对象存储 | `object_write_started`、`object_written`、`object_write_failed`、`object_deleted`。 |

禁止默认记录：

- 完整请求体和响应体。
- 密钥、token、数据库 URL、签名 secret。
- 完整 Prompt、模型输入输出、隐私文本。
- 大文件内容、完整供应商响应。

`X-Request-ID` 只接受受控字符和长度；非法值必须丢弃并生成新 id。响应头必须回写最终 `X-Request-ID`。

## Metrics

最小 metrics 面：

- HTTP 请求数、耗时、校验失败、未知异常。
- Job 创建、成功、失败、恢复、排队时间、执行时间。
- Callback pending / retrying / delivered / failed 和投递耗时。
- AI provider 调用次数、成功、失败、超时、限流、token 和成本摘要。
- 对象存储写入、大小、清理和失败。

标签必须低基数。禁止把 `request_id`、`trace_id`、`job_id`、`client_request_id`、URL query、Prompt、异常消息放入 metrics 标签。

## DB、ORM 与 Repository

数据库模型使用 SQLAlchemy ORM。ORM model 是持久化结构，不是对外 schema。

目标模块：

```text
app/db/base.py
app/db/session.py
app/db/models/job.py
app/repositories/job_repo.py
```

规则：

- `DatabaseSettings` 独立管理 URL、连接池、echo、迁移连接来源。
- API 使用请求级 session dependency。
- Worker / Recovery 使用同一 session factory 或 Unit of Work 语义；保留必要的 `NullPool` 进程隔离。
- 事务边界在 Service / Unit of Work，不在 Repository。
- Repository 只做读写、查询表达、CAS 更新，不做业务判断、不发外部请求、不投递 broker。
- Alembic 是迁移入口，API 启动不得隐式 `create_tables()`。
- 新字段、索引、唯一约束必须同步 ORM model、迁移和测试。

Job 表是状态权威。Celery task id、broker、日志和 result backend 只能做旁证。

Job 持久化必须能表达：

- `job_id`、`caller_id`、`client_request_id`、`job_type`。
- `status`、progress、终态时间、错误对象。
- `job_params_ref`、`job_params_hash`。
- `runtime_ref`、runtime fields hash。
- canonical result、job_result、callback 状态。
- callback 状态、尝试次数、下次重试时间。
- execution generation、发布状态、恢复状态。

状态迁移必须通过 CAS、行锁、唯一约束或等价机制保护。

Job lifecycle event 是排障、审计、恢复和 timeline 的持久化事实源，不得从普通日志临时反推。

| 事件 | 记录要求 |
|---|---|
| `job.created` | Job 持久化创建完成，记录 `job_id/caller_id/client_request_id/job_type`。 |
| `job.publish_requested` | 已生成执行器消息 id，准备投递。 |
| `job.published` | broker / executor 投递确认完成。 |
| `job.started` | Worker 通过 CAS 从 `queued` 领取到 `running`。 |
| `job.progressed` | 进度阶段、work item、chunk 或摘要变化。 |
| `job.succeeded` | 成功终态和 `job_result` 写入完成。 |
| `job.failed` | 失败终态和注册错误对象写入完成。 |
| `job.recovered` | recovery 执行补偿或收敛动作。 |
| `callback.scheduled` | 终态 callback 或外部写回副作用进入待投递状态。 |
| `callback.delivered` | callback 或外部写回确认成功。 |
| `callback.failed` | callback 或外部写回失败，记录可恢复信息和下次重试计划。 |

事件记录至少包含事件名、Job 标识、状态迁移、时间、触发来源、`trigger_request_id`、错误分类和必要摘要。副作用状态与 Job 业务终态分开记录，不允许 callback 或 RS 写回失败反向修改 `succeeded/failed` 业务终态。

## Integrations

外部系统必须通过 adapter / integration client 进入：

```text
integrations/ai
integrations/storage
integrations/callback
integrations/rs
```

规则：

- Integration 负责外部协议校验、错误转换、超时、重试、幂等和摘要日志。
- Service / Workflow 不直接拼外部 HTTP 响应结构。
- 外部错误必须转成注册错误码或 Job error，不暴露供应商原文。
- Callback、RS 写回、对象存储写入等副作用必须明确幂等键、恢复策略和失败收敛。

## Entrypoints

本项目稳定入口：

- `./scripts/dev.sh`：本地服务生命周期。
- `./scripts/verify.sh`：一次性验证。
- `./scripts/deploy.sh`：compose 部署形态。

脚本入口只做参数分发和稳定命令面，具体能力下沉到子目录原子脚本。脚本不得管理其他仓库、远程数据库、生产平台或跨项目清理。

## 验收基线

规范落地后，至少应有以下可检查入口：

- Settings 字段分类、env key 映射、废弃 key、未知 key 检查。
- `ResponseEnvelope`、`ErrorDetail`、`JobEnvelope`、`CallbackEnvelope` 只有一个公共定义。
- 错误码注册表、operation registry、当前 schema registry、`job_type` registry 无重复和缺失。
- Registry consistency 检查：route inventory 能反查 operation registry；异常和 handler 使用的数字 `code` / `reason` 能反查 error registry；`job_type` registry 引用的当前 schema 能反查 schema registry。
- Envelope allowlist 检查：除 `/health`、`/healthz`、OpenAPI、metrics、下载流和框架生态页面外，所有受保护业务接口必须投影为 `ResponseEnvelope[TData]`。
- OpenAPI/schema 快照不出现裸错误对象、重复 envelope 或未声明顶层字段。
- OpenAPI example、README 示例和 mock fixture 必须能被当前 schema 验证，不能只做各自 snapshot。
- 全局异常转换覆盖校验错误、业务错误、认证错误、未知错误。
- Contract tests 覆盖 `POST /jobs`、`GET /jobs/{job_id}`、Callback 和至少一个失败响应。
- Job envelope round-trip 测试覆盖创建 Job、持久化 Job、查询 `data.job`、生成 `CallbackEnvelope.job`，并校验 `job_result` 一致。
- Repository / Job 状态迁移测试覆盖 CAS、重复消息、终态幂等和 recovery。
- 日志字段测试覆盖 request_id、operation_id、caller_id、job_id 和错误码。
- 日志负向测试覆盖非法 `X-Request-ID` 重生、敏感字段不进入 repr / `model_dump()` / 日志、请求体 / 响应体 / Prompt / 供应商原文不被默认记录。
- Metrics 测试覆盖最小指标和高基数标签禁区。
- `job_params` / `job_result` 中对象存储引用的 hash、大小、权限和过期策略测试。

`./scripts/verify.sh check` 必须执行配置检查、脚本检查、Python 语法检查、测试和 registry consistency suite。新增规范检查应优先纳入该入口。
