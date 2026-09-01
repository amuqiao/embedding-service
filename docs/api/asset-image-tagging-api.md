# 图片素材 AI 打标 Job API

本文面向业务后端，定义 `asset_image_tagging` 的异步 Job 对接合同。该接口用于批量给图片素材生成候选标签和素材描述；AI 服务不维护素材库，不维护标签库，不写业务数据库。

## 基本信息

| 项 | 内容 |
|---|---|
| 能力名称 | 图片素材 AI 打标 |
| `job_type` | `asset_image_tagging` |
| 接口形态 | 异步 Job |
| 提交接口 | `POST /api/v1/ai-jobs/jobs` |
| 查询接口 | `GET /api/v1/ai-jobs/jobs/{job_id}` |
| Callback | 支持，可选 |
| Billing | 支持，可选查询 |
| 批量形态 | `job_params.items[]` 一次提交多个素材；单素材是批量特例 |
| 状态保存 | AI 服务只保存 Job 执行状态和结果快照；不保存为业务标签事实 |

## 调用流程

```text
业务后端素材库 + 业务后端标签库
  -> 业务后端组装 items[] + label_snapshot[] + rules
  -> 创建 asset_image_tagging Job
  -> AI 服务读取 items[].asset 指向的图片资源
  -> AI 服务按 items[].category_id 匹配 label_snapshot[] 中对应分类的标签组
  -> AI 服务内部把 label_id 转为临时标签编号
  -> 模型只基于临时编号 / 标签名 / 标签定义选择标签
  -> AI 服务把临时编号映射回 label_id，并按 single / multiple 校验选择结果
  -> 业务后端轮询或接收 Callback
  -> 业务后端审核、采信或写库
```

## 标签选择模型

图片打标的核心输入是“素材 + 标签组快照”。`label_snapshot[]` 是业务方为本次打标准备的标签组列表，每个标签组声明自己适用的素材分类。模型只能从当前素材分类匹配到的标签组里选择标签，不能创建新标签。

```text
items[]
  hair_001
    item_name=棕色中长卷发
    category_id=hair
    category_name=发型
    asset.public_url=https://...
    asset.content_type=image/png

label_snapshot[]
  第 1 组
    category_id=hair
    category_name=发型
    selection_mode=single
    labels: brown / black / blonde
  第 2 组
    category_id=hair
    category_name=发型
    selection_mode=multiple
    labels: wavy / straight / braided

输出
  hair_001
    第 1 组 -> brown
    第 2 组 -> wavy
```

`selection_mode` 表达标签组的选择方式：

| `selection_mode` | 含义 | 结果约束 |
|---|---|---|
| `single` | 单选标签组 | 该组最多返回 1 个标签 |
| `multiple` | 多选标签组 | 该组可返回 0 个、1 个或多个标签 |

是否接受空选、低置信结果或人工补标，由业务后端结合结果中的 `validation_issues`、`weight` 和自身规则判断；本接口只用 `selection_mode` 表达单选或多选。
业务后端应把标签库中的单选、多选配置映射到 `selection_mode` 后传入。同一个 `category_id` 可以在 `label_snapshot[]` 中出现多次，表示同一素材分类下存在多个标签组。

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
| `succeeded` | 是 | Job 已产出结果矩阵；业务方仍需读取 item 级状态 |
| `failed` | 是 | Job 批级失败，或所有 item 均失败 |

当前 `asset_image_tagging` 不承诺在 `running` 或 `failed` 状态返回 `job_result` 快照。业务方只应以 `job_status` 判断 Job 是否终态。

## 创建 Job

### Request

```http
POST /api/v1/ai-jobs/jobs
```

