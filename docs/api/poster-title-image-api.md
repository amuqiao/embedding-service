# AI Poster Title Image API

本文定义 CPP 美术任务接入 AI 标题图生成时需要确认的 vNext 目标接口合同。

## Status

- 合同状态：draft for implementation。
- 当前优先级：本文不覆盖 [`service-contract.md`](service-contract.md) 中已经实现的共享服务合同。
- 发布要求：正式对外发布前，必须同步升级 route、schema、contract tests 和 `service-contract.md`。
- vNext shared-contract 提案：本文中的 `/jobs/{job_id}/cost`、`Cost`、`running` 状态可返回非空 `job_result` 和共享 `job_progress` envelope 都尚未覆盖当前实现合同。
- 服务前缀、认证、HTTP envelope 和通用错误语义以 [`service-contract.md`](service-contract.md) 为准。

## Scope

本文覆盖 CPP 本期需要的业务接入面：

1. 模型列表：返回厂商和模型基础信息。
2. 语言列表：返回基础语言目录。
3. 默认提示词：按 `job_type`、语言、模型和分组返回默认提示词。
4. 生成图片：通过异步 Job 创建批量标题图生成任务，并通过轮询获取增量结果和终态，通过 Callback 获取终态通知和总费用。
5. 费用查询：额外查询 Job 级总费用 `Cost`，不返回 token、图片、视频或音频分项账单。

本文不暴露 provider 密钥、内部价格矩阵、绿底抠图策略、透明背景兼容细节、OSS 转存实现、worker 编排细节或 provider raw response。

## Key Rules

- `model_options.background` 只表达业务输出目标，例如 `transparent`；本接口不向 CPP 暴露 `chroma_key_color`、抠图方式或后处理参数。AI 服务内部负责生成可用的透明标题 PNG 或可合成产物。
- 输入参考图和输出图片都使用 OSS URL ref；不直接传 base64、本地路径或临时签名 URL。
- CPP 临时修改的提示词只对本次 Job 生效，不写回默认提示词模板。
- `client_request_id` 使用 CPP 的 `requestID`，作为同一 caller 下的提交幂等键。
- 轮询是主链路，Callback 是终态通知。终态 Job 查询和 Callback 都返回 Job 级总费用 `Cost`；Callback 失败不改变 Job 终态。
- 一个 `poster_title_image` Job 可以包含多个语言 item；每个 item 有独立参考图、模型参数和提示词覆盖。
- 对外只返回 Job 级总费用 `Cost`：`currency`、`amount`、`final`。不返回 `tokens`、`images`、`videos`、`audio` 或其它分项账单。
- 内部可以在 `aigc_api_logs`、AI call ledger 或等价日志中保留每次调用的 `provider`、`model`、`operation`、`usage_detail` 和成本明细；这些明细不属于外部 API 合同。

## Shared Types

### OSS URL Ref

CPP 传入参考图、AI 服务返回生成图片时都使用 `OSS URL Ref`。AI 服务读取输入时优先使用 `internal_url`；调用方读取输出时可以使用 `public_url` 或按自身网络选择 `internal_url`。

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
| `public_url` | string | 是 | 公网 HTTPS OSS URL |
| `internal_url` | string | 是 | 内网 HTTPS OSS internal URL |
| `content_type` | string | 是 | MIME type，例如 `image/png`、`image/jpeg`、`image/webp` |
| `sha256` | string | 是 | 同一个 OSS object 原始内容的小写 64 位 hex SHA-256，不带 `sha256:` 前缀 |

OSS 责任边界：

