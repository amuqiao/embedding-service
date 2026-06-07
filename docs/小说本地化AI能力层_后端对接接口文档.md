# 小说本地化 AI 能力层后端对接接口文档

本文档面向业务后端开发，定义首版对接小说本地化 AI 能力层需要实现的接口调用、字段校验、错误处理和轮询规则。

## 1. 对接边界

业务后端负责：

```text
用户系统
项目管理
原文保存
PromptConfig 保存
步骤状态机
上游步骤结果保存
用户是否重跑的业务决策
```

AI 能力层负责：

```text
返回模型列表
返回默认 Prompt 模板
接收本次任务输入和 Prompt
创建 AI Job
将大文本结果写入 OSS
返回 Job 状态和结构化结果
任务结束后回调业务后端
```

业务后端调用 AI 能力层时，必须传入本次任务完整输入、完整 `prompt.blocks`、大文本输出 OSS 位置和回调地址。AI 能力层不通过 `project_id` 查询原文，不保存用户 PromptConfig，不查询历史 artifact。

## 2. 鉴权

所有接口使用固定服务级 Bearer API Key。

```http
Authorization: Bearer <service_api_key>
```

规则：

```text
service_api_key 只允许配置在业务后端 / BFF。
浏览器前端不得持有 service_api_key。
AI 能力层不提供登录、获取 token、刷新 token、用户信息接口。
```

## 3. 首版接口

| 能力 | 方法 | 路径 |
|---|---|---|
| 健康检查 | `GET` | `/health` |
| 支持模型列表 | `GET` | `/models` |
| 默认 Prompt 模板 | `GET` | `/prompt-templates` |
| 创建 AI 任务 | `POST` | `/jobs` |
| 查询 AI 任务 | `GET` | `/jobs/{job_id}` |

首版不提供以下请求字段：

```text
options
execution_mode
runtime_prompt_blocks
```

`POST /jobs` 请求体中出现以上字段时，返回 `422`。

## 4. GET /health

响应：

```json
{
  "status": "ok",
  "service": "novel-localization-ai",
  "version": "1.0.0"
}
```

## 5. GET /models

响应：

```json
{
  "default_model_id": "gpt-xxx",
  "models": [
    {
      "id": "gpt-xxx",
      "name": "GPT xxx",
      "provider": "openai",
      "enabled": true,
      "context_window": 128000,
      "supports_json_output": true,
      "notes": "用于小说本地化"
    }
  ]
}
```

规则：

```text
default_model_id 仅用于业务后端初始化默认选择。
POST /jobs 必须显式传 model_id。
model_id 必须来自 models[].id，且 enabled=true。
缺失或不可用返回 422，error.code=MODEL_NOT_AVAILABLE。
```

## 6. GET /prompt-templates

响应：

```json
{
  "version": "2026-06-05",
  "job_types": [
    {
      "job_type": "novel_localization.step1_localize",
      "name": "本地化",
      "description": "将中文短篇小说进行本地化改写。",
      "prompt_blocks": [
        {
          "key": "system",
          "role": "system",
          "label": "系统 Prompt",
          "default_content": "系统提示词..."
        },
        {
          "key": "user",
          "role": "user",
          "label": "用户 Prompt",
          "default_content": "用户提示词默认值..."
        },
        {
          "key": "work_note",
          "role": "user",
          "label": "工作注释 Prompt",
          "default_content": ""
        }
      ]
    },
    {
      "job_type": "novel_localization.step2_review",
      "name": "本地化校验",
      "description": "检查本地化结果是否满足要求，并在失败时生成优化建议。",
      "prompt_blocks": [
        {
          "key": "system",
          "role": "system",
          "label": "系统 Prompt",
          "default_content": "系统提示词..."
        },
        {
          "key": "user",
          "role": "user",
          "label": "用户 Prompt",
          "default_content": "用户提示词默认值..."
        },
        {
          "key": "work_note",
          "role": "user",
          "label": "工作注释 Prompt",
          "default_content": ""
        }
      ]
    },
    {
      "job_type": "novel_localization.step3_translate",
      "name": "英文翻译",
      "description": "将本地化后的中文稿翻译为英文。",
      "prompt_blocks": [
        {
          "key": "system",
          "role": "system",
          "label": "系统 Prompt",
          "default_content": "系统提示词..."
        },
        {
          "key": "user",
          "role": "user",
          "label": "用户 Prompt",
          "default_content": "用户提示词默认值..."
        },
        {
          "key": "work_note",
          "role": "user",
          "label": "工作注释 Prompt",
          "default_content": ""
        }
      ]
    }
  ]
}
```

