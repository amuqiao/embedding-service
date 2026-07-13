# 开发环境 compose-full 操作手册

本文说明开发环境使用 `./scripts/deploy.sh up compose-full` 启动完整服务后，如何查看状态、执行排障脚本和查看日志。

## 先理解运行边界

`compose-full` 表示 API、worker、PostgreSQL 和 Redis 都由 Docker Compose 管理。

```text
宿主机
  -> ./scripts/deploy.sh 管理 compose-full 生命周期
  -> docker compose logs 查看容器日志
  -> curl 或浏览器访问宿主机映射端口

api / worker 容器
  -> /app/start-api.sh 或 /app/start-worker.sh 启动服务
  -> /app/scripts/jobs.sh 查询 Job 数据库证据
```

`/app/scripts/k8s.sh` 只用于带 `KUBERNETES_SERVICE_HOST` 的 K8s Pod 内手动运维，不用于 `compose-full` 生命周期，也不要在 `compose-full` 容器里当作 compose 运维入口。

`compose-full` 和 `./scripts/dev.sh start` 管理的 `local` API / worker 不能混跑。切到 `compose-full` 前先停止本地进程：

```bash
./scripts/dev.sh stop
```

本文默认使用根目录 `.env`。如果显式使用其他配置文件，启动和后续 `docker compose` 命令要保持同一套 `ENV_FILE`、`COMPOSE_PROJECT_NAME` 和端口配置。

## 启动和状态

启动完整 compose 服务：

```bash
./scripts/deploy.sh up compose-full
```

查看 compose-full 状态：

```bash
./scripts/deploy.sh status compose-full
```

直接查看 Compose 服务状态：

```bash
docker compose --profile app ps
```

API 默认映射到宿主机 `8100` 端口，端口由 `.env` 中的 `API_HOST_PORT` 控制：

```bash
curl -fsS http://127.0.0.1:8100/health
curl -fsS http://127.0.0.1:8100/healthz
```

停止 compose-full：

```bash
./scripts/deploy.sh down compose-full
```

`down compose-full` 当前使用 `docker compose stop`，会停止容器但保留 PostgreSQL 和对象存储 volume。

## 在容器内使用 scripts

运行镜像包含 `scripts/` 目录。开发机宿主环境 Python 版本过旧、没有 `.venv`，或不想处理宿主机 `DATABASE_URL` 时，优先进入 `api` 容器执行 `jobs.sh`。

查看 `jobs.sh` 帮助：

```bash
docker compose --profile app exec api /app/scripts/jobs.sh --help
```

查看最近 Job：

```bash
docker compose --profile app exec api /app/scripts/jobs.sh list
```

查看更宽时间窗口：

```bash
docker compose --profile app exec api /app/scripts/jobs.sh list --since 24h --limit 20
docker compose --profile app exec api /app/scripts/jobs.sh summary --since 24h
```

查看当前注册的 `job_type`：

```bash
docker compose --profile app exec api /app/scripts/jobs.sh types
```

排查单个 Job：

```bash
docker compose --profile app exec api /app/scripts/jobs.sh job <job_id>
docker compose --profile app exec api /app/scripts/jobs.sh workflow <job_id>
docker compose --profile app exec api /app/scripts/jobs.sh inspect <job_id>
docker compose --profile app exec api /app/scripts/jobs.sh timeline <job_id> --limit 50
```

`jobs.sh` 只读查询数据库，不创建 Job、不取消、不重试、不补偿、不重放 callback。容器内执行时使用 `docker-compose.yml` 覆盖后的容器网络地址：

```text
DATABASE_URL=postgresql+asyncpg://postgres:postgres@postgres:5432/<POSTGRES_DB>
REDIS_URL=redis://redis:6379/0
```

因此容器内 `jobs.sh` 不依赖宿主机 Python 版本，也不依赖宿主机能解析 `postgres` 服务名。

## 在宿主机使用 scripts

宿主机也可以直接执行 `scripts/jobs.sh`，但必须满足两个条件：

