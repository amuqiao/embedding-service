# AI Billing 当前模型

本文只记录当前已经落地的 AI 调用成本估算和 billing read model。这里的 billing 不是用户扣费、钱包余额、订单账单、发票或财务总账。

## 当前行为

- `ai_call_ledger_entries` 是当前 AI provider call usage / cost estimate 的事实源。
- `GET /api/v1/ai-jobs/jobs/{job_id}/billing` 从 `ai_call_ledger_entries` 按 Job scope 聚合 billing read model。
- workflow child Job 的 AI 调用写入 root Job scope，公开 root Job billing 能覆盖 descendant AI 调用。
- Job billing 只允许在 Job 进入 `succeeded` 或 `failed` 后查询。
- Job result、callback payload 和 workflow summary 不是成本事实源。
- 如果 provider 已经被调用但 ledger terminal update 失败，系统不能重放 provider 调用来修账。

## Ledger 生命周期

```text
generate_text_with_ledger()
  -> create pending ledger row before provider call
  -> provider call
  -> normalize usage
  -> calculate cost
  -> mark ledger succeeded

failure path
  -> mark ledger failed with failure_phase / error_code / billable_status
```

当前 `failure_phase` 主要包括：

- `provider`
- `usage`
- `pricing`
- `recovery`

`app/tasks/recovery.py` 会把超时停留在 `pending` 的 AI ledger 行收敛为失败状态，让 billing 显式表达不完整或失败，不伪造 0 成本成功。

## 稳定合同与扩展边界

billing 模块只消费标准化后的 `usage_units` 和已经冻结的 `cost_amount`，不消费 provider 原始 usage，也不关心具体 adapter 类型。

当前链路的职责边界是：

| 层级 | 当前职责 |
|---|---|
| adapter | 调用 provider SDK，并把 provider 返回的 raw usage 放入 adapter result |
| `UsageNormalizer` | 把 adapter result 标准化为 `UsageRecord`，并产出可聚合的 `usage_units` |
| `pricing_registry` | 根据 `pricing_type` 和标准化后的 `UsageRecord` 计算单次调用成本 |
| ledger | 冻结单次 AI 调用的 `usage_units`、`pricing_ref`、`pricing_version`、`cost_amount` 和生命周期状态 |
| billing | 按 scope 聚合 ledger 行，生成 billing read model 和公开投影 |

新增模型时，优先扩展模型配置、价格配置和 adapter / normalizer 的 provider usage 映射。只有新增当前 `pricing_registry` 不支持的计价方式，或公开 billing 投影需要新增稳定字段时，才应修改 billing 合同。

例如当前 `gpt-image-2` 使用 `per_image_token` 计费，adapter 必须能提供 Images API 语义的图片 token usage；不应把 Responses API 的通用 usage 直接当成图片计费 usage。billing 聚合层仍只读取标准化后的 `usage_units` 和 `cost_amount`。

## Ledger 字段边界

| 字段类别 | 当前事实 |
|---|---|
| scope | `scope_type`、`scope_id`；公开 Job billing 使用 `scope_type="job"` 和公开 Job id，workflow child 使用 root Job id |
| Job attribution | 当前持久化实际执行的 `job_id`、`attempt_id`、`job_type` |
| provider | `model_id`、`provider`、`provider_model`、`litellm_model` |
| request / response | `request_hash`、`response_hash`、`input_size_bytes`、`output_size_bytes` |
| usage | `usage_detail` 保存 provider raw usage 诊断信息；`usage_units` 保存可聚合计价单位 |
| pricing | `pricing_ref`、`pricing_version`、`currency` |
| cost | `cost_amount`、`cost_calculation_status` |
| lifecycle | `status`、`billable_status`、`started_at`、`completed_at`、`duration_ms` |

`usage_detail` 不参与聚合计费；任何进入 billing 的单位都必须先标准化到 `usage_units`。

## Billing Projection

`app/services/billing.py` 从 ledger 行构造 `BillingEnvelope`：

已完成且可计费的 ledger 行使用 pending row 创建时记录的 `pricing_ref` / `pricing_version`，以及 terminal update 写入的 `cost_amount` / `currency`；billing projection 只聚合这些 ledger 事实，不会用当前 `pricing.yaml` 对历史成本重新查价或重算。没有 ledger 行或没有 billable 行时，0 成本 billing 的展示币种仍来自当前默认币种配置。

`BillingEnvelope` 字段和 `status` 含义属于外部合同，以 [`../api/service-contract.md`](../api/service-contract.md) 为准；本文只维护内部聚合事实。

## Public Contract

外部合同以 [`../api/service-contract.md`](../api/service-contract.md) 为准。当前公开入口是 `GET /api/v1/ai-jobs/jobs/{job_id}/billing`。

`GET /models` 可以按配置公开 `billing_enabled` 和 `cost_estimate_available` 摘要，但不暴露 `pricing_ref`、价格矩阵、provider raw usage schema 或内部成本明细。

## 当前限制

- 当前没有 `job_cost_summary` 表；Job billing 每次从 ledger 聚合。
- 当前没有 workflow node / child cost attribution 专用列；公开查询只稳定支持 Job scope，root billing 可聚合 descendant AI 调用。
- 当前不是资金账本；外部扣费系统只能把本服务成本估算作为输入信号，不能复用 `ai_call_ledger_entries` 当扣费流水。

## 验证

- `tests/test_billing_service.py`
- `tests/test_billing_route_contract.py`
- `tests/test_ai_gateway.py`
- `tests/test_ai_call_log_repo.py`
- `tests/test_recovery.py`
- `./scripts/verify.sh check`
