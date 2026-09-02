# 扩展接入指南

本文记录新增业务能力时应修改的稳定入口。具体实现先沿用现有目录和测试风格，不新增平行规范文档。

## 新增 tool

新增 tool 只走统一注册入口，不在业务代码中散落 definition。

新增 tool：

1. 在 `app/tools/private/<name>.py` 中实现单一底层执行边界；tool 不依赖 `app/jobs` 或 `app/business_packages`。如需第三方 SDK/client，把 provider client 放在 `app/tools/providers/<provider>.py`，由 private tool 调用 provider。
2. 如需 request / result schema，在 tool 附近的合同模块中定义，例如 `app/tools/private/<name>_contracts.py`，并加入 `app/schemas/registry.py`。
3. 在 `app/tools/register.py` 中创建 `ToolDefinition`，声明 `tool_ref`、kind、entrypoint、schema、required settings、error codes 和 log events。
4. 只在进程级必需依赖上使用 `startup_validators`；可选模型链路或 demo job 依赖留在执行路径或专项 smoke / verify 中 fail-fast。
5. 补充或调整 `tests/test_registry_contract.py`、`tests/test_tool_registry.py` 和 `tests/test_tools_script.py`。

当前准入测试会阻止 `ToolDefinition(...)` 散落到统一注册入口之外；`./scripts/tools.sh registry --json` 的 graph 也是精确快照，新增注册关系必须同步更新。

## 新增 job_type

1. 在 `app/business_packages/<package>/schemas.py` 或包内同级 schema 文件中定义 Params、Runtime fields 和 Result schema，并导出 `SCHEMAS`。
2. 在 `app/business_packages/<package>/` 中实现 `JobExecutor`。正式业务使用包目录，至少保留 schema 文件、executor 文件、`register.py` 和轻量 `__init__.py`，按需增加 `errors.py`、`prompts.yaml`、`models.yaml` 或 `model_asset.yaml` 等业务内聚文件。一个业务包可以拥有多个 `job_type`，但它们必须属于同一个业务语义边界。
3. 在 executor 上使用 `@register_job_type` 标记源码准入，并声明稳定 `name`、`visibility`、`role`、`params_schema`、`runtime_fields_schema_name`、`canonical_result_schema`、`public_result_schema`、`retry_policy`、`required_tool_refs` 和 side-effect 元数据。`JobTypeSpec` 是代码级事实源；不要只在文档里描述这些字段。
4. 在业务包 `register.py` 中声明 `PACKAGE = BusinessPackage(..., schemas=SCHEMAS)`，并由 `register_job_package(register)` 注册本包全部 executor、errors 和 workflow definition。`register.py` 可以顶层导入 schema 和 error 注册函数，但 executor 必须在 `register_job_package()` 内部延迟导入；业务包 `__init__.py` 不 re-export executor。中心 `app/business_packages/register.py` 只维护 `BUSINESS_PACKAGE_MODULES` 显式清单，不直接 import 业务 executor。
5. 如需模型调用，通过 `app/ai/gateway.py` 进入，不直接调用 provider adapter。
6. 如需大输入或大结果，使用 runtime ref、result ref 和对象存储边界，不把大 payload 直接塞进 Job response。
7. 补充 schema、registry、workflow 和 contract 测试。

`@register_job_type` 只作为静态源码标记，不在 import executor 时写入全局 registry。源码扫描测试会比较所有 `@register_job_type` class 的 `name` 与 package registrar 的注册结果；新增 executor 文件但忘记接入 package `register.py`、`BusinessPackage.schemas` 或中心 `BUSINESS_PACKAGE_MODULES` 会导致验证失败。

标准业务包样板是 `app/business_packages/example_lifecycle_probe/`；对应 smoke 样板是 `smoke/flows/examples/lifecycle_probe.py`。

`job_type` 名称是外部合同；发布后不要随意改名。`job_params` 字段由该 `job_type` 独占校验，不在通用 Job envelope 中新增业务专用字段。

`visibility` 用于目录展示、接入心智模型和外部提交准入，当前取值为：

| visibility | 用途 |
|---|---|
| `public` | 正式业务入口，可作为调用方合同宣传；所有环境都允许外部提交 |
| `demo` | 模板示例、smoke 或压测入口，不是正式业务合同；只允许 `APP_ENV=local/dev` 外部提交 |
| `internal` | 只供服务内部 workflow child 使用；任何环境都不能被外部直接提交 |