规则：

```text
job_type 必须来自 job_types[].job_type。
prompt.blocks 必须传齐当前 job_type 返回的全部 prompt_blocks。
当前首版固定为 system、user、work_note 三个 block。
work_note.content 可以是空字符串。
AI 能力层不会自动用 default_content 补齐缺失 block。
```

## 7. POST /jobs

创建 AI 异步任务。成功返回 `202 Accepted`。

### 7.1 text 输入请求

```json
{
  "client_request_id": "optional-idempotency-key",
  "job_type": "novel_localization.step1_localize",
  "model_id": "gpt-xxx",
  "input": {
    "type": "text",
    "content": "小说原文或上一步结果",
    "content_hash": "sha256:<64位小写hex>"
  },
  "output": {
    "type": "oss_prefix",
    "oss_bucket": "output-bucket-name",
    "oss_prefix": "novel-localization/jobs/job-123/",
    "oss_region": "cn-hangzhou"
  },
  "callback": {
    "url": "https://backend.example.com/internal/ai-callbacks/novel-localization",
    "events": ["job.succeeded", "job.failed"]
  },
  "prompt": {
    "blocks": [
      {
        "key": "system",
        "role": "system",
        "content": "系统 prompt"
      },
      {
        "key": "user",
        "role": "user",
        "content": "用户 prompt"
      },
      {
        "key": "work_note",
        "role": "user",
        "content": "工作注释 prompt"
      }
    ]
  },
  "metadata": {
    "external_project_ref": "业务项目 ID",
    "external_step_ref": "业务步骤 ID",
    "external_job_ref": "业务后端任务 ID",
    "triggered_by": "optional-external-user-ref"
  }
}
```

### 7.2 OSS 输入请求

```json
{
  "client_request_id": "optional-idempotency-key",
  "job_type": "novel_localization.step1_localize",
  "model_id": "gpt-xxx",
  "input": {
    "type": "oss_object",
    "oss_bucket": "your-bucket-name",
    "oss_key": "novels/project-123/original.txt",
    "oss_region": "cn-hangzhou",
    "content_hash": "sha256:<64位小写hex>"
  },
  "output": {
    "type": "oss_prefix",
    "oss_bucket": "output-bucket-name",
    "oss_prefix": "novel-localization/jobs/job-123/",
    "oss_region": "cn-hangzhou"
  },
  "callback": {
    "url": "https://backend.example.com/internal/ai-callbacks/novel-localization",
    "events": ["job.succeeded", "job.failed"]
  },
  "prompt": {
    "blocks": [
      {
        "key": "system",
        "role": "system",
        "content": "系统 prompt"
      },
      {
        "key": "user",
        "role": "user",
        "content": "用户 prompt"
      },
      {
        "key": "work_note",
        "role": "user",
        "content": "工作注释 prompt"
      }
    ]
  },
  "metadata": {
    "external_project_ref": "业务项目 ID",
    "external_step_ref": "业务步骤 ID",
    "external_job_ref": "业务后端任务 ID"
  }
}
```

### 7.3 成功响应

```json
{
  "job_id": "uuid",
  "status": "queued",
  "status_url": "/api/v1/novel-localization-ai/jobs/uuid",
  "created_at": "datetime"
}
```

### 7.4 字段规则

| 字段 | 必填 | 规则 |
|---|---|---|
| `client_request_id` | 否 | 幂等键。24 小时内相同调用方、相同 key 返回首次创建的 `job_id`。 |
| `job_type` | 是 | 必须来自 `GET /prompt-templates`。 |
| `model_id` | 是 | 必须来自 `GET /models` 且 `enabled=true`。 |
| `input` | 是 | 支持 `text` 或 `oss_object`。 |
| `output` | 是 | 大文本结果输出位置，首版固定为 `oss_prefix`。 |
| `callback` | 是 | Job 进入终态后通知业务后端的回调配置。 |
| `prompt.blocks` | 是 | 必须传齐当前 `job_type` 的全部 prompt blocks。 |
| `metadata` | 否 | 仅用于审计、排障和日志关联，不参与 AI 执行。 |

`input.type=text`：

| 字段 | 必填 | 规则 |
|---|---|---|
| `type` | 是 | 固定为 `text`。 |
| `content` | 是 | UTF-8 文本，最大 1 MB。 |
| `content_hash` | 否 | 格式必须为 `sha256:<64位小写hex>`；传入时必须校验。 |

`input.type=oss_object`：

