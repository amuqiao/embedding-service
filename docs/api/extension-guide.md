# 扩展接入指南

本文记录新增业务能力时应修改的稳定入口。具体实现先沿用现有目录和测试风格，不新增平行规范文档。

## 新增 job_type

1. 在 `app/schemas/jobs.py` 中定义 Params、Runtime fields 和 Result schema。
2. 在 `app/jobs/types/<job_type>.py` 或 `app/jobs/types/<job_type>/` 中实现 `JobExecutor`。简单 job 可继续使用单文件；正式业务或带 prompt、errors、workflow 的复杂 job 使用包目录，至少保留 `executor.py` 和 `__init__.py`，按需增加 `errors.py`、`prompts.yaml` 等业务内聚文件。
3. 在 executor 上声明稳定 `name`、`visibility`、`role`、`params_schema`、`runtime_fields_schema_name`、`canonical_result_schema`、`public_result_schema`、`retry_policy` 和 side-effect 元数据。
4. 在 `app/jobs/types/register.py` 显式导入并注册。
5. 如需模型调用，通过 `app/services/ai_gateway_facade.py` 进入，不直接调用 provider adapter。
6. 如需大输入或大结果，使用 runtime ref、result ref 和对象存储边界，不把大 payload 直接塞进 Job response。
7. 补充 schema、registry、workflow 和 contract 测试。

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

1. 在 `app/schemas/jobs.py` 中定义 root `job_type` 的 Params、Runtime fields 和 Result schema。
2. 在 `app/jobs/types/<job_type>.py` 或 `app/jobs/types/<job_type>/` 中实现 root `JobExecutor`，root executor 使用 `role="root"`，只声明 schema 和运行时字段；实际执行由 workflow orchestration 推进 internal child Jobs。正式业务 workflow 优先使用包目录，把 root、internal child executors、workflow definition、业务错误和 prompt 模板放在同一个 `job_type` 边界内。
3. 使用 `app.workflows` 的 `task`、`chain`、`group`、`chord`、`map_items`、`starmap_items` 或 `chunks` 生成受控 `workflow_plan`。
4. 在 `app/jobs/types/register.py` 中注册 executor 和 workflow definition。
5. 按业务语义选择 `failure_policy`；默认 `fail_fast`，需要容忍部分 child 失败时才显式使用 `allow_partial`。
6. 补充 compiler、orchestrator、registry、workflow smoke 或业务 e2e 测试。

workflow child node 应引用 `role="leaf"` 或 `role="root_or_leaf"` 的 executor。`visibility="internal"` 或内部 child Job 的创建由服务内部 workflow orchestrator 完成，不经过外部 `POST /jobs` 提交准入；Job 实例是否为 child 由 `root_job_id` 和 `workflow_node_key` 共同表达：public root 的两者都为空，workflow child 的两者都非空。

当前开发者示例是 `job_test_workflow`，标记为 `visibility="demo"`、`role="root"`。它覆盖 `single`、`chain`、`group`、`chord`、`map`、`starmap` 和 `chunks`，可作为本地理解 root/child 模式和压测 workflow 链路的参考，但不是正式业务 API 合同。

## 新增 HTTP 接口

1. 在 `app/api/routes/` 中新增 route 或扩展现有 router。
2. 在 `app/api/operations.py` 注册稳定 operation id。
3. 在 `app/schemas/` 中定义 request 和 response data schema。
4. route 返回内层 data schema，不手工构造 `HttpEnvelope`。
5. 错误码先在所属模块声明并注册到 `app/core/error_registry.py` 的全局 registry；service 只抛出已注册的稳定 `AppError` reason。
6. 补充 contract 测试，确保 OpenAPI 和 envelope 结构稳定。

新增同步 AI 能力接口时，不要绕过 AI gateway，也不要在业务 response 里临时发明 `cost`、`usage` 或 `billing` 字段。需要公开计费信息时，应复用统一 billing read model。

## 新增模型

1. 修改 `app/core/models.yaml`。
2. 确认 `model_id` 是对外稳定 ID。
3. 在模型顶层配置运行时字段：`adapter`、provider、`provider_model`、`adapter_model`、`pricing_ref`、required env 和内部调用参数。
4. 在 `public` 块配置 `/models` 返回的公开投影：`name`、公开 provider 标签、`model_type`、能力标签、输入/输出媒体类型、`limits`、`features`、`parameters` 和 `notes`。
5. `public.model_type` 只用于目录粗分类，当前取值为 `text`、`image`、`audio` 或 `video`；具体可执行任务由 `public.capabilities` 表达。
6. `public.capabilities` 使用本服务稳定能力值；`public.input_media_types` 和 `public.output_media_types` 使用 MIME type。
7. 使用 `public.limits` 和 `public.features` 声明公开类型化元信息；文本模型需要 `limits.context_window` 和 `features.supports_json_output`。
8. 使用 `public.parameters` 声明允许 `/models` 展示的模型级可配置参数；没有公开参数时显式配置为空列表。图片模型可在这里声明数量、尺寸、背景、质量和输出格式等公开参数。
9. 已有 adapter 支持的新模型优先只修改 `models.yaml` 和 `pricing.yaml`；新 provider 或新调用协议再新增 adapter。
10. `provider_model` 是 provider 原始模型名；`adapter_model` 是传给 adapter 的模型标识。使用 LiteLLM adapter 时，`adapter_model` 通常是 `openai/<provider_model>`。
11. 确保 `pricing_ref` 存在且与模型配置匹配。
12. 补充或调整模型 registry 测试。

Provider 密钥来自环境变量，不写入 YAML 或文档示例。

## 新增 Prompt 模板

1. 共享或模板级 Prompt 修改 `PROMPT_CONFIG_PATH` 指向的配置文件，默认是 `app/core/prompts.yaml`。
2. 正式业务包内 Prompt 放在 `app/jobs/types/<job_type>/prompts.yaml`，由 Prompt registry 自动合并。
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
./scripts/dev.sh start
./scripts/verify.sh workflow-smoke
./scripts/dev.sh stop
```
