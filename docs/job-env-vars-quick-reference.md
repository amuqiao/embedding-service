# Job 关键环境变量配置速查

本文用于快速定位 Job 吞吐、排队、超时、对象存储和横向扩容相关配置，重点区分 `.env`、`.env.dev`、`.env.test` 的使用边界。

## 适用边界

本文负责回答三个问题：

- 排查 Job 问题时先看哪些环境变量。
- 修改 `.env`、`.env.dev`、`.env.test` 时分别影响什么运行形态。
- 横向扩容或控制吞吐量时，哪些变量必须一起调整。

本文不替代完整部署说明。部署模式、脚本入口和验证流程仍以 `README.md`、`docs/部署与发布手册.md` 和 `docs/job-implementation-guide.md` 为准。

## 配置文件职责

| 文件 | 职责 | 典型使用方式 | 注意事项 |
|---|---|---|---|
| `.env.example` | 可提交模板，记录稳定默认值和注释 | `./scripts/dev.sh bootstrap` 缺少 `.env` 时复制它 | 不写真实密钥、真实远程连接串 |
| `.env` | 默认本地运行配置；`Settings` 默认读取它 | `./scripts/dev.sh start`、宿主机 API/worker、本地 smoke/e2e | `dev.sh` 生命周期命令会拒绝明显非本地的 `DATABASE_URL` / `REDIS_URL` |
| `.env.dev` | 开发联调配置，可指向开发 OSS、真实模型或指定代理 | `ENV_FILE=.env.dev ./scripts/deploy.sh up compose-full`；部分检查脚本支持 `--env-file .env.dev` | 不会被 `dev.sh` 自动读取；宿主机进程要使用它时需显式注入环境变量 |
| `.env.test` | 测试环境或压测倾向配置，可使用远程 DB/Redis、更长超时和更高并发 | `ENV_FILE=.env.test ./scripts/deploy.sh up compose-full`，或部署平台注入同名变量 | 不应直接复制成 `.env` 后运行 `dev.sh start`，否则可能绕过本地保护边界 |

配置加载优先级：

```text
运行时显式环境变量
> docker-compose.yml environment
> ENV_FILE 指定的 env 文件
> .env
> 应用默认值
```

重要影响：

- `docker-compose.yml environment` 会覆盖 `ENV_FILE` 中的同名变量。
- 当前 `compose-full` 的 API/worker 都通过 `/app/start-api.sh` 和 `/app/start-worker.sh` 启动；worker 并发由 `ENV_FILE` 或运行时显式环境变量中的 `WORKER_POOL` / `WORKER_CONCURRENCY` 控制。
- `start-worker.sh` 只有在 `WORKER_POOL` 不是 `solo` 且设置了 `WORKER_CONCURRENCY` 时，才会把并发参数传给 Celery。

## 三个 env 文件的定位

| 维度 | `.env` | `.env.dev` | `.env.test` |
|---|---|---|---|
| 运行目标 | 本地默认服务栈 | 开发联调 | 测试环境、压测或类生产验证 |
| DB/Redis | 应保持本地地址 | 通常保持本地 DB/Redis，便于 `dev.sh` 管理 | 可指向远程测试 DB/Redis |
| 对象存储 | 默认可用 `local` | 可切到 `aliyun_oss` 验证真实 OSS | 通常使用测试 OSS bucket/prefix |
| 模型调用 | 可为空或真实 key | 真实模型联调 | 真实模型、长文本验证 |
| 吞吐策略 | 保守、单 worker | 保守，优先排障 | 可放开积压和并发，但必须同步检查 DB/Redis/模型配额 |
| 安全要求 | 不提交 | 不提交 | 不提交 |

## 吞吐量旋钮

Job 吞吐由三层共同决定：

```text
接单上限：MAX_ACTIVE_JOBS
执行槽位：Worker 实例数 × WORKER_CONCURRENCY
单 Job 时长：模型响应时间、派生超时链、分块配置、重试次数
```

### 接单上限

| 变量 | 默认 | 作用 | 调整建议 |
|---|---:|---|---|
| `MAX_ACTIVE_JOBS` | `5000` | `queued + running` 总数达到上限时，`POST /jobs` 返回 503 | 生产初始目标为 5000 排队；`0` 表示禁用检查 |

`MAX_ACTIVE_JOBS` 控制的是是否继续接单，不控制同时执行的 Job 数。即使设为 `0`，实际并发仍由 worker 槽位限制。

### Worker 执行槽位

| 变量 | 默认 | 作用 | 调整建议 |
|---|---:|---|---|
| `WORKER_POOL` | `solo` | Celery worker pool 模式 | 本地排障用 `solo`；并发执行需改为 `threads` |
| `WORKER_CONCURRENCY` | 未启用或 `1` | 单个 worker 同时执行的任务数 | 仅 `WORKER_POOL=threads` 时生效；从 `2~4` 起步，再按资源和模型配额增加 |
| `WORKER_MAX_TASKS_PER_CHILD` | 未设置 | 子进程最大任务数 | 仅适合非 `solo` 且需要进程回收的场景 |
| `WORKER_LOGLEVEL` | `info` | worker 日志级别 | 排障时临时设为 `debug` |

