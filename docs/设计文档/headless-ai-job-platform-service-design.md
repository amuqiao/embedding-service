# Headless AI Job Platform Service 设计文档

```text
Version: 0.1.0
Status: Proposed / Design Baseline Candidate
Date: 2026-06-22
Scope: model catalog v2, ai_call_logs, pricing config, structured_llm 示例 job_type
Change Policy: 进入开发后，任何模型目录合同、usage/cost 账本字段、pricing 规则、结构化 LLM 运行时、公开 API 字段或数据保留策略变化都必须升版本并记录变更原因。
Change Reason: 在现有 FastAPI + Taskiq AI Job 服务骨架上，定义下一阶段平台服务能力边界和实施顺序。
```

本文定义本项目从 AI Job 执行模板升级为无业务状态 Headless AI Job Platform Service 时，模型目录、模型调用账本、成本估算和结构化 LLM 示例能力的设计边界。

核心结论：

- 本服务可以作为 **Headless AI Job Platform Service**，但不是纯无状态 API 网关；在共享 PostgreSQL、Redis 和外部对象存储的前提下，API / worker 进程可以无状态横向扩展，Job、Attempt、Callback 和模型调用账本仍以 PostgreSQL 为事实源。
- `model catalog v2` 是本服务对外暴露和创建期校验的模型事实源；LiteLLM 只是模型调用适配层，LiteLLM Proxy 如接入也只能作为外部模型网关。
- `ai_call_logs` 是服务内部 usage ledger 和 cost estimate 账本，不是钱包、支付、发票、税务或最终对账系统。
- `pricing config` 只负责把 provider usage 转换为可审计的成本估算；价格缺失、模型缺失或配置漂移必须 fail-fast。
- `structured_llm` 只能作为一个真实 LLM `job_type` 示例，验证 Job / usage / cost / Callback 完整链路，不得引入第二套 Job 外壳或业务编排框架。

## 1. 文档职责

本文负责定义：

- 无业务状态 Platform Service 的服务边界。
- `model catalog v2` 的职责、配置结构和校验规则。
- `pricing config` 的职责、价格版本和成本估算规则。
- `ai_call_logs` 的账本模型、写入时机、脱敏边界和与 Job 状态的关系。
- 一个真实 `structured_llm` 示例 `job_type` 应如何走完整链路。
- 从 `cms-ai-manga-backend-master` 参考项目可借鉴的设计点，以及不应照搬的部分。
- 后续实现的分阶段路线和验证要求。

本文不负责：

- 用户系统、租户系统、项目管理、前端页面状态或业务步骤编排。
- 钱包扣费、授信额度、发票、税务、退款、支付渠道或最终财务对账。
- 生产部署平台、K8s、云平台 Secrets、CI/CD 发布流水线。
- 任一具体业务能力的 Prompt 内容、业务标签体系或业务流程。
- 长期保存模型 transcript、完整 Prompt、完整模型输出或供应商原始响应。

相关基础文档：

- [`../架构/project-standards-code-facts.md`](../架构/project-standards-code-facts.md)
- [`../架构/架构总览.md`](../架构/架构总览.md)
- [`taskiq-job-model-design.md`](taskiq-job-model-design.md)
- [`callback-job-unified-envelope-design.md`](callback-job-unified-envelope-design.md)
- [`../接口层/job-type-extension-standard.md`](../接口层/job-type-extension-standard.md)

## 2. 当前代码基线

当前项目已经具备以下基础：

```text
FastAPI API
  ├─ /models
  ├─ /prompt-templates
  ├─ POST /jobs
  └─ GET /jobs/{job_id}

Taskiq Job runtime
  ├─ jobs
  ├─ job_attempts
  ├─ callback_outbox
  ├─ job_events
  └─ reconciler_leases

AI runtime MVP
  ├─ app/core/models.yaml
  ├─ app/core/model_registry.py
  ├─ app/integrations/ai_gateway.py
  └─ LiteLLM acompletion()
```

当前限制：

