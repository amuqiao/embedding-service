# 远端测试环境 FastAPI / Redis / Taskiq 排障手册

本文记录远端测试环境中运行镜像依赖、Redis 服务端能力和 Taskiq broker 配置不一致时的排障路径。

本文只处理已经进入 Pod 后的应用运行时排查，不负责 K8s 资源编排、镜像仓库权限、Redis 实例升级或云平台账号权限配置。排障命令会读取运行时环境变量，但文档不记录数据库、Redis、OSS 的密码或 AccessKey。

## 核心模型

远端测试环境常见问题不是单一配置项错误，而是三层运行事实不一致：

```text
代码层
  -> /mnt/app、/mnt/scripts，由初始化容器复制

运行环境层
  -> Python、FastAPI、redis-py、taskiq-redis，由工作容器提供

外部依赖层
  -> PostgreSQL、Redis、OSS，由平台注入连接串和权限
```

判断问题时先切清层级：

```text
app/routes/脚本内容不对
  -> 更新初始化容器代码镜像

Python 包版本不对
  -> 更新工作容器运行环境镜像，或确认 pyproject.toml / uv.lock 的安装方式

Redis 命令不支持
  -> 查 Redis 服务端版本和 TASKIQ_BROKER_KIND，不要只查 redis-py 包版本
```

## 快速判断

进入 API 或 worker Pod 后，先看当前实际运行配置：

```bash
python - <<'PY'
import fastapi
import redis
import sys

from app.core.config import settings
from app.tasks.taskiq_app import broker

print("python=", sys.version.split()[0])
print("fastapi=", fastapi.__version__, fastapi.__file__)
print("redis_py=", redis.__version__, redis.__file__)
print("TASKIQ_BROKER_KIND=", settings.broker.kind)
print("broker_class=", type(broker).__name__)
PY
```

如果 API Pod 已经 CrashLoop，无法稳定 `exec` 进去，可以先在使用同一工作容器镜像的 worker Pod 中检查 Python 包版本和 broker 配置。

预期关系：

```text
TASKIQ_BROKER_KIND=redis_stream -> broker_class=RedisStreamBroker
TASKIQ_BROKER_KIND=redis_list   -> broker_class=ListQueueBroker
```

首选使用 Redis 排障事实源查看服务端能力：

```bash
./scripts/redis.sh check --show-url
```

这个命令会打印完整 `REDIS_URL` 和解析出的密码，只用于受控终端排障，不要把原始输出直接复制到文档或工单。已部署 Pod 内也可以通过 `./scripts/k8s.sh check redis` 编排同一套 Redis 排障能力。

如果 `redis.sh` 入口自身不可用，再用下面的手工 Python 片段复现 Redis 服务端能力：

```bash
python - <<'PY'
import os
from redis import Redis

r = Redis.from_url(os.environ["REDIS_URL"], protocol=2)
print("PING", r.ping())
print("redis_version=", r.info("server").get("redis_version"))

for cmd in ["XADD", "XREADGROUP", "XACK", "XAUTOCLAIM"]:
    try:
        print(cmd, r.execute_command("COMMAND", "INFO", cmd))
    except Exception as exc:
        print(cmd, type(exc).__name__, exc)
PY
```

Redis 5.0.0 支持基础 Stream 命令，但不支持 `XAUTOCLAIM`。因此 Redis 5 环境不能使用 `redis_stream` broker。

## FastAPI 版本漂移

### 现象

API Pod 启动时报：

```text
ValueError: registered operation ids not mounted by routes:
['create_ai_job', 'get_ai_job', 'get_job_billing', ...]
```

如果远端 `fastapi.__version__` 高于项目锁定版本，可能是工作容器构建时没有使用 `uv.lock`，而是只根据 `pyproject.toml` 重新解析依赖。

### 判断命令

```bash
python - <<'PY'
import fastapi
print("fastapi_version=", fastapi.__version__)
print("fastapi_file=", fastapi.__file__)
PY
```

如果要做路由挂载存在性的 sanity check，可以手工组装一个临时 `FastAPI()` 实例：

```bash
python - <<'PY'
from fastapi import FastAPI
from app.api.routes import health, jobs, meta
from app.core.config import settings

a = FastAPI()
a.include_router(health.router)
a.include_router(meta.router, prefix=settings.service.api_prefix)
a.include_router(jobs.router, prefix=settings.service.api_prefix)

for path, methods in a.openapi()["paths"].items():
    for method, op in methods.items():
        print(method.upper(), path, op.get("operationId"))
PY
```

如果这个临时实例能看到业务 `operationId`，但真实 `app.main` 启动仍在 route registry 自检阶段报未挂载，下一步应继续核对真实 `create_app()` 装配路径、FastAPI 版本和 `app.routes` / `app.openapi()` 输出，不要直接按业务路由缺失处理。

### 处理原则

优先保证远端工作容器依赖与项目一致：

```text
pyproject.toml 固定直接依赖版本
uv.lock 同步
工作容器镜像重新构建并发布
```

本项目当前已经将 Python 和直接运行依赖固定在 `pyproject.toml`，用于降低远端只按 `pyproject.toml` 安装时的依赖漂移。

## Redis 协议与客户端版本

### HELLO 3 报错

如果 `./scripts/redis.sh check`、`./scripts/k8s.sh check redis` 或应用连接 Redis 时报：

```text
unknown command `HELLO`, with args beginning with: `3`, `AUTH`, ...
```

说明客户端正在用 RESP3 握手，但 Redis 服务端或代理不支持 `HELLO`。

判断命令：

