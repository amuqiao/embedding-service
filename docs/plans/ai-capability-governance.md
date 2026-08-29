# AI Capability Governance Plan

本文规划本服务下一阶段 AI 能力治理架构。它描述目标结构、配置边界、注册机制和迁移路径；当前已实现事实仍以 [`../current/ai-capability.md`](../current/ai-capability.md) 为准。

## 目标模型

本服务需要一套轻量但完整的 AI 能力治理机制。目标不是建设复杂模型平台，而是让每个 Job 服务都能稳定回答四个问题：

```text
本服务代码支持哪些 provider / adapter / capability
全局配置启用了哪些 model，当前环境是否具备执行凭证
某个 job_type 允许使用哪些 model
真实执行时最终路由到哪个 capability route / provider_model / adapter
```

AI 能力不应由每个业务 Job 自己拼接。业务 Job 只声明自己需要的 capability、输入输出 schema 和可选模型策略；模型目录、provider 差异、adapter 调用、usage 归一、pricing 校验和诊断脚本由统一 AI harness 承担。

## Current Baseline

- 全局模型目录当前由 `app/core/models.yaml` 和 `app/core/model_registry.py` 管理。
- 当前模型目录支持 `text`、`image`、`audio`、`video` 粗分类，以及 `text_generation`、`multimodal_text_generation`、`image_generation`、`image_edit` 等 capability。
- 当前 AI 调用入口是 `app/services/ai_gateway_facade.py`，包含文本、带图文本和图片生成的 ledger path。
- 当前 AI kernel 在 `app/services/ai_capability_kernel.py`，包含模型准入、provider 调用编排、usage normalizer、pricing resolver 和 ledger writer。
- 当前 adapter registry 在 `app/integrations/ai_adapters/`，内置 `litellm`、`openai_responses` 和 `openai_images`。
- 当前 LiteLLM 文本调用在 `app/integrations/ai_gateway.py`。
- 当前业务级模型配置雏形在 `app/jobs/types/<job_type>/models.yaml`，例如 `poster_title_image` 定义公开可选模型、内部 style probe 模型和图片 adapter。
- 当前配置层只有 `OPENAI_API_KEY`、`OPENAI_BASE_URL`、`DEFAULT_MODEL_ID`、`MODEL_CONFIG_PATH` 和 `PRICING_CONFIG_PATH` 等通用入口；没有独立 DashScope provider 配置。
- 当前 `scripts/models.sh` 管本地模型资产下载、校验和 ONNX inspect；`scripts/smoke.sh` 管 Job/API/worker/callbacker E2E；还没有独立的云模型厂商诊断脚本。

## Remaining Gaps

- Provider 概念没有独立注册层，OpenAI、DashScope、自建 OpenAI-compatible 网关等厂商差异还没有稳定边界。
- Adapter 和 provider 边界容易混淆；`OpenAI-compatible` 是调用协议形态，不应替代真实 `provider=dashscope` 或 `provider=openai`。
- 当前全局模型配置偏向“一个模型一个 provider/adapter”，还不能清楚表达同一模型在不同 capability 下使用不同 execution route。
- 全局模型启用、业务 Job 可用模型、当前环境凭证可执行性三层控制还没有统一 resolver。
- 业务级 `models.yaml` 当前偏向 `poster_title_image`，还不是所有 Job 都可复用的通用模型策略配置。
- 缺少标准 `scripts/ai-providers.sh` 入口来独立检查 provider 配置、列出本地支持模型、查询 live models 和执行最小 probe。
- 缺少 embedding/vector 这类非生成能力的统一 capability、request/result、usage 和 pricing 扩展位置。
- API 与 worker 多实例运行时如果配置不一致，可能出现 API 接受某个模型而 worker 执行时拒绝的漂移风险；需要启动校验和可诊断输出。

## 设计原则

1. 代码注册全集，全局 `models.yaml` 启用子集，业务 `models.yaml` 授权再收窄。
2. 全局模型列表是兜底；只有业务确实需要限制或多模型槽位时，才在 `job_type` 子目录添加模型策略。
3. Job 业务不直接依赖 OpenAI、DashScope、SDK 或 provider raw response。
4. Provider 管厂商身份、鉴权、base URL、live models、probe 和错误归一；adapter 管调用协议和 SDK 形态。
5. `/models`、Job 创建校验、worker 执行、provider 诊断脚本必须消费同一套 catalog/resolver 事实源。
6. 配置错误 fail-fast；不通过 silent fallback 自动换 provider、换模型或吞掉 pricing/usage 错误。
7. `.env` 只承载密钥、base URL、超时和配置文件路径；模型启停不叠加 `.env` allowlist/denylist。
8. 模型目录先使用版本化 YAML；只有出现明确运行时治理需求时才引入数据库配置表。