计算：

```text
总执行槽位 = Worker 实例数 × WORKER_CONCURRENCY
```

横向扩容优先增加 Worker 实例数，其次再提高单实例 `WORKER_CONCURRENCY`。提高单实例并发前，应确认内存、DB 连接、Redis 和模型 API 并发额度都足够。

### DB 连接预算

| 变量 | 默认 | 作用 | 调整建议 |
|---|---:|---|---|
| `DB_POOL_SIZE` | `5` | API 侧 SQLAlchemy 连接池常驻连接数 | API Pod 增多时同步核算 |
| `DB_MAX_OVERFLOW` | `10` | API 侧连接池临时溢出连接数 | 不要简单随 Pod 数线性放大 |
| `DB_POOL_RECYCLE` | `1800` | 空闲连接回收秒数 | 网络设备有 idle timeout 时保留 |
| `DB_SSL` | `true` | DB 连接是否启用 SSL | 本地或特定内网库不支持 SSL 时设为 `false` |

粗略预算：

```text
峰值 DB 连接
≈ API 实例数 × (DB_POOL_SIZE + DB_MAX_OVERFLOW)
  + Worker 实例数 × WORKER_CONCURRENCY
  + 迁移 / recovery / 运维余量
```

Worker 侧每个任务使用 `NullPool` 创建短连接，不长期持有连接池，但高并发时仍会形成并发连接压力。

## 超时与重试

超时链由一个锚点和三个 buffer 派生：

```text
MODEL_CALL_TIMEOUT_SECONDS
  + CELERY_SOFT_TIMEOUT_BUFFER_SECONDS
  = celery_soft_time_limit

celery_soft_time_limit
  + CELERY_HARD_TIMEOUT_BUFFER_SECONDS
  = celery_time_limit

celery_time_limit
  + JOB_STALE_RUNNING_BUFFER_SECONDS
  = job_stale_running_seconds
```

推荐 buffer：

```text
CELERY_SOFT_TIMEOUT_BUFFER_SECONDS >= 300
CELERY_HARD_TIMEOUT_BUFFER_SECONDS >= 60
JOB_STALE_RUNNING_BUFFER_SECONDS >= 600
```

| 变量 | 默认 | 作用 | 排障方向 |
|---|---:|---|---|
| `MODEL_CALL_TIMEOUT_SECONDS` | `300` 或 env 中覆盖 | L1 主超时，`asyncio.wait_for` 截断模型调用 | 模型慢、长文本超时时优先调它 |
| `CELERY_SOFT_TIMEOUT_BUFFER_SECONDS` | `300` | L3 软超时相对 L1 的 buffer | 低于推荐值可能影响 L1 后状态写入和 callback |
| `CELERY_HARD_TIMEOUT_BUFFER_SECONDS` | `60` | L4 硬超时相对 L3 的 buffer | 低于推荐值可能导致 soft-limit 来不及收尾 |
| `JOB_STALE_RUNNING_BUFFER_SECONDS` | `600` | L5 stale 扫描相对 L4 的 buffer | 过小会误杀刚被 SIGKILL 的任务 |
| `CELERY_MAX_RETRIES` | `0` | L1/L3 超时后的 Celery 重试次数 | 模型临时过载可设大；输入过长不建议靠重试解决 |
| `CELERY_RETRY_DELAY` | `60` | 重试等待秒数 | 仅 `CELERY_MAX_RETRIES > 0` 有意义 |
| `CELERY_RESULT_EXPIRES` | `86400` | Celery result backend 保留时间 | 通常无需按吞吐调大 |

调用方最大等待时间粗略估算：

```text
(MODEL_CALL_TIMEOUT_SECONDS + CELERY_RETRY_DELAY) × (CELERY_MAX_RETRIES + 1)
```

## 分块与单 Job 时长

| 变量 | 默认 | 作用 | 调整建议 |
|---|---:|---|---|
| `NOVEL_LOCALIZATION_CHUNKING_ENABLED` | `false` | 是否启用 chunked workflow | 默认关闭；开启前先确认成本、恢复逻辑和输出契约 |
| `NOVEL_LOCALIZATION_SINGLE_MAX_CHARS` | `20000` | 不分块时的字符阈值 | 长文本频繁超时时，可降低阈值并启用分块 |
| `NOVEL_LOCALIZATION_CHUNK_SIZE` | `3000` | 分块目标字符数 | 越小单次模型更快，但 chunk 数、成本和 merge 压力上升 |
| `OSS_INPUT_MAX_BYTES` | `5242880` | 输入对象最大字节数 | 超限返回 `INPUT_TOO_LARGE` |

控制吞吐时不要只看并发数。单 Job 输入越长、重试越多、分块越细，模型调用次数和 callback 延迟都会上升。

## 存储、Callback 与模型

