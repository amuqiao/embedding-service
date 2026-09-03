# AGENTS.md

本文是 `fastapi-best-ai-architecture` 模板仓库的 Agent 协作入口，只记录本项目内稳定、必要的工作规则。

## 项目边界

本仓库是 FastAPI AI Job 服务模板，负责模型执行、异步 Job、对象存储产物、状态查询和 Callback。

本服务不负责用户系统、项目管理、前端页面状态、业务步骤编排或生产部署。

## 技术栈

- 后端框架：`FastAPI`
- 异步任务：`Taskiq`
- 数据库：`PostgreSQL`
- 缓存和任务 broker：`Redis`
- 迁移工具：`Alembic`
- 包管理：`uv`
- 本地依赖服务：`docker compose`

## 运行与开发入口

本项目区分日常本地 recipe、本地进程入口和 compose 部署入口：

- 日常本地开发使用 `./scripts/run.sh up dev`、`./scripts/run.sh status dev`、`./scripts/run.sh down dev`。
- 本地 API / worker 进程使用 `./scripts/dev.sh`。
- Docker compose 形态使用 `./scripts/deploy.sh`。
- 一次性验证使用 `./scripts/verify.sh`。
- 业务 smoke / E2E 使用 `./scripts/smoke.sh`。
- Job 查询与运维使用 `./scripts/jobs.sh`、`./scripts/job-ops.sh`。
- 已部署 Pod 内手动运维使用 `./scripts/k8s.sh`。

部署配置加载优先级：

```text
运行时显式环境变量
> docker-compose.yml environment
> ENV_FILE 指定的 env 文件
> .env
> 应用默认值
```

`docker-compose.yml environment` 只放运行形态覆盖；业务配置、密钥、模型参数和限制参数来自 env 文件或运行时注入。

本项目不维护生产部署、远程数据库重置、K8s 资源、云平台 Secrets 或 CI/CD 发布流水线。已部署 Pod 内的 PostgreSQL / Redis 连接检查、OSS 显式检查、Alembic 状态查询和手动 Alembic 迁移入口是 `./scripts/k8s.sh`，只使用当前 Pod 注入的应用环境变量。

不要绕过 `scripts/dev.sh` 直接拼散本地服务命令，除非是在排查脚本本身。完整命令以各脚本 `-h`、`scripts/README.md` 和 `docs/current/script-entrypoint-contract.md` 为准。

### 脚本目录维护规则

`scripts/` 是稳定工程接口，不是临时命令堆放区。修改或新增 `scripts/` 入口、子命令、下沉实现或公共 helper 前，必须先阅读 `scripts/README.md` 和 `docs/current/script-entrypoint-contract.md`；细则以这两处和各脚本 `-h` 输出为准。

新增脚本或子命令优先放入已有入口或下沉目录。只有存在独立生命周期、安全边界、事实源，或确实需要被多个入口编排复用时，才新增顶层 `*.sh`；新增顶层入口必须同步更新 `scripts/README.md`。

编排入口只组合稳定入口，不复制具体能力实现。Redis、OSS、Job 查询、Job 写操作、业务 smoke、模型资产和本地素材等能力必须各自只有一个事实源；其他入口需要这些能力时只编排或复用。

脚本 help、输出和测试只维护当前合同，不保留旧格式兼容。涉及远端写入、写库、上传、费用、迁移或覆盖文件的命令必须有显式确认或明确输出路径；`--json` 必须保持 stdout 纯 JSON；配置错误必须 fail-fast。修改脚本行为时同步检查 help、文档和测试。

## 验证要求

修改代码后，优先运行：

```bash
./scripts/verify.sh check
```

修改服务启动、任务执行、数据库迁移、对象存储或 Job 流程后，还应运行：

```bash
./scripts/run.sh up dev
./scripts/verify.sh workflow-smoke
./scripts/run.sh down dev
```

修改 Job 内部执行、Taskiq workflow、分块或 merge 后，优先运行可重复的模板 Job workflow 验证：

```bash
./scripts/run.sh up dev
./scripts/verify.sh workflow-smoke
./scripts/run.sh down dev
```

