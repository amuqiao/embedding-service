# AI 标题图生成接口文档

本文档面向调用方，定义 AI 标题图生成能力的 vNext 交付评审接口合同。本文档为自包含文档，不依赖服务端项目内部文档。

> **版本信息**
>
> | 项目 | 内容 |
> |---|---|
> | 文档版本 | `vNext draft` |
> | 发布日期 | `2026-06-24` |
> | 文档状态 | 交付评审草案，非当前已上线合同 |
> | 适用范围 | 模型获取、语种获取、模板获取、任务创建、任务查询、费用查询、终态 Callback |
>
> **修改记录**
>
> | 版本 | 日期 | 修改内容 |
> |---|---|---|
> | `vNext draft` | `2026-06-24` | 初版交付评审草案，定义 AI 标题图生成对接入口、Job 查询结果、费用查询和终态 Callback 合同。 |

### 合同状态说明

本文定义目标交付合同，用于双方评审接口形态；不表示所有字段、状态和路由已经在当前服务实现中上线。

正式对外发布前，必须完成服务端 route、schema、contract tests、Job 状态枚举、Callback event schema 和共享服务合同同步升级。发布前，非终态 `job_result` 增量快照、`GET /jobs/{job_id}/cost` 和 `job_progress` envelope 都只作为 vNext 目标合同。

## 1. 接入约定

### Base URL

```text
https://<ai-service-host>
```

本文所有接口路径均以 `/api/v1/ai-jobs` 为前缀。

### Authentication

除双方另有约定外，请求需要携带：

```http
Authorization: Bearer <service-key>
X-AI-Service-Caller-ID: <caller-id>
Content-Type: application/json
```

### Success Envelope

所有 HTTP 成功响应均使用统一 envelope：

```json
{
  "code": "0",
  "msg": "success",
  "data": {},
  "request_id": "01J...",
  "server_time": "2026-06-24T12:00:00+00:00"
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `code` | string | `"0"` 表示 HTTP 请求处理成功 |
| `msg` | string | 响应消息 |
| `data` | object | 业务数据 |
| `request_id` | string | 服务端请求追踪 ID |
| `server_time` | string | 服务端响应时间，ISO 8601 |

### Error Envelope

HTTP 请求校验失败、鉴权失败或服务端无法处理请求时返回错误 envelope：

```json
{
  "code": "INVALID_INPUT",
  "msg": "invalid input",
  "error": {
    "code": "INVALID_INPUT",
    "message": "reference image is required",
    "details": {
        "field": "job_params.items[0].reference_image"
    }
  },
  "request_id": "01J...",
  "server_time": "2026-06-24T12:00:00+00:00"
}
```

### OSS URL Ref

图片输入和输出都使用 `OSS URL Ref`，不在接口中传 base64、本地路径或临时签名 URL。

```json
{
  "public_url": "https://cpp-rs-dev.oss-ap-southeast-1.aliyuncs.com/ai-output/poster-title/018f9a7f/es-ES/title-layer.png",
  "internal_url": "https://cpp-rs-dev.oss-ap-southeast-1-internal.aliyuncs.com/ai-output/poster-title/018f9a7f/es-ES/title-layer.png",
  "content_type": "image/png",
  "sha256": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
}
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `public_url` | string | 是 | 公网 HTTPS OSS URL |
| `internal_url` | string | 是 | 内网 HTTPS OSS internal URL |
| `content_type` | string | 是 | MIME type，例如 `image/png`、`image/jpeg`、`image/webp` |
| `sha256` | string | 是 | 同一个 OSS object 原始内容的小写 64 位 hex SHA-256，不带 `sha256:` 前缀 |

规则：

- URL 必须使用 `https`，不允许任何 query string 或 fragment，也不允许携带访问密钥或临时签名参数。
- `public_url` 和 `internal_url` 必须指向同一个 OSS object；如果 bucket、object path 或等价对象身份不一致，服务返回 `INVALID_INPUT`。
- URL host 必须命中服务端配置的 OSS allowlist；不允许把该字段作为任意 URL 下载入口。
- 服务读取输入对象时优先使用 `internal_url`。如果 `internal_url` 不可访问，该 item 失败；服务不自动改用 `public_url` 作为静默兜底。
- 服务读取输入对象后必须校验 MIME、大小和 `sha256`；校验失败返回 `INVALID_INPUT`。
- `sha256` 是对象原始内容的 hash，不是 URL 字符串的 hash；同一个 object 的 `public_url` 和 `internal_url` 共用一个 `sha256`。

