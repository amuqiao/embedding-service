# AI Gateway Layer 设计文档

```text
Version: 0.1.0
Status: Accepted / Target Design Baseline
Date: 2026-06-23
Scope: AI gateway facade, model catalog, LiteLLM adapter, AI call ledger, pricing config, billing read models, Job scope projection
```

本文定义本项目后续 AI 网关层的目标设计：先以“每次模型调用”为核心建立可审计的调用、usage 和 cost 账本，再让 Job、同步接口或内部任务作为上层归属 scope 使用这套能力。

> AI gateway 统一承接所有 LLM 调用并落 usage/cost 账本；Job 和 HTTP 请求只是不同 scope；BillingEnvelope 从账本按 scope 聚合，支持用 job_id 查询单个 Job 的完整 LLM 消耗。

## 文档职责

本文负责说明：

- AI gateway 在 FastAPI 4 层架构中的位置。
- `model catalog`、LiteLLM adapter、pricing config、AI call ledger 和 billing read model 的事实源关系。
- AI call ledger 如何表达一次真实模型调用，而不是强绑定某一种上层入口。
- Job 如何作为 `scope_type="job"` 的一种消费者获得 Job 级 billing。
- 非 Job 接口后续如何复用同一套 AI cost 账本。

本文不负责：

- 用户余额、扣款、发票、税务、退款或最终财务对账。
- LiteLLM Proxy 的部署、virtual key、team budget 或 proxy 管理 API。
- 任一具体业务 `job_type` 的 Prompt 内容、业务参数或业务流程。
- 当前代码事实说明；当前实现事实以 [`../架构/project-standards-code-facts.md`](../架构/project-standards-code-facts.md) 为准。

## 1. 当前基线

当前代码已经落地 AI gateway / billing 的首个 Job scope 实现切片。当前事实以 [`../架构/project-standards-code-facts.md`](../架构/project-standards-code-facts.md) 为准；当前 AI gateway / runtime adapter 内部边界以 [`../架构/ai-gateway-runtime-boundary.md`](../架构/ai-gateway-runtime-boundary.md) 为准。本文只保留目标边界和后续扩展规则，不重复维护当前模块清单。

当前仍未开放或未完整落地的能力：

- 通用 `GET /billing/scopes/{scope_type}/{scope_id}` 公开查询。
- caller 时间窗口 billing 聚合、批量导出或长期 warehouse 对接。
- AI call ledger 的长期财务账本语义；当前仍是成本估算和审计 ledger。
- provider 调用成功但 terminal ledger 更新失败后的完整人工结算或外部对账；当前 recovery 只把长期 pending ledger 行收敛为 failed / unknown，不能通过重放 provider call 修复。
- metrics endpoint 和全量结构化日志。
- 当前内置 `workflow-smoke` 不调用真实模型。

因此，本文后续章节中的通用 scope 查询、非 Job scope、批量导出、长期保留和运维增强仍是目标设计；已经落地的 Job scope billing 事实以代码和 `project-standards-code-facts.md` 为准。

## 2. 核心定调

AI cost 的事实源应围绕“真实模型调用”建立，而不是围绕 Job 建立。

```text
AI gateway facade
  ↓
每次真实 LiteLLM / provider call
  ↓
ai_call_ledger_entries 一行调用账本
  ↓
scope billing 读模型
  ├─ Job billing
  ├─ sync API billing
  ├─ internal task billing
  └─ caller/time-window export
```

关键判断：

- `ai_call_ledger_entries` 是 AI call ledger 的物理表；ORM 类名当前仍为 `AiCallLog`。
- AI call ledger 不是 `job_aggregates` 表扩展。
- `job_id` 是一种可选归属上下文，不是 AI cost 的唯一主键。
- LiteLLM 是执行适配器，不是本服务的计费事实源。
- `pricing config` 是成本估算规则，不是真实扣款系统。
- Billing read model 只从已冻结的 AI call ledger 聚合，不用当前价格文件重算历史调用。
- Job billing 是 `scope_type="job"` 的公开投影，不是独立账本。

## 3. 分层心智模型