- 现有 `models.yaml` 只覆盖文本模型的最小元数据和 LiteLLM model id，不包含价格、价格版本、模型调用账本策略或多模态能力矩阵。
- `generate_text()` 会读取 `prompt_tokens` 和 `completion_tokens`，但 token usage 没有落库，也没有进入 Job、Attempt、Event 或公开响应。
- 当前 Job 账本只表达 Job / Attempt / Callback / Event 状态，不是 AI usage/cost 账本。
- 当前 `jobs` 表没有顶层 `model_id`；模型选择属于 runtime snapshot 中的 `runtime_fields.model_id`。
- 当前内置示例 `job_type` 不调用真实模型；`workflow-smoke` 也不访问真实供应商。

因此，本文后续所有 `ai_call_logs`、`pricing config`、`structured_llm` 均为待实现设计，不是当前已落地事实。

## 3. 目标心智模型

调用方把本服务理解为“可恢复的 AI Job 执行层”，而不是业务流程系统或支付系统。

```text
业务后端 / BFF
  ├─ 管理用户、项目、业务状态和业务重试
  ├─ 选择 job_type、model_id 和业务参数
  ├─ 调用 POST /jobs
  ├─ 轮询 GET /jobs/{job_id}
  └─ 接收终态 Callback

Headless AI Job Platform Service
  ├─ 鉴权并确定 caller_id
  ├─ 校验 job_type / job_params / callback / model_id
  ├─ 冻结 runtime snapshot
  ├─ 创建 Job 和 Attempt
  ├─ Worker 领取 Attempt
  ├─ 写入 ai_call_logs pending row
  ├─ 调用 LiteLLM / provider adapter
  ├─ 解析 usage 并按 pricing snapshot 估算成本
  ├─ 更新 ai_call_logs terminal row
  ├─ 校验 canonical result 并投影 public result
  ├─ 收敛 Job 到 succeeded / failed
  └─ 投递 Callback

外部计费 / 财务系统
  ├─ 可读取或接收 usage/cost 估算摘要
  ├─ 自己处理用户余额、账单、发票和对账
  └─ 不反向改写 Job 执行事实
```

设计分层：

| 层级 | 回答的问题 | 事实源 |
|---|---|---|
| `JobEnvelope` | Job 当前状态、公开结果或公开错误是什么 | `jobs` |
| `ai_call_logs` | 哪次模型调用发生了，供应商返回了什么 usage，估算成本是多少 | `ai_call_logs` |
| `pricing snapshot` | 这次调用按哪个价格版本估算 | `pricing config` + 调用时冻结字段 |
| 外部 billing | 谁应该付费、是否扣款、是否开票、是否退款 | 外部业务 / 财务系统 |

`JobEnvelope` 不应为了 usage/cost 增加公共顶层字段。后续如果需要对调用方公开成本摘要，应通过明确版本化的 `job_result` 字段、专用内部查询接口或外部运营导出实现，而不是污染所有 `job_type` 的公共外壳。

## 4. 设计原则

### 4.1 目录负责稳定语义，LiteLLM 负责执行适配

`model catalog v2` 是本服务的模型事实源，负责：

- 对外 `model_id`。
- 模型启停。
- 模型能力声明。
- 创建期和 worker 侧 gate。
- LiteLLM model id 映射。
- 必需环境变量。
- pricing 引用。
- 治理字段。

LiteLLM SDK 负责：

- provider 协议适配。
- 请求发送。
- timeout、retry 等调用参数。
- 返回 provider response 和 usage。

LiteLLM Proxy 可以作为外部模型网关使用，但不应把以下概念直接抬升为本服务公共合同：

- virtual key 管理。
- team / user budget。
- fallback chain。
- provider routing policy。
- proxy 管理 API。
- provider 全量私有参数面。

本服务可以记录 `litellm_model` 和 `provider_model`，但公开模型合同必须以本服务自己的 `model_id` 为准。

### 4.2 usage/cost 是内部账本，不是真实收费系统

`ai_call_logs` 和 `pricing config` 只提供可审计的成本估算。

它们可以支持：

- Job 排障。
- 成本归因。
- 调用方维度用量统计。
- 供应商异常排查。
- 给外部 billing 系统提供 charge input。

它们不负责：

- 扣余额。
- 信用额度。
- 支付渠道。
- 发票和税务。
- 退款。
- 供应商账单差异裁决。
- 用户级账单展示。

原因是服务内 PostgreSQL 只能证明“本服务尝试过什么模型调用并记录到什么 usage”，不能凭空恢复供应商侧是否真实扣费、是否补偿、是否按商业合同折扣结算。

