# 素材标签体系翻译 Job API

本文面向业务后端，定义 `asset_tag_schema_translation` 的异步 Job 对接合同。该接口用于批量翻译标签组名、标签名和标签定义；AI 服务不维护标签库，不写业务数据库，不决定标签启停。

## 基本信息

| 项 | 内容 |
|---|---|
| 能力名称 | 素材标签体系翻译 |
| `job_type` | `asset_tag_schema_translation` |
| 接口形态 | 异步 Job |
| 提交接口 | `POST /api/v1/ai-jobs/jobs` |
| 查询接口 | `GET /api/v1/ai-jobs/jobs/{job_id}` |
| Callback | 支持，可选 |
| Billing | 支持，可选查询 |
| 状态保存 | AI 服务只保存 Job 执行状态和结果快照；不保存为业务标签事实 |

## 调用流程

```text
业务后端标签库
  -> 导出待翻译标签快照 terms[]
  -> 创建 asset_tag_schema_translation Job
  -> AI 服务异步翻译 name / definition
  -> 业务后端轮询或接收 Callback
  -> 业务后端按 term_id 写回自己的标签库
```

## 公共请求约定

### Headers

| Header | 必填 | 说明 |
|---|---:|---|
| `Content-Type: application/json` | 是 | 请求体使用 JSON |
| `Authorization: Bearer <service-key>` | 是 | 服务访问凭证；本地调试环境如显式关闭鉴权可不传 |
| `X-AI-Service-Caller-ID` | 否 | 调用方标识；不传时使用服务端默认 caller |
| `X-Request-ID` | 否 | 请求追踪 ID；响应会返回同一个 `request_id` |

### 通用响应 Envelope

成功响应：

```json
{
  "code": "0",
  "msg": "success",
  "data": {},
  "request_id": "req_01",
  "server_time": "2026-08-31T10:01:00+00:00"
}
```

失败响应：

```json
{
  "code": "INVALID_JOB_PARAMS",
  "msg": "job_params is invalid",
  "error": {
    "code": "INVALID_JOB_PARAMS",
    "message": "job_params is invalid",
    "details": {}
  },
  "request_id": "req_01",
  "server_time": "2026-08-31T10:01:00+00:00"
}
```

### Job 状态

| 状态 | 是否终态 | 说明 |
|---|---:|---|
| `queued` | 否 | Job 已创建，等待 worker 执行 |
| `running` | 否 | Job 正在执行 |
| `succeeded` | 是 | Job 已产出结果矩阵；业务方仍需读取术语级状态 |
| `failed` | 是 | Job 批级失败，或所有术语均失败 |

当前 `asset_tag_schema_translation` 不承诺在 `running` 或 `failed` 状态返回 `job_result` 快照。业务方只应以 `job_status` 判断 Job 是否终态。

## 创建 Job

### Request

```http
POST /api/v1/ai-jobs/jobs
```

```json
{
  "client_request_id": "asset-tags-translation-20260831-zh-to-en",
  "job_type": "asset_tag_schema_translation",
  "job_params": {
    "source_language": "zh",
    "target_languages": ["en"],
    "terms": [
      {
        "term_id": "group_hair_color",
        "term_type": "tag_group",
        "source_name": "发色",
        "source_definition": "头发主体颜色维度",
        "parent_term_id": null,
        "metadata": {
          "asset_type": "hair"
        }
      },
      {
        "term_id": "label_hair_color_brown",
        "term_type": "tag",
        "source_name": "棕色",
        "source_definition": "头发主体颜色为棕色或棕褐色",
        "parent_term_id": "group_hair_color",
        "metadata": {
          "asset_type": "hair"
        }
      }
    ]
  },
  "callback": {
    "url": "https://backend.example.com/ai-callbacks",
    "events": ["job.succeeded", "job.failed"]
  },
  "metadata": {
    "source_service": "asset-backend",
    "taxonomy_batch_id": "asset-tags-v1"
  },
  "options": {
    "priority": "normal",
    "idempotency_mode": "return_existing"
  }
}
```

### Top-Level Fields

| 字段 | 类型 | 必填 | 说明 |
|---|---:|---:|---|
| `client_request_id` | string | 是 | 调用方幂等键；同一调用方下应唯一 |
| `job_type` | string | 是 | 固定为 `asset_tag_schema_translation` |
| `job_params` | object | 是 | 标签翻译参数 |
| `callback` | object 或 null | 否 | 终态 Callback 配置 |
| `metadata` | object | 否 | 调用方透传元数据；不参与模型输入和幂等指纹 |
| `options` | object | 否 | Job 选项 |

