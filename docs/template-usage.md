# 模板使用指南

本文说明如何把本仓库作为新的 AI Job 后端模板使用，以及如何接入新的 workflow。

本模板提供的是通用 Job 执行层：FastAPI API、Celery 异步任务、对象存储产物、状态轮询、Callback、模型配置和 workflow 注册机制。它不负责用户系统、项目管理、前端状态、业务流程编排或生产部署。

Job 公共骨架只关心 `client_request_id`、`job_type`、`job_params`、`callback`、`metadata` 和 `options`。具体任务入参由 `job_type` 自己的 `job_params` schema 定义；具体任务出参由 `job_type` 自己的 `result` schema 定义。`novel_localization` 是内置示例 workflow，用来展示如何把文本 LLM 任务适配到这套通用 Job 骨架。

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

一个 workflow 表示一组共享同一业务主题的 job type，例如 `document_translation` 或 `audio_transcription`。接入新 workflow 的最小路径是：写 handler、注册 handler、为 `job_params` 提供 schema 归一化、按需补 Prompt YAML、用 API 创建 Job 验证。

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

    def normalize_job_params(self, job_params: dict) -> dict:
        # 在这里使用本 job_type 的 Pydantic/JSON Schema 校验并返回规范化参数。
        return job_params

    def runtime_job_fields(self, job_params: dict) -> dict:
        # 只有需要当前 LLM 执行器的 workflow 才返回 model_id / prompt_payload。
        # 非 LLM workflow 可以返回空 dict，但必须同时实现 build_execution_plan()
        # 和 execute_standard_item()，避免默认文本分块计划读取 source。
        return {}

    def build_execution_plan(self, job):
        # 非 LLM / 非文本 source workflow 在这里基于 job_params_from_job(job)
        # 创建自己的 work_items。返回 None 会回到默认文本 source 分块计划。
        return None

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

`POST /jobs` 的顶层字段保持通用；文本、图片、Prompt、模型等具体任务参数都放在 `job_params` 中。内置 `generic.echo` 用来质检通用骨架，不调用模型，不需要 `source` / `prompt` / `model_id`：

```bash
curl -X POST http://localhost:8100/api/v1/ai-jobs/jobs \
  -H "Authorization: Bearer $SERVICE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "job_type": "generic.echo",
    "job_params": {
      "value": {"hello": "world"},
      "label": "Echo"
    },
    "metadata": {"caller_task_id": "echo-1"}
  }'
```

以内置小说本地化示例为例，开发阶段也可以使用 inline source，不需要先写 OSS 输入对象：

```bash
curl -X POST http://localhost:8100/api/v1/ai-jobs/jobs \
  -H "Authorization: Bearer $SERVICE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "job_type": "your_workflow.my_job",
    "callback": {"url": "http://localhost:9999/callback"},
    "metadata": {"caller_task_id": "task-1"},
    "options": {"priority": "normal", "timeout_seconds": 300},
    "job_params": {
      "model_id": "gpt-4.1",
      "source": {"inline": {"text": "Hello world"}},
      "prompt": {"blocks": [{"key": "user", "role": "user", "content": "Process this text."}]}
    }
  }'
```

创建成功只表示 Job 已进入队列：

```json
{
  "job_id": "uuid",
  "client_request_id": "optional-idempotency-key",
  "job_type": "your_workflow.my_job",
  "status": "queued",
  "status_url": "/api/v1/ai-jobs/jobs/uuid",
  "created_at": "2026-06-15T10:00:00Z"
}
```

`GET /jobs/{job_id}` 返回统一 JobView。`queued` / `running` 时 `result` 和 `error` 都为 `null`；`succeeded` 时 `result` 由 job_type 的结果 schema 定义；`failed` 时 `error` 使用统一错误结构。`callback` 描述终态通知的投递状态；Callback 投递失败只重试 Callback，不重新执行 Job。

```json
{
  "job_id": "uuid",
  "client_request_id": "optional-idempotency-key",
  "job_type": "your_workflow.my_job",
  "status": "running",
  "progress": {"percent": 30, "message": "processing", "stage": null},
  "result": null,
  "error": null,
  "callback": {
    "status": "pending",
    "attempts": 0,
    "next_retry_at": null,
    "last_error": null
  },
  "metadata": {"caller_task_id": "task-1"},
  "created_at": "2026-06-15T10:00:00Z",
  "started_at": "2026-06-15T10:00:03Z",
  "finished_at": null
}
```

Callback 只在终态事件触发，body 使用公共 Job 事件字段加 `data` 扩展，不嵌套 `JobView`。Callback 正文不携带投递状态；发送完成后的 `delivered` / `failed` / `next_retry_at` 以之后的 `GET /jobs/{job_id}` 为准。

```json
{
  "schema_version": "v1",
  "event": "job.succeeded",
  "event_id": "uuid",
  "attempt": 1,
  "sent_at": "2026-06-15T10:01:00Z",
  "job_id": "uuid",
  "client_request_id": "optional-idempotency-key",
  "job_type": "your_workflow.my_job",
  "status": "succeeded",
  "progress": {"percent": 100, "message": "已完成", "stage": null},
  "error": null,
  "metadata": {"caller_task_id": "task-1"},
  "data": {},
  "created_at": "2026-06-15T10:00:00Z",
  "started_at": "2026-06-15T10:00:03Z",
  "finished_at": "2026-06-15T10:01:00Z"
}
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
- `merge_chunks()` 能把多个 chunk work item 的 `result` 合并成最终 `JobResult`。
- 大文本 artifact 应通过 handler 的 `large_artifact_keys` 写入对象存储；该规则同时作用于 work item 中间结果和最终 Job 结果，避免把正文直接塞进数据库或 JSON 响应。

## job_params 参数

当某个 job type 需要业务参数时，统一放入 `CreateJobRequest.job_params`，并由对应 handler 的 `normalize_job_params()` 校验。Job 公共层不会解释这些字段。

```json
{
  "job_type": "your_workflow.my_job",
  "job_params": {
    "target_language": "fr",
    "style": "formal"
  }
}
```

在 handler 中校验并规范化：

```python
def normalize_job_params(self, job_params: dict) -> dict:
    params = MyJobParams.model_validate(job_params)
    return params.model_dump()
```

`job_params` 会被规范化后写入运行时对象，并在 `AIJob.job_params_ref` 中保存引用；`AIJob.job_params_hash` 用于执行前校验引用内容没有漂移。创建 Job 时还会写入 `runtime_ref`，保存 handler 当时派生出的 `runtime_fields` 和输出目标引用。当前内置 `novel_localization` 为了复用现有 LLM 执行器，会通过 `runtime_job_fields()` 把 `job_params.model_id` 和 `job_params.prompt` 映射为运行时字段；这不是通用 Job 创建层的要求。

## 新实例配置

部署一个新的服务实例时，通常只需要先改这些身份和路径类配置：

```bash
SERVICE_NAME=your-service-name       # Celery app 名称和 health service 字段
SERVICE_TITLE=Your Service Title     # FastAPI docs 标题
SERVICE_API_PREFIX=/api/v1/your-api  # Job 相关 API 前缀
OSS_OUTPUT_PREFIX=your-prefix/jobs   # Job 输出 artifact 的对象存储前缀
```

其余数据库、Redis、Callback、超时、对象存储、模型等配置见 `.env.example`。
