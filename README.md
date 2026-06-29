# fastapi-best-ai-architecture

FastAPI AI Job 执行后端模板。`fastapi-best-ai-architecture` 是模板默认名，复用时应替换为目标项目名，并同步 `.env`、`pyproject.toml` 和部署配置中的服务身份。服务只负责模型执行、异步 Job、产物写入对象存储、状态查询和 Callback，不承担用户系统、项目管理、前端页面状态或业务步骤编排。当前架构以 [当前架构](docs/current/architecture.md) 为基线；新增 `job_type` 按 [扩展接入指南](docs/api/extension-guide.md) 接入。

外部 Job 合同采用标准 envelope 和稳定 Job 骨架：`POST /jobs` 顶层只包含 `client_request_id`、`job_type`、`job_params`、`callback`、`metadata` 和 `options`；具体任务入参由 `job_type` 的 `job_params` schema 定义。`POST /jobs` 和 `GET /jobs/{job_id}` 都返回 `HttpEnvelope[JobResponseData]`，业务字段位于 `data.job`。Callback 使用独立 `CallbackEnvelope`，不套 HTTP envelope。

## 本地启动

```bash
./scripts/dev.sh bootstrap
./scripts/dev.sh start
./scripts/dev.sh status
```

`./scripts/dev.sh start` 会启动 PostgreSQL / Redis，执行 Alembic 迁移，并启动 FastAPI API 与 Taskiq worker。

## 运行与部署模式

本项目区分 1 个本地运行入口和 2 个 compose 部署入口：

- `local`：宿主机运行 FastAPI API 和 Taskiq worker，`docker compose` 只提供 PostgreSQL / Redis。本地开发默认使用此模式，入口是 `./scripts/dev.sh`。
- `compose-deps`：只启动 PostgreSQL / Redis 依赖服务，适合给宿主机上的应用进程提供依赖。
- `compose-full`：API、worker、PostgreSQL、Redis 全部由 `docker compose` 管理，并在应用启动前执行 Alembic 迁移。

`local` 可以与 `compose-deps` 组合使用，但不能与当前仓库下任何 `compose-full` 的 API / worker 混跑。切换到 `compose-full` 前先执行 `./scripts/dev.sh stop`；切回 `local` 前先执行 `./scripts/deploy.sh down compose-full`。

生产 K8s 形态不由本仓库创建或管理资源。api / worker Pod 可继续使用 `start-api.sh` 和 `start-worker.sh` 作为启动入口；Pod 内连接检查和手动数据库迁移使用 `./scripts/k8s.sh`。

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

K8s Pod 内运维入口只在已经部署的 Pod 中执行，不调用 `kubectl`，不创建 Job / Pod / Secret / ConfigMap。它使用当前 Pod 注入的 `DATABASE_URL` / `REDIS_URL` 检查外部 PostgreSQL / Redis，并使用 `DATABASE_URL` 执行 Alembic 迁移：

```bash
./scripts/k8s.sh check
./scripts/k8s.sh check postgres
./scripts/k8s.sh check redis
./scripts/k8s.sh current
./scripts/k8s.sh heads
./scripts/k8s.sh migrate --confirm
```

`check` 是聚合命令，会依次执行 `check postgres` 和 `check redis`；单项检查便于只验证某一个外部连接。检查命令会打印完整连接串、编码密码和解码密码，便于核对生产连接串中特殊字符是否正确 URL 编码。`migrate` 是写库动作，必须显式传入 `--confirm`。生产多副本部署时，只应在一个 Pod 内执行一次迁移，并在执行前确认该 Pod 运行的是要发布的代码版本。

配置加载优先级：

```text
运行时显式环境变量
> docker-compose.yml environment
> ENV_FILE 指定的 env 文件
> .env
> 应用默认值
```

`docker-compose.yml` 中的 `environment` 只覆盖容器运行形态必须不同的值，例如容器网络内的 `DATABASE_URL` / `REDIS_URL` 和容器内对象存储路径。业务配置、密钥、模型参数和限制参数应来自 `.env`、`ENV_FILE` 指定文件或运行时显式环境变量。

`APP_ENV` 是应用运行环境身份，允许值为 `local`、`dev`、`test` 和 `prd`。它是配置安全规则开关，不是 API/worker 生命周期开关，也不是自动选择 env 文件的开关。`local/dev` 属于开发模式；`test/prd` 属于发布模式，使用同一套生产级启动校验。`test` 应作为发生产前的同构验证环境，和 `prd` 的行为差异只应来自数据库、Redis、对象存储和密钥等资源值。

