# 模板项目相关配置替换清单

本文只说明复制模板成为真实业务项目时，哪些配置里的项目身份和项目资源命名需要替换；它不是完整环境变量手册，也不覆盖容量、安全、模型或业务调参。

## 先区分配置类型

复制模板后需要优先清理的是“这个服务是谁、使用哪个项目资源命名空间”。这些值如果继续保留模板默认名，会导致服务标识、数据库、compose 资源或对象存储路径仍然像模板项目。

不属于本文范围的是运行策略和业务能力配置，例如连接池大小、鉴权密钥、模型 ID、Job 容量、超时时间和日志等级。它们也需要按环境配置，但不是“项目改名”问题。

## 必须替换的项目身份

| 配置 | 模板默认值 | 复制项目后的处理 |
|---|---|---|
| `TEMPLATE_NAME` | `fastapi-best-ai-architecture` | 改成目标项目或模板实例名；compose project 默认也会参考它 |
| `SERVICE_NAME` | `fastapi-best-ai-architecture` | 改成运行服务名，用于健康检查和运行期服务身份 |
| `SERVICE_TITLE` | `FastAPI Best AI Architecture` | 改成业务服务标题，用于 OpenAPI / FastAPI 标题 |
| `pyproject.toml` 的 `project.name` | `fastapi-best-ai-architecture` | 不是 env 变量，但复制模板后也应按项目包名替换 |

`SERVICE_API_PREFIX` 默认是 `/api/v1/ai-jobs`。如果新项目仍然暴露通用 AI Job 服务，可以保留；如果需要按业务服务隔离路径，应显式改成业务自己的 API 前缀。这个值不是必须改名项，但复制模板时必须确认。

## 必须替换的数据库命名

应用实际连接哪个 PostgreSQL database，以 `DATABASE_URL` 为准。复制到业务项目后，连接串里的 database name 不应继续使用模板默认值：

```text
fastapi_best_ai_architecture
```

例如应改成业务项目自己的库名：

```bash
DATABASE_URL=postgresql+asyncpg://user:password@postgres-host:5432/your_project_ai_jobs
```

API、worker 和 Alembic 迁移都使用同一套 `DATABASE_URL`。K8s 场景下，Pod 注入的 `DATABASE_URL` 是事实源；不要依赖 `POSTGRES_DB` 来决定应用连接哪个数据库。

本地 compose 还有一个 launcher 配置：

| 配置 | 模板默认值 | 使用场景 |
|---|---|---|
| `POSTGRES_DB` | `fastapi_best_ai_architecture` | 根目录 `.env` 中的 compose PostgreSQL 初始化库名，并用于 compose 内部拼接 `DATABASE_URL` |

使用 local 或 compose 模式时，`POSTGRES_DB` 应与根目录 `.env` 里的 `DATABASE_URL` database name 保持一致。使用 K8s 时，直接检查平台注入的 `DATABASE_URL`。

## 必须替换的 compose 命名空间

`COMPOSE_PROJECT_NAME` 决定本地 compose 容器、网络和 volume 的名称前缀。复制模板后，如果仍使用默认值，同一台机器上可能和模板项目资源混在一起。

| 配置 | 模板默认值 | 复制项目后的处理 |
|---|---|---|
| `COMPOSE_PROJECT_NAME` | `fastapi-best-ai-architecture` | 改成目标项目名；未设置时部署脚本会使用 `TEMPLATE_NAME` 作为默认来源 |

`COMPOSE_PROJECT_NAME` 属于根目录 `.env` 中的 launcher/compose 配置；应用 `Settings` 允许该 key 出现在 `.env`，但不会把它作为业务配置字段使用。

复制模板后，如果多个仓库继续共用同一个 `COMPOSE_PROJECT_NAME`，compose 入口会拒绝复用或接管其他目录的容器；完整运行边界见 [`architecture.md`](architecture.md)。

## 需要确认的对象存储命名空间

如果业务项目启用外部对象存储，应按项目隔离 bucket、根路径或输出前缀，避免多个项目把产物写进同一命名空间。

| 配置 | 默认值或空值 | 复制项目后的处理 |
|---|---|---|
| `OSS_BUCKET` | 空 | 使用业务项目约定的 bucket |
| `OSS_PROJECT_ROOT` | 空 | 使用业务项目根路径或资源命名空间 |
| `OSS_OUTPUT_PREFIX` | `ai-jobs` | 确认是否需要改成业务项目专属前缀 |

`STORAGE_BACKEND`、`OSS_REGION`、`OSS_ACCESS_KEY_ID`、`OSS_ACCESS_KEY_SECRET`、`OSS_ENDPOINT` 和 `OSS_PUBLIC_ENDPOINT` 是对象存储运行方式和凭证配置，不属于项目改名清单。

## 不属于本文清单的配置

以下配置不应混入“项目相关配置替换”文档。它们可以在部署、调优、安全或业务接入文档中处理：

- 运行环境和端口：`APP_ENV`、`API_HOST`、`API_PORT`、`API_HOST_PORT`、`POSTGRES_HOST_PORT`、`REDIS_HOST_PORT`
- 安全和调用方边界：`SERVICE_API_KEY`、`DISABLE_HTTP_AUTH_HEADER`、`DISABLE_CALLER_ID_HEADER`、`ALLOWED_ORIGINS`、`CALLBACK_SIGNING_SECRET`、`ALLOW_INSECURE_CALLBACKS`
- 容量和 worker 参数：`DB_POOL_SIZE`、`DB_MAX_OVERFLOW`、`WORKER_CONCURRENCY`、`WORKER_LOGLEVEL`、`WORKER_RECOVERY_LOOP`、`MAX_ACTIVE_JOBS`
- 模型、Prompt 和价格配置：`OPENAI_API_KEY`、`OPENAI_BASE_URL`、`DEFAULT_MODEL_ID`、`MODEL_CONFIG_PATH`、`PROMPT_CONFIG_PATH`、`PRICING_CONFIG_PATH`
- 具体业务能力配置：所有只针对某个 `job_type` 的模型、限制、输入白名单或业务参数

## 复制后的最小人工检查

复制模板后，至少检查这些位置是否仍残留模板默认名：

```text
.env.example
.env
pyproject.toml
部署平台注入的 DATABASE_URL
K8s Secret / ConfigMap / Helm values 中的服务名和数据库连接串
```

`.env.example` 是后续 `bootstrap` 生成本地 `.env` 的来源。业务项目如果要长期维护自己的模板副本，应先替换 `.env.example` 里的项目身份、数据库名和 compose 命名空间，再生成本地 `.env`。

如果复制项目决定不使用根目录 `.env`，启动 `compose-deps` 或 `compose-full` 前必须显式设置一个存在的 `ENV_FILE`。

重点搜索这些值：

```text
fastapi-best-ai-architecture
fastapi_best_ai_architecture
FastAPI Best AI Architecture
```

如果业务项目已经使用 K8s 部署，还应在 Pod 内确认当前注入的 `DATABASE_URL` 指向业务项目数据库，而不是模板默认数据库。