```text
调用入口
  ├─ JobExecutor
  ├─ 同步 HTTP 能力接口
  ├─ 内部运营任务
  └─ 后台批处理
        ↓
AI gateway facade（应用服务层）
  ├─ 校验 model_id
  ├─ 绑定 scope
  ├─ 创建 pending AI call ledger row
  ├─ 调用 LiteLLM adapter
  ├─ 提取 usage
  ├─ 按 pricing snapshot 估算 cost
  └─ 更新 terminal AI call ledger row
        ↓
调用方拿到模型输出或稳定错误

billing query / export
  └─ 从 AI call ledger 聚合，不反向控制调用入口状态
```

对象职责：

| 对象 | 回答的问题 | 事实源 |
|---|---|---|
| `model catalog` | 哪些服务模型可用，映射到哪个 provider model，支持什么能力 | `app/core/models.yaml` 或后续 registry |
| LiteLLM adapter | 如何把一次请求发送给 provider，并拿回 provider response / usage | `app/integrations/` |
| `pricing config` | usage 如何换算成 cost estimate | `app/core/pricing.yaml` 或后续 registry |
| AI call ledger | 哪次模型调用发生了，usage 和 cost estimate 是多少 | `ai_call_ledger_entries` |
| billing read model | 某个 scope 或时间窗口的 usage/cost 聚合是什么 | AI call ledger 聚合 |
| Job runtime | Job 状态、Attempt、Dispatch、结果投影和 Callback | `job_aggregates`、`job_execution_attempts`、`dispatch_outbox`、`callback_outbox` |

## 4. 架构落位

AI gateway 不新增第五层，也不引入 `app/domain/`。

| 设计对象 | 目录落位 | 职责 |
|---|---|---|
| AI gateway facade | `app/services/` | 对上层入口提供统一模型调用门面，编排 model gate、ledger、adapter、usage extraction 和 cost estimate。 |
| LiteLLM / provider adapter | `app/integrations/` | 只负责 provider 协议适配、超时、错误归一化和 usage 提取所需原始响应。 |
| model catalog / pricing config | `app/core/` | 维护模型、能力、provider 映射、价格版本和启动期 fail-fast 校验。 |
| AI call ledger repository | `app/repositories/` | 封装 pending / terminal 写入、按 scope 聚合、导出查询和行级筛选。 |
| AI call ledger ORM | `app/models/` | ORM 类名当前为 `AiCallLog`，物理表为 `ai_call_ledger_entries`。 |
| billing schema | `app/schemas/` | 定义唯一公开 `BillingEnvelope`、response data wrapper、状态、错误和版本化合同。 |
| Job 使用方 | `app/jobs/` + `app/services/` | `JobExecutor` 通过 gateway facade 调用模型，并传入 `scope_type="job"`。 |

稳定依赖方向：

```text
api/routes 或 jobs/runner
  -> app/services/ai_gateway_facade
  -> app/core model/pricing registry
  -> app/repositories AI call ledger
  -> app/integrations LiteLLM adapter

billing service
  -> app/repositories AI call ledger
  -> app/schemas BillingEnvelope
```

不允许：

- LiteLLM adapter 直接写 `job_aggregates.status`。
- LiteLLM adapter 直接创建 Callback。
- Billing 查询反向修改 AI call ledger 明细。
- Job 状态机依赖 `cost_amount` 判断 succeeded / failed。
- 缺 usage、缺价格或成本估算失败时静默写 `0` 后继续成功。

## 5. Scope 归属模型

每次模型调用都必须有稳定 scope，用于权限、排障、聚合和导出。

```text
scope_type
  job        异步 Job runtime 内的模型调用
  sync_api   同步 HTTP 能力接口内的模型调用
  internal   内部工具、运营任务或验证任务
  batch      后台批处理中的模型调用

scope_id
  scope 内唯一 id
```

推荐规则：

| 场景 | `scope_type` | `scope_id` | 额外上下文 |
|---|---|---|---|
| Job 内调用模型 | `job` | `job_id` | `job_id`、`attempt_id`、`job_type`、`step_name` |
| 同步接口调用模型 | `sync_api` | `request_id` 或业务请求 id | `endpoint_name`、`operation` |
| 内部工具调用模型 | `internal` | run id | `operation`、操作者或任务名 |
| 批处理调用模型 | `batch` | batch run id | `operation`、chunk id |

`operation` 是稳定语义名，例如：

```text
structured_llm.extract_json
chat.completion
rewrite.text
summarize.chunk
batch.merge
```

这样可以保留 Job billing，同时避免非 Job 调用为了计费被迫套一个假 Job。

## 6. `model catalog`

