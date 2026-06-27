# Lifespan 与进程生命周期维护手册

本文说明本项目中 FastAPI `lifespan`、Taskiq worker 生命周期、脚本临时资源和共享静态初始化的边界。

本文不负责解释 FastAPI 基础语法，也不负责生产部署编排。生产环境的 Pod、Secret、ConfigMap、CI/CD 和 K8s 资源不由本仓库维护。

## 核心模型

本项目不是单进程应用。即使它们来自同一份代码，也会以不同进程运行：

```text
同一份代码仓库
├─ API 进程      FastAPI / Uvicorn，处理 HTTP 请求
├─ worker 进程   Taskiq，执行异步 Job
├─ recovery loop 可选常驻循环，周期性恢复 outbox、lease、callback 等状态
└─ 脚本进程      verify、jobs.sh、migration、Pod 内排障命令
```

`lifespan` 只属于 FastAPI API 进程：

```text
FastAPI API 进程启动
  -> lifespan startup
  -> HTTP 请求处理
  -> lifespan shutdown
  -> FastAPI API 进程退出
```

它不覆盖 Taskiq worker、一次性脚本、Pod 内手动命令或 recovery loop。

因此判断资源放在哪里时，先问两个问题：

```text
这是静态规则，还是运行期资源?

静态规则
  -> bootstrap_runtime() 或对应 registry 初始化

运行期资源
  -> 谁创建进程，谁负责关闭
```

```text
这个资源属于哪个进程?

API 独占
  -> FastAPI lifespan

worker 独占
  -> Taskiq worker startup/shutdown

脚本临时使用
  -> 脚本自己的 open/close 或 try/finally

所有进程都要知道的规则
  -> 共享静态初始化模式，不放连接池
```

## 当前实现

当前代码已经把 API 进程的 PostgreSQL async engine 放进 FastAPI `lifespan`：

```text
app/main.py
  create_app()
    bootstrap_runtime()
    FastAPI(..., lifespan=api_lifespan)

  api_lifespan()
    startup:
      init_db_engine()
    shutdown:
      close_db_engine()
```

注意：`create_app()` 会执行静态初始化并构造 FastAPI app，但不会直接进入 `lifespan`。DB engine 是 ASGI server 启动应用并触发 lifespan startup 时创建的。

数据库连接池的创建和关闭集中在 `app/core/database.py`：

```text
init_db_engine()
  -> create_async_engine(...)
  -> async_sessionmaker(...)

close_db_engine()
  -> engine.dispose()
  -> 清空 engine / session_factory

get_db()
  -> 从已初始化的 session_factory 创建 request session
```

这意味着 API 请求里的数据库访问依赖 API 进程已经完成 lifespan startup。如果 engine 未初始化，`get_db_engine()` 和 `get_session_factory()` 会快速失败。

worker 不依赖 FastAPI `lifespan`。Taskiq worker 用自己的事件管理同一类数据库资源：

```text
app/tasks/taskiq_app.py
  WORKER_STARTUP:
    init_db_engine()

  WORKER_SHUTDOWN:
    close_db_engine()
```

这不是重复设计，而是进程边界不同：

```text
API Pod
  FastAPI lifespan
    -> API 自己的 DB engine / session_factory

Worker Pod
  Taskiq worker events
    -> worker 自己的 DB engine / session_factory
```

`app/core/database.py` 是共享模块，不代表共享进程内存。API Pod 和 worker Pod 各自 import 这份代码，各自持有自己的 `_engine`。

worker 执行 Job 时还会通过 `app/tasks/jobs.py` 的 `_ensure_workflows_registered()` 保护性注册和校验 `job_type`、错误码和模型目录。它和 API 的 `bootstrap_runtime()` 属于同一类静态初始化职责，但当前不是同一个函数入口。

当前 worker 内部还同时存在两类 DB 访问：

```text
Taskiq worker startup 初始化的共享 engine
  -> 供依赖 get_session_factory() 的路径使用，例如 AI ledger

run_job_attempt / publish / recovery side effect 中的部分 JobRepo 操作
  -> 使用调用路径自己的 NullPool 临时 engine
  -> 操作结束后 dispose
```

因此文档和代码都不要笼统写成“worker 只用共享 engine”或“worker 只用临时 engine”。准确边界是：worker 不依赖 FastAPI lifespan；worker 需要的长驻资源由 Taskiq worker events 管，局部一次性 DB 操作由调用路径自己关闭。

