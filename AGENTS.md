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

## 运行与部署模式

本项目区分 1 个本地运行入口和 2 个 compose 部署入口：

- `local`：宿主机运行 API/worker，`docker compose` 只提供 PostgreSQL/Redis；入口是 `./scripts/dev.sh`。
- `compose-deps`：只启动 PostgreSQL/Redis 依赖服务；入口是 `./scripts/deploy.sh up compose-deps`。
- `compose-full`：API、worker、PostgreSQL、Redis 全部由 `docker compose` 管理；入口是 `./scripts/deploy.sh up compose-full`。

部署配置加载优先级：

```text
运行时显式环境变量
> docker-compose.yml environment
> ENV_FILE 指定的 env 文件
> .env
> 应用默认值
```

`docker-compose.yml environment` 只放容器网络地址、容器内端口和容器内路径等运行形态覆盖；业务配置、密钥、模型参数和限制参数来自 env 文件或运行时注入。

本项目不维护生产部署、远程数据库、K8s、云平台 Secrets 或 CI/CD 发布流水线。

## 开发入口

本项目的本地开发统一入口是：

```bash
./scripts/dev.sh --help
```

常用命令：

```bash
./scripts/dev.sh bootstrap
./scripts/dev.sh start
./scripts/dev.sh status
./scripts/dev.sh stop
./scripts/verify.sh workflow-smoke
./scripts/verify.sh check
./scripts/deploy.sh check
```

`start`、`stop`、`restart`、`status` 支持指定服务：

```bash
./scripts/dev.sh start api
./scripts/dev.sh restart worker
./scripts/dev.sh status api
```

不要绕过 `scripts/dev.sh` 直接拼散本地服务命令，除非是在排查脚本本身。一次性验证任务使用 `scripts/verify.sh`。

## 验证要求

修改代码后，优先运行：

```bash
./scripts/verify.sh check
```

修改服务启动、任务执行、数据库迁移、对象存储或 Job 流程后，还应运行：

```bash
./scripts/dev.sh start
./scripts/verify.sh workflow-smoke
./scripts/dev.sh stop
```

修改 Job 内部执行、Taskiq workflow、分块或 merge 后，优先运行可重复的模板 Job workflow 验证：

```bash
./scripts/dev.sh start
./scripts/verify.sh workflow-smoke
./scripts/dev.sh stop
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
- 不要在本仓库脚本中加入生产部署、远程数据库重置、密钥写入或跨仓库清理逻辑。

## 配置面规则

- 配置项只暴露稳定控制意图，不暴露底层实现细节或派生结果。
- `.env.example` 只放生产或本地常用的安全旋钮；高级参数默认留在 `Settings`，内部不变量使用模块常量。
- 有联动关系的值必须由代码派生，并在启动时做 fail-fast 校验。
- 新增或暴露配置项前，必须确认真实生效、默认值合理、非法值会报错、安全边界不会被 silent fallback 绕过。
- 修改配置项时必须同步检查 `app/core/config.py`、`.env.example`、部署文档和相关测试。

## 代码修改规则

- 先读现有结构，再做小范围修改。
- 优先沿用当前目录、命名和错误响应风格。
- 数据库结构变化必须配套 `alembic/versions/` 迁移。
- API 行为变化应同步更新 schema、测试和 README 中的入口说明。
- 不要引入无关重构、依赖升级或目录迁移。

## 文档规则

- 面向协作的说明默认使用中文。
- 命令、路径、配置键、协议名、接口路径、类名和包名保留英文原文。
- README 只写稳定入口和必要背景；临时排查记录不要写入 README。

  # Git 规则

  - 提交必须保持单一意图，不混入无关改动；跨主题改动应拆分提交。
  - 提交前确认改动范围、提交主题、入口文档或规则文件同步情况。
  - 提交前完成最小必要验证；无法验证时说明原因和剩余风险。
  - 提交信息默认使用中文；无仓库规范时优先使用 Conventional Commits，例如 `docs:`、`feat:`、`fix:`、`refactor:`、`chore:`。
  - 提交信息优先写“改了什么”和对象，不写空泛标题。
  - 只在用户明确要求时提交；非明确要求下不做 `amend`，不改写历史。

## 配置设计规则

配置项应表达稳定的控制意图，而不是暴露底层实现细节。新增或调整配置时，优先提供少量真实生效、业务可理解的关键变量，让代码根据这些变量派生内部参数。

存在联动关系的值，不要让用户分别配置多个最终值。应暴露主控制变量和必要的 buffer、margin、ratio 等增量参数，再由代码集中计算最终值，避免出现超时倒挂、容量不一致或 Job 生命周期被调参破坏。

典型规则：

- 配置“模型最长等待多久”，而不是直接暴露完整 worker 超时链。
- 配置“软超时 buffer”“硬超时 buffer”“stale running buffer”，由代码派生 worker soft/hard time limit 和 `JOB_STALE_RUNNING_SECONDS`。
- 配置“单 Worker 并发数”和“接单缓冲倍数”，由代码或部署说明推导 `MAX_ACTIVE_JOBS` 等容量限制。
- 配置“Callback 单次超时”和“领取窗口 buffer”，由代码派生最终领取窗口。
- 配置“总执行槽位倍数”，由代码或部署说明推导积压上限。

配置规则必须满足：

- 每个暴露给用户的配置项都必须真实生效，不保留无效旋钮。
- 配置项之间不能互相打架；有顺序、容量或生命周期约束时必须集中校验。
- 非法配置应在启动或配置加载阶段快速失败，不要静默修正或降级。
- `.env.example`、README、部署脚本和代码默认值必须保持同一套配置语义。
