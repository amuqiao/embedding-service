# 小说本地化 AI 能力层文档导航

本文是 `docs/` 目录的入口索引，只列出当前仍维护的文档。

## 对接文档

| 文档 | 用途 |
|---|---|
| `接口层/README.md` | 接口层快速入口 |
| `接口层/小说本地化AI能力层_后端对接接口文档.md` | 后端对接主文档，包含创建 Job、轮询、callback、artifact 契约 |
| `接口层/小说本地化AI能力层接口文档.md` | 较完整的接口参考文档 |

## Prompt 与工作注释

| 文档 | 用途 |
|---|---|
| `prompt/短篇小说本地化_prompt.md` | 产品提供的 step1 本地化原始提示词素材 |
| `prompt/短篇小说本地化校验_prompt.md` | 产品提供的 step2 本地化校验原始提示词素材 |
| `prompt/短篇小说翻译_prompt.md` | 产品提供的 step3 翻译原始提示词素材 |
| `note.md` | 三阶段流程、工作注释、建议工作注释和重跑闭环说明 |
| `术语替换示例.md` | 本地化术语替换示例 |

运行时 Prompt 默认配置不直接读取 `docs/prompt`，而是读取 `PROMPT_CONFIG_PATH` 指向的 YAML，默认文件为 `app/infrastructure/novel_loc/prompts.yaml`。

## 运维与流程

| 文档 | 用途 |
|---|---|
| `部署与发布手册.md` | 本地开发、compose 部署、配置项、验证和排障 |
| `localization_workflow_v2.html` | 小说本地化业务流程图 |

## 快速阅读路径

后端对接优先阅读：

```text
接口层/小说本地化AI能力层_后端对接接口文档.md
  ↓
note.md
  ↓
部署与发布手册.md
```

Prompt 调整优先阅读：

```text
docs/prompt/ 三份原始素材
  ↓
app/infrastructure/novel_loc/prompts.yaml
  ↓
接口层/小说本地化AI能力层_后端对接接口文档.md
```
