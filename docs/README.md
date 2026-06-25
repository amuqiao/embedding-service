# fastapi-best-ai-architecture 文档地图

本文是 `docs/` 目录唯一文档地图。核心文档只保留当前事实、公开合同、扩展入口和后续计划四类；历史长文档放入 `docs/archived/`，不进入默认阅读路径。

## 核心文档

| 文档 | 用途 |
|---|---|
| [`current/architecture.md`](current/architecture.md) | 当前服务边界、模块职责、运行形态、AI gateway/billing 边界和验证基线 |
| [`current/job-kernel.md`](current/job-kernel.md) | 当前 Job、幂等键、Attempt、Dispatch outbox、Callback outbox 和 audit event 的职责划分 |
| [`api/service-contract.md`](api/service-contract.md) | 当前 HTTP envelope、Job、Callback、Billing、认证和公开 route 合同 |
| [`api/extension-guide.md`](api/extension-guide.md) | 新增 `job_type`、HTTP 接口、模型、Prompt 和对象存储产物的接入入口 |
| [`api/poster-title-image-api.md`](api/poster-title-image-api.md) | CPP 美术任务接入 AI 标题图生成的 vNext 目标接口草案，不覆盖当前实现合同 |
| [`plans/hardening.md`](plans/hardening.md) | 不阻塞主干开发的运维硬化 backlog |
| [`plans/ai-capability-enhancement.md`](plans/ai-capability-enhancement.md) | AI Capability Kernel、provider adapter、usage normalizer、Prompt fail-fast 和多模态能力接入骨架 |
| [`plans/ai-capability-cost-boundary-design.md`](plans/ai-capability-cost-boundary-design.md) | AI 能力层成本估算、typed pricing、ledger 事实源和非扣费边界的设计计划 |
| [`plans/workflow-kernel-design.md`](plans/workflow-kernel-design.md) | 在当前 Job kernel 之上增加最小 durable workflow kernel 的设计计划 |
| [`plans/implementation-terminal-acceptance.md`](plans/implementation-terminal-acceptance.md) | workflow、AI capability 和 cost boundary 开发前后的终态验收门禁 |

## 分层规则

- `docs/current/` 只写当前代码已经落地的事实。
- `docs/api/` 只写外部调用方和业务扩展方需要遵守的合同。
- `docs/plans/` 只写未来计划、待办和目标方向，不覆盖当前事实。
- `docs/archived/` 只保存历史设计和旧计划，归档文档不能作为当前事实源。

## 维护规则

- 新增长期文档前先判断是否能合并进现有核心文档。
- 普通文档不新增“相关文档”“阅读路径”“文档索引”等导航型列表。
- 当前实现事实优先以代码、测试和 `docs/current/` 为准。
- 对外合同变化必须同步 `docs/api/`、schema、route、测试和顶层 `README.md`。
- Job 内核变化必须同步 `docs/current/job-kernel.md` 和相关验证命令。