## bootstrap_runtime 的边界

`bootstrap_runtime()` 负责静态合同初始化。当前它做的事情是：

- 注册所有 `job_type`。
- 冻结错误码 registry。
- 校验模型目录。
- 配置日志。
- 记录应用启动日志。

这些动作适合共享，因为它们不持有需要关闭的外部连接。

当前直接调用 `bootstrap_runtime()` 的入口是 `app/main.py` 的 `create_app()`。worker 侧使用 `_ensure_workflows_registered()` 做相近的 registry 初始化和校验；维护时应保持两侧静态合同一致，但不要把 worker 写成依赖 API 的 `bootstrap_runtime()` 或 FastAPI app。

不要把下面这些运行期资源放进 `bootstrap_runtime()`：

- PostgreSQL engine / connection pool。
- Redis client / broker connection。
- 复用型 HTTP client。
- 长驻模型、索引、缓存句柄。
- 需要显式 close、dispose、aclose 或 shutdown 的资源。

判断标准：

```text
初始化后不需要释放，且 API 进程必须拥有这套规则
  -> 可以放 bootstrap_runtime()

初始化后不需要释放，且 worker 进程也必须拥有这套规则
  -> 同步维护 worker 的静态初始化入口

初始化后占用 socket、连接池、文件句柄或内存，退出时必须释放
  -> 放对应进程 lifecycle
```

## 资源放置规则

| 对象 | 当前建议位置 | 原因 |
| --- | --- | --- |
| `job_type` 注册 | API `bootstrap_runtime()`；worker `_ensure_workflows_registered()` | API 和 worker 都必须认识同一套 Job 合同，不需要关闭 |
| 错误码 registry | API `bootstrap_runtime()`；worker `_ensure_workflows_registered()` | 启动或执行前冻结合同，不占外部连接 |
| 模型目录校验 | API `bootstrap_runtime()`；worker `_ensure_workflows_registered()` | 静态配置校验，失败应阻止入口继续运行 |
| API DB engine | FastAPI `api_lifespan` | API 请求依赖连接池，API shutdown 必须释放 |
| worker DB engine | Taskiq `WORKER_STARTUP` / `WORKER_SHUTDOWN` | worker 不运行 FastAPI app，不能依赖 API lifespan |
| recovery DB session | 每次 recovery 批次自己的 `try/finally` 临时 engine | recovery loop 可以常驻，但单次扫描不复用 API/worker DB pool |
| publish/reconcile 临时 DB 访问 | 当前调用路径自己的 `NullPool` 临时 engine | `publish_job_attempt()` 和 recovery side effect 不复用 API request session |
| Taskiq broker publish/consume | broker 定义在 `app/tasks/taskiq_app.py`，API、worker、recovery 都可能触发 publish；worker events 只管理 worker 进程钩子 | broker 不属于 API lifespan，也不能简单归为 worker 独占资源 |
| 脚本 DB 查询 | 脚本内部 open/close | 脚本进程短生命周期，命令结束即释放 |

## 为什么 worker 不能使用 FastAPI lifespan

FastAPI `lifespan` 是 ASGI app 的生命周期钩子，只有运行 FastAPI app 的进程才会触发。

worker 进程通常启动的是 Taskiq worker，而不是 Uvicorn/FastAPI：

```text
API 进程
  uvicorn app.main:app
    -> FastAPI create_app()
    -> api_lifespan startup
    -> HTTP routes
    -> api_lifespan shutdown
```

```text
worker 进程
  taskiq worker app.tasks.taskiq_app:broker
    -> broker startup events
    -> consume jobs.run_attempt
    -> broker shutdown events
```

所以不能设计成：

```text
FastAPI lifespan 创建 DB engine
  -> worker 直接复用这个 engine
```

worker 根本不会触发 FastAPI `api_lifespan`，也不能跨进程复用 API Pod 内存里的连接池。正确做法是让 worker 在自己的 startup 中创建自己的 DB engine，并在 shutdown 中关闭。

## 脚本和 recovery 的生命周期

脚本和 Pod 内手动命令通常是短生命周期进程。recovery 则分两层理解：`recovery_loop` 可以是常驻进程，但每次 `run_recovery()` 批次都自己创建并释放临时 DB engine。

它们都不应该依赖 API lifespan，也不应该假设可以复用 worker DB pool。