### 4.3 不 silent fallback

以下情况必须快速失败，不能静默降级：

- `models.yaml` 结构非法。
- 模型重复。
- 默认模型未启用。
- 启用模型缺少必需字段。
- 启用模型引用不存在的 pricing。
- `requires_env` 指向服务不支持读取的环境变量。
- 创建 Job 时请求不可用模型。
- 模型不支持 `structured_output`，却用于 `structured_llm`。
- provider usage 缺失但 pricing 策略要求 usage。
- 成本解析失败。
- 账本 pending row 写入失败。
- runtime snapshot hash 不匹配。

不允许：

- 自动换模型。
- 自动换 provider。
- 缺价格时当作 `0`。
- 缺 usage 时当作 `0 token`。
- 结构化输出解析失败后返回空对象。
- 记录日志失败后继续调用真实模型。

### 4.4 日志不保存 transcript

默认不在 `ai_call_logs` 中保存完整 Prompt、完整输入、完整输出、供应商原始响应或密钥。

允许保存：

- `request_hash`。
- `response_hash`。
- `input_size_bytes`。
- `output_size_bytes`。
- `prompt_template_id`。
- `prompt_version`。
- `runtime_ref`。
- 脱敏后的 usage。
- 脱敏后的 provider error 摘要。

如果某个 `job_type` 需要公开模型输出，应通过 `canonical_result`、`public_result` 或对象存储 artifact 表达，而不是把 `ai_call_logs` 当成长期 transcript 仓库。

## 5. model catalog v2

### 5.1 职责

`model catalog v2` 是以下流程的共同事实源：

- `GET /models` 能力发现。
- 创建 Job 时校验 `model_id` 是否可用。
- Worker 调用前再次 gate，避免过期 runtime snapshot 烧真实 provider 请求。
- pricing consistency 检查。
- Prompt / structured output 能力检查。

参考项目 `cms-ai-manga-backend-master` 中值得借鉴的是“provider SDK typed catalog + 本服务 allowlist overlay”的分层思路。迁移到本项目时，应明确服务自有 YAML 才是公开 `/models`、创建期校验和 worker gate 的唯一事实源；SDK 或 adapter 元数据只能作为生成、校验或人工维护输入，不能直接决定公开合同。当前项目不应强绑定该参考项目的私有 `aigc` SDK，但可以保留同样的目录边界。

### 5.2 配置结构建议

`MODEL_CONFIG_PATH` 仍指向 YAML。v2 建议结构：

```yaml
version: "2"
models:
  - id: gpt-4.1-mini
    name: GPT-4.1 mini
    provider: openai
    litellm_model: openai/gpt-4.1-mini
    enabled: true
    status: stable
    kind: text
    capabilities:
      chat: true
      structured_output: true
      tool_calling: false
    context_window: 1047576
    output_token_limit: 32768
    requires_env:
      - OPENAI_API_KEY
    generation:
      temperature: 0.7
      num_retries: 0
      drop_params: true
    pricing_ref: openai:gpt-4.1-mini@2026-06-22
    usage_policy:
      usage_required: true
    exposure:
      public: true
      use_cases:
        - structured_llm
    governance:
      owner: platform
      review_after: "2026-09-22"
      replacement: null
    notes: ""
```

字段规则：

| 字段 | 规则 |
|---|---|
| `id` | 对外稳定 `model_id`，调用方和 `job_params` 只使用它。 |
| `provider` | 本服务内部 provider 分类，不必等于 LiteLLM 前缀。 |
| `litellm_model` | LiteLLM SDK 调用时使用的模型名。 |
| `enabled` | `false` 时不得出现在 `/models`，创建期也不可用。 |
| `status` | `experimental`、`stable`、`deprecated`、`disabled` 等治理状态。 |
| `kind` | v1 固定为 `text`；`image`、`audio`、`video` 留到后续扩展。 |
| `capabilities` | 创建期和 runtime gate 使用，不依赖调用方自报。 |
| `requires_env` | 必需密钥或 endpoint 环境变量，必须可由配置层显式读取。 |
| `generation` | 稳定调用参数，不暴露 provider 私有全量参数。 |
| `pricing_ref` | 指向 pricing config 中的价格版本。 |
| `usage_policy` | v1 固定要求 provider 返回 usage；后续如支持非计费用途再扩展。 |
| `exposure` | 控制 `/models` 是否公开，以及可选 use case 过滤。 |
| `governance` | 维护责任和复核时间，不影响 runtime。 |