`role` 描述该 `job_type` 在目录中的预期入口角色，不替代 Job 实例上的 root/child lineage：

| role | 用途 |
|---|---|
| `root` | 面向调用方或示例的聚合根入口 |
| `leaf` | 只作为 workflow child node 的可执行任务 |
| `root_or_leaf` | 既可直接提交为 root，也可被 workflow 复用为 child |

## 新增 workflow job_type

需要 root/child 编排时，新增业务自己的 `job_type` 和 workflow definition，不开放任意 DAG 提交。

1. 在 `app/business_packages/<package>/schemas.py` 或包内同级 schema 文件中定义 root `job_type` 的 Params、Runtime fields 和 Result schema，并导出 `SCHEMAS`。
2. 在 `app/business_packages/<job_type>/` 中实现 root `JobExecutor`，root executor 使用 `role="root"`，只声明 schema 和运行时字段；实际执行由 workflow orchestration 推进 internal child Jobs。正式业务 workflow 把 root、internal child executors、workflow definition、业务错误和 prompt 模板放在同一个 `job_type` 边界内。
3. 使用 `app.workflows` 的 `task`、`chain`、`group`、`chord`、`map_items`、`starmap_items` 或 `chunks` 生成受控 `workflow_plan`。
4. 在业务包 `register.py` 中注册 executor 和 workflow definition，并把该包 module path 加入中心 `BUSINESS_PACKAGE_MODULES`。`WorkflowDefinition` 必须声明 `workflow_type`、`root_job_type`、`workflow_version`、`failure_policy`、`max_nodes` 和 `runtime_job_type_dependencies`；当前 `workflow_type` 与 `root_job_type` 必须同名，因为外部提交使用 root `job_type` 查找 workflow。业务包被启用时，root、leaf 和 workflow definition 作为一个单元注册；创建 root Job 时，编译出的 `workflow_plan.nodes[].job_type` 必须全部落在该依赖集合内。
5. 按业务语义选择 `failure_policy`；默认 `fail_fast`，需要容忍部分 child 失败时才显式使用 `allow_partial`。
6. 补充 compiler、orchestrator、registry、workflow smoke 或业务 e2e 测试。

workflow child node 应引用 `role="leaf"` 或 `role="root_or_leaf"` 的 executor。`visibility="internal"` 或内部 child Job 的创建由服务内部 workflow orchestrator 完成，不经过外部 `POST /jobs` 提交准入；Job 实例是否为 child 由 `root_job_id` 和 `workflow_node_key` 共同表达：public root 的两者都为空，workflow child 的两者都非空。

当前开发者示例是 `example_workflow`，标记为 `visibility="demo"`、`role="root"`。它可作为本地理解 root/child 模式和压测 workflow 链路的参考；当前示例 mode catalog 见 [`../current/workflow-kernel.md`](../current/workflow-kernel.md)，但它不是正式业务 API 合同。

业务 workflow 对接的是 `job_type` / workflow primitive / profile 合同，不是继承 `example_*`。正式业务可以复用 `task`、`chain`、`group`、`chord`、`map`、`starmap` 和 `chunks` 的编译语义，但必须定义自己的 root params、public result、internal child schema、错误 reason 和业务 e2e。

## 新增压测 Profile

`scripts/load.sh` 的 `case` 表达压测链路，`profile` 表达压测对象和默认参数。新增业务 `job_type` 后，优先新增 JSON profile，而不是修改 `scripts/load/locustfile.py`。

最小步骤：

1. 使用 `./scripts/load.sh init <profile-key> --job-type <job_type>` 生成 `.run/load/profiles/<profile-key>.json`。
2. 在 JSON profile 顶层填写 `case`、`job_type` 和 `job_params`；把 `users`、`spawn_rate`、`time`、`poll_interval_seconds` 和 `flow_timeout_seconds` 等压测默认值放入 `defaults` 对象。
3. 运行时使用 `./scripts/load.sh run --profile <profile-file> --allow-real-job`；非 `example_*` 类型必须显式确认。
4. 使用 `./scripts/load.sh run --profile <profile-file> --dry-run --allow-real-job` 检查 manifest，再进入真实压测。
5. 长期保留的业务 profile 应补充 profile 解析或 dry-run manifest 测试，证明不需要改 Locust runner。

