# AI Gateway / Runtime Adapter 边界

本文描述当前已经实现的 AI gateway、Job runtime、provider adapter 和 AI call ledger 边界。它不是公开 HTTP 合同；公开合同仍以 [`service-contract-boundary.md`](service-contract-boundary.md) 为准。

## 职责边界

| 边界 | 当前 owner | 职责 | 不负责 |
|---|---|---|---|
| Job kernel | `app/jobs/runner.py`、`app/tasks/jobs.py`、`app/repositories/job_repo.py` | Job / Attempt 状态迁移、租约、重试、终态写回和 Callback outbox | provider 协议、usage 提取、成本估算 |
| Job runtime consumer | `app/services/executor.py`、具体 `JobExecutor` | 从 runtime snapshot 读取模型调用输入，把 Job scope 上下文传给 AI gateway | 直接写 `ai_call_logs` 或自行计算 billing |
| AI gateway facade | `app/services/ai_gateway_facade.py` | model gate、pending ledger、provider adapter 调用、usage 校验、成本估算、terminal ledger 更新 | Job 状态迁移、Callback 投递、公开 HTTP envelope 包装 |
| Provider adapter | `app/integrations/ai_gateway.py` | 通过 LiteLLM 执行文本生成并返回 `TextGenerationResult` | 写数据库、写 Job、写 Callback、决定 billing envelope |
| AI call ledger | `app/repositories/ai_call_log_repo.py`、`ai_call_logs` | 保存每次真实 AI provider 调用的 pending / terminal 事实 | 反向决定 Job succeeded / failed |
| Billing read model | `app/services/billing.py` | 从 `ai_call_logs` 聚合 Job scope `BillingEnvelope` | 修改 ledger、Job、Attempt 或 provider 调用结果 |

稳定依赖方向：

```text
Job runner
  -> JobExecutor / Job runtime consumer
  -> AI gateway facade
  -> model catalog / pricing registry
  -> ai_call_logs repository
  -> LiteLLM provider adapter

Billing service
  -> ai_call_logs repository
  -> BillingEnvelope
```

AI gateway 不 import Job repository，不推进 Job 状态机，不投递 Callback。Job 失败或成功由 Job kernel 根据 executor 返回或抛出的稳定错误写回。

## 当前调用路径

内置 LLM 文本运行时：

```text
Taskiq attempt
  -> app/jobs/runner.py
  -> JobExecutor.run()
  -> app/services/executor.py::run_ai_job()
  -> app/services/ai_gateway_facade.py::generate_text_with_ledger()
  -> app/integrations/ai_gateway.py::generate_text()
  -> ai_call_logs terminal row
```

自定义 `JobExecutor` 也可以调用 `generate_text_with_ledger()`。当前 `job_real_llm_double_echo` 使用这种方式在同一 Job scope 内发起两次真实 LLM 调用，并由 Job billing 聚合两条 ledger 行。

## Ledger 写入语义

AI gateway facade 当前使用两阶段 ledger 写入：

1. 调用 provider 前创建 `ai_call_logs.status=pending` 并提交。
2. Provider 返回后校验 usage、计算 cost estimate，再把同一行更新为 terminal。
3. 如果 pending ledger 创建失败，不调用 provider。
4. 如果 provider 超时或失败，尝试把 ledger 标记为 failed，并抛稳定 `AppError`。
5. 如果 provider 成功但缺 usage 或成本计算失败，尝试把 ledger 标记为 failed，且 `billable_status=unknown`、`cost_calculation_status=failed`。
6. 如果 provider 已成功但 terminal ledger 更新无法 claim 该 pending row，抛不可自动重试的 `AI_LEDGER_UPDATE_FAILED`。

`AI_LEDGER_UPDATE_FAILED` 不能作为自动重放 provider 调用的依据。真实模型调用可能已经发生；recovery 只会把长期未收敛的 `pending` ledger 行收敛为 `failed + billable_status=unknown`，运维排障必须保留 ledger 行和 scope 上下文。

## Scope 规则

每次通过 AI gateway 调用模型都必须传入：

- `caller_id`
- `scope_type`
- `scope_id`
- `operation`
- `model_id`

当前 `scope_type` 允许 `job`、`sync_api`、`internal`、`batch`。其中只有 `job` scope 已有公开 billing 查询合同。

`scope_type="job"` 还必须传入 `job_id`、`attempt_id` 和 `job_type`，且 `scope_id` 必须等于 `job_id`。非 Job scope 目前只是内部复用边界，不自动产生公开 HTTP route、公开 billing 查询或同步 chat 接口。

## 错误边界

当前 provider adapter 通过 `asyncio.wait_for()` 对 LiteLLM 调用做总时长截断；timeout 由 AI gateway facade 收敛为稳定 `MODEL_CALL_TIMEOUT`。其它 provider 失败也由 AI gateway facade 收敛为稳定 `MODEL_CALL_FAILED`。Provider 原始错误可以作为内部 ledger 诊断信息保存，但不能进入公开 HTTP / Job / Callback 合同。

当前稳定错误语义：

| reason | 当前语义 |
|---|---|
| `MODEL_CALL_TIMEOUT` | provider 调用超时，ledger 尝试标记 failed。 |
| `MODEL_CALL_FAILED` | provider 调用失败，ledger 尝试标记 failed。 |
| `MODEL_USAGE_MISSING` | provider 已返回但缺少可计量 usage，ledger 标记 failed / unknown。 |
| `MODEL_COST_CALCULATION_FAILED` | provider 已返回且有 usage，但成本估算失败，ledger 标记 failed / unknown。 |
| `AI_LEDGER_UPDATE_FAILED` | ledger terminal 更新无法完成；不可自动重试，不重放 provider 调用。 |

当前 recovery loop 会扫描超过 `JOB_STALE_RUNNING_SECONDS + 60s` 的 `pending` ledger 行，并把它们标记为 `failed`、`failure_phase=recovery`、`error_code=AI_CALL_PENDING_TIMEOUT`、`billable_status=unknown`、`cost_calculation_status=not_applicable`。该阈值晚于 worker hard timeout 和 stale running 窗口，避免抢先收敛仍可能合法写 terminal 的 live worker。若出现 `unknown` ledger 行，当前 billing read model 显式表达 `incomplete`，不能靠重新调用 provider 修复账本。

## 验证

当前边界由以下测试覆盖：

- `tests/test_ai_gateway_facade.py`
- `tests/test_ai_call_log_repo.py`
- `tests/test_billing_service.py`
- `tests/test_registry_contract.py`
- `tests/test_job_workflow.py`
- `tests/test_recovery.py`

真实 LLM Job billing 只通过 `./scripts/real-flow.sh ... --confirm-cost` 手动触发，不属于默认 `workflow-smoke`。
