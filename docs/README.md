# fastapi-best-ai-architecture 文档地图

本文是 `docs/` 目录唯一文档地图。核心文档只保留当前事实、公开合同、可重复排障手册和后续计划四类；历史长文档放入 `docs/archived/`，不进入默认阅读路径。

## 核心文档

| 文档 | 用途 |
|---|---|
| [`current/architecture.md`](current/architecture.md) | 当前服务边界、模块职责、运行形态、AI gateway/billing 边界和验证基线 |
| [`current/ai-capability.md`](current/ai-capability.md) | 当前 AI 调用入口、model/prompt/pricing registry、AI kernel 组件和文本 provider path |
| [`current/ai-billing.md`](current/ai-billing.md) | 当前 AI call ledger、usage/cost 事实源、Job billing 聚合和非资金账本边界 |
| [`current/job-kernel.md`](current/job-kernel.md) | 当前 Job、幂等键、Attempt、Dispatch outbox、Callback outbox、root/child lineage 和表设计边界 |
| [`current/workflow-kernel.md`](current/workflow-kernel.md) | 当前 DAG-lite root/child workflow、child Job 执行、root 汇总和 billing scope 行为 |
| [`current/template-readiness.md`](current/template-readiness.md) | 当前仓库作为 AI Job 微服务模板复制给新业务前的就绪边界、必改项和最小验收 |
| [`current/job-load-testing.md`](current/job-load-testing.md) | Job 发布、查询和完整异步流程的 Locust 压测选型、执行、指标和评估方法 |
| [`current/job-load-test-plan.md`](current/job-load-test-plan.md) | Job 压测的分阶段执行命令、浏览器实时查看和离线报告查看方法 |
| [`runbooks/jobs使用与排障手册.md`](runbooks/jobs使用与排障手册.md) | `scripts/jobs.sh` 查询 Job 状态、定位异步任务问题和常见排障顺序 |
| [`runbooks/MAX_ACTIVE_JOBS 估算与生产调优.md`](runbooks/MAX_ACTIVE_JOBS%20估算与生产调优.md) | `MAX_ACTIVE_JOBS` 估算、K8s 生产调优顺序和 PostgreSQL/Redis 瓶颈判断 |
| [`runbooks/lifespan.md`](runbooks/lifespan.md) | API、worker、recovery 生命周期与运行期资源放置边界 |
| [`api/service-contract.md`](api/service-contract.md) | 当前 HTTP envelope、Job、Callback、Billing、认证和公开 route 合同 |
| [`api/extension-guide.md`](api/extension-guide.md) | 新增 `job_type`、HTTP 接口、模型、Prompt 和对象存储产物的接入入口 |
| [`api/业务语种规范.md`](api/业务语种规范.md) | CPP / AI / RS 三方共享语种代码、`in` 例外和 AI 服务语种目录接口规范 |
| [`api/poster-title-image-api.md`](api/poster-title-image-api.md) | CPP 美术任务接入 AI 标题图生成的 vNext 目标接口草案，不覆盖当前实现合同 |
| [`plans/hardening.md`](plans/hardening.md) | 不阻塞主干开发的运维硬化 backlog |
| [`plans/ai-capability-enhancement.md`](plans/ai-capability-enhancement.md) | 多模态 provider、正式业务 `job_type`、node/child attribution 和业务 e2e 后续计划 |
| [`plans/ai-capability-cost-boundary-design.md`](plans/ai-capability-cost-boundary-design.md) | `job_cost_summary`、node/child cost attribution、多模态成本路径和持久化 usage 扩展后续计划 |
| [`plans/workflow-kernel-design.md`](plans/workflow-kernel-design.md) | workflow 从模板能力走向正式业务编排前的剩余缺口 |
| [`plans/job-kernel-data-model-hardening.md`](plans/job-kernel-data-model-hardening.md) | Job kernel 表职责、字段事实源、root/child lineage 和上线前数据模型收口计划 |
| [`plans/retry-domain-data-model.md`](plans/retry-domain-data-model.md) | retry domain、attempt purpose、policy snapshot 和 Job retry 数据模型重构计划 |
| [`plans/implementation-terminal-acceptance.md`](plans/implementation-terminal-acceptance.md) | 模板阶段剩余验收门禁和业务接入前置条件 |

## 分层规则

- `docs/current/` 只写当前代码已经落地的事实。
- `docs/api/` 只写外部调用方和业务扩展方需要遵守的合同。
- `docs/plans/` 只写未来计划、待办和目标方向，不覆盖当前事实。
- `docs/runbooks/` 只写可重复执行的排障手册，不重复维护代码事实或 API 合同。
- `docs/archived/` 只保存历史设计和旧计划，归档文档不能作为当前事实源。

## 维护规则

- 新增长期文档前先判断是否能合并进现有核心文档。
- 普通文档不新增“相关文档”“阅读路径”“文档索引”等导航型列表，也不维护前后阅读顺序；所有索引和阅读顺序统一在 `docs/README.md` 维护。
- 当前实现事实优先以代码、测试和 `docs/current/` 为准。
- 对外合同变化必须同步 `docs/api/`、schema、route、测试和顶层 `README.md`。
- Job 内核变化必须同步 `docs/current/job-kernel.md` 和相关验证命令。