### 5.3 可用性判定

模型只有同时满足以下条件才可用：

```text
enabled == true
status != disabled
所有 requires_env 都存在且非空
pricing_ref 存在且通过 pricing config 校验
capabilities 满足目标 job_type 要求
generation 配置合法
```

`GET /models` 只返回可用且 `exposure.public=true` 的模型。公开字段应避免暴露密钥、内部 endpoint 和完整 provider 参数。

创建 Job 时：

- `job_params.model_id` 必须存在于可用模型集合。
- `structured_llm` 必须要求 `capabilities.structured_output=true`。
- 若模型 `status=deprecated`，可以继续允许创建，但 `/models` 应给出 `replacement` 提示；是否阻断由配置显式决定。

Worker 调用前：

- 必须用 runtime snapshot 中的 `model_id` 再次查询当前模型目录。
- 如果模型已被禁用，应让 Job 失败为稳定错误，不得自动替换。
- 如果模型仍可用，应使用调用时目录字段和 runtime snapshot 共同构造请求。

## 6. pricing config

### 6.1 职责

`pricing config` 负责把 provider usage 转换为内部成本估算。

它必须满足：

- 配置化新增价格，不改业务代码。
- v1 所有生产启用模型必须能找到价格；不提供 `cost_required=false` 的生产路径。
- 每次调用冻结 `pricing_ref`、`pricing_version` 和计算输入摘要。
- 历史 `ai_call_logs` 不因价格文件更新而重新解释。
- 金额使用 decimal，不使用 float 做最终存储。

### 6.2 配置结构建议

建议新增独立 `PRICING_CONFIG_PATH`，默认可为 `app/core/pricing.yaml`。

```yaml
version: "2026-06-22"
currency: USD
prices:
  openai:gpt-4.1-mini@2026-06-22:
    model_id: gpt-4.1-mini
    provider: openai
    provider_model: gpt-4.1-mini
    pricing_type: per_token
    input_per_1m: "0.40"
    cached_input_per_1m: "0.10"
    output_per_1m: "1.60"
```

v1 只支持 `per_token`：

| 类型 | 用途 |
|---|---|
| `per_token` | 文本和结构化 LLM 模型。 |

参考项目中的 `CostResolver` 支持 `per_token`、`per_image`、`per_second`、`per_call` 和 provider-prefixed key，值得借鉴。但本项目 v1 只落地 `per_token`，其它 pricing 类型进入后续扩展。参考项目对缺配置和解析失败返回 `None`，本项目不应照搬；对本项目而言，启用模型的价格缺失是配置错误，不是运行时可忽略告警。

### 6.3 成本估算规则

成本估算输入：

```text
model catalog:
  model_id
  provider
  litellm_model
  pricing_ref

provider response:
  usage
  usage_details
  cached token details

runtime context:
  job_id
  attempt_id
  job_type
  step_name
  request metadata needed for pricing
```

输出：

```text
cost_amount
currency
pricing_ref
pricing_version
usage_detail
usage_units
cost_calculation_status
```

缺 usage 策略：

- v1 生产启用模型固定要求 `usage_required=true`；provider response 缺 usage 时，模型调用应被视为失败，Job 写入 `MODEL_USAGE_MISSING` 或等价错误。
- 不得把缺 usage 解释为 `0`。

缺价格策略：

- 启用模型引用不存在的 `pricing_ref` 时，服务启动失败。
- `pricing_ref` 指向价格类型与模型能力不匹配时，服务启动失败。
- 成本计算异常时，当前模型调用失败；不得把 cost 写成 `0` 或 `null` 后继续成功。

## 7. ai_call_logs

### 7.1 职责

`ai_call_logs` 是每次 provider / LiteLLM 调用的内部账本。它用于回答：

- 哪个 Job 的哪个 attempt 发起了模型调用。
- 调用了哪个服务模型和 provider 模型。
- 模型调用处于什么状态。
- provider 返回了什么 usage 摘要。
- 按哪个 pricing snapshot 计算了多少成本。
- 调用失败时，失败发生在 submit、provider、usage 解析、成本估算还是账本写入阶段。

它不用于：

