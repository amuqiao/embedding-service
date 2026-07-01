# AI Poster Title Image API

本文定义 CPP 美术任务接入 AI 标题图生成时需要确认的接口合同。

## Status

- 合同状态：current implementation with remaining vNext proposals。
- 当前优先级：服务前缀、认证、HTTP envelope、通用错误和共享 Job 状态语义以 [`service-contract.md`](service-contract.md) 为准。
- 已实现：`poster_title_image` 声明 `result_snapshot_statuses={"running","failed"}`，在 `running` 和 `failed` 状态可以返回非空 `job_result`，用于展示已经成功生成的标题图 item。
- 当前费用查询以 [`service-contract.md`](service-contract.md) 中的 `/jobs/{job_id}/billing` 为准；`job.cost` 是 Job snapshot 和 Callback 中的 Job 级总费用快照。

## Scope

本文覆盖 CPP 本期需要的业务接入面：

1. 模型列表：返回厂商和模型基础信息。
2. 语言列表：返回基础语言目录。
3. 默认提示词：查看服务支持的提示词模板入口；当前支持的查询维度以 [`service-contract.md`](service-contract.md) 为准。
4. 生成图片：通过异步 Job 创建批量标题图生成任务，并通过轮询获取增量结果和终态，通过 Callback 获取终态通知和总费用。
5. 费用查询：通过 Job billing 查询 Job scope 的费用聚合结果，读取总费用、计费状态和诊断信息。

本文不暴露 provider 密钥、内部价格矩阵、绿底抠图策略、透明背景兼容细节、OSS 转存实现、worker 编排细节或 provider raw response。

## Key Rules

- `model_options.background` 只表达业务输出目标，例如 `transparent`；本接口不向 CPP 暴露 `chroma_key_color`、抠图方式或后处理参数。AI 服务内部负责生成可用的透明标题 PNG 或可合成产物。
- 输入参考图和输出图片都使用 OSS URL ref；不直接传 base64、本地路径或临时签名 URL。
- CPP 临时修改的提示词只对本次 Job 生效，不写回默认提示词模板。
- `client_request_id` 使用 CPP 的 `requestID`，作为同一 caller 下的提交幂等键。
- 轮询是主链路，Callback 是终态通知。终态 Job 查询和 Callback 可返回 Job 级总费用 `Cost`；Callback 失败不改变 Job 终态。
- 一个 `poster_title_image` Job 可以包含多个语言 item；每个 item 有独立参考图、模型参数和提示词覆盖。
- `job.cost` 只返回 Job 级总费用 `Cost`：`currency`、`amount`、`final`。需要计费状态、调用次数、计费单位或诊断信息时查询 Job billing。
- 内部可以在 `aigc_api_logs`、AI call ledger 或等价日志中保留每次调用的 `provider`、`model`、`operation`、`usage_detail` 和成本明细；这些明细不属于外部 API 合同。

## Shared Types

### OSS URL Ref

CPP 传入参考图、AI 服务返回生成图片时都使用 `OSS URL Ref`。AI 服务读取输入时使用 `public_url`；调用方读取输出时可以使用 `public_url` 或按自身网络选择 `internal_url`。`public_url` 可以是 OSS 官方公网域名，也可以是服务端配置的 CDN/public endpoint。

```json
{
  "public_url": "https://cpp-rs-dev.oss-ap-southeast-1.aliyuncs.com/ai-output/poster-title/018f9a7f/es/title-layer.png",
  "internal_url": "https://cpp-rs-dev.oss-ap-southeast-1-internal.aliyuncs.com/ai-output/poster-title/018f9a7f/es/title-layer.png",
  "content_type": "image/png",
  "sha256": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
}
```

字段规则：

| 字段 | 类型 | 必填 | 说明 |
|---|---:|---:|---|
| `public_url` | string | 是 | 公网 HTTPS OSS URL 或服务端配置的 CDN/public endpoint URL |
| `internal_url` | string | 是 | 内网 HTTPS OSS internal URL |
| `content_type` | string | 是 | MIME type，例如 `image/png`、`image/jpeg`、`image/webp` |
| `sha256` | string | 是 | 同一个 OSS object 原始内容的小写 64 位 hex SHA-256，不带 `sha256:` 前缀 |

