# Registry Governance 当前模型

本文记录当前已经落地的 registry governance、业务包和工具注册事实。它不是对外 API 合同；调用方仍只依赖 `docs/api/` 中的接口文档。

## 当前定位

Registry 是代码事实源和启动期合同校验系统，不是插件系统、数据库 catalog、运行时配置中心或 public API。

当前统一治理的关系是：

```text
Operation
  -> Route / OpenAPI / service contract

Business Package
  -> one or more Job Types
  -> Workflow Definition
  -> Package-local errors / routes / storage policy
  -> required_tool_refs

Tool
  -> private tool implementation
  -> provider adapter when needed
```

业务能力通过 `app/business_packages/<package>/` 内聚。一个业务包可以注册多个同一业务语义下的 `job_type`，例如 `audio_stem_separation` 包同时注册本地 ONNX 和 Triton 两种执行形态。工具不是业务包，也不表达跨业务复合能力；它只封装可复用的底层执行边界，例如对象存储读取、音频解码、图片处理或第三方 provider client。

## 当前入口

| 机制 | 当前入口 | 当前职责 |
|---|---|---|
| Ref parser | `app/core/registries/refs.py` | 校验 `tool_ref` 的 `<key>:<version>` 格式 |
| Tool registry | `app/tools/registry.py`、`app/tools/register.py` | 注册 `ToolDefinition`，支持 freeze 和测试清理 |
| Business package registry | `app/business_packages/base.py`、`app/business_packages/register.py` | 维护业务包清单，按 `ENABLED_BUSINESS_PACKAGES` 启用业务包，并收集业务包 routes 和 schema |
| Job Type registry | `app/jobs/registry.py`、`app/jobs/base.py` | `JobTypeSpec.required_tool_refs` 声明 job type 依赖的工具 |
| Workflow registry | `app/workflows/registry.py` | `WorkflowDefinition` 声明 workflow type、root job type、版本、失败策略、节点上限和 runtime child job type 依赖 |
| Operation registry | `app/api/operations.py` | `OperationSpec` 声明 HTTP operation path、method、成功状态、schema、错误码和副作用 |
| Error registry | `app/core/error_registry.py` | `ErrorSpec` 包含 `visibility` 和 `projection_targets` 元数据 |
| Registry check | `app/core/registry_checks.py`、`tests/test_registry_contract.py` | 校验 error、operation、job type、tool、schema、log event、entrypoint、settings、error projection、注册入口、import direction 和 route operation |

API startup 和 worker startup 都执行同一组注册和校验。`app/business_packages/register.py` 是当前业务包 composition root：它先注册平台工具，再通过 `BUSINESS_PACKAGE_MODULES` 懒加载各业务包的 `PACKAGE = BusinessPackage(...)` registrar。业务包的 `register.py` 只暴露轻量 package metadata 和 schema 声明，executor 必须在 `register_job_package()` 内部延迟导入；业务包 `__init__.py` 不 re-export executor。业务包 registrar 内聚注册本包 error、job type、workflow definition，并可按需注册本包 HTTP routes；API startup 会在 `validate_all_registries(application)` 前挂载业务包 routes，worker startup 只注册执行侧能力，不挂载 routes。

注册完成后 API/worker 会 freeze error 和 tool registry；freeze 后相同 definition 可幂等重复注册，变更 definition 会失败。

开发者查看当前注册清单使用只读命令：

```bash
./scripts/tools.sh registry
./scripts/tools.sh registry --json
```

`./scripts/tools.sh registry --json` 当前输出 operation、job type、workflow、tool 和 job-tool 关系。它是模板治理 manifest，不是 public API；字段可随模板治理需要演进，但新增正式能力时必须能通过测试确认分类、schema、错误码、工具引用和 workflow 元数据。

## 当前 Graph 校验

当前 registry checks 和 contract tests 会 fail-fast 校验：

- `tool_ref` 格式合法。
- `job_type.required_tool_refs` 引用已注册 tool。
- tool 引用的 schema 存在于 `app/schemas/registry.py`。工具合同 schema 放在对应 tool 模块附近，业务 schema 放在对应业务包的 schema 文件并通过 `BusinessPackage.schemas` 声明，公共 `app/schemas/jobs.py` 只承载平台 Job 合同。
- tool 引用的 error reason 存在于 error registry。
- tool 引用的 log event 存在于日志事件白名单。
- tool entrypoint 和 startup validator 可导入。
- tool required settings 是当前配置面可识别的 `section.field`。
- error `visibility` 只能是 `public` / `internal`。
- internal error 的 `projection_targets` 必须指向已注册 public error。
- public HTTP operation 不能引用 internal error。
- job type public error contract 不能声明 internal error。
- 源码中 `@register_job_type` 声明的 job type 必须全部由某个 `BusinessPackage` registrar 注册，并出现在 `app/business_packages/register.py` composition root 的注册结果中。
- 每个业务包必须通过 `BusinessPackage.schemas` 声明自己的 request/runtime/result schema；公共 `app/schemas/jobs.py` 不定义业务专属 schema。
- 冷导入 `app.schemas.registry` 不得导入任何 `app.business_packages.*.executor` 模块，也不得写入 job/workflow registry。
- `ToolDefinition(...)` 只允许出现在 `app/tools/register.py`。
- route decorator 从 `OperationSpec` 派生 path、`operation_id`、`response_model`、成功状态和错误响应描述；registry check 会校验 route/OpenAPI 与 operation metadata 对齐。
- registered workflow 必须声明 `root_job_type`，且当前 `root_job_type` 必须与 `workflow_type` 相同，因为外部提交路径用 `job_type` 查找 workflow definition。
- workflow root job type 必须存在且 role 为 `root` 或 `root_or_leaf`。
- `runtime_job_type_dependencies` 必须引用已注册且 child-capable 的 `job_type`。
- import direction 由 registry contract 测试覆盖：`app/tools` 和 `app/object_storage` 不反向依赖 `app/jobs`、`app/business_packages`、旧 `app/integrations` 或旧 `app/capabilities`。
- operation registry、job type registry、prompt config、route operation 和 error code 唯一性仍按既有规则校验。
- 启用的业务包如果声明 `requires_object_storage=True`，启动校验会用 `app/object_storage` 构建当前 `STORAGE_BACKEND` repository 配置；OSS 配置错误必须在启动/验证阶段 fail-fast。

