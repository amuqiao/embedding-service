# 小说本地化 AI 能力层接口文档

本文定义小说本地化 AI 能力层对业务后端提供的接口。该服务只承接 AI 执行能力，不承担用户系统、项目管理、前端页面状态、Prompt 配置持久化或业务流程编排。

## 1. 服务定位

本服务定位为：

```text
无状态 API 网关 / Headless Service / Platform Service / 基础设施层 AI 能力服务
```

这里的“无状态”指 API 层不维护用户登录态、不理解业务项目归属、不保存前端页面状态。服务内部保存 AI 任务状态，用于异步执行和结果查询。

## 2. 职责边界

### 2.1 本服务负责

```text
健康检查
返回支持的模型列表
返回默认 Prompt 模板
创建 AI 任务
读取任务输入文本，支持直接文本或 OSS 对象引用
将大文本结果写入 OSS
查询 AI 任务状态和结果
任务结束后回调业务后端
后置能力：返回任务快照 / 完整 Prompt / 取消任务
```

### 2.2 本服务不负责

```text
用户系统
用户权限
业务项目列表
业务项目详情
原文长期管理
用户修改后的 PromptConfig 保存
上游步骤结果保存
步骤状态机
前端页面状态
用户是否选择重跑
```

这些能力由业务后端 / BFF / 调用方系统负责。

## 3. 调用链路

```text
产品用户
  ↓
业务前端
  ↓
业务后端 / BFF
  ↓ Authorization: Bearer <service_api_key>
小说本地化 AI 能力层
```

浏览器前端不得直接持有 `service_api_key`；该密钥只能配置在业务后端 / BFF。

## 4. 鉴权方式

首版采用固定服务级 Bearer API Key。

```http
Authorization: Bearer <service_api_key>
```

说明：

```text
该 token 标识调用方服务，不标识产品用户。
本服务不提供登录、获取 token、刷新 token、用户信息接口。
产品用户身份如需审计，由业务后端放入 metadata。
```

## 5. 接口总览

### 5.1 首版必须接口

| 能力 | 方法 | 路径 |
|---|---|---|
| 健康检查 | `GET` | `/health` |
| 支持模型列表 | `GET` | `/models` |
| 默认 Prompt 模板 | `GET` | `/prompt-templates` |
| 创建 AI 任务 | `POST` | `/jobs` |
| 查询 AI 任务 | `GET` | `/jobs/{job_id}` |

## 6. 核心设计原则

### 6.1 POST /jobs 必须带完整执行输入

因为本服务不管理业务 Project，也不保存用户 PromptConfig，所以业务后端创建 AI 任务时，必须把本次执行需要的输入传完整。

包括：

```text
job_type
model_id
input
prompt.blocks
output
callback
metadata，可选
```

本服务不通过 `project_id` 查原文，不通过 `step_code` 查上游结果，不保存用户编辑后的 prompt 配置。本地化正文和英文翻译正文这类大文本结果写入 OSS，接口和 Callback 只返回 OSS 引用。

所有步骤都使用同一套 `input` 结构。无论输入是原文、上一步本地化结果、校验对象还是待翻译文本，都由业务后端在 `POST /jobs` 时传入。AI 能力层只读取本次任务的 `input`，不理解业务项目中的上下游关系。

`input` 支持两种方式：

```text
text：直接传入文本。
oss_object：传入 OSS 对象引用。
```

当 `input.type = oss_object` 时，AI 能力层使用自己配置的 OSS 凭证读取对象。调用方不得在请求体中传 OSS AccessKey、Secret 或临时凭证。

`output` 用于指定大文本结果的 OSS 输出位置。首版 `output.type` 固定为 `oss_prefix`。AI 能力层把本地化正文、英文翻译正文写入该 OSS 前缀，并在 `artifacts[]` 中返回实际 OSS key。

`callback` 用于指定 Job 终态通知地址。AI 能力层在 Job 进入 `succeeded` 或 `failed` 后主动 POST `callback.url`。Callback 是完成通知，不替代 `GET /jobs/{job_id}` 查询。

### 6.2 Prompt 模板与用户 Prompt 分离

本服务提供默认 Prompt 模板：

