# Operational Hardening Backlog

本文只记录开发主干之外的运维硬化 backlog。它不是 workflow、AI capability 或 cost boundary 的同级架构计划；当前事实仍以 `docs/current/` 和 `docs/api/` 为准。

核心架构前置合同和终态验收以 [`implementation-terminal-acceptance.md`](implementation-terminal-acceptance.md) 为准。已经被 workflow、AI capability 或 cost boundary 计划吸收的事项，不在本文重复维护。

## 定位

本文负责：

- 记录不阻塞第一版架构开发、但有运维价值的增强项。
- 保留后续可排期的 observability、runbook、deployment checklist 和文档治理事项。
- 避免把 operational backlog 写成新的 API 合同、数据事实源或实现前置条件。

本文不负责：

- 定义 root / child Job 可见性。
- 定义 workflow 状态机或 workflow tables。
- 定义 AI cost truth source。
- 定义 `job.cost`、callback cost 或 billing projection 合同。
- 重新打开已列为 non-goal 的公开接口，例如 `GET /billing/scopes/{scope_type}/{scope_id}`。

## 已前置到终态验收的事项

以下事项不再作为普通 backlog 维护，开发前必须通过终态验收文档收口：

| 事项 | 归属 |
|---|---|
| Root / child Job 可见性 | `implementation-terminal-acceptance.md` |
| Ledger attribution 最小字段集或等价 scope | `implementation-terminal-acceptance.md` |
| Root Job progress / result 投影合同 | `implementation-terminal-acceptance.md` |
| Root workflow 创建、child dispatch、root terminal projection 的 crash-window 验收 | `implementation-terminal-acceptance.md` |
| Retention 基线 | `implementation-terminal-acceptance.md` |
| Callback receiver mock 和签名验收 | `implementation-terminal-acceptance.md` |
| Outbox / callback dead letter 的最小可诊断状态 | `implementation-terminal-acceptance.md` |

## Backlog

### Job Kernel Operations

- 增加 outbox dead letter 的运维查询入口。
- 评估人工重放入口；只有在 dead letter 状态、幂等键和权限边界清楚后再实现。
- 增加 attempt、dispatch、callback 的指标和告警。
- 增加更细粒度的 stale running 诊断事件。
- 为 Job、Attempt、Callback、Audit event 和 AI call ledger 的不同保留期补 runbook。

### Callback Operations

- 增加 Callback 投递观测面：最近错误、下一次重试、dead letter 原因。
- 增加 callback receiver mock 的本地演示脚本或测试 fixture；正式验收要求归属终态验收文档。
- 如未来公开 callback cost summary，必须复用 cost boundary 中的同一 projection，不在本文另开合同。

### Billing Operations

- 读压证明需要时，可以评估 `job_cost_summary` 这类派生 read model。
- summary 只能从 `ai_call_ledger_entries` 重建，不能成为新的成本事实源。
- caller 时间窗口聚合、批量导出或 warehouse 对接属于后续分析能力，不阻塞 workflow / AI capability 第一版。
- 不新增公开 `GET /billing/scopes/{scope_type}/{scope_id}`；如未来需要，必须作为单独 API contract 评审。

### Deployment Readiness

- 本仓库不维护生产部署，但可以补充模板级平台部署前置条件清单。
- 多副本生产形态应使用外部对象存储、托管 PostgreSQL/Redis、密钥管理和统一日志采集。
- 平台部署清单不能包含远程数据库重置、生产 secret 写入或跨仓库清理逻辑。

### Documentation Hygiene

- 历史长文档只归档，不作为当前阅读入口。
- 新增长期文档前先判断应放入 `current`、`api` 还是 `plans`。
- 普通文档不新增阅读路径、相关文档列表或重复索引。

## Acceptance

- 本文不包含当前事实声明。
- 本文不定义新的公开 API、Job 状态、callback event 或 billing route。
- 本文不把 summary、callback payload 或 `job.cost` 写成成本事实源。
- 本文不与 workflow、AI capability、cost boundary 或 service contract 的 non-goals 冲突。
