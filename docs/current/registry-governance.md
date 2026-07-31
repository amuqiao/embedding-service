# Registry Governance 当前模型

本文记录当前已经落地的 registry governance、Job capability 和 tool 注册事实。它不是对外 API 合同；调用方仍只依赖 `docs/api/` 中的 Job 合同。

## 当前定位

Registry 是代码事实源和启动期合同校验系统，不是插件系统、数据库 catalog、运行时配置中心或 public API。

当前统一治理覆盖的新增能力链路是：

```text
Operation
  -> Route / OpenAPI / service contract

Job Type
  -> Capability
    -> Tool
      -> Integration / adapter

Workflow Definition
  -> Root Job Type
  -> Internal Child Jobs at runtime
```

已有 operation、schema、prompt、workflow、model、pricing 和 AI adapter registry 仍沿用各自入口，但统一由 `app/core/registry_checks.py` 的 `validate_all_registries()` 汇总校验。

## 当前入口

| 机制 | 当前入口 | 当前职责 |
|---|---|---|
| Ref parser | `app/core/registries/refs.py` | 校验 `capability_ref` / `tool_ref` 的 `<key>:<version>` 格式 |
| Capability registry | `app/capabilities/registry.py`、`app/capabilities/register.py` | 注册 `CapabilityDefinition`，支持 freeze 和测试清理 |
| Tool registry | `app/tools/registry.py`、`app/tools/register.py` | 注册 `ToolDefinition`，支持 freeze 和测试清理 |
| Job Type registry | `app/jobs/registry.py`、`app/jobs/base.py` | `JobTypeSpec.allowed_capability_refs` 声明 job type 可用 capability |
| Workflow registry | `app/workflows/registry.py` | `WorkflowDefinition` 声明 workflow type、root job type、版本、失败策略和节点上限 |
| Operation registry | `app/api/operations.py` | `OperationSpec` 声明 HTTP operation path、method、成功状态、schema、错误码和副作用 |
| Error registry | `app/core/error_registry.py` | `ErrorSpec` 包含 `visibility` 和 `projection_targets` 元数据 |
| Registry check | `app/core/registry_checks.py`、`tests/test_registry_contract.py` | 校验 error、operation、job type、capability、tool、schema、log event、entrypoint、settings、error projection、注册入口、import direction 和 route operation |

API startup 和 worker startup 都执行同一组注册和校验。`app/jobs/types/register.py` 仍是当前 composition root：它显式调用 tool、capability、error、job type 和 workflow 注册入口。`@register_job_type` 只作为源码准入标记，import executor 不写入全局 registry。注册完成后 API/worker 会 freeze error、tool 和 capability registry；freeze 后相同 definition 可幂等重复注册，变更 definition 会失败。

开发者查看当前注册清单使用只读命令：

```bash
./scripts/tools.sh registry
./scripts/tools.sh registry --json
```

该命令会经过 registry composition root，可能读取应用配置；输出只包含代码注册图，不执行完整 registry consistency 校验。完整校验仍由 `./scripts/verify.sh check` 和 `scripts/verify/registry_check.py` 负责。

`./scripts/tools.sh registry --json` 当前输出 operation、job type、workflow、capability、tool 和 job-capability 关系。它是模板治理 manifest，不是 public API；字段可随模板治理需要演进，但新增正式能力时必须能通过测试确认分类、schema、错误码、能力引用和 workflow 元数据。

Tool `startup_validators` 只用于 API/worker 进程级必需依赖。可选能力、demo job 或特定模型运行时依赖不能放入全局 startup validator；这类依赖应在对应 capability/job 执行路径或专项 verify/smoke 中 fail-fast。

## 当前 Graph 校验

当前 registry checks 和 contract tests 会 fail-fast 校验：

- `capability_ref` / `tool_ref` 格式合法。
- `job_type.allowed_capability_refs` 引用已注册 capability。
- `capability.allowed_tool_refs` 引用已注册 tool。
- capability / tool 引用的 schema 存在于 `app/schemas/registry.py`。
- capability / tool 引用的 error reason 存在于 error registry。
- capability / tool 引用的 log event 存在于日志事件白名单。
- capability service entrypoint、tool entrypoint 和 startup validator 可导入。
- tool required settings 是当前配置面可识别的 `section.field`。
- error `visibility` 只能是 `public` / `internal`。
- internal error 的 `projection_targets` 必须指向已注册 public error。
- public HTTP operation 不能引用 internal error。
- job type public error contract 不能声明 internal error。
- 源码中 `@register_job_type` 声明的 job type 必须全部出现在 `app/jobs/types/register.py` composition root 的注册结果中。
- `ToolDefinition(...)` 只允许出现在 `app/tools/register.py`，`CapabilityDefinition(...)` 只允许出现在 `app/capabilities/register.py`。
- route decorator 从 `OperationSpec` 派生 path、`operation_id`、`response_model`、成功状态和错误响应描述；registry check 会校验 route/OpenAPI 与 operation metadata 对齐。
- registered workflow 必须声明 `root_job_type`，且当前 `root_job_type` 必须与 `workflow_type` 相同，因为外部提交路径用 `job_type` 查找 workflow definition。
- workflow root job type 必须存在且 role 为 `root` 或 `root_or_leaf`；`failure_policy`、`workflow_version`、`max_nodes` 和 `build` callable 由 registry check 校验。
- `./scripts/tools.sh registry --json` 的当前 manifest 由测试覆盖关键结构；新增 operation、job type、workflow、tool、capability 或 job capability 关系时必须同步测试和文档。
- import direction 由 registry contract 测试覆盖：`app/capabilities` 不依赖 `app/jobs`，也不跳过 tool 直接依赖 `app/integrations`；`app/tools` 不反向依赖 `app/jobs` / `app/capabilities`；`app/integrations` 不反向依赖 `app/jobs` / `app/capabilities` / `app/tools`。
- operation registry、job type registry、prompt config、route operation 和 error code 唯一性仍按既有规则校验。

