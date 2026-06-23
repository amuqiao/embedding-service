# AI Job 服务规范先行重构计划

本文定义后续重构的实施顺序：以 [AI Job 服务项目规范与骨架（代码事实版）](project-standards-code-facts.md) 为当前标准，先明确项目规范和骨架，再让接口、Job、Workflow、配置、日志、异常和 ORM 逐步适配标准。

## 文档职责

本文负责回答：

- 重构为什么改为规范先行。
- 哪些规范先定义，哪些代码后适配。
- 每个阶段的目标、禁止事项和验证入口。
- 如何覆盖 `claude_blueprint/rules/backend` 的标准。

本文不负责重复定义所有规范细节。规范细节见 [AI Job 服务项目规范与骨架（代码事实版）](project-standards-code-facts.md)。

## 核心原则

`project-standards-code-facts.md` 是接口、Job、Callback、错误码、日志、配置和 ORM 的当前合同事实源。代码、测试、OpenAPI、README、mock fixture 和对接文档都必须向该标准收敛。

实施顺序：

```text
定义项目规范
  -> 建立目标骨架和公共真源落点
  -> 建立 schema / error / operation / job_type 注册真源
  -> 建立配置 / 日志 / 安全 / 时间 / metrics / DB 基础模块
  -> 改造接口输入输出为标准 envelope
  -> 改造 Job / Workflow schema
  -> 收口 ORM、Repository、Job lifecycle 和 Integration 副作用
  -> 补齐 contract / OpenAPI / 日志 / 配置 / DB 验证
```

执行原则：

- 不维护两套输入输出合同。
- 不通过适配器或参数开关保留非标准接口形态。
- 不让 README、mock fixture、OpenAPI 示例或对接文档成为第二套合同。
- 不用默认值吞掉非法输入、未知字段或废弃配置。
- 不写入任何低于 `project-standards-code-facts.md` 的实现目标。

## 规范真源

本轮重构先建立这些真源：

| 真源 | 目标文件或模块 |
|---|---|
| 项目规范与骨架（代码事实版） | `docs/架构/project-standards-code-facts.md` |
| HTTP envelope / ErrorDetail / JobResponseData / JobEnvelope / CallbackEnvelope | `app/schemas/envelope.py`、`app/schemas/errors.py`、`app/schemas/jobs.py`、`app/schemas/callbacks.py` |
| 错误码注册表 | `app/core/error_registry.py` 或等价结构 |
| operation registry | `app/core/operation_registry.py` 或等价结构 |
| 当前 schema registry | `app/core/schema_registry.py` 或等价结构 |
| `job_type` registry | `app/jobs/registry.py` 或从 `app/core/workflow_registry.py` 迁移 |
| 配置子对象 | `app/core/settings.py` |
| 日志与 request context | `app/core/logging.py`、`app/core/time.py` |
| 异常和 handler | `app/core/exceptions.py`、`app/api/exception_handlers.py` |
| ORM / session | `app/db/base.py`、`app/db/session.py`、`app/db/models/` |
| Repository / Job lifecycle | `app/repositories/`、`app/jobs/` |

## 阶段 0：规范定稿

目标：先定义标准，不先改业务实现。

范围：

- 定稿 [AI Job 服务项目规范与骨架（代码事实版）](project-standards-code-facts.md)。
- 明确 HTTP 标准响应：`ResponseEnvelope[TData]`。
- 明确错误响应：`ErrorDetail` + 错误码注册表。
- 明确 Job 创建和查询：`ResponseEnvelope[JobResponseData[JobResult]]`，Job 字段只放在 `data.job`。
- 明确 Callback：`CallbackEnvelope[JobEnvelope[JobResult]]`，不套 HTTP envelope。
- 明确 request schema 默认拒绝未知字段。
- 明确 Settings 子对象、日志字段、ORM / Repository 边界。

禁止：

- 写入任何弱化 `project-standards-code-facts.md` 的例外条款。
- 为非标准响应、非标准错误结构或非标准 Job / Callback envelope 预留实现入口。
- 让接口文档、README 示例、OpenAPI 示例或 mock fixture 反向定义合同。

验证：

- 文档自检：规范文档、重构计划、文档地图之间不冲突。
- `git diff --check`

## 阶段 1：目标骨架与公共真源落点

