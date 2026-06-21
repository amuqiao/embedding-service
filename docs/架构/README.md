# 架构文档

本目录记录 AI Job 服务的稳定架构说明。文档不合并成单篇；每篇承担不同心智入口，避免把外部合同、内部实现和生产准入证据混在一起。

## 文档心智模型

| 文档 | 负责回答 | 不负责 |
|---|---|---|
| [项目规范与骨架](project-standards.md) | 标准：目录骨架、HTTP envelope、输入输出 schema、异常、配置、日志、ORM、Repository 和验证基线。 | 具体 PR 的迁移步骤、业务字段细节。 |
| [新增 HTTP 接口标准接入规范](http-api-extension-standard.md) | 接入流程：新增业务 HTTP 接口时如何补 schema、operation registry、错误、日志和测试。 | 具体业务字段设计、调用方流程。 |
| [新增 job_type 标准接入规范](job-type-extension-standard.md) | 接入流程：新增 `job_type` 时如何补 Params、Runtime、Result、`JobExecutor`、错误、日志和测试。 | 具体 Prompt、模型输出内容、业务标签体系。 |
| [架构总览](架构总览.md) | 当前代码事实：服务边界、运行组件、Job 生命周期、Taskiq 执行、恢复机制、数据模型。 | 具体调用方协议细节、生产准入结论。 |
| [Taskiq Job 数据模型设计](taskiq-job-model-design.md) | 目标设计：移除 Celery、改用 Taskiq 后的 Job 聚合根、Attempt、Callback Outbox、Event 和生命周期心智模型。 | 当前代码事实、迁移 PR 的文件级步骤、具体 `job_type` 业务参数。 |
| [通用 AI Job 接入规范](通用_AI_Job_接入规范.md) | 外部合同：调用方如何创建、查询、接收 Callback；新增 `job_type` 必须遵守什么。 | 内部代码结构、部署调参。 |
| [AI Job 服务重构计划](refactor-plan.md) | 实施路线：按项目规范先建骨架，再适配接口、Job、配置、日志、异常和 ORM。 | 具体 PR 的文件级实现方案。 |
| [生产就绪性评审](production-readiness-review.md) | 生产判断：当前骨架能力、上线前置条件、调参方式和剩余风险。 | API 合同细节、某个业务能力的领域规则。 |

## 阅读顺序

第一次理解服务时，先读 [架构总览](架构总览.md)。

需要新增接口、改 schema、改异常、改配置、改日志或改 ORM 时，先读 [项目规范与骨架](project-standards.md)。

需要新增业务 HTTP 接口时，读 [新增 HTTP 接口标准接入规范](http-api-extension-standard.md)。

需要新增 `job_type` 时，读 [新增 job_type 标准接入规范](job-type-extension-standard.md)。

接入新调用方或新增能力时，读 [通用 AI Job 接入规范](通用_AI_Job_接入规范.md)。

需要评估上线风险、做上线决策或准备发布准入材料时，读 [生产就绪性评审](production-readiness-review.md)。

需要规划代码结构重构或拆分实施 PR 时，读 [AI Job 服务重构计划](refactor-plan.md)。

需要设计移除 Celery、切换 Taskiq 后的新 Job 状态机和数据模型时，读 [Taskiq Job 数据模型设计](taskiq-job-model-design.md)。

## 维护规则

- 架构文档只记录当前代码和稳定设计，不记录临时排查过程。
- 项目标准、目录骨架、HTTP envelope、异常、配置、日志、ORM 或 Repository 边界变化时，应同步更新 [项目规范与骨架](project-standards.md)。
- HTTP operation registry、接口错误、接口日志或接口合同测试规则变化时，应同步更新 [新增 HTTP 接口标准接入规范](http-api-extension-standard.md)。
- `job_type` registry、`JobExecutor` metadata、能力 schema、Job 错误或能力测试规则变化时，应同步更新 [新增 job_type 标准接入规范](job-type-extension-standard.md)。
- `app/jobs/registry.py`、Job 状态机、Taskiq 执行、recovery、数据库字段或内部执行路径变化时，应同步更新 [架构总览](架构总览.md)。
- Taskiq 执行引擎、Job attempt、Callback outbox、Job event、取消或幂等设计变化时，应同步更新 [Taskiq Job 数据模型设计](taskiq-job-model-design.md)。
- API 合同、错误码、Callback envelope、`job_type` 接入边界变化时，应同步更新 [通用 AI Job 接入规范](通用_AI_Job_接入规范.md)。
- 重构阶段、保护行为、验证口径或目录职责边界变化时，应同步更新 [AI Job 服务重构计划](refactor-plan.md)。
- 生产准入结论、目标环境证据缺口、风险清单和生产前置条件放在 [生产就绪性评审](production-readiness-review.md)，不要混入总览文档。
- 后续如果补齐目标环境 e2e、K8s 接入或压测证据，应同步更新 [生产就绪性评审](production-readiness-review.md)。
