# AI 能力层成本边界设计

本文定义 AI 能力层在模型目录、typed pricing、provider usage、AI call ledger 和总费用投影上的目标边界。它是计划文档，不描述当前已经完整实现的能力；当前事实仍以 `docs/current/architecture.md`、`docs/api/service-contract.md` 和代码为准。

## 设计目标

AI 能力层只负责回答一个问题：

```text
一次或多次 AI provider 调用已经发生了什么 usage，按当前价格配置估算出的模型成本是多少。
```

AI 能力层不负责回答：

```text
用户是否有余额、是否应该扣费、扣了多少钱、是否退款、财务账单是否结清。
```

因此本文中的 cost、billing、pricing 和 ledger 都只表示模型调用成本估算与查询投影，不表示用户资金扣费、钱包余额、订单账单、财务总账或收入确认。未来本服务也不承担真实扣费职责；如果外部系统需要扣费，必须在本服务之外建立独立资金账本，并把本服务的 cost summary 当作输入信号，而不是把 `ai_call_ledger_entries` 复用为资金账本。

## 需求翻译

表面需求：

- 在接入 `poster_title_image` 和后续多模态 Job 前，先稳定 AI 能力层架构。
- 对外不暴露计费明细，只返回 Job 级总费用。
- 参考旧项目 `cms-ai-manga-backend-master/backend/cost_config.yaml` 的模型覆盖面和计费维度。
- 避免 workflow、Job result 或业务代码里散落计费逻辑。

真实需求：

- 建立一个可复用的内部 AI cost boundary，让不同 job_type、workflow node 和 child Job 都通过同一套模型目录、pricing 规则、usage normalizer 和 ledger 写入路径形成成本事实。
- 让 root Job 终态 callback / polling 能返回总费用，但总费用只能是从 ledger 聚合出的派生投影。
- 保持模型目录、价格事实配置、provider usage 原始数据、typed usage 和公开 API 响应之间的边界清楚。
- 保证配置缺失、usage shape 错误、价格规则缺失、ledger 未收敛时显式失败或 incomplete，不伪造 0 成本成功。

不在解决的问题：

- 不做用户扣费、余额冻结、信用额度、退款、发票、收款或财务总账。
- 不暴露 provider raw usage、价格矩阵、pricing ref、token 明细或内部成本维度给普通调用方。
- 不把 `job.cost`、callback payload、Job result summary 或 workflow summary 升级为成本事实源。
- 不复制旧项目 `cost_config.yaml` 的文件结构、analytics 字段或 fallback 语义。
- 不在 workflow kernel 内直接调用 provider 或直接计算成本。

## 成熟模式

本文采用以下成熟模式组合：

| 模式 | 用途 |
|---|---|
| Catalog | `models.yaml` 和 `pricing.yaml` 分别管理模型目录和价格事实配置 |
| Typed Rule Engine | `PricingRule + UsageRecord + PricingContext -> CostAmount` |
| Anti-Corruption Layer | Provider adapter 输出 raw usage，由 normalizer 转为内部 typed usage |
| Per-call Fact Ledger | `ai_call_ledger_entries` 记录真实 AI provider call、usage、pricing 和 cost estimate 事实 |
| Read Model Projection | Billing / cost summary 从 ledger 聚合，不反向修改 ledger 或 Job 执行事实 |
| Fail-fast Configuration Validation | 模型、价格、prompt 和 capability 配置不一致时启动或验证失败 |
| Idempotent Consumer / Reconciler | provider 调用、ledger terminal update 和 stale pending ledger 都必须可恢复、可排障 |

这些模式的目的不是建设通用计费平台，而是在单服务 AI Job 模板内稳定模型调用成本的事实来源。本文所说的 ledger 是 AI call cost-estimate fact ledger，不是财务级 append-only / double-entry ledger；当前实现可以从 `pending` 行更新为 terminal 行，但 terminal 后的 usage / pricing / cost 语义必须可审计、可诊断、不可被 Job result 覆盖。

