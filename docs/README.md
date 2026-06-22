# fastapi-best-ai-architecture 文档导航

本文是 `docs/` 目录的入口索引。当前主线是 FastAPI + Taskiq 的 AI Job 能力层服务；文档按项目标准、接口接入、执行模型和设计记录分层维护。

`fastapi-best-ai-architecture` 是可替换的模板标识。复用本模板时，应按目标业务项目替换包名、服务名、API 前缀、数据库名、对象存储前缀和业务 `job_type`。

## 当前必要文档

| 文档 | 用途 |
|---|---|
| [`架构/README.md`](架构/README.md) | 架构目录入口 |
| [`架构/project-standards-code-facts.md`](架构/project-standards-code-facts.md) | 项目规范与骨架（代码事实版）：当前代码中的 envelope、输入输出 schema、异常、配置、日志、ORM、Repository 和验证基线 |
| [`接口层/http-api-extension-standard.md`](接口层/http-api-extension-standard.md) | 新增业务 HTTP 接口时的 schema、operation registry、错误、日志和测试接入规范 |
| [`接口层/job-type-extension-standard.md`](接口层/job-type-extension-standard.md) | 新增 `job_type` 时的 Params、Runtime、Result、`JobExecutor`、错误、日志和测试接入规范 |
| [`设计文档/taskiq-job-model-design.md`](设计文档/taskiq-job-model-design.md) | Taskiq Job MVP 的数据模型、生命周期、Attempt、Callback Outbox 和 Event 设计 |
| [`设计文档/FastAPI 统一响应信封架构设计文档.md`](设计文档/FastAPI%20统一响应信封架构设计文档.md) | HTTP 成功/错误统一响应信封设计 |
| [`设计文档/callback-job-unified-envelope-design.md`](设计文档/callback-job-unified-envelope-design.md) | Job 轮询结果与 Callback envelope 的一致性设计 |

## 辅助文档

| 文档 | 用途 |
|---|---|
| [`架构/架构总览.md`](架构/架构总览.md) | 服务边界和整体架构入口；执行引擎细节以 Taskiq 设计文档和当前代码为准 |
| [`架构/refactor-plan.md`](架构/refactor-plan.md) | 规范先行重构路线和阶段性检查清单 |
| [`接口层/language-codes.md`](接口层/language-codes.md) | 业务语种合约资料；仅在接入相关业务能力时使用 |

## 候选设计

| 文档 | 状态 | 用途 |
|---|---|---|
| [`设计文档/headless-ai-job-platform-service-design.md`](设计文档/headless-ai-job-platform-service-design.md) | Proposed | Headless AI Job Platform Service 的模型目录、模型调用账本、成本估算和结构化 LLM 示例设计；不是当前已实现合同 |

## 快速阅读路径

理解服务架构：

```text
架构/project-standards-code-facts.md
  ↓
设计文档/taskiq-job-model-design.md
  ↓
架构/架构总览.md
```

新增 HTTP 接口：

```text
架构/project-standards-code-facts.md
  ↓
接口层/http-api-extension-standard.md
```

新增 `job_type`：

```text
架构/project-standards-code-facts.md
  ↓
接口层/job-type-extension-standard.md
  ↓
设计文档/taskiq-job-model-design.md
```

## 维护规则

- 文档导航只引用当前仓库内实际存在的文件。
- 当前实现事实优先以代码、测试和 `project-standards-code-facts.md` 为准；设计文档用于解释已接受的结构和边界。
- 新增 HTTP 接口时同步检查接口规范、operation registry、schema registry 和合同测试。
- 新增 `job_type` 时同步检查 `job_type` 规范、executor metadata、schema registry、Callback 投影和 workflow smoke。