真实模型业务 e2e 不属于当前模板核心 `scripts/` 命令面。接入正式业务 `job_type` 后，再恢复对应业务 e2e 脚本或放入 `examples/business/`。

如果因本机环境、Docker 权限或端口占用无法验证，必须在回复中明确说明未验证项和原因。

修改 Dockerfile、docker compose、部署脚本或配置加载规则后，至少运行：

```bash
./scripts/deploy.sh check
```

## 环境与安全

- `.env` 是本地私有配置，不提交。
- `.env.example` 是可提交的配置模板。
- `fastapi-best-ai-architecture` 是模板默认名；复用模板时通过 `TEMPLATE_NAME`、`SERVICE_NAME`、`SERVICE_TITLE`、`COMPOSE_PROJECT_NAME`、`POSTGRES_DB` 和 `DATABASE_URL` 替换项目身份，不要把业务项目名硬编码进脚本。
- `.data/` 是本地验证输入，不提交。
- 本地默认端口：API `8100`，PostgreSQL `25432`，Redis `26379`。
- `scripts/dev.sh` 会拒绝明显非本地的 `DATABASE_URL` 和 `REDIS_URL`。
- `scripts/k8s.sh check` 会在 Pod 内打印完整 `DATABASE_URL` / `REDIS_URL` 和解析出的密码，用于生产连接串排障。
- `scripts/k8s.sh migrate --confirm` 是 Pod 内写库迁移动作，只应在一个已部署 Pod 内执行一次。
- 不要在本仓库脚本中加入生产部署、远程数据库重置、密钥写入或跨仓库清理逻辑。

## 配置与目录边界规则

- 本节记录跨项目稳定规范，不承载具体业务逻辑；具体业务入参、流程和模型策略留在业务包、接口文档或 runbook。
- 配置项只暴露稳定控制意图，不暴露底层实现细节或派生结果。
- `APP_ENV` 只表示配置安全规则环境；它不是 API/worker 生命周期开关，也不是自动选择 `.env.*` 文件的开关。
- `.env.example` 只放生产或本地常用的安全旋钮；高级参数默认留在 `Settings`，内部不变量使用模块常量。
- 有联动关系的值不要让用户分别配置最终值；应暴露主控制变量和必要的 buffer、margin、ratio 等增量参数，再由代码集中派生并做 fail-fast 校验。
- 新增或暴露配置项前，必须确认真实生效、默认值合理、非法值会报错、安全边界不会被 silent fallback 绕过。
- 配置项之间不能互相打架；有顺序、容量或生命周期约束时必须集中校验。
- 修改配置项时必须同步检查 `app/core/config.py`、`.env.example`、部署文档和相关测试。
- OSS 接入规则：`.env` 只放默认 OSS 事实，业务包内聚自己的 storage policy / adapter，并由业务 adapter 合成 `app/object_storage` payload；`app/object_storage` 不感知具体业务。
- 脚本类配置文件选择遵循：显式 `--env-file` 或 `ENV_FILE` 表示指定配置文件为事实源，脚本不得再用当前 shell 同名环境变量覆盖该文件；只有默认读取 `.env` 时，才允许当前 shell 环境变量覆盖 `.env`，用于本地临时调试。
- 业务包治理遵循：代码维护静态业务包全集，运行时按 `ENABLED_BUSINESS_PACKAGES` 启用业务包子集，pytest 默认验证全集，smoke 场景列表展示支持全集，执行时按当前 enabled 业务包 fail-fast。全量 executor catalog 仍注册，用于历史 Job 查询、schema 校验和结果投影；`job_type` 是业务包内部注册事实，不通过全局 env 单独裁剪。
- 业务包目录规则：正式业务包放在 `app/business_packages/<package>/`，业务 schema 放 `schemas.py` 或包内同级 schema 文件并通过 `BusinessPackage.schemas` 声明，executor 放在包内 executor 文件，注册入口放 `register.py`。业务包可以拥有多个 `job_type`，但必须由同一个业务包 registrar 统一注册。业务包 `__init__.py` 只保留轻量包说明，不 re-export executor；`register.py` 顶层只导入 metadata、schema 和 error 注册函数，executor 在 `register_job_package()` 内部延迟导入。
- 业务包之间不互相 import。确实属于同一业务语义的多个 `job_type` 应放入同一个业务包；多个业务包都需要且无业务语义的代码才允许沉到 `app/tools/private` 或 `app/tools/providers`。
- 公共 schema 规则：`app/schemas/jobs.py` 只保留平台 Job envelope、callback、progress、billing 关联的公共合同；业务专属 Params、Runtime fields 和 Result schema 不放入公共 schema 模块。

