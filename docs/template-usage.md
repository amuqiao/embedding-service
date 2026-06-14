# 模板使用指南

本文说明如何把本仓库作为新的 AI Job 后端模板使用，以及如何接入新的 workflow。

本模板提供的是通用执行层：FastAPI API、Celery 异步任务、对象存储 artifact、状态查询、Callback、模型配置和 workflow 注册机制。它不负责用户系统、项目管理、前端状态、业务流程编排或生产部署。

`novel_localization` 是内置示例 workflow，用来展示多 job type、分块、merge、artifact 解析和 Prompt YAML 的接入方式。新项目可以先保留它作为参考，也可以在替换完成后删除。

## 新项目替换清单

创建新项目时，优先替换下列稳定标识。

| 替换项 | 位置 | 当前值 |
|---|---|---|
| 项目包名 | `pyproject.toml` -> `[project].name` | `fastapi-ai-job-template` |
| Celery app 名称 | 由 `SERVICE_NAME` 自动派生 | `ai-job-service` |
| 服务展示标题 | `SERVICE_TITLE` | `AI Job Service` |
| API 前缀 | `SERVICE_API_PREFIX` | `/api/v1/ai-jobs` |
| OSS 输出前缀 | `OSS_OUTPUT_PREFIX` | `ai-jobs` |
| 数据库名 | `DATABASE_URL` | `ai_jobs` |

这些值决定对外服务身份、API 路径、对象存储路径和本地数据库名称。业务密钥、模型参数、Callback、Redis、PostgreSQL、对象存储等配置继续按 `.env.example` 维护。

## 处理内置示例

### 方案 A：保留示例

保留 `app/workflows/novel_localization/`，三个 `novel_localization.*` job type 会继续可用。这个方案适合在新 workflow 完成前保留一个可运行参考。

当你准备切换到自己的 Prompt 配置时，把 `PROMPT_CONFIG_PATH` 指向新的 YAML 文件。

### 方案 B：删除示例

如果新项目不需要小说本地化示例，按下面顺序删除：

1. 删除 `app/workflows/novel_localization/`。
2. 从 `app/workflows/register.py` 移除 `_register_novel_localization()`。
3. 删除或替换 `app/workflows/novel_localization/prompts.yaml`。
4. 如果仍使用 YAML Prompt 模板，把 `PROMPT_CONFIG_PATH` 指向新的 YAML 文件。

## 接入新 Workflow

一个 workflow 表示一组共享同一业务主题的 job type，例如 `document_translation` 或 `audio_transcription`。接入新 workflow 的最小路径是：写 handler、注册 handler、按需补 Prompt YAML、用 API 创建 Job 验证。

### 1. 创建 Handler 文件

推荐目录结构：

```text
app/workflows/
└── your_workflow/
    ├── __init__.py
    └── handler.py
```

`handler.py` 示例：

```python
from app.core.workflow_registry import WorkflowHandler, register
from app.schemas.jobs import JobResult


class MyJobHandler(WorkflowHandler):
    job_type = "your_workflow.my_job"   # 全局唯一
    canvas_pattern = "single"           # "single" | "memory_fanout" | "plain_chord" | "scan_chord"
    chunking_enabled = False            # True 表示大文本会进入分块流程
    max_single_chars = 20000            # 超过该字符数后触发分块；chunking_enabled=False 时忽略
    chunk_size = 3000                   # 每个 chunk 的目标字符数

    def parse_output(self, text: str) -> JobResult:
        return JobResult(
            artifacts=[{"key": "result", "type": "text", "label": "Result", "content": text}],
            signals={},
        )

    def merge_chunks(self, items) -> JobResult:
        # 只有 chunking_enabled=True 时需要实现
        raise NotImplementedError


def register_all() -> None:
    register(MyJobHandler())
```

### 2. 注册 Workflow

在 `app/workflows/register.py` 增加注册入口：

```python
from app.workflows.your_workflow.handler import register_all as _register_your_workflow

_register_your_workflow()
```

API、worker 和 pytest 都通过这个统一入口注册 workflow。新增 workflow 时不要只在 `app/main.py` 注册，否则 worker 进程可能拿不到 handler。

### 3. 定义 Prompt 模板

如果希望 `GET /prompt-templates` 返回该 job type，并让请求校验使用 YAML 中的 Prompt block 定义，需要在 `PROMPT_CONFIG_PATH` 指向的 YAML 文件里加入新 job type。

可以参考 `app/workflows/novel_localization/prompts.yaml` 的结构。

如果跳过 YAML 配置，只要 handler 已注册，registry 级别的 `job_type` 校验仍然可用；但 `GET /prompt-templates` 不会列出这个 job type。

### 4. 调用 API 验证

开发阶段可以使用 inline source，不需要先写 OSS 输入对象：

```bash
curl -X POST http://localhost:8100/api/v1/ai-jobs/jobs \
  -H "Authorization: Bearer $SERVICE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "job_type": "your_workflow.my_job",
    "model_id": "gpt-4.1",
    "source": {"inline": {"text": "Hello world"}},
    "callback": {"url": "http://localhost:9999/callback"},
    "prompt": {"blocks": [{"key": "user", "role": "user", "content": "Process this text."}]}
  }'
```

## Canvas Pattern

`canvas_pattern` 决定 Celery 如何编排一个 Job 的 work items。

| Pattern | 适用场景 | Celery 结构 |
|---|---|---|
| `single` | 单次模型调用，不需要分块 | `chain(execute -> finalize)` |
| `plain_chord` | 并行处理多个 chunk，不需要特殊前置或后置模型调用 | `chord(parallel_chunks -> finalize)` |
| `memory_fanout` | 先基于全文生成共享 memory，再并行处理 chunk | `chain(memory -> fanout(chunks) -> finalize)` |
| `scan_chord` | 并行处理 chunk 后，再做一次最终 scan / merge 模型调用 | `chord(chunks, scan) -> finalize` |

如果设置 `chunking_enabled=True`，还需要确保：

- `parse_output()` 能解析单个 chunk 的模型输出。
- `merge_chunks()` 能把多个 chunk 的 `result_payload` 合并成最终 `JobResult`。
- 大文本 artifact 应通过 handler 的 `large_artifact_keys` 写入对象存储，避免把正文直接塞进 JSON 响应。

## extra 参数

当某个 job type 需要额外业务参数，但这些参数不适合放进 `prompt.blocks` 时，使用 `CreateJobRequest.extra`。

请求示例：

```json
{
  "job_type": "your_workflow.my_job",
  "extra": {"target_language": "fr", "style": "formal"}
}
```

在 handler 中校验：

```python
def validate_extra(self, extra: dict | None) -> None:
    if extra and "target_language" not in extra:
        raise ValidationAppError("INVALID_INPUT", "extra.target_language is required")
```

`extra` 会存入 `AIJob.input_payload["extra"]`，handler 中可以通过 `(job.input_payload or {}).get("extra")` 读取。

## 新实例配置

部署一个新的服务实例时，通常只需要先改这些身份和路径类配置：

```bash
SERVICE_NAME=your-service-name       # Celery app 名称和 health service 字段
SERVICE_TITLE=Your Service Title     # FastAPI docs 标题
SERVICE_API_PREFIX=/api/v1/your-api  # Job 相关 API 前缀
OSS_OUTPUT_PREFIX=your-prefix/jobs   # Job 输出 artifact 的对象存储前缀
```

其余数据库、Redis、Callback、超时、对象存储、模型等配置见 `.env.example`。