校验失败会中止启动或测试，不做自动跳过、silent fallback 或动态降级。

## 当前 Tool

当前已注册 media input 相关 tools：

| `tool_ref` | kind | entrypoint | request schema | result schema |
|---|---|---|---|---|
| `audio_decode_normalize:1` | `media_transform` | `app.tools.private.media_audio:decode_normalize_audio` | `AudioDecodeNormalizeRequest` | - |

`audio_stem_separation` 和 `audio_stem_separation_triton` 都声明：

```text
required_tool_refs = [
  audio_decode_normalize:1
]
```

两个 job type 在创建 Job 的 `runtime_fields` 时由业务包 storage adapter 冻结 `media_input_plan`，执行期只读取 frozen plan，不按最新配置重新推导输入读取策略，也不直接解析调用方 payload。

当前 frozen plan 支持对象存储中的 WAV / MP3 输入，并冻结对象身份、读取策略和规范化目标：

```text
AudioInputPlanSnapshot
  source = provider / bucket / region / key / content_type / content_hash
  fetch = max_bytes
  decode = source_content_type / target_sample_rate=44100 / target_channels=2
  max_duration_seconds = request policy snapshot
```

`public_url` 和 `internal_url` 仍属于调用方 payload 兼容字段；执行期不把完整 URL 当作权威对象身份。

## 当前准入规则

新增 `job_type` 必须在 executor 上使用 `@register_job_type` 源码标记，并通过业务包 `PACKAGE = BusinessPackage(...)` 注册。正式业务包使用 `app/business_packages/<package>/register.py` 内聚 errors、workflow definition 和 schema 声明；executor 放在业务包内并只在 `register_job_package()` 内部导入。中心 `app/business_packages/register.py` 只维护 `BUSINESS_PACKAGE_MODULES` 显式清单并懒加载 package registrar，不直接 import 业务 executor。`@register_job_type` 不产生 import-time 注册副作用；源码扫描测试会比较所有 `@register_job_type` class 的 `name` 与 business package registrar 注册结果；新增文件但忘记接入 package registrar、`BusinessPackage.schemas` 或中心 business package module 清单会失败。

`ENABLED_BUSINESS_PACKAGES` 是当前服务实例的业务包启用开关。为空表示启用全部静态注册业务包；显式配置时，composition root 仍全量注册 executor catalog、workflow definition 和错误码，用于 schema 校验、历史 Job 查询和 public projection，但只有列出的业务包进入 enabled/external 准入集合，并且只有列出的业务包会挂载 HTTP routes。

新增 tool 必须通过 `app/tools/register.py` 创建 `ToolDefinition`。不要在业务 executor、tool 实现或测试外路径中直接散落 definition 构造。

`ToolDefinition.startup_validators` 只允许表达进程级必需依赖；可选模型链路、demo job 或特定业务运行时依赖必须在对应执行路径或专项 smoke / verify 中显式失败，不能扩大为 API/worker 全局启动依赖。

当前 registry 治理保持轻量：不做数据库 catalog、动态插件加载、运行时工具开关、管理后台或 public registry API。

## 当前边界

业务包拥有业务语义、Job schema、executor、workflow、业务错误、业务 routes 和业务 storage policy。业务包可以依赖平台 Job 内核、AI gateway、对象存储仓储层和工具包；业务包之间不互相 import，确实属于同一业务语义的多个 `job_type` 应放入同一个业务包。工具包不能反向依赖业务包或 Job 层。

Tool 不拥有 Job 状态、attempt、lease、heartbeat、retry、dispatch、callback 或 billing。Tool 不写 Job 状态，不投影 public result，不决定 retry。需要独立调度、恢复、取消或查询的步骤仍应建模为 internal child Job / workflow node。

当前依赖方向是：

```text
business_packages -> jobs / workflows / tools / object_storage / ai
tools -> object_storage / providers / schemas / core
object_storage -> core config / provider SDK boundary
```

运行时 Job 失败落库前会执行 public error 投影。若 executor 抛出未被当前 `job_type` 声明的 internal reason，持久化 Job error 会被投影为 public `JOB_EXECUTION_FAILED`，并只在 details 中保留 `internal_reason`。外部返回和 Callback 合同仍以 [`../api/service-contract.md`](../api/service-contract.md) 为准。

## 验证

当前覆盖：

- `tests/test_registry_contract.py`
- `tests/test_tool_registry.py`
- `tests/test_audio_input_tool.py`
- `tests/test_audio_stem_separation.py`
- `tests/test_audio_stem_separation_triton.py`
- `tests/test_tagged_text_translation.py`
- `uv run python scripts/verify/registry_check.py`
- `./scripts/verify.sh check`
- `./scripts/verify.sh workflow-smoke`
