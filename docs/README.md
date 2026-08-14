# fastapi-best-ai-architecture 文档地图

本文是 `docs/` 目录唯一文档地图。核心文档只保留当前事实、公开合同、可重复排障手册和后续计划四类；历史长文档放入 `docs/archived/`，不进入默认阅读路径。

## 核心文档

| 文档 | 用途 |
|---|---|
| [`current/architecture.md`](current/architecture.md) | 当前服务边界、模块职责、运行形态、AI gateway/billing 边界和验证基线 |
| [`current/ai-capability.md`](current/ai-capability.md) | 当前 AI 调用入口、model/prompt/pricing registry、AI kernel 组件和文本/图片 provider path |
| [`current/ai-billing.md`](current/ai-billing.md) | 当前 AI call ledger、usage/cost 事实源、Job billing 聚合和非资金账本边界 |
| [`current/job-kernel.md`](current/job-kernel.md) | 当前 Job、幂等键、Attempt、Dispatch outbox、Callback outbox、root/child lineage 和表设计边界 |
| [`current/registry-governance.md`](current/registry-governance.md) | 当前 registry governance、capability/tool 注册、graph 校验、`./scripts/tools.sh registry` 查看入口和 `media.audio_input:2` 能力事实 |
| [`current/audio-stem-separation-triton.md`](current/audio-stem-separation-triton.md) | `audio_stem_separation_triton` 当前 Job、Triton endpoint 依赖、tensor I/O、配置和实现边界；不替代外部 Triton 部署手册 |
| [`runbooks/job/job-kernel-explained.md`](runbooks/job/job-kernel-explained.md) | 跟一个请求走完整条 Job 链路的心智模型讲解，配套 `job-kernel.md` 的事实清单，不是独立事实源 |
| [`runbooks/job/job-orchestration-examples.md`](runbooks/job/job-orchestration-examples.md) | 独立讲解 Job 编排心智模型，用 `audio_stem_separation_triton` 和 `poster_title_image` 对比单 executor、root/child workflow、tool/capability 接入和新增 Job 开发 |
| [`current/workflow-kernel.md`](current/workflow-kernel.md) | 当前 DAG-lite root/child workflow、child Job 执行、root 汇总和 billing scope 行为 |
| [`current/observability.md`](current/observability.md) | 当前日志出口、request_id、业务事件白名单、本地 `logs/` 边界和新增日志代码规范 |
| [`current/template-readiness.md`](current/template-readiness.md) | 当前仓库作为 AI Job 微服务模板的就绪边界、可复用能力和不承诺事项 |
| [`runbooks/template-development-mental-model.md`](runbooks/template-development-mental-model.md) | 轻量说明本模板怎么开发和维护、如何选择单 executor 或 root/child workflow、以及新增业务时哪些代码事实源不能绕过；不是独立事实源 |
| [`runbooks/template-adoption-runbook.md`](runbooks/template-adoption-runbook.md) | 复制模板成为真实业务项目时，项目身份、数据库名、compose 命名空间、对象存储命名空间和最小验收操作清单 |
| [`current/script-entrypoint-contract.md`](current/script-entrypoint-contract.md) | 当前脚本入口 `-h`、子命令 help、输出、副作用、示例和验证的 envelope 合同 |
| [`current/ops-dashboard.md`](current/ops-dashboard.md) | 当前只读 `ops_dashboard` 路由、data source / widget / layout / renderer 注册层和 Job 运维展示边界 |
| [`current/job-load-testing.md`](current/job-load-testing.md) | 当前 Job 压测入口、case/profile、manifest、产物、安全确认和指标语义事实 |
| [`runbooks/job/job-load-testing-runbook.md`](runbooks/job/job-load-testing-runbook.md) | 执行一次 Job 压测、选择示例 profile、模拟 `poster_title_image` 结构、观察 dashboard 和压后诊断的唯一操作手册 |
| [`runbooks/ops/compose-full-dev-operations.md`](runbooks/ops/compose-full-dev-operations.md) | `compose-full` 开发形态的启动、状态、容器内排障脚本和日志操作；不覆盖 `local` 或 K8s |
| [`runbooks/job/jobs使用与排障手册.md`](runbooks/job/jobs使用与排障手册.md) | `scripts/jobs.sh` 只读证据查询、root/family scope、Job/workflow 排障命令含义；不承担压测主流程 |
| [`runbooks/poster-title-image-smoke-runbook.md`](runbooks/poster-title-image-smoke-runbook.md) | 用 `scripts/smoke.sh` 创建真实 `poster_title_image` Job、确认模型/OSS/billing/输出图链路；会产生真实费用 |
| [`runbooks/audio/htdemucs-ft-onnx-local-assets.md`](runbooks/audio/htdemucs-ft-onnx-local-assets.md) | 用 `scripts/models.sh` 和 `scripts/media.sh` 准备 htdemucs-ft ONNX 本地模型资产和 44.1kHz 双声道 WAV 测试音频；不提交 Job |
| [`runbooks/audio/audio-stem-separation-dev-server-runbook.md`](runbooks/audio/audio-stem-separation-dev-server-runbook.md) | 在开发服务器准备 `audio_stem_separation` 模型、测试音频、compose-full 可见性检查和真实 Job 验证；不替代 `deploy.sh` |
| [`runbooks/audio/audio-stem-separation-triton-smoke-benchmark.md`](runbooks/audio/audio-stem-separation-triton-smoke-benchmark.md) | 用 `smoke.sh` 验证 `audio_stem_separation_triton` 真实业务链路，并附 2026-07-13 开发服务器 baseline 样本；不作为生产容量承诺 |
| [`runbooks/标题生成链路.md`](runbooks/标题生成链路.md) | `poster_title_image` 从接单、style probe、生图、OSS 写入、join 到结果快照的链路定位和排障顺序；不写 smoke 执行步骤 |
| [`runbooks/job/MAX_ACTIVE_JOBS 估算与生产调优.md`](runbooks/job/MAX_ACTIVE_JOBS%20估算与生产调优.md) | `MAX_ACTIVE_JOBS` 估算、K8s 生产调优顺序和 PostgreSQL/Redis 瓶颈判断；不是压测执行手册 |
| [`runbooks/ops/lifespan.md`](runbooks/ops/lifespan.md) | API、worker、recovery 生命周期与运行期资源放置边界 |
| [`runbooks/ops/local-postgres-database-name.md`](runbooks/ops/local-postgres-database-name.md) | 本地项目数据库名变更后，PostgreSQL volume 旧库与 `.env` 新库不一致的排障和修复 |
| [`runbooks/ops/remote-test-env-fastapi-redis-taskiq.md`](runbooks/ops/remote-test-env-fastapi-redis-taskiq.md) | 远端测试环境 FastAPI 依赖漂移、Redis 5 和 Taskiq broker 排障记录 |
| [`api/service-contract.md`](api/service-contract.md) | 当前 HTTP envelope、Job、Callback、Billing、认证和公开 route 合同 |
| [`api/extension-guide.md`](api/extension-guide.md) | 新增 `job_type`、HTTP 接口、模型、Prompt 和对象存储产物的接入入口 |
| [`api/tagged-text-translation-api.md`](api/tagged-text-translation-api.md) | `tagged_text_translation` 批量带标签文案翻译 Job 的当前对接合同，覆盖创建、轮询、Callback、结果和错误码；不暴露 Prompt 接口 |
| [`api/languages-api.md`](api/languages-api.md) | `GET /api/v1/ai-jobs/languages` 语种目录对接文档，说明环境配置、响应字段、当前语种列表和错误响应 |
| [`api/业务语种规范.md`](api/业务语种规范.md) | CPP / AI / RS 三方共享语种代码、`in` 例外和 AI 服务语种目录接口规范 |
| [`api/poster-title-image-delivery-api.md`](api/poster-title-image-delivery-api.md) | AI 标题图生成对接的当前交付接口合同、任务创建、查询、费用和 Callback 说明 |
| [`plans/job-kernel-hardening.md`](plans/job-kernel-hardening.md) | 当前 Job kernel 可靠性、一致性和公开信息边界的短期硬化计划；完成后应归档 |
| [`plans/template-drift-checklist.md`](plans/template-drift-checklist.md) | 当前模板骨架 operation/job_type/workflow/capability/tool 合同漂移的收口计划和验收条件 |
| [`plans/ops-dashboard-post-mvp.md`](plans/ops-dashboard-post-mvp.md) | `ops_dashboard` MVP 后的表格可用性、长窗口分析、Job Trace 可视化、环境诊断和安全边界后续优化计划 |
| [`plans/job-observability-governance.md`](plans/job-observability-governance.md) | Job 日志、stage、adapter、链路 ID 和新 `job_type` 观测接入标准的治理计划；完成后沉淀到 current/API 文档 |
| [`plans/ai-capability-long-term.md`](plans/ai-capability-long-term.md) | AI provider、正式业务 `job_type`、usage normalizer、cost attribution 和 billing read model 的 trigger-based 长期计划 |
| [`plans/workflow-kernel-long-term.md`](plans/workflow-kernel-long-term.md) | Job 内部 workflow kernel 从模板能力走向正式业务 `job_type` 前的 trigger-based 长期计划 |
| [`plans/job-platform-orchestration-options.md`](plans/job-platform-orchestration-options.md) | 未来公共 Job Platform 是否支持 DAG-lite 编排的两种微服务拆分方案比较、接入示例和推荐边界 |