OSS 责任边界：

- URL 必须使用 `https`，不允许任何 query string 或 fragment，也不允许携带访问密钥或临时签名参数。
- `public_url` 和 `internal_url` 必须指向同一个 OSS object；如果 bucket、object path 或等价对象身份不一致，服务返回 `INVALID_INPUT`。
- URL host 必须命中 OSS 官方公网域名，或命中服务端配置的当前 bucket/project 专用 CDN/public endpoint；不允许把该字段作为任意 URL 下载入口。
- AI 服务读取输入对象时使用 `public_url`；`internal_url` 仍必须提供，并用于校验它和 `public_url` 指向同一个 OSS object。
- AI 服务读取输入对象后必须校验 MIME、大小和 `sha256`；校验失败返回 `INVALID_INPUT`。
- `sha256` 是对象原始内容的 hash，不是 URL 字符串的 hash；同一个 object 的 `public_url` 和 `internal_url` 共用一个 `sha256`。
- 输出对象由 AI 服务写入约定的输出 namespace，并在 `job_result.items[].images[].object` 返回同一结构的 OSS URL ref。

### Cost

对外费用只使用 Job 级总费用三元组。

```json
{
  "currency": "USD",
  "amount": "0.755700",
  "final": true
}
```

字段规则：

| 字段 | 类型 | 必填 | 说明 |
|---|---:|---:|---|
| `currency` | string | 是 | ISO 4217 货币代码 |
| `amount` | string | 是 | 十进制定点字符串，表示该 Job 聚合后的总费用 |
| `final` | boolean | 是 | `true` 表示该费用已经根据内部调用日志聚合完成 |

Rules:

- `amount` 是 Job 内所有可计费 provider 调用的总和。例如 LLM、图片生成、视频生成或查询调用都只折算进一个总费用。
- `job.cost` 不返回 token、图片、音频、视频、调用次数、provider、model、operation 或 pricing ref 明细。
- `job.cost` 只在终态 Job snapshot 中返回；如果返回，必须是 `final=true`。非终态 Job snapshot 的 `job.cost` 必须为 `null`。
- 独立费用查询使用 [`service-contract.md`](service-contract.md) 中的 `/jobs/{job_id}/billing`；费用是否可用由 `BillingEnvelope.status` 表达。
- 如果费用尚不可用，`job.cost` 为 `null`；费用状态以 Job billing 的 `status` 为准。
- 当费用计算失败且无法形成可信总数时，返回统一错误响应，不返回伪造的 0 成本成功。

### Job Progress

`job_status` 是唯一程序状态。`job_progress` 只表达展示进度，不表达状态机或业务步骤。

```json
{
  "percent": 55,
  "stage": "calling_model",
  "message": "正在生成标题图"
}
```

字段规则：

| 字段 | 类型 | 必填 | 说明 |
|---|---:|---:|---|
| `percent` | integer | 是 | 0 到 100 的整数 |
| `stage` | string | 否 | 服务当前可能返回的内部进度阶段；调用方不能依赖该字段一定存在 |
| `message` | string 或 null | 否 | 服务当前可能返回的展示文案；调用方不能依赖该字段一定存在 |

Rules:

- 调用方只能使用 `job_status` 判断排队、运行、成功或失败。
- `percent` 是近似进度，不表达计费比例或 item 完成比例。
- `percent` 只用于 UI 展示，调用方不能用它判断成功、失败或是否可取结果。
- 终态 Job 的 `percent` 必须是 `100`。
- 非终态 Job 的 `percent` 必须是 `0` 到 `99`。
- 非终态 `percent` 可以不精确，但同一次 Job attempt 内不应倒退。
- 业务内部步骤可以写入服务日志或内部 trace，不进入通用 progress 合同。

## 1. Shared Model Catalog

