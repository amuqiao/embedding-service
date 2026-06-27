# AI Capability 当前模型

本文只记录当前已经落地的 AI 调用能力事实。未来多模态 provider、业务 `job_type` 和 workflow attribution 计划放在 `docs/plans/`。

## 当前行为

- 当前稳定 AI 调用入口是 `app/services/ai_gateway_facade.py` 的 `generate_text_with_ledger()`。
- 当前真实 provider path 是文本生成；内置真实 LLM 示例 `job_real_llm_echo` 和 `job_real_llm_double_echo` 通过该入口调用模型。
- `app/services/ai_capability_kernel.py` 承载当前 AI kernel 组件：`ModelGate`、`ProviderGateway`、`UsageNormalizer`、`TypedPricingResolver` 和 `UsageLedgerWriter`。
- `app/integrations/ai_adapters/` 承载模型调用 adapter registry；当前内置 `litellm` adapter。
- `app/integrations/ai_gateway.py` 是当前 LiteLLM 文本调用实现，返回 `TextGenerationResult` 和 provider usage，不写数据库、不改 Job 状态、不生成 billing 响应。
- `app/core/usage_records.py` 已有 `TextUsageRecord`、`ImageUsageRecord`、`AudioUsageRecord` 和 `VideoUsageRecord` 类型；当前只有文本 provider path 会产生真实 provider usage record。

## Runtime Path

```text
Job executor / real LLM job_type
  -> generate_text_with_ledger()
  -> ModelGate
  -> UsageLedgerWriter.create_pending()
  -> ProviderGateway.generate_text()
  -> ai_adapters.registry
  -> litellm adapter
  -> UsageNormalizer.normalize_text()
  -> TypedPricingResolver.calculate_cost()
  -> UsageLedgerWriter.mark_succeeded() / mark_failed()
```

## Registry

模型目录由 `app/core/models.yaml` 和 `app/core/model_registry.py` 管理。当前 enabled 模型必须声明：

- `id`
- `name`
- `model_type`
- `adapter`
- `provider`
- `provider_model`
- `adapter_model`
- `pricing_ref`
- `enabled`
- `capabilities`
- `input_media_types`
- `output_media_types`
- `limits`
- `features`
- `parameters.public`
- `notes`
- `requires_env`
- text 模型的 generation 参数

`model_type` 是模型目录粗分类，当前支持 `text`、`image`、`audio` 和 `video`。`capabilities` 表达具体可执行能力，`model_type` 不绑定单一 capability 或输出 MIME type。当前真实 provider path 只覆盖文本生成；图片模型可以进入 catalog 展示，但必须由对应业务 `job_type` 和 adapter 调用链路决定是否可提交和执行。

`limits` 和 `features` 是公开的类型化元信息。当前文本模型使用 `limits.context_window` 和 `features.supports_json_output`。`parameters.public` 是允许 `GET /models` 展示给调用方的模型级可配置参数 schema。当前内置文本模型没有公开模型级参数，因此配置为 `parameters.public: []`。`generation` 仍是 text provider 内部调用配置，不进入公开模型合同。

`adapter` 指向模型调用 adapter，当前内置 `litellm` adapter 复用 LiteLLM 文本生成调用。`provider_model` 是 provider 原始模型名，用于 pricing 匹配和审计；`adapter_model` 是传给 adapter 的模型标识，LiteLLM adapter 当前使用 `openai/<provider_model>` 形式。缺少 required env 的模型不会出现在 `GET /models` 返回中。`GET /models` 只返回模型目录的公开投影，不暴露 `adapter`、`adapter_model`、`pricing_ref`、`requires_env`、`generation` 或 provider 内部参数。

Prompt 目录由 `PROMPT_CONFIG_PATH` 指向的基础配置和 `app/jobs/types/*/prompts.yaml` 的业务包内配置共同组成，加载逻辑在 `app/core/prompt_templates.py`。当前 Prompt registry 会进入 registry consistency 校验；正式业务 `job_type` 需要按自身 schema 引用 prompt refs，且不同配置文件之间不得重复声明同一个 prompt ref。

价格目录由 `app/core/pricing.yaml` 和 `app/core/pricing_registry.py` 管理。`pricing_ref` 必须存在并与模型的 `model_id`、`provider`、`provider_model` 匹配。

## 分层边界

| 层 | 当前职责 |
|---|---|
| `app/jobs/` | 业务 `job_type` 选择模型、构造 messages、消费 provider result |
| `app/services/ai_gateway_facade.py` | 对业务 Job 暴露稳定 AI 调用 facade |
| `app/services/ai_capability_kernel.py` | 模型准入、provider 调用编排、usage 标准化、pricing 计算和 ledger 写入组件 |
| `app/integrations/ai_adapters/` | 模型调用 adapter registry 和具体 provider adapter |
| `app/integrations/ai_gateway.py` | 当前 LiteLLM 文本调用实现 |
| `app/core/model_registry.py` | 模型目录加载、公开模型列表和模型配置校验 |
| `app/core/pricing_registry.py` | typed pricing rule 加载、匹配校验和成本估算 |
| `app/core/usage_records.py` | 内部 typed usage record |

禁止的依赖方向：

- API route 直接调用 provider adapter。
- Provider adapter 直接写数据库。
- 业务 `job_type` 直接计算 pricing 或写 `ai_call_ledger_entries`。
- Public schema 暴露 `pricing_ref`、价格矩阵、provider raw usage 或 provider key。

## 当前限制

- 当前真实 provider path 只覆盖文本生成。
- image / audio / video usage record 和 pricing rule 基础类型已经存在，但还没有真实多模态 provider adapter 和业务消费链路。
- 当前 `ai_call_ledger_entries` 持久化 `usage_detail` 和 `usage_units`，没有独立持久化 `usage_kind` 或 `usage_schema_version` 列。
- workflow child AI 调用当前使用 root Job billing scope；node / child 级成本归因没有专用持久化字段。

## 验证

- `tests/test_ai_gateway_facade.py`
- `tests/test_model_registry.py`
- `tests/test_pricing_registry.py`
- `tests/test_usage_records.py`
- `tests/test_job_real_llm_echo_workflow.py`
- `./scripts/verify.sh check`
