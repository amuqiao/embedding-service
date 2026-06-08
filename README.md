# cms-novel-localize

小说本地化 AI 能力层独立服务。服务只负责模型执行、异步 Job、产物写入对象存储、状态查询和 Callback，不承担用户系统、项目管理、前端页面状态或业务步骤编排。

## 本地启动

```bash
./scripts/dev.sh bootstrap
./scripts/dev.sh start
./scripts/dev.sh status
```

`./scripts/dev.sh start` 会启动 PostgreSQL / Redis，执行 Alembic 迁移，并启动 FastAPI API 与 Celery worker。

## 部署模式

本项目维护 3 种部署模式：

- `local`：宿主机运行 FastAPI API 和 Celery worker，`docker compose` 只提供 PostgreSQL / Redis。本地开发默认使用此模式，入口是 `./scripts/dev.sh`。
- `compose-deps`：只启动 PostgreSQL / Redis 依赖服务，适合给宿主机上的应用进程提供依赖。
- `compose-full`：API、worker、PostgreSQL、Redis 全部由 `docker compose` 管理，并在应用启动前执行 Alembic 迁移。

部署入口：

```bash
./scripts/deploy.sh modes
./scripts/deploy.sh check
./scripts/deploy.sh up compose-deps
./scripts/deploy.sh down compose-deps
./scripts/deploy.sh up compose-full
./scripts/deploy.sh status compose-full
./scripts/deploy.sh down compose-full
```

配置加载优先级：

```text
运行时显式环境变量
> docker-compose.yml environment
> ENV_FILE 指定的 env 文件
> .env
> 应用默认值
```

`docker-compose.yml` 中的 `environment` 只覆盖容器运行形态必须不同的值，例如容器网络内的 `DATABASE_URL` / `REDIS_URL` 和容器内对象存储路径。业务配置、密钥、模型参数和限制参数应来自 `.env`、`ENV_FILE` 指定文件或运行时显式环境变量。

默认接口：

- `GET /health`
- `GET /healthz`，兼容部署平台健康检查
- `GET /api/v1/novel-localization-ai/models`
- `GET /api/v1/novel-localization-ai/prompt-templates`
- `POST /api/v1/novel-localization-ai/jobs`
- `GET /api/v1/novel-localization-ai/jobs/{job_id}`

除 `/health` 和 `/healthz` 外，请求必须携带：

```http
Authorization: Bearer dev-service-key
```

## 冒烟验证

```bash
./scripts/dev.sh smoke
```

默认使用 `mock-novel-localizer`，不需要真实 OpenAI Key。对象存储默认使用本地模拟后端，文件写入 `storage/objects/`。

## 阿里云 OSS 连通性测试

开发环境 OSS 凭据只写入本地 `.env.dev` 或 `.env`，不要提交。配置键：

```bash
OSS_BUCKET=
OSS_REGION=
OSS_ACCESS_KEY_ID=
OSS_ACCESS_KEY_SECRET=
OSS_PROJECT_ROOT=
OSS_PUBLIC_ENDPOINT=
```

运行连通性测试：

```bash
./.venv/bin/python scripts/check_aliyun_oss.py --env-file .env.dev
```

脚本会在 `OSS_PROJECT_ROOT` 下写入一个临时对象，验证 `PUT`、`GET`、`HEAD` 后默认删除。服务运行时要使用阿里云 OSS 时，将本地 `.env` 中的 `STORAGE_BACKEND` 设为 `aliyun_oss`，并配置同一组 `OSS_*` 环境变量。

## 开发脚本

```bash
./scripts/dev.sh start
./scripts/dev.sh start api
./scripts/dev.sh restart worker
./scripts/dev.sh stop
./scripts/dev.sh restart
./scripts/dev.sh status
./scripts/dev.sh status api
./scripts/dev.sh logs api
./scripts/dev.sh logs worker
./scripts/dev.sh migrate
./scripts/dev.sh test
./scripts/dev.sh smoke
./scripts/dev.sh workflow-smoke
./scripts/dev.sh e2e
./scripts/dev.sh check
./scripts/dev.sh --help
```

`dev.sh` 是本服务的本地总控脚本：

- `bootstrap`：缺少 `.env` 时从 `.env.example` 生成，并执行 `uv sync`。
- `start [api|worker]`：启动指定服务；不传服务名时启动 PostgreSQL、Redis、执行数据库迁移、启动 API 和 worker，并检查 `/health`。
- `stop [api|worker]`：停止指定服务；不传服务名时停止 API、worker、PostgreSQL 和 Redis。
- `restart [api|worker]`：重启指定服务；不传服务名时重启完整本地服务栈。
- `status [api|worker]`：展示指定服务状态；不传服务名时展示依赖容器、应用进程 PID、日志路径和健康状态。
- `logs api|worker`：跟随查看 API 或 worker 日志。
- `migrate`：显式执行 Alembic 迁移。
- `test`：运行本地 pytest。
- `smoke`：对已运行 API 执行 mock Job 冒烟验证。
- `workflow-smoke`：使用 mock 模型和放大输入验证服务内部自动分块、Celery canvas 和 merge。
- `e2e`：从 `.data` 读取 `.txt`，使用真实 OpenAI 模型模拟后端调用，依次验证本地化、校验、翻译三个 Job；脚本会把 step1 返回的 `project_memory` 显式注入 step2/step3 的 `work_note`，并产出 `localized.txt` 与经过翻译后扫描的 `translated.txt`。
- `check`：运行脚本语法检查和 pytest。

脚本只面向本地开发环境，不做部署、不重置数据库、不管理其他仓库；当 `.env` 中 `DATABASE_URL` 或 `REDIS_URL` 指向非本地主机时，会拒绝执行生命周期和迁移动作。启动 API 前会检查 `8100` 端口是否已被其他进程占用。

`deploy.sh` 只面向本项目已验收的本地/compose 部署形态，不负责生产部署、远程数据库、K8s、云平台 Secrets 或 CI/CD 发布流水线。

真实模型端到端验证需要 `.env` 已配置 `OPENAI_API_KEY`，且 `.data/` 下存在至少一个 `.txt` 输入文件：

```bash
./scripts/dev.sh start
./scripts/dev.sh e2e
./scripts/dev.sh stop
```

`e2e` 完成后会打印本地对象存储中的 `localized.txt`、`translated.txt` 和 `e2e_report.json` 路径。

验证 Job 内部 workflow 可使用 mock 模型，不产生真实模型费用：

```bash
./scripts/dev.sh start
./scripts/dev.sh workflow-smoke
./scripts/dev.sh stop
```

## 说明文档

- [部署与发布手册](docs/部署与发布手册.md)
- [独立服务抽取与流程说明](docs/独立服务抽取与流程说明.md)