| 字段 | 必填 | 规则 |
|---|---|---|
| `type` | 是 | 固定为 `oss_object`。 |
| `oss_bucket` | 是 | OSS bucket 名称。 |
| `oss_key` | 是 | OSS object key。 |
| `oss_region` | 是 | OSS region，例如 `cn-hangzhou`。 |
| `content_hash` | 否 | 格式必须为 `sha256:<64位小写hex>`；传入时读取后必须校验。 |

OSS 规则：

```text
调用方不得传 OSS AccessKey、Secret 或临时凭证。
AI 能力层使用自身配置的 OSS 凭证读取对象。
OSS 对象必须是 UTF-8 文本。
OSS 对象读取后最大 5 MB。
OSS 拉取失败时，Job 进入 failed，error.code=OSS_FETCH_FAILED。
content_hash 校验失败时，Job 进入 failed，error.code=INPUT_HASH_MISMATCH。
```

`output` 规则：

```text
output.type 首版固定为 oss_prefix。
output.oss_bucket、output.oss_prefix、output.oss_region 必填。
AI 能力层使用自身配置的 OSS 凭证写入结果文件。
本地化正文和英文翻译正文必须写入 OSS，不得在 JSON 中直接返回全文。
AI 能力层写入完成后，在 artifact 中返回实际 oss_key、content_hash 和 content_size_bytes。
OSS 写入失败时，Job 进入 failed，error.code=OSS_WRITE_FAILED。
```

大文本 artifact key 规则：

```text
novel_localization.step1_localize 的正文结果 key 固定为 localized_text。
novel_localization.step3_translate 的正文结果 key 固定为 translated_text。
localized_text 和 translated_text 必须使用 storage=oss_object 返回，不得使用 content 返回全文。
review_summary、optimization_prompt、notes 这类短文本可以使用 content 返回。
```

`callback` 规则：

```text
callback.url 必填，必须是业务后端可访问的 HTTPS 地址。
callback.events 首版固定支持 job.succeeded 和 job.failed；不传时默认订阅这两个事件。
Job 进入 succeeded 或 failed 后，AI 能力层必须 POST callback.url。
Callback 是完成通知，不替代 GET /jobs/{job_id}；业务后端必须保留查询兜底。
Callback 投递失败不改变 Job 最终状态。
Callback 失败后重试 3 次，重试间隔固定为 10 秒、30 秒、60 秒。
业务后端必须按 job_id + event 做幂等去重。
```

Callback 请求头：

```http
X-AI-Service-Job-Id: <job_id>
X-AI-Service-Event: job.succeeded
X-AI-Service-Timestamp: <iso8601-utc>
X-AI-Service-Signature: sha256=<hmac>
```

Callback 签名规则：

```text
签名密钥由双方线下配置，不通过接口传输。
签名原文为 timestamp + "." + request_body。
算法为 HMAC-SHA256。
业务后端收到 callback 后必须校验 timestamp 和 signature。
```

`prompt.blocks[]` 规则：

```text
key 必须来自当前 job_type 的 prompt_blocks[].key。
role 必须与 prompt_templates 中对应 key 的 role 一致。
content 不能为 null，允许空字符串。
缺失 key、未知 key、重复 key 返回 422。
AI 能力层不会自动用 default_content 补齐缺失 block。
```

`metadata` 规则：

```text
metadata 不参与 AI 执行逻辑。
AI 能力层不校验 metadata 内部字段含义。
metadata 中的值必须是 string、number、boolean 或 null。
metadata 不允许嵌套对象和数组。
metadata 单次请求序列化后不得超过 8 KB，超过返回 422。
```

## 8. GET /jobs/{job_id}

查询任务状态、进度和结果。

### 8.1 运行中响应

```json
{
  "job_id": "uuid",
  "job_type": "novel_localization.step1_localize",
  "status": "running",
  "progress_percent": 42,
  "progress_text": "正在处理文本",
  "result": null,
  "error": null,
  "created_at": "datetime",
  "started_at": "datetime",
  "finished_at": null
}
```

### 8.2 成功响应

```json
{
  "job_id": "uuid",
  "job_type": "novel_localization.step1_localize",
  "status": "succeeded",
  "progress_percent": 100,
  "progress_text": "已完成",
  "result": {
    "artifacts": [
      {
        "key": "localized_text",
        "type": "text",
        "label": "本地化正文",
        "storage": "oss_object",
        "oss_bucket": "output-bucket-name",
        "oss_key": "novel-localization/jobs/job-123/localized.txt",
        "oss_region": "cn-hangzhou",
        "content_hash": "sha256:<64位小写hex>",
        "content_size_bytes": 123456
      },
      {
        "key": "notes",
        "type": "text",
        "label": "工作注释",
        "content": "模型输出的注释..."
      }
    ],
    "signals": {}
  },
  "error": null,
  "created_at": "datetime",
  "started_at": "datetime",
  "finished_at": "datetime"
}
```