## 目标目录结构

目标结构按职责收口到 `app/ai/`，再由现有 service、route、job 和脚本调用。迁移完成前，当前 `app/core/*`、`app/services/*` 和 `app/integrations/*` 可作为旧位置逐步搬迁或包装，不要求一次性重命名。

```text
app/ai/
  __init__.py
  capabilities.py              # 稳定 capability 枚举、媒体类型、模型用途
  resolver.py                  # job_type + capability + requested model -> ResolvedModel
  gateway.py                   # 业务唯一 AI 调用入口
  errors.py                    # AI 层稳定错误 reason 和错误归一入口

  catalog/
    __init__.py
    models.yaml                # 全局模型目录目标位置
    registry.py                # 模型加载、校验、公开投影、enabled 过滤
    schema.py                  # ModelDefinition / PublicModel / ModelParameter
    validation.py              # catalog consistency 校验

  providers/
    __init__.py
    base.py                    # ProviderDefinition / ProviderClient / ProviderProbe 协议
    registry.py                # provider 注册全集和配置校验

    openai/
      __init__.py
      provider.py              # OpenAI provider 配置、live models、probe
      errors.py                # OpenAI 错误映射

    dashscope/
      __init__.py
      provider.py              # DashScope provider 配置、live models、probe
      errors.py                # DashScope 错误映射

  adapters/
    __init__.py
    base.py                    # Text/Image/Embedding 等标准 request/result 协议
    registry.py                # adapter 注册全集和 capability 校验
    litellm.py                 # LiteLLM 文本或 OpenAI-compatible 调用协议
    openai_responses.py        # OpenAI Responses 调用协议
    openai_images.py           # OpenAI Images 调用协议
    openai_compatible_embeddings.py
                                # 通用 OpenAI-compatible 向量调用协议；不适配时再新增 provider-specific adapter

  policy/
    __init__.py
    job_models.py              # job_type 模型策略加载和校验
    schema.py                  # JobModelPolicy / ModelSlotPolicy

  usage/
    __init__.py
    records.py                 # Text/Image/Embedding/Audio/Video usage record
    normalizers.py             # provider raw usage -> 标准 usage units

  pricing/
    __init__.py
    pricing.yaml               # pricing 目标位置
    registry.py                # pricing_ref 加载、匹配和成本计算
```

Prompt registry 当前仍按现状归属 `app/core/prompt_templates.py`、`app/core/prompts.yaml` 和 `app/jobs/types/*/prompts.yaml`。本计划不单独重构 prompt governance，但 `verify.sh check` 的一致性校验必须继续覆盖 prompt refs，避免 AI 模型治理和 Prompt 治理变成两套互相漂移的事实源。只有当 prompt 需要和模型 slot、provider capability 或 eval 策略绑定时，再评估把 prompt 入口纳入 `app/ai/`。

脚本层只保留入口和输出合同：

```text
scripts/ai-providers.sh
  -> scripts/ai_providers/cli.py
       -> app.ai.providers.registry
       -> app.ai.catalog.registry
       -> app.ai.resolver
       -> app.ai.gateway
```

## 配置文件规划

### 全局模型目录

全局模型目录是本服务支持模型的代码级事实源。目标位置为 `app/ai/catalog/models.yaml`；迁移期可以继续通过 `MODEL_CONFIG_PATH` 指向当前 `app/core/models.yaml`。

建议从当前 v1 结构演进到 v2 结构：公共投影仍放在 `public`，执行细节统一放入 `execution.routes.<capability>`。这样一个模型可以同时支持文本生成、带图文本、图片生成或向量能力，每个 capability 都有清楚的 provider/adapter 路由。