应用默认只自动读取根目录 `.env`。`.env.dev`、`.env.test` 和 `.env.prd` 可以作为开发者本地自管的配置草稿或复制粘贴来源，但项目不维护这些文件，也不会根据 `APP_ENV` 自动选择它们。需要显式使用某份文件时，由启动或部署入口设置 `ENV_FILE`，或由平台直接注入环境变量。

本地配置统一维护在仓库根目录 `.env`，模板是 `.env.example`。`API_PORT`、`API_HOST_PORT`、`POSTGRES_DB`、`POSTGRES_HOST_PORT`、`REDIS_HOST_PORT`、`COMPOSE_PROJECT_NAME`、`WORKER_CONCURRENCY`、`WORKER_LOGLEVEL` 和 `WORKER_RECOVERY_LOOP` 等本地脚本或 compose 编排变量也写入这套文件，避免应用、脚本和 compose 使用不同配置源。

`APP_ENV=test` 或 `APP_ENV=prd` 时，启动会拒绝本地绕过认证、`ALLOW_INSECURE_CALLBACKS=true`、`STORAGE_BACKEND=local`、`TASKIQ_BROKER_KIND=redis_list` 和明显占位或过短的 `SERVICE_API_KEY` / `CALLBACK_SIGNING_SECRET`。`STORAGE_BACKEND=local` 只适用于本地开发或单机 compose；发布模式必须使用外部对象存储后端，例如 `aliyun_oss`，避免 API / worker 节点之间读写不同本地磁盘。

模板身份默认值：

- `TEMPLATE_NAME=fastapi-best-ai-architecture`
- `SERVICE_NAME=fastapi-best-ai-architecture`
- `SERVICE_TITLE=FastAPI Best AI Architecture`
- `POSTGRES_DB=fastapi_best_ai_architecture`（本地 PostgreSQL 初始化库名）
- `COMPOSE_PROJECT_NAME` 未设置时，`scripts/deploy.sh` 使用 `TEMPLATE_NAME` 作为 compose project name；需要显式设置时写入根目录 `.env`。

复用模板时优先替换这些值；不要直接改脚本逻辑来表达业务项目名。

默认接口：

- `GET /health`
- `GET /healthz`，兼容部署平台健康检查
- `GET /api/v1/ai-jobs/models`
- `GET /api/v1/ai-jobs/prompt-templates`
- `POST /api/v1/ai-jobs/jobs`
- `GET /api/v1/ai-jobs/jobs/{job_id}`
- `GET /api/v1/ai-jobs/jobs/{job_id}/billing`

API 前缀由 `SERVICE_API_PREFIX` 配置，默认是 `/api/v1/ai-jobs`。

Prompt 配置文件由 `PROMPT_CONFIG_PATH` 指定，默认是 `app/core/prompts.yaml`。当前内置测试和示例 `job_type` 只用于模板验证与接入样例，边界见 [模板采用就绪度](docs/current/template-readiness.md)；新增正式 LLM 能力时再按项目规范补充 Prompt 模板、`job_type` 注册和验证用例。

`APP_ENV=test` 或 `APP_ENV=prd` 时，`POST /jobs` 只允许提交 `visibility="public"` 的 `job_type`。`visibility="demo"` 的模板示例只能在 `local/dev` 用于本地验证、smoke 或压测；`visibility="internal"` 的类型只供服务内部 workflow child 使用，任何环境都不能被外部直接提交。

模型配置文件由 `MODEL_CONFIG_PATH` 指定，默认是 `app/core/models.yaml`。新增或停用模型时优先修改该 YAML，配置项包括对外 `model_id`、`model_type`、`adapter`、`provider_model`、`adapter_model`、所需环境变量、`limits` / `features` 类型化元信息、内部模型调用参数和可由 `/models` 展示的 `parameters.public`。

`poster_title_image` 的风格探针模型由服务端 `POSTER_TITLE_IMAGE_STYLE_PROBE_MODEL_ID` 配置，默认 `gpt-5.5`；当前该模型也作为 OpenAI Responses 生图调用宿主模型。标题图默认生图模型由 `POSTER_TITLE_IMAGE_GENERATION_DEFAULT_MODEL_ID` 配置，默认 `gpt-image-2`；调用方可传的生图模型范围由 `POSTER_TITLE_IMAGE_GENERATION_ALLOWED_MODEL_IDS` 配置，首版默认仅 `gpt-image-2`。这些模型都必须存在于 `MODEL_CONFIG_PATH` 指向的模型目录并满足对应能力约束。