### Job Params Fields

| 字段 | 类型 | 必填 | 说明 |
|---|---:|---:|---|
| `source_language` | string | 是 | 源语种，例如 `zh`、`en` |
| `target_languages` | string[] | 是 | 目标语种列表，至少 1 个 |
| `terms` | object[] | 是 | 待翻译标签术语列表，至少 1 条 |

### Term Fields

| 字段 | 类型 | 必填 | 说明 |
|---|---:|---:|---|
| `term_id` | string | 是 | 业务方稳定 ID；标签行建议直接使用后端 `label_id`；标签组行使用后端标签组 ID |
| `term_type` | string | 是 | `tag_group` 或 `tag` |
| `source_name` | string | 是 | 源语种名称 |
| `source_definition` | string 或 null | 否 | 源语种定义；标签建议必填 |
| `parent_term_id` | string 或 null | 否 | 标签所属标签组 ID；`term_type=tag` 时建议传入 |
| `metadata` | object | 否 | 透传字段，例如资源类型、原始 Sheet 名、原始行号 |

### Callback Fields

| 字段 | 类型 | 必填 | 说明 |
|---|---:|---:|---|
| `url` | string | 是 | 业务方接收 Callback 的 HTTPS 地址 |
| `events` | string[] 或 null | 否 | 支持 `job.succeeded`、`job.failed`；省略时默认订阅两种终态事件，建议业务方显式传入 |

Callback 签名密钥由业务方和 AI 服务部署配置预先约定，不在创建 Job 请求中传递。

### Options Fields

| 字段 | 类型 | 必填 | 说明 |
|---|---:|---:|---|
| `priority` | string | 否 | `low` 或 `normal` |
| `idempotency_mode` | string | 否 | 建议 `return_existing`；同一幂等键重复提交时返回已有 Job |

## 创建响应

```json
{
  "code": "0",
  "msg": "success",
  "data": {
    "job": {
      "job_id": "018f9a7f-2b7d-7a0a-9f24-6ff0b87c8b91",
      "client_request_id": "asset-tags-translation-20260831-zh-to-en",
      "job_type": "asset_tag_schema_translation",
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
      "status_url": "/api/v1/ai-jobs/jobs/018f9a7f-2b7d-7a0a-9f24-6ff0b87c8b91",
      "created_at": "2026-08-31T10:00:00+00:00",
      "updated_at": "2026-08-31T10:00:00+00:00",
      "finished_at": null
    }
  },
  "request_id": "req_01",
  "server_time": "2026-08-31T10:00:00+00:00"
}
```

## 查询 Job

### Request

```http
GET /api/v1/ai-jobs/jobs/{job_id}
```

### Succeeded Response

```json
{
  "code": "0",
  "msg": "success",
  "data": {
    "job": {
      "job_id": "018f9a7f-2b7d-7a0a-9f24-6ff0b87c8b91",
      "client_request_id": "asset-tags-translation-20260831-zh-to-en",
      "job_type": "asset_tag_schema_translation",
      "job_status": "succeeded",
      "job_progress": {
        "percent": 100
      },
      "job_result": {
        "schema_version": "default",
        "job_type": "asset_tag_schema_translation",
        "source_language": "zh",
        "target_languages": ["en"],
        "batch_summary": {
          "total": 2,
          "succeeded": 2,
          "failed": 0
        },
        "items": [
          {
            "term_id": "group_hair_color",
            "term_type": "tag_group",
            "status": "succeeded",
            "translations": {
              "en": {
                "name": "Hair Color",
                "definition": "The main hair color dimension"
              }
            },
            "error": null
          },
          {
            "term_id": "label_hair_color_brown",
            "term_type": "tag",
            "status": "succeeded",
            "translations": {
              "en": {
                "name": "Brown",
                "definition": "The main hair color is brown or brownish"
              }
            },
            "error": null
          }
        ]
      },
      "job_error": null,
      "cost": null,
      "usage": null,
      "callback": {
        "status": "delivered",
        "attempt": 1,
        "last_error": null,
        "next_retry_at": null
      },
      "status_url": "/api/v1/ai-jobs/jobs/018f9a7f-2b7d-7a0a-9f24-6ff0b87c8b91",
      "created_at": "2026-08-31T10:00:00+00:00",
      "updated_at": "2026-08-31T10:01:00+00:00",
      "finished_at": "2026-08-31T10:01:00+00:00"
    }
  },
  "request_id": "req_02",
  "server_time": "2026-08-31T10:01:00+00:00"
}
```

