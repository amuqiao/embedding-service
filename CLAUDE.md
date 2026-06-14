# AGENTS.md

本文是 `cms-novel-localize` 仓库的 Agent 协作入口，只记录本项目内稳定、必要的工作规则。

## 项目边界

本仓库是小说本地化 AI 能力层服务，负责模型执行、异步 Job、对象存储产物、状态查询和 Callback。

本服务不负责用户系统、项目管理、前端页面状态、业务步骤编排或生产部署。

## 技术栈

- 后端框架：`FastAPI`
- 异步任务：`Celery`
- 数据库：`PostgreSQL`
- 缓存和任务 broker：`Redis`
- 迁移工具：`Alembic`
- 包管理：`uv`
- 本地依赖服务：`docker compose`

## 部署模式

本项目维护 3 种部署模式：

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
./scripts/dev.sh smoke
./scripts/dev.sh workflow-smoke
./scripts/dev.sh e2e
./scripts/dev.sh check
./scripts/dev.sh stop
./scripts/deploy.sh check
```

`start`、`stop`、`restart`、`status` 支持指定服务：

```bash
./scripts/dev.sh start api
./scripts/dev.sh restart worker
./scripts/dev.sh status api
```

不要绕过 `scripts/dev.sh` 直接拼散命令，除非是在排查脚本本身。

## 验证要求

修改代码后，优先运行：

```bash
./scripts/dev.sh check
```

修改服务启动、任务执行、数据库迁移、对象存储或 Job 流程后，还应运行：

```bash
./scripts/dev.sh start
./scripts/dev.sh smoke
./scripts/dev.sh stop
```

修改 Job 内部执行、Celery workflow、分块或 merge 后，优先运行可重复的 mock 长文本验证：

```bash
./scripts/dev.sh start
./scripts/dev.sh workflow-smoke
./scripts/dev.sh stop
```

需要验证真实模型调用时，确认 `.env` 已配置 `OPENAI_API_KEY` 且 `.data/` 下存在 `.txt` 文件，然后运行：

```bash
./scripts/dev.sh start
./scripts/dev.sh e2e
./scripts/dev.sh stop
```

如果因本机环境、Docker 权限或端口占用无法验证，必须在回复中明确说明未验证项和原因。

修改 Dockerfile、docker compose、部署脚本或配置加载规则后，至少运行：

```bash
./scripts/deploy.sh check
```

## 环境与安全

- `.env` 是本地私有配置，不提交，用于个人本机运行。
- `.env.dev` 是开发环境配置，用于共享开发环境或开发部署。
- `.env.test` 是测试环境配置，用于测试运行和测试部署。
- `.env.example` 是可提交的配置模板，只放配置键、默认示例值和必要注释，不放真实密钥。
- 新增、删除或重命名配置项时，必须同步检查 `.env.example`、`.env.dev`、`.env.test`，并确认 `.env` 是否需要本地更新。
- 同步配置键和注释，不强行统一各环境独有的值；端口、容器地址、模型参数、限制参数、密钥占位等允许按环境保留差异。

### 配置面正确性约束

- **`.env.example` 是配置面基准**：只包含 `Settings` 类中操作员应主动感知的业务配置项和运营调优参数；派生值、内部边距常量（以 `_` 开头的模块常量）不得出现。
- **代码常量不得出现在任何 env 文件中**：已提升为模块常量的字段（如超时链 buffer、AI 调用不重试等）在 `.env.example`、`.env.dev`、`.env.test`、`.env` 中均不得设置，设置无效且制造误导。
- **`.env.dev` / `.env.test` 以 `.env.example` 为基础**：可覆盖值、添加环境私有项（凭证、代理、环境特定覆盖），但不应引入 `.env.example` 未列出的 `Settings` 字段；环境私有项须在行内注释说明原因。
- **修改 `Settings` 字段时必须回写配置文件**：将字段从可配置改为代码常量时，必须同步从 `.env.example`、`.env.dev`、`.env.test` 中删除对应键；将代码常量改为可配置字段时，必须同步向 `.env.example` 补充该键和注释。
- **`.env.example` 的键名必须与 `Settings` 类一致**：提交前确认 `.env.example` 中的每个键都存在于 `app/infrastructure/config.py` 的 `Settings` 类中。`WORKER_*` 系列（`WORKER_CONCURRENCY`、`WORKER_POOL`、`WORKER_LOGLEVEL`）是 `start-worker.sh` 读取的 shell 脚本参数，不在 `Settings` 中，属于合理例外。
- `.data/` 是本地验证输入，不提交。
- 本地默认端口：API `8000`，PostgreSQL `25432`，Redis `26379`。
- `scripts/dev.sh` 会拒绝明显非本地的 `DATABASE_URL` 和 `REDIS_URL`。
- 不要在本仓库脚本中加入生产部署、远程数据库重置、密钥写入或跨仓库清理逻辑。

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

- 提交应尽量保持单一意图，不把多个无关改动混在同一个提交中。
- 如果改动跨多个主题，先拆分，再提交。
- 仓库已有提交规范时，优先遵守仓库规范；若无明确规范，优先使用 Conventional Commits。
- 无明确规范时，可优先使用 `docs:`、`feat:`、`fix:`、`refactor:`、`chore:` 等类型前缀表达主题。
- 提交信息默认使用中文；如果使用 Conventional Commits，类型前缀可保留英文，描述部分默认使用中文。
- 不要积攒过大的杂糅提交；完成一个独立改动后及时提交。
- 提交前先确认改动范围和提交主题一致。
- 提交前确认相关入口文档或规则文件已同步更新。
- 提交信息优先写“改了什么”，再写对象，不写空泛标题。
- 提交前完成最小必要验证；无法验证时，明确说明原因和剩余风险。
- 非明确要求下，不做 `amend`，不改写历史。
- 不为了凑提交而拆出没有独立意义的碎提交。
