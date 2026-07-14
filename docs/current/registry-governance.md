# Registry Governance 当前模型

本文记录当前已经落地的 registry governance、Job capability 和 tool 注册事实。它不是对外 API 合同；调用方仍只依赖 `docs/api/` 中的 Job 合同。

## 当前定位

Registry 是代码事实源和启动期合同校验系统，不是插件系统、数据库 catalog、运行时配置中心或 public API。

当前统一治理覆盖的新增能力链路是：

```text
Job Type
  -> Capability
    -> Tool
      -> Integration / adapter
```

已有 operation、schema、prompt、workflow、model、pricing 和 AI adapter registry 仍沿用各自入口，但统一由 `app/core/registry_checks.py` 的 `validate_all_registries()` 汇总校验。

## 当前入口

| 机制 | 当前入口 | 当前职责 |
|---|---|---|
| Ref parser | `app/core/registries/refs.py` | 校验 `capability_ref` / `tool_ref` 的 `<key>:<version>` 格式 |
| Capability registry | `app/capabilities/registry.py`、`app/capabilities/register.py` | 注册 `CapabilityDefinition`，支持 freeze 和测试清理 |
| Tool registry | `app/tools/registry.py`、`app/tools/register.py` | 注册 `ToolDefinition`，支持 freeze 和测试清理 |
| Job Type registry | `app/jobs/registry.py`、`app/jobs/base.py` | `JobTypeSpec.allowed_capability_refs` 声明 job type 可用 capability |
| Error registry | `app/core/error_registry.py` | `ErrorSpec` 包含 `visibility` 和 `projection_targets` 元数据 |
| Registry check | `app/core/registry_checks.py` | 校验 error、operation、job type、capability、tool、schema、log event、entrypoint、settings、error projection、import direction 和 route operation |

API startup 和 worker startup 都执行同一组注册和校验。`app/jobs/types/register.py` 仍是当前 composition root：它显式调用 tool、capability、error、job type 和 workflow 注册入口。注册完成后 API/worker 会 freeze error、tool 和 capability registry；freeze 后相同 definition 可幂等重复注册，变更 definition 会失败。

## 当前 Graph 校验

当前 `validate_all_registries()` 会 fail-fast 校验：

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
- `app/capabilities` 不依赖 `app/jobs`，也不跳过 tool 直接依赖 `app/integrations`；`app/tools` 不反向依赖 `app/jobs` / `app/capabilities`；`app/integrations` 不反向依赖 `app/jobs` / `app/capabilities` / `app/tools`。
- operation registry、job type registry、prompt config、route operation 和 error code 唯一性仍按既有规则校验。

校验失败会中止启动或测试，不做自动跳过、silent fallback 或动态降级。

## 当前 Capability / Tool

当前首个已注册 capability 是 `media.audio_input:1`：

| 字段 | 当前值 |
|---|---|
| `capability_ref` | `media.audio_input:1` |
| plan schema | `AudioWavInputPlanSnapshot` |
| result schema | `PreparedAudioInputMetadata` |
| service entrypoint | `app.capabilities.media.audio_input:prepare_audio_wav_input` |
| allowed tools | `object_storage_read:1` |

当前首个已注册 tool 是 `object_storage_read:1`：

| 字段 | 当前值 |
|---|---|
| `tool_ref` | `object_storage_read:1` |
| kind | `object_storage` |
| entrypoint | `app.tools.object_storage:read_object_bytes` |
| request schema | `CanonicalObjectRefSnapshot` |
| required settings | `storage.backend`、`job.oss_input_max_bytes` |

`audio_stem_separation` 和 `audio_stem_separation_triton` 都声明 `allowed_capability_refs={"media.audio_input:1"}`。两个 job type 在创建 Job 的 `runtime_fields` 时由 job shared builder 冻结 `media_input_plan`，执行期 capability 只读取 frozen plan，不按最新配置重新推导输入读取策略，也不直接解析调用方 payload。

当前 frozen plan 只支持 WAV 输入，且只冻结对象身份和读取策略：

```text
AudioWavInputPlanSnapshot
  capability_ref = media.audio_input:1
  tool_refs = [object_storage_read:1]
  source = provider / bucket / region / key / content_type / content_hash
  fetch = object_storage / canonical_object_ref / max_bytes / forbid redirects
  max_duration_seconds = request policy snapshot
```

`public_url` 和 `internal_url` 仍属于调用方 payload 兼容字段；执行期不把完整 URL 当作权威对象身份。

## 当前边界

Capability 不拥有 Job 状态、attempt、lease、heartbeat、retry、dispatch、callback 或 billing。Tool 不写 Job 状态，不投影 public result，不决定 retry。需要独立调度、恢复、取消或查询的步骤仍应建模为 internal child Job / workflow node。

当前依赖方向是 `jobs -> capabilities -> tools -> integrations`。Job 层可以构造 capability plan snapshot 并消费 capability 结果；capability 层只消费 frozen snapshot 并调用已注册 tool；tool 层只封装底层执行边界并触达 integration adapter。

运行时 Job 失败落库前会执行 public error 投影。若 executor 误抛未被当前 job type 声明的 reason，或 future internal reason，持久化 Job error 会被投影为 public `JOB_EXECUTION_FAILED`，并只在 details 中保留 `internal_reason`，避免 internal reason 进入 `GET /jobs` 或 Callback 合同。

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
