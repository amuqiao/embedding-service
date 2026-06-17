# FastAPI AI Job Template

FastAPI AI Job 执行后端模板。服务只负责模型执行、异步 Job、产物写入对象存储、状态查询和 Callback，不承担用户系统、项目管理、前端页面状态或业务步骤编排。新的 Job 类型通过 `WorkflowHandler` 接入，并在 `app/workflows/register.py` 的统一入口注册；`novel_localization` 是内置示例 workflow，实现在 `app/workflows/novel_localization/handler.py`。

外部 Job 合同采用稳定骨架：`POST /jobs` 顶层只包含 `client_request_id`、`job_type`、`job_params`、`callback`、`metadata` 和 `options`；具体任务入参由 `job_type` 的 `job_params` schema 定义。`GET /jobs/{job_id}` 返回统一 JobView，终态 `result` / `error` 与 Callback envelope 中的 `job` 字段复用同一结构。

## 本地启动

```bash
./scripts/dev.sh bootstrap
./scripts/dev.sh start
./scripts/dev.sh status
```

`./scripts/dev.sh start` 会启动 PostgreSQL / Redis，执行 Alembic 迁移，并启动 FastAPI API 与 Celery worker。

## 运行与部署模式

本项目区分 1 个本地运行入口和 2 个 compose 部署入口：

- `local`：宿主机运行 FastAPI API 和 Celery worker，`docker compose` 只提供 PostgreSQL / Redis。本地开发默认使用此模式，入口是 `./scripts/dev.sh`。
- `compose-deps`：只启动 PostgreSQL / Redis 依赖服务，适合给宿主机上的应用进程提供依赖。
- `compose-full`：API、worker、PostgreSQL、Redis 全部由 `docker compose` 管理，并在应用启动前执行 Alembic 迁移。

`deploy.sh` 只管理 compose 部署入口：

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
- `GET /api/v1/ai-jobs/models`
- `GET /api/v1/ai-jobs/prompt-templates`
- `POST /api/v1/ai-jobs/jobs`
- `GET /api/v1/ai-jobs/jobs/{job_id}`

API 前缀由 `SERVICE_API_PREFIX` 配置，默认是 `/api/v1/ai-jobs`。

Prompt 配置文件由 `PROMPT_CONFIG_PATH` 指定，默认是内置示例 `app/workflows/novel_localization/prompts.yaml`。该 YAML 是运行时 Prompt 的默认配置源，定义各 `job_type` 的 `system/user/work_note` 默认值和运行时输出契约。

模型配置文件由 `MODEL_CONFIG_PATH` 指定，默认是 `app/core/models.yaml`。新增或停用模型时优先修改该 YAML，配置项包括对外 `model_id`、LiteLLM model id、上下文窗口、所需环境变量和模型调用参数。

除 `/health` 和 `/healthz` 外，请求必须携带：

```http
Authorization: Bearer dev-service-key
```

## 冒烟验证

```bash
./scripts/verify.sh smoke
```