## Canonical Glossary

| 术语 | 含义 |
|---|---|
| `model_id` | 本服务对内和对外稳定使用的模型 ID |
| `provider` | provider 标识，例如 `openai`、`gemini`、`tongyi_cn` |
| `provider_model` | provider 侧模型名，用于价格匹配和排障 |
| `litellm_model` | LiteLLM 或 adapter 调用名；它是调用适配字段，不是公开模型 ID |
| `pricing_ref` | 某条稳定价格规则的引用，必须随 AI call ledger 行冻结 |
| `pricing_version` | pricing catalog 的版本快照，用于说明该次调用按哪版价格配置估算 |
| catalog `version` | `models.yaml` 或 `pricing.yaml` 文件自身的配置版本，不等同于单个 `pricing_ref` |
| `usage_detail` | provider raw usage 或诊断详情，不作为聚合计价单位 |
| `usage_units` | 标准化、可聚合、可计价的内部 usage 单位 |
| `cost_amount` | 基于 `pricing_ref`、`pricing_version` 和 `usage_units` 计算出的模型成本估算 |

## 分层边界

```text
app/api
  -> 只处理 HTTP route、认证依赖和 response data schema

app/services
  -> 编排 ModelGate、ProviderGateway、UsageNormalizer、TypedPricingResolver、UsageLedgerWriter

app/core
  -> 管理 model / pricing / prompt registry、配置解析和 fail-fast 校验

app/integrations
  -> 调用外部 provider，返回 raw response / raw usage，不写数据库

app/repositories
  -> 封装 ledger、Job 和 billing 查询，不承载 provider 调用或价格规则

app/schemas
  -> 公开请求/响应合同，不暴露 provider raw usage 或内部价格矩阵
```

AI 能力层与 workflow kernel 的边界：

| 主题 | AI 能力层 | Workflow kernel |
|---|---|---|
| 模型准入 | 负责 | 不负责 |
| provider 调用 | 负责 | 不负责 |
| usage 标准化 | 负责 | 不负责 |
| cost 计算 | 负责 | 不负责 |
| AI call ledger | 负责写入和收敛 | 只传递 root / node / child attribution |
| 多节点编排 | 不负责 | 负责 |
| root Job result projection | 提供 cost summary 输入 | 负责投影到 root Job |
| callback outbox | 不负责 | root Job terminal 时负责 |

## 模型目录和价格目录

### `app/core/models.yaml`

`models.yaml` 是可调用模型目录，负责表达服务允许选择哪些模型，以及这些模型如何映射到 provider。

目标字段边界：

| 字段 | 说明 |
|---|---|
| `id` | 对内和对外稳定使用的 `model_id` |
| `name` | 公开展示名称 |
| `provider` | provider 标识，例如 `openai`、`gemini`、`tongyi_cn` |
| `provider_model` / `litellm_model` | 内部 provider 模型名或 adapter 模型名 |
| `pricing_ref` | 绑定 `pricing.yaml` 中的价格规则 |
| `enabled` | 是否允许调用 |
| `capabilities` | 能力标签，例如 `text_generation`、`image_generation`、`tts`、`video_generation` |
| `input_media_types` | 支持的输入媒体类型 |
| `output_media_types` | 支持的输出媒体类型 |
| `generation` | 稳定生成参数约束，不暴露 provider 密钥 |
| `requires_env` | 启用该模型所需环境变量 |

`GET /models` 只能暴露调用方选择模型需要的公开元信息。不得暴露：

- `pricing_ref`
- provider raw model if not intended for public selection
- 价格矩阵
- provider raw usage schema
- 内部成本明细
- API key 或 provider 私有配置

### `app/core/pricing.yaml`

`pricing.yaml` 是模型调用价格事实配置，负责表达某个 `pricing_ref` 如何把 typed usage 估算成 cost。

