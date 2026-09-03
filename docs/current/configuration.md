# 配置治理

本文记录当前服务已经落地的配置事实。它只说明运行时配置如何进入应用、哪些 key 可以写入 env 文件、哪些 key 必须被拒绝，以及新增配置时应落到哪个层。

## 配置目标

配置项只表达稳定控制意图，不暴露底层派生值。API、Taskiq worker、dispatcher、callbacker、reconciler、Alembic 和本地脚本使用同一套根目录 env 文件语义。

```text
显式进程环境变量
  -> ENV_FILE 指定的 env 文件
  -> 根目录 .env
  -> Settings / 代码默认值
```

`APP_ENV` 只参与配置安全校验，不选择运行形态，也不会自动加载 `.env.test` 或 `.env.prd`。切换 env 文件必须显式设置 `ENV_FILE`，或由运行平台直接注入环境变量。

## 配置文件职责

| 文件或入口 | 当前职责 |
|---|---|
| `.env.example` | 可提交配置模板，包含当前允许写入根 env 文件的 application key、launcher key 和 POC key |
| `.env` | 本地私有实例配置，不提交，可以省略可选 key 并使用代码默认值 |
| `ENV_FILE` | 进程级选择器，只允许作为环境变量或脚本参数传入，不写进 `.env.example` 或 `.env` |
| `app/core/config.py` | application env key、launcher env key、derived key、deprecated key、Settings 字段映射和启动校验事实源 |
| `scripts/verify/env_config_check.py` | env 文件机器检查入口，从 `app/core/config.py` 读取 key manifest |
| `app/ai/catalog/models.yaml` | 全局模型启停、默认模型、capability route 和 `/models` 公开投影 |
| `app/ai/pricing/pricing.yaml` | 模型成本估算规则 |
| `app/core/prompts.yaml` 和 `app/business_packages/*/prompts.yaml` | 共享 Prompt 和业务包 Prompt |

`.env.example` 当前没有拆成多个模板文件。这样本地 `./scripts/run.sh up dev`、`docker compose` 和 smoke 都能共享一份 `.env`。代价是同一个文件内会同时出现 application key 和 launcher key，因此新增 key 时必须先判定分类。

## Key 分类

### Application Key

Application key 会进入 `Settings`，由 `APPLICATION_ENV_FIELD_MAP` 映射到嵌套子对象。典型例子：

```text
APP_ENV
SERVICE_API_PREFIX
DATABASE_URL
DB_SSL
DB_POOL_SIZE
DB_MAX_OVERFLOW
SERVICE_API_KEY
REDIS_URL
TASKIQ_BROKER_KIND
CALLBACK_SIGNING_SECRET
STORAGE_BACKEND
OPENAI_API_KEY
DASHSCOPE_API_KEY
MODEL_CONFIG_PATH
PRICING_CONFIG_PATH
PROMPT_CONFIG_PATH
ENABLED_BUSINESS_PACKAGES
ASSET_IMAGE_TAGGING_MODEL_ADAPTER
ASSET_IMAGE_TAGGING_MODEL_ID
ASSET_IMAGE_TAGGING_MAX_ITEMS
ASSET_VECTOR_DASHSCOPE_API_KEY
ASSET_VECTOR_DASHSCOPE_BASE_URL
ASSET_VECTOR_EMBEDDING_MODEL
ASSET_VECTOR_EMBEDDING_DIMENSION
ASSET_VECTOR_MAX_ITEMS
ASSET_VECTOR_DELETE_MAX_ITEMS
ASSET_VECTOR_SEARCH_DEFAULT_TOP_K
ASSET_VECTOR_SEARCH_MAX_TOP_K
MODEL_CALL_TIMEOUT_SECONDS
MAX_ACTIVE_JOBS
CALLBACK_TIMEOUT_SECONDS
LOG_LEVEL
```

新增 application key 必须同时满足：

- 在 `APPLICATION_ENV_FIELD_MAP` 中声明映射。
- 在对应 `Settings` 子对象中有类型、默认值或必填约束。
- 在启动校验中覆盖非法值或跨字段不变量。
- 在 `.env.example` 中给出占位值或默认值。
- 被 `./scripts/verify.sh env-config` 和相关测试覆盖。

`ASSET_IMAGE_TAGGING_MODEL_ADAPTER` 是 `asset_image_tagging` 业务包自己的模型 adapter 选择项，当前只支持
`openai_responses`。`ASSET_IMAGE_TAGGING_MODEL_ID` 是该 adapter 调用的视觉理解模型 ID，默认对齐当前模型目录中的
OpenAI 多模态文本模型。图片打标使用 `OPENAI_API_KEY` / `OPENAI_BASE_URL`，不使用 `DASHSCOPE_API_KEY` /
`DASHSCOPE_BASE_URL`。`ASSET_IMAGE_TAGGING_MAX_ITEMS` 控制单 Job 外部批量 items 数量上限，默认 `10`，超限
在创建 Job 阶段返回业务错误。真实执行使用平台 workflow 按 item 拆分 child job；每个 child job 单独调用一次
模型，并使用 OpenAI Responses `text.format=json_schema` 约束结构化输出。

