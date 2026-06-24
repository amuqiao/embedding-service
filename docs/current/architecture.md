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

本地开发入口是 `./scripts/dev.sh`，部署检查入口是 `./scripts/deploy.sh`。

```text
API Pod(s)
  -> PostgreSQL
  -> Redis broker

Worker Pod(s)
  -> PostgreSQL
  -> Redis broker
  -> Object storage
  -> AI provider adapter

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
| AI gateway | `app/services/ai_gateway_facade.py`、`app/integrations/ai_gateway.py` | 模型启用校验、provider 调用、AI call ledger、usage 和 cost 记录 |
| Billing | `app/services/billing.py`、`app/schemas/billing.py` | 从 `ai_call_ledger_entries` 聚合 Job scope billing read model |

## 请求与响应边界

HTTP 成功响应由统一 middleware 包装为 `HttpEnvelope[T]`。route 函数只返回内层 data schema，例如 `JobResponseData`、`JobBillingResponseData`。

公开 Job route：

- `POST /api/v1/ai-jobs/jobs`
- `GET /api/v1/ai-jobs/jobs/{job_id}`
- `GET /api/v1/ai-jobs/jobs/{job_id}/billing`

元信息 route：

- `GET /api/v1/ai-jobs/models`
- `GET /api/v1/ai-jobs/prompt-templates`

健康检查 route：

- `GET /health`
- `GET /healthz`

Callback 是服务主动向调用方发送的终态事件，不套 HTTP response envelope。Callback payload 使用 `CallbackEnvelope`。

## AI Gateway 与 Billing

`app/services/ai_gateway_facade.py` 是模型调用的业务入口。它负责：

- 校验 `model_id` 已启用。
- 校验 pricing 与模型配置匹配。
- 在 provider 调用前写入 `ai_call_ledger_entries` 的 `pending` 行。
- 调用 `app/integrations/ai_gateway.py` 的 LiteLLM provider adapter。
- 校验 provider usage。
- 计算 cost。
- 将 ledger 行更新为 succeeded 或 failed。

`app/integrations/ai_gateway.py` 只执行 provider 调用并返回 `TextGenerationResult`，不写 Job、Callback、Billing envelope 或数据库。

Job scope 调用必须传入：

- `scope_type="job"`
- `scope_id=str(job_id)`
- `job_id`
- `attempt_id`
- `job_type`

`GET /jobs/{job_id}/billing` 从 `ai_call_ledger_entries` 聚合 Job scope billing。`ai_call_ledger_entries` 是 billing 事实源；后续如新增 summary 表，只能作为派生读模型。

如果 provider 已经被调用但 ledger terminal update 失败，不能通过重放 provider 调用修复账本。recovery 只负责把超时停留在 `pending` 的 ledger 行收敛为失败或未知状态，让 billing read model 显式表达不完整。

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
./scripts/dev.sh stop
```