`poster_title_image` 的单 Job item 数量上限由 `POSTER_TITLE_IMAGE_MAX_ITEMS` 配置，默认 50。单 item 候选图上限由 `POSTER_TITLE_IMAGE_MAX_DRAW_COUNT` 配置，默认 4；该值只能在接口硬上限 `1..4` 内收紧，不能把业务能力放大到 4 以上。

除 `/health` 和 `/healthz` 外，请求必须携带：

```http
Authorization: Bearer dev-service-key
```

`X-AI-Service-Caller-ID` 可选；不传时使用 `default` caller。本地联调可通过 `DISABLE_HTTP_AUTH_HEADER=true` 跳过 Bearer 校验；可通过 `DISABLE_CALLER_ID_HEADER=true` 忽略 caller header 并统一使用 `default` caller。`Settings` 会要求 DB/Redis 指向 loopback；本地 `dev.sh` / `start-api.sh` 入口还会要求 `API_HOST` 是 loopback。生产环境应保持这两个开关为 `false`。

## 冒烟验证

```bash
./scripts/verify.sh check
```

`check` 是模板级最小质量门，不调用真实模型，也不访问外部对象存储。内置 `workflow-smoke` 验证测试 `job_type` 的本地 Job 创建、异步执行和状态轮询流程；`workflow-modes-smoke` 验证 `chain`、`group`、`chord`、`map`、`starmap` 和 `chunks` 六种 DAG-lite workflow 模式。真实模型业务链路需要在接入正式 `job_type` 后另行恢复 `smoke` / `e2e` 验证。

发布到测试或生产环境前，可以在本地提前用目标配置文件执行启动配置校验。该命令不会连接数据库、Redis 或对象存储，只检查 env 键名和 `Settings` 启动规则，包括 `APP_ENV=test/prd` 的发布模式安全约束：

```bash
./scripts/verify.sh env-config --env-file .env.test --app-env test
./scripts/verify.sh env-config --env-file .env.prd --app-env prd
```

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
./.venv/bin/python examples/business/check_aliyun_oss.py --env-file .env.dev
```

该脚本属于业务/供应商扩展示例，不进入 `scripts/verify.sh` 的稳定命令面。脚本会在 `OSS_PROJECT_ROOT` 下写入一个临时对象，验证 `PUT`、`GET`、`HEAD` 后默认删除。服务运行时要使用阿里云 OSS 时，将本地 `.env` 中的 `STORAGE_BACKEND` 设为 `aliyun_oss`，并配置同一组 `OSS_*` 环境变量。

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
./scripts/tools.sh secret --prefix prd_
./scripts/dev.sh --help
```

`dev.sh` 是本服务的本地服务总控脚本：