CPP 可以调用 `GET /api/v1/ai-jobs/models?job_type=poster_title_image` 渲染标题图任务可选模型。该接口响应结构仍是共享 `ModelsResponse`，但 `data.default_model_id` 和 `data.models[]` 会按 `poster_title_image` 的任务级模型配置过滤。

本文不定义 `/models` 的响应结构；该接口的权威合同以 [`service-contract.md`](service-contract.md) 为准。`poster_title_image` 首版允许调用方传入 `items[].model_id`，但必须命中 `app/jobs/types/poster_title_image/models.yaml` 中的 `public_model_selection.allowed_model_ids`。

## 2. Shared Language Catalog

CPP 可以调用服务级基础语言目录 `GET /api/v1/ai-jobs/languages` 渲染语言名称。该接口只返回语言基础信息，不接收 `job_type`，不返回 `poster_title_image` 业务字段。

本文不重复定义 `/languages` 的完整响应结构；该接口的权威合同以 [`service-contract.md`](service-contract.md) 为准，语种主表见 [`业务语种规范.md`](业务语种规范.md)。`poster_title_image` 可提交语种来自共享语种目录，并以服务端校验为准。

## 3. Prompt Templates

### Method / Path

```http
GET /api/v1/ai-jobs/prompt-templates?job_type=poster_title_image
```

### Purpose

CPP 用该接口获取指定 `job_type` 下的默认提示词模板。调用方可以展示提示词块，并在创建 Job 时按 `prompt_blocks[].key` 回填临时覆盖内容。

### Request Query

| 参数 | 必填 | 说明 |
|---|---:|---|
| `job_type` | 否 | 默认 `poster_title_image`；传入未知 `job_type` 返回 `INVALID_JOB_TYPE` |

### Response

```json
{
  "code": "0",
  "msg": "success",
  "data": {
    "version": "default",
    "job_type": "poster_title_image",
    "name": "Poster Title Image",
    "description": "Default prompt blocks for poster title image generation",
    "prompt_blocks": [
      {
        "key": "style_probe",
        "role": "user",
        "label": "风格探针",
        "default_content": "Analyze the reference title image and describe the visual design style of the letterforms only..."
      },
      {
        "key": "additional_prompt",
        "role": "user",
        "label": "附加提示词",
        "default_content": "Keep the result suitable for a standalone poster title layer..."
      },
      {
        "key": "layout_rules",
        "role": "user",
        "label": "排版规则",
        "default_content": "标题为横向标题区。先评估该语言文案的视觉宽度..."
      }
    ]
  },
  "request_id": "01J...",
  "server_time": "2026-06-24T12:00:00+00:00"
}
```

Rules:

- `job_type` 是唯一查询维度；当前接口不接收 `language`、`model_id`、`group_key` 或 `schema_version` query。
- `prompt_blocks[].key` 是稳定提示词块标识，CPP 可按对应 `job_type` 的任务创建合同回填到 `prompt_overrides`。
- `role`、`label` 和 `default_content` 只描述默认模板展示和覆盖入口；服务端仍会在执行时把模板块、任务参数和固定业务编排拼成最终模型请求。
- 核心系统提示词、输出 schema、透明图处理约束和安全边界不在该接口暴露，也不允许 CPP 覆盖。

## 4. Create Poster Title Image Job

> vNext job contract：本节定义批量 `poster_title_image` 的目标 `job_params`。当前实现是否支持 item 级模型、参考图、输出参数和提示词覆盖，以 [`service-contract.md`](service-contract.md) 与实际 schema 为准。

### Method / Path

```http
POST /api/v1/ai-jobs/jobs
```

### Purpose

创建异步批量标题图生成任务。该接口只表示 AI 服务已接单，不表示图片已经生成完成。

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
          "additional_prompt": "optional item-level additional prompt",
          "layout_rules": "optional item-level layout rule override"
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

Field rules:

| 字段 | 必填 | 说明 |
|---|---:|---|
| `client_request_id` | 是 | CPP requestID；同一 caller 下用于幂等 |
| `job_type` | 是 | 固定为 `poster_title_image` |
| `job_params.items` | 是 | 批量生成 item，至少 1 个，最多由本接口 Job 约束决定 |
| `job_params.items[].item_id` | 是 | 调用方提供的稳定 item 关联键；同一 Job 内唯一，不应由服务端推导 |
| `job_params.items[].language` | 是 | 语种代码，必须来自共享语言列表并符合本接口 Job 约束 |
| `job_params.items[].title_text` | 是 | 目标语言标题文本 |
| `job_params.items[].model_id` | 否 | 标题图生图模型 ID；不传时使用服务端 `poster_title_image` 默认生图模型 |
| `job_params.items[].model_options.size` | 是 | 目标输出尺寸 |
| `job_params.items[].model_options.quality` | 是 | 目标输出质量 |
| `job_params.items[].model_options.draw_count` | 否 | 默认 1，表示该 item 需要返回的标题图片候选数量 |
| `job_params.items[].model_options.background` | 是 | 业务输出背景要求，不是 provider raw 参数 |
| `job_params.items[].model_options.output_format` | 是 | 业务输出格式要求，不是 provider raw 参数 |
| `job_params.items[].reference_image` | 是 | 该 item 的标题样式参考图 OSS input URL ref |
| `job_params.items[].prompt_overrides.style_probe` | 否 | 该 item 的风格探针提示词覆盖 |
| `job_params.items[].prompt_overrides.additional_prompt` | 否 | 该 item 的附加提示词 |
| `job_params.items[].prompt_overrides.layout_rules` | 否 | 该 item 的排版规则提示词覆盖 |
| `callback` | 否 | 终态 Callback 配置；payload 和签名语义沿用 `service-contract.md` |
| `callback.url` | 条件必填 | 传 `callback` 时必填，必须是 HTTPS URL |
| `callback.events` | 否 | 需要通知的终态事件列表 |
| `metadata` | 否 | 调用方透传元数据，AI 服务不按该字段做业务决策 |
| `options.priority` | 否 | 接单优先级；首版默认 `normal` |
| `options.idempotency_mode` | 否 | 幂等命中行为；首版默认 `return_existing` |

Job constraints:

本表是 `poster_title_image` vNext 自身约束。新增地区变体前，必须先进入共享语种目录，不能在本接口单独维护平行语种代码。

| 约束 | 值 |
|---|---:|
| `job_params.items` | 至少 1 个 item；默认最多 50 个，受服务端 `POSTER_TITLE_IMAGE_MAX_ITEMS` 配置限制 |
| `job_params.items[].item_id` | 1 到 64 个字符；同一 Job 内唯一；首字符必须是字母或数字，后续只允许字母、数字、`.`、`_`、`-` |
| `job_params.items[].language` | 语种代码必须来自 [`业务语种规范.md`](业务语种规范.md)；同一 Job 内允许重复 |
| `job_params.items[].title_text` | 1 到 200 个字符 |
| `job_params.items[].model_id` | 可省略；默认值和 allowlist 来自 `app/jobs/types/poster_title_image/models.yaml`；当前默认和 allowlist 均为 `gpt-image-2`；同一 Job 内必须一致 |
| `job_params.items[].model_options.size` | `1024x1024`、`1536x1024`、`1024x1536`、`auto` |
| `job_params.items[].model_options.quality` | `low`、`medium`、`high`、`auto` |
| `job_params.items[].model_options.draw_count` | 1 到 4，且不能超过服务端 `POSTER_TITLE_IMAGE_MAX_DRAW_COUNT` |
| `job_params.items[].model_options.background` | `transparent` |
| `job_params.items[].model_options.output_format` | `png` |
| `job_params.items[].reference_image.public_url` | 必须，HTTPS OSS URL |
| `job_params.items[].reference_image.internal_url` | 必须，HTTPS OSS internal URL |
| `job_params.items[].reference_image.content_type` | 必须，`image/png`、`image/jpeg` 或 `image/webp` |
| `job_params.items[].reference_image.sha256` | 必须，同一个 OSS object 原始内容的小写 64 位 hex SHA-256 |
| 输入图片 MIME | `image/png`、`image/jpeg`、`image/webp` |
| 单个输入图片大小 | 最大 20 MB |
| 单个输入图片尺寸 | 最大 4096 x 4096，且总像素不超过 16777216 |