## Runbook 职责边界

`docs/runbooks/` 只保留可重复执行的操作、排障和心智模型手册。不要在 runbook 里复制 current 事实表、API 字段合同或计划 backlog；需要事实时链接 `docs/current/`，需要调用方合同时链接 `docs/api/`，需要未来工作时链接 `docs/plans/`。

| 场景 | 默认入口 | 不放入这里的内容 |
|---|---|---|
| 理解模板开发维护全局入口 | [`runbooks/template-development-mental-model.md`](runbooks/template-development-mental-model.md) | 字段事实、API 合同、Job 编排长示例、具体接入清单 |
| 理解 Job 内核链路 | [`runbooks/job/job-kernel-explained.md`](runbooks/job/job-kernel-explained.md) | 字段真源、表结构大表、API 合同 |
| 讲解 Job 编排和新增 Job 落点 | [`runbooks/job/job-orchestration-examples.md`](runbooks/job/job-orchestration-examples.md) | API 字段合同、smoke 命令、数据库细节 |
| 查 Job 运行证据 | [`runbooks/job/jobs使用与排障手册.md`](runbooks/job/jobs使用与排障手册.md) | 压测执行步骤、业务专属链路解释 |
| 执行 Job 压测 | [`runbooks/job/job-load-testing-runbook.md`](runbooks/job/job-load-testing-runbook.md) | `load.sh` 机器合同、生产容量调优公式 |
| 估算生产容量 | [`runbooks/job/MAX_ACTIVE_JOBS 估算与生产调优.md`](runbooks/job/MAX_ACTIVE_JOBS%20估算与生产调优.md) | 一次压测怎么跑、Job 明细查询命令教程 |
| 验证 `poster_title_image` smoke | [`runbooks/poster-title-image-smoke-runbook.md`](runbooks/poster-title-image-smoke-runbook.md) | 标题图内部链路排障、完整 API 字段合同 |
| 准备 htdemucs-ft ONNX 本地模型与测试音频 | [`runbooks/audio/htdemucs-ft-onnx-local-assets.md`](runbooks/audio/htdemucs-ft-onnx-local-assets.md) | `audio_stem_separation` 实现计划、模型推理代码、Job 提交流程 |
| 在开发服务器验证 `audio_stem_separation` | [`runbooks/audio/audio-stem-separation-dev-server-runbook.md`](runbooks/audio/audio-stem-separation-dev-server-runbook.md) | 自动下载模型、生产部署、K8s 资源管理、模型推理代码 |
| 压测 `audio_stem_separation_triton` smoke | [`runbooks/audio/audio-stem-separation-triton-smoke-benchmark.md`](runbooks/audio/audio-stem-separation-triton-smoke-benchmark.md) | Triton model repository 部署、生产容量承诺、旧本地 ONNX Job 验证；文内 baseline 只作复现对比样本 |
| 排查标题图生成链路 | [`runbooks/标题生成链路.md`](runbooks/标题生成链路.md) | smoke 执行步骤、完整 API 字段合同 |
| 处理运行形态问题 | [`runbooks/ops/compose-full-dev-operations.md`](runbooks/ops/compose-full-dev-operations.md)、[`runbooks/ops/lifespan.md`](runbooks/ops/lifespan.md)、[`runbooks/ops/remote-test-env-fastapi-redis-taskiq.md`](runbooks/ops/remote-test-env-fastapi-redis-taskiq.md)、[`runbooks/ops/local-postgres-database-name.md`](runbooks/ops/local-postgres-database-name.md) | 业务 Job 合同或压测报告 |

## 分层规则

- `docs/current/` 只写当前代码已经落地的事实。
- `docs/api/` 只写外部调用方和业务扩展方需要遵守的合同。
- `docs/plans/` 只保留短期可执行计划和长期 trigger-based 演进计划；可保留简短 current baseline 作为上下文，但当前事实真源仍在 `docs/current/`。短期计划完成后必须归档或沉淀为 current/API/runbook 事实。
- `docs/runbooks/` 只写可重复执行的操作、排障和心智模型手册，不重复维护代码事实、API 合同或计划 backlog。
- `docs/archived/` 只保存历史设计和旧计划，归档文档不能作为当前事实源。

## 维护规则

- 新增长期文档前先判断是否能合并进现有核心文档。
- 普通文档不新增“相关文档”“阅读路径”“文档索引”等导航型列表，也不维护前后阅读顺序；所有索引和阅读顺序统一在 `docs/README.md` 维护。
- 当前实现事实优先以代码、测试和 `docs/current/` 为准。
- 对外合同变化必须同步 `docs/api/`、schema、route、测试和顶层 `README.md`。
- Job 内核变化必须同步 `docs/current/job-kernel.md` 和相关验证命令。
