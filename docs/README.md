# fastapi-best-ai-architecture 文档地图

本文是 `docs/` 目录唯一文档地图。核心文档只保留当前事实、公开合同、可重复排障手册和后续计划四类；历史长文档放入 `docs/archived/`，不进入默认阅读路径。

## 核心文档

| 文档 | 用途 |
|---|---|
| [`current/architecture.md`](current/architecture.md) | 当前服务边界、模块职责、运行形态、AI gateway/billing 边界和验证基线 |
| [`current/ai-capability.md`](current/ai-capability.md) | 当前 AI 调用入口、model/prompt/pricing registry、AI kernel 组件和文本 provider path |
| [`current/ai-billing.md`](current/ai-billing.md) | 当前 AI call ledger、usage/cost 事实源、Job billing 聚合和非资金账本边界 |
| [`current/job-kernel.md`](current/job-kernel.md) | 当前 Job、幂等键、Attempt、Dispatch outbox、Callback outbox、root/child lineage 和表设计边界 |
| [`runbooks/job-kernel-explained.md`](runbooks/job-kernel-explained.md) | 跟一个请求走完整条 Job 链路的心智模型讲解，配套 `job-kernel.md` 的事实清单，不是独立事实源；同名 `.html` 是可视化版，内容与 `.md` 一致 |
| [`current/workflow-kernel.md`](current/workflow-kernel.md) | 当前 DAG-lite root/child workflow、child Job 执行、root 汇总和 billing scope 行为 |
| [`current/observability.md`](current/observability.md) | 当前日志出口、request_id、业务事件白名单、本地 `logs/` 边界和新增日志代码规范 |
| [`current/template-readiness.md`](current/template-readiness.md) | 当前仓库作为 AI Job 微服务模板复制给新业务前的就绪边界、必改项和最小验收 |
| [`current/template-project-config.md`](current/template-project-config.md) | 复制模板成为真实业务项目时，项目身份、数据库名、compose 命名空间和对象存储命名空间的替换清单 |
| [`current/script-entrypoint-contract.md`](current/script-entrypoint-contract.md) | 当前脚本入口 `-h`、子命令 help、输出、副作用、示例和验证的 envelope 合同 |
| [`current/ops-dashboard.md`](current/ops-dashboard.md) | 当前只读 `ops_dashboard` 路由、data source / widget / layout / renderer 注册层和 Job 运维展示边界 |
| [`current/job-load-testing.md`](current/job-load-testing.md) | Job 发布、查询和完整异步流程的压测入口、执行计划、指标和评估方法 |
| [`runbooks/compose-full-dev-operations.md`](runbooks/compose-full-dev-operations.md) | 开发环境使用 `compose-full` 启动服务后，查看状态、容器内排障脚本和日志的操作手册 |
| [`runbooks/jobs使用与排障手册.md`](runbooks/jobs使用与排障手册.md) | `scripts/jobs.sh` overview、root/family scope、Job/workflow 查询和常见排障顺序的辅助说明；命令真源以 `scripts/jobs.sh` 和 `scripts/jobs/cli.py` 为准 |
| [`runbooks/标题生成链路.md`](runbooks/标题生成链路.md) | `poster_title_image` 从接单、style probe、生图、OSS 写入、join 到结果快照的链路定位和排障顺序 |
| [`runbooks/MAX_ACTIVE_JOBS 估算与生产调优.md`](runbooks/MAX_ACTIVE_JOBS%20估算与生产调优.md) | `MAX_ACTIVE_JOBS` 估算、K8s 生产调优顺序和 PostgreSQL/Redis 瓶颈判断 |
| [`runbooks/lifespan.md`](runbooks/lifespan.md) | API、worker、recovery 生命周期与运行期资源放置边界 |
| [`runbooks/local-postgres-database-name.md`](runbooks/local-postgres-database-name.md) | 本地项目数据库名变更后，PostgreSQL volume 旧库与 `.env` 新库不一致的排障和修复 |
| [`runbooks/remote-test-env-fastapi-redis-taskiq.md`](runbooks/remote-test-env-fastapi-redis-taskiq.md) | 远端测试环境 FastAPI 依赖漂移、Redis 5 和 Taskiq broker 排障记录 |
| [`api/service-contract.md`](api/service-contract.md) | 当前 HTTP envelope、Job、Callback、Billing、认证和公开 route 合同 |
| [`api/extension-guide.md`](api/extension-guide.md) | 新增 `job_type`、HTTP 接口、模型、Prompt 和对象存储产物的接入入口 |
| [`api/业务语种规范.md`](api/业务语种规范.md) | CPP / AI / RS 三方共享语种代码、`in` 例外和 AI 服务语种目录接口规范 |
| [`api/poster-title-image-delivery-api.md`](api/poster-title-image-delivery-api.md) | AI 标题图生成对接的当前交付接口合同、任务创建、查询、费用和 Callback 说明 |
| [`api/poster-title-image-api.md`](api/poster-title-image-api.md) | CPP 美术任务接入 AI 标题图生成的 vNext 目标接口草案，不覆盖当前实现合同 |
| [`plans/hardening.md`](plans/hardening.md) | 不阻塞主干开发的运维硬化 backlog |
| [`plans/ops-dashboard-post-mvp.md`](plans/ops-dashboard-post-mvp.md) | `ops_dashboard` MVP 后的表格可用性、长窗口分析、Job Trace 可视化、环境诊断和安全边界后续优化计划 |
| [`plans/job-kernel-reliability-review.md`](plans/job-kernel-reliability-review.md) | Job 机制生产可靠性、一致性、安全性和测试覆盖审查结论 |
| [`plans/ai-capability-enhancement.md`](plans/ai-capability-enhancement.md) | 多模态 provider、正式业务 `job_type`、node/child attribution 和业务 e2e 后续计划 |
| [`plans/ai-capability-cost-boundary-design.md`](plans/ai-capability-cost-boundary-design.md) | `job_cost_summary`、node/child cost attribution、多模态成本路径和持久化 usage 扩展后续计划 |
| [`plans/workflow-kernel-design.md`](plans/workflow-kernel-design.md) | workflow 从模板能力走向正式业务编排前的剩余缺口 |
| [`plans/job-type-example-load-testing-standardization.md`](plans/job-type-example-load-testing-standardization.md) | 标准化 Job type 示例、workflow 原语和 `load.sh` 压测合同，让代码事实源、示例实现和业务接入参考解耦的计划 |
| [`plans/implementation-terminal-acceptance.md`](plans/implementation-terminal-acceptance.md) | 模板阶段剩余验收门禁和业务接入前置条件 |

## 分层规则

- `docs/current/` 只写当前代码已经落地的事实。
- `docs/api/` 只写外部调用方和业务扩展方需要遵守的合同。
- `docs/plans/` 只写未来计划、待办和目标方向；可保留简短 current baseline 作为上下文，但当前事实真源仍在 `docs/current/`。
- `docs/runbooks/` 只写可重复执行的排障手册，不重复维护代码事实或 API 合同。
- `docs/archived/` 只保存历史设计和旧计划，归档文档不能作为当前事实源。

## 维护规则

- 新增长期文档前先判断是否能合并进现有核心文档。
- 普通文档不新增“相关文档”“阅读路径”“文档索引”等导航型列表，也不维护前后阅读顺序；所有索引和阅读顺序统一在 `docs/README.md` 维护。
- 当前实现事实优先以代码、测试和 `docs/current/` 为准。
- 对外合同变化必须同步 `docs/api/`、schema、route、测试和顶层 `README.md`。
- Job 内核变化必须同步 `docs/current/job-kernel.md` 和相关验证命令。