```text
GET /prompt-templates
```

业务后端从该接口读取默认模板，并在自己的系统中保存 PromptConfig。用户修改后的 PromptConfig 由业务后端保存。

### 6.3 Prompt 拼接由调用方完成

AI 能力层只接收本次任务最终要使用的 `prompt.blocks`。

```text
系统 Prompt、用户 Prompt、工作注释 Prompt 都由业务后端在 POST /jobs 时显式传入。
用户接受校验失败后的优化建议时，业务后端负责把建议文本合并到 `work_note` 或 `user` prompt 中，再重新 `POST /jobs`。
AI 能力层不提供额外的 runtime_prompt_blocks，也不根据 artifact_key 查询历史结果。
```

这样可以让 AI 能力层保持简单：调用方给最终输入和最终 Prompt，能力层只负责执行。

### 6.4 结果统一用 artifacts 表达

所有任务结果统一返回：

```json
{
  "artifacts": [],
  "signals": {}
}
```

`artifacts` 表示任务产物，例如正文、注释、校验报告、优化建议 Prompt。`signals` 表示结构化业务信号，例如校验是否通过。

## 7. 接口契约

### 7.1 GET /health

检查服务是否可用。

响应：

```json
{
  "status": "ok",
  "service": "novel-localization-ai",
  "version": "1.0.0"
}
```

### 7.2 GET /models

返回当前支持的模型列表。

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

说明：

```text
default_model_id 表示服务默认模型，仅用于调用方初始化选择。
POST /jobs 仍必须显式传 model_id；缺失 model_id 返回 422。
model_id 必须来自 models[].id，且对应模型 enabled=true。
```

### 7.3 GET /prompt-templates

返回 AI 能力层内置的默认 Prompt 模板。模板只表示默认能力配置，不表示用户当前保存的 prompt。

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

### 7.4 POST /jobs

创建 AI 异步任务。请求体必须包含本次执行需要的完整输入。

`job_type` 枚举值来自 `GET /prompt-templates` 返回的 `job_types[].job_type`。

#### 7.4.1 直接传文本

业务后端直接把待处理文本放入 `input.content`。

