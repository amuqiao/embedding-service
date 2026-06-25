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

```text
BillingEnvelope
  schema_version
  scope_type
  scope_id
  status
  kind
  currency
  total_cost_amount
  usage_units
  pricing_refs
  ai_call_count
  billable_call_count
  unbillable_call_count
  failed_call_count
  diagnostic_reason
  finalized_at
```

`status` 当前允许：

| status | 含义 |
|---|---|
| `estimated` | 已有可计费调用且成本聚合成功 |
| `not_billable` | 没有 AI 调用，或没有 billable 调用 |
| `incomplete` | 存在 `pending`、`unknown` 或未收敛 AI 调用 |
| `failed` | 成本计算失败、币种冲突、billable 行缺少币种或成本 |

## Public Contract

外部合同以 [`docs/api/service-contract.md`](../api/service-contract.md) 为准：

- `GET /api/v1/ai-jobs/jobs/{job_id}/billing`
- `HttpEnvelope[JobBillingResponseData]`
- `data.billing -> BillingEnvelope`

`GET /models` 可以按配置公开 `billing_enabled` 和 `cost_estimate_available` 摘要，但不暴露 `pricing_ref`、价格矩阵、provider raw usage schema 或内部成本明细。

## 当前限制

- 当前没有 `job_cost_summary` 表；Job billing 每次从 ledger 聚合。
- 当前没有 workflow node / child cost attribution 专用列；公开查询只稳定支持 Job scope，root billing 可聚合 descendant AI 调用。
- 当前不是资金账本；外部扣费系统只能把本服务成本估算作为输入信号，不能复用 `ai_call_ledger_entries` 当扣费流水。

## 验证

- `tests/test_billing_service.py`
- `tests/test_billing_route_contract.py`
- `tests/test_ai_gateway_facade.py`
- `tests/test_ai_call_log_repo.py`
- `tests/test_recovery.py`
- `./scripts/verify.sh check`
