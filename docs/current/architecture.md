# 当前架构

本文只记录当前代码已经落地的稳定事实。历史设计、目标意见和被吸收的长文档放在 `docs/archived/`，不覆盖本文。

## 服务边界

本仓库是 FastAPI AI Job 服务模板，负责：

- 接收异步 AI Job。
- 校验 `job_type` 与 `job_params`。
- 通过 Taskiq worker 执行 Job。
- 写入对象存储产物和标准 Job 结果。
- 提供 Job 查询、Job billing 查询和终态 Callback。

本服务不负责用户系统、项目管理、前端页面状态、跨业务步骤编排、生产部署、云平台 Secrets 或 CI/CD 发布流水线。

## 运行形态

本地开发入口是 `./scripts/dev.sh`，compose 部署检查入口是 `./scripts/deploy.sh`。生产 K8s 资源和发布流水线不由本仓库管理；已部署 Pod 内的 PostgreSQL / Redis 连接检查和 Alembic 迁移入口是 `./scripts/k8s.sh`。

```text
API Pod(s)
  -> PostgreSQL
  -> Redis broker

Worker Pod(s)
  -> PostgreSQL
  -> Redis broker
  -> Object storage
  -> AI provider adapter

Manual migration in one deployed Pod
  -> PostgreSQL

Manual connection check in one deployed Pod
  -> PostgreSQL
  -> Redis broker

Recovery / publisher loop
  -> PostgreSQL
  -> Redis broker
  -> Callback receiver
```

API 与 worker 可以多副本运行。并发安全依赖数据库唯一约束、`SELECT ... FOR UPDATE SKIP LOCKED`、lease token、heartbeat 和 outbox 幂等键，而不是依赖单进程内存。

## 主要模块

| 层 | 当前 owner | 职责 |
|---|---|---|
| API routes | `app/api/routes/` | HTTP route、operation id、认证依赖和 response data schema |
| Schemas | `app/schemas/` | `HttpEnvelope` 内层 data、Job、Callback、Billing、Error 合同 |
| Job kernel | `app/models/job.py`、`app/repositories/job_repo.py`、`app/tasks/jobs.py`、`app/tasks/recovery.py` | Job 聚合、Attempt、Dispatch outbox、Callback outbox、状态迁移和恢复 |
| Job extension | `app/jobs/`、`app/services/job_runtime.py`、`app/services/executor.py` | `job_type` 注册、运行时快照、executor 执行和结果投影 |
| Workflow | `app/workflows/`、`app/jobs/types/job_test_workflow.py` | DAG-lite root/child 编排、ready child 创建、child terminal 后推进和 root 汇总 |
| AI gateway | `app/services/ai_gateway_facade.py`、`app/services/ai_capability_kernel.py`、`app/integrations/ai_gateway.py` | 模型启用校验、provider 调用、AI call ledger、usage 和 cost 记录 |
| Billing | `app/services/billing.py`、`app/schemas/billing.py` | 从 `ai_call_ledger_entries` 聚合 Job scope billing read model |

## 请求与响应边界

HTTP 成功响应由统一 middleware 包装为 `HttpEnvelope[T]`。route 函数只返回内层 data schema，例如 `JobResponseData`、`JobBillingResponseData`。

请求身份和请求追踪是两条边界：

- `require_service_auth` 解析 `X-AI-Service-Caller-ID`，得到 caller 身份。
- `RequestIDMiddleware` 解析或生成本次 HTTP 请求的 `request_id`。`X-Request-ID` 只作为可选链路追踪输入，不表示 caller 身份。
- 对外 header 规则、错误语义和格式约束以 [`../api/service-contract.md`](../api/service-contract.md) 为准。

`RequestIDMiddleware` 会把最终 `request_id` 写入 `request.state.request_id`、日志上下文和 `X-Request-ID` 响应头。成功响应由 `SuccessEnvelopeMiddleware` 使用同一个 `request.state.request_id` 生成 `HttpEnvelope.request_id`；错误响应由异常处理或 `RequestIDMiddleware` 早返回的 `ErrorEnvelope` 使用同一个 `request_id`。创建 Job 时，route 将该值传入 Job service，写入 `runtime_fields._system.trigger_request_id`；后续 Callback payload 的 `trigger_request_id` 也来自该值。

公开 Job route：

- `POST /api/v1/ai-jobs/jobs`
- `GET /api/v1/ai-jobs/jobs/{job_id}`
- `GET /api/v1/ai-jobs/jobs/{job_id}/billing`

元信息 route：

- `GET /api/v1/ai-jobs/models`
- `GET /api/v1/ai-jobs/languages`
- `GET /api/v1/ai-jobs/prompt-templates`

健康检查 route：

- `GET /health`
- `GET /healthz`

Callback 是服务主动向调用方发送的终态事件，不套 HTTP response envelope。Callback payload 使用 `CallbackEnvelope`。

## AI Gateway 与 Billing

AI 调用当前事实见 [`ai-capability.md`](ai-capability.md)。当前稳定入口是 `app/services/ai_gateway_facade.py` 的 `generate_text_with_ledger()`，真实 provider path 覆盖文本生成；多模态 provider path 和 workflow node 级成本归因不是当前事实。

AI billing 当前事实见 [`ai-billing.md`](ai-billing.md)。`GET /jobs/{job_id}/billing` 从 `ai_call_ledger_entries` 聚合 Job scope billing；workflow child AI 调用使用 root Job scope 聚合到 root billing。`ai_call_ledger_entries` 是 AI provider call usage / cost estimate 事实源，不是资金账本。

## 配置边界

配置项表达稳定控制意图，不暴露底层派生值。典型入口：

- `SERVICE_API_PREFIX`
- `DATABASE_URL`
- `REDIS_URL`
- `STORAGE_BACKEND`
- `MODEL_CONFIG_PATH`
- `PROMPT_CONFIG_PATH`
- `PRICING_CONFIG_PATH`
- `MAX_ACTIVE_JOBS`
- `BILLING_ENABLED`
- `CALLBACK_SIGNING_SECRET`

配置加载优先级和本地/compose 运行规则以顶层 `README.md` 与 `AGENTS.md` 为准。

## 验证基线

模板级最小质量门：

```bash
./scripts/verify.sh check
```

修改 Job 内部执行、Taskiq workflow、Attempt、Recovery、Callback 或对象存储后，还应运行：

```bash
./scripts/dev.sh start
./scripts/verify.sh workflow-smoke
./scripts/verify.sh workflow-modes-smoke
./scripts/dev.sh stop
```
