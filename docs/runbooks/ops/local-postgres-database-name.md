# 本地 PostgreSQL 数据库名变更排障

本文用于处理本地 `./scripts/run.sh up dev` 时，PostgreSQL 容器健康但 Alembic 迁移报 `database "... " does not exist` 的问题。

## 先理解这件事

本地开发有两个容易混淆的数据库配置：

```text
.env DATABASE_URL
  API、worker、Alembic 实际连接哪个 database。

.env POSTGRES_DB
  docker compose 初始化 PostgreSQL 空数据目录时创建哪个 database。
```

`POSTGRES_DB` 只在 PostgreSQL volume 第一次初始化时生效。后续即使修改 `.env`，已有 volume 里的数据库也不会自动改名或补建。

因此可能出现这种状态：

```text
PostgreSQL 容器 healthy
当前 volume 里只有旧数据库
.env DATABASE_URL 指向新数据库
Alembic 连接新数据库失败
```

## 典型现象

执行：

```bash
./scripts/run.sh up dev
```

依赖容器启动成功，但数据库迁移阶段失败：

```text
psycopg2.OperationalError: connection to server at "127.0.0.1", port ... failed:
FATAL:  database "cms_poster_title" does not exist
```

这不是迁移文件损坏，也不是 PostgreSQL 没启动；通常是本地 volume 里缺少 `.env` 当前连接串指向的 database。

## 排查步骤

先确认脚本当前使用的端口、compose project 和目标库名：

```bash
grep -E '^(DATABASE_URL|POSTGRES_DB|POSTGRES_HOST_PORT|COMPOSE_PROJECT_NAME)=' .env
./scripts/run.sh status dev
```

再查看当前 PostgreSQL 容器里实际有哪些 database。以下命令需要从仓库根目录执行，并把 `VALUE` 替换成 `.env` 中的实际值：

```bash
COMPOSE_PROJECT_NAME=VALUE \
POSTGRES_DB=VALUE \
POSTGRES_HOST_PORT=VALUE \
docker compose exec -T postgres \
  psql -U postgres -Atc "select datname from pg_database where not datistemplate order by datname"
```

如果输出里没有 `.env` 的 `DATABASE_URL` 最后一级 database name，就可以确认是数据库名变更后旧 volume 未补建数据库。

## 推荐修复

如果本地旧数据还要保留，只创建缺失 database，不删除 volume：

```bash
COMPOSE_PROJECT_NAME=VALUE \
POSTGRES_DB=VALUE \
POSTGRES_HOST_PORT=VALUE \
docker compose exec -T postgres \
  createdb -U postgres DATABASE_NAME
```

然后执行迁移，并重启宿主机 API / worker：

```bash
./scripts/dev.sh migrate
./scripts/dev.sh restart
./scripts/dev.sh status
```

成功标准：

```text
Alembic upgrade 跑到 head
api running
worker running
health ok
```

## 可选修复：重建本地 volume

只有确认本地 PostgreSQL 数据可以丢弃时，才删除 compose volume 重建。这个操作会删除该 compose project 下的本地数据库数据。

先停止服务：

```bash
./scripts/run.sh down dev
```

再使用 Docker Compose 删除对应 project 的 PostgreSQL volume。执行前先用 `docker volume ls` 确认 volume 名称，不要删除其他项目的 volume。

重建后再次启动：

```bash
./scripts/run.sh up dev
```

## 改名时的预防规则

复制模板或替换业务项目名时，先同步这几处，再第一次启动本地依赖：

```text
.env
.env.example
```

重点保持这些值一致：

```text
.env DATABASE_URL 的 database name
.env POSTGRES_DB
.env COMPOSE_PROJECT_NAME
.env POSTGRES_HOST_PORT 与 DATABASE_URL 端口
```

如果本地 compose 已经启动过，并且后来才改 database name，必须选择：

```text
保留旧数据：手动 createdb 新库
丢弃旧数据：删除对应 PostgreSQL volume 后重建
```

不要只改 `.env` 后期待已有 volume 自动创建新库。

## 什么时候再脚本化

如果这个问题频繁出现，可以考虑给 `./scripts/dev.sh` 增加只读诊断入口，例如检查：

```text
DATABASE_URL database name
.env POSTGRES_DB
当前 compose project 的 PostgreSQL database 列表
```

是否自动创建 database 需要谨慎。创建缺失 database 是写入动作，应显式确认；删除 volume 是破坏性动作，不应做成默认修复。