目标规则：

- 每个价格规则必须有稳定 `pricing_ref`。
- 每个 `pricing_ref` 必须绑定 `model_id`、`provider`、`provider_model` 和 `version`。
- `pricing_ref` 与 `models.yaml` 的绑定必须 fail-fast 校验。
- 每次 AI call ledger 行必须冻结本次使用的 `pricing_ref`、`pricing_version` 和 `currency`；后续更新 `pricing.yaml` 不能改变历史调用的解释。
- 条件价格不允许 silent fallback 到 0。
- 条件价格为 `null` 时，表示该价格单元尚未确认；命中该单元必须导致 cost calculation failed 或明确 incomplete。
- 价格币种使用配置中的 `currency`，不在业务代码里隐式换汇。

`pricing.yaml` 不负责：

- 用户折扣。
- 促销。
- 余额抵扣。
- 税费。
- 发票。
- 真实扣费流水。

## Typed Usage 和 Pricing

### UsageRecord

`UsageRecord` 是 provider raw usage 进入成本计算前的内部标准形态。provider adapter 可以保留 `raw_usage` / `usage_detail` 作为排障材料，但 billing projection 只能聚合 `usage_units`。

目标类型：

| kind | 核心单位 | 示例 |
|---|---|---|
| `text` | token | input / cached input / output tokens |
| `image` | image count 或 image token | 生成图片张数、输入图片 token、输出图片 token |
| `audio` | duration 或 token | TTS 输入 token、音频秒数 |
| `video` | duration 或 token | 视频秒数、输出 token |

`raw_usage` 可以保留 provider 原始数据用于排障，但 cost resolver 不能直接依赖未经标准化的 provider 字段散落计算。

`usage_detail` 与 `usage_units` 的边界：

| 字段 | 职责 | 可否聚合计费 |
|---|---|---|
| `usage_detail` | 保存 provider raw usage、response 中的原始 usage block 或 adapter 诊断上下文 | 否 |
| `usage_units` | 保存标准化后的 token、image_count、duration_ms、resolution 等内部单位 | 是 |

任何需要进入 billing / cost summary 的单位，都必须先进入 `usage_units`。不得在 billing service 中临时解析 provider raw usage。

### PricingRule

第一层 pricing type：

| pricing_type | 说明 |
|---|---|
| `per_token` | 按 token 计价 |
| `per_image` | 按生成图片张数计价 |
| `per_second` | 按音频或视频时长计价 |
| `per_call` | 按调用次数固定计价 |

第二层条件维度：

| 维度 | 说明 |
|---|---|
| `token_modality` | `text`、`image`、`audio`、`video` |
| `token_direction` | `input`、`cached_input`、`output` |
| `resolution` | `540`、`720`、`1080`、`4k` 等 |
| `has_audio` | 视频是否带音频 |
| `has_reference_image` | 是否使用参考图 |
| `has_reference_video` | 是否使用参考视频 |
| `region` | 区域差异，例如 domestic / international |
| `provider_prefix` | provider 前缀差异，例如 `tongyi_cn:`、`tongyi:` |

成本计算目标接口：

```text
PricingRule
+ UsageRecord
+ PricingContext
-> CostAmount
```

`PricingContext` 来自请求参数、模型配置、provider response 和 workflow/node attribution 的稳定字段，不应从自然语言 prompt 或不稳定日志中推断。

## AI Call Ledger

### Phase 1 数据库/表边界

Phase 1 只收口成本边界合同和验收语义，不新增 AI 成本核心表，也不把未来 workflow / child Job 归因字段写成当前表事实。