这类入口的资源管理模式应保持简单：

```text
脚本命令开始
  -> 读取当前环境变量 / ENV_FILE
  -> 创建临时 engine 或 client
  -> 执行一次性任务
  -> finally 关闭资源
  -> 命令退出
```

```text
recovery_loop 常驻运行
  -> 周期性调用 run_recovery()
  -> 每个 recovery 批次创建 NullPool 临时 engine
  -> 扫描和修复 outbox / lease / callback
  -> finally dispose 临时 engine
  -> 等待下一轮
```

当前 recovery 批次使用独立临时 engine，并在 `finally` 中 dispose。这符合它的资源边界：recovery loop 可以常驻，但单次扫描不需要保留 API 或 worker 的连接池。

当前 `publish_job_attempt()` 也使用独立的 `NullPool` 临时 engine。即使它由 API 创建 Job 后触发，也不复用 API route 的 request session；即使它由 recovery 触发，也不复用 recovery 扫描阶段的 session。

`scripts/jobs.sh` 这类排障脚本不走 SQLAlchemy async engine，而是脚本内部建立同步只读数据库连接，命令结束后关闭。

## APP_ENV 与 lifespan 没有关联

`APP_ENV` 是配置安全规则开关，不是生命周期选择开关，也不是自动选择 `.env.*` 文件的开关。

```text
APP_ENV=test/prd
  -> 启用发布模式配置校验
  -> 拒绝本地对象存储、redis_list broker、关闭鉴权、占位密钥等不安全配置

APP_ENV 不会决定：
  -> 是否启用 FastAPI lifespan
  -> worker 是否启用 Taskiq startup/shutdown
  -> 是否自动读取 .env.test 或 .env.prd
```

只要 API app 由 ASGI server 启动并进入 lifespan，FastAPI `lifespan` 就是 API 的资源生命周期机制；只要 worker 通过 Taskiq broker 运行，worker 就使用 Taskiq 的 worker 事件。

## 新增资源时怎么判断

新增需要长期持有的资源时，按这个顺序判断：

```text
1. 资源是否需要 close / dispose / aclose / shutdown?
   否 -> 更可能是静态初始化或普通配置
   是 -> 进入下一步

2. 谁使用这个资源?
   只有 API -> API lifespan
   只有 worker -> worker startup/shutdown
   API 和 worker 都用 -> 各自创建、各自关闭，不共享连接池实例
   只有脚本 -> 脚本内部 try/finally

3. 资源是否影响启动安全?
   是 -> startup 阶段 fail-fast
   否 -> 可以按需创建，但必须有清晰关闭路径

4. 资源是否要跨请求复用?
   是 -> 进程级 lifecycle
   否 -> request/task/command 级上下文管理
```

典型例子：

| 资源 | 推荐生命周期 |
| --- | --- |
| API PostgreSQL 连接池 | FastAPI `lifespan` |
| worker PostgreSQL 连接池 | Taskiq worker events |
| API 复用 HTTP client | FastAPI `lifespan` |
| worker 复用 HTTP client | Taskiq worker events |
| 单次 callback HTTP 请求 | 函数内部上下文管理 |
| 对象存储 client | 按实际 SDK 连接语义决定；长连接 client 放对应进程 lifecycle |
| 大模型或索引常驻内存 | 使用它的进程 lifecycle，并明确内存预算和启动失败行为 |

## 修改生命周期代码时的验证

改动 API lifespan、worker startup/shutdown、数据库 engine、Taskiq broker、recovery 或脚本资源管理后，至少运行：

```bash
./scripts/verify.sh check
```

如果改动影响服务启动、Job 执行、Taskiq workflow、Recovery、Callback 或对象存储，还应运行：

```bash
./scripts/dev.sh start
./scripts/verify.sh workflow-smoke
./scripts/dev.sh stop
```

只改文档时，至少检查：

```bash
git diff -- docs/runbooks/lifespan.md docs/README.md
```

## 维护规则

- 本文只写生命周期判断方法和当前维护边界；当前架构总览以 [`../current/architecture.md`](../current/architecture.md) 为准。
- 不把未来想做但尚未落地的 lifecycle 方案写成当前事实。
- 不把 FastAPI `lifespan` 称为“整个项目生命周期”。
- 不把 `APP_ENV` 写成生命周期开关。
- 新增长期资源时，同步检查 API、worker、脚本和 recovery 四类入口，不要只改 API。
