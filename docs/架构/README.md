# 架构文档

本目录记录 AI Job 服务的架构标准和结构入口。接口接入规范放在 `docs/接口层/`，Taskiq 与响应信封的细节设计放在 `docs/设计文档/`。

## 文档心智模型

| 文档 | 负责回答 | 不负责 |
|---|---|---|
| [项目规范与骨架（代码事实版）](project-standards-code-facts.md) | 标准：当前代码中的目录骨架、HTTP envelope、输入输出 schema、异常、配置、日志、ORM、Repository 和验证基线。 | 具体 PR 的迁移步骤、业务字段细节。 |
| [架构总览](架构总览.md) | 服务边界和整体结构入口。 | 接口接入步骤、Taskiq 深层状态机。 |
| [Job / AI / Billing 心智模型](job-ai-billing-mental-model.md) | 分层：`Job`、AI 能力层、`ai_call_logs`、`BillingEnvelope` 和 `model_id` 的依赖边界。 | Taskiq 字段级状态机、具体价格公式、具体 `job_type` 参数。 |
| [AI Job 服务重构计划](refactor-plan.md) | 实施路线：按项目规范先建骨架，再适配接口、Job、配置、日志、异常和 ORM。 | 具体 PR 的文件级实现方案。 |
| [新增 HTTP 接口标准接入规范](../接口层/http-api-extension-standard.md) | 接入流程：新增业务 HTTP 接口时如何补 schema、operation registry、错误、日志和测试。 | 具体业务字段设计、调用方流程。 |
| [新增 job_type 标准接入规范](../接口层/job-type-extension-standard.md) | 接入流程：新增 `job_type` 时如何补 Params、Runtime、Result、`JobExecutor`、错误、日志和测试。 | 具体 Prompt、模型输出内容、业务标签体系。 |
| [Taskiq Job 数据模型设计](../设计文档/taskiq-job-model-design.md) | Taskiq Job MVP 的 Job 聚合根、Attempt、Callback Outbox、Event 和生命周期心智模型。 | 当前代码逐行说明、具体 `job_type` 业务参数。 |

## 阅读顺序

第一次理解服务时，先读 [项目规范与骨架（代码事实版）](project-standards-code-facts.md)，再读 [架构总览](架构总览.md) 和 [Job / AI / Billing 心智模型](job-ai-billing-mental-model.md)，最后读 [Taskiq Job 数据模型设计](../设计文档/taskiq-job-model-design.md)。

需要新增接口、改 schema、改异常、改配置、改日志或改 ORM 时，先读 [项目规范与骨架（代码事实版）](project-standards-code-facts.md)。

需要新增业务 HTTP 接口时，读 [新增 HTTP 接口标准接入规范](../接口层/http-api-extension-standard.md)。

需要新增 `job_type` 时，读 [新增 job_type 标准接入规范](../接口层/job-type-extension-standard.md)。

需要判断 Job、AI gateway、模型目录、`ai_call_logs` 或 `BillingEnvelope` 应该放在哪一层时，读 [Job / AI / Billing 心智模型](job-ai-billing-mental-model.md)。

需要规划代码结构重构或拆分实施 PR 时，读 [AI Job 服务重构计划](refactor-plan.md)。如果任务涉及 Taskiq 执行、Job attempt、Callback outbox、Job event、取消或幂等设计，再读 [Taskiq Job 数据模型设计](../设计文档/taskiq-job-model-design.md)。

## 维护规则

- 架构文档只记录当前代码和稳定设计，不记录临时排查过程。
- 项目标准、目录骨架、HTTP envelope、异常、配置、日志、ORM 或 Repository 边界变化时，应同步更新 [项目规范与骨架（代码事实版）](project-standards-code-facts.md)。
- HTTP operation registry、接口错误、接口日志或接口合同测试规则变化时，应同步更新 [新增 HTTP 接口标准接入规范](../接口层/http-api-extension-standard.md)。
- `job_type` registry、`JobExecutor` metadata、能力 schema、Job 错误或能力测试规则变化时，应同步更新 [新增 job_type 标准接入规范](../接口层/job-type-extension-standard.md)。
- `app/jobs/registry.py`、Job 状态机、Taskiq 执行、recovery、数据库字段或内部执行路径变化时，应同步检查 [架构总览](架构总览.md) 和 [Taskiq Job 数据模型设计](../设计文档/taskiq-job-model-design.md)。
- AI gateway、模型目录、`ai_call_logs`、`BillingEnvelope` 或 Job/AI/Billing 依赖边界变化时，应同步更新 [Job / AI / Billing 心智模型](job-ai-billing-mental-model.md)。
- 重构阶段、保护行为、验证口径或目录职责边界变化时，应同步更新 [AI Job 服务重构计划](refactor-plan.md)。