```yaml
version: "2"
default_model_ids:
  text_generation: qwen-plus
  embeddings: qwen-embedding
models:
  - id: qwen-plus
    enabled: true
    public:
      name: Qwen Plus
      provider: dashscope
      model_type: text
      capabilities:
        - text_generation
      input_media_types:
        - text/plain
      output_media_types:
        - text/plain
      limits:
        context_window: 131072
      features:
        supports_json_output: true
      parameters: []
      notes: ""
    execution:
      routes:
        text_generation:
          provider: dashscope
          provider_model: qwen-plus
          adapter: litellm
          adapter_model: openai/qwen-plus
          pricing_ref: dashscope:qwen-plus@2026-xx-xx
          requires_env:
            - DASHSCOPE_API_KEY
          generation:
            temperature: 0.7
            num_retries: 0
            drop_params: true

  - id: qwen-embedding
    enabled: true
    public:
      name: Qwen Embedding
      provider: dashscope
      model_type: embedding
      capabilities:
        - embeddings
      input_media_types:
        - text/plain
      output_media_types:
        - application/vnd.embedding-vector
      limits:
        dimensions: 1536
        batch_max_items: 64
      features: {}
      parameters: []
      notes: ""
    execution:
      routes:
        embeddings:
          provider: dashscope
          provider_model: text-embedding-v4
          adapter: openai_compatible_embeddings
          adapter_model: text-embedding-v4
          pricing_ref: dashscope:text-embedding-v4@2026-xx-xx
          requires_env:
            - DASHSCOPE_API_KEY
          embedding:
            dimensions: 1536
            batch_max_items: 64
```

说明：

- `id` 是本服务对外稳定模型 ID，不等于厂商原始模型名。
- `provider` 是真实厂商身份，例如 `openai`、`dashscope`、`internal`。
- `provider_model` 是厂商原始模型名，用于审计、pricing 和 provider probe。
- `adapter` 是调用协议实现，例如 `litellm`、`openai_responses`、`openai_images`、`openai_compatible_embeddings`。
- `adapter_model` 是传给 adapter 的模型标识；OpenAI-compatible 协议可以用 `openai/<provider_model>`，但 provider 仍应写真实厂商。
- `enabled` 表示模型在全局模型目录中是否启用；模型可执行性还要继续受 provider/adapter/pricing 校验和 `requires_env` 凭证满足情况控制。
- `public` 块仍是 `/models` 公开投影的唯一来源；内部字段不直接暴露给调用方。
- `execution.routes` 是内部执行真相源；同一个 `model_id` 的不同 capability 可以使用不同 adapter 或 provider route。
- `pricing_ref` 应绑定在 capability route 上，因为同一模型不同能力的计价方式可能不同。

### 配置文件数量与职责

AI 全局配置优先保持两份文件：`models.yaml` 和 `pricing.yaml`。业务需要收窄模型范围时，再在对应 `job_type` 子目录下添加自己的 `models.yaml`。不要为了模型启停额外引入 `.env` allowlist/denylist。

```text
app/ai/catalog/models.yaml
  -> 全局模型列表、enabled、全局默认模型、provider、adapter、capability route、公开投影

app/ai/pricing/pricing.yaml
  -> pricing_ref、计价类型、币种、价格版本

app/jobs/types/<job_type>/models.yaml
  -> 可选；只在该业务需要限定模型、设置业务默认模型或声明内部模型 slot 时添加

.env
  -> 密钥、base URL、超时、配置文件路径；不放模型启停和默认模型
```

模型启停规则：

```text
禁用整个服务的某个模型
  -> 改全局 models.yaml 的 enabled: false

禁用某个 job_type 使用某个模型
  -> 从 app/jobs/types/<job_type>/models.yaml 的 allowed_model_ids 移除

修改某个 job_type 的默认模型
  -> 改 app/jobs/types/<job_type>/models.yaml 的 default_model_id

没有 job_type 专属 models.yaml
  -> 使用全局 models.yaml 的 default_model_ids 和 enabled 模型，并按 required_capabilities 自动过滤
```

### 环境配置

`.env` 只描述运行环境，不描述业务模型策略。它可以选择配置文件路径，也可以提供 provider 凭证和超时，但不再叠加模型启停开关。

建议环境变量：

```dotenv
OPENAI_API_KEY=
OPENAI_BASE_URL=

DASHSCOPE_API_KEY=
DASHSCOPE_BASE_URL=

MODEL_CONFIG_PATH=app/ai/catalog/models.yaml
PRICING_CONFIG_PATH=app/ai/pricing/pricing.yaml
MODEL_CALL_TIMEOUT_SECONDS=300
```

语义：