目标：先建立目录骨架和公共真源落点，不急着迁移业务语义。

范围：

- 建立 `api/router.py`、`api/dependencies.py`、`api/exception_handlers.py`。
- 建立 `schemas/` 公共 schema 落点。
- 建立 `core/` 配置、日志、metrics、time、security 落点。
- 建立 `db/base.py`、`db/session.py`、`db/models/`。
- 建立 `repositories/`。
- 建立 `jobs/registry.py`、`jobs/lifecycle.py`、`jobs/publisher.py`、`jobs/recovery.py`。
- 明确 `services/`、`jobs/`、`workflows/`、`integrations/` 的职责边界。

禁止：

- 目录搬迁时顺手改业务语义。
- 保留两个同名职责模块长期并存。
- Repository 反向依赖 API、tasks 或 workflows。
- Workflow 复制通用 Job route、Job 表或 callback envelope。

验证：

- import / app bootstrap 测试。
- 依赖方向检查或静态导入检查。
- 公共 schema、registry、settings、db、repository 模块路径存在且可导入。

## 阶段 2：公共 Schema、异常和注册表

目标：建立统一输入输出、异常模块和可检查注册表。

范围：

- 新增 `ResponseEnvelope[TData]`。
- 新增 `ErrorDetail`。
- 新增或重构 `JobResponseData[TJobResult]`、`JobEnvelope[TJobResult]`、`CallbackEnvelope[TJob]`。
- 新增错误码注册表。
- 新增 operation registry。
- 新增当前 schema registry。
- 新增或迁移 `job_type` registry。
- 收口 `AppError`、`ValidationAppError`、`AuthAppError`、`NotFoundAppError`、`ConflictAppError`、`DependencyAppError`、`InternalAppError`。

禁止：

- route 临时拼 envelope。
- route 返回裸错误对象。
- 对外 details 暴露堆栈、密钥、完整供应商响应、完整 Prompt 或隐私文本。
- 注册表只写在 Markdown，不能被测试消费。

验证：

- 全局异常转换测试：Pydantic 校验错误、业务错误、认证错误、404、未知异常。
- Contract tests 覆盖成功 envelope 和错误 envelope。
- OpenAPI/schema 快照不出现重复 envelope 或裸错误对象。
- Registry consistency suite：route inventory、operation registry、error registry、当前 schema registry 和 `job_type` registry 互相能反查。

## 阶段 3：配置、日志、安全、时间、Metrics 与 DB 基础模块

目标：让公共运行时模块符合 blueprint 基线，并在接口适配前可复用。

范围：

- 将 Settings 拆为 `DatabaseSettings`、`BrokerSettings`、`JobSettings`、`AIProviderSettings`、`StorageSettings`、`CallbackSettings`、`SecuritySettings`、`ObservabilitySettings`。
- 标注 `env-driven`、`tunable constants`、`derived`。
- 明确 `.env.example` 单向真源、废弃 key 拒绝清单、允许清单。
- 统一 `request_id`、`trigger_request_id`、`trace_id`、`operation_id`、`caller_id`、`job_id` 日志字段。
- 定义安全边界、401/403 语义、caller_id 来源、对象存储引用访问边界和 callback 签名边界。
- 统一 `server_time`、业务时间、耗时字段单位。
- 定义最小 metrics 和高基数标签禁区。
- 建立 DB session factory / Unit of Work 基础语义，但不在本阶段大规模迁移业务查询。

禁止：

- 业务代码直接读 `os.environ`。
- Service / Repository 重新构造 Settings。
- 派生字段进入 `.env.example`。
- 日志记录完整请求体、完整响应体、密钥、Prompt 或模型输出。
- API 启动时隐式 `create_tables()`。

验证：

- Settings 初始化、非法配置、敏感字段保护测试。
- deprecated / unknown env key 失败矩阵。
- API / Worker / Recovery 共享同一配置语义测试。
- env key 机器检查。
- 日志字段测试。
- 日志负向测试：非法 `X-Request-ID` 重生；请求体、响应体、Prompt、供应商原文和密钥不被默认记录。
- metrics 最小字段和高基数标签检查。
- DB session / Unit of Work bootstrap 测试。
- `./scripts/verify.sh check`

## 阶段 4：HTTP 接口适配标准 Envelope