Batch rules:

- 单语种调用也使用 `items`，只传 1 个 item。
- 每个 item 是独立业务单元，显式声明自己的模型、模型参数、参考图和提示词覆盖；不同 item 可以传入相同 `reference_image`。
- 同一 Job 内 `items[].item_id` 必须唯一，并作为请求 item 与结果 item 的主关联键。
- 同一 Job 内 `items[].language` 允许重复；服务端始终以 `item_id` 关联请求 item 与结果 item。
- 不提供 `batch_options`。首版批量策略固定为 item 独立执行、root Job 最后 join/finalize。
- 服务端按 `reference_image.sha256 + effective style_probe prompt` 复用风格探针结果；这只影响内部执行节点数量，不改变每个 item 的独立结果。
- 所有 item 失败时，Job 进入 `failed`。
- `draw_count` 表示该 item 成功时需要返回的标题图片候选数量。服务端按候选数量多次独立生成，每次只接受 provider 返回 1 张图；`draw_count` 不是 provider raw 参数 `n`。
- 任意一次候选图生成失败，或 provider 单次返回的图片数量不是 1，该 item 都不能标记为 `succeeded`。
- `background` 和 `output_format` 是业务输出要求。服务端可以通过 provider 参数、后处理或对象存储转码实现，但这些内部实现不属于外部合同。
- `background=transparent` 且 `output_format!=png` 时，服务端必须 fail-fast 返回 `INVALID_INPUT`；首版不定义透明 JPEG 或透明 WebP 输出。
- 首版 `output_format` 固定为 `png`，输出 OSS `content_type` 固定为 `image/png`。
Option rules:

- 未传 `callback` 时不发送 Callback。
- `callback.events` 目标合同允许 `job.succeeded`、`job.failed`。
- 传 `callback.url` 但未传 `callback.events` 时，vNext 默认通知 `job.succeeded`、`job.failed`。
- `options.priority` 首版只定义 `normal`。
- `options.idempotency_mode=return_existing` 时，重复 `client_request_id` 返回已有 Job 当前状态。

Forbidden request fields:

- 不传 `chroma_key_color`。
- 不传抠图方式、透明图生成方式或后处理策略。
- 不传 provider raw model name、API key、价格规则或业务线标识。
- 不传 token、图片、视频或音频计费用量。
- 不传外层 `model_id`、`model_options`、`source`、`render_options`、`prompt_overrides` 或 `batch_options`。
- 不传 `items[].layout`；排版由 `title_text`、item 级提示词和服务内部规则共同决定。
- 不传拆分的 `bucket`、`region`、`endpoint`、`object_key` 或临时签名参数；输入参考图只使用 `OSS URL Ref` 字段。

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

Rules:

- 接单响应中的 `cost` 固定为 `null`。
- Job 进入终态后，`GET /jobs/{job_id}` 和终态 Callback 可返回 `cost.final=true` 的 Job 级总费用。
- 需要独立查询费用时，以 [`service-contract.md`](service-contract.md) 中的 `/jobs/{job_id}/billing` 为准。
- `job.cost` 是 Job snapshot 中的总费用快照；Job billing 是费用状态、调用统计和诊断信息的稳定查询入口。

## 5. Poll Poster Title Image Job

### Method / Path

```http
GET /api/v1/ai-jobs/jobs/{job_id}
```

### Processing Response

该响应是增量结果快照：Job 尚未终态，但已经成功生成的 item 图片可以先返回给调用方展示。运行中快照复用终态 `job_result` 结构，只包含已经成功的 item，不返回未开始、运行中或失败的 internal leaf job 状态。

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
                }
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

### Succeeded Response

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
                }
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

Result fields:

| 字段 | 说明 |
|---|---|
| `job_result.schema_version` | 固定为 `default` |
| `job_result.job_type` | 固定为 `poster_title_image` |
| `job_result.batch_summary.total` | 当前 `job_result.items[]` 中的 item 总数；成功终态时等于本 Job 请求 item 总数 |
| `job_result.batch_summary.succeeded` | 成功 item 数 |
| `job_result.batch_summary.failed` | 当前公开结果中的失败 item 数；当前实现固定为 `0` |
| `job_result.batch_summary.running` | 当前公开结果中的运行中 item 数；当前实现固定为 `0` |
| `job_result.batch_summary.pending` | 当前公开结果中的未开始 item 数；当前实现固定为 `0` |
| `job_result.items[].item_id` | 结果 item 主关联键，对应请求中的唯一 `items[].item_id` |
| `job_result.items[].language` | 结果 item 语言，必须与请求中同一 `item_id` 的 `language` 一致 |
| `job_result.items[].status` | 当前实现对外返回 `succeeded` |
| `job_result.items[].images[]` | item 输出标题图片列表 |
| `job_result.items[].images[].object` | 标题图片 OSS URL ref；`content_type` 必须等于请求 item `model_options.output_format` 映射后的 MIME |
| `job_result.items[].error` | item 失败原因；成功时为 `null` |
| `job_result.duration_ms.ai_model` | 已完成内部 AI 节点的 provider 调用耗时累计 |
| `job_result.duration_ms.total` | 已完成内部 AI 节点的服务端执行耗时累计，不包含排队等待时间 |
| `job.cost` | Job 级总费用；非终态为 `null`，终态可返回 `Cost` |

Result rules:

- HTTP `200` 只表示成功查到 Job，不表示 Job 执行成功。
- `poster_title_image` 声明 `result_snapshot_statuses={"running","failed"}`；其它 `job_type` 是否支持非终态或失败态结果快照，以各自合同为准。
- `job_status=queued` 时，`job_result` 必须为 `null`。
- `job_status=running` 或 `job_status=failed` 时可以返回非空 `job_result`，用于展示已经成功生成的 item 图片产物；如果尚未生成首个成功 item，也可以返回 `job_result=null`。
- `job_status=running` 或 `job_status=failed` 且 `job_result` 非空时，`items[]` 只包含 `status=succeeded` 的 item，不返回未开始、运行中或失败的 internal leaf job 状态。
- `job_status=running` 或 `job_status=failed` 的 `job_result.items[]` 必须按请求 `items[]` 顺序返回已成功 item 子集。
- 已经在任一轮询响应中返回过的成功 item，后续响应必须继续返回该 item 及其已产出的 `images`；已公开成功结果对调用方保持单调可见。
- `batch_summary` 必须与 `items[].status` 一致；`total = pending + running + succeeded + failed`。部分结果快照只包含成功 item，因此 `total=succeeded`，`failed=0`、`running=0`、`pending=0`。
- `job_status=succeeded` 时，所有 item 的 `status` 必须为 `succeeded`，且 `running=0`、`pending=0`、`failed=0`。
- `job_status=failed` 时，失败原因在 `job.job_error`；已经成功生成的 item 仍可继续通过 `job_result` 返回。
- `status=succeeded` 的 item 必须返回该请求 item `model_options.draw_count` 个标题图片 OSS object。
- `images[]` 数组顺序是稳定候选顺序；同一 Job 的后续轮询和终态响应不得重排已经公开的图片。
- 如果某个 item 无法产出请求 item `model_options.draw_count` 个标题图片，该 item 不能标记为 `succeeded`；首版不对外暴露部分成功候选图。
- 首版不返回海报底图、合成海报、贴图坐标或图片尺寸元数据。
- 当前实现不在 `job_result` 中返回失败 item；失败终态的 Job 级错误见 `job.job_error`。
- `duration_ms.ai_model` 统计已完成内部 AI 节点的 provider 调用耗时累计；`duration_ms.total` 统计已完成内部 AI 节点的服务端执行耗时累计，不包含排队等待时间。
- token、图片、视频、音频和调用次数等计费明细不在 `job_result` 中返回。
- `job_status=queued` 或 `job_status=running` 时，`job.cost` 必须为 `null`。
- `job_status=succeeded` 或 `job_status=failed` 时，`job.cost` 可返回 `Cost`；如果返回，`cost.final=true`。