请求：

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
    "caller_internal_project_id": "业务后端的项目 ID（可选示例）",
    "caller_internal_job_id": "业务后端的任务 ID（可选示例）",
    "anything_else": "任何其他调用方需要的字段"
  }
}
```

**metadata 说明**：

```text
metadata 完全由调用方定义，无需遵循特定字段名。
上面示例中的字段名仅为说明，不是协议规定。
调用方可以使用任何字段名、任何结构来组织自己的业务信息。
AI 能力层原样保存 metadata，在 callback 时原样返回。
```

#### 7.4.2 通过 OSS 对象引用传输入

业务后端只传 OSS 对象引用，AI 能力层使用自身配置的 OSS 访问凭证读取对象。

请求中的 `input` 示例：

```json
{
  "type": "oss_object",
  "oss_bucket": "your-bucket-name",
  "oss_key": "novels/project-123/original.txt",
  "oss_region": "cn-hangzhou",
  "content_hash": "sha256:<64位小写hex>"
}
```

使用 OSS 输入时，完整 `POST /jobs` 仍必须同时传入 `output`、`callback`、`prompt.blocks` 和 `metadata`。

约束:

```text
调用方不得传 OSS AccessKey、Secret 或临时凭证。
OSS 对象必须是 UTF-8 文本。
OSS 对象内容可以是原文，也可以是上游步骤结果。
如果传入 `content_hash`，AI 能力层读取对象后必须校验 hash；校验失败时 Job 进入 `failed`，`error.code = INPUT_HASH_MISMATCH`。
如果 OSS 拉取失败，Job 进入 `failed`，`error.code = OSS_FETCH_FAILED`。
```

#### 7.4.3 input 字段说明

`input.type = text`：

| 字段 | 必填 | 说明 |
|---|---|---|
| `type` | 是 | 固定为 `text`。 |
| `content` | 是 | 本次任务要处理的 UTF-8 文本，可以是原文，也可以是上游步骤结果。 |
| `content_hash` | 否 | 输入文本 hash，格式必须为 `sha256:<64位小写hex>`；传入时 AI 能力层必须校验。 |

`input.type = oss_object`：

| 字段 | 必填 | 说明 |
|---|---|---|
| `type` | 是 | 固定为 `oss_object`。 |
| `oss_bucket` | 是 | OSS bucket 名称。 |
| `oss_key` | 是 | OSS object key。 |
| `oss_region` | 是 | OSS region，例如 `cn-hangzhou`。 |
| `content_hash` | 否 | OSS 对象内容 hash，格式必须为 `sha256:<64位小写hex>`；传入时 AI 能力层读取后必须校验。 |

#### 7.4.4 字段说明

| 字段 | 必填 | 说明 |
|---|---|---|
| `client_request_id` | 否 | 调用方生成的幂等键。相同调用方、相同 key 可复用已创建任务，避免重复提交。 |
| `job_type` | 是 | 任务类型，取值来自 `GET /prompt-templates`。 |
| `model_id` | 是 | 模型 ID，取值来自 `GET /models`。 |
| `input` | 是 | 本次任务输入，支持 `text` 或 `oss_object`。 |
| `output` | 是 | 大文本结果输出位置，首版固定为 `oss_prefix`。 |
| `callback` | 是 | Job 进入终态后通知业务后端的回调配置。 |
| `prompt.blocks` | 是 | 本次任务实际使用的 prompt blocks，由业务后端传入。 |
| `metadata` | 否 | 调用方业务引用，仅用于审计、排障和日志关联。 |

`metadata` 规则：

```text
metadata 可选，完全由调用方自定义。
```

**metadata 的实际用途**：

- **业务关联**：存放业务后端的项目 ID、步骤 ID、用户 ID，用于关联查询
- **审计日志**：存放请求来源、操作人、审批信息等追踪字段
- **排障信息**：存放请求链路 ID、内部追踪码、context ID 等排查线索
- **其他自定义字段**：调用方可以存放任何需要在 callback 时回传的信息

**约束**：

```text
AI 能力层不检查、不解释、不依赖 metadata 的字段名或结构。
metadata 不参与 AI 执行逻辑，不影响 Job 行为。
metadata 可以是任意嵌套的 JSON 值，包括对象和数组。
metadata 单次请求序列化后不得超过 8 KB，超过返回 422。
AI 能力层会在 callback 中原样返回 metadata，由调用方处理。
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

#### 7.4.5 client_request_id 幂等规则

`client_request_id` 用于防止业务后端重复提交同一次任务。

```text
相同调用方、相同 client_request_id 在 24 小时内重复提交，返回首次创建的任务。
重复提交仍返回 202 Accepted，response 中的 job_id 与首次创建时相同。
幂等命中只比对调用方身份和 client_request_id，不重新校验 job_type、input、prompt 是否一致。
超过 24 小时幂等窗口后，相同 client_request_id 视为新任务。
如果调用方无法保证 client_request_id 唯一，可以不传；不传时每次 POST /jobs 都创建新任务。
```

说明：

```text
调用方必须保证同一个 client_request_id 在 24 小时内只代表同一次提交。
client_request_id 可以使用业务任务 ID、请求流水号或 UUID。
不得使用 project_id、step_code 这类会被多次复用的业务字段作为 client_request_id。
```

#### 7.4.6 prompt.blocks[] 字段说明

| 字段 | 必填 | 说明 |
|---|---|---|
| `key` | 是 | 对应 `GET /prompt-templates` 中当前 `job_type` 的 `prompt_blocks[].key`。缺失、未知或重复 key 返回 `422`。 |
| `role` | 是 | 必须与 `GET /prompt-templates` 中对应 `key` 的 `role` 一致。当前只允许 `system` 或 `user`；不一致返回 `422`。 |
| `content` | 是 | 本次实际使用的 prompt 内容，不能为 `null`，允许空字符串。 |

规则：

```text
prompt.blocks 必须传齐当前 job_type 在 GET /prompt-templates 中返回的全部 prompt_blocks。
当前首版固定为 system、user、work_note 三个 block。
work_note 可以是空字符串。
AI 能力层不会自动用默认模板补齐缺失 block。
如果业务后端希望使用默认 prompt，应先从 GET /prompt-templates 读取默认值，再在 POST /jobs 中显式传入。
缺失 key、未知 key、重复 key 都返回 422。
```