- `bootstrap`：缺少 `.env` 时从 `.env.example` 生成，并执行 `uv sync`。
- `start [api|worker]`：启动指定服务；不传服务名时启动 PostgreSQL、Redis、执行数据库迁移、启动 API 和 worker，并检查 `/health`。
  本地 API 默认通过 `start-api.sh` 稳定启动；需要热更新时临时执行 `DEV_API_RELOAD=true ./scripts/dev.sh start api`，改用 `uvicorn --reload`。reload 默认使用 polling，避免 macOS 或受限目录中文件监听失败；如需使用系统文件事件，可临时执行 `WATCHFILES_FORCE_POLLING=false DEV_API_RELOAD=true ./scripts/dev.sh start api`。worker 不做自动热更新，代码变更后使用 `./scripts/dev.sh restart worker`。
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
./scripts/verify.sh workflow-smoke
./scripts/verify.sh workflow-modes-smoke
./scripts/verify.sh env-config
./scripts/verify.sh env-config --env-file .env.test --app-env test
./scripts/verify.sh check
./scripts/verify.sh --help
```

- `test`：运行本地 pytest。
- `workflow-smoke`：使用内置 `job_test_echo` 验证本地 Job 创建、异步执行和状态轮询流程，不调用真实模型或外部供应商。
- `workflow-modes-smoke`：使用内置 `job_test_workflow` 验证 `chain`、`group`、`chord`、`map`、`starmap` 和 `chunks` 的 root/child Job e2e。
- `smoke` / `mock-smoke` / `e2e`：当前未接入正式 LLM `job_type`，命令保留但不可用，新增正式模型能力后再恢复对应验证。
- `env-config`：校验 env 文件键名；传 `--env-file` 和 `--app-env` 时，还会实例化应用 `Settings`，提前验证 test/prd 发布模式配置是否能安全启动。
- `check`：运行脚本语法、入口 help、Python 语法、env 配置、registry consistency 和 pytest。

Job 只读排障由 `jobs.sh` 承接：

```bash
./scripts/jobs.sh doctor --since 10m
./scripts/jobs.sh list --status running --since 24h --limit 20
./scripts/jobs.sh show <job_id>
./scripts/jobs.sh inspect <job_id>
./scripts/jobs.sh timeline <job_id> --limit 50
./scripts/jobs.sh stuck --older-than 10m
./scripts/jobs.sh types --json
```

`jobs.sh` 只执行只读查询，不创建 Job、不取消、不重试、不补偿、不重放 callback。默认输出面向人读，`--json` 输出纯 JSON，适合 AI、CI 或运维平台解析。`verify.sh check` 只校验 `jobs.sh --help` 和 Python 语法，不连接数据库。

脚本入口采用“中控脚本 + 子目录原子脚本 + 公共库”的结构：`scripts/dev.sh` 调度 `scripts/dev/` 中的本地服务能力，`scripts/verify.sh` 调度 `scripts/verify/` 中的一次性验证能力，`scripts/jobs.sh` 调度 `scripts/jobs/` 中的只读 Job 排障能力，`scripts/deploy.sh` 只调度 compose 部署能力，`scripts/k8s.sh` 只提供 Pod 内连接检查和 Alembic 运维入口，`scripts/tools.sh` 只提供无默认持久副作用的本地开发辅助工具。公共 shell 能力位于 `scripts/lib/`：`common.sh` 放输出、错误和基础校验，`runtime.sh` 放本地 API / Python venv 等运行时变量，`compose.sh` 放 docker compose 包装。本地脚本变量、应用配置和 compose 编排变量统一从根目录 `.env` 或运行时环境读取；不再维护 `scripts/.env`。`dev.sh` 只面向本地开发服务，不做部署、不重置数据库、不管理其他仓库；当 `.env` 中 `DATABASE_URL` 或 `REDIS_URL` 指向非本地主机时，会拒绝执行生命周期和迁移动作。启动 API 前会检查 `8100` 端口是否已被其他进程占用。

入口脚本约束：

- 外层入口脚本只做参数分发、帮助说明和稳定命令面，不承载具体业务实现。
- 具体能力下沉到职责对应的子目录原子脚本；公共 shell 能力按 `common.sh`、`runtime.sh`、`compose.sh`、`modes.sh` 分层放在 `scripts/lib/`。
- `scripts/dev.sh` 只管理本地服务生命周期和本地开发端口探测；模板级一次性验证放在 `scripts/verify.sh`。
- 业务/供应商扩展示例放在 `examples/business/`，不进入 `scripts/` 稳定命令面。
- `scripts/deploy.sh` 只管理 `compose-deps` 和 `compose-full`，不管理 `local` 本地服务生命周期。
- `scripts/k8s.sh` 只在 K8s Pod 内检查 PostgreSQL / Redis 连接、查询或执行 Alembic 迁移，不管理 K8s 资源，不替代发布编排。
- `scripts/tools.sh` 只放无默认持久副作用的小型开发辅助工具，不读取或修改 `.env`。
- 不新增 silent fallback、默认吞错或跨职责兼容别名；命令不满足前置条件时应直接报错。
- `local` 与 `compose-full` 的 API / worker 运行模式必须互斥；脚本发现混跑或残留进程时应 fail-fast 或在 status 中明确告警。

`deploy.sh` 只面向本项目已验收的 compose 部署形态，不负责本地服务生命周期、生产部署、远程数据库、K8s 资源、云平台 Secrets 或 CI/CD 发布流水线。

模板内置 Job workflow 验证不依赖真实模型：

```bash
./scripts/dev.sh start
./scripts/verify.sh workflow-smoke
./scripts/dev.sh stop
```

真实模型端到端验证不属于当前模板核心 `scripts/` 命令面。接入正式业务 `job_type` 后，再恢复对应业务 e2e 脚本或放入 `examples/business/`。

## 说明文档

- [文档地图](docs/README.md)