- URL 必须使用 `https`，不允许任何 query string 或 fragment，也不允许携带访问密钥或临时签名参数。
- `public_url` 和 `internal_url` 必须指向同一个 OSS object；如果 bucket、object path 或等价对象身份不一致，服务返回 `INVALID_INPUT`。
- URL host 必须命中服务端配置的 OSS allowlist；不允许把该字段作为任意 URL 下载入口。
- AI 服务读取输入对象时优先使用 `internal_url`。如果 `internal_url` 不可访问，本 item 失败；服务不自动改用 `public_url` 作为静默兜底。
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
- API 不返回 token、图片、音频、视频、调用次数、provider、model、operation 或 pricing ref 明细。
- `job.cost` 只在终态 Job snapshot 中返回，且必须是 `final=true`；非终态 Job snapshot 的 `job.cost` 必须为 `null`。
- `final=false` 只允许出现在独立 `GET /jobs/{job_id}/cost` 查询中，表示 Job 尚未终态或内部日志尚未聚合完成。
- 对外发布终态 Job snapshot 前，AI 服务必须先完成费用聚合。
- 如果所有 item 已完成但费用尚未聚合完成，对外 `job_status` 仍保持 `running`，`job_result` 可以展示全部 item 终态结果，`job.cost` 必须为 `null`。
- 当费用计算失败且无法形成可信总数时，返回统一错误响应，不返回伪造的 0 成本成功。

### Job Progress

> vNext shared-contract proposal：当前共享合同尚未定义这套 `job_progress` envelope。落地前必须同步升级 [`service-contract.md`](service-contract.md)、schema 和 contract tests。

`job_status` 是唯一程序状态。`job_progress` 只表达展示进度，不表达状态机或业务步骤。

```json
{
  "percent": 55
}
```

字段规则：

| 字段 | 类型 | 必填 | 说明 |
|---|---:|---:|---|
| `percent` | integer | 是 | 0 到 100 的整数 |

Rules:

- 调用方只能使用 `job_status` 判断排队、运行、成功或失败。
- `percent` 是近似进度，不表达计费比例或 item 完成比例。
- `percent` 只用于 UI 展示，调用方不能用它判断成功、失败或是否可取结果。
- 终态 Job 的 `percent` 必须是 `100`。
- 非终态 Job 的 `percent` 必须是 `0` 到 `99`。
- 非终态 `percent` 可以不精确，但同一次 Job attempt 内不应倒退。
- 业务内部步骤可以写入服务日志或内部 trace，不进入通用 progress 合同。

## 1. Shared Model Catalog

CPP 可以调用服务级基础模型目录 `GET /api/v1/ai-jobs/models` 渲染模型名称。该接口只返回模型基础信息，不接收 `job_type`，不返回 `poster_title_image` 业务字段。

本文不定义 `/models` 的响应结构；该接口的权威合同以 [`service-contract.md`](service-contract.md) 为准。`poster_title_image` 是否允许使用某个 `model_id`，只由本文 `POST /jobs` 的 Job constraints 和服务端校验决定。

## 2. Shared Language Catalog

CPP 可以调用服务级基础语言目录 `GET /api/v1/ai-jobs/languages` 渲染语言名称。该接口只返回语言基础信息，不接收 `job_type`，不返回 `poster_title_image` 业务字段。

本文不重复定义 `/languages` 的完整响应结构；该接口的权威合同以 [`service-contract.md`](service-contract.md) 为准，语种主表见 [`业务语种规范.md`](业务语种规范.md)。`poster_title_image` 可用语言是共享语种目录与本文 `POST /jobs` Job constraints 的交集，并以服务端校验为准。

## 3. Prompt Templates

> vNext route contract：当前实现是否已支持按 `job_type`、`language`、`model_id` 和分组返回提示词，以 [`service-contract.md`](service-contract.md) 为准。

### Method / Path

```http
GET /api/v1/ai-jobs/prompt-templates?job_type=poster_title_image&language=es&model_id=gpt-image-2
```

### Purpose

CPP 用该接口获取指定 `job_type` 下的默认提示词。提示词按组返回，CPP 可以展示并允许用户临时编辑业务层提示词。

### Request Query