`model catalog` 是本服务的模型事实源，调用方只使用本服务公开的 `model_id`，不直接依赖 provider model 或 LiteLLM 私有参数。

它负责：

- 对外稳定 `model_id`。
- 模型启停。
- provider 与 LiteLLM model 映射。
- 能力声明，例如 `chat`、`structured_output`、`tool_calling`。
- 创建期和运行期 gate。
- 必需环境变量。
- 稳定 generation 参数。
- `pricing_ref`。
- 治理字段。

LiteLLM 负责：

- provider 协议适配。
- 请求发送。
- provider response 接收。
- timeout / retry 等调用参数。

LiteLLM Proxy 可以作为外部 endpoint 被配置到 adapter，但 Proxy 的 virtual key、team budget、fallback chain、routing policy 和管理 API 不成为本服务公共合同，也不成为 billing 事实源。

模型可用性必须 fail-fast：

```text
enabled == true
status != disabled
requires_env 全部可读取且非空
pricing_ref 存在且通过 pricing config 校验
capabilities 满足调用 operation 要求
generation 配置合法
```

## 7. AI 调用生命周期

gateway facade 必须让账本包住真实 provider 调用。

```text
调用方传入:
  caller_id
  scope_type / scope_id
  operation
  model_id
  messages / structured request
  request metadata

AI gateway facade
  ↓
读取 model catalog 并 gate
  ↓
读取 pricing snapshot
  ↓
构造 request hash / size 摘要
  ↓
写 ai_call_ledger_entries pending
  ↓
pending 写入成功后才调用 LiteLLM adapter
  ↓
LiteLLM / provider call
  ↓
提取 normalized usage
  ↓
计算 cost estimate
  ↓
更新 ai_call_ledger_entries succeeded / failed
  ↓
返回模型输出或稳定错误
```

规则：

- pending row 写失败时，不得调用 provider。
- provider 调用成功但 terminal row 更新失败时，只允许短重试账本更新，不得自动重放 provider 调用。
- provider response 缺 usage 且模型要求 usage 时，调用失败为 `MODEL_USAGE_MISSING` 或等价稳定错误。
- pricing 缺失应在启动期失败；运行期成本计算异常时，本次调用失败，不得写 `0` 或 `null` 后继续成功。
- provider 已调用成功但上层业务输出校验失败时，AI call 仍可为 `billable`，上层 scope 可以失败。

## 8. `ai_call_ledger_entries`

`ai_call_ledger_entries` 是每次真实 LiteLLM / provider 调用的内部账本。

建议表结构：

```text
ai_call_ledger_entries
  id uuid primary key

  caller_id varchar(64) not null
  scope_type varchar(32) not null
  scope_id varchar(128) not null
  operation varchar(128) not null
  step_name varchar(128) null
  request_id varchar(128) null
  trace_id varchar(128) null

  job_id uuid null
  attempt_id uuid null
  job_type varchar(96) null

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
  billable_status varchar(32) not null

  started_at timestamptz not null
  completed_at timestamptz null
  duration_ms integer null
  created_at timestamptz not null
  updated_at timestamptz not null
```

约束：

- `scope_type`、`scope_id`、`operation` 必填。
- `scope_type="job"` 时，`job_id` 必填，且 `scope_id` 应等于 `job_id` 的稳定字符串表达。
- `attempt_id` 和 `job_type` 只在 Job scope 中必需。
- `billable_status` 首版枚举为 `pending`、`not_billable`、`billable`、`unknown`。
- provider 调用前失败必须是 `not_billable`。
- provider 已发出但是否计费不可确认时必须是 `unknown`，不得推断为 `0`。

建议索引：

```text
(scope_type, scope_id, created_at)
(caller_id, created_at)
(operation, created_at)
(job_id, created_at)
(attempt_id, created_at)
(model_id, created_at)
(provider, provider_model, created_at)
(status, created_at)
```

`ai_call_ledger_entries` 不保存：

- 完整 Prompt。
- 完整输入。
- 完整模型输出。
- provider 原始响应。
- 密钥。
- 长期 transcript。

只保存 hash、size、normalized usage、cost estimate 和脱敏错误摘要。

## 9. Pricing config

`pricing config` 只负责把 provider usage 转换成可审计的 cost estimate。

首版建议只支持 `per_token`：

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
  caller_id
  scope_type
  scope_id
  operation
  request metadata needed for pricing