目标：公开接口按 `project-standards-code-facts.md` 的标准输入输出适配。

范围：

- `GET /models`、`GET /prompt-templates`、`POST /jobs`、`GET /jobs/{job_id}` 返回 `ResponseEnvelope[TData]`。
- `POST /jobs` 和 `GET /jobs/{job_id}` 的 `data` 只允许包含 `job`，即 `ResponseEnvelope[JobResponseData[JobResult]]`。
- 错误响应全部进入标准 envelope。
- 所有 request schema 默认拒绝未知字段。
- OpenAPI 示例和 mock fixture 更新为标准 envelope。
- README、接入规范、mock interface 文档同步更新。

禁止：

- 为非标准响应增加 query/header 开关。
- 在 route 中手写 envelope。
- 继续让 `{"error": ...}` 作为 HTTP 顶层错误结构。

验证：

- Contract tests 覆盖所有公开接口成功和失败结构。
- Route / OpenAPI envelope allowlist 检查：所有非豁免受保护接口必须使用 `ResponseEnvelope[TData]`。
- Job HTTP contract tests 检查 `data.job.job_result`，不得把 Job 字段平铺到 `data` 顶层。
- Mock 接口仅在重新定义为正式调试能力后补合同测试；当前不保留旧 mock interface 测试。
- OpenAPI/schema 快照。
- OpenAPI example、README 示例、mock fixture 与 schema 的 parity check。

## 阶段 5：Job / Workflow Schema 规范化

目标：让 `job_type` 的输入、runtime、内部结果和 `job_result` 都有当前 schema 注册真源，并让轮询和 callback 复用同一 Job envelope。

范围：

- 每个 `job_type` 声明 Params / RuntimeFields / CanonicalResult / JobResult schema。
- `runtime_ref` 保存 runtime fields 和 hash，不保存历史 schema 版本字段。
- canonical result 投影为同一份 `job_result`，轮询 `data.job.job_result` 和 callback `job.job_result` 必须同源一致。
- `job_result = null` 的 workflow 必须声明稳定交付渠道，且轮询和 callback 都返回 `job_result: null`。
- `WorkflowHandler` 拆分或组合为 params、runtime、plan、executor、merger、callback projector、success side effect。
- 对象存储引用、大 JSON 或外部写回摘要如需对外暴露，必须进入具体任务的 `job_params` 或 `job_result`，不得新增通用产物字段。

禁止：

- Prompt YAML 反向定义 `job_type`。
- Callback `job.job_result` 与轮询 `data.job.job_result` 不一致。
- 新 `job_type` 复制通用 Job route、Job 表、Job envelope 或 callback envelope。
- 在通用 Job envelope、callback envelope 或 HTTP `data` 顶层新增能力专属字段。

验证：

- `job_type` registry 唯一性和当前 schema 反查检查。
- Job envelope round-trip：创建 Job、持久化 Job、查询 `data.job`、生成 `CallbackEnvelope.job`。
- canonical result -> `job_result` 可追溯性检查。
- `data.job.job_result` 与 `CallbackEnvelope.job.job_result` 一致性测试。
- JobResponseData / JobEnvelope / CallbackEnvelope contract tests。
- 新增 job_type 时补对应 workflow schema / callback / result 合同测试；当前不保留旧 workflow 专属测试。
- `tests/test_callback_delivery.py`
- `tests/test_workflow_dispatch.py`

## 阶段 6：ORM、Repository 和 Job 状态权威

目标：持久化边界符合 ORM / Repository / CAS 规则。

范围：

- ORM model 迁移到 `app/db/models/` 或建立等价清晰边界。
- API / Worker / Recovery 使用统一 session factory / Unit of Work 语义。
- Repository 只做查询表达和 CAS 更新。
- Job lifecycle、publisher、recovery 从 task / service 中收口到 `app/jobs/`。
- Alembic 迁移与 ORM model 同步。

禁止：

- API 启动时隐式 `create_tables()`。
- Worker task 跳过 Service 直接改 Job 终态。
- Repository 调用外部 HTTP、broker 或 callback。
- ORM 对象作为响应 schema 返回。

验证：

- Repository / Unit of Work 测试。
- Job CAS 状态迁移测试。
- recovery orphan / unpublished / stale / callback / cleanup 测试。
- Alembic migration check。