- `MODEL_CONFIG_PATH` 和 `PRICING_CONFIG_PATH` 只选择配置文件，不改变模型启停语义。
- `OPENAI_API_KEY`、`DASHSCOPE_API_KEY` 等密钥只决定对应 route 是否具备执行条件，不作为业务开关。
- release 环境如果 `enabled: true` 的模型缺少 required env，应启动失败；local/dev 可以让缺密钥模型不可用，但必须在 `check` 输出中明确展示。
- 全局默认模型由 `models.yaml` 的 `default_model_ids` 声明，不再通过 `.env` 叠加一层默认模型开关。

### Job 模型策略

业务模型策略放在 `app/jobs/types/<job_type>/models.yaml`。没有该文件时，Job 使用全局模型列表兜底，并按 executor 声明的 capability 自动过滤。

推荐从单一 `public_model_selection` 演进到通用 `model_slots`：

```yaml
version: tagged_text_translation.models.v1
job_type: tagged_text_translation
model_slots:
  generation:
    visibility: public
    request_field: job_params.model_id
    required_capabilities:
      - text_generation
    default_model_id: qwen-plus
    allowed_model_ids:
      - qwen-plus
      - gpt-4o-mini
```

复杂 Job 可以同时声明公开模型和内部模型：

```yaml
version: semantic_search.models.v1
job_type: semantic_search
model_slots:
  query_embedding:
    visibility: internal
    required_capabilities:
      - embeddings
    default_model_id: qwen-embedding
    allowed_model_ids:
      - qwen-embedding

  answer_generation:
    visibility: public
    request_field: job_params.model_id
    required_capabilities:
      - text_generation
    default_model_id: qwen-plus
    allowed_model_ids:
      - qwen-plus
      - gpt-4o-mini
```

规则：

- Job 自定义 `allowed_model_ids` 必须是全局模型目录中的模型。
- Job 默认模型必须包含在自己的 `allowed_model_ids` 中。
- Job 模型策略只能收窄全局能力，不能绕过全局 `enabled: false`、provider/adapter 缺失、密钥缺失或 pricing 缺失。
- 公开 slot 才允许通过请求字段选择模型；内部 slot 由 executor 固定使用。
- 一个 Job 没有 `models.yaml` 时，不需要业务自行接入 AI，统一 resolver 会按 capability 使用全局默认模型。

## 注册机制

### ProviderDefinition

Provider 注册描述厂商身份和诊断能力，不直接实现业务 Job。

```text
ProviderDefinition
  name
  required_env_keys
  optional_env_keys
  supports_live_models
  supported_protocols
  probe_entrypoint
  error_mapper
```

新增 provider 的最小动作：

1. 新增 `app/ai/providers/<provider>/provider.py`。
2. 在 `app/ai/providers/registry.py` 注册 `ProviderDefinition`。
3. 在配置层声明 required env，例如 `DASHSCOPE_API_KEY`。
4. 新增或复用 adapter。
5. 在全局模型目录新增模型项。
6. 补充 registry 校验和 provider probe 验证。

### AdapterDefinition

Adapter 注册描述调用协议能力。一个 provider 可以复用多个 adapter，一个 adapter 也可以服务多个 OpenAI-compatible provider。

```text
AdapterDefinition
  name
  capabilities
  request_protocol
  result_protocol
  entrypoint
```

示例：

```text
litellm
  capabilities: text_generation

openai_responses
  capabilities: multimodal_text_generation, image_generation

openai_images
  capabilities: image_generation, image_edit

openai_compatible_embeddings
  capabilities: embeddings
```

### CapabilityDefinition

Capability 是业务与 AI 层之间的稳定语言。推荐维护在 `app/ai/capabilities.py`。

初始集合：

```text
text_generation
multimodal_text_generation
image_generation
image_edit
embeddings
audio_transcription
audio_generation
video_generation
rerank
```

不要求一次实现全部 capability；但注册表需要拒绝未知 capability，避免 YAML 拼写错误变成运行时隐患。

### ModelRouteDefinition

Model route 是 `model_id + capability` 的执行事实。resolver 不应只根据模型顶层字段决定调用路径，而应定位到具体 capability route。

```text
ModelRouteDefinition
  model_id
  capability
  provider
  provider_model
  adapter
  adapter_model
  pricing_ref
  requires_env_keys
  call_defaults
```

v1 迁移期可以由旧字段派生默认 route；v2 完成后，新增模型必须显式声明 `execution.routes`。

## Resolver 行为