- 保存完整 Prompt。
- 保存完整模型输出。
- 保存完整 provider response。
- 长期保存业务结果。
- 承担外部支付系统的最终事实。

### 7.2 表结构建议

建议新增 Alembic 迁移，创建 `ai_call_logs`。

```text
ai_call_logs
  id uuid primary key
  job_id uuid not null
  attempt_id uuid null
  caller_id varchar(64) not null
  job_type varchar(96) not null
  step_name varchar(128) not null
  operation varchar(64) not null

  model_id varchar(128) not null
  provider varchar(64) not null
  provider_model varchar(255) not null
  litellm_model varchar(255) null

  status varchar(32) not null
  failure_phase varchar(32) null
  error_code varchar(96) null
  error_message varchar(512) null

  request_hash varchar(128) null
  response_hash varchar(128) null
  input_size_bytes integer null
  output_size_bytes integer null

  usage_detail jsonb null
  usage_units jsonb null
  cost_amount numeric(20, 8) null
  currency varchar(8) null
  pricing_ref varchar(255) null
  pricing_version varchar(64) null
  cost_calculation_status varchar(32) not null

  started_at timestamptz not null
  completed_at timestamptz null
  duration_ms integer null
  created_at timestamptz not null
  updated_at timestamptz not null
```

建议索引：

```text
(job_id, created_at)
(attempt_id, created_at)
(caller_id, created_at)
(model_id, created_at)
(provider, provider_model, created_at)
(status, created_at)
```

### 7.3 写入时机

账本写入必须包住真实 provider 调用。

```text
Worker 已成功 claim attempt
  ↓
构造模型调用请求
  ↓
写 ai_call_logs pending row
  ↓
pending row 写入成功后，才允许调用 provider
  ↓
调用 LiteLLM / provider adapter
  ↓
提取 usage
  ↓
计算 cost estimate
  ↓
更新 ai_call_logs succeeded / failed
  ↓
继续解析模型输出和收敛 Job
```

规则：

- pending row 写入失败时，不得调用 provider。
- provider 调用成功但 terminal row 更新失败时，必须做短重试；重试仍失败时，Job 应进入明确失败状态，并保留 pending row 供 recovery 或人工排查。
- 不得因账本更新失败而自动重放 provider 调用；真实模型可能已经扣费，盲目重试会扩大成本。
- 一个 Job 可以有多条 `ai_call_logs`，例如多阶段 Prompt、分块、merge 或成功前副作用调用。
- Job 成本摘要可以由 `ai_call_logs` 聚合得出，但聚合结果不应成为 Job 状态机的唯一事实。

### 7.4 与 JobEnvelope 的关系

首版不把 `usage` 或 `cost_estimate` 加入通用 `JobEnvelope` 顶层。

允许的表达方式：

- 内部只读运维查询接口按 `job_id` 查询 `ai_call_logs`。
- 特定 `job_type` 的 `public_result` 可以选择公开成本摘要，但必须作为该能力的明确业务合同。
- Callback payload 默认不携带完整 usage 或 cost estimate；如果某能力公开成本摘要，轮询和 Callback 必须使用同一 public result 投影。

不允许：

- 修改所有 Job 公共外壳增加 `cost` 或 `cost_estimate`。
- 把 provider usage 原样暴露给普通调用方。
- 把 `ai_call_logs` 中的内部错误或供应商响应直接塞进 `job_error.details`。

### 7.5 保留期

MVP 中 `ai_call_logs` 默认与 Job 保留期对齐，不作为长期财务账本保存。

如果需要长期成本分析，应通过外部数据仓库、运营导出或 billing 系统接收脱敏后的聚合事件。这样可以避免 Job 服务承担长期 transcript、审计归档和财务对账职责。

## 8. structured_llm 示例 job_type

### 8.1 目标

`structured_llm` 是第一个真实调用模型的模板级示例能力，用于验证：

- 创建期模型 gate。
- Prompt 模板加载和版本校验。
- LiteLLM 结构化输出。
- `ai_call_logs` pending / terminal 写入。
- usage 提取。
- cost estimate。
- canonical result / public result 投影。
- Callback 成功和失败投递。
- workflow smoke 或 mock smoke。

它不是业务能力，不负责漫画、图像、视频、资产解析或具体业务流程。

### 8.2 job_params 合同建议