- 当前阶段不新增第二套 AI call usage / cost 核心表；`ai_call_ledger_entries` 仍是唯一 AI provider call usage / cost estimate 事实源。
- 当前阶段不新增 `job_cost_summary`。Job cost summary 继续从 `ai_call_ledger_entries` 查询聚合；只有后续证明查询性能或 root projection 稳定性需要时，才评估新增可重建 read model。
- `job_cost_summary` 即使未来新增，也只能从 `ai_call_ledger_entries` 重建，不能保存 provider usage / pricing / cost 的新事实，不能成为扣费账本或事实源。
- workflow / child Job 归因落地前，才评估在 `ai_call_ledger_entries` 增加 `root_job_id`、`workflow_id`、`workflow_node_id`、`child_job_id` 等列，或新增等价 attribution 辅助表。
- `usage_kind`、`usage_schema_version` 当前不是已存在持久化列；如果后续需要按它们查询、过滤或建立索引，必须通过 Alembic migration 明确增加，不能只写入 JSON 后在文档中描述为表字段。

### 核心事实表：`ai_call_ledger_entries`

`ai_call_ledger_entries` 是 AI 模型调用和成本估算的唯一事实源。

它负责记录：

| 字段类别 | 说明 |
|---|---|
| scope | `scope_type`、`scope_id`，当前公开 billing 使用 Job scope |
| Job attribution | 当前已持久化 `job_id`、`attempt_id`、`job_type` |
| Workflow attribution | 当前不作为已存在表字段；未来落地 workflow / child Job 归因前，再评估 `root_job_id`、`workflow_id`、`workflow_node_id`、`child_job_id` 加列或等价辅助表 |
| provider | `model_id`、`provider`、`provider_model`、adapter model |
| operation | 文本生成、图片生成、TTS、视频生成等 |
| request / response hash | 排障和幂等辅助 |
| usage | 当前已持久化 `usage_detail`、`usage_units` |
| usage extension | 未来如需持久化 `usage_kind`、`usage_schema_version`，必须通过明确 migration 增加，不能写成当前表事实 |
| pricing | `pricing_ref`、`pricing_version`、`currency` |
| cost | `cost_amount`、`cost_calculation_status` |
| billable diagnostic | `billable_status`、`failure_phase`、`error_code`、`error_message` |
| lifecycle | `started_at`、`completed_at`、`duration_ms` |

写入规则：

- 调用 provider 前必须先创建 `pending` ledger 行。
- provider 成功后必须标准化 usage、计算 cost，并将 ledger 更新为 terminal。
- provider 失败、usage 缺失、pricing 失败或 ledger terminal update 失败时，不能重放 provider 调用修账。
- stale pending ledger 由 recovery 收敛为明确失败或未知状态。
- ledger terminal 信息不因 root Job 失败而删除。

### Ledger 状态到 Billing 状态

ledger 行级状态与公开 billing 状态必须有稳定映射，避免不同模块各自解释 incomplete / failed。

| Ledger 条件 | Billing / cost summary 状态 |
|---|---|
| scope 无 ledger 行 | `not_billable` |
| 任一 ledger 行 `status=pending` | `incomplete` |
| 任一 ledger 行 `billable_status=pending` 或 `unknown` | `incomplete` |
| 任一 ledger 行 `cost_calculation_status=failed` | `failed` |
| billable 行缺少 `currency` 或 `cost_amount` | `failed` |
| billable 行存在多个 currency | `failed` |
| 全部 ledger 行 terminal，且 billable 行都有 cost | `estimated` |

如果未来 `job.cost` 作为更窄的总费用投影发布，它只能在 billing projection 可形成可信 total 时返回 `final=true`。如果底层 billing 状态是 `incomplete` 或 `failed`，不能伪造 `final=true` 的 0 成本。

### Workflow Attribution 未来合同

Workflow 开发前必须先冻结 descendant AI call 的归因策略。以下字段或语义是未来 workflow / child Job 归因的候选合同，不是当前 Phase 1 已存在的数据库 schema。推荐第一版使用 root Job scope 聚合，同时保留 workflow / node / child Job 排障维度：