## 阶段 7：Integration 与副作用规范化

目标：外部服务、Callback、对象存储和具体 workflow 副作用全部通过 adapter 和注册错误码收口。

范围：

- `integrations/ai` 管模型 provider。
- `integrations/storage` 管对象存储读写。
- `integrations/callback` 管 Callback 投递、签名、重试。
- workflow 专属外部系统如需接入，必须先定义独立 integration 边界、错误映射和幂等语义。
- 外部错误转换为注册错误码或 Job error。
- 副作用声明幂等键、恢复策略和失败收敛。

禁止：

- Workflow 直接拼外部响应协议。
- 外部错误原文直接进入 HTTP error 或 Job error。
- 大 JSON、文件引用或对象存储引用绕过 `job_params` / `job_result` 进入通用 envelope 顶层。

验证：

- Callback HMAC、retry、非标准 callback ack 显式失败、失败不改 Job 终态。
- `job_params` / `job_result` 中对象存储引用的 hash、大小和过期。
- workflow 专属 integration 的 mock / real mode 配置校验。
- AI provider 超时、限流、结构化输出非法测试。

## 阶段 8：文档、OpenAPI 和 Mock Fixture 收口

目标：所有投影都从 schema / registry / contract tests 对齐，不再各写一套。

范围：

- `docs/README.md` 指向通用合同真源。
- 接入规范更新为标准 envelope。
- mock interface 文档和 fixture 使用标准 envelope。
- README 示例更新。
- OpenAPI operation_id 稳定且唯一。

禁止：

- README 示例手写与 schema 不一致的结构。
- mock fixture 成为第二套事实源。
- 任何接口文档继续作为当前合同入口。

验证：

- Mock fixture 不作为合同真源；当前不保留旧 mock interface 测试。
- OpenAPI/schema 快照。
- OpenAPI example、README 示例、mock fixture 与 schema 的 parity check。
- 文档链接检查或 `rg` 检查非标准响应模式。

## Review Checklist

每个 PR 先检查是否符合 `project-standards-code-facts.md`：

- 是否符合 [项目规范与骨架（代码事实版）](project-standards-code-facts.md)。
- 是否使用标准 `ResponseEnvelope[TData]`。
- 错误是否来自错误码注册表。
- request schema 是否拒绝未知字段。
- 新接口是否有 operation_id、字段表、错误码和 contract tests。
- Job HTTP 响应是否使用 `ResponseEnvelope[JobResponseData[JobResult]]`，且 Job 字段只出现在 `data.job`。
- `job_type` 是否声明 params/runtime/canonical/job_result 当前 schema。
- Callback 是否使用 `CallbackEnvelope[JobEnvelope[JobResult]]`。
- 轮询 `data.job.job_result` 与 callback `job.job_result` 是否一致。
- Settings 是否通过子对象和机器检查维护。
- 日志是否包含 request_id、operation_id、caller_id、job_id 和错误码。
- ORM model 是否只作为持久化结构。
- Repository 是否只做 DB 访问和 CAS。
- 是否没有 silent fallback、默认吞错、空结果兼容或自动降级。

## 验证基线

每个重构 PR 默认运行：

```bash
./scripts/verify.sh check
```

按触碰范围追加：

| 触碰范围 | 追加验证 |
|---|---|
| Envelope / exception / error registry | contract tests、OpenAPI/schema 快照、全局异常转换测试、registry consistency suite |
| Settings / env | `tests/test_config.py`、env config check、敏感字段保护测试、deprecated / unknown key 失败矩阵 |
| Logging / metrics | 日志字段测试、非法 request id 和敏感字段负向测试、metrics 标签禁区测试 |
| API route | contract tests、mock interface tests、OpenAPI/schema 快照、envelope allowlist 检查 |
| Job / workflow schema | workflow dispatch、callback delivery、workflow contract tests、Job envelope round-trip、`job_result` 一致性测试 |
| DB / ORM / Repository | job repo、migration、recovery、CAS 状态迁移测试 |
| Integration | callback、对象存储、AI provider、workflow 专属 integration 相关测试、外部错误转换注册检查 |

如果 `./scripts/verify.sh check` 暴露既有顺序污染或环境问题，应先记录并单独定位；不得因为规范重构需要通过验证而修改无关业务逻辑。
