# AI Job Template 文档导航

本文是 `docs/` 目录的入口索引。当前主线是通用 FastAPI AI Job 执行模板；`novel_localization` 相关文档只作为内置示例 workflow 或历史上下文使用。

## 当前必要文档

| 文档 | 用途 |
|---|---|
| [`template-usage.md`](template-usage.md) | 模板替换清单，以及接入新 workflow 的最小步骤 |
| [`架构/README.md`](架构/README.md) | 架构目录入口 |
| [`架构/架构总览.md`](架构/架构总览.md) | 服务定位、边界、API、Job 生命周期、异步执行、Callback、恢复机制、数据模型和扩展边界；待进一步模板化精简 |
| [`job-implementation-guide.md`](job-implementation-guide.md) | Job 系统实施说明；待进一步从内置示例 workflow 中抽离通用机制说明 |
| [`部署与发布手册.md`](部署与发布手册.md) | 本地开发、compose 部署、配置规则、验证入口和常见排障 |

## 内置示例文档

| 文档 | 用途 |
|---|---|
| [`接口层/小说本地化AI能力层_后端对接接口文档.md`](接口层/小说本地化AI能力层_后端对接接口文档.md) | `novel_localization` 示例 workflow 的历史对接文档，不作为通用模板 API 主文档 |

## 阶段性维护文档

| 文档 | 用途 |
|---|---|
| [`架构/production-readiness-review.md`](架构/production-readiness-review.md) | 生产就绪性评审报告。仅在上线准入、风险复盘和补齐验证证据时维护；结论稳定后可归档 |

## 已归档文档

归档文档保留历史上下文，不作为当前实现和对接的事实来源。详情见 [`archive/README.md`](archive/README.md)。

| 文档 | 归档原因 |
|---|---|
| [`archive/async-job-spec.md`](archive/async-job-spec.md) | 通用异步 Job 设计规范，内容大于当前项目实际启用范围 |
| [`archive/job-env-vars-quick-reference.md`](archive/job-env-vars-quick-reference.md) | 配置速查内容与部署手册、生产评审重复，后续按需提炼回主文档 |
| [`archive/MVP_生产配置规范.md`](archive/MVP_生产配置规范.md) | 配置面设计讨论稿，适合作为历史决策材料 |
| [`archive/接口层/小说本地化AI能力层接口文档.md`](archive/接口层/小说本地化AI能力层接口文档.md) | 旧版完整接口稿，当前以后端对接主文档为准 |
| [`archive/架构/配置项.md`](archive/架构/配置项.md) | 配置项盘点稿，当前配置事实以 `.env.example`、`Settings` 和部署手册为准 |
| [`archive/deploy/`](archive/deploy/) | K8s、Kuboard、CI 平台类材料，不属于本仓库当前维护的 local / compose 部署主线 |
| [`archive/localization_workflow_v2.html`](archive/localization_workflow_v2.html) | 业务流程图参考，不作为接口或实现契约 |

## 快速阅读路径

理解服务架构：

```text
架构/架构总览.md
  ↓
job-implementation-guide.md
  ↓
架构/production-readiness-review.md
```

后端对接：

```text
接口层/小说本地化AI能力层_后端对接接口文档.md
  ↓
架构/架构总览.md
  ↓
部署与发布手册.md
```

Prompt 调整：

```text
app/workflows/novel_localization/prompts.yaml
  ↓
架构/架构总览.md
  ↓
template-usage.md
```

## 后续合并提炼规则

- 配置说明只保留两处：稳定规则写入 [`部署与发布手册.md`](部署与发布手册.md)，生产调参和准入口径写入 [`架构/production-readiness-review.md`](架构/production-readiness-review.md)。
- 通用 API 契约应优先沉淀到模板文档；`novel_localization` 文档只保留示例 workflow 的业务契约，不作为新 workflow 的默认对接依据。
- Job 机制说明分两层：[`架构/架构总览.md`](架构/架构总览.md) 讲心智模型和边界，[`job-implementation-guide.md`](job-implementation-guide.md) 讲当前实现选择和排障判断。
- `archive/` 只允许保存历史依据，不允许被 README、AGENTS.md 或对接方作为当前事实来源引用。