#### 7.4.7 执行策略说明

首版 `POST /jobs` 不提供 `options` 或 `execution_mode` 参数。

```text
调用方不需要选择执行模式。
AI 能力层默认使用自动执行策略，包括内部是否分 chunk、是否做中间分析、如何合并结果。
这些都是服务内部实现细节，不作为首版接口契约暴露。
首版请求体中出现 `options` 或 `execution_mode` 时返回 `422`。
```

响应使用 `202 Accepted`：

```json
{
  "job_id": "uuid",
  "status": "queued",
  "status_url": "/api/v1/novel-localization-ai/jobs/uuid",
  "created_at": "datetime"
}
```

### 7.5 GET /jobs/{job_id}

查询任务状态、进度和结果。

运行中响应：

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

成功响应：

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

校验失败但任务成功的响应示例：

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

失败响应：

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

说明：

```text
error 是结构化失败原因，供业务后端判断和日志记录。
status=failed 时应返回 error；status=queued/running/succeeded 时 error 为 null。
```

轮询规则：

```text
queued / running 状态每 2 秒轮询一次。
连续请求失败时做指数退避，例如 2 秒、4 秒、8 秒、16 秒。
进入终态 succeeded / failed / canceled 后停止轮询。
`GET /jobs/{job_id}/stream` 不属于首版接口；首版调用方必须通过轮询获取进度。
```

#### 7.5.1 结果字段说明

| 字段 | 说明 |
|---|---|
| `status` | Job 执行状态，只表达任务是否执行完成，不表达校验是否通过。 |
| `progress_percent` | 任务进度，整数范围 `0-100`。`status=succeeded` 时必须为 `100`；`failed/canceled` 时返回最后一次已知进度。 |
| `result.artifacts[]` | AI 任务产物列表，例如正文、注释、校验报告、优化建议 Prompt。 |
| `result.signals` | 结构化业务信号，例如校验任务的 `passed=false`。前端或业务后端可用它决定下一步动作。 |
| `error` | 任务失败时的结构化错误；`status=failed` 时返回。 |

`status=succeeded` 只表示任务执行成功。如果校验任务正常完成但校验不通过，应返回 `status=succeeded` 和 `signals.passed=false`。

## 8. 后置可选接口

以下接口不进入首版必做范围。只有在后端联调确认需要时再补充契约，避免首版过度设计。

| 能力 | 方法 | 路径 | 说明 |
|---|---|---|---|
| 查询任务快照 | `GET` | `/jobs/{job_id}/snapshot` | 用于排障和审计，默认不返回大字段全文。 |
| 查询完整 Prompt | `GET` | `/jobs/{job_id}/full-prompt` | 用于排查 Prompt 拼接问题。 |
| 取消任务 | `POST` | `/jobs/{job_id}/cancel` | 任务耗时较长后再考虑。 |
| 任务 SSE | `GET` | `/jobs/{job_id}/stream` | 轮询不能满足实时性时再考虑。 |

首版只要求通过 `GET /jobs/{job_id}` 查询任务状态和结果。

## 9. Callback 通知

