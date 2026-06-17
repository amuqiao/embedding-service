# CPP 请求 AI 打标任务接口

本文面向 CPP 调用方，完整定义 CPP 如何创建短剧打标 Job、轮询查询 Job 状态，以及接收终态 callback。

## 接口边界

CPP 向 AI 提供作品素材资源和 callback 地址。AI 接收任务后，从 RS 获取默认标签体系和互斥规则，异步执行打标，终态 callback CPP 后再写入 RS。

CPP 负责：

- 提供 `t_book_id`、剧名、简介、字幕语言、剧集结构等作品上下文。
- 提供字幕、视频等素材资源，当前至少需要 `subtitle_srt`。
- 创建 AI Job，保存 `job_id`。
- 通过 `job_id` 轮询状态。
- 接收 AI callback，并按 `job_id` 幂等处理。

CPP 不负责：

- 提供标签结构体、互斥标签结构体或标签唯一 id。
- 传递 `tag_schema_version`。
- 调用模型生成标签。
- 将 AI 打标结果写入 RS。

## 基础接口

AI 对 CPP 使用通用 AI Job 接口：

```text
POST /api/v1/ai-jobs/jobs
GET  /api/v1/ai-jobs/jobs/{job_id}
```

除健康检查外，请求应携带服务鉴权：

```http
Authorization: Bearer <SERVICE_API_KEY>
X-AI-Service-Caller-ID: cpp
```

## Job 壳边界

本接口使用统一 AI Job 壳。外层只承载 Job 生命周期字段，短剧打标专属数据必须放在固定扩展位置：

| 数据类型 | 位置 | 说明 |
| --- | --- | --- |
| 打标任务入参 | `job_params` | `t_book_id`、作品上下文和素材资源都放在这里。 |
| 打标成功结果 | RS `ai_auto` 结果 | AI 内部生成并写入 RS，CPP 不通过 JobView 获取标签 payload。 |
| 打标失败信息 | `error` | 失败终态统一使用 `code`、`message`、`details`。 |
| 终态通知 | `CallbackEnvelope` | Callback 请求体是事件 envelope，其中 `job` 字段复用轮询接口返回的成功或失败终态 `JobView`。 |

不得把 `t_book_id`、素材、标签结果、剧情分析或打标明细提升到 Job 顶层。

### Schema 合同

创建请求先校验通用 `CreateJobRequest`，再根据 `job_type=short_drama.tagging.initial` 或 `short_drama.tagging.incremental` 校验 `job_params` 是否满足 `ShortDramaTaggingParams`。`job_params` 校验通过后才会创建 Job。

查询响应和 callback 中的 `job` 字段都使用同一套 `JobView` 状态组合校验：

- `queued` / `running`：`result=null`，`error=null`。
- `succeeded`：`result=null`，`error=null`。
- `failed`：`result=null`，`error` 必须存在。

短剧打标的 canonical result 是 AI 内部产物，用于成功 callback 后写入 RS；它不是 CPP 接口的公开 result。CPP 面向的成功 result schema 因此固定为 `null`。

创建请求顶层只使用：

```text
client_request_id
job_type
job_params
callback
metadata
options
```

查询响应统一使用 `JobView`。`JobView.result` 固定为 `null`，成功终态只表示 AI 已完成打标并生成可写入 RS 的 canonical result。如果 CPP 在创建请求中传入 `callback.url`，AI 只在 Job 进入 `succeeded` 或 `failed` 后发送 callback，且 callback 请求体中的 `job` 字段与同一 Job 的终态 `JobView` 同形。

## 创建打标 Job

```http
POST /api/v1/ai-jobs/jobs
Content-Type: application/json
```

成功返回 `202 Accepted`。该响应只表示 AI 已接单，不表示打标完成。

### 请求体

```json
{
  "client_request_id": "cpp:204200150000004872:initial:20260615",
  "job_type": "short_drama.tagging.initial",
  "job_params": {
    "t_book_id": "204200150000004872",
    "work_context": {
      "title": "Acting for Real-He Fell First",
      "synopsis": "To change her fate and pay off her debts...",
      "subtitle_language": "en",
      "series_structure": "continuous_series",
      "content_type": "漫剧",
      "episode_count": 80
    },
    "assets": [
      {
        "asset_type": "subtitle_srt",
        "episode_no": 1,
        "format": "srt",
        "uri": "oss://bucket/path/204200150000004872/episode_001.srt",
        "content_hash": "sha256:optional"
      },
      {
        "asset_type": "video",
        "episode_no": 1,
        "format": "mp4",
        "uri": "oss://bucket/path/204200150000004872/episode_001.mp4",
        "content_hash": "sha256:optional"
      }
    ]
  },
  "callback": {
    "url": "https://cpp.example.com/ai-jobs/callback"
  },
  "metadata": {
    "source_service": "cpp",
    "business_scene": "short_drama_tagging"
  },
  "options": {
    "priority": "normal",
    "timeout_seconds": 1800
  }
}
```