```

输出必须冻结到 `ai_call_ledger_entries`：

```text
usage_detail
usage_units
cost_amount
currency
pricing_ref
pricing_version
cost_calculation_status
billable_status
```

规则：

- 金额使用 decimal，不使用 float 做最终存储。
- 所有 `enabled=true` 模型都必须能找到合法价格。
- 历史 `ai_call_ledger_entries` 不因价格文件更新而重算。
- `BILLING_ENABLED=false` 只关闭公共 billing 查询、公开计费能力和批量导出，不允许 enabled 模型缺少 pricing。

## 10. Billing read model

Billing read model 是从 `ai_call_ledger_entries` 派生的读取投影，不是新的支付事实。

通用聚合维度：

```text
scope billing:
  scope_type + scope_id

caller window billing:
  caller_id + time window + optional operation/model/currency filters

single call billing:
  ai_call_ledger_entries.id
```

Job billing 是 scope billing 的一个特例：

```text
GET /api/v1/ai-jobs/jobs/{job_id}/billing
  -> scope_type = "job"
  -> scope_id = job_id
```

### 10.1 统一 BillingEnvelope 合同

`BillingEnvelope` 是本项目当前唯一已公开的平台级计费返回对象。已落地的 Job billing 必须使用这组字段、状态和错误语义；后续若开放 scope billing、单次调用 billing 或 caller 时间窗口聚合，应优先复用同一 envelope，并在 Phase 1 的合同边界中单独冻结公开语义。

已落地 Job billing 和后续若开放的 billing 查询接口，HTTP 响应外层都应使用全局 `HttpEnvelope[T]`：

```text
HttpEnvelope[JobBillingResponseData]
  data.billing -> BillingEnvelope

HttpEnvelope[ScopeBillingResponseData]
  data.billing -> BillingEnvelope
```

接口 route 只返回裸 response data schema，不手动构造 `code`、`msg`、`data`、`request_id` 或 `server_time`：

```text
route handler
  -> return JobBillingResponseData(billing=billing)
  -> SuccessEnvelopeMiddleware
  -> HttpEnvelope[JobBillingResponseData]