AI 能力层在 Job 进入终态后，向 `POST /jobs` 中的 `callback.url` 发送通知。Callback body 与 `GET /jobs/{job_id}` 的终态核心结构保持一致。

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
    "external_project_id": "业务项目 ID",
    "external_step_id": "业务步骤 ID",
    "user_id": "用户 ID"
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
    "external_project_id": "业务项目 ID",
    "external_step_id": "业务步骤 ID",
    "user_id": "用户 ID"
  },
  "finished_at": "datetime"
}
```

规则：

```text
Callback 成功条件为业务后端返回 2xx。
业务后端返回非 2xx 或请求超时，AI 能力层按重试规则重试。
Callback 失败不改变 Job 状态；业务后端可通过 GET /jobs/{job_id} 兜底查询。
```

## 10. 状态与错误

### 10.1 Job 状态

```text
queued | running | succeeded | failed | canceled
```

### 10.2 HTTP 状态码

| 场景 | 状态码 |
|---|---:|
| 创建任务成功 | `202` |
| 查询成功 | `200` |
| 参数错误 | `422` |
| 任务不存在 | `404` |
| 服务 token 缺失或无效 | `401` |
| 调用方无服务权限 | `403` |
| 内部处理失败 | `500` |

### 10.3 错误响应体

非 2xx 响应统一使用以下结构：

```json
{
  "error": {
    "code": "INVALID_JOB_TYPE",
    "message": "不支持的 job_type: novel_localization.step99_xxx",
    "details": {}
  }
}
```

说明：

```text
code：稳定错误码，供业务后端判断。
message：可读错误信息，供日志和排障使用。
details：可选结构化细节，例如字段名、限制值、OSS key。
```

### 10.4 业务错误 code

| code | 场景 |
|---|---|
| `INVALID_JOB_TYPE` | 不支持的 `job_type` |
| `MODEL_NOT_AVAILABLE` | 模型不存在或不可用 |
| `INVALID_INPUT` | 输入为空、过长或格式不支持 |
| `INPUT_TOO_LARGE` | 输入超过服务限制 |
| `INPUT_HASH_MISMATCH` | `content_hash` 校验失败 |
| `OSS_OBJECT_NOT_FOUND` | OSS 对象不存在 |
| `OSS_FETCH_FAILED` | OSS 对象读取失败 |
| `OSS_WRITE_FAILED` | OSS 对象写入失败 |
| `JOB_NOT_FOUND` | `job_id` 不存在 |
| `JOB_ALREADY_CANCELED` | 任务已取消 |
| `MODEL_CALL_FAILED` | 模型调用失败 |
| `JOB_TIMEOUT` | 任务执行超时 |

说明：

```text
JOB_TIMEOUT 不是独立 Job 状态。
任务超时后，最终 status = failed，并在 error.code 中返回 JOB_TIMEOUT。
Job 状态枚举仍保持 queued | running | succeeded | failed | canceled。
```

### 10.5 输入限制与超时

首版输入限制与超时规则如下。超过限制时，任务不得继续执行。

| 项 | 限制 | 说明 |
|---|---:|---|
| `input.type = text` | 最大 1 MB | 超出返回 `INPUT_TOO_LARGE`；长文本使用 `oss_object`。 |
| `input.type = oss_object` | 最大 5 MB | 读取后超过限制返回 `INPUT_TOO_LARGE`。 |
| OSS 文本编码 | UTF-8 | 非 UTF-8 返回 `INVALID_INPUT`。 |
| Job 排队超时 | 10 分钟 | 超时后 `status=failed`，`error.code=JOB_TIMEOUT`。 |
| Job 执行超时 | 30 分钟 | 超时后 `status=failed`，`error.code=JOB_TIMEOUT`。 |

超过首版输入限制的内容，调用方必须使用 `oss_object`。AI 能力层内部是否分 chunk、分析和合并，不影响接口契约。

## 11. 业务后端如何使用

### 11.1 初始化默认 Prompt

```text
业务后端 GET /prompt-templates
业务后端保存或展示默认 system / user / work_note prompt
用户修改后的 prompt 由业务后端保存
```

### 11.2 执行本地化

```text
业务后端读取自己的项目原文和 PromptConfig
业务后端直接传 `input.type=text`，或传 `input.type=oss_object`
业务后端 POST /jobs，job_type = novel_localization.step1_localize
AI 能力层返回 job_id
业务后端 GET /jobs/{job_id} 轮询
AI 能力层返回 localized_text 的 OSS 引用 / notes
业务后端保存 OSS 引用和 notes 到自己的业务库
```

### 11.3 执行校验

```text
业务后端读取 step1 的本地化结果，或传入 step1 结果所在的 OSS 对象引用
业务后端 POST /jobs，job_type = novel_localization.step2_review
AI 能力层返回 review_summary / optimization_prompt / passed signal
业务后端和前端决定是否用 optimization_prompt 重跑 step1
```

### 11.4 使用校验建议重跑

**快速参考**（三行代码）：

```python
# 1. 从 step2 结果中提取建议
optimization = next(a for a in step2_result['result']['artifacts'] if a['key'] == 'optimization_prompt')