| 候选字段或语义 | 说明 |
|---|---|
| `scope_type="job"` | 公开 billing scope 仍是 Job |
| `scope_id=<root_job_id>` | descendant AI call 聚合到 root Job billing |
| `root_job_id` | 候选显式列：对外 root Job，必须可用于 root cost summary |
| `workflow_id` | 候选显式列：内部 workflow instance |
| `workflow_node_id` | 候选显式列：内部 node 归因 |
| `child_job_id` | 候选显式列：实际执行 child Job |
| `attempt_id` | 实际执行 attempt，继续用于排障和恢复 |

workflow / child Job 归因落地前，需要在两种实现之间做一次明确选择：

- 通过 migration 给 `ai_call_ledger_entries` 增加 `root_job_id`、`workflow_id`、`workflow_node_id`、`child_job_id` 等归因列。
- 保持 `ai_call_ledger_entries` 表结构更窄，并新增可重建、可校验的 attribution 辅助表或等价 scope 规则。

无论采用显式列还是等价 attribution 辅助表，都必须在开发前证明：

- `GET /jobs/{root_job_id}/billing` 能覆盖所有 descendant ledger 行。
- cost summary 能按 workflow / node / child Job 排障。
- reconciler 能在 child Job 终态后找到需要收敛的 ledger 行。
- terminal callback 和 terminal polling 使用同一聚合路径。

不能先让 child Job ledger 写入 child Job scope，再在终态时从 `job_result` 或 callback payload 反推 root cost。

### 辅助表和派生投影

Phase 1 不新增 AI 成本核心表，也不新增 `job_cost_summary`。下表用于说明当前 Job kernel 表与未来可选辅助表的边界；其中 workflow 相关对象和 `job_cost_summary` 只有在对应阶段落地时才进入当前事实。

| 对象 | 职责 | 不能承担的职责 |
|---|---|---|
| `job_aggregates` | 保存 root Job 对外状态、进度、result、error、callback 摘要 | 不保存 provider usage / cost 明细，不成为 cost 事实源 |
| `job_execution_attempts` | worker attempt、lease、heartbeat、retry 事实 | 不保存模型价格规则 |
| `dispatch_outbox` | 发布 Job attempt 执行消息 | 不代表 provider 调用已发生 |
| `callback_outbox` | 终态 callback 投递账本 | 不代表扣费成功或失败 |
| `workflow_instances` / `workflow_nodes` | 多节点编排、child Job 归属和 root projection 输入 | 不直接调用 provider，不计算 cost |
| `workflow_child_jobs` | 可选归属辅助表，连接 root workflow node 和 child Job | 不保存价格事实 |
| `job_cost_summary` / billing summary | 未来可选 read model，加速 Job 总费用查询 | 只能从 `ai_call_ledger_entries` 派生，可删除重建 |

如未来新增 `job_cost_summary` 或等价 summary 表，必须满足：

- 可由 ledger 重建。
- 不能作为扣费或成本事实源。
- 不能在 ledger incomplete 时伪造 final cost。
- 不能覆盖 ledger 中的 diagnostic status。

## Cost Summary 投影

公开 API 可以返回 Job 级总费用，但它只是 read model：

```text
ai_call_ledger_entries
  -> Job scope billing read model
  -> job.cost summary / callback cost summary
```

投影规则：

- 非终态 Job 的 `job.cost` 为 `null`。
- 终态 Job 的 `job.cost.final=true` 只能在 ledger 已收敛且 cost summary 可计算时返回。
- 如果所有 workflow item 已完成但 ledger 仍 pending / unknown / failed，root Job 不能伪造成功成本。
- callback payload 和 `GET /jobs/{job_id}` 中的 cost summary 必须来自同一聚合逻辑。
- `GET /jobs/{job_id}/billing` 可以继续作为排障型公开 billing 投影；vNext `job.cost` 是更窄的总费用展示投影。

## 与旧项目配置的关系

`cms-ai-manga-backend-master/backend/cost_config.yaml` 只作为覆盖面参考：