```

允许：

- `JobBillingResponseData.billing: BillingEnvelope`
- `ScopeBillingResponseData.billing: BillingEnvelope`
- 内部导出使用 `BillingEnvelope` 或其字段等价的脱敏聚合行。

不允许：

- 具体 `job_type` 自定义 `billing` 字段含义。
- 某个同步 HTTP 接口返回自己的 `cost` / `usage` / `billing` 外壳。
- route 手动调用 `success_resp()` 拼接计费响应。
- 把 `BillingEnvelope` 默认塞进通用 `JobEnvelope` 顶层。
- Callback payload 默认携带 billing；若未来携带，必须与公开 billing 查询使用同一 `BillingEnvelope` 投影。

特定业务结果可以在 `public_result` 中展示业务摘要，但不能替代平台级 `BillingEnvelope`，也不能与平台 `BillingEnvelope` 使用相同字段名表达不同语义。

建议 `BillingEnvelope` 字段：

```json
{
  "schema_version": "1",
  "scope_type": "job",
  "scope_id": "01JZ8Q7Y4W7X2Z6M8N9P0Q1R2S",
  "status": "estimated",
  "kind": "cost_estimate",
  "currency": "USD",
  "total_cost_amount": "0.00012345",
  "usage_units": {
    "input_tokens": 1000,
    "cached_input_tokens": 0,
    "output_tokens": 300,
    "total_tokens": 1300
  },
  "pricing_refs": ["openai:gpt-4.1-mini@2026-06-22"],
  "ai_call_count": 1,
  "billable_call_count": 1,
  "unbillable_call_count": 0,
  "failed_call_count": 0,
  "diagnostic_reason": null,
  "finalized_at": "2026-06-22T10:00:00Z"
}
```

建议 response data wrapper：

```json
{
  "billing": {
    "schema_version": "1",
    "scope_type": "job",
    "scope_id": "01JZ8Q7Y4W7X2Z6M8N9P0Q1R2S",
    "status": "estimated",
    "kind": "cost_estimate",
    "currency": "USD",
    "total_cost_amount": "0.00012345",
    "usage_units": {
      "input_tokens": 1000,
      "cached_input_tokens": 0,
      "output_tokens": 300,
      "total_tokens": 1300
    },
    "pricing_refs": ["openai:gpt-4.1-mini@2026-06-22"],
    "ai_call_count": 1,
    "billable_call_count": 1,
    "unbillable_call_count": 0,
    "failed_call_count": 0,
    "diagnostic_reason": null,
    "finalized_at": "2026-06-22T10:00:00Z"
  }
}
```

最终 HTTP 成功响应由统一 envelope 层包装为：

```json
{
  "code": "0",
  "msg": "success",
  "data": {
    "billing": {
      "schema_version": "1",
      "scope_type": "job",
      "scope_id": "01JZ8Q7Y4W7X2Z6M8N9P0Q1R2S",
      "status": "estimated",
      "kind": "cost_estimate",
      "currency": "USD",
      "total_cost_amount": "0.00012345",
      "usage_units": {
        "input_tokens": 1000,
        "cached_input_tokens": 0,
        "output_tokens": 300,
        "total_tokens": 1300
      },
      "pricing_refs": ["openai:gpt-4.1-mini@2026-06-22"],
      "ai_call_count": 1,
      "billable_call_count": 1,
      "unbillable_call_count": 0,
      "failed_call_count": 0,
      "diagnostic_reason": null,
      "finalized_at": "2026-06-22T10:00:00Z"
    }
  },
  "request_id": "req_xxx",
  "server_time": "2026-06-22T10:00:01Z"
}
```

### 10.2 状态和聚合规则

状态规则：

| 场景 | 返回语义 |
|---|---|
| billing 公共能力关闭 | 稳定 `BILLING_DISABLED` 错误，不返回空 envelope 或伪造 `0`。 |
| scope 不存在或无权限 | 稳定 not found 错误，不泄露其它 caller 的 scope。 |
| scope 尚未终态且该查询要求终态 | 稳定 not terminal 错误，或后续单独设计 pending 预估接口。 |
| scope 终态且没有真实 provider 调用 | `status="not_billable"`，金额为 `"0.00000000"`。 |
| 所有账本 terminal 且可聚合 | `status="estimated"`。 |
| 存在 pending 或 unknown 调用 | `status="incomplete"`，不得忽略。 |
| usage/cost 计算失败导致无法估算 | `status="failed"`，`diagnostic_reason` 必填。 |

聚合规则：

- 只聚合调用时冻结在 `ai_call_ledger_entries` 中的 `cost_amount`、`currency`、`pricing_ref`、`pricing_version` 和 `usage_units`。
- 不使用当前 `pricing.yaml` 重新解释历史调用。
- 同一 envelope 出现多个 currency 时，首版返回 `failed` 或 `incomplete`，不自动汇率换算。
- `total_cost_amount` 使用 decimal 精确求和。
- 任何 incomplete row 都必须显式暴露，不得伪装为最终估算。

### 10.3 计费开关语义

计费开关只控制公共读取和公开能力，不允许调用方或具体能力绕过 ledger：

| 开关 | 控制范围 | 不控制 |
|---|---|---|
| `BILLING_ENABLED` | 公共 billing 查询、公开计费能力摘要和批量导出是否可用。 | 不允许 enabled 模型缺 pricing；不允许真实 LLM 调用绕过 `ai_call_ledger_entries`。 |
| `MODEL_CATALOG_EXPOSE_BILLING_CAPABILITY` | `/models` 是否公开服务级 billing capability 摘要。 | 不影响内部 model / pricing 校验，不暴露价格数值。 |

当 `BILLING_ENABLED=false`：

- `GET /api/v1/ai-jobs/jobs/{job_id}/billing` 或通用 scope billing 查询返回稳定 `BILLING_DISABLED`。
- 批量 billing / analytics 导出不可用。
- `/models` 如公开 billing capability，必须表达 `billing_enabled=false` 和 `cost_estimate_available=false`。
- AI gateway 仍不得因为公共查询关闭而允许 enabled 模型缺 pricing。

真实 LLM 调用是否落账不应交给每个 Job 或 HTTP 接口单独决定。只要调用经过 AI gateway facade，就必须尝试写 `ai_call_ledger_entries`；pending row 写失败时不得调用 provider。

## 11. 与 Job runtime 的关系

Job 是 AI gateway 的一种消费者。

```text
JobExecutor
  ↓
AI gateway facade.call(
    caller_id=job.caller_id,
    scope_type="job",
    scope_id=job.job_id,
    operation="<job_type>.<step>",
    job_id=job.job_id,
    attempt_id=attempt.attempt_id,
    job_type=job.job_type,
    model_id=runtime_fields.model_id,
    ...
  )
  ↓
