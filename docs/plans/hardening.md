# 后续硬化计划

本文只记录尚未完成或可继续增强的事项，不描述当前事实。当前事实以 `docs/current/` 和 `docs/api/` 为准。

## Job Kernel

- 增加 outbox dead letter 的运维查询和人工重放入口。
- 增加 attempt、dispatch、callback 的指标和告警。
- 增加更细粒度的 stale running 诊断事件。
- 明确长期保留策略：Job、Attempt、Callback、Audit event 和 AI call ledger 的保留期可以不同，但不能破坏 Job 查询期内的公开合同。

## Billing

- 评估是否需要通用 scope billing 查询，例如 `GET /billing/scopes/{scope_type}/{scope_id}`。
- 评估是否需要 caller 时间窗口聚合、批量导出或 warehouse 对接。
- 如果读压证明需要 summary 表，只能从 `ai_call_ledger_entries` 派生，不能成为新的计费事实源。

## Callback

- 增加 Callback receiver mock 和签名验收用例。
- 增加 Callback 投递观测面：最近错误、下一次重试、dead letter 原因。
- 如未来要在 Callback 中携带 billing，必须与 `GET /jobs/{job_id}/billing` 使用同一 `BillingEnvelope` 投影，并同步升级合同测试。

## Deployment

- 本仓库不维护生产部署，但可以补充模板级平台部署前置条件清单。
- 多副本生产形态应使用外部对象存储、托管 PostgreSQL/Redis、密钥管理和统一日志采集。

## 文档

- 历史长文档只归档，不作为当前阅读入口。
- 新增长期文档前先判断应放入 `current`、`api` 还是 `plans`。
- 普通文档不新增阅读路径、相关文档列表或重复索引。