# 2. 重跑 step1，注入建议到 work_note
step1_retry = post_job('novel_localization.step1_localize', {
    'prompt': {'blocks': [
        ...,
        {'key': 'work_note', 'role': 'user', 'content': optimization['content']}  # ← 直接用建议内容
    ]}
})
```

**详细说明**：

```text
业务后端从 step2 的失败结果中提取 optimization_prompt artifact
业务后端 POST /jobs，job_type = novel_localization.step1_localize
业务后端把 optimization_prompt.content 直接赋给 prompt.blocks 中 work_note 的 content
input = 原文或业务后端选择的输入，支持 text 或 oss_object
AI 能力层返回新的本地化结果
```

**关键约定**：
- ✅ `job_type` 保持不变（仍为 `novel_localization.step1_localize`）
- ✅ 仅修改 `prompt.blocks` 中 `work_note` 的 `content`
- ✅ 直接使用 `optimization_prompt.content`，无需再处理
- ✅ 支持多轮迭代，直到 `signals.passed=true` 或达到重试次数上限

## 12. 项目记忆（Project Memory）

### 12.1 什么是项目记忆

项目记忆是 AI 能力层为长文本一致性生成的结果 artifact。它包括：

```text
characters - 人物列表和描述
places - 地点列表和描述
glossary - 术语表
style_guide - 风格指南
cultural_rules - 文化转换规则
continuity_notes - 连续性笔记
chunk_summaries - 分块总结
```

### 12.2 项目记忆的性质

```text
项目记忆是本服务内部为了 AI 质量而生成的执行 artifact，不是业务项目状态。
本服务不按 project_id 长期保存或管理项目记忆。
调用方如果希望后续 Job 使用同一份项目记忆，需要显式传入。
```

### 12.3 如何使用项目记忆

#### 步骤 1：从 step1 结果中获取项目记忆

```text
业务后端执行 novel_localization.step1_localize 后获得 result.artifacts[]。
如果某个 artifact 的 key 为 project_memory，则表示该 Job 生成了项目记忆。
业务后端保存该项目记忆（可存于自己的数据库或临时存储）。
```

#### 步骤 2：后续 Job 中重复使用项目记忆

```text
业务后端在 step2、step3 或后续 Job 的 prompt.blocks 中的 work_note 传入：

<project_memory>
{
  "characters": [...],
  "places": [...],
  ...
}
</project_memory>

AI 能力层会从 work_note 的 XML 标签中提取项目记忆，并在执行时使用。
```

#### 步骤 3：示例代码

```python
# step1 执行
step1_result = post_job('novel_localization.step1_localize', {...})

# 从结果中提取项目记忆
project_memory = next(
    (a['content'] for a in step1_result['result']['artifacts'] 
     if a['key'] == 'project_memory'),
    None
)

# step2 中重用项目记忆
work_note_content = f"""<project_memory>
{json.dumps(project_memory, ensure_ascii=False, indent=2)}
</project_memory>"""

step2_result = post_job('novel_localization.step2_review', {
    'prompt': {'blocks': [
        ...,
        {'key': 'work_note', 'role': 'user', 'content': work_note_content}
    ]}
})
```

### 12.4 关键约定

```text
✅ 项目记忆由调用方管理和显式传回，不由本服务长期持久化。
✅ 每个 Job 请求仍然是完整自包含的。
✅ 项目记忆作为 artifact 可能出现在 step1 结果中，但不是强制的。
✅ 本服务不提供"查询项目记忆"或"管理项目"的接口。
```

## 13. 不应该由本服务承担的接口

以下接口属于业务后端，AI 能力层首版不提供：

```text
POST /projects
GET /projects
GET /projects/{project_id}
DELETE /projects/{project_id}
GET /projects/{project_id}/steps/{step_code}/prompt
PUT /projects/{project_id}/steps/{step_code}/prompt
GET /projects/{project_id}/export
```

## 14. 最终建议

首版实现：

```text
GET  /health
GET  /models
GET  /prompt-templates
POST /jobs
GET  /jobs/{job_id}
```

一句话边界：

```text
业务后端给我输入、prompt、模型；我创建 AI job，执行后返回结构化结果。
```
