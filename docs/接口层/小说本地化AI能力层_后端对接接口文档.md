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
  "version": "2026-06-10.1",
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
          "label": "已有工作注释 Prompt",
          "default_content": ""
        }
      ]
    },
    {
      "job_type": "novel_localization.step2_review",
      "name": "本地化校验",
      "description": "检查本地化结果是否满足要求，并在失败时生成建议工作注释。",
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
          "label": "当前工作注释 Prompt",
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
          "label": "当前工作注释 Prompt",
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
work_note.content 可以是空字符串。空字符串表示本次没有已确认的工作注释输入，AI 能力层运行时不会向模型发送空的工作注释 message。
AI 能力层不会自动用 default_content 补齐缺失 block。
role 只属于 prompt block，表示 AI 能力层组装模型 messages 时使用的消息角色。
user.default_content 默认来自 PROMPT_CONFIG_PATH 指定的 YAML 配置；当前默认 YAML 已内置用户提示词内容。
业务后端如需使用默认用户提示词，应读取 user.default_content 后显式放入 POST /jobs 的 prompt.blocks。
AI 能力层运行时会按 job_type 追加输出格式契约，确保模型输出能解析为标准 artifact。
```

## 7. POST /jobs

创建 AI 异步任务。成功返回 `202 Accepted`。

### 7.1 source.oss 输入请求

新建 Job 只支持 OSS 对象输入。业务后端应先把原文或上游步骤结果写入 OSS，再把对象引用传给 AI 能力层。OSS bucket、region、endpoint 和访问凭证由 AI 能力层配置文件提供，不在请求体中传递。

```json
{
  "client_request_id": "optional-idempotency-key",
  "job_type": "novel_localization.step1_localize",
  "model_id": "gpt-xxx",
  "source": {
    "oss": {
      "oss_key": "novel-localization/editable/10001/res_translate_001/final_en.txt",
      "oss_url": "https://novel-localization/editable/10001/res_translate_001/final_en.txt",
      "content_hash": "sha256:<64位小写hex>",
      "content_type": "text/plain; charset=utf-8"
    }
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
        "content": "已有工作注释；初始 step1 通常为空"
      }
    ]
  }
}
```

### 7.2 step1_localize 重跑时的关键差异

**初始调用** step1_localize，work_note 通常为空：

```json
{
  "job_type": "novel_localization.step1_localize",
  "prompt": {
    "blocks": [
      {"key": "system", "role": "system", "content": "..."},
      {"key": "user", "role": "user", "content": "..."},
      {"key": "work_note", "role": "user", "content": ""}
    ]
  }
}
```

**根据 step2_review 反馈重跑** step1_localize，source 换成本地化稿，work_note 填入 step2 返回的建议工作注释：

```json
{
  "job_type": "novel_localization.step1_localize",
  "source": {
    "oss": {
      "oss_key": "step1 输出的 localized_text oss_key",
      "oss_url": "...",
      "content_type": "text/plain; charset=utf-8"
    }
  },
  "prompt": {
    "blocks": [
      {"key": "system", "role": "system", "content": "..."},
      {"key": "user", "role": "user", "content": "..."},
      {"key": "work_note", "role": "user", "content": "【step2 返回的建议工作注释】"}
    ]
  }
}
```

**关键差异**：
- ✅ `job_type` 保持 `novel_localization.step1_localize`（不改）
- ✅ `callback` 保持一致（不改）
- ✅ `source` **改为** step1 输出的 `localized_text` 的 OSS key——重跑的输入是本地化稿，不是原文
- ✅ `work_note` **改为** step2 返回的建议工作注释（`apply_mode=replace`，直接替换使用，不与旧注释合并）
- ✅ step1 的提示词约束"对于无明显问题的文字保持不变"确保模型只修正 work_note 指出的问题，不重写整篇

**用户手动编辑工作注释后重跑**与上述模式相同：source 传本地化稿 OSS key，work_note 传用户编辑后的注释内容。

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
| `source` | 是 | 本次任务输入对象引用，仅支持 `source.oss`。 |
| `callback` | 是 | Job 进入终态后通知业务后端的回调配置。 |
| `prompt.blocks` | 是 | 必须传齐当前 `job_type` 的全部 prompt blocks。 |

`source.oss`：

| 字段 | 必填 | 规则 |
|---|---|---|
| `oss_key` | 是 | OSS object key。 |
| `oss_url` | 是 | 调用方用于追踪或展示的对象 URL，AI 能力层不依赖该 URL 读取对象。 |
| `content_hash` | 否 | 格式必须为 `sha256:<64位小写hex>`；传入时读取后必须校验。 |
| `content_type` | 是 | 当前固定为 `text/plain; charset=utf-8`。 |

OSS 规则：

```text
调用方不得传 OSS AccessKey、Secret 或临时凭证。
调用方不得传 OSS bucket、region、endpoint。
AI 能力层使用自身配置的 OSS bucket、region、endpoint 和凭证读取对象。
OSS 对象必须是 UTF-8 文本。
content_type 必须为 text/plain; charset=utf-8。
OSS 对象读取后最大 5 MB。
OSS 拉取失败时，Job 进入 failed，error.code=OSS_FETCH_FAILED。
content_hash 校验失败时，Job 进入 failed，error.code=INPUT_HASH_MISMATCH。
```

结果输出规则：

```text
调用方不在 POST /jobs 中传 output。
AI 能力层使用配置文件中的 OSS_OUTPUT_PREFIX 和 job_id 生成结果前缀。
AI 能力层使用自身配置的 OSS bucket、region、endpoint 和凭证写入结果文件。
本地化正文和英文翻译正文必须写入 OSS，不得在 JSON 中直接返回全文。
本地化正文是完成美国文化语境改写后的中文小说稿，不是英文译文。
AI 能力层写入完成后，在 artifact 中返回实际 oss_key、content_hash 和 content_size_bytes。
OSS 写入失败时，Job 进入 failed，error.code=OSS_WRITE_FAILED。
```

大文本 artifact key 规则：

```text
novel_localization.step1_localize 的正文结果 key 固定为 localized_text，语义是中文本地化稿。
novel_localization.step3_translate 的正文结果 key 固定为 translated_text，语义是英文终稿。
localized_text 和 translated_text 必须使用 storage=oss_object 返回，不得使用 content 返回全文。
review_summary、work_note 这类短文本可以使用 content 返回。
work_note 是统一的工作注释类 artifact，但必须按阶段和 `apply_mode` 区分方向：
- step1_localize 返回 work_note，apply_mode=replace，表示这是本次本地化模型输出的完整工作注释，和 localized_text 同属 step1 输出结果。
- step2_review 校验不通过时返回 work_note，apply_mode=replace，供调用方作为下一次 step1 的 work_note 直接使用，不与 step1 旧注释合并。
响应 artifact 不是 prompt block，不返回 role。AI 能力层只定义 artifact 结构和 apply_mode 语义，不定义调用方如何持久化或映射业务对象。
```

artifact 字段规则：

| 字段 | 必填 | 规则 |
| --- | --- | --- |
| `key` | 是 | 业务语义唯一键。正文固定为 `localized_text` 或 `translated_text`；工作注释统一为 `work_note`。 |
| `type` | 是 | artifact 类型。工作注释统一使用 `work_note`，不要再拆成 `notes`、`prompt_suggestion` 等结构。 |
| `label` | 是 | 给业务后端展示用的中文名称，不作为程序判断依据。 |
| `role` | 不支持 | artifact 不使用该字段。`role` 只属于请求中的 `prompt.blocks[]`。 |
| `apply_mode` | 否 | 仅 `work_note` 使用。`replace` 表示此 work_note 是本次 step 的完整输出，供调用方直接存储或作为下一次 step1 的 work_note 传入；step1 和 step2 均使用 `replace`。 |
| `content` | 否 | 短文本内容。`review_summary`、`work_note` 可以直接放在 `content`。 |
| `storage` | 否 | 大文本正文使用 `oss_object`，并配套返回 OSS 字段。 |
| `oss_key` | 否 | `storage=oss_object` 时必填，表示 AI 能力层写出的对象 key。 |
| `content_hash` | 否 | `storage=oss_object` 时返回，格式为 `sha256:<64位小写hex>`。 |
| `content_size_bytes` | 否 | `storage=oss_object` 时返回，表示写出对象大小。 |

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
        "key": "work_note",
        "type": "work_note",
        "label": "工作注释",
        "apply_mode": "replace",
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

说明：`localized_text` 的文件内容应为中文本地化正文。它已经完成角色、称谓、场景、文化元素等美国语境改写，但叙述语言仍是中文。英文文件只应出现在 `step3_translate` 的 `translated_text`。

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
        "key": "work_note",
        "type": "work_note",
        "label": "建议工作注释",
        "apply_mode": "replace",
        "content": "重新本地化时请统一角色称呼，并保持语气一致。"
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
step2_review 仅在 signals.passed=false 时返回 work_note 建议工作注释；signals.passed=true 时通常只返回 review_summary。
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
      },
      {
        "key": "work_note",
        "type": "work_note",
        "label": "工作注释",
        "apply_mode": "replace",
        "content": "模型输出的注释..."
      }
    ],
    "signals": {}
  },
  "error": null,
  "finished_at": "datetime"
}
```

