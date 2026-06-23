# fastapi-best-ai-architecture 文档地图

本文是 `docs/` 目录当前唯一的文档地图和集中索引。当前主线是 FastAPI + Taskiq 的 AI Job 能力层服务；文档按项目标准、接口接入、执行模型和设计记录分层维护。

其他文档不单独维护导航型索引或阅读路径；只有在正文确实依赖某个事实源、前置规范或冲突裁决规则时，才在上下文中保留必要链接。

`fastapi-best-ai-architecture` 是可替换的模板标识。复用本模板时，应按目标业务项目替换包名、服务名、API 前缀、数据库名、对象存储前缀和业务 `job_type`。

## 当前必要文档

| 文档 | 用途 |
|---|---|
| [`架构/project-standards-code-facts.md`](架构/project-standards-code-facts.md) | 项目规范与骨架（代码事实版）：当前代码中的 envelope、输入输出 schema、异常、配置、日志、ORM、Repository 和验证基线 |
| [`架构/service-contract-boundary.md`](架构/service-contract-boundary.md) | 当前 HTTP、Job、Error、Callback、Billing envelope 的合同边界和内部事实 owner |
| [`架构/job-lifecycle-state-model.md`](架构/job-lifecycle-state-model.md) | 当前 Job、Attempt、Dispatch、Callback、Recovery 和 AI call ledger 的内部生命周期状态权威；不是对外 envelope 合同 |
| [`架构/ai-gateway-runtime-boundary.md`](架构/ai-gateway-runtime-boundary.md) | 当前 AI gateway、Job runtime、provider adapter、AI call ledger 和 Billing read model 的内部依赖边界 |
| [`接口层/http-api-extension-standard.md`](接口层/http-api-extension-standard.md) | 新增业务 HTTP 接口时的 schema、operation registry、错误、日志和测试接入规范 |
| [`接口层/job-type-extension-standard.md`](接口层/job-type-extension-standard.md) | 新增 `job_type` 时的 Params、Runtime、Result、`JobExecutor`、错误、日志和测试接入规范 |
| [`设计文档/FastAPI 统一响应信封架构设计文档.md`](设计文档/FastAPI%20统一响应信封架构设计文档.md) | HTTP 成功/错误统一响应信封设计 |
| [`架构/job-ai-billing-mental-model.md`](架构/job-ai-billing-mental-model.md) | `Job`、AI gateway、AI call ledger、Billing read model 和 `model_id` 的分层心智模型 |

## 辅助文档

| 文档 | 用途 |
|---|---|
| [`架构/架构总览.md`](架构/架构总览.md) | 服务边界和整体架构入口；执行引擎细节以生命周期状态模型和当前代码为准 |
| [`架构/refactor-plan.md`](架构/refactor-plan.md) | 规范先行重构路线和阶段性检查清单 |
| [`接口层/language-codes.md`](接口层/language-codes.md) | 业务语种合约资料；仅在接入相关业务能力时使用 |

## 进行中计划

| 文档 | 状态 | 用途 |
|---|---|---|
| [`架构/production-ai-job-kernel-plan.md`](架构/production-ai-job-kernel-plan.md) | Plan | 生产级 AI Job 生命周期内核重构计划；覆盖合同边界、生命周期模型、Job kernel、AI gateway / runtime adapter、AI ledger / billing 和迁移验证 |

## 设计基线

| 文档 | 状态 | 用途 |
|---|---|---|
| [`设计文档/callback-job-unified-envelope-design.md`](设计文档/callback-job-unified-envelope-design.md) | Candidate | Job 轮询结果与 Callback envelope 的一致性设计候选；当前合同以 `service-contract-boundary.md` 和代码事实为准 |
| [`设计文档/taskiq-job-model-design.md`](设计文档/taskiq-job-model-design.md) | Historical / Partially Superseded | Taskiq Job MVP 长设计记录；当前公开合同以 `service-contract-boundary.md` 为准，当前内部生命周期状态权威以 `job-lifecycle-state-model.md` 为准 |
| [`设计文档/ai-gateway-layer-design.md`](设计文档/ai-gateway-layer-design.md) | Accepted Target Baseline | AI gateway layer、模型调用账本、pricing、billing read model 和 Job scope 投影设计；不是当前已实现合同 |

## 快速阅读路径

理解服务架构：

```text
架构/project-standards-code-facts.md
  ↓
架构/service-contract-boundary.md
  ↓
架构/job-lifecycle-state-model.md
  ↓
架构/ai-gateway-runtime-boundary.md
  ↓
架构/架构总览.md
```

新增 HTTP 接口：

```text
架构/project-standards-code-facts.md
  ↓
架构/service-contract-boundary.md
  ↓
接口层/http-api-extension-standard.md
```

新增 `job_type`：

```text
架构/project-standards-code-facts.md
  ↓
架构/service-contract-boundary.md
  ↓
接口层/job-type-extension-standard.md
  ↓
架构/job-lifecycle-state-model.md
```

## 维护规则

- 文档地图只引用当前仓库内实际存在的文件。
- `docs/` 下的文档地图、集中索引和阅读路径只在本文维护；普通文档不新增导航型索引，避免多处互相引用后难以同步。
- 子目录默认不维护 README；只有当单个子目录中文档数量明显增多，且确实需要目录级边界规则时，才考虑新增子目录 README。
- 当前实现事实优先以代码、测试和 `project-standards-code-facts.md` 为准；设计文档用于解释已接受的结构和边界。
- `Plan` 文档可以引用 current truth 作为背景，但只能服务于后续工作；不得覆盖 current 事实或对外合同。
- 新增 HTTP 接口时同步检查接口规范、operation registry、schema registry 和合同测试。
- 新增 `job_type` 时同步检查 `job_type` 规范、executor metadata、schema registry、Callback 投影和 workflow smoke。