校验失败会中止启动或测试，不做自动跳过、silent fallback 或动态降级。

## 当前 Capability / Tool

当前已注册媒体输入 capability 是 `media.audio_input:2`：

| 字段 | 当前值 |
|---|---|
| `capability_ref` | `media.audio_input:2` |
| plan schema | `AudioInputPlanSnapshot` |
| result schema | `PreparedAudioInputMetadata` |
| service entrypoint | `app.capabilities.media.audio_input:prepare_audio_input` |
| allowed tools | `object_storage_read:1`、`audio_decode_normalize:1` |

当前已注册 media input 相关 tools：

| `tool_ref` | kind | entrypoint | request schema | result schema |
|---|---|---|---|---|
| `object_storage_read:1` | `object_storage` | `app.tools.object_storage:read_object_bytes` | `CanonicalObjectRefSnapshot` | - |
| `audio_decode_normalize:1` | `media_transform` | `app.tools.media_audio:decode_normalize_audio` | `AudioDecodeNormalizeRequest` | - |

`audio_stem_separation` 和 `audio_stem_separation_triton` 都声明 `allowed_capability_refs={"media.audio_input:2"}`。两个 job type 在创建 Job 的 `runtime_fields` 时由 job shared builder 冻结 `media_input_plan`，执行期 capability 只读取 frozen plan，不按最新配置重新推导输入读取策略，也不直接解析调用方 payload。

`audio_decode_normalize:1` 是进程内 media transform tool：request schema 包含原始对象字节和 decode policy；执行结果是本地内存中的 canonical audio，不登记为可序列化 result schema。

当前 frozen plan 支持对象存储中的 WAV / MP3 输入，并冻结对象身份、读取策略和规范化目标：

```text
AudioInputPlanSnapshot
  capability_ref = media.audio_input:2
  tool_refs = [object_storage_read:1, audio_decode_normalize:1]
  source = provider / bucket / region / key / content_type / content_hash
  fetch = object_storage / canonical_object_ref / max_bytes / forbid redirects
  decode = source_content_type / target_sample_rate=44100 / target_channels=2
  max_duration_seconds = request policy snapshot
```

`public_url` 和 `internal_url` 仍属于调用方 payload 兼容字段；执行期不把完整 URL 当作权威对象身份。

## 当前准入规则

新增 `job_type` 必须在 executor 上使用 `@register_job_type` 源码标记，并在 `app/jobs/types/register.py` 显式导入和注册。`@register_job_type` 不产生 import-time 注册副作用；源码扫描测试会比较所有 `@register_job_type` class 的 `name` 与 composition root 注册结果；新增文件但忘记接入 composition root 会失败。

新增 tool 必须通过 `app/tools/register.py` 创建 `ToolDefinition`。新增 capability 必须通过 `app/capabilities/register.py` 创建 `CapabilityDefinition`。不要在业务 executor、capability service、tool 实现或测试外路径中直接散落 definition 构造。

新增 capability 与 tool 的 schema、entrypoint、error code、log event 和 settings 引用必须能被 `validate_capability_tool_registry()` 校验。新增 job type 的 `allowed_capability_refs` 必须引用已注册 capability。

`ToolDefinition.startup_validators` 只允许表达进程级必需依赖；可选模型链路、demo job 或特定业务运行时依赖必须在对应执行路径或专项 smoke / verify 中显式失败，不能扩大为 API/worker 全局启动依赖。

当前 registry 治理保持轻量：不做数据库 catalog、动态插件加载、运行时 capability 开关、管理后台或 public registry API。

## 当前边界

Capability 不拥有 Job 状态、attempt、lease、heartbeat、retry、dispatch、callback 或 billing。Tool 不写 Job 状态，不投影 public result，不决定 retry。需要独立调度、恢复、取消或查询的步骤仍应建模为 internal child Job / workflow node。

当前依赖方向是 `jobs -> capabilities -> tools -> integrations`。Job 层可以构造 capability plan snapshot 并消费 capability 结果；capability 层只消费 frozen snapshot 并调用已注册 tool；tool 层只封装底层执行边界并触达 integration adapter。

运行时 Job 失败落库前会执行 public error 投影。若 executor 抛出未被当前 `job_type` 声明的 internal reason，持久化 Job error 会被投影为 public `JOB_EXECUTION_FAILED`，并只在 details 中保留 `internal_reason`。外部返回和 Callback 合同仍以 [`../api/service-contract.md`](../api/service-contract.md) 为准。

当前没有新增 capability 运行表、tool catalog 表、插件 manifest、动态发现机制或 capability 查询 API。

## 验证

当前覆盖：

- `tests/test_registry_contract.py`
- `tests/test_capability_tool_registry.py`
- `tests/test_media_capability.py`
- `tests/test_audio_stem_separation.py`
- `tests/test_audio_stem_separation_triton.py`
- `uv run python scripts/verify/registry_check.py`
- `./scripts/verify.sh check`
- `./scripts/verify.sh workflow-smoke`