### Failed Response

```json
{
  "code": "0",
  "msg": "success",
  "data": {
    "job": {
      "job_id": "018f9a7f-2b7d-7a0a-9f24-6ff0b87c8b91",
      "client_request_id": "asset-tags-translation-20260831-zh-to-en",
      "job_type": "asset_tag_schema_translation",
      "job_status": "failed",
      "job_progress": {
        "percent": 100
      },
      "job_result": null,
      "job_error": {
        "reason": "ASSET_TAG_SCHEMA_TRANSLATION_ALL_ITEMS_FAILED",
        "details": {
          "total": 2,
          "failed": 2
        },
        "retryable": false
      },
      "cost": null,
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
      "status_url": "/api/v1/ai-jobs/jobs/018f9a7f-2b7d-7a0a-9f24-6ff0b87c8b91",
      "created_at": "2026-08-31T10:00:00+00:00",
      "updated_at": "2026-08-31T10:01:00+00:00",
      "finished_at": "2026-08-31T10:01:00+00:00"
    }
  },
  "request_id": "req_03",
  "server_time": "2026-08-31T10:01:00+00:00"
}
```

### Job Result Fields

| 字段 | 类型 | 说明 |
|---|---:|---|
| `schema_version` | string | 结果 schema 版本，首版为 `default` |
| `job_type` | string | 固定为 `asset_tag_schema_translation` |
| `source_language` | string | 源语种 |
| `target_languages` | string[] | 目标语种列表 |
| `batch_summary.total` | integer | 请求 `terms[]` 总数 |
| `batch_summary.succeeded` | integer | 翻译成功 item 数 |
| `batch_summary.failed` | integer | 翻译失败 item 数 |
| `items[]` | object[] | 翻译结果，按请求 `terms[]` 顺序返回 |
| `items[].term_id` | string | 对应请求 `terms[].term_id` |
| `items[].term_type` | string | 对应请求 `terms[].term_type` |
| `items[].status` | string | `succeeded` 或 `failed` |
| `items[].translations` | object | key 为目标语种；value 为翻译结果 |
| `translations.{lang}.name` | string | 翻译后的名称 |
| `translations.{lang}.definition` | string 或 null | 翻译后的定义 |
| `items[].error` | object 或 null | 术语级失败原因；成功时为 `null` |

### Job Error Fields

| 字段 | 类型 | 说明 |
|---|---:|---|
| `job_error.reason` | string | Job 级失败原因 |
| `job_error.details` | object | 结构化诊断信息 |
| `job_error.retryable` | boolean | 是否建议业务方稍后重试 |

## 查询费用

### Request

```http
GET /api/v1/ai-jobs/jobs/{job_id}/billing
```

### Response

```json
{
  "code": "0",
  "msg": "success",
  "data": {
    "billing": {
      "schema_version": "default",
      "scope_type": "job",
      "scope_id": "018f9a7f-2b7d-7a0a-9f24-6ff0b87c8b91",
      "status": "estimated",
      "kind": "cost_estimate",
      "currency": "USD",
      "total_cost_amount": "0.000123",
      "usage_units": {},
      "pricing_refs": [],
      "ai_call_count": 1,
      "billable_call_count": 1,
      "unbillable_call_count": 0,
      "failed_call_count": 0,
      "diagnostic_reason": null,
      "finalized_at": "2026-08-31T10:01:00+00:00"
    }
  },
  "request_id": "req_03",
  "server_time": "2026-08-31T10:01:05+00:00"
}
```

| 字段 | 类型 | 说明 |
|---|---:|---|
| `billing.status` | string | `estimated`、`not_billable`、`incomplete` 或 `failed` |
| `billing.kind` | string | 当前为 `cost_estimate` |
| `billing.currency` | string 或 null | 币种 |
| `billing.total_cost_amount` | string 或 null | 费用金额，使用字符串避免浮点误差 |
| `billing.ai_call_count` | integer | Job 关联的 AI 调用次数 |
| `billing.finalized_at` | string 或 null | 费用快照最终完成时间 |

## Callback

创建 Job 时传入 `callback.url` 后，AI 服务会在终态投递 Callback。Callback 的 `job` 字段必须是完整 Job snapshot，结构与查询接口返回的 `data.job` 一致。

### Callback Headers