说明：callback 中的 `localized_text` 与轮询接口语义一致，仍然是中文本地化正文；不要把它当成英文翻译结果消费。

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
| `MODEL_OUTPUT_INVALID` | 模型输出不符合当前 `job_type` 的解析约定。 |
| `JOB_TIMEOUT` | 任务排队或执行超时。 |

## 11. 限制与超时

| 项 | 限制 | 失败行为 |
|---|---:|---|
| `source.oss` | 最大 5 MB | Job 进入 `failed`，`error.code=INPUT_TOO_LARGE`。 |
| OSS 文本编码 | UTF-8 | Job 进入 `failed`，`error.code=INVALID_INPUT`。 |
| 单次模型 API 调用超时 | 默认 300 秒，由 `MODEL_CALL_TIMEOUT_SECONDS` 配置 | Job 进入 `failed`，`error.code=MODEL_CALL_FAILED`。 |
| Job 排队超时 | 10 分钟 | Job 进入 `failed`，`error.code=JOB_TIMEOUT`。 |
| Job 执行超时 | 30 分钟 | Job 进入 `failed`，`error.code=JOB_TIMEOUT`。 |

## 12. 轮询规则

```text
queued / running 状态每 2 秒轮询一次。
连续请求失败时做指数退避，例如 2 秒、4 秒、8 秒、16 秒。
进入 succeeded / failed / canceled 后停止轮询。
首版不提供 GET /jobs/{job_id}/stream。
```

