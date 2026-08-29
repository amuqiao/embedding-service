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

运行形态先按“谁运行 API/worker、谁提供 PostgreSQL/Redis”区分。`APP_ENV` 只参与配置安全校验，不选择运行形态，也不会自动选择 `.env.dev`、`.env.test` 或 `.env.prd`。

| 形态 | 入口 | API/worker 运行位置 | PostgreSQL/Redis 来源 | 关键边界 |
|---|---|---|---|---|
| `dev` recipe | `./scripts/run.sh up dev` | 宿主机 | docker compose 依赖服务 | 本地开发默认路径；编排 `compose-deps`、migration 和宿主机 API/worker |
| `local` | `./scripts/dev.sh` | 宿主机 | 外部或已启动依赖 | 只管理宿主机 API/worker 进程；可复用 `compose-deps`，不能和 `compose-full` 的 API/worker 混跑 |
| `compose-deps` | `./scripts/deploy.sh up compose-deps` | 宿主机或外部进程 | docker compose | 只启动 PostgreSQL/Redis 依赖服务 |
| `compose-full` | `./scripts/deploy.sh up compose-full` | docker compose | docker compose | API、worker、PostgreSQL、Redis 全部由 compose 管理 |
| 已部署 Pod | 平台部署 + `./scripts/k8s.sh` | Pod | 平台注入的外部资源 | 本仓库不创建 K8s 资源、云平台 Secrets 或 CI/CD 流水线 |

`run.sh` 是日常 recipe 入口，只编排 `dev.sh` 和 `deploy.sh` 的稳定命令，不直接实现进程或 Compose 管理。`deploy.sh check` 是只读部署入口检查，不启动服务。`deploy.sh check` 和 `deploy.sh up` 会读取 Docker Compose 容器 label，检查当前 `COMPOSE_PROJECT_NAME` 是否已经被其他 `working_dir` 占用；`deploy.sh up` 还要求 `ENV_FILE` 指向的配置文件存在，默认是 `.env`。

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

当前运行角色按 role-first 设计，部署拓扑可以合并或拆分：

- `api`：运行 `start-api.sh`，只处理 HTTP、创建 Job、查询 Job 和写入 outbox 意图。
- `taskiq-worker`：运行 `start-worker.sh`，只消费 Taskiq `jobs.run_attempt` 并执行 Job attempt。
- `dispatcher`：运行 `start-dispatcher.sh`，扫描 `dispatch_outbox` 并发布 Taskiq 消息。
- `callbacker`：运行 `start-callbacker.sh`，扫描 `callback_outbox` 并投递终态 Callback。
- `reconciler`：运行 `start-reconciler.sh`，修复 stale attempt、missing outbox、workflow root 和 dead-letter 状态。
- `worker-bundle`：运行 `start-worker-bundle.sh`，在一个服务/容器内组合 `taskiq-worker + dispatcher + callbacker + reconciler`。

默认本地和 `compose-full` 使用 `api + worker-bundle` 两服务形态；K8s 小流量部署可以使用 api Pod + worker Pod，并在 worker Pod 内以多容器方式运行四个角色；高流量部署可以把四个角色拆成独立 Deployment。代码入口和数据表不随部署拓扑改变。

API 进程的 PostgreSQL async engine 和 session factory 由 FastAPI lifespan 管理：API startup 创建连接池，API shutdown 释放连接池。静态合同初始化仍由 `bootstrap_runtime()` 完成，供 API、worker、脚本共享。Taskiq worker、dispatcher、callbacker 和 reconciler 不依赖 FastAPI lifespan；后台角色在自己的启动路径初始化运行时。

## 主要模块

