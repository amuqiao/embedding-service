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

## 环境与安全

- `.env` 是本地私有配置，不提交。
- `.env.example` 是可提交的配置模板。
- `.data/` 是本地验证输入，不提交。
- 本地默认端口：API `8100`，PostgreSQL `15432`，Redis `16379`。
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