ai_call_ledger_entries(scope_type="job", scope_id=job_id, ...)
  ↓
JobExecutor 校验模型输出并生成 result
  ↓
Job runtime 收敛 Job 和 Callback
```

Job 相关规则：

- AI gateway 不直接推进 Job 状态。
- Job 或 `job_type` 不定义自己的计费 envelope；如需计费信息，使用统一 `BillingEnvelope` 查询。
- Job failed 不等于 not billable。
- provider 调用成功但 Job 输出 schema 校验失败时，应能表达 `failed-but-billed`。
- 多 attempt、多步骤、多模型调用都聚合到同一个 Job scope。
- Callback payload 首版不默认携带 billing；如未来携带，必须与 `GET /api/v1/ai-jobs/jobs/{job_id}/billing` 使用同一聚合规则。

## 12. 非 Job 调用如何计费

同步接口或内部任务只要通过 AI gateway facade 调用模型，也可以写入同一张 `ai_call_ledger_entries`。

示例：

```text
POST /api/v1/ai-jobs/some-sync-capability
  ↓
AI gateway facade.call(
    caller_id=caller_id,
    scope_type="sync_api",
    scope_id=request_id,
    operation="rewrite.text",
    model_id="gpt-4.1-mini",
    ...
  )
  ↓
ai_call_ledger_entries(scope_type="sync_api", scope_id=request_id, operation="rewrite.text")
```

这样该同步接口不需要创建 Job，也能保留：

- usage 审计。
- cost estimate。
- caller 维度导出。
- 单 scope 排障。
- 后续 billing 查询能力。

同步 HTTP 接口接入规则：

- route 只定义业务 request / response data schema，不定义计费 envelope。
- service 调用 AI gateway facade，并传入 `caller_id`、`scope_type`、`scope_id`、`operation`、`model_id`。
- 接口若需要公开计费信息，必须通过统一 scope billing 查询返回 `ScopeBillingResponseData(billing=BillingEnvelope)`。
- 不允许同步接口在自己的业务 response data 中临时增加未版本化 `cost`、`usage` 或 `billing` 字段。
- 不允许绕过 AI gateway 直接调用 LiteLLM 或手写 `ai_call_ledger_entries`。

但是否公开同步 AI 能力接口、是否提供 `GET /billing/scopes/{scope_type}/{scope_id}` 或 caller 时间窗口查询，必须作为独立 HTTP 合同设计，不能因为有 ledger 就隐式开放。

## 13. `structured_llm` 示例定位

`structured_llm` 可以作为第一个真实调用模型的 Job 示例能力，但它不应定义 AI gateway 的边界。

它应该验证：

- Job 创建期模型 gate。
- Prompt 模板加载和版本校验。
- LiteLLM 结构化输出。
- AI gateway pending / terminal 账本写入。
- usage 提取和 cost estimate。
- Job scope billing 查询。
- canonical result / public result 投影。
- Callback 成功和失败投递。

它不负责：

- 定义通用 billing 模型。
- 定义同步 API 如何计费。
- 引入第二套 Job 外壳或业务编排框架。

## 14. 保留期和导出

`ai_call_ledger_entries` 首版不作为长期财务账本保存。

建议规则：

- Job scope 的账本保留期应至少覆盖 Job 可查询期，避免 Job 可查但 Job billing 不可查。
- 非 Job scope 的保留期由对应 scope owner 或 billing/export 策略定义。
- 长期成本分析交给外部 billing、analytics 或 warehouse。
- 批量只读导出只能读取 `ai_call_ledger_entries` 或其只读派生 view。
- 导出只输出脱敏后的 usage/cost estimate 字段，不输出完整 Prompt、完整输出、密钥或 provider 原始响应。

首版不新增：

```text
billing_outbox
job_billing_summaries 事实表
内部 usage 明细 HTTP 端点
```

后续如果读压或导出窗口证明需要投影表，可以新增 `job_billing_summaries` 或 scope billing materialized view，但它只能是 `ai_call_ledger_entries` 的派生读模型，不能成为新事实源。

## 15. 启动期校验

新增 AI gateway / billing 能力后，启动期应至少检查：

```text
settings:
  [ ] BILLING_ENABLED 可解析为 bool
  [ ] MODEL_CATALOG_EXPOSE_BILLING_CAPABILITY 可解析为 bool
  [ ] PRICING_CONFIG_PATH 指向可读取配置文件