- 参考模型族覆盖：OpenAI image、Gemini image/TTS、Veo、Tongyi Wan、Kling、Vidu、Doubao Seedream / Seedance、BytePlus Dreamina。
- 参考计费维度：文本/图片 token、图片张数、音频/视频秒数、分辨率、音频开关、参考图/参考视频、区域或 provider 前缀。

不复制：

- `models:` 单文件结构。
- analytics DB 语义。
- 未知价格自动 `NULL` 的行为。
- provider key 命名方式。
- fallback 到默认价格的隐式策略。
- CNY 到 USD 的硬编码换汇方式。

本项目必须继续使用：

```text
app/core/models.yaml
app/core/pricing.yaml
pricing_ref
ai_call_ledger_entries
BillingEnvelope / Cost summary projection
```

## Poster Title Image 前置要求

在 `poster_title_image` 接入真实图片生成前，至少需要完成：

- `models.yaml` 能声明图片生成模型的公开 `model_id`、能力标签、输入/输出媒体类型和生成参数约束。
- `pricing.yaml` 能表达该图片模型的真实计费方式。
- `pricing_ref` 与 model catalog 启动期 fail-fast 校验。
- 图片 provider adapter 返回可标准化 usage，或由 adapter 根据 provider response 与 request context 生成 typed usage。
- `ai_call_ledger_entries` 能记录图片调用的 `usage_units`、raw usage、pricing ref 和 cost；如果图片路径需要持久化 `usage_kind` 或 `usage_schema_version` 用于查询/索引，必须先通过 migration 增加对应列。
- Job scope billing 能聚合 child Job / workflow descendant AI calls。
- terminal root Job cost summary 只从 ledger 聚合，不从 `job_result.items[]` 反推。

如果真实 provider 暂时无法返回足够 usage：

- 该 adapter 必须明确声明 usage 来源。
- 如果无法形成可信 usage，不允许返回 0 成本成功。
- ledger 必须进入 `failed` 或 `incomplete` 可诊断状态。

## Failure Modes

| 失败模式 | 正确处理 |
|---|---|
| `models.yaml` 缺少 `pricing_ref` | 启动或 verify fail-fast |
| `pricing_ref` 不存在 | 启动或 verify fail-fast |
| `pricing_ref` 与 model/provider 不匹配 | 启动或 verify fail-fast |
| provider 调用前 ledger pending 创建失败 | 不调用 provider，Job 失败或重试 |
| provider 成功但 usage 缺失 | ledger terminal failed，billing failed / incomplete |
| pricing 条件命中 `null` | cost calculation failed，不回退到 0 |
| ledger terminal update 失败 | 不重放 provider 调用；通过 recovery / 人工排障收敛 |
| workflow child Job 成功但 root scope 未聚合 descendant ledger | root cost summary 不得 final |
| callback 投递失败 | 不改变 Job cost、ledger 或终态 |

## Planned Work

### Phase 0: Attribution Readiness

- 通过 [`implementation-terminal-acceptance.md`](implementation-terminal-acceptance.md) 中的 Phase 0 readiness。
- 在成本边界内冻结 root scope cost aggregation 的查询路径。
- 冻结 ledger terminal convergence 规则：provider 已调用后不能重放 provider 修账，ledger incomplete / failed 不能伪造 `final=true` 的 0 成本。
- 冻结 cost summary projection 的单一来源：terminal polling、terminal callback 和 future `job.cost` 都从 ledger 聚合读取。

### Phase 1: 成本边界合同收口

- 明确 AI 层只做模型成本估算，不做真实扣费。
- 在文档和代码命名中区分 `cost estimate`、`billing projection` 和 `payment / charge`。
- 保持 `ai_call_ledger_entries` 是唯一 AI call usage / cost 事实源。
- 明确 Phase 1 不新增 AI 成本核心表，不新增 `job_cost_summary`，不要求修改当前 `ai_call_ledger_entries` 表结构。
- 明确 `job.cost`、callback cost 和 future summary table 都只是派生投影。
- 明确 `usage_kind`、`usage_schema_version` 如需持久化查询/索引，必须通过 migration 增加，不能写成当前已存在事实。