```json
{
  "client_request_id": "asset-image-tagging-import-20260831-001",
  "job_type": "asset_image_tagging",
  "job_params": {
    "tagging_language": "zh",
    "items": [
      {
        "item_id": "hair_001",
        "item_name": "棕色中长卷发",
        "category_id": "hair",
        "category_name": "发型",
        "asset": {
          "public_url": "https://bucket.example.com/assets/hair_001.png",
          "content_type": "image/png"
        },
        "metadata": {
          "filename": "hair_001.png"
        }
      }
    ],
    "label_snapshot": [
      {
        "category_id": "hair",
        "category_name": "发型",
        "selection_mode": "single",
        "labels": [
          {
            "label_id": "hair_color_brown",
            "label_name": "棕色",
            "definition": "头发主体颜色为棕色或棕褐色"
          },
          {
            "label_id": "hair_color_black",
            "label_name": "黑色",
            "definition": "头发主体颜色为黑色或深黑色"
          }
        ]
      },
      {
        "category_id": "hair",
        "category_name": "发型",
        "selection_mode": "multiple",
        "labels": [
          {
            "label_id": "hair_shape_wavy",
            "label_name": "波浪",
            "definition": "头发带有明显自然波浪或卷曲形态"
          },
          {
            "label_id": "hair_shape_straight",
            "label_name": "直发",
            "definition": "头发整体形态较直，没有明显波浪或卷曲"
          }
        ]
      }
    ],
    "rules": {
      "description_required": true,
      "description_language": "zh",
      "return_reason": true,
      "min_weight": 0.6
    }
  },
  "callback": {
    "url": "https://backend.example.com/ai-callbacks",
    "events": ["job.succeeded", "job.failed"]
  },
  "metadata": {
    "source_service": "asset-backend",
    "business_scene": "asset_import"
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
| `job_type` | string | 是 | 固定为 `asset_image_tagging` |
| `job_params` | object | 是 | 图片素材打标参数 |
| `callback` | object 或 null | 否 | 终态 Callback 配置 |
| `metadata` | object | 否 | 调用方透传元数据；不参与模型输入和幂等指纹 |
| `options` | object | 否 | Job 选项 |

### Job Params Fields

| 字段 | 类型 | 必填 | 说明 |
|---|---:|---:|---|
| `tagging_language` | string | 是 | 本次打标使用的标签语言，例如 `zh`、`en` |
| `items` | object[] | 是 | 待打标素材列表，至少 1 个 |
| `label_snapshot` | object[] | 是 | 业务后端组装后的标签组快照，至少 1 组；必须覆盖所有 `items[].category_id` |
| `rules` | object | 否 | 本次打标规则 |

### Item Fields

| 字段 | 类型 | 必填 | 说明 |
|---|---:|---:|---|
| `item_id` | string | 是 | 业务素材 ID 和本次 Job 内的结果对齐键；同一 Job 内唯一，结果原样返回 |
| `item_name` | string | 是 | 业务素材名称；用于辅助模型理解素材，不作为唯一键 |
| `category_id` | string | 是 | 素材分类 ID；用于匹配 `label_snapshot[].category_id` |
| `category_name` | string | 是 | 素材分类名称；用于辅助模型理解分类语义 |
| `asset` | object | 是 | 通用素材资源引用；当前图片打标要求 `asset.content_type` 为图片 MIME |
| `metadata` | object | 否 | 透传字段，例如文件名、图层、Sheet 名 |

### Asset Fields

| 字段 | 类型 | 必填 | 说明 |
|---|---:|---:|---|
| `public_url` | string | 是 | HTTPS 公网 URL，可以来自对象存储、CDN 或其他可公网访问的资源服务；AI 服务通过该地址读取素材 |
| `content_type` | string | 是 | 素材 MIME；当前图片打标支持 `image/png`、`image/jpeg`、`image/webp` |
| `internal_url` | string 或 null | 否 | 内网 URL 或审计字段；首版可不读取 |
| `sha256` | string 或 null | 否 | 原始资源内容的小写 64 位 hex SHA-256；业务方有内容 hash 时可传 |

### Label Snapshot Fields

| 字段 | 类型 | 必填 | 说明 |
|---|---:|---:|---|
| `category_id` | string | 是 | 标签组适用的素材分类 ID；用于和 `items[].category_id` 匹配 |
| `category_name` | string | 是 | 标签组适用的素材分类名称 |
| `selection_mode` | string | 是 | 当前标签组选择方式；支持 `single`、`multiple` |
| `labels` | object[] | 是 | 当前标签组下的候选标签列表，至少 1 个 |
| `metadata` | object | 否 | 透传字段，例如原始 Sheet 名、标签体系版本 |

### Candidate Label Fields

| 字段 | 类型 | 必填 | 说明 |
|---|---:|---:|---|
| `label_id` | string | 是 | 后端标签唯一 ID；同一 Job 内必须全局唯一 |
| `label_name` | string | 是 | 当前 `tagging_language` 下的标签名 |
| `definition` | string | 是 | 当前 `tagging_language` 下的标签定义 |
| `metadata` | object | 否 | 透传字段 |

### Rules Fields

| 字段 | 类型 | 必填 | 说明 |
|---|---:|---:|---|
| `description_required` | boolean | 否 | 是否要求为每个成功素材生成描述 |
| `description_language` | string 或 null | 否 | 描述语言；不传时默认等于 `tagging_language` |
| `return_reason` | boolean | 否 | 是否要求返回每个标签的 `reason`；建议为 `true` |
| `min_weight` | number 或 null | 否 | 低于该阈值的标签视为不采信；范围 `0 < min_weight <= 1` |

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
      "client_request_id": "asset-image-tagging-import-20260831-001",
      "job_type": "asset_image_tagging",
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

`job_status=succeeded` 表示 Job 已产出结构化结果，不表示每个素材都完整命中标签。业务方必须读取 `job_result.items[].status` 和 `validation_issues`。

```json
{
  "code": "0",
  "msg": "success",
  "data": {
    "job": {
      "job_id": "018f9a7f-2b7d-7a0a-9f24-6ff0b87c8b91",
      "client_request_id": "asset-image-tagging-import-20260831-001",
      "job_type": "asset_image_tagging",
      "job_status": "succeeded",
      "job_progress": {
        "percent": 100
      },
      "job_result": {
        "schema_version": "default",
        "job_type": "asset_image_tagging",
        "tagging_language": "zh",
        "batch_summary": {
          "total": 1,
          "succeeded": 1,
          "partial_success": 0,
          "failed": 0
        },
        "items": [
          {
            "item_id": "hair_001",
            "item_name": "棕色中长卷发",
            "category_id": "hair",
            "category_name": "发型",
            "asset": {
              "public_url": "https://bucket.example.com/assets/hair_001.png",
              "content_type": "image/png"
            },
            "status": "succeeded",
            "label_group_selections": [
              {
                "label_snapshot_index": 0,
                "category_id": "hair",
                "category_name": "发型",
                "selection_mode": "single",
                "labels": [
                  {
                    "label_id": "hair_color_brown",
                    "label_name": "棕色",
                    "definition": "头发主体颜色为棕色或棕褐色",
                    "weight": 0.92,
                    "reason": "图片中头发主体呈棕褐色"
                  }
                ]
              },
              {
                "label_snapshot_index": 1,
                "category_id": "hair",
                "category_name": "发型",
                "selection_mode": "multiple",
                "labels": [
                  {
                    "label_id": "hair_shape_wavy",
                    "label_name": "波浪",
                    "definition": "头发带有明显自然波浪或卷曲形态",
                    "weight": 0.88,
                    "reason": "发尾有明显自然波浪"
                  }
                ]
              }
            ],
            "description": {
              "language": "zh",
              "text": "棕色中长发，发尾带自然波浪，头发主体颜色偏棕褐色。"
            },
            "validation_issues": [],
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
      "client_request_id": "asset-image-tagging-import-20260831-001",
      "job_type": "asset_image_tagging",
      "job_status": "failed",
      "job_progress": {
        "percent": 100
      },
      "job_result": null,
      "job_error": {
        "reason": "ASSET_IMAGE_TAGGING_ALL_ITEMS_FAILED",
        "details": {
          "total": 1,
          "failed": 1
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
| `job_type` | string | 固定为 `asset_image_tagging` |
| `tagging_language` | string | 与请求 `job_params.tagging_language` 一致 |
| `batch_summary.total` | integer | 请求 `items[]` 总数 |
| `batch_summary.succeeded` | integer | 完整成功 item 数 |
| `batch_summary.partial_success` | integer | 有结果但存在校验问题的 item 数 |
| `batch_summary.failed` | integer | item 级失败数 |
| `items[]` | object[] | 每个素材的打标结果，按请求 `items[]` 顺序返回 |
| `items[].item_id` | string | 对应请求 `items[].item_id` |
| `items[].item_name` | string | 对应请求 `items[].item_name` |
| `items[].category_id` | string | 对应请求 `items[].category_id` |
| `items[].category_name` | string | 对应请求 `items[].category_name` |
| `items[].asset` | object | 素材资源引用快照 |
| `items[].status` | string | `succeeded`、`partial_success` 或 `failed` |
| `items[].label_group_selections` | object[] | 按匹配到的标签组顺序组织的选择结果；成功和部分成功 item 按匹配到的 `label_snapshot[]` 顺序全量返回，失败 item 为空数组 |
| `label_group_selections[].label_snapshot_index` | integer | 该标签组在请求 `label_snapshot[]` 中的位置，从 0 开始 |
| `label_group_selections[].category_id` | string | 标签组分类 ID，与当前 item 的 `category_id` 一致 |
| `label_group_selections[].category_name` | string | 标签组分类名称 |
| `label_group_selections[].selection_mode` | string | 标签组选择方式，与请求 `label_snapshot[].selection_mode` 一致 |
| `label_group_selections[].labels[]` | object[] | 该组内选中的标签；空数组表示该组没有选中标签。`single` 组长度只能是 0 或 1，`multiple` 组可返回多个 |
| `labels[].label_id` | string | 后端标签唯一 ID |
| `labels[].label_name` | string | 打标语言下的标签名 |
| `labels[].definition` | string | 打标语言下的标签定义快照 |
| `labels[].weight` | number | 置信或强度，范围 `0 < weight <= 1` |
| `labels[].reason` | string 或 null | 打标原因；`rules.return_reason=true` 时必须非空 |
| `items[].description` | object 或 null | 素材描述；不要求生成描述时可为 `null` |
| `description.language` | string | 描述语言 |
| `description.text` | string | 描述文本 |
| `items[].validation_issues` | object[] | item 级校验问题；完整成功时为空数组 |
| `items[].error` | object 或 null | item 级失败原因；失败 item 必须非空 |

### Job Error Fields

| 字段 | 类型 | 说明 |
|---|---:|---|
| `job_error.reason` | string | Job 级失败原因 |
| `job_error.details` | object | 结构化诊断信息 |
| `job_error.retryable` | boolean | 是否建议业务方稍后重试 |

### Validation Issue Fields

| 字段 | 类型 | 说明 |
|---|---:|---|
| `issue` | string | 校验问题类型 |
| `label_snapshot_index` | integer 或 null | 相关标签组在请求 `label_snapshot[]` 中的位置，从 0 开始 |
| `label_id` | string 或 null | 相关标签 ID |
| `message` | string | 可读说明 |
| `details` | object | 结构化诊断信息 |

常见 `issue`：

| `issue` | 含义 |
|---|---|
| `low_weight_filtered` | 标签低于 `rules.min_weight` 被过滤 |
| `description_missing` | 要求生成描述但模型未返回有效描述 |

## Batch Semantics

| 情况 | Job 终态 | 说明 |
|---|---|---|
| 所有 item 成功 | `succeeded` | `batch_summary.failed=0` |
| 部分 item 成功，部分 item 失败 | `succeeded` | 业务方读取 `items[].status` 处理部分失败 |
| item 模型输出不符合合同 | `succeeded` 或 `failed` | 该 item 标记为 `failed`；如果所有 item 都失败，则 Job 为 `failed` |
| 所有 item 都失败 | `failed` | `job_error.reason=ASSET_IMAGE_TAGGING_ALL_ITEMS_FAILED` |
| 请求级结构非法 | 创建请求失败 | 不创建 Job，返回错误 envelope |

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
      "total_cost_amount": "0.000456",
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
  "request_id": "req_04",
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
    "client_request_id": "asset-image-tagging-import-20260831-001",
    "job_type": "asset_image_tagging",
    "job_status": "succeeded",
    "job_progress": {
      "percent": 100
    },
    "job_result": {
      "schema_version": "default",
      "job_type": "asset_image_tagging",
      "tagging_language": "zh",
      "batch_summary": {
        "total": 1,
        "succeeded": 1,
        "partial_success": 0,
        "failed": 0
      },
      "items": [
        {
          "item_id": "hair_001",
          "item_name": "棕色中长卷发",
          "category_id": "hair",
          "category_name": "发型",
          "asset": {
            "public_url": "https://bucket.example.com/assets/hair_001.png",
            "content_type": "image/png"
          },
          "status": "succeeded",
          "label_group_selections": [
            {
              "label_snapshot_index": 0,
              "category_id": "hair",
              "category_name": "发型",
              "selection_mode": "single",
              "labels": [
                {
                  "label_id": "hair_color_brown",
                  "label_name": "棕色",
                  "definition": "头发主体颜色为棕色或棕褐色",
                  "weight": 0.92,
                  "reason": "图片中头发主体呈棕褐色"
                }
              ]
            },
            {
              "label_snapshot_index": 1,
              "category_id": "hair",
              "category_name": "发型",
              "selection_mode": "multiple",
              "labels": [
                {
                  "label_id": "hair_shape_wavy",
                  "label_name": "波浪",
                  "definition": "头发带有明显自然波浪或卷曲形态",
                  "weight": 0.88,
                  "reason": "发尾有明显自然波浪"
                }
              ]
            }
          ],
          "description": {
            "language": "zh",
            "text": "棕色中长发，发尾带自然波浪，头发主体颜色偏棕褐色。"
          },
          "validation_issues": [],
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

- AI 服务不从业务后端拉素材库或标签库；每个 Job 必须携带本次打标需要的完整 `items[]` 和 `label_snapshot[]` 快照。
- `label_snapshot[]` 是标签组列表，不是分类树；同一个 `category_id` 可以出现多次，表示同一分类下有多个标签组。
- 每个 `items[].category_id` 必须能在 `label_snapshot[].category_id` 中找到至少一个标签组；否则请求参数非法。
- AI 服务处理单个素材时，只使用 `category_id` 匹配到的标签组，不使用其他分类的标签组参与判断。
- `tagging_language` 是本次打标语言；`label_snapshot[].labels[].label_name` 和 `label_snapshot[].labels[].definition` 必须使用该语言。
- 如果业务后端只有另一种语言的标签，应先用标签翻译 Job 生成并保存对应语言，再提交本 Job。
- 同一 Job 内 `items[].item_id` 必须唯一。
- 同一 Job 内 `label_snapshot[].labels[].label_id` 必须全局唯一。
- 每个标签组必须声明 `selection_mode`。`single` 表示最多选 1 个，`multiple` 表示可选多个。
- 成功和部分成功 item 的 `label_group_selections[]` 必须按当前 item 匹配到的 `label_snapshot[]` 顺序全量返回；未选中标签的组返回 `labels=[]`，不能省略整个组。
- `selection_mode=single` 的结果中，`labels[]` 长度只能是 0 或 1。模型输出多个候选时，AI 服务必须把该 item 标记为 `failed`，不能向业务方返回违反合同的多个标签。
- 真实 `label_id` 是业务标签事实源，只在请求和结果合同中出现；AI 服务调用模型时应转为临时内部编号，模型不得直接看到或返回真实 `label_id`。
- 当前图片打标要求 `items[].asset.content_type` 必须是图片 MIME，图片必须通过公网 URL 传入，不接受裸 base64。
- 本 Job 不写业务资源表，不写业务标签表。

## 错误码

| 错误码 | HTTP 状态 | 场景 | 说明 |
|---|---:|---|---|
| `INVALID_JOB_TYPE` | 400 | `job_type` 不支持 | 不创建 Job |
| `INVALID_JOB_PARAMS` | 400 | `job_params` 结构非法 | 例如 `items[]` 为空、`label_snapshot[]` 为空、`items[].category_id` 没有匹配标签组、ID 重复 |
| `CLIENT_REQUEST_ID_CONFLICT` | 409 | 幂等键冲突 | 同一 `client_request_id` 对应不同请求指纹 |
| `UNSUPPORTED_LANGUAGE` | 400 | 语种不支持 | `tagging_language` 或 `description_language` 非法 |
| `ASSET_LABEL_SCHEMA_INVALID` | 400 | 标签组结构非法 | 例如 `selection_mode` 不支持、`label_id` 重复 |
| `ASSET_REF_INVALID` | 400 | 素材资源引用非法 | `asset.public_url`、`asset.content_type` 格式不符合要求，或可选 `asset.sha256` 格式不符合要求 |
| `INPUT_HASH_MISMATCH` | 400 | 素材内容 hash 不一致 | 仅当请求传入可选 `asset.sha256` 且校验不一致时触发；不继续调用模型 |
| `INPUT_TOO_LARGE` | 400 | 素材大小、图片宽高或像素超过限制 | 不继续调用模型 |
| `MODEL_CALL_FAILED` | 502 | 模型调用失败 | provider 错误、限流或超时 |
| `MODEL_OUTPUT_INVALID` | 500 | 模型输出不符合合同 | JSON 非法、未知标签引用、字段缺失、单选标签组返回多个标签 |
| `ASSET_IMAGE_TAGGING_ALL_ITEMS_FAILED` | 500 | 所有素材都打标失败 | Job 级失败 |
