# 模板采用就绪度

本文只记录本仓库作为 AI Job 微服务模板的当前就绪边界。复制模板后的具体改名、配置替换和验收操作见 [`../runbooks/template-adoption-runbook.md`](../runbooks/template-adoption-runbook.md)。

## 当前结论

当前仓库可以作为 **试点级 AI Job 微服务模板** 复制给单个业务服务使用。它适合承载异步 AI Job、Taskiq worker、对象存储产物、状态查询、Callback、billing 查询和 Job 内部 DAG-lite root/child workflow。

本仓库不应被当作公司级通用后端、用户系统、项目管理系统、跨服务 workflow 平台或生产部署平台。

## 当前可复用能力

| 能力 | 当前事实 |
|---|---|
| HTTP API | 默认前缀 `/api/v1/ai-jobs` 下的 Job、Billing、模型和 Prompt 元信息 route |
| 异步执行 | FastAPI + Taskiq worker + PostgreSQL / Redis |
| Job 内核 | Job / Attempt / Dispatch outbox / Callback outbox / Recovery |
| 提交幂等 | `caller_id + client_request_id` 由 `job_submission_keys` 约束 |
| 对象存储产物 | Job executor 按业务 schema 写入结果或 artifact 引用 |
| AI 调用 | AI facade、model registry、prompt registry、pricing registry 和 AI call ledger |
| Billing | `ai_call_ledger_entries` 聚合为 Job billing read model，不是资金账本 |
| Workflow | `job_aggregates` 自索引表达 root/child，支持 DAG-lite demo workflow |
| Registry governance | 业务包、`job_type`、tool、error、log event 注册检查 |
| 运维读模型 | `scripts/jobs.sh`、`ops_dashboard` 和 Job 压测入口 |

## 能力可见性

`job_type.visibility` 决定外部提交准入：

| visibility | 当前含义 |
|---|---|
| `public` | 可以作为业务 API 对外提交，`test/prd` 也允许 |
| `demo` | 模板、本地、开发和示例验证能力；`test/prd` 不允许外部提交 |
| `internal` | 只能由服务内部 workflow 创建 child Job |

模板内置 demo 能力包括低副作用示例、真实 LLM 示例、本地 ONNX 音频分离示例和 Triton 音频分离示例。它们用于验证模板能力边界，不自动成为新业务的正式 API 合同。

正式业务应新增自己的 `job_type`、schema、executor、workflow definition、模型/Prompt/pricing 配置和验证入口。不要把 `example_*` 或 demo 真实模型能力包装成正式业务能力。

## 复制边界

复制模板时必须让项目身份、数据库 / Redis、compose 命名空间、对象存储命名空间、模型 / Prompt / pricing 配置和 Callback 签名归属于新业务服务。具体替换清单、搜索项和验收命令只在 [`../runbooks/template-adoption-runbook.md`](../runbooks/template-adoption-runbook.md) 维护。

## 当前不承诺

- 不提供 Kubernetes、CI/CD、云平台 Secrets 或生产发布流水线。
- 不提供用户、项目、权限、余额、订单、退款、发票或财务总账。
- 不开放任意 DAG 提交，也不做跨服务业务步骤编排。
- 不保证非终态 Job 一定返回增量 `job_result`；只有声明了 `result_snapshot_statuses` 的 `job_type` 才会返回运行中或失败态快照。
- 不把真实模型 e2e 或外部对象存储 e2e 纳入模板默认 `scripts/verify.sh check`。

## 验收归属

模板复制、业务改名、发布模式配置校验、compose 检查和 workflow smoke 的操作步骤以 [`../runbooks/template-adoption-runbook.md`](../runbooks/template-adoption-runbook.md) 为准。本文不维护命令清单。