默认使用真实模型；需要在 `.env` 配置 `OPENAI_API_KEY`。对象存储默认使用本地后端，文件写入 `storage/objects/`。单次模型调用超时由 `MODEL_CALL_TIMEOUT_SECONDS` 控制，e2e 脚本的 `--timeout-seconds` 只控制脚本轮询等待时间。

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
./scripts/verify.sh oss --env-file .env.dev
```

脚本会在 `OSS_PROJECT_ROOT` 下写入一个临时对象，验证 `PUT`、`GET`、`HEAD` 后默认删除。服务运行时要使用阿里云 OSS 时，将本地 `.env` 中的 `STORAGE_BACKEND` 设为 `aliyun_oss`，并配置同一组 `OSS_*` 环境变量。

## 开发脚本

```bash
./scripts/dev.sh bootstrap
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
./scripts/dev.sh ports --format json --ports 3000,5173,8000-8010
./scripts/dev.sh --help
```

`dev.sh` 是本服务的本地服务总控脚本：

- `bootstrap`：缺少 `.env` 时从 `.env.example` 生成，并执行 `uv sync`。
- `start [api|worker]`：启动指定服务；不传服务名时启动 PostgreSQL、Redis、执行数据库迁移、启动 API 和 worker，并检查 `/health`。
- `stop [api|worker]`：停止指定服务；不传服务名时停止 API、worker、PostgreSQL 和 Redis。
- `restart [api|worker]`：重启指定服务；不传服务名时重启完整本地服务栈。
- `status [api|worker]`：展示指定服务状态；不传服务名时展示依赖容器、应用进程 PID、日志路径和健康状态。
- `logs api|worker`：跟随查看 API 或 worker 日志。
- `migrate`：显式执行 Alembic 迁移。
- `ports [port ...]`：扫描本地可用端口，支持 AI 可读 JSON 输出和运行时参数，适合多个本地项目避免端口冲突。

端口检测示例：

```bash
./scripts/dev.sh ports
./scripts/dev.sh ports --format json
./scripts/dev.sh ports 3000 5173 8100
./scripts/dev.sh ports --ports 3000,5173,8000-8010 --format json
./scripts/dev.sh ports --ports 3000-3020 --count 3
```

`ports` 默认扫描一组常见本地开发端口；也可以通过位置参数或 `--ports` 传入端口和范围。JSON 输出包含 `schema_version`、`kind`、`ok`、`recommended_ports`、`free_ports`、`busy_ports` 和逐端口 `checks`，适合 AI 或自动化脚本直接读取。常用运行时参数包括 `--format text|json`、`--host`、`--ports`、`--count`、`--allow-busy`。

验证类命令统一由 `verify.sh` 承接：

```bash
./scripts/verify.sh test
./scripts/verify.sh smoke
./scripts/verify.sh mock-smoke
./scripts/verify.sh workflow-smoke
./scripts/verify.sh e2e --input-file .data/test_novel.txt
./scripts/verify.sh oss --env-file .env.dev
./scripts/verify.sh check
./scripts/verify.sh --help
```

- `test`：运行本地 pytest。
- `smoke`：对已运行 API 执行真实模型短链路验证。
- `mock-smoke`：使用 Mock OpenAI 和本地存储验证完整任务流程，不调用真实模型；会临时停止已运行的 worker，并在结束时仅恢复原本处于运行状态的 worker。
- `workflow-smoke`：使用真实模型和放大输入验证服务内部自动分块、Celery canvas 和 merge。
- `e2e`：从 `.data` 读取 `.txt`，使用真实模型模拟调用方请求，枚举 `health`、`models`、`prompt-templates`、`jobs`、轮询和 callback，并用内置 `novel_localization` 示例 workflow 验证 artifact 契约。
- `oss`：校验 Aliyun OSS 读写删除连通性。
- `check`：运行脚本语法检查和 pytest。

脚本入口采用“中控脚本 + 子目录原子脚本”的结构：`scripts/dev.sh` 调度 `scripts/dev/` 中的本地服务能力，`scripts/verify.sh` 调度 `scripts/verify/` 中的一次性验证能力，`scripts/deploy.sh` 只调度 compose 部署能力，公共 shell 工具位于 `scripts/lib/`。`dev.sh` 只面向本地开发服务，不做部署、不重置数据库、不管理其他仓库；当 `.env` 中 `DATABASE_URL` 或 `REDIS_URL` 指向非本地主机时，会拒绝执行生命周期和迁移动作。启动 API 前会检查 `8100` 端口是否已被其他进程占用。

入口脚本约束：

- 外层入口脚本只做参数分发、帮助说明和稳定命令面，不承载具体业务实现。
- 具体能力下沉到职责对应的子目录原子脚本；公共 shell 能力放在 `scripts/lib/`。
- `scripts/dev.sh` 只管理本地服务生命周期和本地开发端口探测；验证、对象存储连通性等一次性任务放在 `scripts/verify.sh`。
- `scripts/deploy.sh` 只管理 `compose-deps` 和 `compose-full`，不管理 `local` 本地服务生命周期。
- 不新增 silent fallback、默认吞错或跨职责兼容别名；命令不满足前置条件时应直接报错。

`deploy.sh` 只面向本项目已验收的 compose 部署形态，不负责本地服务生命周期、生产部署、远程数据库、K8s、云平台 Secrets 或 CI/CD 发布流水线。

真实模型端到端验证需要 `.env` 已配置 `OPENAI_API_KEY`，且 `.data/` 下存在至少一个 `.txt` 输入文件：

```bash
./scripts/dev.sh start
./scripts/verify.sh e2e --input-file .data/test_novel.txt
./scripts/dev.sh stop
```

`e2e` 默认会启动本地 callback receiver，并校验 callback body、header、签名与轮询终态一致；如只想跑示例 workflow 主链路，可追加 `--skip-contract-check`；如只想验证 meta 和 `POST /jobs` 错误请求契约、不创建真实模型 Job，可追加 `--contract-only`。完成后会打印本地对象存储中的示例 artifact 和 `e2e_trace/e2e_report.json` 路径，并在 `e2e_trace/` 下按阶段保存 `request.json`、`create_response.json`、`final_response.json`、`callback.json` 和主要 artifact 文本，便于回溯分析。

验证 Job 内部 workflow 会调用真实模型，可能产生费用：

```bash
./scripts/dev.sh start
./scripts/verify.sh workflow-smoke
./scripts/dev.sh stop
```

## 说明文档

- [模板使用指南](docs/template-usage.md)
- [文档导航](docs/README.md)
- [架构总览](docs/架构/架构总览.md)
- [部署与发布手册](docs/部署与发布手册.md)