## 13. 数据保留策略

### 13.1 双模式支持

本服务同时支持两种调用模式：

**Callback 模式（推荐）**
```text
业务后端 POST /jobs 时指定 callback.url
AI 能力层在任务完成时主动 POST callback
业务后端从 callback 获取完整结果
Job 记录在 24 小时后自动清理
```

**轮询模式（备选）**
```text
业务后端 POST /jobs 后轮询 GET /jobs/{job_id}
支持 24 小时内查询
24 小时后 Job 记录自动删除，无法查询
业务后端应在此时间内保存数据到自己的数据库
```

### 13.2 数据生命周期

```text
Job 创建时间：T0

T0 ~ T0+24h：标记为过期
  ├─ GET /jobs/{job_id}：仍可查询
  ├─ Callback：已发送（若已完成）
  └─ 数据仍然完整可用

T0+24h ~ 下月 1 日 02:00 UTC：等待清理
  ├─ 数据已过期但尚未被删除
  ├─ GET /jobs/{job_id}：仍可查询
  └─ 实际保留时间：24h ~ 最长 1 个月

下月 1 日 02:00 UTC：定时清理执行
  ├─ 删除所有 expires_at <= now() 的 Job 记录
  ├─ 关联的中间数据自动级联删除
  └─ GET /jobs/{job_id}：返回 404
```

### 13.3 最佳实践

业务后端应该：

```text
✅ 优先使用 Callback 获取结果
✅ 在 Callback 中立即保存完整结果到业务库
✅ 轮询仅作为短期排障手段（<24h）
❌ 不要依赖 AI 能力层做长期数据存储
❌ 不要假设超过 24h 后仍然能查询 Job
```

---

## 14. 后期可能提供的 AI 能力层接口

以下接口不进入首版对接范围。当前只给后端做能力预期参考，不定义具体入参和出参。后续确认需要实现时，再单独补充接口契约。

| 能力 | 方法 | 路径 | 用途 | 当前状态 |
|---|---|---|---|---|
| 查询任务快照 | `GET` | `/jobs/{job_id}/snapshot` | 用于排障和审计，查看某次 Job 冻结的模型、输入摘要、Prompt 摘要。 | 后期可选 |
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
