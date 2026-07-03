# AI 标题图生成接口文档

本文档面向调用方，定义 AI 标题图生成能力的独立交付接口合同；已实现能力和仍属 vNext 的能力在“合同状态说明”中区分。服务前缀、HTTP envelope、通用 Job、Callback、billing、模型、语种和 Prompt 元信息合同均在本文内说明，不依赖其它文档。

> **版本信息**
>
> | 项目 | 内容 |
> |---|---|
> | 文档版本 | `current + vNext review` |
> | 发布日期 | `2026-06-24` |
> | 文档状态 | 部分当前实现，部分 vNext 交付评审草案 |
> | 适用范围 | 模型获取、语种获取、模板获取、任务创建、任务查询、费用查询、终态 Callback |
>
> **修改记录**
>
> | 版本 | 日期 | 修改内容 |
> |---|---|---|
> | `current + vNext review` | `2026-07-03` | 明确终态 Callback payload 中的 `job` 与任务查询 Job snapshot 结构一致，补齐 `job.usage` 合同。 |
> | `current + vNext review` | `2026-07-02` | 任务查询 Job snapshot 增加 `job.usage` 轻量用量摘要投影；模型获取示例补齐 `parameters` 和 `notes`。 |
> | `current + vNext review` | `2026-06-29` | 调整为调用方独立交付文档，移除其它文档依赖；模型获取接口使用 `job_type=poster_title_image` 返回标题图可选模型。 |
> | `current + vNext review` | `2026-06-29` | 更新 dev 环境模型、语种、Prompt 模板和任务查询响应示例，对齐当前接口实际返回字段。 |
> | `current + vNext review` | `2026-06-24` | 初版交付评审草案，定义 AI 标题图生成对接入口、Job 查询结果、费用查询和终态 Callback 合同。 |

## 联调填写区

对接或联调前，先在对应环境填入访问地址、请求头值和 Callback 验签密钥。`SERVICE_API_KEY` 用于生成 `Authorization` 请求头；`CALLBACK_SIGNING_SECRET` 用于调用方校验 AI 服务投递到 `callback.url` 的终态 Callback。`X-Request-ID` 是可选的单次请求追踪 ID，不表示调用方身份。不传 `X-Request-ID` 时，服务端会生成新的请求追踪 ID。

### 开发

| 项 | 值 | 说明 |
|---|---|---|
| Base URL | `http://47.119.149.179:18200` | 本地 dev API |
| `SERVICE_API_KEY` | `dev-service-key` | 用于 `Authorization: Bearer` |
| `CALLBACK_SIGNING_SECRET` | `dev-service-key` | 请求中传 `callback.url` 时必填；dev 默认可与 `SERVICE_API_KEY` 一致 |
| `X-Request-ID` | `dev-poster-title-image-001` | 可选 |
| `X-AI-Service-Caller-ID` | `dev-caller` | 可选；不传时使用 `default` |

### 测试

| 项 | 值 | 说明 |
|---|---|---|
| Base URL |  | 测试环境 AI 服务地址 |
| `SERVICE_API_KEY` | `test_gawgTHkWo6afEC0wAe-1FbTfYQ-_9sOm1B_WQoft7fc` | 用于 `Authorization: Bearer` |
| `CALLBACK_SIGNING_SECRET` | `test_gawgTHkWo6afEC0wAe-1FbTfYQ-_9sOm1B_WQoft7fc` | 请求中传 `callback.url` 时必填 |
| `X-Request-ID` |  | 可选 |
| `X-AI-Service-Caller-ID` |  | 可选；不传时使用 `default` |

### 生产

| 项 | 值 | 说明 |
|---|---|---|
| Base URL |  | 生产环境 AI 服务地址 |
| `SERVICE_API_KEY` | `prd_9sUubcUpISZKB3OfNP0zRdZZGhoMfm-LK5obiMADyag` | 用于 `Authorization: Bearer` |
| `CALLBACK_SIGNING_SECRET` | `prd_9sUubcUpISZKB3OfNP0zRdZZGhoMfm-LK5obiMADyag` | 请求中传 `callback.url` 时必填 |
| `X-Request-ID` |  | 可选 |
| `X-AI-Service-Caller-ID` |  | 可选；不传时使用 `default` |

请求头：

```http
Authorization: Bearer <SERVICE_API_KEY>
X-Request-ID: <request-id>
X-AI-Service-Caller-ID: <caller-id>
Content-Type: application/json
```

`X-AI-Service-Caller-ID` 格式：长度 1 到 64；首字符必须是 ASCII 字母或数字；后续字符只能使用 ASCII 字母、数字、下划线 `_`、点号 `.`、冒号 `:` 或连字符 `-`。示例：`cpp-service`、`cpp.service:dev`、`caller_01`。不要包含空格、斜杠或中文字符。

Callback 签名：如果创建任务时传了 `callback.url`，调用方必须使用同一个 `CALLBACK_SIGNING_SECRET` 校验 AI 服务回调请求头 `X-Callback-Signature`。当前 dev 环境可约定 `CALLBACK_SIGNING_SECRET` 与 `SERVICE_API_KEY` 使用同一个值，例如 `dev-service-key`。

dev 示例：

```bash
curl -sS -X GET "http://127.0.0.1:8100/api/v1/ai-jobs/models" \
  -H "Authorization: Bearer dev-service-key" \
  -H "X-Request-ID: dev-poster-title-image-001" \
  -H "X-AI-Service-Caller-ID: dev-caller"
```

### 合同状态说明

本文定义交付评审合同，用于双方评审接口形态；不表示所有字段、状态和路由都已经在当前服务实现中上线。

当前服务已支持 `poster_title_image` 声明 `result_snapshot_statuses={"running","failed"}`，在 `running` 和 `failed` 状态返回已成功 item 的 `job_result` 增量快照。当前稳定费用查询入口是 `GET /jobs/{job_id}/billing`；`job.cost` 和 `job.usage` 是 Job snapshot 和 Callback 中的 Job 级费用与用量摘要投影。

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