## 6. Query Job Billing

### Method / Path

```http
GET /api/v1/ai-jobs/jobs/{job_id}/billing
```

### Purpose

CPP 用该接口查询单个 Job 的费用聚合结果。终态 `GET /jobs/{job_id}` 和终态 Callback 可以返回 `job.cost` 总费用快照；Job billing 用于独立刷新费用、补偿读取或排查费用状态。

### Response

```json
{
  "code": "0",
  "msg": "success",
  "data": {
    "billing": {
      "schema_version": "1",
      "scope_type": "job",
      "scope_id": "018f9a7f-0183-4e4f-938d-1baf7411b4fd",
      "status": "estimated",
      "kind": "cost_estimate",
      "currency": "USD",
      "total_cost_amount": "0.755700",
      "usage_units": {
        "image_count": 12
      },
      "pricing_refs": ["openai:gpt-image-2@2026-06-24"],
      "ai_call_count": 12,
      "billable_call_count": 12,
      "unbillable_call_count": 0,
      "failed_call_count": 0,
      "diagnostic_reason": null,
      "finalized_at": "2026-06-24T12:01:00+00:00"
    }
  },
  "request_id": "01J...",
  "server_time": "2026-06-24T12:01:00+00:00"
}
```

Rules:

- `billing.total_cost_amount` 是该 Job scope 内所有可计费内部调用的总费用。
- `billing.status` 表达费用聚合状态；`estimated` 和 `not_billable` 表示总费用可用，`incomplete` 和 `failed` 表示不能把 `total_cost_amount` 当作最终费用使用。
- Job billing 只允许在 Job 进入 `succeeded` 或 `failed` 后查询。
- Job 终态响应和终态 Callback 中如果返回 `job.cost`，其 `final` 必须是 `true`。
- 已失败的 Job 也可以有最终费用；是否收费由内部调用日志聚合决定。

## 7. Callback

Callback payload、签名和 delivery 语义沿用 [`service-contract.md`](service-contract.md) 的 `CallbackEnvelope`。

Rules:

- Callback payload 顶层 `job` 使用同一套 `JobEnvelope` 字段结构；调用方需要最新增量结果时，应以 `job.status_url` 再查询 `GET /jobs/{job_id}`。
- 终态 Callback payload 可返回 `job.cost`；如果返回，`job.cost.final=true`。
- CPP 收到终态 Callback 后，可以直接读取 payload 顶层 `job.cost`；需要费用状态、调用统计或诊断信息时查询 `GET /jobs/{job_id}/billing`。

## 8. Error Codes

错误 envelope 和计费错误语义沿用 [`service-contract.md`](service-contract.md)。`poster_title_image` 专属错误码由 `app/jobs/types/poster_title_image/errors.py` 声明，并注册到全局 error registry。

| reason | HTTP | 说明 |
|---|---:|---|
| `POSTER_TITLE_IMAGE_REFERENCE_INVALID` | 400 | `reference_image` 不符合业务要求，例如引用格式、内容类型、hash、尺寸、透明背景或图片可解码性无效 |
| `POSTER_TITLE_IMAGE_DRAW_COUNT_EXCEEDS_LIMIT` | 400 | `draw_count` 超过服务端 `POSTER_TITLE_IMAGE_MAX_DRAW_COUNT` |
| `POSTER_TITLE_IMAGE_ALL_ITEMS_FAILED` | 502 | 批量生成没有任何 item 成功 |

模型、OSS、worker、callback 和计费类失败继续使用服务级通用错误码，例如 `MODEL_CALL_FAILED`、`MODEL_OUTPUT_INVALID`、`OSS_FETCH_FAILED`、`OSS_WRITE_FAILED` 和 `JOB_TIMEOUT`。