```json
{
  "model_id": "gpt-4.1-mini",
  "template_id": "structured_llm.echo_json",
  "template_vars": {
    "instruction": "提取输入文本的主题和关键词"
  },
  "source": {
    "inline": {
      "text": "待处理文本"
    }
  }
}
```

规则：

- `model_id` 必须来自 `model catalog v2`。
- `template_id` 必须来自 Prompt registry。
- `template_vars` 只能包含模板声明的变量。
- `source` 沿用现有 inline / OSS 输入边界。
- 输出 schema 由服务端 PromptSpec 绑定，不允许调用方任意上传 JSON Schema 驱动模型输出。

### 8.3 runtime fields

`runtime_job_fields()` 应冻结：

```text
model_id
template_id
prompt_version
prompt_payload 或 prompt_ref
response_schema_name
output_target
```

Worker 执行时必须从 runtime snapshot 读取这些字段，不能重新解释调用方原始请求。

### 8.4 执行流程

```text
structured_llm JobExecutor
  ↓
读取 normalized job_params
  ↓
读取 runtime snapshot
  ↓
Prompt registry 渲染 system / user
  ↓
校验 model.capabilities.structured_output
  ↓
ai_call_logs pending
  ↓
LiteLLM structured completion
  ↓
提取 usage + cost estimate
  ↓
ai_call_logs succeeded
  ↓
Pydantic response schema 校验
  ↓
canonical result
  ↓
public result
  ↓
Job succeeded + Callback
```

结构化输出失败：

- provider 调用失败：`MODEL_CALL_FAILED`。
- provider 超时：`MODEL_CALL_TIMEOUT` 或 `JOB_TIMEOUT`，按现有超时分层决定。
- 输出无法通过 Pydantic schema：`MODEL_OUTPUT_INVALID`。
- usage 缺失且该模型要求 usage：`MODEL_USAGE_MISSING`。
- cost estimate 失败且该模型要求 cost：`MODEL_COST_ESTIMATION_FAILED`。

### 8.5 Prompt registry

参考项目中 `PromptSpec + prompt.yaml + schema.py` 启动期交叉校验的设计值得借鉴。本项目已有 `prompts.yaml` 和 `/prompt-templates`，后续可演进为更强的 Prompt registry，但必须保持以下规则：

- Prompt 模板和输出 schema 由服务端管理。
- 模板版本必须进入 runtime snapshot。
- Prompt 变量必须声明并校验。
- Prompt registry 启动期 fail-fast。
- `job_type` registry 仍是 Job 类型事实源，Prompt registry 不是 Job 类型注册表。

首版可以继续使用单 YAML；当结构化能力增加时，再拆成目录式 Prompt registry。

## 9. 参考项目借鉴与改进

本设计参考了本地项目 `cms-ai-manga-backend-master` 的已实现能力。可借鉴点和改进点如下。

### 9.1 可借鉴

| 参考设计 | 借鉴方式 |
|---|---|
| provider catalog + allowlist overlay | `model catalog v2` 可借鉴分层思路，但本服务 YAML 是公开合同和 gate 的唯一事实源。 |
| `/models` 同步能力发现 | 本项目继续保持 `/models` 不走异步 Job。 |
| `per_token / per_image / per_second / per_call` pricing | v1 只采用 `per_token`；其它类型作为后续扩展参考。 |
| provider-prefixed price key | 用于同名模型在不同 provider 或区域有不同价格。 |
| 每次 provider call 写日志 | 演进为 `ai_call_logs` usage ledger。 |
| PromptSpec 绑定模板和 Pydantic schema | 演进本项目结构化 LLM 示例。 |
| Job terminal 成本聚合 | 可作为内部运维查询或特定 job_type public result 的派生值。 |

### 9.2 不应照搬

| 参考实现行为 | 本项目处理 |
|---|---|
| 私有 `aigc` SDK 强绑定 | 只借鉴抽象，不引入私有 SDK 作为模板核心依赖。 |
| 成本缺配置返回 `None` | 启用模型缺价格必须 fail-fast，不能静默返回未知成本。 |
| AIGC logger 吞掉 DB 写入异常 | 如果 `ai_call_logs` 是账本，pending 写失败不得调用 provider。 |
| 保存完整 request / response | 默认只保存 hash、size、usage、成本和脱敏错误摘要。 |
| 漫画业务 capability 和 Prompt | 不迁移业务内容，只迁移设计模式。 |
| image / audio / llm / video 多 broker 固定拆分 | 本项目先保留现有 Taskiq 结构，未来按容量隔离再设计队列角色。 |
| raw SQL migration | 本项目继续使用 Alembic。 |