所有 API、Job 创建、worker 执行、`/models` 和诊断脚本应复用同一个 resolver 规则。

```text
resolve_model(job_type, slot, requested_model_id, required_capabilities)
  -> 加载全局模型目录
  -> 应用 model.enabled
  -> 应用 required env
  -> 读取 job_type/models.yaml；不存在则走全局兜底
  -> 按 slot 的 allowed_model_ids 收窄
  -> 校验 required_capabilities
  -> 选择 requested_model_id 或 slot/default/global default
  -> 选择 capability 对应的 execution route
  -> 校验 route.adapter 支持 capability
  -> 校验 route.provider 已注册且当前启用
  -> 校验 pricing_ref 存在且匹配
  -> 返回 ResolvedModel
```

返回对象应包含：

```text
model_id
provider
provider_model
adapter
adapter_model
capability
pricing_ref
source_policy
route_config_hash
```

`source_policy` 用于排障，说明模型来自全局默认、Job slot 默认，还是请求显式指定。
`route_config_hash` 用于排查多 Pod 配置漂移；API 接受模型和 worker 执行模型时应能看到同一份有效配置摘要。

## Runtime Path

业务调用路径：

```text
Job executor
  -> app.ai.gateway
  -> app.ai.resolver
  -> usage ledger create_pending
  -> adapter registry
  -> provider client / SDK
  -> usage normalizer
  -> pricing resolver
  -> usage ledger mark_succeeded / mark_failed
  -> Job result
```

模型目录路径：

```text
GET /models
  -> app.ai.catalog.registry
  -> app.ai.resolver visible models
  -> public projection
```

Job scoped 模型目录路径：

```text
GET /models?job_type=<job_type>
  -> app.ai.policy.job_models
  -> app.ai.resolver visible models for job_type
  -> public projection
```

Provider 诊断路径：

```text
scripts/ai-providers.sh
  -> scripts/ai_providers/cli.py
  -> app.ai.providers.registry
  -> app.ai.catalog.registry
  -> app.ai.resolver
  -> 可选 app.ai.gateway probe
```

## 脚本 Harness 规划

新增脚本建议命名为 `scripts/ai-providers.sh`，定位为云模型厂商诊断入口。

它不替代：

- `scripts/models.sh`：本地模型资产。
- `scripts/smoke.sh`：Job/API/worker/callbacker E2E。
- `scripts/tools.sh registry`：本地代码注册事实查看。

建议命令：

```text
check
  检查 provider env、base_url、enabled provider/model、default model 和 catalog/pricing 一致性。

models
  列出本服务本地声明支持的模型，支持 --provider、--capability、--job-type、--json。

live-models
  访问厂商接口，列出当前凭据在 provider 侧实际可见模型；默认需要显式 --confirm-network。

resolve
  输入 job_type、slot、capability 和可选 model_id，输出最终 ResolvedModel。

probe
  做一次最小真实调用，必须要求 --confirm-cost；embedding probe 只返回维度、数量和 usage 摘要，不打印完整向量。
```

输出要求：

- 默认人读；`--json` 时 stdout 必须是纯 JSON。
- 不打印 API key、完整 prompt、完整向量、图片 base64 或 provider raw 大响应。
- provider 不可用返回外部依赖类 exit code，不把失败伪装成空模型列表。

## 向量模型接入路径

未来接入通义千问/DashScope 向量模型时，应该作为 `embeddings` capability 接入，而不是在业务 Job 中直接调用 DashScope SDK。

最小扩展：

```text
app/ai/capabilities.py
  -> 新增 embeddings capability

app/ai/adapters/base.py
  -> 新增 EmbeddingRequest / EmbeddingResult / EmbeddingAdapter

app/ai/adapters/openai_compatible_embeddings.py
  -> 优先实现通用 OpenAI-compatible embedding 调用协议

app/ai/adapters/<provider>_embeddings.py
  -> 只有 provider 不能复用通用协议时才新增

app/ai/providers/dashscope/provider.py
  -> 声明 required env、live models、probe、错误映射

app/ai/catalog/models.yaml
  -> 新增 qwen-embedding 模型项

app/ai/usage/records.py
  -> 新增 EmbeddingUsageRecord 或复用标准 token usage record

app/ai/pricing/pricing.yaml
  -> 新增 embedding pricing_ref

scripts/ai-providers.sh probe
  -> 支持 --capability embeddings
```

业务 Job 使用方式：

