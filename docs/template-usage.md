# Template Usage Guide

This project is a generic "AI Job execution + async tasks + artifact storage + status query + Callback" backend template. Novel localization is the built-in example workflow.

## Quick Start: New Project

### Step 1 — Required substitutions

| What | Where | Current value (change this) |
|---|---|---|
| Project name | `pyproject.toml` → `[project].name` | `cms-novel-localize` |
| Celery app name | Auto-derived from `SERVICE_NAME` env var | `ai-job-service` |
| Service display title | `SERVICE_TITLE` env var | `AI Job Service` |
| API URL prefix | `SERVICE_API_PREFIX` env var | `/api/v1/ai-jobs` |
| OSS output prefix | `OSS_OUTPUT_PREFIX` env var | `ai-jobs` |
| Database name | `DATABASE_URL` | `ai_jobs` |

### Step 2 — Decide what to do with the novel localization example

**Option A — Keep as example workflow (recommended for reference)**
Leave `app/workflows/novel_localization/` in place. The 3 job types remain available and serve as a working reference. Update `PROMPT_CONFIG_PATH` to point to your own `prompts.yaml` when ready.

**Option B — Remove it**
1. Delete `app/workflows/novel_localization/`
2. Remove the import + `_register_novel_localization()` call in `app/main.py`
3. Delete or replace `app/workflows/novel_localization/prompts.yaml` (and update `PROMPT_CONFIG_PATH`)
4. Remove `PROMPT_CONFIG_PATH` from `.env.example` if you don't use YAML-driven prompts

## Adding a New Workflow

A workflow = one or more job types sharing a common topic (e.g., "document_translation", "audio_transcription").

### Step 1 — Create your handler file

```
app/workflows/
└── your_workflow/
    ├── __init__.py
    └── handler.py
```

`handler.py` example:

```python
from app.core.workflow_registry import WorkflowHandler, register
from app.schemas.jobs import JobResult

class MyJobHandler(WorkflowHandler):
    job_type = "your_workflow.my_job"   # must be unique across all handlers
    canvas_pattern = "single"           # "single" | "memory_fanout" | "plain_chord" | "scan_chord"
    chunking_enabled = False            # True = split large inputs into chunks
    max_single_chars = 20000            # chars threshold; ignored if chunking_enabled=False
    chunk_size = 3000                   # target chars per chunk

    def parse_output(self, text: str) -> JobResult:
        # Parse raw LLM output → JobResult
        return JobResult(
            artifacts=[{"key": "result", "type": "text", "label": "Result", "content": text}],
            signals={},
        )

    def merge_chunks(self, items) -> JobResult:
        # Only needed if chunking_enabled=True
        raise NotImplementedError

def register_all() -> None:
    register(MyJobHandler())
```

### Step 2 — Register on startup

In `app/main.py`, add:
```python
from app.workflows.your_workflow.handler import register_all as _register_your_workflow
_register_your_workflow()
```

### Step 3 — Define prompts (optional)

If you want YAML-driven prompt templates (used by the `GET /prompt-templates` endpoint and request validation), add your job type to `PROMPT_CONFIG_PATH`'s YAML file. See `app/workflows/novel_localization/prompts.yaml` for the format.

If you skip this, validation still works (registry-based), but `GET /prompt-templates` won't list your job type.

### Step 4 — Test

```bash
# With inline source (no OSS needed for development)
curl -X POST http://localhost:8000/api/v1/ai-jobs/jobs \
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

## Canvas Patterns

The `canvas_pattern` property tells the framework how to orchestrate Celery tasks:

| Pattern | When to use | Celery structure |
|---|---|---|
| `single` | One-shot jobs; no chunking | chain(execute → finalize) |
| `plain_chord` | Parallel chunks, no special pre/post step | chord(parallel_chunks → finalize) |
| `memory_fanout` | Chunks need a shared "memory" built from the full text first | chain(memory → fanout(chunks) → finalize) |
| `scan_chord` | Parallel chunks, then a final scan/merge pass | chord(chunks, scan) → finalize |

## extra_params Pattern

For job-type-specific params that don't fit in `prompt.blocks`, use `CreateJobRequest.extra`:

```json
{
  "job_type": "your_workflow.my_job",
  "extra": {"target_language": "fr", "style": "formal"}
}
```

Validate in your handler:
```python
def validate_extra(self, extra: dict | None) -> None:
    if extra and "target_language" not in extra:
        raise ValidationAppError("INVALID_INPUT", "extra.target_language is required")
```

`extra` is stored in `AIJob.input_payload["extra"]` and available to your handler as `(job.input_payload or {}).get("extra")`.

## Configuration Reference

Only these keys need changing when deploying a new instance:

```bash
SERVICE_NAME=your-service-name       # used for Celery app name + health endpoint
SERVICE_TITLE=Your Service Title     # FastAPI docs title
SERVICE_API_PREFIX=/api/v1/your-api  # URL prefix for all job endpoints
OSS_OUTPUT_PREFIX=your-prefix/jobs   # OSS key prefix for job output artifacts
```

All other settings (DB, Redis, callback, timeouts) are described in `.env.example`.
