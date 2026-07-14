# AI Capability 当前模型

本文只记录当前已经落地的 AI 调用能力事实。未来 provider、业务 `job_type` 和 workflow attribution 计划放在 `docs/plans/`；对外 `/models` 字段合同以 [`../api/service-contract.md`](../api/service-contract.md) 为准。

## 当前行为

- 当前稳定 AI 调用入口是 `app/services/ai_gateway_facade.py` 的 `generate_text_with_ledger()`、`generate_text_with_images_with_ledger()` 和 `generate_image_with_ledger()`。
- 当前真实 provider path 覆盖文本生成、带参考图文本生成，以及 `poster_title_image` 使用的图片生成；内置真实 LLM 示例 `job_real_llm_echo` 和 `job_real_llm_double_echo` 通过文本入口调用模型。
- `app/services/ai_capability_kernel.py` 承载当前 AI kernel 组件：`ModelGate`、`ProviderGateway`、`UsageNormalizer`、`TypedPricingResolver` 和 `UsageLedgerWriter`。
- `app/integrations/ai_adapters/` 承载模型调用 adapter registry；当前内置 `litellm`、`openai_responses` 和 `openai_images` adapter。
- `app/integrations/ai_gateway.py` 是当前 LiteLLM 文本调用实现，返回 `TextGenerationResult` 和 provider usage，不写数据库、不改 Job 状态、不生成 billing 响应。
- `app/core/usage_records.py` 已有 `TextUsageRecord`、`ImageUsageRecord`、`AudioUsageRecord` 和 `VideoUsageRecord` 类型；当前文本和图片 provider path 会产生真实 provider usage record，audio / video 仍只有基础类型。

## Runtime Path

文本生成：

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

图片生成：

```text
poster_title_image generate item
  -> generate_image_with_ledger()
  -> ModelGate
  -> UsageLedgerWriter.create_pending()
  -> ProviderGateway.generate_image()
  -> ai_adapters.registry
  -> openai_images adapter
  -> UsageNormalizer.normalize_image()
  -> TypedPricingResolver.calculate_cost()
  -> UsageLedgerWriter.mark_succeeded() / mark_failed()
```

## Registry

模型目录由 `app/core/models.yaml` 和 `app/core/model_registry.py` 管理。顶层字段是运行时服务配置；`public` 块是 `GET /models` 的唯一公开投影来源。修改 `provider_model`、`adapter_model`、`pricing_ref`、`requires_env` 或 `generation` 不应改变调用方看到的模型信息，除非同时显式修改 `public` 块。

`public.model_type` 是模型目录粗分类，当前支持 `text`、`image`、`audio` 和 `video`。`public.capabilities` 表达具体可执行能力，`model_type` 不绑定单一 capability 或输出 MIME type。图片模型可以进入 catalog 展示，但是否可提交和执行由对应业务 `job_type` 和 adapter 调用链路决定；当前 `poster_title_image` 通过任务级模型选择和 `generation.image_adapter` 使用图片生成能力。

`public.limits`、`public.features` 和 `public.parameters` 是允许 `GET /models` 展示给调用方的公开元信息。字段细节不在本文重复维护。

`adapter` 指向模型调用 adapter。`litellm` 当前复用 LiteLLM 文本生成调用；`openai_responses` 使用 OpenAI Responses API，支持带参考图文本生成；`openai_images` 使用 OpenAI Images API 直连生图/编辑。`provider_model` 是 provider 原始模型名，用于 pricing 匹配和审计；`adapter_model` 是传给 adapter 的模型标识。缺少 required env 的模型不会出现在 `GET /models` 返回中。

`GET /models?job_type=<job_type>` 仍使用同一个公开模型投影；当对应 `app/jobs/types/<job_type>/models.yaml` 存在时，响应会按任务级 `public_model_selection` 过滤并返回任务级默认模型。

`poster_title_image` 的生图连接路径由 `app/jobs/types/poster_title_image/models.yaml` 中的 `generation.image_adapter` 控制，当前默认是 `openai_images`。prompt 构造、绿底后处理、draw_count、Job workflow 和 billing scope 不随 adapter 选择改变。adapter 选择会写入 Job runtime snapshot；修改 YAML 后只影响后续新建 Job，已创建 Job 和已生成的内部子任务继续使用入库时冻结的 adapter。

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

- 当前图片 provider path 只覆盖 `poster_title_image` 所需的生图/编辑形态；mask、单次请求多图和 API 级透明背景不属于当前合同。
- audio / video usage record 和 pricing rule 基础类型已经存在，但还没有真实 provider adapter 和业务消费链路。
- 当前 `ai_call_ledger_entries` 持久化 `usage_detail` 和 `usage_units`，没有独立持久化 `usage_kind` 或 `usage_schema_version` 列。
- workflow child AI 调用当前使用 root Job billing scope；node / child 级成本归因没有专用持久化字段。

## 验证

- `tests/test_ai_gateway_facade.py`
- `tests/test_model_registry.py`
- `tests/test_pricing_registry.py`
- `tests/test_usage_records.py`
- `tests/test_job_real_llm_echo_workflow.py`
- `./scripts/verify.sh check`