```bash
python - <<'PY'
import os
from redis import Redis

url = os.environ["REDIS_URL"]

for protocol in (2, 3):
    client = Redis.from_url(url, protocol=protocol, socket_connect_timeout=5, socket_timeout=5)
    try:
        print("protocol", protocol, "ping=", client.ping())
    except Exception as exc:
        print("protocol", protocol, "error=", type(exc).__name__, exc)
    finally:
        client.connection_pool.disconnect()
PY
```

如果 `protocol 2` 成功、`protocol 3` 失败，可以在 Redis 5 环境使用：

```text
REDIS_URL=redis://:<password>@<host>:<port>/<db>?protocol=2
```

不要把 Redis 服务端版本和 Python `redis` 包版本混为一谈：

```text
redis-py 版本
  -> Python 客户端包

INFO redis_version
  -> Redis 服务端能力
```

## TASKIQ_BROKER_KIND 选择

### redis_stream

`redis_stream` 使用 `RedisStreamBroker`：

```text
优点：基于 Redis Stream consumer group，有 ACK / pending / 恢复语义
要求：Redis 6.2+，因为 taskiq-redis 会调用 XAUTOCLAIM
适用：Redis 服务端满足命令要求时优先使用
```

Redis 5 环境使用 `redis_stream` 会报：

```text
unknown command `XAUTOCLAIM`
```

### redis_list

`redis_list` 使用 `ListQueueBroker`：

```text
优点：兼容 Redis 5，依赖命令少
限制：恢复语义弱于 redis_stream
适用：测试或生产 Redis 只能提供 5.0.0 时
```

项目当前允许 `local`、`test`、`prd` 都显式配置：

```env
TASKIQ_BROKER_KIND=redis_list
```

API Pod 和 worker Pod 必须使用同一个 broker kind。否则 API 可能按一种队列类型写入，worker 按另一种队列类型读取。

## 从 redis_stream 切到 redis_list

### 现象

切到 `redis_list` 后，worker 可能报：

```text
redis.exceptions.ResponseError: WRONGTYPE Operation against a key holding the wrong kind of value
```

堆栈里会出现：

```text
redis_conn.brpop(self.queue_name)
```

这说明 `redis_list` 已经生效，但 Redis 中同名队列 key 仍是之前 `redis_stream` 留下的 Stream 类型。

### 判断 key 类型

当前 pinned `taskiq-redis` 默认队列 key 是 `taskiq`：

```bash
python - <<'PY'
import os
from redis import Redis

r = Redis.from_url(os.environ["REDIS_URL"], protocol=2)
print("taskiq type=", r.type("taskiq"), "exists=", r.exists("taskiq"))
PY
```

如果输出：

```text
taskiq type= b'stream' exists= 1
```

而当前 broker 是 `ListQueueBroker`，就会触发 `WRONGTYPE`。

### 清理旧队列 key

禁止在生产直接执行下面的删除命令。它会丢失默认队列中的待处理任务；只在确认测试环境队列可清空时执行：

```bash
python - <<'PY'
import os
from redis import Redis

r = Redis.from_url(os.environ["REDIS_URL"], protocol=2)
print("before", r.type("taskiq"), r.exists("taskiq"))
print("deleted", r.delete("taskiq"))
print("after", r.type("taskiq"), r.exists("taskiq"))
PY
```

预期：

```text
before b'stream' 1
deleted 1
after b'none' 0
```

然后重启 worker Pod。

重启后确认：

```bash
python - <<'PY'
from app.core.config import settings
from app.tasks.taskiq_app import broker

print("TASKIQ_BROKER_KIND=", settings.broker.kind)
print("broker_class=", type(broker).__name__)
PY
```

预期：

```text
TASKIQ_BROKER_KIND= redis_list
broker_class= ListQueueBroker
```

没有任务时，`taskiq` key 可能不存在：

```text
taskiq type= b'none' exists= 0
```

有任务入队后，它才会变成 List 类型。

## OSS 检查补充

远端 OSS 配置必须作为进程环境变量注入。使用 ConfigMap 或配置字典时，可以通过 `envFrom` 或逐项 `env:` 注入；如果只是把 `.env` 文件挂载到 `/mnt/.env`，`os.getenv()` 不会自动读取这个文件。

检查入口：

```bash
./scripts/k8s.sh check oss --confirm
```

Pod 入口会编排对象存储事实源：

```bash
./scripts/oss.sh check --remote --confirm
```

当前检查要求 `PUT / GET / HEAD` 成功，不要求 `DeleteObject` 权限。输出会包含测试对象信息和可用时的 `public_url`：

```text
[OK] remote: key=... bytes=... sha256=... retained=true
[OK] object: public_url=...
```

检查对象会保留在 OSS，需要按输出 key 手动清理或配置生命周期规则清理。

## 当前远端测试环境容器分工示例

当前远端测试环境使用初始化容器和工作容器分工。其他部署形态应以实际平台编排为准：

```text
初始化容器
  -> 提供仓库代码、app、scripts、docs
  -> 代码或脚本改动后更新它

工作容器
  -> 提供 Python、系统依赖、已安装包
  -> Python 版本或依赖安装方式改动后更新它
```

常见判断：

```text
scripts/k8s.sh 改了
  -> 更新初始化容器

pyproject.toml / uv.lock / Python 运行环境改了
  -> 更新工作容器

env_test/.env 改了
  -> 更新配置字典或 Pod 环境变量，并重启相关 Pod
```

## 安全边界

本文命令不要复制输出中的密码、token、完整连接串或 AccessKey 到文档、工单或聊天记录。

可以记录：

```text
包版本
Redis server 版本
broker kind
Redis key 类型
错误类型和命令名
```

不要记录：

```text
DATABASE_URL 完整密码
REDIS_URL 完整密码
OSS_ACCESS_KEY_SECRET
服务 API key
Callback signing secret
```