## 10. 配置与启动校验

启动期必须执行以下检查：

```text
model catalog:
  [ ] YAML version 支持
  [ ] models 是列表
  [ ] id 唯一
  [ ] enabled 模型字段完整
  [ ] DEFAULT_MODEL_ID 指向 enabled 模型
  [ ] requires_env 全部是 Settings 支持读取的 key
  [ ] capabilities 合法
  [ ] generation 合法
  [ ] pricing_ref 存在

pricing config:
  [ ] YAML version 支持
  [ ] price key 唯一
  [ ] pricing_type 合法
  [ ] decimal 字段可解析
  [ ] currency 合法
  [ ] model_id / provider / provider_model 与 catalog 一致
  [ ] enabled 模型都有 pricing

prompt registry:
  [ ] template_id 唯一
  [ ] 模板变量声明完整
  [ ] response schema 可反查
  [ ] structured_llm 引用的模板存在

job_type registry:
  [ ] structured_llm schema 登记
  [ ] runtime fields schema 登记
  [ ] error codes 登记
  [ ] workflow smoke 覆盖
```

任何检查失败都应阻止服务启动或阻止 Job 创建，不允许降级启动。

## 11. API 表面

首版外部 API 不新增必选端点。已有端点含义演进如下：

| 端点 | 演进 |
|---|---|
| `GET /models` | 返回 `model catalog v2` 的公开可用模型和能力摘要。 |
| `GET /prompt-templates` | 返回结构化 LLM 可用模板摘要，不返回完整敏感 Prompt。 |
| `POST /jobs` | 支持 `structured_llm` 示例 `job_type`。 |
| `GET /jobs/{job_id}` | 仍返回统一 `JobEnvelope`，不默认暴露完整 usage/cost。 |

后续可选的内部运维查询形态：

```text
GET /internal/jobs/{job_id}/ai-call-logs
只读脚本或数据库视图按 caller_id / since / until 聚合 usage
```

这些查询如果实现，必须明确为内部运维能力，并遵守鉴权、脱敏和保留期规则。它们不属于普通调用方 Job 合同。v1 优先考虑只读脚本或数据库视图，避免过早承诺新的 HTTP API。

## 12. 数据一致性

### 12.1 模型调用和账本

`ai_call_logs` pending row 是调用真实 provider 前的前置条件。这样可以避免“供应商已经扣费，但服务完全没有调用痕迹”的最坏情况。

```text
pending 写失败:
  不调用 provider
  Job failed 或 attempt failed

provider 调用失败:
  更新 ai_call_logs failed
  Job failed 或按 job_type retry_policy 处理

provider 调用成功但 usage/cost 失败:
  更新 ai_call_logs failed
  Job failed

provider 调用成功且账本成功:
  更新 ai_call_logs succeeded
  继续 result schema 校验
```

### 12.2 retry 策略

真实模型调用可能产生费用，因此默认不做立即自动重试。

允许重试的前提：

- 错误发生在 provider 调用前，例如 pending 写入失败、创建期校验失败，不会产生外部成本。
- provider 明确返回未执行或未计费的错误，并且错误分类被 `job_type` retry policy 显式允许。
- retry 会创建新的 ai_call log，而不是覆盖旧调用。

不允许：

- provider 已经返回成功后，因为后续解析或写结果失败而自动重放模型调用。
- usage/cost 缺失时自动重放模型调用。
- Callback 失败触发模型重试。

## 13. 安全与隐私

`ai_call_logs` 不得保存：

- API key。
- Authorization header。
- Callback signing secret。
- 完整 Prompt。
- 完整模型输出。
- 完整 provider 原始响应。
- 大输入文本。
- 私有对象存储签名 URL。
- SQL、堆栈或内部环境变量。

错误摘要规则：

```text
error_code:
  使用已登记 reason。

error_message:
  最长 512 字符。
  不含密钥、Prompt、模型全文、请求头。

usage_detail:
  只保留 provider usage 中用于成本估算的字段。

request_hash / response_hash:
  使用 sha256:<hex>。
```