### 字段规则

| 字段 | 必需性 | 说明 |
| --- | --- | --- |
| `client_request_id` | 可选 | CPP 幂等键。重试创建同一业务任务时必须复用。 |
| `job_type` | 必需 | `short_drama.tagging.initial` |
| `job_params.t_book_id` | 必需 | 作品主键。 |
| `job_params.work_context.title` | 必需 | 剧名。 |
| `job_params.work_context.synopsis` | 建议 | 剧情简介。没有简介时可为空字符串，但不能替代字幕。 |
| `job_params.work_context.subtitle_language` | 必需 | 三方业务语种合约代码，见 `业务语种规范.md` / `language-codes.md`；例如 `zh`、`en`、`es`、`pt`、`in`，不得把 `in` 改写为 `id`。 |
| `job_params.work_context.series_structure` | 必需 | `continuous_series` 或 `unit_series`。 |
| `job_params.assets` | 必需 | 素材资源列表，至少包含一个 `subtitle_srt`。 |
| `callback` | 可选 | CPP 终态通知配置。不传时 CPP 只轮询。 |
| `callback.url` | 配置 callback 时必需 | AI 在终态时请求的 CPP callback 地址。 |
| `metadata` | 可选 | CPP 透传元数据，AI 不解释业务语义。 |
| `options.priority` | 可选 | 执行优先级，允许 `low`、`normal`、`high`。 |
| `options.timeout_seconds` | 可选 | 任务超时时间，必须大于 `0`。 |

标签数据由 AI 在执行时从 RS 获取，CPP 创建 Job 时只提供作品素材和作品上下文。

### 响应体

创建成功后，AI 返回已创建的 job 基本信息。首次创建的新 job 初始 `status` 必须为 `queued`。

如果 `client_request_id` 命中已有 job，AI 不创建新 job，仍返回该 job 的基本信息；响应中的 `status` 必须是已有 job 的当前真实状态，可能是 `queued`、`running`、`succeeded` 或 `failed`。

```json
{
  "job_id": "7b5c2c62-9a3a-41b7-bd41-f24a5d34a099",
  "client_request_id": "cpp:204200150000004872:initial:20260615",
  "job_type": "short_drama.tagging.initial",
  "status": "queued",
  "status_url": "/api/v1/ai-jobs/jobs/7b5c2c62-9a3a-41b7-bd41-f24a5d34a099",
  "created_at": "2026-06-15T10:00:00Z"
}
```

创建请求和幂等命中请求均返回 `202 Accepted`；CPP 必须以响应体中的 `job_id` 和 `status` 为准。

## 素材资源

字幕可通过文件地址交付：

```json
{
  "asset_type": "subtitle_srt",
  "episode_no": 1,
  "format": "srt",
  "uri": "oss://bucket/path/episode_001.srt",
  "content_hash": "sha256:optional"
}
```

也可通过文本交付：

```json
{
  "asset_type": "subtitle_srt",
  "episode_no": 1,
  "format": "srt",
  "text": "1\n00:00:01,000 --> 00:00:03,000\nHello."
}
```

规则：

- `subtitle_srt` 的 `format` 固定为 `srt`。
- `uri` 和 `text` 至少提供一个。
- 如同时提供 `text` 和 `uri`，`text` 是本次执行输入，`uri` 只用于追溯。
- URI 型素材应尽量提供 `content_hash`，保证任务可复现。
- 视频资源是扩展素材，当前 POC 主要依赖 SRT。

## 查询 Job

```http
GET /api/v1/ai-jobs/jobs/{job_id}
```

### 状态语义

`status` 表示 AI Job 当前状态。CPP 轮询时必须按 `status` 判断是否继续等待、是否确认完成或读取 `error`。

| status | 是否终态 | CPP 处理方式 | 语义 |
| --- | --- | --- | --- |
| `queued` | 否 | 继续轮询。 | AI 已接单并创建 job，但尚未开始执行。 |
| `running` | 否 | 继续轮询，可展示 `progress`。 | AI 已开始执行，可能处于素材解析、拉取 RS 标签体系、模型打标或结果校验阶段。 |
| `succeeded` | 是 | 停止轮询，确认任务完成。 | AI 已生成 canonical result，完成结果校验，并已进入成功终态；RS 写入由成功 callback 后的后置动作发送。 |
| `failed` | 是 | 停止轮询，读取 `error`。 | 任务失败。失败原因由 `error.code` 和 `error.message` 表示。 |

状态字段规则：