| 参数 | 必填 | 说明 |
|---|---:|---|
| `job_type` | 是 | 固定为 `poster_title_image` |
| `language` | 否 | 语言特化提示词；不传时返回通用默认提示词 |
| `model_id` | 否 | 模型特化提示词；不传时返回该 `job_type` 的默认模型提示词 |
| `schema_version` | 否 | 提示词 schema 版本；不传时默认为 `default` |

### Response

```json
{
  "code": "0",
  "msg": "success",
  "data": {
    "schema_version": "default",
    "job_type": "poster_title_image",
    "language": "es",
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
            "prompt_ref": "poster_title_image.layout_rules.es.v1",
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

Rules:

- `job_type` 是主索引，必须传。
- `schema_version` 默认值为 `default`；同一接口不再同时返回 `prompt_set_version`。
- `language` 和 `model_id` 是可选过滤条件。只有确实存在语言或模型差异时，AI 服务才需要维护对应特化提示词。
- `group_key` 和 `prompt_key` 是稳定合同，CPP 可按它们回填临时修改。
- `prompt_ref` 是 AI 服务返回的引用，调用方不应拼接或推导。
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
| `job_params.items[].model_id` | 是 | 来自模型列表接口，且必须符合本接口 Job 约束 |
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
| `job_params.items` | 1 到 20 个 item |
| `job_params.items[].item_id` | 1 到 64 个字符；同一 Job 内唯一 |
| `job_params.items[].language` | `ja`、`ko`、`ar`、`th`、`ru`、`fr`、`de`、`es`、`pt`、`pl`；首版同一 Job 内唯一 |
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

Batch rules:

- 单语种调用也使用 `items`，只传 1 个 item。
- 每个 item 是独立业务单元，包含自己的模型、模型参数、参考图和提示词覆盖。
- 同一 Job 内 `items[].item_id` 必须唯一，并作为请求 item 与结果 item 的主关联键。
- 首版同一 Job 内 `items[].language` 也必须唯一；如果未来允许同一语言多版本，仍以 `item_id` 关联结果。
- 不提供 `batch_options`。首版批量策略固定为 item 独立执行、root Job 最后 join/finalize。
- item 之间不共享 `reference_image`、`model_options` 或 `prompt_overrides`。
- 所有 item 失败时，Job 进入 `failed`。
- `draw_count` 表示该 item 成功时需要返回的标题图片候选数量。
- `background` 和 `output_format` 是业务输出要求。服务端可以通过 provider 参数、后处理或对象存储转码实现，但这些内部实现不属于外部合同。
- `background=transparent` 且 `output_format!=png` 时，服务端必须 fail-fast 返回 `INVALID_INPUT`；首版不定义透明 JPEG 或透明 WebP 输出。
- `output_format` 到输出 OSS `content_type` 的映射固定为：`png -> image/png`、`webp -> image/webp`、`jpeg -> image/jpeg`。
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
- Job 进入终态后，`GET /jobs/{job_id}` 和终态 Callback 都必须返回 `cost.final=true` 的 Job 级总费用。
- `GET /jobs/{job_id}/cost` 是额外查询接口，不是调用方获取终态费用的必经路径。当前实现仍可能只支持 [`service-contract.md`](service-contract.md) 中的 `/jobs/{job_id}/billing`。
- CPP 接入 vNext 合同前，以 [`service-contract.md`](service-contract.md) 中的 `/billing` 为准；接入 vNext 合同后，以本文 `job.cost` 和 `/cost` 为准。
- 同一个 CPP 集成环境不应同时混读旧 `/billing` 和新 `Cost` 合同；如果服务端迁移期同时暴露两套接口，新 `Cost` 是 CPP 的权威费用合同。

## 5. Poll Poster Title Image Job

> vNext job result contract：本节引入 `running` 状态可返回非空 `job_result` 和批量 result item。当前共享合同仍以 [`service-contract.md`](service-contract.md) 为准；正式发布本节行为前，必须同步升级 `JobEnvelope` schema、Job 状态枚举、callback event schema 和 contract tests。

### Method / Path

```http
GET /api/v1/ai-jobs/jobs/{job_id}
```

### Processing Response

该响应是 vNext 增量结果快照：Job 尚未终态，但已完成 item 的图片可以先返回给调用方展示。

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
            "item_id": "es",
            "language": "es",
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
            "item_id": "pt",
            "language": "pt",
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

Result fields:

| 字段 | 说明 |
|---|---|
| `job_result.schema_version` | 固定为 `default` |
| `job_result.job_type` | 固定为 `poster_title_image` |
| `job_result.batch_summary.total` | 本 Job 请求 item 总数 |
| `job_result.batch_summary.succeeded` | 成功 item 数 |
| `job_result.batch_summary.failed` | 失败 item 数 |
| `job_result.batch_summary.running` | 正在执行的 item 数 |
| `job_result.batch_summary.pending` | 尚未开始执行的 item 数 |
| `job_result.items[].item_id` | 结果 item 主关联键，对应请求中的唯一 `items[].item_id` |
| `job_result.items[].language` | 结果 item 语言，必须与请求中同一 `item_id` 的 `language` 一致 |
| `job_result.items[].status` | `pending`、`running`、`succeeded` 或 `failed` |
| `job_result.items[].images[]` | item 输出标题图片列表 |
| `job_result.items[].images[].object` | 标题图片 OSS URL ref；`content_type` 必须等于请求 item `model_options.output_format` 映射后的 MIME |
| `job_result.items[].error` | item 失败原因；成功时为 `null` |
| `job_result.duration_ms.ai_model` | AI provider 调用耗时 |
| `job_result.duration_ms.total` | Job 总耗时 |
| `job.cost` | Job 级总费用；非终态为 `null`，终态必须为 `Cost` |

Result rules:

- HTTP `200` 只表示成功查到 Job，不表示 Job 执行成功。
- `job_status=queued` 时，`job_result` 必须为 `null`。
- `job_status=running` 时可以返回非空 `job_result`，用于展示已完成 item 的图片产物和未完成 item 的状态；如果尚未生成首个 item 快照，也可以返回 `job_result=null`。
- `job_result` 一旦非空，必须包含本次请求的全部 `items[]`，未完成的 item 使用 `pending` 或 `running` 表达，不允许只返回已完成 item 子集。
- `job_result.items[]` 必须按请求 `items[]` 顺序返回，且每个结果 item 的 `item_id` 必须与请求 item 一一对应。
- item 对外状态只允许按 `pending -> running -> succeeded|failed` 推进；`succeeded` 和 `failed` 是 item 级终态，一旦公开给调用方，后续响应不得改回其他状态。
- 服务端仍可能内部重试的 item，不应提前对外标记为 `failed`，应继续保持 `pending` 或 `running`。
- 已经在任一轮询响应中返回过 `status=succeeded` 的 item，后续响应必须继续返回该 item 及其已产出的 `images`；已公开成功结果对调用方保持单调可见。
- `batch_summary` 必须与 `items[].status` 一致；`total = pending + running + succeeded + failed`。
- `batch_summary.succeeded` 必须等于 `status=succeeded` 的 item 数，`failed`、`running`、`pending` 同理。
- `job_status=running` 且 `job_result` 非空时，允许两种快照：
  - 至少一个 item 仍为 `running` 或 `pending`，表示图片生成仍在执行。
  - 所有 item 都已经是 `succeeded` 或 `failed`，但 `job.cost=null`，表示图片生成已完成、费用仍在聚合。
- `job_status=succeeded` 时，所有 item 的 `status` 必须为 `succeeded`，且 `running=0`、`pending=0`、`failed=0`。
- `job_status=failed` 表示没有任何 item 成功；如果已经形成 item 快照，应返回非空 `job_result`，且所有 item 的 `status` 必须为 `failed`，`succeeded=0`、`running=0`、`pending=0`。
- Job 在形成首个 item 快照前失败时，`job_status=failed` 可以返回 `job_result=null`，失败原因在 `job.job_error`。
- `status=pending` 或 `status=running` 的 item 必须返回空 `images`、`error=null`。
- `status=succeeded` 的 item 必须返回该请求 item `model_options.draw_count` 个标题图片 OSS object。
- `images[]` 数组顺序是稳定候选顺序；同一 Job 的后续轮询和终态响应不得重排已经公开的图片。
- 如果某个 item 无法产出请求 item `model_options.draw_count` 个标题图片，该 item 不能标记为 `succeeded`。
- 首版不返回海报底图、合成海报、贴图坐标或图片尺寸元数据。
- `status=failed` 的 item 必须返回空 `images` 和非空 `error`。
- `duration_ms.ai_model` 只统计 AI provider 调用耗时；`duration_ms.total` 统计 Job 总耗时。
- token、图片、视频、音频和调用次数等计费明细不在 `job_result` 中返回。
- `job_status=queued` 或 `job_status=running` 时，`job.cost` 必须为 `null`。
- `job_status=succeeded` 或 `job_status=failed` 时，`job.cost` 必须为 `Cost`，且 `cost.final=true`。

## 6. Query Job Cost

> vNext billing proposal：本节定义面向 CPP 的额外费用查询接口。正式发布前必须同步升级 [`service-contract.md`](service-contract.md)、schema、route 和 contract tests；在此之前，调用方不能把 `/jobs/{job_id}/cost` 视为当前已实现接口。

### Method / Path

```http
GET /api/v1/ai-jobs/jobs/{job_id}/cost
```

### Purpose

CPP 用该接口额外查询单个 Job 的总费用。终态 `GET /jobs/{job_id}` 和终态 Callback 已经返回 `job.cost`；该接口用于调用方需要独立刷新费用、补偿读取或排查费用状态时使用。

### Response

```json
{
  "code": "0",
  "msg": "success",
  "data": {
    "cost": {
      "currency": "USD",
      "amount": "0.755700",
      "final": true
    }
  },
  "request_id": "01J...",
  "server_time": "2026-06-24T12:01:00+00:00"
}
```

Rules:

- `amount` 是该 Job 内所有可计费内部调用的总费用。
- 对外不返回 `tokens`、`images`、`videos`、`audio`、`usage_units`、`pricing_refs`、`ai_call_count`、`provider`、`model` 或 `operation`。
- `final=true` 表示内部调用日志已经聚合完毕，CPP 可以展示为最终费用。
- `final=false` 表示 Job 或费用聚合还未完成，CPP 应继续轮询 Job 或 Cost。
- Job 终态响应和终态 Callback 中的 `job.cost.final` 必须是 `true`。
- 已失败的 Job 也可以有最终费用；是否收费由内部调用日志聚合决定。
- CPP 接入 vNext 合同后，应以 `job.cost` 和本接口返回的 `Cost` 为费用权威；旧 `/billing` 如果仍存在，只作为兼容旧调用方的接口，不作为 CPP 新接入面的读取来源。

## 7. Callback

Callback payload、签名和 delivery 语义沿用 [`service-contract.md`](service-contract.md) 的 `CallbackEnvelope`。

Rules:

- Callback payload 顶层 `job` 必须是完整 `JobEnvelope`，字段结构与 `GET /jobs/{job_id}` 中的 `data.job` 一致。
- 终态 Callback payload 的 `job.cost` 必须存在，且 `job.cost.final=true`。
- CPP 收到终态 Callback 后，可以直接读取 payload 顶层 `job.cost`；`GET /jobs/{job_id}/cost` 只是额外查询接口。

## 8. Error Codes

错误 envelope、通用错误码和计费错误语义沿用 [`service-contract.md`](service-contract.md)。本文不新增 `poster_title_image` 专属错误码。