| Header | 说明 |
|---|---|
| `Content-Type` | 固定为 `application/json` |
| `X-Callback-Timestamp` | 签名时间戳，Unix 秒 |
| `X-Callback-Signature` | HMAC-SHA256 签名，格式为 `sha256=<hex>` |

签名原文为：

```text
<X-Callback-Timestamp>.<raw_request_body>
```

业务方使用双方预先约定的 Callback 签名密钥对签名原文计算 HMAC-SHA256 后，与 `X-Callback-Signature` 做恒定时间比较。

`event_id` 是 Callback 事件幂等键，业务方应按它去重。同一 `event_id` 重复投递但已成功处理时，业务方仍应返回 `accepted=true`。

### Callback Body

```json
{
  "event": "job.succeeded",
  "event_id": "018f9a7f-93f0-7dd4-8d2a-64c509158ec1",
  "attempt": 1,
  "sent_at": "2026-08-31T10:01:01+00:00",
  "trigger_request_id": "req_02",
  "caller_id": "asset-backend",
  "job": {
    "job_id": "018f9a7f-2b7d-7a0a-9f24-6ff0b87c8b91",
    "client_request_id": "asset-tags-translation-20260831-zh-to-en",
    "job_type": "asset_tag_schema_translation",
    "job_status": "succeeded",
    "job_progress": {
      "percent": 100
    },
    "job_result": {
      "schema_version": "default",
      "job_type": "asset_tag_schema_translation",
      "source_language": "zh",
      "target_languages": ["en"],
      "batch_summary": {
        "total": 1,
        "succeeded": 1,
        "failed": 0
      },
      "items": [
        {
          "term_id": "label_hair_color_brown",
          "term_type": "tag",
          "status": "succeeded",
          "translations": {
            "en": {
              "name": "Brown",
              "definition": "The main hair color is brown or brownish"
            }
          },
          "error": null
        }
      ]
    },
    "job_error": null,
    "cost": null,
    "usage": {
      "ai_call_count": 1,
      "total_tokens": null,
      "final": true
    },
    "callback": {
      "status": "delivered",
      "attempt": 1,
      "last_error": null,
      "next_retry_at": null
    },
    "status_url": "/api/v1/ai-jobs/jobs/018f9a7f-2b7d-7a0a-9f24-6ff0b87c8b91",
    "created_at": "2026-08-31T10:00:00+00:00",
    "updated_at": "2026-08-31T10:01:00+00:00",
    "finished_at": "2026-08-31T10:01:00+00:00"
  }
}
```

业务方接收成功必须返回 HTTP `2xx`、`Content-Type: application/json` 和以下 JSON body：

```json
{
  "accepted": true
}
```

`204`、空 body、非 JSON body、缺少 `accepted`、`accepted` 不是 boolean，或 `accepted=false` 都会被视为未接受。Callback 投递失败不会改变 Job 终态。业务方必须保留轮询能力。

## 处理规则

- `terms[].term_id` 是业务方和 AI 服务之间唯一稳定对齐键，AI 服务不得新增、删除、替换或翻译该字段。
- `term_type=tag` 时，建议 `source_definition` 必填；只翻译标签名容易造成多义词误差。
- 单个 Job 应只包含一种 `source_language`；混合源语种应拆成多个 Job。
- `target_languages[]` 至少 1 个，且不能包含 `source_language`。
- `metadata` 只用于透传和排查，不参与模型输入合同。
- 翻译结果只是候选产物，是否写回标签库由业务后端决定。

## 错误码

| 错误码 | HTTP 状态 | 场景 | 说明 |
|---|---:|---|---|
| `INVALID_JOB_TYPE` | 400 | `job_type` 不支持 | 不创建 Job |
| `INVALID_JOB_PARAMS` | 400 | `job_params` 结构非法 | 例如 `terms[]` 为空、`term_id` 重复、语种非法 |
| `CLIENT_REQUEST_ID_CONFLICT` | 409 | 幂等键冲突 | 同一 `client_request_id` 对应不同请求指纹 |
| `MODEL_CALL_FAILED` | 502 | 模型调用失败 | provider 错误、限流或超时 |
| `MODEL_OUTPUT_INVALID` | 500 | 模型输出不符合合同 | JSON 非法、漏项、改写 `term_id`、语种缺失 |
| `ASSET_TAG_SCHEMA_TRANSLATION_ALL_ITEMS_FAILED` | 500 | 所有术语翻译失败 | Job 级失败 |