- 只有 `succeeded` 和 `failed` 是终态。
- 只有终态会触发 CPP callback；`queued` 和 `running` 只通过轮询观察，不触发 callback。
- 所有状态下 `result` 都必须为 `null`。
- `queued`、`running` 和 `succeeded` 时 `error` 必须为 `null`。
- `failed` 时 `error` 必须存在，`result` 必须为 `null`。
- 内部执行阶段应通过 `progress.stage` 表示，不扩展外部 `status` 枚举。

成功终态示例：

```json
{
  "job_id": "7b5c2c62-9a3a-41b7-bd41-f24a5d34a099",
  "client_request_id": "cpp:204200150000004872:initial:20260615",
  "job_type": "short_drama.tagging.initial",
  "status": "succeeded",
  "progress": {
    "percent": 100,
    "message": "finished",
    "stage": "finished"
  },
  "result": null,
  "error": null,
  "metadata": {
    "source_service": "cpp",
    "business_scene": "short_drama_tagging"
  },
  "created_at": "2026-06-15T10:00:00Z",
  "started_at": "2026-06-15T10:00:05Z",
  "finished_at": "2026-06-15T10:03:00Z"
}
```

失败终态示例：

```json
{
  "job_id": "7b5c2c62-9a3a-41b7-bd41-f24a5d34a099",
  "client_request_id": "cpp:204200150000004872:initial:20260615",
  "job_type": "short_drama.tagging.initial",
  "status": "failed",
  "progress": {
    "percent": 100,
    "message": "model output invalid",
    "stage": "failed"
  },
  "result": null,
  "error": {
    "code": "MODEL_OUTPUT_INVALID",
    "message": "AI generated tagging result is not valid for the RS tag schema.",
    "details": {
      "t_book_id": "204200150000004872",
      "reason": "selected tag label name is not in schema"
    }
  },
  "metadata": {
    "source_service": "cpp",
    "business_scene": "short_drama_tagging"
  },
  "created_at": "2026-06-15T10:00:00Z",
  "started_at": "2026-06-15T10:00:05Z",
  "finished_at": "2026-06-15T10:03:00Z"
}
```

### 查询响应字段

| 字段 | 类型 | 必需性 | 说明 |
| --- | --- | --- | --- |
| `job_id` | `string` | 必需 | AI Job 唯一 id。 |
| `client_request_id` | `string \| null` | 可选 | 创建请求中传入的幂等键。 |
| `job_type` | `string` | 必需 | `short_drama.tagging.initial` 或 `short_drama.tagging.incremental`。 |
| `status` | `string` | 必需 | `queued`、`running`、`succeeded`、`failed`。 |
| `progress` | `object` | 必需 | 任务进度信息。 |
| `result` | `null` | 必需 | 固定为 `null`。CPP 不通过 JobView 获取打标结果。 |
| `error` | `object \| null` | 失败终态必需 | 失败终态的错误对象。非失败终态为 `null`。 |
| `metadata` | `object` | 可选 | 创建请求透传元数据。 |
| `created_at` | `string` | 必需 | Job 创建时间，RFC 3339 / ISO 8601。 |
| `started_at` | `string \| null` | 可选 | Job 开始执行时间。 |
| `finished_at` | `string \| null` | 终态时必需 | Job 完成时间。 |

## Callback

Callback 是终态通知，不替代轮询。CPP 创建 Job 时传入 `callback.url` 后，AI 只在 `succeeded` 或 `failed` 终态向该地址发送 callback。

如果创建请求没有传入 `callback.url`，AI 不发送 callback，CPP 只通过查询接口获取状态。

```http
POST <callback.url>
Content-Type: application/json
X-AI-Service-Job-Id: <job_id>
X-AI-Service-Event: job.succeeded | job.failed
X-AI-Service-Timestamp: 2026-06-15T10:03:01Z
X-AI-Service-Signature: sha256=<hmac>
```

当 AI 配置 callback 签名密钥时，会发送 `X-AI-Service-Signature`。签名内容为：

```text
timestamp + "." + raw_body
```

### Callback 请求体

Callback 请求体是事件 envelope，其中 `job` 字段复用同一个 job 在 `GET /api/v1/ai-jobs/jobs/{job_id}` 查询接口中的终态 `JobView`。成功 callback 的 `job` 字段必须与同一 job 的成功终态轮询响应同形，且 `job.result` 为 `null`；失败 callback 的 `job` 字段必须与同一 job 的失败终态轮询响应同形并来自同一份错误。

失败 callback 示例：