`asset_vector` 使用 DashScope 原生多模态向量接口。默认读取全局 `DASHSCOPE_API_KEY` / `DASHSCOPE_BASE_URL`；
如需业务隔离，可用 `ASSET_VECTOR_DASHSCOPE_API_KEY` / `ASSET_VECTOR_DASHSCOPE_BASE_URL` 覆盖。`asset_vector`
内部会把以 `/compatible-mode/v1` 结尾的 DashScope base URL 规范化为 `/api/v1` 后调用原生
`/services/embeddings/multimodal-embedding/multimodal-embedding`。`ASSET_VECTOR_EMBEDDING_MODEL` 默认
`tongyi-embedding-vision-flash`，`ASSET_VECTOR_EMBEDDING_DIMENSION` 当前固定为 `768`，必须与
`asset_vector_items.embedding vector(768)` 保持一致。`ASSET_VECTOR_MAX_ITEMS` 控制批量新增/更新资源上限；
`ASSET_VECTOR_DELETE_MAX_ITEMS` 控制批量删除资源上限；`ASSET_VECTOR_SEARCH_DEFAULT_TOP_K` 和
`ASSET_VECTOR_SEARCH_MAX_TOP_K` 控制同步搜索返回数量。

### Launcher Key

Launcher key 不进入 `Settings`，只影响本地脚本、docker compose、端口映射或进程参数。当前允许写入根 env 文件的 launcher key 由 `LAUNCHER_ENV_KEYS` 声明：

```text
API_HOST
API_PORT
API_HOST_PORT
COMPOSE_PROJECT_NAME
POSTGRES_DB
POSTGRES_HOST_PORT
REDIS_HOST_PORT
WORKER_PROCESSES
WORKER_MAX_ASYNC_TASKS
WORKER_MAX_PREFETCH
WORKER_LOGLEVEL
```

这些 key 可以留在 `.env.example`，因为它们是本项目本地运行形态的一部分；但应用代码不能从 `Settings` 读取它们。新增 launcher key 必须有明确脚本或 compose 消费方，不能作为业务配置变相进入应用。

### POC Key

POC key 不进入 `Settings`，只给 `poc/` 下的独立验证脚本读取。当前允许写入根 env 文件的 POC key 由 `POC_ENV_KEYS` 声明：

```text
POC_DASHSCOPE_BASE_URL
```

`POC_DASHSCOPE_BASE_URL` 用于 `poc/asset-vector` 这类直接调用 DashScope 原生 `api/v1` 的脚本。服务自身的 DashScope provider 仍使用 `DASHSCOPE_BASE_URL`，应用代码不能通过 `Settings` 读取 POC key。

### Process-Only Key

Process-only key 只允许通过命令行环境变量传入，不允许写入 `.env` 或 `.env.example`：

```text
ENV_FILE
APP_CONFIG_SKIP_DEFAULT_ENV_FILE
API_URL
PYTHON_BIN
```

这类 key 控制脚本或配置加载过程本身，不是服务实例的业务配置。写入 env 文件会增加解释成本，后续排障也难判断到底是文件配置还是进程覆盖。

### Derived Key

Derived key 是代码根据主控配置计算出来的结果，禁止写入 env 文件。当前禁用清单由 `DERIVED_ENV_KEYS` 声明：

```text
WORKER_SOFT_TIME_LIMIT
WORKER_HARD_TIME_LIMIT
JOB_STALE_RUNNING_SECONDS
CALLBACK_DELIVERY_TIMEOUT_SECONDS
SYNC_DATABASE_URL
OSS_ENDPOINT_STYLE
OSS_SCHEME
```

这些值存在硬性联动关系。例如 `MODEL_CALL_TIMEOUT_SECONDS` 会派生 worker soft timeout、worker hard timeout 和 stale running threshold；`CALLBACK_TIMEOUT_SECONDS` 会派生 callback claim window。让这些结果被 env 单独覆盖，会破坏状态机顺序。

### Deprecated Key

Deprecated key 是已经移除或明确不支持的旧配置。当前禁用清单由 `DEPRECATED_ENV_KEYS` 声明。它们不是兼容入口，出现在 `.env`、`.env.example` 或选定 env 文件中都会被 `env-config` 拒绝。

保留禁用清单的目的不是兼容旧行为，而是让错误快速暴露，避免旧模板残留 key 被静默忽略。

## Settings 结构

`Settings` 使用 Pydantic `BaseSettings`，配置对象被冻结，未知字段禁止进入。当前子对象：

