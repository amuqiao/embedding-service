# 模板采用 Runbook

本文是复制本仓库成为新业务 AI Job 微服务时的操作清单。当前模板能力边界见 [`../current/template-readiness.md`](../current/template-readiness.md)；本文只写可执行检查和替换步骤。

## 先确认边界

复制模板不是“改一个服务名”就完成。至少要同时处理：

```text
项目身份
数据库 / Redis 隔离
compose 命名空间
对象存储命名空间
模型 / Prompt / pricing 配置
Callback 签名
demo job_type 暴露边界
```

不属于本文范围的是业务 schema 设计、生产部署方案、云平台 Secrets、容量采购和正式业务 e2e 设计。

## 替换项目身份

| 配置 | 模板默认值 | 复制项目后的处理 |
|---|---|---|
| `TEMPLATE_NAME` | `fastapi-best-ai-architecture` | 改成目标项目或模板实例名 |
| `SERVICE_NAME` | `fastapi-best-ai-architecture` | 改成运行服务名 |
| `SERVICE_TITLE` | `FastAPI Best AI Architecture` | 改成业务服务标题 |
| `pyproject.toml` `project.name` | `fastapi-best-ai-architecture` | 改成业务项目包名 |

`SERVICE_API_PREFIX` 默认是 `/api/v1/ai-jobs`。如果新项目仍然暴露通用 AI Job 服务，可以保留；如果需要按业务服务隔离路径，应显式改成业务自己的 API 前缀。

## 替换数据库和 compose 命名

应用实际连接哪个 PostgreSQL database，以 `DATABASE_URL` 为准。复制后不要继续使用模板默认库名：

```text
fastapi_best_ai_architecture
```

本地 compose 还需要同步：

| 配置 | 作用 |
|---|---|
| `POSTGRES_DB` | compose PostgreSQL 初始化库名，并用于 compose 内部拼接连接串 |
| `COMPOSE_PROJECT_NAME` | compose 容器、网络和 volume 的名称前缀 |

使用 local 或 compose 模式时，`POSTGRES_DB` 应与根目录 `.env` 中 `DATABASE_URL` 的 database name 保持一致。K8s 或平台部署场景下，Pod 注入的 `DATABASE_URL` 是事实源。

## 确认对象存储命名空间

如果启用外部对象存储，应按项目隔离 bucket、根路径或输出前缀：

| 配置 | 默认值或空值 | 复制项目后的处理 |
|---|---|---|
| `OSS_BUCKET` | 空 | 使用业务项目约定的 bucket |
| `OSS_PROJECT_ROOT` | 空 | 使用业务项目根路径或资源命名空间 |
| `OSS_OUTPUT_PREFIX` | `ai-jobs` | 确认是否需要业务项目专属前缀 |

`STORAGE_BACKEND`、`OSS_REGION`、`OSS_ACCESS_KEY_ID`、`OSS_ACCESS_KEY_SECRET`、`OSS_ENDPOINT` 和 `OSS_PUBLIC_ENDPOINT` 是对象存储运行方式和凭证配置，不属于项目改名清单。

## 处理业务配置

以下配置不应混入项目改名，但复制模板时必须按业务确认：

- 安全和调用方边界：`SERVICE_API_KEY`、`DISABLE_HTTP_AUTH_HEADER`、`DISABLE_CALLER_ID_HEADER`、`ALLOWED_ORIGINS`、`CALLBACK_SIGNING_SECRET`、`ALLOW_INSECURE_CALLBACKS`
- 容量和 worker 参数：`DB_POOL_SIZE`、`DB_MAX_OVERFLOW`、`WORKER_CONCURRENCY`、`MAX_ACTIVE_JOBS`
- 模型、Prompt 和价格配置：`OPENAI_API_KEY`、`OPENAI_BASE_URL`、`DASHSCOPE_API_KEY`、`DASHSCOPE_BASE_URL`、`MODEL_CONFIG_PATH`、`PROMPT_CONFIG_PATH`、`PRICING_CONFIG_PATH`
- 具体业务能力配置：所有只针对某个 `job_type` 的模型、限制、输入白名单或业务参数

使用 `poster_title_image`、`audio_stem_separation` 或 `audio_stem_separation_triton` 时，还要确认对应模型配置、输入 OSS 白名单、模型资产目录或 Triton endpoint。

## 处理 demo 能力

模板内置 demo `job_type` 可以保留用于本地或内部验证，但不能作为发布环境的外部入口。业务服务复制模板后有两种选择：

| 选择 | 适用场景 |
|---|---|
| 保留 demo | 需要继续运行模板 smoke 或内部验证 |
| 移除或禁用 demo | 不希望业务仓库保留模板验证能力 |

`APP_ENV=test` 或 `APP_ENV=prd` 时，服务只允许外部提交 `visibility="public"` 的 `job_type`。正式业务应新增自己的 public `job_type`。

## 最小检查

复制后搜索模板默认名：

```text
fastapi-best-ai-architecture
fastapi_best_ai_architecture
FastAPI Best AI Architecture
```

至少检查：

```text
.env.example
.env
pyproject.toml
部署平台注入的 DATABASE_URL
K8s Secret / ConfigMap / Helm values 中的服务名和数据库连接串
```

如果业务项目不使用根目录 `.env`，启动 `compose-deps` 或 `compose-full` 前必须显式设置存在的 `ENV_FILE`。

## 验证命令

复制模板并完成业务改名后，先跑离线检查：

```bash
./scripts/verify.sh check
```

如果业务项目会使用 compose 入口：

```bash
./scripts/deploy.sh check
```

准备发布到测试或生产环境前，用目标配置文件做启动配置校验：

```bash
./scripts/verify.sh env-config --env-file .env.test --app-env test
./scripts/verify.sh env-config --env-file .env.prd --app-env prd
```

涉及 Job、Taskiq、Callback、Workflow 或 Recovery 的改动，还要跑本地服务 smoke：

```bash
./scripts/run.sh up dev
./scripts/verify.sh workflow-smoke
./scripts/verify.sh workflow-modes-smoke
./scripts/run.sh down dev
```

接入正式业务 `job_type` 后，新增业务 e2e。业务 e2e 应验证真实输入、真实 child Job、真实对象存储产物、Callback mock 和 billing 读模型。