profile manifest 是压测合同的机器可读投影。它应能说明 case、profile、`job_type`、是否存在 `job_params`、是否需要真实业务确认和输出目录；manifest 不应打印完整业务 payload。`example_*` profile 是模板默认低成本目标；真实业务 profile 只复用同一 runner 和 manifest 合同。

## 新增 HTTP 接口

1. 在 `app/api/routes/` 中新增 route 或扩展现有 router。
2. 在 `app/api/operations.py` 注册稳定 `OperationSpec`。`OperationSpec` 是 path、method、成功状态、request/response schema、错误码和副作用的代码级事实源；route decorator 应使用 `operation_path()` 和 `operation_route_kwargs()` 消费它，不要手写一份重复 metadata。
3. 在 `app/schemas/` 中定义 request 和 response data schema。
4. route 返回内层 data schema，不手工构造 `HttpEnvelope`。
5. 错误码先在所属模块声明并注册到 `app/core/error_registry.py` 的全局 registry；service 只抛出已注册的稳定 `AppError` reason。
6. 补充 contract 测试，确保 OpenAPI 和 envelope 结构稳定。

新增同步 AI 能力接口时，不要绕过 AI gateway，也不要在业务 response 里临时发明 `cost`、`usage` 或 `billing` 字段。需要公开计费信息时，应复用统一 billing read model。

## 新增模型

1. 修改 `app/ai/catalog/models.yaml`；如果新增价格，继续修改 `app/ai/pricing/pricing.yaml`。
2. 确认 `model_id` 是对外稳定 ID。
3. 在 `execution.routes.<capability>` 配置运行时字段：`adapter`、`provider`、`provider_model`、`adapter_model`、`pricing_ref`、required env 和内部调用参数。
4. 在 `public` 块配置 `/models` 返回的公开投影：`name`、公开 provider 标签、`model_type`、能力标签、输入/输出媒体类型、`limits`、`features`、`parameters` 和 `notes`。
5. `public.model_type` 只用于目录粗分类，当前取值为 `text`、`image`、`audio` 或 `video`；具体可执行任务由 `public.capabilities` 表达。
6. `public.capabilities` 使用本服务稳定能力值；`public.input_media_types` 和 `public.output_media_types` 使用 MIME type。
7. 使用 `public.limits` 和 `public.features` 声明公开类型化元信息；文本模型需要 `limits.context_window` 和 `features.supports_json_output`。
8. 使用 `public.parameters` 声明允许 `/models` 展示的模型级可配置参数；没有公开参数时显式配置为空列表。图片模型可在这里声明数量、尺寸、背景、质量和输出格式等公开参数。
9. 已有 provider/adapter 支持的新模型优先只修改 `models.yaml` 和 `pricing.yaml`；新 provider 或新调用协议再新增 provider/adapter 注册。
10. `provider_model` 是 provider 原始模型名；`adapter_model` 是传给 adapter 的模型标识。使用 LiteLLM adapter 时，`adapter_model` 通常是 `openai/<provider_model>`。
11. 确保 `pricing_ref` 存在且与模型配置匹配。
12. 运行 `./scripts/ai.sh models <provider>` 验证模型厂商 API Key 和远端模型列表；运行 `./scripts/verify.sh check` 确保 provider、adapter、pricing 和 job model policy 一致。

Provider 密钥来自环境变量，不写入 YAML 或文档示例。

## 新增 Prompt 模板

1. 共享或模板级 Prompt 修改 `PROMPT_CONFIG_PATH` 指向的配置文件，默认是 `app/core/prompts.yaml`。
2. 正式业务包内 Prompt 放在 `app/business_packages/<job_type>/prompts.yaml`，由 Prompt registry 自动合并。
3. 保持模板 ID 稳定；不同配置文件之间不得重复声明同一个 prompt ref。
4. 在对应 `job_type` executor 中引用模板，不在 route 层拼 prompt。
5. 补充 prompt registry 或 workflow 测试。

## 新增对象存储产物

Job result 中的小结果可以直接进入 `job_result`。大结果或文件类产物应写对象存储，并在 result 中返回 artifact metadata 或 ref。

本地开发可使用 `STORAGE_BACKEND=local`。多副本或生产形态必须使用外部对象存储，避免 API/worker 节点读写不同本地磁盘。

## 最小验证

通用修改后运行：

```bash
./scripts/verify.sh check
```

修改 Job 执行链路后运行：

```bash
./scripts/run.sh up dev
./scripts/verify.sh workflow-smoke
./scripts/run.sh down dev
```