## 代码修改规则

- 先读现有结构，再做小范围修改。
- 优先沿用当前目录、命名和错误响应风格。
- 数据库结构变化必须配套 `alembic/versions/` 迁移。
- API 行为变化应同步更新 schema、测试和 README 中的入口说明。
- 不要引入无关重构、依赖升级或目录迁移。

- `app/object_storage` 不适配业务，业务 adapter 适配 `app/object_storage`。
- 不新增跨业务“复合能力”目录。可复用但无业务语义的代码放 `app/tools/private`；第三方 SDK/client 适配放 `app/tools/providers`；业务语义、HTTP routes、Job schema、executor、workflow、错误码和 storage adapter 都留在业务包内。

## 未发布收口规则

在系统尚未发布且没有外部调用方或历史数据依赖时，移除旧设计应直接更新当前事实源，不保留兼容层、别名、兜底、迁移分支或专项旧合同测试；验证只覆盖当前合同和通用校验机制。只有已发布、已有调用方或已有历史数据依赖时，才设计兼容与迁移。

## 文档规则

- 面向协作的说明默认使用中文。
- 命令、路径、配置键、协议名、接口路径、类名和包名保留英文原文。
- README 只写稳定入口和必要背景；临时排查记录不要写入 README。
- 当前只在 `docs/README.md` 维护一份文档地图；顶层 `README.md` 只保留稳定文档入口，不重复维护完整索引。
- 核心长期文档按 `docs/current/`、`docs/api/`、`docs/plans/`、`docs/runbooks/` 分层维护：current 写当前事实，api 写对外合同和扩展入口，plans 写未来计划，runbooks 写可重复执行的排障手册。
- `docs/archived/` 只保存历史设计和旧计划，不能作为当前事实源或默认阅读路径。
- 默认不要读取、引用或基于 `docs/archived/` 推导当前实现；只有用户明确要求追溯历史设计、恢复旧方案或检查归档内容时，才允许读取该目录。
- 子目录默认不维护 README；只有当单个子目录中文档数量明显增多，且确实需要目录级边界规则时，才考虑新增子目录 README。
- 普通文档不要随意新增“相关文档”“阅读路径”“文档索引”等导航型列表；必要引用只链接直接依赖的事实源或前置规范，避免形成互相引用的维护网。

## 日志规则

- 日志当前事实和新增代码规范以 `docs/current/observability.md` 为准。
- 服务日志必须输出到 stdout/stderr；生产、compose-full 和 Pod 环境以容器或平台日志采集为准。
- 不要在应用代码中默认新增 `logging.FileHandler`，也不要让服务日志只写本地文件。
- `logs/api.log` 和 `logs/worker.log` 只属于 `./scripts/dev.sh` local 模式的 stdout/stderr 重定向结果，不是生产日志合同。
- 新增业务日志优先使用 `app.core.logging.log_event()` 和 `LogEvent` 白名单；新增事件必须同步 registry 引用和测试。
- 不记录密钥、token、完整请求体、完整模型响应、图片二进制、base64 载荷或其他敏感大 payload。

## Git 规则

- 提交必须保持单一意图，不混入无关改动；跨主题改动应拆分提交。
- 提交前确认改动范围、提交主题、入口文档或规则文件同步情况。
- 提交前完成最小必要验证；无法验证时说明原因和剩余风险。
- 提交信息默认使用中文；无仓库规范时优先使用 Conventional Commits，例如 `docs:`、`feat:`、`fix:`、`refactor:`、`chore:`。
- 提交信息优先写“改了什么”和对象，不写空泛标题。
- 只在用户明确要求时提交；非明确要求下不做 `amend`，不改写历史。