### 8.3 校验未通过但任务成功响应

```json
{
  "job_id": "uuid",
  "job_type": "novel_localization.step2_review",
  "status": "succeeded",
  "progress_percent": 100,
  "result": {
    "artifacts": [
      {
        "key": "review_summary",
        "type": "text",
        "label": "校验结果",
        "content": "校验未通过，存在称呼不一致问题。"
      },
      {
        "key": "optimization_prompt",
        "type": "prompt_suggestion",
        "label": "优化建议 Prompt",
        "content": "重新本地化时请统一角色称呼，并保持语气一致。",
        "target": {
          "job_type": "novel_localization.step1_localize",
          "prompt_block_key": "work_note",
          "default_mode": "append"
        }
      }
    ],
    "signals": {
      "passed": false
    }
  },
  "error": null,
  "finished_at": "datetime"
}
```

规则：

```text
status=succeeded 表示 AI Job 执行成功。
signals.passed=false 表示校验业务结果未通过。
校验未通过不是系统错误，不返回 status=failed。
```

### 8.4 失败响应

```json
{
  "job_id": "uuid",
  "job_type": "novel_localization.step1_localize",
  "status": "failed",
  "progress_percent": 42,
  "progress_text": "处理失败",
  "result": null,
  "error": {
    "code": "MODEL_CALL_FAILED",
    "message": "模型调用失败或内部处理失败",
    "details": {}
  },
  "created_at": "datetime",
  "started_at": "datetime",
  "finished_at": "datetime"
}
```

字段规则：

```text
status 取值：queued | running | succeeded | failed | canceled。
status=succeeded 时 progress_percent 必须为 100。
status=failed/canceled 时 progress_percent 返回最后一次已知进度。
status=failed 时必须返回 error。
status=queued/running/succeeded 时 error 必须为 null。
```

## 9. Callback 通知

AI 能力层在 Job 进入终态后，向 `POST /jobs` 中的 `callback.url` 发送通知。

成功通知示例：

```json
{
  "event": "job.succeeded",
  "job_id": "uuid",
  "job_type": "novel_localization.step1_localize",
  "status": "succeeded",
  "result": {
    "artifacts": [
      {
        "key": "localized_text",
        "type": "text",
        "label": "本地化正文",
        "storage": "oss_object",
        "oss_bucket": "output-bucket-name",
        "oss_key": "novel-localization/jobs/job-123/localized.txt",
        "oss_region": "cn-hangzhou",
        "content_hash": "sha256:<64位小写hex>",
        "content_size_bytes": 123456
      }
    ],
    "signals": {}
  },
  "error": null,
  "metadata": {
    "external_project_ref": "业务项目 ID",
    "external_job_ref": "业务后端任务 ID"
  },
  "finished_at": "datetime"
}
```

失败通知示例：

```json
{
  "event": "job.failed",
  "job_id": "uuid",
  "job_type": "novel_localization.step1_localize",
  "status": "failed",
  "result": null,
  "error": {
    "code": "MODEL_CALL_FAILED",
    "message": "模型调用失败或内部处理失败",
    "details": {}
  },
  "metadata": {
    "external_project_ref": "业务项目 ID",
    "external_job_ref": "业务后端任务 ID"
  },
  "finished_at": "datetime"
}
```

规则：

```text
Callback body 与 GET /jobs/{job_id} 的终态核心结构保持一致。
Callback 成功条件为业务后端返回 2xx。
业务后端返回非 2xx 或请求超时，AI 能力层按重试规则重试。
Callback 失败不改变 Job 状态；业务后端可通过 GET /jobs/{job_id} 兜底查询。
```

## 10. 错误响应

非 2xx 响应统一使用：

```json
{
  "error": {
    "code": "INVALID_JOB_TYPE",
    "message": "不支持的 job_type: novel_localization.step99_xxx",
    "details": {}
  }
}
```

错误码：