| 层 | 当前 owner | 职责 |
|---|---|---|
| API routes | `app/api/routes/` | HTTP route、operation id、认证依赖和 response data schema |
| Schemas | `app/schemas/` | `HttpEnvelope` 内层 data、Job、Callback、Billing、Error 合同 |
| Job kernel | `app/models/job.py`、`app/repositories/job_repo.py`、`app/tasks/jobs.py`、`app/tasks/recovery.py` | Job 聚合、Attempt、Dispatch outbox、Callback outbox、状态迁移和恢复 |
| Job extension | `app/jobs/`、`app/services/job_runtime.py`、`app/services/executor.py` | `job_type` 注册、运行时快照、executor 执行和结果投影 |
| Capability / Tool registry | `app/capabilities/`、`app/tools/`、`app/core/registries/`、`app/core/registry_checks.py` | `Job Type -> Capability -> Tool` 代码注册、ref 校验、graph 校验和启动期 fail-fast |
| Workflow | `app/workflows/`、`app/jobs/types/examples.py` | DAG-lite root/child 编排、ready child 创建、child terminal 后推进和 root 汇总 |
| AI gateway | `app/ai/gateway.py`、`app/ai/kernel.py`、`app/ai/adapters/litellm_client.py` | 模型启用校验、provider 调用、AI call ledger、usage 和 cost 记录 |
| Billing | `app/services/billing.py`、`app/schemas/billing.py` | 从 `ai_call_ledger_entries` 聚合 Job scope billing read model |

## 请求与响应边界

HTTP 成功响应由统一 middleware 包装为 `HttpEnvelope[T]`。route 函数只返回内层 data schema，例如 `JobResponseData`、`JobBillingResponseData`。

请求身份和请求追踪是两条边界：

- `require_service_auth` 校验服务密钥并解析 `X-AI-Service-Caller-ID` 得到 caller 标识；该 header 只适用于单可信上游场景，不是多 caller 安全隔离边界。
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

AI 调用当前事实见 [`ai-capability.md`](ai-capability.md)。当前稳定入口是 `app/ai/gateway.py`，已覆盖文本生成、带参考图文本生成和 `poster_title_image` 使用的图片生成；audio / video provider path 和 workflow node 级成本归因不是当前事实。

AI billing 当前事实见 [`ai-billing.md`](ai-billing.md)。`GET /jobs/{job_id}/billing` 从 `ai_call_ledger_entries` 聚合 Job scope billing；workflow child AI 调用使用 root Job scope 聚合到 root billing。`ai_call_ledger_entries` 是 AI provider call usage / cost estimate 事实源，不是资金账本。

## 配置边界

配置当前事实以 [`configuration.md`](configuration.md) 为准。配置项表达稳定控制意图，不暴露底层派生值。`.env.example` 是可提交配置模板，`.env` 是本地私有实例配置；`Settings` 只消费 application key，脚本和 compose 使用 launcher key，derived / deprecated key 写入 env 文件会被 `env-config` 拒绝。

典型 application key：

- `APP_ENV`
- `SERVICE_API_PREFIX`
- `DATABASE_URL`
- `REDIS_URL`
- `STORAGE_BACKEND`
- `MODEL_CONFIG_PATH`
- `PROMPT_CONFIG_PATH`
- `PRICING_CONFIG_PATH`
- `MAX_ACTIVE_JOBS`
- `POSTER_TITLE_IMAGE_MAX_ITEMS`
- `POSTER_TITLE_IMAGE_ALLOWED_OSS_BUCKETS`
- `POSTER_TITLE_IMAGE_ALLOWED_OSS_REGIONS`
- `BILLING_ENABLED`
- `CALLBACK_SIGNING_SECRET`

`APP_ENV` 允许 `local`、`dev`、`test` 和 `prd`。它是配置安全规则开关，不是 API/worker 生命周期开关，也不是自动选择 env 文件的开关。`test/prd` 是发布模式，启动时使用同一套生产级校验。配置文件选择是显式行为：本地和 compose 入口默认使用 `ENV_FILE=.env`；需要使用 `.env.dev`、`.env.test` 或 `.env.prd` 时，必须显式设置 `ENV_FILE`，或由平台直接注入环境变量。

## 验证基线

模板级最小质量门：

```bash
./scripts/verify.sh check
```

修改 Job 内部执行、Taskiq workflow、Attempt、Recovery、Callback 或对象存储后，还应运行：

```bash
./scripts/run.sh up dev
./scripts/verify.sh workflow-smoke
./scripts/verify.sh workflow-modes-smoke
./scripts/run.sh down dev
```
