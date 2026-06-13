# 小说本地化 AI 能力层文档导航

本文是 `docs/` 目录的入口索引，只列出当前仍维护的文档。

## 架构与实现

| 文档 | 用途 |
|---|---|
| `架构/README.md` | 架构目录入口 |
| `架构/架构总览.md` | 服务定位、边界、API、Job 生命周期、异步执行、Callback、恢复机制、数据模型和扩展边界 |
| `架构/production-readiness-review.md` | 生产就绪性评审、风险清单和修复状态 |
| `job-implementation-guide.md` | Job 系统实施说明：执行模式、超时链、恢复机制、错误码、运维速查 |
| `job-env-vars-quick-reference.md` | Job 关键环境变量速查：区分 `.env`、`.env.dev`、`.env.test`，辅助排障、吞吐控制和横向扩容 |
| `async-job-spec.md` | 通用 AI 异步 Job 系统设计规范，本项目实施以 `job-implementation-guide.md` 和代码为准 |

## 对接文档

| 文档 | 用途 |
|---|---|
| `接口层/小说本地化AI能力层_后端对接接口文档.md` | 后端对接主文档，包含创建 Job、轮询、callback、artifact 契约 |
| `接口层/小说本地化AI能力层接口文档.md` | 较完整的接口参考文档 |

## 运维与流程

| 文档 | 用途 |
|---|---|
| `部署与发布手册.md` | 本地开发、compose 部署、配置项、验证和排障 |
| `localization_workflow_v2.html` | 小说本地化业务流程图 |

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
app/infrastructure/novel_loc/prompts.yaml
  ↓
架构/架构总览.md
  ↓
接口层/小说本地化AI能力层_后端对接接口文档.md
```