### Cost

对外只返回 Job 级总费用，不返回 token、图片、视频、音频或 provider 调用明细。

```json
{
  "currency": "USD",
  "amount": "0.083400",
  "final": true
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `currency` | string | ISO 4217 货币代码 |
| `amount` | string | 十进制定点字符串，表示该 Job 的总费用 |
| `final` | boolean | `true` 表示费用已聚合完成 |

规则：

- 非终态 Job 的 `cost` 为 `null`。
- 终态 Job 的 `cost` 必须存在，且 `cost.final=true`。
- 如果图片生成已完成但费用尚未聚合完成，Job 仍保持 `running`，`cost=null`。

### Job Status

| 状态 | 说明 |
|---|---|
| `queued` | 已接单，尚未开始执行 |
| `running` | 执行中，或图片已完成但费用仍在聚合 |
| `succeeded` | 全部 item 成功 |
| `failed` | 没有任何 item 成功 |

`job_status` 是唯一程序状态。`job_progress.percent` 只用于 UI 展示，不能用于判断成功、失败或是否可取结果。

## 2. 模型获取接口

获取 AI 服务当前可用的基础模型列表。该接口不接收 `job_type`，不返回 `poster_title_image` 业务参数。

本节只给出调用方需要读取的最小字段示例；共享模型目录的完整响应字段以双方最终发布的共享目录合同为准。`poster_title_image` 是否允许使用某个 `model_id`，只由任务创建接口的约束和服务端校验决定。

### Method / Path

```http
GET /api/v1/ai-jobs/models
```

### Response Example

```json
{
  "code": "0",
  "msg": "success",
  "data": {
    "models": [
      {
        "model_id": "gpt-image-2",
        "display_name": "GPT Image 2",
        "provider": "openai"
      }
    ]
  },
  "request_id": "01J...",
  "server_time": "2026-06-24T12:00:00+00:00"
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `models[].model_id` | string | 提交任务时使用的模型 ID |
| `models[].display_name` | string | 展示名称 |
| `models[].provider` | string | 模型供应方标识 |

## 3. 语种获取接口

获取 AI 服务当前可用的基础语种列表。该接口不接收 `job_type`，不返回 `poster_title_image` 业务参数。

本节只给出调用方需要读取的最小字段示例；共享语种目录的完整响应字段以双方最终发布的共享目录合同为准。`poster_title_image` 可提交语种以任务创建接口的约束和服务端校验为准。

### Method / Path

```http
GET /api/v1/ai-jobs/languages
```

### Response Example

```json
{
  "code": "0",
  "msg": "success",
  "data": {
    "languages": [
      {
        "language": "es-ES",
        "display_name": "Spanish (Spain)",
        "native_name": "Español (España)"
      },
      {
        "language": "pt-BR",
        "display_name": "Portuguese (Brazil)",
        "native_name": "Português (Brasil)"
      }
    ]
  },
  "request_id": "01J...",
  "server_time": "2026-06-24T12:00:00+00:00"
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `languages[].language` | string | 提交任务时使用的语种代码 |
| `languages[].display_name` | string | 英文展示名称 |
| `languages[].native_name` | string | 本地语言展示名称 |

## 4. 模板获取接口

获取指定任务类型下的默认提示词模板。调用方可以展示模板内容，并在创建任务时通过 `prompt_overrides` 临时覆盖；临时覆盖只对本次任务生效。

### Method / Path

```http
GET /api/v1/ai-jobs/prompt-templates?job_type=poster_title_image&language=es-ES&model_id=gpt-image-2
```

### Query

| 参数 | 必填 | 说明 |
|---|---:|---|
| `job_type` | 是 | 固定为 `poster_title_image` |
| `language` | 否 | 语种代码；不传时返回通用默认模板 |
| `model_id` | 否 | 模型 ID；不传时返回该任务类型默认模型模板 |
| `schema_version` | 否 | 模板 schema 版本；不传时默认为 `default` |

### Response

```json
{
  "code": "0",
  "msg": "success",
  "data": {
    "schema_version": "default",
    "job_type": "poster_title_image",
    "language": "es-ES",
    "model_id": "gpt-image-2",
    "groups": [
      {
        "group_key": "style_probe",
        "display_name": "风格探针",
        "editable": true,
        "prompts": [
          {
            "prompt_key": "style_probe",
            "prompt_ref": "poster_title_image.style_probe.v1",
            "content": "Analyze the reference title image and describe the visual design style of the letterforms only..."
          }
        ]
      },
      {
        "group_key": "additional_prompt",
        "display_name": "附加提示词",
        "editable": true,
        "prompts": [
          {
            "prompt_key": "additional_prompt",
            "prompt_ref": "poster_title_image.additional_prompt.v1",
            "content": "Keep the result suitable for a standalone poster title layer..."
          }
        ]
      },
      {
        "group_key": "layout_rules",
        "display_name": "排版规则",
        "editable": true,
        "prompts": [
          {
            "prompt_key": "layout_rules",
            "prompt_ref": "poster_title_image.layout_rules.es-ES.v1",
            "content": "标题为横向标题区。先评估该语言文案的视觉宽度..."
          }
        ]
      }
    ]
  },
  "request_id": "01J...",
  "server_time": "2026-06-24T12:00:00+00:00"
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `schema_version` | string | 模板 schema 版本，默认 `default` |
| `job_type` | string | 任务类型 |
| `language` | string 或 null | 本次返回模板对应的语种 |
| `model_id` | string 或 null | 本次返回模板对应的模型 |
| `groups[].group_key` | string | 提示词分组 key |
| `groups[].display_name` | string | 分组展示名称 |
| `groups[].editable` | boolean | 调用方是否允许编辑该分组 |
| `groups[].prompts[].prompt_key` | string | 提示词 key |
| `groups[].prompts[].prompt_ref` | string | 服务端提示词引用 |
| `groups[].prompts[].content` | string | 默认提示词内容 |

稳定分组：

| `group_key` | 说明 | 创建任务覆盖字段 |
|---|---|---|
| `style_probe` | 风格探针 | `job_params.items[].prompt_overrides.style_probe` |
| `additional_prompt` | 附加提示词 | `job_params.items[].prompt_overrides.additional_prompt` |
| `layout_rules` | 排版规则 | `job_params.items[].prompt_overrides.layout_rules` |

## 5. 任务创建接口

创建异步批量标题图生成任务。该接口只表示服务已接单，不表示图片已经生成完成。

### Method / Path

```http
POST /api/v1/ai-jobs/jobs
```

### Request

```json
{
  "client_request_id": "cpp-request-20260624-000001",
  "job_type": "poster_title_image",
  "job_params": {
    "items": [
      {
        "item_id": "es-ES",
        "language": "es-ES",
        "title_text": "Cuando el amor se alejó",
        "model_id": "gpt-image-2",
        "model_options": {
          "size": "auto",
          "quality": "high",
          "draw_count": 1,
          "background": "transparent",
          "output_format": "png"
        },
        "reference_image": {
          "public_url": "https://cpp-rs-dev.oss-ap-southeast-1.aliyuncs.com/artwork_cover/material/200000000000006250/160001000000006250/title/es-ES.png",
          "internal_url": "https://cpp-rs-dev.oss-ap-southeast-1-internal.aliyuncs.com/artwork_cover/material/200000000000006250/160001000000006250/title/es-ES.png",
          "content_type": "image/png",
          "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        },
        "prompt_overrides": {
          "style_probe": "optional item-level style probe override",
          "additional_prompt": "optional item-level additional prompt",
          "layout_rules": "optional item-level layout rule override"
        }
      },
      {
        "item_id": "pt-BR",
        "language": "pt-BR",
        "title_text": "Quando o amor se foi",
        "model_id": "gpt-image-2",
        "model_options": {
          "size": "auto",
          "quality": "high",
          "draw_count": 1,
          "background": "transparent",
          "output_format": "png"
        },
        "reference_image": {
          "public_url": "https://cpp-rs-dev.oss-ap-southeast-1.aliyuncs.com/artwork_cover/material/200000000000006250/160001000000006250/title/pt-BR.png",
          "internal_url": "https://cpp-rs-dev.oss-ap-southeast-1-internal.aliyuncs.com/artwork_cover/material/200000000000006250/160001000000006250/title/pt-BR.png",
          "content_type": "image/png",
          "sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
        },
        "prompt_overrides": {
          "style_probe": "optional item-level style probe override",
          "additional_prompt": "optional item-level additional prompt",
          "layout_rules": "optional item-level layout rule override"
        }
      }
    ]
  },
  "callback": {
    "url": "https://cpp.example.com/ai-callback",
    "events": ["job.succeeded", "job.failed"]
  },
  "metadata": {
    "cpp_task_id": "art-task-123",
    "business_scene": "cover_title"
  },
  "options": {
    "priority": "normal",
    "idempotency_mode": "return_existing"
  }
}
```

### Request Fields

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `client_request_id` | string | 是 | 调用方请求 ID；同一调用方下用于幂等 |
| `job_type` | string | 是 | 固定为 `poster_title_image` |
| `job_params.items` | array | 是 | 批量生成 item，1 到 20 个 |
| `job_params.items[].item_id` | string | 是 | 调用方提供的稳定 item 关联键；同一任务内唯一 |
| `job_params.items[].language` | string | 是 | 语种代码，来自语种获取接口；首版同一任务内必须唯一 |
| `job_params.items[].title_text` | string | 是 | 目标语种标题文本 |
| `job_params.items[].model_id` | string | 是 | 模型 ID，来自模型获取接口 |
| `job_params.items[].model_options.size` | string | 是 | 目标输出尺寸 |
| `job_params.items[].model_options.quality` | string | 是 | 目标输出质量 |
| `job_params.items[].model_options.draw_count` | integer | 否 | 该 item 返回的标题图片候选数量，默认 1，范围 1 到 4 |
| `job_params.items[].model_options.background` | string | 是 | 业务输出背景要求，不是 provider raw 参数 |
| `job_params.items[].model_options.output_format` | string | 是 | 业务输出格式要求，不是 provider raw 参数 |
| `job_params.items[].reference_image` | object | 是 | 该 item 的参考图，使用 `OSS URL Ref` |
| `job_params.items[].prompt_overrides.style_probe` | string | 否 | 该 item 的风格探针提示词覆盖 |
| `job_params.items[].prompt_overrides.additional_prompt` | string | 否 | 该 item 的附加提示词 |
| `job_params.items[].prompt_overrides.layout_rules` | string | 否 | 该 item 的排版规则提示词覆盖 |
| `callback.url` | string | 否 | 终态通知地址；传 `callback` 时必填，必须为 HTTPS URL |
| `callback.events` | array | 否 | 需要通知的终态事件；不传时默认通知全部终态事件 |
| `metadata` | object | 否 | 调用方透传元数据，服务不按该字段做业务决策 |
| `options.priority` | string | 否 | 首版固定为 `normal` |
| `options.idempotency_mode` | string | 否 | 首版支持 `return_existing` |

### Poster Title Image Constraints

`GET /models` 和 `GET /languages` 是服务级基础目录。`poster_title_image` 当前可提交的子集由本接口约束决定。

| 约束 | 值 |
|---|---|
| `job_params.items` | 1 到 20 个 item |
| `job_params.items[].item_id` | 1 到 64 个字符；同一任务内唯一 |
| `job_params.items[].language` | `ja`、`ko`、`ar`、`th`、`ru`、`fr`、`de`、`es-ES`、`es-419`、`pt-BR`、`pt-PT`、`pl`；首版同一任务内唯一 |
| `job_params.items[].title_text` | 1 到 200 个字符 |
| `job_params.items[].model_id` | 首版固定为 `gpt-image-2` |
| `job_params.items[].model_options.size` | `1024x1024`、`1536x1024`、`1024x1536`、`auto` |
| `job_params.items[].model_options.quality` | `low`、`medium`、`high`、`auto` |
| `job_params.items[].model_options.draw_count` | 1 到 4 |
| `job_params.items[].model_options.background` | `transparent`、`auto` |
| `job_params.items[].model_options.output_format` | `png`、`webp`、`jpeg`；`background=transparent` 时首版只允许 `png` |
| `job_params.items[].reference_image.public_url` | 必须，HTTPS OSS URL |
| `job_params.items[].reference_image.internal_url` | 必须，HTTPS OSS internal URL |
| `job_params.items[].reference_image.content_type` | 必须，`image/png`、`image/jpeg` 或 `image/webp` |
| `job_params.items[].reference_image.sha256` | 必须，同一个 OSS object 原始内容的小写 64 位 hex SHA-256 |
| 输入图片 MIME | `image/png`、`image/jpeg`、`image/webp` |
| 单个输入图片大小 | 最大 20 MB |

### Request Rules

- `model_options.background` 只表达业务输出目标，例如 `transparent`；本接口不暴露 `chroma_key_color`、抠图方式或后处理参数。
- 首版不接收海报底图，不返回合成海报或贴图坐标，只返回生成的标题图片。
- 每个 item 是独立业务单元，包含自己的模型、模型参数、参考图和提示词覆盖。
- 同一任务内 `items[].item_id` 必须唯一，并作为请求 item 与结果 item 的主关联键。
- 首版同一任务内 `items[].language` 也必须唯一；如果未来允许同一语言多版本，仍以 `item_id` 关联结果。
- 不提供 `batch_options`。首版批量策略固定为 item 独立执行、root Job 最后 join/finalize。
- 所有 item 失败时，Job 进入 `failed`。
- `background=transparent` 且 `output_format!=png` 时，服务端必须直接返回 `INVALID_INPUT`；首版不定义透明 JPEG 或透明 WebP 输出。
- `output_format` 到输出 OSS `content_type` 的映射固定为：`png -> image/png`、`webp -> image/webp`、`jpeg -> image/jpeg`。
- 不允许传 provider API key、provider raw model name、价格规则、token 用量或其它内部字段。
- 不传外层 `model_id`、`model_options`、`source`、`render_options`、`prompt_overrides` 或 `batch_options`。
- 不传拆分的 `bucket`、`region`、`endpoint`、`object_key` 或临时签名参数；参考图只使用 `OSS URL Ref` 字段。
- 不传 `items[].layout`；排版由 `title_text`、item 级提示词和服务内部规则共同决定。

### Callback Notification

`callback` 是任务创建接口的可选通知配置，不是额外 HTTP 查询接口。服务只在 Job 进入终态后向 `callback.url` 投递通知。

Callback payload 不套 HTTP success envelope：

```json
{
  "event": "job.succeeded",
  "event_id": "evt_018f9a7f",
  "attempt": 1,
  "sent_at": "2026-06-24T12:01:00+00:00",
  "trigger_request_id": "01J...",
  "caller_id": "cpp",
  "job": {
    "job_id": "018f9a7f-0183-4e4f-938d-1baf7411b4fd",
    "client_request_id": "cpp-request-20260624-000001",
    "job_type": "poster_title_image",
    "job_status": "succeeded",
    "job_progress": {
      "percent": 100
    },
    "job_result": {
      "schema_version": "default",
      "job_type": "poster_title_image",
      "batch_summary": {
        "total": 2,
        "succeeded": 2,
        "failed": 0,
        "running": 0,
        "pending": 0
      },
      "items": [
        {
          "item_id": "es-ES",
          "language": "es-ES",
          "status": "succeeded",
          "images": [
            {
              "object": {
                "public_url": "https://cpp-rs-dev.oss-ap-southeast-1.aliyuncs.com/ai-output/poster-title/018f9a7f/es-ES/title-layer.png",
                "internal_url": "https://cpp-rs-dev.oss-ap-southeast-1-internal.aliyuncs.com/ai-output/poster-title/018f9a7f/es-ES/title-layer.png",
                "content_type": "image/png",
                "sha256": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
              }
            }
          ],
          "error": null
        },
        {
          "item_id": "pt-BR",
          "language": "pt-BR",
          "status": "succeeded",
          "images": [
            {
              "object": {
                "public_url": "https://cpp-rs-dev.oss-ap-southeast-1.aliyuncs.com/ai-output/poster-title/018f9a7f/pt-BR/title-layer.png",
                "internal_url": "https://cpp-rs-dev.oss-ap-southeast-1-internal.aliyuncs.com/ai-output/poster-title/018f9a7f/pt-BR/title-layer.png",
                "content_type": "image/png",
                "sha256": "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
              }
            }
          ],
          "error": null
        }
      ],
      "duration_ms": {
        "ai_model": 42310,
        "total": 58920
      }
    },
    "job_error": null,
    "cost": {
      "currency": "USD",
      "amount": "0.083400",
      "final": true
    },
    "callback": {
      "status": "delivered",
      "attempt": 1,
      "last_error": null,
      "next_retry_at": null
    },
    "status_url": "/api/v1/ai-jobs/jobs/018f9a7f-0183-4e4f-938d-1baf7411b4fd",
    "created_at": "2026-06-24T12:00:00+00:00",
    "updated_at": "2026-06-24T12:01:00+00:00",
    "finished_at": "2026-06-24T12:01:00+00:00"
  }
}
```

规则：

- `event` 允许 `job.succeeded`、`job.failed`。
- Callback payload 顶层 `job` 与任务查询接口返回的 `data.job` 结构一致。
- 终态 Callback payload 的 `job.cost` 必须存在，且 `job.cost.final=true`。
- 调用方接收 Callback 时应返回 HTTP `2xx` 和 JSON body：`{"accepted": true}`。
- Callback 失败不改变 Job 终态；调用方仍可通过任务查询接口获取最终结果。

### Accepted Response

```json
{
  "code": "0",
  "msg": "success",
  "data": {
    "job": {
      "job_id": "018f9a7f-0183-4e4f-938d-1baf7411b4fd",
      "client_request_id": "cpp-request-20260624-000001",
      "job_type": "poster_title_image",
      "job_status": "queued",
      "job_progress": {
        "percent": 0
      },
      "job_result": null,
      "job_error": null,
      "cost": null,
      "callback": {
        "status": "pending",
        "attempt": 0,
        "last_error": null,
        "next_retry_at": null
      },
      "status_url": "/api/v1/ai-jobs/jobs/018f9a7f-0183-4e4f-938d-1baf7411b4fd",
      "created_at": "2026-06-24T12:00:00+00:00",
      "updated_at": "2026-06-24T12:00:00+00:00",
      "finished_at": null
    }
  },
  "request_id": "01J...",
  "server_time": "2026-06-24T12:00:00+00:00"
}
```

## 6. 任务查询接口

查询任务当前状态、增量结果和终态费用。任务未完成时，已完成 item 的标题图片可以先返回给调用方展示。

### Method / Path

```http
GET /api/v1/ai-jobs/jobs/{job_id}
```

### Running Response

```json
{
  "code": "0",
  "msg": "success",
  "data": {
    "job": {
      "job_id": "018f9a7f-0183-4e4f-938d-1baf7411b4fd",
      "client_request_id": "cpp-request-20260624-000001",
      "job_type": "poster_title_image",
      "job_status": "running",
      "job_progress": {
        "percent": 55
      },
      "job_result": {
        "schema_version": "default",
        "job_type": "poster_title_image",
        "batch_summary": {
          "total": 2,
          "succeeded": 1,
          "failed": 0,
          "running": 1,
          "pending": 0
        },
        "items": [
          {
            "item_id": "es-ES",
            "language": "es-ES",
            "status": "succeeded",
            "images": [
              {
                "object": {
                  "public_url": "https://cpp-rs-dev.oss-ap-southeast-1.aliyuncs.com/ai-output/poster-title/018f9a7f/es-ES/title-layer.png",
                  "internal_url": "https://cpp-rs-dev.oss-ap-southeast-1-internal.aliyuncs.com/ai-output/poster-title/018f9a7f/es-ES/title-layer.png",
                  "content_type": "image/png",
                  "sha256": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
                }
              }
            ],
            "error": null
          },
          {
            "item_id": "pt-BR",
            "language": "pt-BR",
            "status": "running",
            "images": [],
            "error": null
          }
        ],
        "duration_ms": {
          "ai_model": 31000,
          "total": 42000
        }
      },
      "job_error": null,
      "cost": null,
      "callback": {
        "status": "pending",
        "attempt": 0,
        "last_error": null,
        "next_retry_at": null
      },
      "status_url": "/api/v1/ai-jobs/jobs/018f9a7f-0183-4e4f-938d-1baf7411b4fd",
      "created_at": "2026-06-24T12:00:00+00:00",
      "updated_at": "2026-06-24T12:00:30+00:00",
      "finished_at": null
    }
  },
  "request_id": "01J...",
  "server_time": "2026-06-24T12:00:30+00:00"
}
```

### Terminal Response

全部成功时，`job_status=succeeded`。

```json
{
  "code": "0",
  "msg": "success",
  "data": {
    "job": {
      "job_id": "018f9a7f-0183-4e4f-938d-1baf7411b4fd",
      "client_request_id": "cpp-request-20260624-000001",
      "job_type": "poster_title_image",
      "job_status": "succeeded",
      "job_progress": {
        "percent": 100
      },
      "job_result": {
        "schema_version": "default",
        "job_type": "poster_title_image",
        "batch_summary": {
          "total": 2,
          "succeeded": 2,
          "failed": 0,
          "running": 0,
          "pending": 0
        },
        "items": [
          {
            "item_id": "es-ES",
            "language": "es-ES",
            "status": "succeeded",
            "images": [
              {
                "object": {
                  "public_url": "https://cpp-rs-dev.oss-ap-southeast-1.aliyuncs.com/ai-output/poster-title/018f9a7f/es-ES/title-layer.png",
                  "internal_url": "https://cpp-rs-dev.oss-ap-southeast-1-internal.aliyuncs.com/ai-output/poster-title/018f9a7f/es-ES/title-layer.png",
                  "content_type": "image/png",
                  "sha256": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
                }
              }
            ],
            "error": null
          },
          {
            "item_id": "pt-BR",
            "language": "pt-BR",
            "status": "succeeded",
            "images": [
              {
                "object": {
                  "public_url": "https://cpp-rs-dev.oss-ap-southeast-1.aliyuncs.com/ai-output/poster-title/018f9a7f/pt-BR/title-layer.png",
                  "internal_url": "https://cpp-rs-dev.oss-ap-southeast-1-internal.aliyuncs.com/ai-output/poster-title/018f9a7f/pt-BR/title-layer.png",
                  "content_type": "image/png",
                  "sha256": "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
                }
              }
            ],
            "error": null
          }
        ],
        "duration_ms": {
          "ai_model": 42310,
          "total": 58920
        }
      },
      "job_error": null,
      "cost": {
        "currency": "USD",
        "amount": "0.083400",
        "final": true
      },
      "callback": {
        "status": "delivered",
        "attempt": 1,
        "last_error": null,
        "next_retry_at": null
      },
      "status_url": "/api/v1/ai-jobs/jobs/018f9a7f-0183-4e4f-938d-1baf7411b4fd",
      "created_at": "2026-06-24T12:00:00+00:00",
      "updated_at": "2026-06-24T12:01:00+00:00",
      "finished_at": "2026-06-24T12:01:00+00:00"
    }
  },
  "request_id": "01J...",
  "server_time": "2026-06-24T12:01:00+00:00"
}
```

### Failed Response

没有任何 item 成功时，`job_status=failed`。

```json
{
  "code": "0",
  "msg": "success",
  "data": {
    "job": {
      "job_id": "018f9a7f-0183-4e4f-938d-1baf7411b4fd",
      "client_request_id": "cpp-request-20260624-000001",
      "job_type": "poster_title_image",
      "job_status": "failed",
      "job_progress": {
        "percent": 100
      },
      "job_result": {
        "schema_version": "default",
        "job_type": "poster_title_image",
        "batch_summary": {
          "total": 2,
          "succeeded": 0,
          "failed": 2,
          "running": 0,
          "pending": 0
        },
        "items": [
          {
            "item_id": "es-ES",
            "language": "es-ES",
            "status": "failed",
            "images": [],
            "error": {
              "code": "MODEL_CALL_FAILED",
              "message": "image provider failed",
              "details": {
                "failure_phase": "image_generation"
              }
            }
          },
          {
            "item_id": "pt-BR",
            "language": "pt-BR",
            "status": "failed",
            "images": [],
            "error": {
              "code": "MODEL_CALL_FAILED",
              "message": "image provider failed",
              "details": {
                "failure_phase": "image_generation"
              }
            }
          }
        ],
        "duration_ms": {
          "ai_model": 12450,
          "total": 13020
        }
      },
      "job_error": {
        "code": "ALL_ITEMS_FAILED",
        "message": "all batch items failed",
        "details": {
          "failure_phase": "batch_execution"
        }
      },
      "cost": {
        "currency": "USD",
        "amount": "0.042800",
        "final": true
      },
      "callback": {
        "status": "pending",
        "attempt": 0,
        "last_error": null,
        "next_retry_at": null
      },
      "status_url": "/api/v1/ai-jobs/jobs/018f9a7f-0183-4e4f-938d-1baf7411b4fd",
      "created_at": "2026-06-24T12:00:00+00:00",
      "updated_at": "2026-06-24T12:01:00+00:00",
      "finished_at": "2026-06-24T12:01:00+00:00"
    }
  },
  "request_id": "01J...",
  "server_time": "2026-06-24T12:01:00+00:00"
}
```

### Job Fields

| 字段 | 类型 | 说明 |
|---|---|---|
| `job.job_id` | string | 服务端任务 ID |
| `job.client_request_id` | string | 调用方提交的幂等请求 ID |
| `job.job_type` | string | 固定为 `poster_title_image` |
| `job.job_status` | string | `queued`、`running`、`succeeded`、`failed` |
| `job.job_progress.percent` | integer | 展示进度，非终态 0 到 99，终态 100 |
| `job.job_result` | object 或 null | 任务结果快照 |
| `job.job_error` | object 或 null | Job 级失败原因 |
| `job.cost` | object 或 null | Job 级总费用；非终态为 `null`，终态为 `Cost` |
| `job.callback` | object | Callback 投递状态摘要 |
| `job.status_url` | string | 任务查询路径 |
| `job.created_at` | string | 创建时间 |
| `job.updated_at` | string | 更新时间 |
| `job.finished_at` | string 或 null | 终态完成时间 |

### Result Fields

| 字段 | 类型 | 说明 |
|---|---|---|
| `job_result.schema_version` | string | 固定为 `default` |
| `job_result.job_type` | string | 固定为 `poster_title_image` |
| `job_result.batch_summary.total` | integer | 请求 item 总数 |
| `job_result.batch_summary.succeeded` | integer | 成功 item 数 |
| `job_result.batch_summary.failed` | integer | 失败 item 数 |
| `job_result.batch_summary.running` | integer | 正在执行 item 数 |
| `job_result.batch_summary.pending` | integer | 尚未开始 item 数 |
| `job_result.items[].item_id` | string | 结果 item 主关联键，对应请求中的唯一 `items[].item_id` |
| `job_result.items[].language` | string | 结果 item 语言，必须与请求中同一 `item_id` 的 `language` 一致 |
| `job_result.items[].status` | string | `pending`、`running`、`succeeded`、`failed` |
| `job_result.items[].images[]` | array | 生成的标题图片列表 |
| `job_result.items[].images[].object` | object | 标题图片 OSS URL Ref；`content_type` 必须等于请求 item `model_options.output_format` 映射后的 MIME |
| `job_result.items[].error` | object 或 null | item 失败原因 |
| `job_result.duration_ms.ai_model` | integer | AI provider 调用耗时 |
| `job_result.duration_ms.total` | integer | Job 总耗时 |

### Error Object Fields

`job.job_error` 和 `job_result.items[].error` 使用相同结构。

| 字段 | 类型 | 说明 |
|---|---|---|
| `error.code` | string | 稳定错误码，例如 `MODEL_CALL_FAILED`、`ALL_ITEMS_FAILED` |
| `error.message` | string | 可展示或记录的错误消息 |
| `error.details` | object | 结构化错误详情，例如 `failure_phase` |

规则：

- `failed` 时，如果已经形成 item 快照，`job_result.items[].error` 返回每个失败 item 的原因，`job_error` 返回 Job 级汇总原因。
- `succeeded` item 的 `error` 必须为 `null`。

### Query Rules

- HTTP `200` 只表示成功查到 Job，不表示 Job 执行成功。
- `queued` 时 `job_result=null`、`cost=null`。
- `running` 时可以返回非空 `job_result`，用于展示已完成 item 的图片产物和未完成 item 的状态；如果尚未生成首个 item 快照，也可以返回 `job_result=null`。
- `running` 且全部 item 已经完成时，表示费用仍在聚合；此时 `cost=null`。
- `succeeded`、`failed` 为终态，`cost` 必须存在且 `cost.final=true`。
- `job_result` 一旦非空，必须包含本次请求的全部 item，不允许只返回已完成 item 子集。
- `job_result.items[]` 必须按请求 `items[]` 顺序返回，且每个结果 item 的 `item_id` 必须与请求 item 一一对应。
- item 对外状态只允许按 `pending -> running -> succeeded|failed` 推进；`succeeded` 和 `failed` 是 item 级终态。
- 服务端仍可能内部重试的 item，不应提前对外标记为 `failed`，应继续保持 `pending` 或 `running`。
- 已经公开为 `succeeded` 的 item，后续响应必须继续返回该 item 及其已产出的 `images`。
- `batch_summary` 必须与 `items[].status` 一致。
- `batch_summary.succeeded` 必须等于 `status=succeeded` 的 item 数，`failed`、`running`、`pending` 同理。
- `job_status=succeeded` 时，所有 item 的 `status` 必须为 `succeeded`，且 `running=0`、`pending=0`、`failed=0`。
- `job_status=failed` 表示没有任何 item 成功；如果已经形成 item 快照，应返回非空 `job_result`，且所有 item 的 `status` 必须为 `failed`，`succeeded=0`、`running=0`、`pending=0`。
- Job 在形成首个 item 快照前失败时，`job_status=failed` 可以返回 `job_result=null`，失败原因在 `job.job_error`。
- `status=pending` 或 `status=running` 的 item 必须返回空 `images`、`error=null`。
- `status=succeeded` 的 item 必须返回该请求 item `model_options.draw_count` 个标题图片对象；每个图片对象的 `object` 字段承载 `OSS URL Ref`，`images[]` 顺序稳定。
- 如果某个 item 无法产出请求 item `model_options.draw_count` 个标题图片，该 item 不能标记为 `succeeded`。
- `status=failed` 的 item 必须返回空 `images` 和非空 `error`。
- 首版不返回图片 `width`、`height`、文件大小、海报底图、合成海报或贴图坐标。