### Phase 2: Catalog fail-fast

- 扩展 `models.yaml` 的能力标签、媒体类型和生成参数约束。
- 扩展 `pricing.yaml` 的 typed pricing rule。
- 在启动、worker 启动和 `./scripts/verify.sh check` 中校验 model / pricing / prompt 一致性。
- `GET /models` 仍只暴露公开可选模型元信息。

### Phase 3: Typed pricing resolver

- 支持基础 pricing type：`per_token`、`per_image`、`per_second`、`per_call`。
- 支持条件维度：文本/图片 token 拆分、分辨率、音频开关、参考图/参考视频、区域或 provider 前缀。
- 使用 `PricingRule + UsageRecord + PricingContext` 计算 cost。
- 条件价格缺失、usage 缺失或 shape 不匹配时 fail-fast 或写入明确 failed / incomplete ledger。

### Phase 4: Workflow cost attribution

- 在 workflow / child Job 归因落地前，评估并选择：为 `ai_call_ledger_entries` 增加 `root_job_id`、`workflow_id`、`workflow_node_id`、`child_job_id` 等 attribution 字段，或使用等价 attribution 辅助表 / scope 规则。
- root Job cost summary 必须覆盖 descendant AI calls。
- workflow terminal、root Job terminal、cost summary 和 callback outbox 必须在同一终态投影路径中收敛。

### Phase 5: Poster Title Image adoption

- 接入图片生成 provider adapter。
- 实现图片 usage normalizer。
- 为 `poster_title_image` 模型补 `models.yaml` 和 `pricing.yaml`。
- 验证多 item child Job 的 ledger 聚合到 root Job cost summary。

## Acceptance

Phase 1 验收：

- Phase 1 验收只要求数据库/表边界已说明清楚：不新增 AI 成本核心表，不新增 `job_cost_summary`，`ai_call_ledger_entries` 是唯一 AI call usage / cost estimate 事实源。
- `job.cost`、callback cost 和 future summary table 都被定义为派生投影，不能成为 provider usage / pricing / cost 事实源。
- `usage_kind`、`usage_schema_version` 没有被写成当前已存在 DB 事实；如需持久化查询/索引，验收条件必须包含 migration。
- Phase 1 不要求 workflow descendant AI calls 已经落地，也不要求当前 `ai_call_ledger_entries` 增加 workflow / child Job attribution 字段。
- `ai_call_ledger_entries` 覆盖当前 provider call、usage、pricing、cost 和 diagnostic status；未来多模态或 workflow 归因不能被写成当前已实现事实。

后续阶段验收：

- [`implementation-terminal-acceptance.md`](implementation-terminal-acceptance.md) 中的 cost boundary gates 已经通过。
- `models.yaml` 和 `pricing.yaml` 的不一致会在启动或 verify 阶段失败。
- `pricing_ref` 是 model catalog 与 pricing catalog 的唯一绑定点。
- workflow / child Job 归因字段或等价辅助表必须在 Phase 4 前完成设计选择。
- workflow descendant AI calls 能按 root Job scope 聚合后，才可验收 Phase 4。
- `./scripts/verify.sh check` 覆盖 model registry、pricing registry、AI gateway facade、billing service 和相关 fail-fast 测试。

## Non-goals

- 不做真实扣费。
- 不做用户余额。
- 不做钱包、积分、信用额度或预授权。
- 不做支付渠道、退款、发票、财务总账或收入确认。
- 不做 provider 成本分析后台。
- 不暴露调用明细给普通调用方。
- 不新增第二套 AI call ledger。
- 不让 workflow kernel 直接调用 provider 或计算模型成本。