| 子对象 | 职责 |
|---|---|
| `runtime` | `APP_ENV` 和 release 环境判断 |
| `service` | 服务名、标题和 API prefix |
| `database` | PostgreSQL URL、SSL 和连接池容量 |
| `broker` | Redis URL 和 Taskiq broker 类型 |
| `security` | 服务鉴权、caller header 和 CORS |
| `storage` | local / Aliyun OSS 对象存储 |
| `callback` | Callback 签名、单次请求超时和投递内部参数 |
| `ai_provider` | OpenAI / DashScope 凭证、base URL 和模型调用主 timeout |
| `registry` | 模型配置和 Prompt 配置路径 |
| `billing` | pricing 配置路径和 billing 开关 |
| `ops_dashboard` | 内部 dashboard 开关、窗口和超时 |
| `job` | Job 容量、启用 root job_type 和业务 job 子配置 |
| `observability` | 日志等级 |

应用代码应读取同一个 `settings` 对象或显式注入 Settings，不在业务路径重新解析 `.env`，也不直接从 `os.environ` 读取 application key。

### OSS 配置语义

`.env` 只提供默认 OSS 连接与命名空间事实；job type 的 `storage_policy.py` 提供可选 OSS 业务策略，job adapter 负责合成 `app/object_storage` 的 config/spec/payload。

`OSS_BUCKET`、`OSS_REGION`、`OSS_ACCESS_KEY_ID`、`OSS_ACCESS_KEY_SECRET` 和 `OSS_PROJECT_ROOT` 是 `aliyun_oss` 后端的必需输入；`OSS_OUTPUT_PREFIX` 是 Job runtime 的输出前缀；`OSS_PUBLIC_ENDPOINT` 只用于对外 `public_url` 投影。

job type `storage_policy.py` 的 OSS 策略按字段生效：未显式配置的字段继承默认 OSS 配置，已显式配置的字段以 job type policy 为准。

`OSS_ENDPOINT` 是服务端读写 OSS 的高级 API endpoint 覆盖项，通常留空。留空时由 `OSS_REGION` 派生为 `oss-<region>.aliyuncs.com`；配置 `OSS_PUBLIC_ENDPOINT` 不会改变服务端 OSS API endpoint。只有服务端读写也必须走自定义域名或代理时，才同时配置 `OSS_ENDPOINT`。

## 启动校验

配置错误必须在启动或配置加载阶段失败。当前校验覆盖：

- unknown / lowercase / derived / deprecated env key。
- `APP_ENV` 只能是 `local`、`dev`、`test`、`prd`。
- `test` / `prd` 禁止关闭 HTTP 鉴权和 caller header。
- `test` / `prd` 禁止 insecure callback。
- `test` / `prd` 在启用对象存储类 job 时禁止 `STORAGE_BACKEND=local`。
- `test` / `prd` 必须提供非占位、长度足够的 `SERVICE_API_KEY` 和 `CALLBACK_SIGNING_SECRET`。
- 本地免鉴权时，`DATABASE_URL` 和 `REDIS_URL` 必须指向 loopback。
- callback claim window 必须小于 retry delay。
- worker hard timeout 必须大于 soft timeout，stale running threshold 必须大于 hard timeout。
- model、prompt、pricing 配置文件必须存在。
- `models.yaml` 中默认模型必须引用 enabled model。

## 验证入口

只检查 env 文件 key：

```bash
./scripts/verify.sh env-config
```

验证指定 env 文件在发布模式下是否能启动：

```bash
./scripts/verify.sh env-config --env-file .env.test --app-env test
```

完整质量门会包含 env-config：

```bash
./scripts/verify.sh check
```

`env-config` 默认扫描根目录 `.env*` 文件。`.env` 可以少于 `.env.example`，因为可选配置允许走代码默认值；但 `.env` 不能出现 `.env.example` 之外的 root env key，也不能出现 derived / deprecated key。

## 新增配置规则

新增配置先判断它属于哪一类：

| 需求 | 放置位置 |
|---|---|
| 部署环境、依赖地址、密钥、安全开关、业务主控容量 | `Settings` application key + `.env.example` |
| 本地端口、compose 项目名、worker CLI 参数 | launcher key + `.env.example` |
| `poc/` 独立验证脚本专用配置 | POC key + `.env.example`，不进入 `Settings` |
| 模型启停、默认模型、provider route、公开模型投影 | `app/ai/catalog/models.yaml` |
| 模型价格、usage 到成本估算规则 | `app/ai/pricing/pricing.yaml` |
| 业务 Job 自己的模型范围或 prompt | `app/business_packages/<job_type>/models.yaml` 或 `prompts.yaml` |
| 由其他配置计算得到的 timeout、endpoint、内部窗口 | 代码 property / constant，加入 derived 禁用清单 |
| 已删除或不再支持的旧 key | 加入 deprecated 禁用清单 |

不要新增 silent fallback，不要让旧 key 自动映射到新 key。需要迁移时，应明确删除旧 key，并通过 `env-config` 报错推动修复。
