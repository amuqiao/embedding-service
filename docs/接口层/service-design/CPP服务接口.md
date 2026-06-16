# CPP 请求 AI 打标任务接口

本文面向 CPP 调用方，定义 CPP 如何调用 AI 服务创建短剧打标任务、查询状态和接收终态 callback。

## 接口边界

CPP 向 AI 提供作品素材资源。AI 接收任务后，从 RS 获取默认 `TagSchemaSnapshot` 和 `MutualExclusionRule[]`，异步执行打标，并在终态写入 RS 后 callback CPP。

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
    "url": "https://cpp.example.com/ai-jobs/callback",
    "events": ["job.succeeded", "job.failed"]
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
| `job_type` | 必需 | `short_drama.tagging.initial` 或 `short_drama.tagging.incremental`。 |
| `job_params.t_book_id` | 必需 | 作品主键。 |
| `job_params.work_context.title` | 必需 | 剧名。 |
| `job_params.work_context.synopsis` | 建议 | 剧情简介。没有简介时可为空字符串，但不能替代字幕。 |
| `job_params.work_context.subtitle_language` | 必需 | BCP 47 语言代码，见 `language-codes.md`。 |
| `job_params.work_context.series_structure` | 必需 | `continuous_series` 或 `unit_series`。 |
| `job_params.assets` | 必需 | 素材资源列表，至少包含一个 `subtitle_srt`。 |
| `callback` | 可选 | CPP 终态通知配置。不传时 CPP 只轮询。 |

请求体不得包含 `tag_schema_version`。标签数据由 AI 从 RS 获取默认结构体。

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

`status` 表示 AI Job 当前状态。CPP 轮询时必须按 `status` 判断是否继续等待、是否消费 `result` 或 `error`。

| status | 是否终态 | CPP 处理方式 | 语义 |
| --- | --- | --- | --- |
| `queued` | 否 | 继续轮询。 | AI 已接单并创建 job，但尚未开始执行。 |
| `running` | 否 | 继续轮询，可展示 `progress`。 | AI 已开始执行，可能处于素材解析、拉取 RS 标签体系、模型打标、结果校验或写 RS 阶段。即使 AI 已生成结果但 RS 尚未接受写入，对外仍保持 `running`。 |
| `succeeded` | 是 | 停止轮询，读取 `result`。 | AI 已生成 canonical result，完成结果校验，且 RS 已接受同一份打标结果写入。 |
| `failed` | 是 | 停止轮询，读取 `error`。 | 任务失败。失败原因由 `error.code` 和 `error.message` 表示。 |

状态字段规则：

- 只有 `succeeded` 和 `failed` 是终态。
- 只有终态会触发 CPP callback；`queued` 和 `running` 只通过轮询观察，不触发 callback。
- `queued` 和 `running` 时 `result` 和 `error` 必须为 `null`。
- `succeeded` 时 `result` 必须存在，`error` 必须为 `null`。
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
  "result": {
    "artifacts": [
      {
        "key": "final_tags",
        "type": "json",
        "label": "最终标签",
        "content": {
          "t_book_id": "204200150000004872",
          "tags": {
            "audience": [
              {
                "label_id": "lbl_audience_female",
                "display_name": "女频"
              }
            ],
            "space_time": [
              {
                "label_id": "lbl_space_modern_urban",
                "display_name": "现代都市"
              }
            ],
            "genre": [
              {
                "label_id": "lbl_genre_romance",
                "display_name": "言情"
              }
            ],
            "plot": [
              {
                "label_id": "lbl_plot_contract_marriage",
                "display_name": "契约婚姻"
              }
            ],
            "character": [
              {
                "label_id": "lbl_character_independent_woman",
                "display_name": "独立女性"
              }
            ],
            "emotion": [
              {
                "label_id": "lbl_emotion_abuse_sweet_satisfying",
                "display_name": "虐-甜-爽"
              }
            ]
          }
        }
      },
      {
        "key": "story_overview",
        "type": "json",
        "label": "剧情概览",
        "content": {
          "t_book_id": "204200150000004872",
          "analysis_status": "success",
          "story_overview": {},
          "uncertainties": []
        }
      },
      {
        "key": "tagging_detail",
        "type": "json",
        "label": "完整标签判断",
        "content": {
          "t_book_id": "204200150000004872",
          "analysis_status": "success",
          "tags": {},
          "uncertainties": []
        }
      }
    ],
    "signals": {
      "t_book_id": "204200150000004872",
      "series_structure": "continuous_series",
      "tagging_strategy": "work",
      "result_checksum": "sha256:canonical-result"
    },
    "warnings": []
  },
  "error": null,
  "callback": {
    "status": "delivered",
    "attempts": 1,
    "next_retry_at": null,
    "last_error": null
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

`final_tags` 必须使用 `label_id`。`display_name` 只用于展示和排查，不作为业务引用键。

## Callback

AI 使用通用 `CallbackEnvelope`。`job` 字段与 `GET /api/v1/ai-jobs/jobs/{job_id}` 返回的 `JobView` 同形。

Callback 与 AI 写 RS 的 payload 必须由同一份 `job.result` 派生，并通过 `result_checksum` 对齐。

CPP 必须按 `event_id` 或 `job.job_id + event + attempt` 做幂等消费。

## 终态成功定义

`succeeded` 表示：

```text
AI 已生成 canonical result
AI 已完成结果校验
RS 已接受该 job_id 对应的 ai_auto 结果写入
```

如果 AI 已生成结果但 RS 写入失败，job 必须进入 `failed`，错误码为 `RS_RESULT_WRITE_FAILED`，并向 CPP callback 失败终态。CPP 不需要额外判断 RS 是否落库。

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
| `RS_RESULT_WRITE_FAILED` | AI 结果已生成，但写入 RS 失败。 |
| `JOB_TIMEOUT` | 任务执行超时。 |
| `INTERNAL_ERROR` | AI 服务内部错误。 |

## result_checksum

`result_checksum` 的计算口径：

```text
sha256(canonical_json(JobResult))
```

其中 `canonical_json` 表示：

- 对完整 `JobResult` 计算，不只对 `final_tags` 计算。
- JSON 对象 key 按字典序排序。
- 使用 UTF-8。
- 不包含空白缩进。
- 保留数组顺序，尤其是 `artifacts` 顺序和标签数组顺序。
- 空对象、空数组和 `null` 按 JSON 标准值参与计算。

CPP callback 和 AI 写 RS 必须携带相同 `result_checksum`。