如果必须保留调试样本，应使用独立的、显式开启的本地开发开关，且不得进入生产默认配置。

## 14. 分阶段实施建议

### Phase 1: 文档和合同

- 新增本文。
- 明确 `usage ledger`、`cost estimate`、`pricing snapshot` 术语。
- 确认不把 cost/billing 做进公共 Job 外壳。

### Phase 2: model catalog v2 + pricing config

- 扩展 `models.yaml` 到 v2。
- 新增 `pricing.yaml`。
- 新增 registry consistency 检查。
- 补模型和价格 drift tests。
- 保持 `/models` 兼容或显式升版本。

### Phase 3: ai_call_logs

- 新增 Alembic 迁移。
- 新增 ORM model 和 Repository。
- 在 AI gateway / LLM service 中包裹 provider 调用。
- 写入 pending / succeeded / failed。
- 补 usage extraction 和 cost estimate 单测。

### Phase 4: structured_llm 示例 job_type

- 新增 Params / RuntimeFields / CanonicalResult / PublicResult schema。
- 新增 Prompt 模板和响应 schema。
- 新增 `JobExecutor`。
- 使用 LiteLLM structured output。
- 使用 mock provider 做 workflow smoke。
- 验证 Callback payload 与轮询 public result 一致。

### Phase 5: 运维查询和导出

- 按需新增只读脚本、数据库视图或内部 usage 查询。
- 按需导出聚合事件给外部 billing / analytics。
- 不在本服务内实现支付和发票。

### Future: 多模态和其它计价方式

- `image`、`audio`、`video` 模型能力。
- `per_image`、`per_second`、`per_call` pricing。
- provider / 区域 / 分辨率 / 参考素材等更复杂价格矩阵。
- 按能力类型拆分 Taskiq 队列或 worker 角色。

这些能力不进入 v1 必做范围，必须在 `structured_llm + per_token` 链路稳定后单独设计和验证。

## 15. 验证标准

实现阶段必须至少覆盖：

```text
配置:
  [ ] model catalog v2 合法配置可启动
  [ ] 重复 model id 启动失败
  [ ] enabled 模型缺 pricing_ref 启动失败
  [ ] pricing_ref 指向不存在价格启动失败
  [ ] requires_env 不支持读取时启动失败

成本:
  [ ] per_token 输入/输出/cached token 计算正确
  [ ] 缺 usage 不写 0
  [ ] 缺价格不写 0

账本:
  [ ] provider 调用前写 pending row
  [ ] provider 成功后写 succeeded row
  [ ] provider 失败后写 failed row
  [ ] pending 写失败不调用 provider
  [ ] terminal 更新失败不自动重放 provider 调用

structured_llm:
  [ ] 创建期拒绝不可用模型
  [ ] 创建期拒绝不支持 structured_output 的模型
  [ ] 输出 schema 校验失败进入 MODEL_OUTPUT_INVALID
  [ ] usage 和 cost estimate 成功落账
  [ ] Job succeeded 后 Callback 使用同一 public result

安全:
  [ ] ai_call_logs 不保存完整 Prompt
  [ ] ai_call_logs 不保存密钥
  [ ] error_message 有长度和脱敏限制
```

项目级验证仍优先使用：

```bash
./scripts/verify.sh check
```

涉及真实 Job workflow 后，还应补充：

```bash
./scripts/dev.sh start
./scripts/verify.sh workflow-smoke
./scripts/dev.sh stop
```

如果 `structured_llm` 需要 mock provider，应优先把 mock smoke 做成可重复、不访问真实供应商、不产生真实费用的模板级验证。

## 16. 开放问题

进入实现前需要确认：

1. `ai_call_logs` 是否需要单独 TTL，还是严格跟随 Job TTL。
2. 是否允许 dev/test 模型显式跳过成本估算，还是所有启用模型都必须可估算成本。
3. `/models` 是否公开 `pricing_available` / `cost_estimate_available` 摘要。
4. 是否需要内部 usage 查询端点，还是只通过只读脚本或数据库视图排查。
5. LiteLLM Proxy 是否只作为外部 endpoint，还是需要在部署文档中提供可选接入示例。

这些问题未确认前，不应把本文升为 Accepted。
