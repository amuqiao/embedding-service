# PostgreSQL 首次部署常见问题排查

本文记录项目首次部署到 K8s 测试环境时，数据库链接失败和表不存在这两个问题的完整排查过程与解决方案。

适用场景：本项目新部署到 K8s、数据库为新建实例、未曾在该环境运行过迁移。

---

## 一、问题全貌

首次部署后，`GET /models`、`GET /prompt-templates` 正常返回，但 `POST /jobs` 报错超时。worker 启动后立刻报 startup recovery 失败。两个问题本质不同，需要分别处理。

```
现象时间线：

部署完成
  → GET 接口正常（不访问数据库）
  → POST /jobs 等待 60 秒后超时报错          ← 问题一：SSL 握手挂住
    → 修复 SSL 后，POST /jobs 立刻报表不存在  ← 问题二：迁移未运行
      → 执行迁移后，服务正常
```

---

## 二、问题一：数据库连接 SSL 握手超时

### 症状

```
POST /api/v1/novel-localization-ai/jobs  duration_ms=60017
TimeoutError
  asyncpg/connect_utils.py, line 969, in _create_ssl_connection
    asyncio.exceptions.CancelledError
```

请求卡满 60 秒后超时，报错来自 asyncpg 的 SSL 连接层。

### 根因

`asyncpg` 默认使用 `sslmode=prefer`：先尝试 SSL 握手，若服务器拒绝再回退明文。K8s 内网的 PostgreSQL（`172.20.x.x`）通常不开启 SSL，但它不会快速拒绝 SSL 握手，而是静默不响应，导致 asyncpg 一直等到 60 秒超时。

GET 接口正常是因为 `/models`（0ms）和 `/prompt-templates`（25ms）均从配置文件读取，不访问数据库。

### 解决方案 A：代码层支持 `DB_SSL` 配置（推荐）

**适用**：需要在不同环境灵活控制是否启用 SSL。

**改动一：`app/infrastructure/config.py`**

```python
DATABASE_URL: str
DB_SSL: bool = True   # 新增，默认 True 保持现有行为
SERVICE_API_KEY: str
```

**改动二：`app/infrastructure/database.py`**

```python
_connect_args = {} if settings.DB_SSL else {"ssl": False}
engine = create_async_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    connect_args=_connect_args,
)
```

**Kuboard 环境变量**（api 和 worker 两个 Deployment 都需要）：

```
DB_SSL = false
```

> **为什么不能只改 `DATABASE_URL`**
>
> SQLAlchemy + asyncpg 组合下，URL 的查询参数会被转换为 `asyncpg.connect()` 的关键字参数，
> 但 asyncpg 的 `ssl` 参数只接受布尔值 `True/False`，URL 里的值永远是字符串，会被解析失败：
>
> | 写法 | asyncpg 收到 | 结果 |
> |---|---|---|
> | `?ssl=false` | 字符串 `"false"` | 尝试当 sslmode 解析，报错 |
> | `?sslmode=disable` | 关键字 sslmode | asyncpg.connect() 无此参数，TypeError |
> | 代码传 `{"ssl": False}` | 布尔 `False` | 正确 |

### 解决方案 B：DATABASE_URL 指向支持 SSL 的数据库实例

**适用**：有条件为 PostgreSQL 开启 SSL，或迁移到已支持 SSL 的托管数据库。

修改 `DATABASE_URL`，指向已开启 SSL 的实例，不需要修改任何代码，默认 `sslmode=prefer` 即可正常工作。

---

## 三、问题二：数据库表不存在

### 症状

```
asyncpg.exceptions.UndefinedTableError: relation "ai_jobs" does not exist
[SQL: SELECT ai_jobs.id, ... FROM ai_jobs WHERE ...]
```

SSL 问题修复后，数据库连接成功，但立刻报表不存在。

### 根因

新建的 PostgreSQL 数据库只有空库，没有任何表结构。Alembic 迁移从未在该数据库上执行过。

---

### 解决方案 A：进容器手动执行迁移

**适用**：快速修复，适合首次或偶发场景。

在 Kuboard 进入 `test-cms-novel-localize`（api 服务）容器 shell，执行：

```bash
cd /mnt && alembic upgrade head
```

预期输出：

```
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Running upgrade  -> 0001_create_ai_jobs, ...
INFO  [alembic.runtime.migration] Running upgrade 0001_create_ai_jobs -> 0002_add_job_workflow, ...
INFO  [alembic.runtime.migration] Running upgrade 0002_add_job_workflow -> 0003_add_expires_at_ttl, ...
```

迁移完成后在 Kuboard 重启 worker，服务即恢复正常。

### 解决方案 B：在 K8s Deployment 加迁移 init container（推荐）

**适用**：每次部署自动完成迁移，避免人工操作，防止漏跑迁移。

在 `api 服务.yaml` 的 `initContainers` 中，在代码复制容器之后、工作容器启动之前加入迁移步骤：

```yaml
initContainers:
  # 第一步：复制代码（已有）
  - name: test-cms-novel-localize
    image: <ci-image>
    command: [sh]
    args: ['-c', 'mv /tmp/* /mnt']
    volumeMounts:
      - mountPath: /mnt
        name: volume-ry5mp

  # 第二步：运行迁移（新增）
  - name: migrate
    image: <os-image>
    command: [sh, '-c']
    args: ['cd /mnt && alembic upgrade head']
    env:
      - name: DATABASE_URL
        value: '<同工作容器的 DATABASE_URL>'
      - name: DB_SSL
        value: 'false'
    volumeMounts:
      - mountPath: /mnt
        name: volume-ry5mp
    workingDir: /mnt
```

`<ci-image>` 和 `<os-image>` 填写当前使用的镜像地址。

> `alembic upgrade head` 是幂等的：已执行过的迁移会跳过，只跑新增的，重复执行无副作用。

---

## 四、新环境部署检查清单

首次在新 K8s 环境部署时，确认以下几项：

- [ ] `DATABASE_URL` 指向正确的数据库实例，数据库已创建
- [ ] 数据库实例不支持 SSL 时，Deployment 已加 `DB_SSL=false`
- [ ] 已执行 `alembic upgrade head` 完成表结构初始化
- [ ] api 和 worker 两个 Deployment 的环境变量保持一致