| 变量 | 作用 | 常见问题 |
|---|---|---|
| `DATABASE_URL` | API、worker、Alembic 使用的 PostgreSQL 地址 | `.env` 中应保持本地地址；远程测试库放 `.env.test` 或部署平台 |
| `REDIS_URL` | Celery broker / result backend | 高吞吐环境应确认 Redis 持久化和连接上限 |
| `SERVICE_API_KEY` | 受保护 API 的 Bearer Token | smoke/e2e 和调用方必须一致 |
| `STORAGE_BACKEND` | `local` 或 `aliyun_oss` | OSS 问题先确认后端是否切到 `aliyun_oss` |
| `LOCAL_OBJECT_STORAGE_PATH` | 本地对象存储目录 | compose-full 中会被覆盖为容器内路径 |
| `OSS_BUCKET` / `OSS_REGION` / `OSS_PROJECT_ROOT` | OSS 读写范围 | 测试环境必须使用隔离 bucket 或 prefix |
| `OSS_ACCESS_KEY_ID` / `OSS_ACCESS_KEY_SECRET` | OSS 凭据 | 只写私有 env 或部署平台 secret |
| `CALLBACK_SIGNING_SECRET` | Callback HMAC 签名密钥 | 调用方验签失败时优先核对 |
| `ALLOW_INSECURE_CALLBACKS` | 是否允许本地 HTTP callback | 非本地环境应关闭 |
| `CALLBACK_TIMEOUT_SECONDS` | 单次 callback HTTP 超时 | Callback 接收端慢时调大 |
| `CALLBACK_DELIVERY_WINDOW_BUFFER_SECONDS` | Callback 领取窗口 buffer | 代码派生 `callback_delivery_timeout_seconds`，避免和单次超时冲突 |
| `OPENAI_API_KEY` | 模型调用密钥 | 不提交；不同 env 文件使用各自账号或项目 key |
| `OPENAI_BASE_URL` | OpenAI 兼容网关地址 | 代理或私有网关排障时重点核对 |
| `DEFAULT_MODEL_ID` | 默认模型 ID | 必须在 `/models` 启用列表中 |
| `MODEL_CALL_MAX_RETRIES` | 模型 SDK 内部重试次数 | 默认 `0`，避免重复费用；和 Celery 重试不是同一层 |

## 横向扩容检查清单

1. 确认模型 API 并发额度和速率限制。
2. 确认 PostgreSQL 可用连接数，按 API 池和 Worker 槽位核算。
3. 确认 Redis 连接数、内存、持久化策略和队列堆积监控。
4. 选择 Worker 实例数和 `WORKER_CONCURRENCY`，得到总执行槽位。
5. 设置 `MAX_ACTIVE_JOBS >= 总执行槽位 × 2`；内部系统能承受无限排队时才设为 `0`。
6. 长文本场景优先调整 `MODEL_CALL_TIMEOUT_SECONDS`；如需更大收尾窗口，再调整 `CELERY_SOFT_TIMEOUT_BUFFER_SECONDS`、`CELERY_HARD_TIMEOUT_BUFFER_SECONDS`、`JOB_STALE_RUNNING_BUFFER_SECONDS`。
7. 如果启用 `CELERY_MAX_RETRIES`，同步评估调用方等待时间和模型费用。
8. 确认 `JOB_ORPHAN_TIMEOUT_SECONDS` 不会在长队列或慢投递场景下过早触发恢复扫描。
9. compose-full 下确认 `docker-compose.yml environment` 是否覆盖了目标 env 文件中的 worker 并发配置。
10. 修改后至少运行 `./scripts/dev.sh check`；修改部署或 compose 配置时运行 `./scripts/deploy.sh check`。

## 常见现象速查

| 现象 | 优先检查 |
|---|---|
| `POST /jobs` 返回 503 / `QUEUE_FULL` | `MAX_ACTIVE_JOBS`、queued+running 数量、Worker 是否有足够槽位 |
| Job 长期 queued | Worker 是否运行、Redis 是否可达、`WORKER_POOL` / `WORKER_CONCURRENCY` 是否符合预期 |
| Job 长期 running | Worker 日志、模型调用耗时、派生超时链、`JOB_STALE_RUNNING_BUFFER_SECONDS` |
| 改了 `.env.test` 并发但 compose 无变化 | 是否通过 `ENV_FILE=.env.test` 启动；`WORKER_POOL` 是否仍为 `solo` |
| 模型频繁超时 | 输入长度、`MODEL_CALL_TIMEOUT_SECONDS`、分块配置、模型网关延迟 |
| 重试后费用明显增加 | `CELERY_MAX_RETRIES`、`MODEL_CALL_MAX_RETRIES`、输入是否必然超时 |
| Callback 验签失败 | `CALLBACK_SIGNING_SECRET`、调用方验签算法、是否用了错误 env 文件 |
| OSS 找不到对象或写入失败 | `STORAGE_BACKEND`、`OSS_BUCKET`、`OSS_PROJECT_ROOT`、AK 权限、endpoint 风格 |
| 本地脚本拒绝启动 | `.env` 中 `DATABASE_URL` / `REDIS_URL` 是否指向远程地址 |