```text
当前仓库有可用的 Python 3.11+ 运行时
DATABASE_URL 指向宿主机映射端口
```

`jobs.sh` 会优先使用仓库内 `.venv/bin/python`；没有 `.venv` 时才回退到系统 `python3` 或 `python`。项目要求 Python `>=3.11`，如果脚本最终使用的是 Python 3.8，会出现类似错误：

```text
ImportError: cannot import name 'Annotated' from 'typing'
```

这种情况下不要继续用 Python 3.8 跑 `jobs.sh`。可以先用项目工具创建 `.venv`，或改用容器内命令。

宿主机 `.env` 中的 `DATABASE_URL` 应使用宿主机端口：

```bash
DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:25432/<POSTGRES_DB>
```

确认环境满足后，宿主机可直接运行：

```bash
./scripts/jobs.sh list --since 24h --limit 20
./scripts/jobs.sh doctor --since 10m
./scripts/jobs.sh inspect <job_id>
```

如果 `jobs.sh list` 输出：

```text
OK        jobs       count=0
no records
```

表示查询成功但当前数据库没有匹配的 Job 记录，不是脚本或数据库连接失败。

## 查看日志

`compose-full` 的 API 和 worker 日志来自容器 stdout/stderr，不在宿主机 `logs/api.log` 或 `logs/worker.log`。

查看 API 日志：

```bash
docker compose --profile app logs --tail=200 api
docker compose --profile app logs -f --tail=200 api
```

查看 worker 日志：

```bash
docker compose --profile app logs --tail=200 worker
docker compose --profile app logs -f --tail=200 worker
```

同时查看 API 和 worker：

```bash
docker compose --profile app logs -f --tail=200 api worker
```

查看迁移容器日志：

```bash
docker compose --profile app logs --tail=200 migrate
```

查看 PostgreSQL 或 Redis 日志：

```bash
docker compose logs --tail=100 postgres
docker compose logs --tail=100 redis
```

Job 压测主流程见 [`job-load-testing-runbook.md`](../job/job-load-testing-runbook.md)。如果 `./scripts/jobs.sh pressure` 需要 `--api-log`，它默认适合读取宿主机本地 `dev.sh` 产生的 `logs/api.log`。`compose-full` 下没有同名宿主机日志文件；需要先把 compose 日志导出成文件，再传给脚本：

```bash
mkdir -p .run
docker compose --profile app logs --no-log-prefix --since 10m api > .run/compose-api.log
./scripts/jobs.sh pressure --since 10m --api-log .run/compose-api.log
```

这条命令需要宿主机 Python 环境满足项目要求。如果宿主机不能运行 `jobs.sh`，容器内仍可执行不带 `--api-log` 的只读查询，例如 `summary`、`drain`、`latency`、`stuck` 和 `inspect`。

## 常见问题

### 容器里提示找不到 `/app/scripts/jobs.sh`

说明当前运行的镜像不是包含 `scripts/` 的新镜像。重新构建并启动：

```bash
./scripts/deploy.sh up compose-full
```

如果仍然失败，确认当前容器使用的是本仓库构建出来的镜像：

```bash
docker compose --profile app ps api
```

### `jobs.sh list` 没有记录

先确认服务状态和 Job 类型：

```bash
./scripts/deploy.sh status compose-full
docker compose --profile app exec api /app/scripts/jobs.sh types
docker compose --profile app exec api /app/scripts/jobs.sh summary --since 24h
```

如果 `types` 正常、`summary` 也没有 Job，说明这套 compose-full 数据库还没有提交过 Job。

### 直接 `docker compose exec api ...` 找不到服务

先确认当前目录是仓库根目录，并且 compose-full 已启动：

```bash
pwd
./scripts/deploy.sh status compose-full
docker compose --profile app ps
```

如果项目使用了非默认 `COMPOSE_PROJECT_NAME`，直接执行 `docker compose` 时也要使用同一个项目名配置；否则可能查到另一套 compose project。