```text
Job executor
  -> ai.gateway.embed_texts(slot="query_embedding", texts=[...])
```

禁止方式：

```text
Job executor
  -> dashscope SDK
  -> 自己读 DASHSCOPE_API_KEY
  -> 自己决定模型和计费
```

## 模型配置表取舍

当前不建议把模型目录搬进数据库。推荐继续使用版本化 YAML 作为事实源。

理由：

- 模型能力是服务合同的一部分，需要代码评审、测试和可回滚部署。
- 当前项目是单服务 Job 模板，模型变更频率通常低于业务调用频率。
- YAML 更容易和 `verify.sh check`、smoke、代码注册一致性测试绑定。
- 过早上数据库配置表会引入 admin UI、权限、审计、缓存失效、多 Pod 配置一致性和回滚复杂度。

只有出现以下触发条件，才评估数据库表：

```text
需要不发版热切模型
需要租户级或调用方级模型策略
需要灰度百分比、动态路由或 quota
需要审计每次模型配置变更
需要运营后台管理 provider/model 开关
```

如果引入数据库，也不建议直接取代 YAML。更稳的方式是：

```text
YAML
  -> 注册全集和 schema 事实源

DB overlay
  -> 运行时启用、租户策略、灰度和审计

effective config snapshot
  -> API/worker 使用同一版本配置
```

DB overlay 的表结构不在本文定案；只有触发上述条件后，才单独新建设计文档评估表结构、缓存失效、审计、回滚和多 Pod 一致性。DB 化必须满足：可审计、可回滚、API/worker 同版本可见、变更有验证、不能让某个 Pod 独自读到半套配置。

## Planned Work

1. 新建 `app/ai/` package，把现有 AI 概念按 provider、adapter、catalog、policy、gateway、usage、pricing 分层收口。
2. 保留当前行为不变，先通过 wrapper 迁移入口：旧 `app/services/ai_gateway_facade.py` 可委托到 `app/ai/gateway.py`。
3. 把 `app/core/models.yaml`、`app/core/model_registry.py` 迁移或包装为 `app/ai/catalog`。
4. 把 `app/integrations/ai_adapters/` 迁移或包装为 `app/ai/adapters`。
5. 新增 provider registry，先注册 OpenAI，再接入 DashScope。
6. 将 `app/jobs/model_selection.py` 演进为通用 `app/ai/policy/job_models.py`，支持 `model_slots`。
7. 新增 `scripts/ai-providers.sh` 和 `scripts/ai_providers/cli.py`。
8. 保持 prompt registry 现状，但在 AI registry 校验中继续验证模型、prompt、pricing 和 job policy 不漂移。
9. 新增 embedding request/result、adapter、usage normalizer 和 pricing rule 后，再接通 DashScope 向量模型。
10. 扩展 `verify.sh check`，让 provider、adapter、catalog、pricing、job policy 的不一致在本地验证阶段 fail-fast。

## Acceptance

- 新增 OpenAI-compatible provider 不需要修改业务 Job executor，只需要新增 provider 注册、模型配置和必要 adapter。
- 新增 DashScope 文本模型只改 provider 配置、模型目录、pricing 和验证；业务继续走 AI gateway。
- 新增 DashScope 向量模型通过 `embeddings` capability 接入，业务不直接读取 DashScope env 或 SDK。
- `GET /models` 和 `GET /models?job_type=<job_type>` 返回的模型与 worker 实际可执行模型一致。
- API 创建 Job 和 worker 执行 Job 使用同一 resolver 规则，不出现 API 接受、worker 拒绝的配置漂移。
- `scripts/ai-providers.sh check/models/resolve/probe` 能独立定位 provider 配置、模型目录、live provider 和最小调用问题。
- 缺 provider、缺 adapter、缺 required env、缺 pricing、job policy 引用不存在模型、capability 不匹配时，验证命令 fail-fast。
- Provider 调用失败不会被记录成 0 成本成功；usage/pricing 缺失继续按失败处理。

## Non-goals

- 不把本服务改成通用多租户模型平台。
- 不在脚本里实现第二套 provider 调用逻辑。
- 不让业务 Job 直接依赖 provider SDK 或 provider env。
- 不把 provider live models 自动写回本地模型目录。
- 不把模型目录、pricing、prompt 和 job policy 在当前阶段搬进数据库。
- 不引入动态路由、灰度、quota、admin UI，除非后续有明确业务触发条件。