| code | 场景 |
|---|---|
| `INVALID_JOB_TYPE` | 不支持的 `job_type`。 |
| `MODEL_NOT_AVAILABLE` | 模型不存在或不可用。 |
| `INVALID_INPUT` | 输入为空、格式错误、编码错误。 |
| `INPUT_TOO_LARGE` | 输入超过大小限制。 |
| `INPUT_HASH_MISMATCH` | `content_hash` 校验失败。 |
| `OSS_OBJECT_NOT_FOUND` | OSS 对象不存在。 |
| `OSS_FETCH_FAILED` | OSS 对象读取失败。 |
| `OSS_WRITE_FAILED` | OSS 对象写入失败。 |
| `JOB_NOT_FOUND` | `job_id` 不存在。 |
| `JOB_ALREADY_CANCELED` | 任务已取消。 |
| `MODEL_CALL_FAILED` | 模型调用失败。 |
| `JOB_TIMEOUT` | 任务排队或执行超时。 |

## 11. 限制与超时

| 项 | 限制 | 失败行为 |
|---|---:|---|
| `input.type=text` | 最大 1 MB | 返回 `INPUT_TOO_LARGE`。 |
| `input.type=oss_object` | 最大 5 MB | Job 进入 `failed`，`error.code=INPUT_TOO_LARGE`。 |
| OSS 文本编码 | UTF-8 | Job 进入 `failed`，`error.code=INVALID_INPUT`。 |
| Job 排队超时 | 10 分钟 | Job 进入 `failed`，`error.code=JOB_TIMEOUT`。 |
| Job 执行超时 | 30 分钟 | Job 进入 `failed`，`error.code=JOB_TIMEOUT`。 |

## 12. 轮询规则

```text
queued / running 状态每 2 秒轮询一次。
连续请求失败时做指数退避，例如 2 秒、4 秒、8 秒、16 秒。
进入 succeeded / failed / canceled 后停止轮询。
首版不提供 GET /jobs/{job_id}/stream。
```

## 13. 标准调用流程

### 13.1 初始化

```text
业务后端 GET /models，保存可用模型列表。
业务后端 GET /prompt-templates，保存默认 Prompt 模板。
业务后端在自己的系统中保存用户修改后的 PromptConfig。
```

### 13.2 执行本地化

```text
业务后端读取项目原文和 PromptConfig。
业务后端 POST /jobs，job_type=novel_localization.step1_localize。
AI 能力层返回 job_id。
业务后端 GET /jobs/{job_id} 轮询。
业务后端保存 localized_text 的 OSS 引用和 notes 到自己的业务库。
```

### 13.3 执行校验

```text
业务后端读取 step1 本地化结果。
业务后端 POST /jobs，job_type=novel_localization.step2_review。
AI 能力层返回 review_summary / optimization_prompt / signals.passed。
业务后端根据 signals.passed 决定业务状态。
```

### 13.4 使用校验建议重跑

```text
业务后端取出 optimization_prompt。
业务后端把 optimization_prompt 合并到 prompt.blocks，例如追加到 work_note.content。
业务后端重新 POST /jobs，job_type=novel_localization.step1_localize。
AI 能力层返回新的本地化结果。
```

## 14. 后期可能提供的 AI 能力层接口

以下接口不进入首版对接范围。当前只给后端做能力预期参考，不定义具体入参和出参。后续确认需要实现时，再单独补充接口契约。

| 能力 | 方法 | 路径 | 用途 | 当前状态 |
|---|---|---|---|---|
| 查询任务快照 | `GET` | `/jobs/{job_id}/snapshot` | 用于排障和审计，查看某次 Job 冻结的模型、输入摘要、Prompt 摘要和调用方 metadata。 | 后期可选 |
| 查询完整 Prompt | `GET` | `/jobs/{job_id}/full-prompt` | 用于排查 Prompt 拼接问题，查看某次 Job 实际发送给模型的 Prompt。 | 后期可选 |
| 取消任务 | `POST` | `/jobs/{job_id}/cancel` | 用于取消排队中或运行中的长耗时任务。 | 后期可选 |
| 任务 SSE | `GET` | `/jobs/{job_id}/stream` | 用于替代轮询，实时推送 Job 状态和进度。 | 后期可选 |

首版后端必须按 `GET /jobs/{job_id}` 轮询实现，不依赖以上接口。

## 15. 不属于 AI 能力层的接口

以下接口属于业务后端 / BFF，不由 AI 能力层提供：

```text
POST /projects
GET /projects
GET /projects/{project_id}
DELETE /projects/{project_id}
GET /projects/{project_id}/steps/{step_code}/prompt
PUT /projects/{project_id}/steps/{step_code}/prompt
GET /projects/{project_id}/export
```