```json
{
  "event": "job.failed",
  "event_id": "8e6a3d4a-1d43-4f4a-a5f5-1efcb75e5a6d",
  "attempt": 1,
  "sent_at": "2026-06-15T10:03:01Z",
  "job": {
    "job_id": "7b5c2c62-9a3a-41b7-bd41-f24a5d34a099",
    "client_request_id": "cpp:204200150000004872:initial:20260615",
    "job_type": "short_drama.tagging.initial",
    "status": "failed",
    "progress": {
      "percent": 100,
      "message": "model output invalid",
      "stage": "failed"
    },
    "result": null,
    "error": {
      "code": "MODEL_OUTPUT_INVALID",
      "message": "AI generated tagging result is not valid for the RS tag schema.",
      "details": {
        "t_book_id": "204200150000004872",
        "reason": "selected tag label name is not in schema"
      }
    },
    "metadata": {
      "source_service": "cpp",
      "business_scene": "short_drama_tagging"
    },
    "created_at": "2026-06-15T10:00:00Z",
    "started_at": "2026-06-15T10:00:05Z",
    "finished_at": "2026-06-15T10:03:00Z"
  }
}
```

### Callback 字段

Callback 请求体字段：

| 字段 | 类型 | 必需性 | 说明 |
| --- | --- | --- | --- |
| `event` | `string` | 必需 | 终态事件，取值为 `job.succeeded` 或 `job.failed`。 |
| `event_id` | `string` | 必需 | 本次 Callback 投递事件 id。 |
| `attempt` | `number` | 必需 | 本次投递尝试序号。 |
| `sent_at` | `string` | 必需 | 本次投递发送时间。 |
| `job` | `object` | 必需 | 终态 `JobView`。成功时 `job.result` 为 `null`；失败原因从 `job.error` 读取。 |

CPP 必须按 `job.job_id + event` 做业务幂等消费，并在处理成功后返回任意 `2xx`。`event_id` 用于标识一次 Callback 投递事件，不能替代 `job.job_id` 作为业务幂等键。AI 收到非 `2xx` 响应时可按内部策略重试同一个终态 Job 的 Callback。

## 一致性规则

- `job_params` 是短剧打标任务唯一入参扩展位置。
- `JobView.result` 固定为 `null`，不承载短剧打标成功出参。
- 成功 callback 的 `job` 字段必须与轮询成功终态响应体同形。
- 失败 callback 的 `job` 字段必须与轮询失败终态响应体同形，并来自同一份错误。
- AI 写 RS 的 payload 必须来自当前 job 的 canonical result，并由专用兼容 adapter 拼接，不允许另行生成一份不同的打标结果。
- CPP callback 表示同一 job 的终态通知，不携带 canonical result。
- Mock 接口中的创建请求、查询响应和 callback 样例只作为发送前数据和回复数据样例，也必须通过同一套 `CreateJobRequest + ShortDramaTaggingParams`、`JobView` 和 `CallbackEnvelope` 校验；mock 接口不维护独立的打标参数或 JobView 校验规则。

## 终态成功定义

`succeeded` 表示：

```text
AI 已生成 canonical result
AI 已完成结果校验
已调度成功 callback 后的 RS 写入动作
```

如果 AI 生成了可写入 RS 的部分结果，但存在缺失分类或低于数量约束等 `partial_success` 问题，Job 仍可进入 `succeeded`，AI 仍会在成功 callback 后写入 RS。该类问题只记录在 AI 内部 canonical result 和写 RS payload 相关明细中，对 CPP 的 `JobView.result` 仍保持 `null`。

如果模型推理、素材校验或标签结构校验失败，job 进入 `failed`，并向 CPP callback 失败终态。RS 写入失败发生在成功 callback 后，由 AI 侧任务日志和告警暴露，CPP 不通过 JobView 消费 RS 落库细节。

## 幂等

幂等范围：

```text
caller_id + client_request_id + 最近 24 小时
```

同一幂等键请求指纹一致时，AI 返回已有 job；请求指纹不一致时返回 `409 CLIENT_REQUEST_ID_CONFLICT`。请求指纹至少覆盖：

- `job_type`
- 归一化后的 `job_params`
- `callback`
- `metadata`
- `options`

## 错误码

| 错误码 | 说明 |
| --- | --- |
| `INVALID_REQUEST` | 请求结构不合法。 |
| `INVALID_JOB_TYPE` | `job_type` 不合法或未注册。 |
| `CLIENT_REQUEST_ID_CONFLICT` | 同一幂等键请求内容不一致。 |
| `JOB_NOT_FOUND` | 查询的 job 不存在或无权访问。 |
| `MISSING_REQUIRED_MATERIAL` | 缺少必需素材。 |
| `INVALID_SUBTITLE_SRT` | 字幕不是合法 SRT。 |
| `TAG_SCHEMA_UNAVAILABLE` | RS 未返回可用默认标签结构体。 |
| `TAG_SCHEMA_INVALID` | 标签结构体或互斥结构体不合法。 |
| `RS_RESULT_WRITE_FAILED` | AI 结果已生成，但后置写入 RS 失败。 |
| `JOB_TIMEOUT` | 任务执行超时。 |
| `INTERNAL_ERROR` | AI 服务内部错误。 |