`X-AI-Service-Caller-ID` 可选；不传时使用 `default` caller。传入时必须满足 `^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,63}$`，否则服务会按未授权请求处理。`X-Request-ID` 也是可选请求头；调用方传入合法值时，服务会在响应 envelope 和响应头中返回同一个请求追踪 ID。

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

图片输入和输出都使用 `OSS URL Ref`，不在接口中传 base64 或本地路径。输入参考图如需对象存储签名，只放在 `public_url` query 中。

```json
{
  "public_url": "https://cpp-rs-dev.oss-ap-southeast-1.aliyuncs.com/ai-output/poster-title/018f9a7f/es/title-layer.png",
  "internal_url": "https://cpp-rs-dev.oss-ap-southeast-1-internal.aliyuncs.com/ai-output/poster-title/018f9a7f/es/title-layer.png",
  "content_type": "image/png",
  "sha256": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
}
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `public_url` | string | 是 | 公网 HTTPS OSS URL；输入参考图可携带对象存储签名 query |
| `internal_url` | string | 是 | 兼容保留字段；输入参考图不要求为 OSS internal URL，输出对象仍返回 AI 服务生成的内网 OSS URL |
| `content_type` | string | 是 | MIME type，例如 `image/png`、`image/jpeg`、`image/webp` |
| `sha256` | string | 是 | 同一个 OSS object 原始内容的小写 64 位 hex SHA-256，不带 `sha256:` 前缀 |

规则：

- URL 必须使用 `https`，不允许 fragment；输入参考图的 `public_url` 可以携带对象存储签名 query。
- URL host 必须命中服务端配置的 OSS allowlist；不允许把该字段作为任意 URL 下载入口。
- 服务读取输入对象时使用 `public_url`；输入参考图的 `internal_url` 只作为兼容字段保留，不参与读取和 OSS object 身份校验。
- 服务读取输入对象后必须校验 MIME、大小和 `sha256`；校验失败返回 `INVALID_INPUT`。
- `sha256` 是 `public_url` 下载到的对象原始内容 hash，不是 URL 字符串的 hash。

### Cost

`job.cost` 只返回 Job 级总费用，不返回 token、图片、视频、音频或 provider 调用明细；需要费用状态和聚合明细时查询 Job billing。

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
- 终态 Job 可返回 `cost`；如果返回，`cost.final=true`。
- 如果费用尚不可用，`cost=null`；费用状态以 Job billing 的 `status` 为准。

### Usage

`job.usage` 只返回 Job snapshot 的轻量用量摘要；任务查询响应和终态 Callback payload 中的 `job` 使用同一结构。不返回 provider 调用明细、输入输出 token 拆分、缓存 token、图片数、价格规则或诊断原因；需要完整聚合明细时查询 Job billing。

```json
{
  "ai_call_count": 3,
  "total_tokens": 1551,
  "final": true
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `ai_call_count` | integer | 该 Job scope 下已记录的 AI provider 调用次数 |
| `total_tokens` | integer 或 null | 该 Job 的总 token 消耗；如果没有 token 维度或 provider 未返回 token，则为 `null` |
| `final` | boolean | `true` 表示用量摘要已随终态 Job 聚合完成 |

规则：

- 非终态 Job 的 `usage` 为 `null`。
- 终态 Job 可返回 `usage`；如果返回，`usage.final=true`。
- 终态 Callback 只会携带终态 Job；如果返回 `usage`，同样必须满足 `usage.final=true`。
- 如果用量摘要尚不可用，`usage=null`；用量聚合状态以 Job billing 的 `status` 为准。
- `total_tokens=null` 表示没有可用 token 维度，不表示 token 消耗为 0。

### Job Status

| 状态 | 说明 |
|---|---|
| `queued` | 已接单，尚未开始执行 |
| `running` | 执行中；可返回已成功生成的 item 增量快照 |
| `succeeded` | 全部 item 成功 |
| `failed` | 整体任务失败；如果失败前已有 item 成功，仍可返回成功 item 结果子集 |

`job_status` 是唯一程序状态。`job_progress.percent` 是唯一保证返回的进度字段，只用于 UI 展示，不能用于判断成功、失败或是否可取结果。服务当前可能同时返回 `stage` 和 `message`，但调用方不能依赖这两个字段一定存在。

## 2. 模型获取接口

获取 AI 标题图生成可用的图片模型列表。调用方应传 `job_type=poster_title_image`，服务返回同一个 `ModelsResponse` 结构，但只包含标题图任务允许调用方选择的模型。

`poster_title_image` 首版允许调用方传入 `items[].model_id`，但必须命中 `app/jobs/types/poster_title_image/models.yaml` 中的生图模型 allowlist；当前默认和 allowlist 均为 `gpt-image-2`。

### Method / Path

```http
GET /api/v1/ai-jobs/models
GET /api/v1/ai-jobs/models?job_type=poster_title_image
```

### Response Example

以下示例是交付给调用方的图片模型响应形状；模型清单会随服务端任务级配置变化。

```json
{
  "code": "0",
  "msg": "success",
  "data": {
    "default_model_id": "gpt-image-2",
    "models": [
      {
        "id": "gpt-image-2",
        "name": "gpt-image-2",
        "model_type": "image",
        "provider": "openai",
        "enabled": true,
        "capabilities": [
          "image_generation",
          "image_edit"
        ],
        "input_media_types": [
          "text/plain",
          "image/png",
          "image/jpeg",
          "image/webp"
        ],
        "output_media_types": [
          "image/png",
          "image/jpeg",
          "image/webp"
        ],
        "limits": {
          "max_output_count": 4
        },
        "features": {
          "native_transparency": false,
          "supports_edit": true
        },
        "parameters": [
          {
            "name": "n",
            "label": "数量",
            "type": "integer",
            "required": false,
            "default": 1,
            "min": 1,
            "max": 4
          },
          {
            "name": "size",
            "label": "尺寸",
            "type": "select",
            "required": false,
            "default": "auto",
            "options": [
              "auto",
              "1024x1024",
              "1536x1024",
              "1024x1536"
            ]
          },
          {
            "name": "background",
            "label": "背景",
            "type": "select",
            "required": false,
            "default": "auto",
            "options": [
              "opaque",
              "auto"
            ]
          },
          {
            "name": "quality",
            "label": "质量",
            "type": "select",
            "required": false,
            "default": "auto",
            "options": [
              "auto",
              "high",
              "medium",
              "low"
            ]
          },
          {
            "name": "output_format",
            "label": "格式",
            "type": "select",
            "required": false,
            "default": "png",
            "options": [
              "png",
              "jpeg",
              "webp"
            ]
          }
        ],
        "notes": ""
      }
    ]
  },
  "request_id": "trace-id-123",
  "server_time": "2026-07-02T12:38:24.128921+00:00"
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `data.default_model_id` | string | `poster_title_image` 默认生图模型；调用方不传 `job_params.items[].model_id` 时服务端使用该模型 |
| `data.models` | array | 当前可用于标题图生成的图片模型列表 |
| `data.billing_enabled` | boolean，可选 | 服务开启模型目录 billing 能力展示时返回；未开启时省略 |
| `data.cost_estimate_available` | boolean，可选 | 服务开启模型目录 billing 能力展示时返回；未开启时省略 |
| `data.models[].id` | string | 图片模型 ID；任务创建接口的 `job_params.items[].model_id` 必须使用该列表中的值 |
| `data.models[].name` | string | 模型名称 |
| `data.models[].model_type` | string | 模型粗分类；本接口当前只返回 `image` |
| `data.models[].provider` | string | 模型供应方标识 |
| `data.models[].enabled` | boolean | 模型是否在当前服务目录中启用 |
| `data.models[].capabilities` | array | 服务定义的公开能力值，例如 `image_generation`、`image_edit` |
| `data.models[].input_media_types` | array | 模型可接受的输入 MIME type |
| `data.models[].output_media_types` | array | 模型可输出的 MIME type |
| `data.models[].limits` | object | 类型化公开限制；图片模型当前包含 `max_output_count` |
| `data.models[].features` | object | 类型化公开能力开关，例如 `supports_edit`、`native_transparency` |
| `data.models[].parameters` | array | 模型目录中允许展示的可配置参数 schema；是否可提交仍以任务创建接口合同为准 |
| `data.models[].parameters[].name` | string | 参数名 |
| `data.models[].parameters[].label` | string | 参数展示名称 |
| `data.models[].parameters[].type` | string | 参数类型：`string`、`integer`、`number`、`boolean` 或 `select` |
| `data.models[].parameters[].required` | boolean | 参数是否必填 |
| `data.models[].parameters[].default` | string、number 或 boolean | 参数默认值 |
| `data.models[].parameters[].options` | array，可选 | `select` 参数允许值；不适用时省略 |
| `data.models[].parameters[].min` | number，可选 | 数值参数最小值；不适用时省略 |
| `data.models[].parameters[].max` | number，可选 | 数值参数最大值；不适用时省略 |
| `data.models[].notes` | string | 模型公开备注；没有备注时为空字符串 |

`/models?job_type=poster_title_image` 在本文交付范围内只返回标题图任务允许展示和提交的模型。当前 `poster_title_image` 可提交的生图模型基线是 `gpt-image-2`。

`parameters[]` 只描述模型目录可展示的模型级参数 schema，不是任务创建接口的直接提交合同。调用方创建任务时仍以第 5 节的 `job_params.items[].model_options` 合同为准，例如使用 `draw_count` 表达候选图数量、`background` 固定为 `transparent`、`output_format` 固定为 `png`，且 `size` 只能提交本业务约束表允许的值。

## 3. 语种获取接口

获取 AI 服务当前可用的基础语种列表。该接口不接收 `job_type`，不返回 `poster_title_image` 业务参数。

本节定义 `poster_title_image` 可提交的语种目录。任务创建接口中的 `job_params.items[].language` 必须来自本接口返回的 `data.languages[].language`，并以服务端校验为准。

### Method / Path

```http
GET /api/v1/ai-jobs/languages
```

### Response Example

以下示例来自 `2026-06-29` 开发环境，用于展示当前语种目录快照；语种目录会随服务端配置变化。

```json
{
  "code": "0",
  "msg": "success",
  "data": {
    "languages": [
      {
        "language": "zh",
        "display_name": "Chinese (Simplified)",
        "native_name": "中文（简体）"
      },
      {
        "language": "zh-TW",
        "display_name": "Chinese (Traditional)",
        "native_name": "繁體中文"
      },
      {
        "language": "en",
        "display_name": "English",
        "native_name": "English"
      },
      {
        "language": "es",
        "display_name": "Spanish",
        "native_name": "Español"
      },
      {
        "language": "pt",
        "display_name": "Portuguese",
        "native_name": "Português"
      },
      {
        "language": "in",
        "display_name": "Indonesian",
        "native_name": "Bahasa Indonesia"
      },
      {
        "language": "th",
        "display_name": "Thai",
        "native_name": "ไทย"
      },
      {
        "language": "de",
        "display_name": "German",
        "native_name": "Deutsch"
      },
      {
        "language": "fr",
        "display_name": "French",
        "native_name": "Français"
      },
      {
        "language": "hi",
        "display_name": "Hindi",
        "native_name": "हिन्दी"
      },
      {
        "language": "fil",
        "display_name": "Filipino",
        "native_name": "Filipino"
      },
      {
        "language": "tr",
        "display_name": "Turkish",
        "native_name": "Türkçe"
      },
      {
        "language": "ko",
        "display_name": "Korean",
        "native_name": "한국어"
      },
      {
        "language": "ja",
        "display_name": "Japanese",
        "native_name": "日本語"
      },
      {
        "language": "ru",
        "display_name": "Russian",
        "native_name": "Русский"
      },
      {
        "language": "ar",
        "display_name": "Arabic",
        "native_name": "العربية"
      },
      {
        "language": "it",
        "display_name": "Italian",
        "native_name": "Italiano"
      },
      {
        "language": "pl",
        "display_name": "Polish",
        "native_name": "Polski"
      },
      {
        "language": "ro",
        "display_name": "Romanian",
        "native_name": "Română"
      },
      {
        "language": "cs",
        "display_name": "Czech",
        "native_name": "Čeština"
      },
      {
        "language": "bg",
        "display_name": "Bulgarian",
        "native_name": "Български"
      },
      {
        "language": "vi",
        "display_name": "Vietnamese",
        "native_name": "Tiếng Việt"
      }
    ]
  },
  "request_id": "trace-id-123",
  "server_time": "2026-06-29T09:44:10.518388+00:00"
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `data.languages` | array | 当前接口返回的可用语种列表 |
| `data.languages[].language` | string | 提交任务时使用的语种代码 |
| `data.languages[].display_name` | string | 英文展示名称 |
| `data.languages[].native_name` | string | 本地语言展示名称 |

语种代码 `in` 表示 Indonesian；本服务不会在内部目录接口中映射为 `id`。

## 4. 模板获取接口

获取指定任务类型下的默认提示词模板。调用方可以展示模板内容，并在创建任务时通过 `prompt_overrides` 临时覆盖；临时覆盖只对本次任务生效。

当前模板内容由服务端维护；调用方应通过本接口读取当前默认模板，不要在客户端硬编码默认提示词内容。

### Method / Path

```http
GET /api/v1/ai-jobs/prompt-templates?job_type=poster_title_image
```

### Query

| 参数 | 必填 | 说明 |
|---|---:|---|
| `job_type` | 否 | 当前默认值为 `poster_title_image`；显式传入时也必须为 `poster_title_image` |

当前实现不接收 `language`、`model_id` 或 `schema_version` 作为模板查询条件。语言和 item 级提示词差异由创建任务时的 `job_params.items[]` 与 `prompt_overrides` 表达。

### Response

以下示例来自 `2026-06-29` 开发环境。`default_content` 是当前默认模板内容快照，不是长期固定文案；调用方如果要展示模板或生成覆盖编辑器，应以接口实时返回为准。模板内容调整不改变本接口的字段合同。

```json
{
  "code": "0",
  "msg": "success",
  "data": {
    "version": "poster_title_image.v1",
    "job_type": "poster_title_image",
    "name": "Poster title image",
    "description": "Generate transparent poster title image layers from reference title styles.",
    "prompt_blocks": [
      {
        "key": "style_probe",
        "role": "user",
        "label": "Style probe",
        "default_content": "Analyze this title image and describe the visual design style of the LETTERFORMS ONLY.\nExtract only reusable letterform attributes for a later image-generation prompt. Do not absorb, imitate, or describe any background, board, plaque, panel, canvas, carrier shape, texture field, atmospheric glow, haze, fog, halo, cast shadow, drop shadow, rim glow, lighting bloom, or object behind or around the text. Treat these as contamination unless they are visibly painted, engraved, or built into the strokes themselves.\n\nOutput a single stable English paragraph suitable for direct reuse inside a later image prompt. Be precise, generative, and self-contained. Describe what a new title's letters should look like, not what the surrounding image looks like.\n\nCover only the following aspects of the letterforms themselves:\n- Stroke weight and overall letter mass: whether the type is heavy/bold, medium, or light/thin; describe the main stroke thickness and visual density.\n- Letter dimensionality: flat / subtly embossed / 3D beveled / carved / extruded\n- Material and surface texture inside the strokes: metal, stone, glass, painted, printed, inked, carved, distressed, or smooth; include only texture that appears on the letter surfaces.\n- Lighting on the letterforms: highlights, reflections, shading, bevel edge treatment, and color temperature only where light is visibly part of the letters.\n- Color palette of the letters: fill color, stroke or outline color, inner gradients, inline accents, and edge colors; describe letter colors only, never background colors.\n- Built-in letter effects: cracks, distress, wear, abrasion, engraved marks, chips, or small fragments that are clearly attached to or cut from the strokes; exclude separate debris, smoke, sparks, splashes, or particles.\n- Typography character: serif style, stroke weight contrast, condensed or expanded proportions, decorative details, overall weight and mood\n- Composition scale of the text block: how large the letters fill the frame, such as \"letters fill nearly the full frame width\" or \"compact centered wordmark\"\n- Overall cinematic / genre mood expressed by the letterforms only\n\nDo NOT mention or describe: background color, background texture, atmospheric glow or haze behind the text, plaque or board silhouettes, frames, banners, badges, shadows cast behind the text, light rays, props, scenery, symbols, icons, or any element that is not part of the letterforms themselves.\nDo NOT include instructions to reproduce the reference image background or carrier. Do NOT include uncertain phrases such as \"appears to\" or \"maybe\".\n\nOutput ONLY the style description paragraph - no headers, no preamble, no explanation."
      },
      {
        "key": "additional_prompt",
        "role": "user",
        "label": "Additional title prompt",
        "default_content": "High-resolution poster title text only, spelling the requested title exactly with no missing, extra, replaced, or distorted characters. The letterforms must be heavy, bold, legible, and visually dominant, with crisp hard edges and clean internal counters. Render only the text itself: no illustration, icon, symbol, object, decoration, drop shadow, cast shadow, outer glow, halo, blur, bloom, smoke, spark, splash, backing plate, brush stroke, banner, badge, frame, plaque, panel, or any non-text carrier element.\n\nPlace the title on a perfectly uniform flat chroma green background for post-processing. The green area must be smooth and untextured, with no gradient, vignette, noise, shadow, reflection, glow, or lighting variation. Keep the text centered, complete, fully visible, and separated from the frame edges, ready to be isolated as a poster title layer."
      },
      {
        "key": "layout_rules",
        "role": "user",
        "label": "Layout rules",
        "default_content": "The title is a horizontal poster-title layer. Render the text large and bold, filling about 85-95% of the frame width while preserving a clear safety margin on every side. Keep text spacing natural and balanced. Do not crop, truncate, overlap, squeeze, warp, rotate, or scatter the text. The full title must remain centered, readable, and naturally spaced."
      }
    ]
  },
  "request_id": "trace-id-123",
  "server_time": "2026-06-29T09:44:33.600341+00:00"
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `data.version` | string | Prompt 配置版本 |
| `data.job_type` | string | 任务类型，当前为 `poster_title_image` |
| `data.name` | string | 模板展示名称 |
| `data.description` | string | 模板说明 |
| `data.prompt_blocks[]` | array | 可展示和可覆盖的提示词块 |
| `data.prompt_blocks[].key` | string | 稳定提示词块 key |
| `data.prompt_blocks[].role` | string | 默认消息角色 |
| `data.prompt_blocks[].label` | string | 展示标签 |
| `data.prompt_blocks[].default_content` | string | 默认提示词内容 |

稳定提示词块：

| `data.prompt_blocks[].key` | 说明 | 创建任务覆盖字段 |
|---|---|---|
| `style_probe` | 风格探针 | `job_params.items[].prompt_overrides.style_probe` |
| `additional_prompt` | 附加视觉或风格提示词，不控制换行或换行位置 | `job_params.items[].prompt_overrides.additional_prompt` |
| `layout_rules` | 视觉排版偏好，不控制换行或换行位置 | `job_params.items[].prompt_overrides.layout_rules` |

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
        "item_id": "es",
        "language": "es",
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
          "public_url": "https://cpp-rs-dev.oss-ap-southeast-1.aliyuncs.com/artwork_cover/material/200000000000006250/160001000000006250/title/es.png",
          "internal_url": "https://cpp-rs-dev.oss-ap-southeast-1-internal.aliyuncs.com/artwork_cover/material/200000000000006250/160001000000006250/title/es.png",
          "content_type": "image/png",
          "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        },
        "prompt_overrides": {
          "style_probe": "optional item-level style probe override",
          "additional_prompt": "optional item-level visual/style prompt; must not control line breaks or line break positions",
          "layout_rules": "optional item-level visual layout preference; must not control line breaks or line break positions"
        }
      },
      {
        "item_id": "pt",
        "language": "pt",
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
          "public_url": "https://cpp-rs-dev.oss-ap-southeast-1.aliyuncs.com/artwork_cover/material/200000000000006250/160001000000006250/title/pt.png",
          "internal_url": "https://cpp-rs-dev.oss-ap-southeast-1-internal.aliyuncs.com/artwork_cover/material/200000000000006250/160001000000006250/title/pt.png",
          "content_type": "image/png",
          "sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
        },
        "prompt_overrides": {
          "style_probe": "optional item-level style probe override",
          "additional_prompt": "optional item-level visual/style prompt; must not control line breaks or line break positions",
          "layout_rules": "optional item-level visual layout preference; must not control line breaks or line break positions"
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
| `job_params.items` | array | 是 | 批量生成 item，至少 1 个；数量上限由服务端 `POSTER_TITLE_IMAGE_MAX_ITEMS` 配置，默认 50 |
| `job_params.items[].item_id` | string | 是 | 调用方提供的稳定 item 关联键；同一任务内唯一 |
| `job_params.items[].language` | string | 是 | 语种代码，必须来自第 3 节语种目录；同一任务内允许重复 |
| `job_params.items[].title_text` | string | 是 | 目标语种标题文本，也是唯一调用方硬分行来源；无 LF `\n` 时服务端允许按标题区域自动换行；有 LF `\n` 时，LF 所在位置就是调用方指定硬分行位置 |
| `job_params.items[].model_id` | string | 否 | 标题图生图模型 ID；不传时使用服务端 `poster_title_image` 默认生图模型 |
| `job_params.items[].model_options.size` | string | 是 | 目标输出尺寸 |
| `job_params.items[].model_options.quality` | string | 是 | 目标输出质量 |
| `job_params.items[].model_options.draw_count` | integer | 否 | 该 item 返回的标题图片候选数量，默认 1，范围 1 到 4，且不能超过服务端 `POSTER_TITLE_IMAGE_MAX_DRAW_COUNT` |
| `job_params.items[].model_options.background` | string | 是 | 业务输出背景要求，不是 provider raw 参数 |
| `job_params.items[].model_options.output_format` | string | 是 | 业务输出格式要求，不是 provider raw 参数 |
| `job_params.items[].reference_image` | object | 是 | 该 item 的参考图，使用 `OSS URL Ref` |
| `job_params.items[].prompt_overrides.style_probe` | string | 否 | 该 item 的风格探针提示词覆盖 |
| `job_params.items[].prompt_overrides.additional_prompt` | string | 否 | 该 item 的附加视觉或风格提示词；不定义、不新增、不删除、不合并、不拆分、不重排、不调整 `title_text` 换行位置 |
| `job_params.items[].prompt_overrides.layout_rules` | string | 否 | 该 item 的视觉排版偏好覆盖；不定义、不新增、不删除、不合并、不拆分、不重排、不调整 `title_text` 换行位置 |
| `callback.url` | string | 否 | 终态通知地址；传 `callback` 时必填，必须为 HTTPS URL |
| `callback.events` | array | 否 | 需要通知的终态事件；不传时默认通知全部终态事件 |
| `metadata` | object | 否 | 调用方透传元数据，服务不按该字段做业务决策 |
| `options.priority` | string | 否 | 首版固定为 `normal` |
| `options.idempotency_mode` | string | 否 | 首版支持 `return_existing` |

### Poster Title Image Constraints

`GET /models` 返回当前可用于标题图生成的图片模型。`GET /languages` 返回当前可提交语种。新增地区变体前，必须先进入第 3 节语种目录，不能在任务创建接口单独维护平行语种代码。

| 约束 | 值 |
|---|---|
| `job_params.items` | 至少 1 个 item；默认最多 50 个，受服务端 `POSTER_TITLE_IMAGE_MAX_ITEMS` 配置限制 |
| `job_params.items[].item_id` | 1 到 64 个字符；同一任务内唯一；首字符必须是字母或数字，后续只允许字母、数字、`.`、`_`、`-` |
| `job_params.items[].language` | 语种代码必须来自第 3 节 `GET /languages` 返回的 `data.languages[].language`；同一任务内允许重复 |
| `job_params.items[].title_text` | 1 到 200 个字符；仅支持 LF `\n` 作为调用方指定硬换行，LF 所在位置就是硬分行位置；最大硬分行行数由当前服务端校验限制控制，默认 2；可传 LF 数量由最大行数减 1 派生，默认最多 1 个 LF；不支持 CRLF、其它换行字符或 HTML `<br />` |
| `job_params.items[].model_id` | 可省略；默认值和 allowlist 来自 `app/jobs/types/poster_title_image/models.yaml`；当前默认和 allowlist 均为 `gpt-image-2`；同一任务内必须一致 |
| `job_params.items[].model_options.size` | `1024x1024`、`1536x1024`、`1024x1536`、`auto` |
| `job_params.items[].model_options.quality` | `low`、`medium`、`high`、`auto` |
| `job_params.items[].model_options.draw_count` | 1 到 4，且不能超过服务端 `POSTER_TITLE_IMAGE_MAX_DRAW_COUNT` |
| `job_params.items[].model_options.background` | `transparent` |
| `job_params.items[].model_options.output_format` | `png` |
| `job_params.items[].reference_image.public_url` | 必须，HTTPS OSS URL；输入参考图可携带对象存储签名 query |
| `job_params.items[].reference_image.internal_url` | 必须，兼容保留字段；输入参考图不要求为 OSS internal URL |
| `job_params.items[].reference_image.content_type` | 必须，`image/png`、`image/jpeg` 或 `image/webp` |
| `job_params.items[].reference_image.sha256` | 必须，同一个 OSS object 原始内容的小写 64 位 hex SHA-256 |
| 输入图片 MIME | `image/png`、`image/jpeg`、`image/webp` |
| 单个输入图片大小 | 最大 20 MB |
| 单个输入图片尺寸 | 最大 4096 x 4096，且总像素不超过 16777216 |

### Request Rules

- `model_options.background` 只表达业务输出目标，例如 `transparent`；本接口不暴露 `chroma_key_color`、抠图方式或后处理参数。
- 首版不接收海报底图，不返回合成海报或贴图坐标，只返回生成的标题图片。
- 每个 item 是独立业务单元，显式声明自己的模型参数、参考图和提示词覆盖；不同 item 可以传入相同 `reference_image`。
- 同一任务内 `items[].item_id` 必须唯一，并作为请求 item 与结果 item 的主关联键。
- 同一任务内 `items[].language` 允许重复；服务端始终以 `item_id` 关联请求 item 与结果 item。
- 不提供 `batch_options`。首版批量策略固定为 item 独立执行、root Job 最后 join/finalize。
- 服务端按 `reference_image.sha256 + effective style_probe prompt` 复用风格探针结果；这只影响内部执行节点数量，不改变每个 item 的独立结果。
- 所有 item 失败时，Job 进入 `failed`。
- `draw_count` 表示该 item 成功时需要返回的标题图片候选数量。服务端按候选数量多次独立生成，每次只接受 provider 返回 1 张图；`draw_count` 不是 provider raw 参数 `n`。
- 任意一次候选图生成失败，或 provider 单次返回的图片数量不是 1，该 item 都不能标记为 `succeeded`。
- `background=transparent` 且 `output_format!=png` 时，服务端必须直接返回 `INVALID_INPUT`；首版不定义透明 JPEG 或透明 WebP 输出。
- 首版 `output_format` 固定为 `png`，输出 OSS `content_type` 固定为 `image/png`。
- 不允许传 provider API key、provider raw model name、价格规则、token 用量或其它内部字段。
- 不传外层 `model_id`、`model_options`、`source`、`render_options`、`prompt_overrides` 或 `batch_options`。
- 不传拆分的 `bucket`、`region`、`endpoint`、`object_key` 或独立临时签名字段；参考图只使用 `OSS URL Ref` 字段，签名参数只能包含在 `public_url` 内。
- 不传 `items[].layout`；视觉排版由 item 级提示词和服务内部规则共同决定，换行结构由 `title_text` 和服务端派生换行合同决定。
- `items[].title_text` 是唯一调用方硬分行来源。未传 LF `\n` 时服务端允许模型按标题区域、画布和可读性自动换行；传入 LF `\n` 时，LF 所在位置就是调用方指定硬分行位置，服务端会要求模型按这些硬分行位置渲染。硬分行最大行数由当前服务端校验限制控制，默认 2；允许 LF 数量由最大行数减 1 派生，默认最多 1 个 LF。`prompt_overrides.additional_prompt` 和 `prompt_overrides.layout_rules` 只能补充视觉或风格偏好，不能控制 `title_text` 的换行合同或调整硬分行位置。

### Callback Notification

`callback` 是任务创建接口的可选通知配置，不是额外 HTTP 查询接口。服务只在 Job 进入终态后向 `callback.url` 投递通知。Callback 投递失败只影响 `job.callback` 投递摘要；`job.job_status` 不会因为 callback delivery retry 或 dead letter 回退或改写。

只要请求中传入 `callback.url`，调用方就必须提前配置 `CALLBACK_SIGNING_SECRET`，用于校验 AI 服务投递 Callback 时携带的 `X-Callback-Signature`。签名算法为 HMAC-SHA256，签名输入为：

```text
<X-Callback-Timestamp>.<raw request body>
```

签名头格式：

```http
X-Callback-Timestamp: <iso-datetime>
X-Callback-Signature: sha256=<hex-hmac>
```

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
          "item_id": "es",
          "language": "es",
          "status": "succeeded",
          "images": [
            {
              "object": {
                "public_url": "https://cpp-rs-dev.oss-ap-southeast-1.aliyuncs.com/ai-output/poster-title/018f9a7f/es/title-layer.png",
                "internal_url": "https://cpp-rs-dev.oss-ap-southeast-1-internal.aliyuncs.com/ai-output/poster-title/018f9a7f/es/title-layer.png",
                "content_type": "image/png",
                "sha256": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
              },
              "width": 1024,
              "height": 1024
            }
          ],
          "error": null
        },
        {
          "item_id": "pt",
          "language": "pt",
          "status": "succeeded",
          "images": [
            {
              "object": {
                "public_url": "https://cpp-rs-dev.oss-ap-southeast-1.aliyuncs.com/ai-output/poster-title/018f9a7f/pt/title-layer.png",
                "internal_url": "https://cpp-rs-dev.oss-ap-southeast-1-internal.aliyuncs.com/ai-output/poster-title/018f9a7f/pt/title-layer.png",
                "content_type": "image/png",
                "sha256": "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
              },
              "width": 1024,
              "height": 1024
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
    "usage": {
      "ai_call_count": 2,
      "total_tokens": 1551,
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
- Callback payload 顶层 `job` 与 `GET /api/v1/ai-jobs/jobs/{job_id}` 成功响应中的 `data.job` 使用同一 Job snapshot 结构；字段定义、可空性和终态规则沿用第 6 节 `Job Fields` 与 `Query Rules`，无 Callback 专属删字段例外。
- 终态 Callback payload 可返回 `job.cost` 和 `job.usage`；如果返回，`job.cost.final=true` 且 `job.usage.final=true`。
- 如需 billing 状态、聚合明细或诊断信息，调用方应查询 `GET /jobs/{job_id}/billing`。
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
      "usage": null,
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

查询任务当前状态、增量结果、终态费用和终态用量摘要。任务未完成时，已成功生成的 item 标题图片可以先返回给调用方展示；未开始、运行中或失败的 internal leaf job 不进入 `job_result.items[]`。

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
          "total": 1,
          "succeeded": 1,
          "failed": 0,
          "running": 0,
          "pending": 0
        },
        "items": [
          {
            "item_id": "es",
            "language": "es",
            "status": "succeeded",
            "images": [
              {
                "object": {
                  "public_url": "https://cpp-rs-dev.oss-ap-southeast-1.aliyuncs.com/ai-output/poster-title/018f9a7f/es/title-layer.png",
                  "internal_url": "https://cpp-rs-dev.oss-ap-southeast-1-internal.aliyuncs.com/ai-output/poster-title/018f9a7f/es/title-layer.png",
                  "content_type": "image/png",
                  "sha256": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
                },
                "width": 1024,
                "height": 1024
              }
            ],
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
      "usage": null,
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

以下示例来自 `2026-06-29` 开发环境，用于展示单 item 成功终态响应；`job_id`、OSS URL、`sha256`、费用、耗时、Callback 状态和时间戳都是该次 Job 的运行快照。

```json
{
  "code": "0",
  "msg": "success",
  "data": {
    "job": {
      "job_id": "8b88ba9d-eb4b-4f6c-ab79-da28e12d0e70",
      "client_request_id": "real-flow-poster-title-image-06dba1ec-2d25-4bd9-bd9b-fb60cf30bd8f",
      "job_type": "poster_title_image",
      "job_status": "succeeded",
      "job_progress": {
        "percent": 100,
        "stage": "completed",
        "message": "已完成"
      },
      "job_result": {
        "schema_version": "default",
        "job_type": "poster_title_image",
        "batch_summary": {
          "total": 1,
          "succeeded": 1,
          "failed": 0,
          "running": 0,
          "pending": 0
        },
        "items": [
          {
            "item_id": "es",
            "language": "es",
            "status": "succeeded",
            "images": [
              {
                "object": {
                  "public_url": "https://cms-aicg-sz.oss-cn-shenzhen.aliyuncs.com/aicg/dev_root/cms_poster_title/ai-jobs/6f78785a-8ade-4ef5-9677-f0f776f01933/poster-title/8b88ba9d-eb4b-4f6c-ab79-da28e12d0e70/es/title-layer.png",
                  "internal_url": "https://cms-aicg-sz.oss-cn-shenzhen-internal.aliyuncs.com/aicg/dev_root/cms_poster_title/ai-jobs/6f78785a-8ade-4ef5-9677-f0f776f01933/poster-title/8b88ba9d-eb4b-4f6c-ab79-da28e12d0e70/es/title-layer.png",
                  "content_type": "image/png",
                  "sha256": "068688a4d7f3ba970b03a619e4989401b4fa069225286842dc1c047823ef5d56"
                },
                "width": 1024,
                "height": 1024
              }
            ],
            "error": null
          }
        ],
        "duration_ms": {
          "ai_model": 98922,
          "total": 98922
        }
      },
      "job_error": null,
      "cost": {
        "currency": "USD",
        "amount": "0.04491750",
        "final": true
      },
      "usage": {
        "ai_call_count": 2,
        "total_tokens": 1551,
        "final": true
      },
      "callback": {
        "status": "not_configured",
        "attempt": 0,
        "last_error": null,
        "next_retry_at": null
      },
      "status_url": "/api/v1/ai-jobs/jobs/8b88ba9d-eb4b-4f6c-ab79-da28e12d0e70",
      "created_at": "2026-06-29T09:16:23.510156Z",
      "updated_at": "2026-06-29T09:18:04.589707Z",
      "finished_at": "2026-06-29T09:18:04.589707Z"
    }
  },
  "request_id": "trace-id-123",
  "server_time": "2026-06-29T09:45:34.384870+00:00"
}
```

### Failed Response

以下示例表示没有任何 item 成功，因此 `job_result=null`。如果失败前已经有 item 成功，`job_result` 会继续返回这些成功 item 的结果子集。

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
      "job_result": null,
      "job_error": {
        "code": "POSTER_TITLE_IMAGE_ALL_ITEMS_FAILED",
        "message": "all poster title image items failed",
        "details": {
          "failure_phase": "batch_execution"
        }
      },
      "cost": {
        "currency": "USD",
        "amount": "0.042800",
        "final": true
      },
      "usage": {
        "ai_call_count": 1,
        "total_tokens": null,
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
| `job.job_progress.stage` | string 或 null | 可选展示阶段；调用方不能用它判断程序状态 |
| `job.job_progress.message` | string 或 null | 可选展示文案；调用方不能用它判断程序状态 |
| `job.job_result` | object 或 null | 任务结果快照 |
| `job.job_error` | object 或 null | Job 级失败原因 |
| `job.cost` | object 或 null | Job 级总费用；非终态为 `null`，终态可返回 `Cost` |
| `job.usage` | object 或 null | Job 级用量摘要；非终态为 `null`，终态可返回 `Usage` |
| `job.callback` | object | Callback 投递状态摘要；来源是终态 callback 投递账本投影，不表示 Job 执行重试 |
| `job.status_url` | string | 任务查询路径 |
| `job.created_at` | string | 创建时间 |
| `job.updated_at` | string | 更新时间 |
| `job.finished_at` | string 或 null | 终态完成时间 |

### Result Fields

| 字段 | 类型 | 说明 |
|---|---|---|
| `job_result.schema_version` | string | 固定为 `default` |
| `job_result.job_type` | string | 固定为 `poster_title_image` |
| `job_result.batch_summary.total` | integer | 当前 `job_result.items[]` 中的 item 总数；成功终态时等于请求 item 总数 |
| `job_result.batch_summary.succeeded` | integer | 成功 item 数 |
| `job_result.batch_summary.failed` | integer | 当前公开结果中的失败 item 数；当前实现固定为 `0` |
| `job_result.batch_summary.running` | integer | 当前公开结果中的运行中 item 数；当前实现固定为 `0` |
| `job_result.batch_summary.pending` | integer | 当前公开结果中的未开始 item 数；当前实现固定为 `0` |
| `job_result.items[].item_id` | string | 结果 item 主关联键，对应请求中的唯一 `items[].item_id` |
| `job_result.items[].language` | string | 结果 item 语言，必须与请求中同一 `item_id` 的 `language` 一致 |
| `job_result.items[].status` | string | 当前实现对外返回 `succeeded` |
| `job_result.items[].images[]` | array | 生成的标题图片列表 |
| `job_result.items[].images[].object` | object | 标题图片 OSS URL Ref；`content_type` 必须等于请求 item `model_options.output_format` 映射后的 MIME |
| `job_result.items[].images[].width` | integer | 标题图片最终 PNG 宽度，单位为像素 |
| `job_result.items[].images[].height` | integer | 标题图片最终 PNG 高度，单位为像素 |
| `job_result.items[].error` | object 或 null | item 失败原因 |
| `job_result.duration_ms.ai_model` | integer | 已完成内部 AI 节点的 provider 调用耗时累计 |
| `job_result.duration_ms.total` | integer | 已完成内部 AI 节点的服务端执行耗时累计，不包含排队等待时间 |

### Error Object Fields

`job.job_error` 使用以下结构。当前实现不在 `job_result` 中返回失败 item。

| 字段 | 类型 | 说明 |
|---|---|---|
| `error.code` | string | 稳定错误码，例如 `MODEL_CALL_FAILED`、`POSTER_TITLE_IMAGE_ALL_ITEMS_FAILED` |
| `error.message` | string | 可展示或记录的错误消息 |
| `error.details` | object | 结构化错误详情，例如 `failure_phase` |

规则：

- `failed` 时，`job_error` 返回 Job 级汇总原因；如果失败前已有 item 成功，`job_result` 会继续返回这些成功 item 的结果子集。
- `succeeded` item 的 `error` 必须为 `null`。

### Query Rules

- HTTP `200` 只表示成功查到 Job，不表示 Job 执行成功。
- `poster_title_image` 支持在 `running` 和 `failed` 状态返回已成功 item 的 `job_result` 增量快照。
- `queued` 时 `job_result=null`、`cost=null`、`usage=null`。
- `running` 或 `failed` 时可以返回非空 `job_result`，用于展示已经成功生成的 item 图片产物；如果尚未生成首个成功 item，也可以返回 `job_result=null`。
- `running` 或 `failed` 的非空 `job_result.items[]` 只包含 `status=succeeded` 的 item，不返回未开始、运行中或失败的 internal leaf job 状态。
- `succeeded`、`failed` 为终态，可返回 `cost` 和 `usage`；如果返回，`cost.final=true`、`usage.final=true`。
- 如果终态 Job 没有可用 token 维度，`usage.total_tokens=null`；调用方不能把 `null` 当成 0。
- 未传 `callback.url` 时，`job.callback.status=not_configured`，`attempt=0`。
- `running` 或 `failed` 的非空 `job_result.items[]` 必须按请求 `items[]` 顺序返回已成功 item 子集。
- 已经公开为 `succeeded` 的 item，后续响应必须继续返回该 item 及其已产出的 `images`。
- `batch_summary` 必须与 `items[].status` 一致。
- `batch_summary.succeeded` 必须等于 `status=succeeded` 的 item 数。当前实现中 `failed=0`、`running=0`、`pending=0`。
- `job_status=succeeded` 时，所有 item 的 `status` 必须为 `succeeded`，且 `running=0`、`pending=0`、`failed=0`。
- `job_status=failed` 时，失败原因在 `job.job_error`；已经成功生成的 item 仍可继续通过 `job_result` 返回。
- `status=succeeded` 的 item 必须返回该请求 item `model_options.draw_count` 个标题图片对象；每个图片对象必须包含承载 `OSS URL Ref` 的 `object` 字段，以及最终 PNG 的 `width` 和 `height`，`images[]` 顺序稳定。
- 如果某个 item 无法产出请求 item `model_options.draw_count` 个标题图片，该 item 不能标记为 `succeeded`；首版不对外暴露部分成功候选图。
- 当前实现不在 `job_result` 中返回失败 item；失败终态的 Job 级错误见 `job.job_error`。
- 首版不返回文件大小、海报底图、合成海报或贴图坐标。