model catalog:
  [ ] YAML version 支持
  [ ] model id 唯一
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

ledger:
  [ ] scope_type / scope_id / operation 规则登记
  [ ] billable_status 枚举登记
  [ ] cost_calculation_status 枚举登记
  [ ] usage missing / cost failed reason 登记

billing:
  [ ] billing envelope schema version 支持
  [ ] BillingEnvelope 是唯一平台级计费返回对象
  [ ] JobBillingResponseData / ScopeBillingResponseData 只包装 billing 字段
  [ ] status 枚举登记
  [ ] diagnostic_reason 稳定 reason 登记
  [ ] 公共查询权限规则明确
  [ ] BILLING_DISABLED / scope not found / scope not terminal 错误 reason 登记
```

任何检查失败都应阻止服务启动或阻止对应调用创建，不允许降级启动。

## 16. 从旧设计吸收和废弃的内容

应吸收：

- 服务自有 `model catalog` 是公开模型事实源。
- LiteLLM SDK 是 provider 执行适配，不是 billing 事实源。
- enabled 模型必须可估算成本，缺价格 fail-fast。
- 每次真实 provider call 都写独立账本。
- pending row 写成功后才允许调用 provider。
- 缺 usage 不得解释为 `0`。
- cost estimate 必须冻结 `pricing_ref` 和 `pricing_version`。
- `BillingEnvelope` 是读模型，不是扣款、发票或最终对账事实。
- `BillingEnvelope` 是唯一平台级计费返回对象，具体 Job 和 HTTP 接口不得自定义计费 envelope。
- failed-but-billed、多 attempt、多调用聚合必须能表达。
- Callback 不应默认携带 billing，除非同步升级轮询和 Callback 合同。

应废弃：

- `ai_call_ledger_entries.job_id not null`。
- 把 `/api/v1/ai-jobs/jobs/{job_id}/billing` 当作 billing 的概念中心。
- 让具体 `job_type` 或同步 HTTP 接口各自定义 `cost` / `usage` / `billing` 字段合同。
- 把 `job_type` 和 `step_name` 当作所有 AI call 的天然主归属。
- 把 AI call ledger 的保留期绝对绑定到 Job TTL。
- 为了让非 Job 调用计费而强行创建假 Job。

## 17. 验收清单

实现本文目标设计时，应能验证：

```text
AI gateway:
  [ ] 所有真实 provider 调用都经过 gateway facade。
  [ ] pending row 写失败时不调用 provider。
  [ ] provider 调用成功但 terminal ledger 更新失败时不自动重放 provider 调用。
  [ ] LiteLLM adapter 不直接写 Job 状态或 Callback。

Ledger:
  [ ] 每次真实 provider 调用一行 ai_call_ledger_entries。
  [ ] scope_type / scope_id 支持 job 和非 job 归属。
  [ ] usage 和 cost estimate 成功冻结。
  [ ] 缺 usage 不写 0。
  [ ] ai_call_ledger_entries 不保存完整 Prompt、完整输出、密钥或 provider 原始响应。

Pricing:
  [ ] enabled 模型缺 pricing 时启动失败。
  [ ] 历史账本不因价格文件更新而重算。
  [ ] decimal 精确求和，不使用 float 作为最终金额。

Billing:
  [ ] BillingEnvelope 是唯一平台级计费返回对象。
  [ ] JobBillingResponseData / ScopeBillingResponseData 复用 BillingEnvelope。
  [ ] Job billing 从 scope_type="job" 聚合。
  [ ] 非 Job scope 可以被内部导出或后续公开查询聚合。
  [ ] incomplete / failed billing 显式返回 diagnostic_reason。
  [ ] Billing read model 不反向修改 ledger 或 Job。
  [ ] 具体 job_type 和同步 HTTP 接口没有自定义计费 envelope。

Job:
  [ ] Job failed-but-billed 可表达。
  [ ] Callback 重试不改变 billing。
  [ ] JobEnvelope 不默认新增未版本化 billing 字段。

HTTP:
  [ ] route 返回裸业务 data schema，由统一 HttpEnvelope 包装。
  [ ] billing 查询 route 返回 data.billing，不手工构造 HttpEnvelope。
  [ ] 同步 AI 接口调用 LLM 时只传 scope 调 AI gateway，不直接写账本。
```
