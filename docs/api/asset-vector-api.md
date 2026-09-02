# 素材向量检索 API

本文面向业务后端，定义素材新增/更新、删除、搜索和对账接口。业务后端负责素材库、权限、上下架、标签库和展示数据；AI 服务负责把业务方提交的素材信息转成向量，并返回匹配到的资源 ID 列表。

## 联调参数配置

以下配置区用于双方联调时填写环境地址和密钥。不要把真实生产密钥提交到仓库；交付文档中的密钥应使用占位符，由安全渠道另行下发。

### 测试环境

| 项 | 示例值 | 说明 |
|---|---|---|
| Base URL | `https://test-ai.example.com` | 测试环境 AI 服务地址 |
| API Prefix | `/api/v1/ai-jobs` | 本文接口前缀 |
| `SERVICE_API_KEY` | `<TEST_SERVICE_API_KEY>` | 用于 `Authorization: Bearer <SERVICE_API_KEY>` |
| `CALLBACK_SIGNING_SECRET` | `<TEST_CALLBACK_SIGNING_SECRET>` | 用于调用方校验 AI 服务投递的 Callback 签名 |
| `X-AI-Service-Caller-ID` | `cms-test` | 可选；不传时服务使用 `default` |
| `X-Request-ID` | `test-asset-vector-001` | 可选；单次请求追踪 ID |

### 生产环境

| 项 | 示例值 | 说明 |
|---|---|---|
| Base URL | `https://ai.example.com` | 生产环境 AI 服务地址 |
| API Prefix | `/api/v1/ai-jobs` | 本文接口前缀 |
| `SERVICE_API_KEY` | `<PROD_SERVICE_API_KEY>` | 用于 `Authorization: Bearer <SERVICE_API_KEY>` |
| `CALLBACK_SIGNING_SECRET` | `<PROD_CALLBACK_SIGNING_SECRET>` | 用于调用方校验 AI 服务投递的 Callback 签名 |
| `X-AI-Service-Caller-ID` | `cms` | 可选；不传时服务使用 `default` |
| `X-Request-ID` | `prod-asset-vector-001` | 可选；单次请求追踪 ID |

请求头：

```http
Authorization: Bearer <SERVICE_API_KEY>
X-AI-Service-Caller-ID: <caller-id>
X-Request-ID: <request-id>
Content-Type: application/json
```

`X-AI-Service-Caller-ID` 是可选调用方标识，不是多租户安全边界。`X-Request-ID` 允许 1 到 128 个 ASCII 字母、数字、点号、下划线、冒号或连字符；不传时服务端生成。

## 接口清单

| 接口 | 方法和路径 | 调用方式 | 业务用途 |
|---|---|---|---|
| 批量新增/更新资源接口 | `POST /api/v1/ai-jobs/jobs` | 异步 Job | 新增或更新素材的可检索信息 |
| 批量删除资源接口 | `POST /api/v1/ai-jobs/jobs` | 异步 Job | 删除素材在 AI 服务中的向量投影 |
| 查询 Job 接口 | `GET /api/v1/ai-jobs/jobs/{job_id}` | 同步 | 轮询新增/更新或删除 Job 的进度、结果和错误 |
| 文本搜索接口 | `POST /api/v1/ai-jobs/vector-search` | 同步 | 根据用户输入文字搜索素材 |
| 图片搜索接口 | `POST /api/v1/ai-jobs/vector-search` | 同步 | 根据上传图片或公网图片搜索相似素材 |
| 资源 ID 搜索接口 | `POST /api/v1/ai-jobs/vector-search` | 同步 | 根据一个或多个已入库资源 ID 搜索相似素材 |
| 混合搜索接口 | `POST /api/v1/ai-jobs/vector-search` | 同步 | 组合文字、图片、资源 ID 搜索信号后搜索素材 |
| 正向对账接口 | `POST /api/v1/ai-jobs/vector-assets:exists` | 同步 | 检查一批资源 ID 是否已写入向量 |
| 反向对账接口 | `GET /api/v1/ai-jobs/vector-assets/ids` | 同步 | 分页拉取 AI 服务已索引的资源 ID |

## 调用流程

```text
新增 / 更新资源

业务后端素材库
  -> 组装资源 ID、资源名称、OSS 资源地址、可选标签
  -> 调批量新增/更新资源接口
  -> AI 服务生成或更新该资源的检索向量
  -> 业务后端轮询 Job 或接收 Callback
```

```text
搜索资源

用户发起搜索
  -> 按搜索意图选择 text / image / item_ids / hybrid
  -> 业务后端按权限、项目、分类、标签、上下架状态预过滤出可选 candidate_item_ids
  -> 调对应搜索接口，传 search_mode、该模式允许的查询字段和可选 candidate_item_ids
  -> AI 服务返回匹配到的 item_ids
  -> 业务后端拿 item_ids 回自己的素材库拼详情
```

```text
删除 / 对账

业务后端资源删除、下架或巡检
  -> 调批量删除资源接口删除不应检索的 item_id
  -> 或用正向 / 反向对账接口发现漏建、脏数据
```

## 公共请求约定

### Headers

| Header | 必填 | 说明 |
|---|---:|---|
| `Content-Type: application/json` | 是 | 请求体使用 JSON |
| `Authorization: Bearer <service-key>` | 是 | 服务访问凭证 |
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
  "code": "100012",
  "msg": "invalid job_params",
  "data": {
    "field": "job_params.items",
    "reason": "items must contain at least one asset item"
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
| `succeeded` | 是 | Job 已产出结果；批量新增/更新已整批生效，批量删除请求的资源已从本服务清除 |
| `failed` | 是 | Job 批级失败；业务数据不应按成功结果处理 |

## 公共字段

### Asset Fields

素材的 OSS 或公网资源信息。

| 字段 | 类型 | 必填 | 说明 |
|---|---:|---:|---|
| `public_url` | string | 是 | HTTPS 公网 URL，可以来自 OSS、CDN 或其他可公网访问的资源服务 |
| `content_type` | string | 是 | 资源 MIME，例如 `image/png`、`image/jpeg`、`image/webp` |
| `internal_url` | string 或 null | 否 | 内网 URL 或审计字段；首版可不读取 |
| `sha256` | string 或 null | 否 | 原始资源内容的小写 64 位 hex SHA-256；业务方有内容 hash 时可传 |

### Label Fields

素材已拥有的业务标签。该结构只用于新增/更新资源时增强建库，不用于搜索接口。没有标签时可以省略 `labels` 或传空数组，不要求素材先完成 AI 打标再建库。

如果业务方已经有中英文或更多语种的标签名、标签描述，应把这些标签文本都展开传入 `labels[]`。多语种标签可以提升文本搜索和混合搜索的召回质量；AI 服务只消费业务方传入的标签文本，不负责自动翻译或补齐语言。

| 字段 | 类型 | 必填 | 说明 |
|---|---:|---:|---|
| `label_id` | string | 是 | 业务标签唯一 ID |
| `language` | string | 是 | 语言代码，例如 `zh`、`en` |
| `label_name` | string | 是 | 当前 `language` 下的标签名 |
| `definition` | string 或 null | 否 | 当前 `language` 下的标签描述 |

### Text Query Fields

文本搜索条件。

| 字段 | 类型 | 必填 | 说明 |
|---|---:|---:|---|
| `query` | string | 是 | 用户输入的搜索文字 |

### Callback Fields

新增/更新资源 Job 和删除资源 Job 都支持 Callback。

| 字段 | 类型 | 必填 | 说明 |
|---|---:|---:|---|
| `url` | string | 是 | 业务方接收 Callback 的 HTTPS 地址 |
| `events` | string[] 或 null | 否 | 支持 `job.succeeded`、`job.failed`；省略时默认订阅两种终态事件 |

### Callback 签名

创建 Job 时传入 `callback.url` 后，AI 服务会在 Job 终态投递 Callback。Callback payload 不套 HTTP envelope，`job` 字段是完整 Job snapshot，结构与查询 Job 接口返回的 `data.job` 一致。

Callback 请求头：

```http
Content-Type: application/json
X-Callback-Timestamp: 2026-08-31T10:01:00+00:00
X-Callback-Signature: sha256=<hex>
```

签名内容是：

```text
timestamp + "." + raw_body
```

签名算法是 HMAC-SHA256，密钥是双方按环境约定的 `CALLBACK_SIGNING_SECRET`。调用方应校验签名、时间戳和 `event_id`，防止伪造与重放。

## 批量新增/更新资源接口

### 接口能力

批量新增或更新素材的可检索信息。业务方每次提交当前资源的完整快照，包括资源 ID、资源名称、OSS 资源地址和可选标签。单资源更新也是批量特例，`items` 数组只放 1 条即可。

该接口按整批语义处理：请求结构或执行过程失败时 Job 失败；Job 成功时本批资源已完成新增或更新。服务不暴露单条失败继续处理开关，调用方按 Job 终态判断本批是否成功。

该接口支持两种获取结果的方式：

| 方式 | 说明 |
|---|---|
| 轮询 | 创建成功后，用返回的 `job_id` 调 `GET /api/v1/ai-jobs/jobs/{job_id}` |
| Callback | 创建时传入 `callback.url`，AI 服务在 Job 终态投递通知 |

### Request

```http
POST /api/v1/ai-jobs/jobs
```

```json
{
  "client_request_id": "asset-vector-upsert-20260831-001",
  "job_type": "asset_vector_batch_upsert",
  "job_params": {
    "items": [
      {
        "item_id": "asset_001",
        "item_name": "棕色中长卷发",
        "asset": {
          "public_url": "https://bucket.example.com/assets/hair_001.png",
          "content_type": "image/png"
        },
        "labels": [
          {
            "label_id": "label_hair_color_brown",
            "language": "zh",
            "label_name": "棕色",
            "definition": "头发主体颜色为棕色或棕褐色"
          },
          {
            "label_id": "label_hair_color_brown",
            "language": "en",
            "label_name": "brown",
            "definition": "The main hair color is brown or brownish."
          }
        ],
        "metadata": {
          "filename": "hair_001.png",
          "asset_category": "hair"
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
    "business_scene": "asset_import"
  }
}
```

### Top-Level Fields

| 字段 | 类型 | 必填 | 说明 |
|---|---:|---:|---|
| `client_request_id` | string | 是 | 调用方幂等键；唯一范围是同一 `X-AI-Service-Caller-ID`，未传 caller 时使用服务端默认 caller |
| `job_type` | string | 是 | 固定为 `asset_vector_batch_upsert` |
| `job_params` | object | 是 | 批量新增/更新参数 |
| `callback` | object 或 null | 否 | 终态 Callback 配置 |
| `metadata` | object | 否 | 调用方透传元数据；不参与模型输入和幂等指纹 |

### Job Params Fields

| 字段 | 类型 | 必填 | 说明 |
|---|---:|---:|---|
| `items` | object[] | 是 | 待新增或更新的资源列表 |

### Item Fields

| 字段 | 类型 | 必填 | 说明 |
|---|---:|---:|---|
| `item_id` | string | 是 | 业务资源唯一 ID，也是结果对齐键；同一 Job 内唯一 |
| `item_name` | string | 是 | 资源名称，可参与检索语义 |
| `asset` | object | 是 | 素材 OSS 或公网资源信息，结构见 `Asset Fields` |
| `labels` | object[] | 否 | 资源已拥有的业务标签，结构见 `Label Fields`；没有标签时可省略或传空数组。如果已有中英文或更多语种标签，应都传入，可提高检索质量 |
| `metadata` | object | 否 | 透传字段，例如文件名、素材业务分类、图层、Sheet 名 |

### 创建响应

```json
{
  "code": "0",
  "msg": "success",
  "data": {
    "job": {
      "job_id": "018f9a7f-2b7d-7a0a-9f24-6ff0b87c8b91",
      "client_request_id": "asset-vector-upsert-20260831-001",
      "job_type": "asset_vector_batch_upsert",
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

### Callback 通知

创建请求传入 `callback.url` 后，AI 服务会在 Job 终态投递 Callback。Callback 的 `job` 字段是完整 Job snapshot，结构与查询 Job 接口返回的 `data.job` 一致。

业务方接收成功必须返回 HTTP `2xx`、`Content-Type: application/json` 和以下 JSON body：

```json
{
  "accepted": true,
  "msg": "ok",
  "details": {}
}
```

`204`、空 body、非 JSON body、缺少 `accepted`、`accepted` 不是 boolean，或 `accepted=false` 都会被视为未接受。Callback 投递失败不会改变 Job 终态，业务方必须保留轮询能力。

## 批量删除资源接口

### 接口能力

批量删除素材在 AI 服务中的向量投影。适用于资源删除、永久下架、批量清理和导入回滚。删除不存在的 `item_id` 也视为成功；成功后这些 `item_id` 在本服务内应不存在。

该接口支持两种获取结果的方式：

| 方式 | 说明 |
|---|---|
| 轮询 | 创建成功后，用返回的 `job_id` 调 `GET /api/v1/ai-jobs/jobs/{job_id}` |
| Callback | 创建时传入 `callback.url`，AI 服务在 Job 终态投递通知 |

### Request

```http
POST /api/v1/ai-jobs/jobs
```

```json
{
  "client_request_id": "asset-vector-delete-20260831-001",
  "job_type": "asset_vector_batch_delete",
  "job_params": {
    "item_ids": ["asset_001", "asset_002"]
  },
  "callback": {
    "url": "https://backend.example.com/ai-callbacks",
    "events": ["job.succeeded", "job.failed"]
  }
}
```

### Top-Level Fields

| 字段 | 类型 | 必填 | 说明 |
|---|---:|---:|---|
| `client_request_id` | string | 是 | 调用方幂等键；唯一范围是同一 `X-AI-Service-Caller-ID`，未传 caller 时使用服务端默认 caller |
| `job_type` | string | 是 | 固定为 `asset_vector_batch_delete` |
| `job_params` | object | 是 | 批量删除参数 |
| `callback` | object 或 null | 否 | 终态 Callback 配置 |
| `metadata` | object | 否 | 调用方透传元数据 |

### Job Params Fields

| 字段 | 类型 | 必填 | 说明 |
|---|---:|---:|---|
| `item_ids` | string[] | 是 | 要删除向量投影的业务资源 ID 列表 |

### 创建响应

```json
{
  "code": "0",
  "msg": "success",
  "data": {
    "job": {
      "job_id": "018f9a7f-2b7d-7a0a-9f24-6ff0b87c8b91",
      "client_request_id": "asset-vector-delete-20260831-001",
      "job_type": "asset_vector_batch_delete",
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
  "request_id": "req_02",
  "server_time": "2026-08-31T10:00:00+00:00"
}
```

### Callback 通知

创建请求传入 `callback.url` 后，AI 服务会在删除 Job 终态投递 Callback。规则同批量新增/更新资源接口，差异是 `job.job_type=asset_vector_batch_delete`，`job.job_result` 使用删除结果结构。

## 查询 Job 接口

### 接口能力

查询新增/更新或删除 Job 的当前状态、进度、结果、错误、Callback 状态和基础用量快照。

### Request

```http
GET /api/v1/ai-jobs/jobs/{job_id}
```

### Path Params

| 字段 | 类型 | 必填 | 说明 |
|---|---:|---:|---|
| `job_id` | string | 是 | 创建响应中的 `data.job.job_id` |

### Succeeded Response

`job_status=succeeded` 表示批量新增/更新已整批完成，或批量删除请求的资源已从本服务清除。

```json
{
  "code": "0",
  "msg": "success",
  "data": {
    "job": {
      "job_id": "018f9a7f-2b7d-7a0a-9f24-6ff0b87c8b91",
      "client_request_id": "asset-vector-upsert-20260831-001",
      "job_type": "asset_vector_batch_upsert",
      "job_status": "succeeded",
      "job_progress": {
        "percent": 100
      },
      "job_result": {
        "schema_version": "default",
        "job_type": "asset_vector_batch_upsert",
        "batch_summary": {
          "total": 1,
          "succeeded": 1
        },
        "items": [
          {
            "item_id": "asset_001",
            "status": "succeeded",
            "indexed": {
              "indexed_at": "2026-08-31T10:01:00+00:00"
            }
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
  },
  "request_id": "req_03",
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
      "client_request_id": "asset-vector-upsert-20260831-001",
      "job_type": "asset_vector_batch_upsert",
      "job_status": "failed",
      "job_progress": {
        "percent": 100
      },
      "job_result": null,
      "job_error": {
        "reason": "MODEL_CALL_FAILED",
        "details": {
          "provider": "dashscope",
          "operation": "embedding"
        },
        "retryable": true
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
  "request_id": "req_04",
  "server_time": "2026-08-31T10:01:00+00:00"
}
```

### Job Fields

| 字段 | 类型 | 说明 |
|---|---:|---|
| `job_id` | string | Job ID |
| `client_request_id` | string | 调用方幂等键；唯一范围是同一 `X-AI-Service-Caller-ID`，未传 caller 时使用服务端默认 caller |
| `job_type` | string | `asset_vector_batch_upsert` 或 `asset_vector_batch_delete` |
| `job_status` | string | `queued`、`running`、`succeeded` 或 `failed` |
| `job_progress.percent` | number | Job 进度百分比 |
| `job_result` | object 或 null | 成功时返回终态结果；失败时通常为 `null`，失败原因见 `job_error` |
| `job_error` | object 或 null | Job 级失败原因 |
| `cost` | object 或 null | Job 级费用快照；不可用或未终态时为 `null` |
| `usage` | object 或 null | AI 调用用量摘要 |
| `callback` | object 或 null | Callback 投递状态 |
| `status_url` | string | 当前 Job 查询路径 |
| `created_at` | string | 创建时间 |
| `updated_at` | string | 更新时间 |
| `finished_at` | string 或 null | 终态完成时间 |

### Job Result Fields

新增/更新结果字段：

| 字段 | 类型 | 说明 |
|---|---:|---|
| `job_type` | string | 固定为 `asset_vector_batch_upsert` |
| `schema_version` | string | 结果结构版本；当前固定为 `default` |
| `batch_summary.total` | integer | 请求 item 总数 |
| `batch_summary.succeeded` | integer | 成功写入或更新向量的 item 数 |
| `items[]` | object[] | item 级结果，按请求 `items[]` 顺序返回 |
| `items[].item_id` | string | 对应请求 `items[].item_id` |
| `items[].status` | string | 固定为 `succeeded` |
| `items[].indexed` | object | 写入成功时的向量信息 |
| `indexed.indexed_at` | string | 索引完成时间 |

删除结果字段：

| 字段 | 类型 | 说明 |
|---|---:|---|
| `job_type` | string | 固定为 `asset_vector_batch_delete` |
| `schema_version` | string | 结果结构版本；当前固定为 `default` |
| `batch_summary.total` | integer | 请求删除的 item 总数 |
| `batch_summary.deleted` | integer | 请求删除完成的 item 数；不存在的 `item_id` 也按删除完成处理 |
| `items[]` | object[] | item 级结果，按请求 `item_ids[]` 顺序返回 |
| `items[].item_id` | string | 请求删除的业务资源 ID |
| `items[].status` | string | 固定为 `deleted` |

### Job Error Fields

| 字段 | 类型 | 说明 |
|---|---:|---|
| `job_error.reason` | string | Job 级失败原因 |
| `job_error.details` | object | 结构化诊断信息 |
| `job_error.retryable` | boolean | 是否建议业务方稍后重试 |

### Usage Fields

| 字段 | 类型 | 说明 |
|---|---:|---|
| `usage.ai_call_count` | integer | 当前 Job 关联的 AI 调用次数 |
| `usage.total_tokens` | integer 或 null | provider 返回的 token 总量；模型不返回时为 `null` |
| `usage.final` | boolean | 是否为最终用量快照 |

### Callback Status Fields

| 字段 | 类型 | 说明 |
|---|---:|---|
| `callback.status` | string | `not_configured`、`pending`、`delivering`、`delivered`、`retrying` 或 `failed` |
| `callback.attempt` | integer | 已投递次数 |
| `callback.last_error` | string 或 null | 最近一次投递失败原因 |
| `callback.next_retry_at` | string 或 null | 下一次重试时间；没有重试计划时为 `null` |

## 文本搜索接口

### 接口能力

根据用户输入文字搜索相似素材。该接口固定 `search_mode=text`，只允许传 `text`、可选 `candidate_item_ids` 和可选 `top_k`。

### Request

```http
POST /api/v1/ai-jobs/vector-search
```

```json
{
  "search_mode": "text",
  "text": {
    "query": "红色礼盒，节日风格"
  },
  "candidate_item_ids": ["asset_001", "asset_002", "asset_003"]
}
```

### Request Fields

| 字段 | 类型 | 必填 | 说明 |
|---|---:|---:|---|
| `search_mode` | string | 是 | 固定为 `text` |
| `text` | object | 是 | 文本搜索条件，结构见 `Text Query Fields` |
| `candidate_item_ids` | string[] | 否 | 候选资源 ID 列表；传入时只在这些资源内排序并返回其子集，不传时在 AI 服务已索引资源内搜索 |
| `top_k` | integer | 否 | 返回结果数量；不传时由服务端使用默认值；传入时必须大于 0，具体最大值由双方上线前确认 |

不允许传 `asset` 或查询种子字段 `item_ids`。

### Response

响应结构同“搜索响应字段”。

## 图片搜索接口

### 接口能力

根据一张查询图片搜索相似素材。该接口固定 `search_mode=image`，只允许传 `asset`、可选 `candidate_item_ids` 和可选 `top_k`。

### Request

```http
POST /api/v1/ai-jobs/vector-search
```

```json
{
  "search_mode": "image",
  "asset": {
    "public_url": "https://bucket.example.com/query/query_001.png",
    "content_type": "image/png"
  },
  "candidate_item_ids": ["asset_001", "asset_002", "asset_003"]
}
```

### Request Fields

| 字段 | 类型 | 必填 | 说明 |
|---|---:|---:|---|
| `search_mode` | string | 是 | 固定为 `image` |
| `asset` | object | 是 | 查询图片 OSS 或公网资源信息，结构见 `Asset Fields` |
| `candidate_item_ids` | string[] | 否 | 候选资源 ID 列表；传入时只在这些资源内排序并返回其子集，不传时在 AI 服务已索引资源内搜索 |
| `top_k` | integer | 否 | 返回结果数量；不传时由服务端使用默认值；传入时必须大于 0，具体最大值由双方上线前确认 |

不允许传 `text` 或查询种子字段 `item_ids`。

### Response

响应结构同“搜索响应字段”。

## 资源 ID 搜索接口

### 接口能力

根据一个或多个已入库资源 ID 搜索相似素材。该接口固定 `search_mode=item_ids`，只允许传 `item_ids`、可选 `candidate_item_ids` 和可选 `top_k`。

### Request

```http
POST /api/v1/ai-jobs/vector-search
```

```json
{
  "search_mode": "item_ids",
  "item_ids": ["asset_001", "asset_002"],
  "candidate_item_ids": ["asset_010", "asset_011", "asset_012"]
}
```

### Request Fields

| 字段 | 类型 | 必填 | 说明 |
|---|---:|---:|---|
| `search_mode` | string | 是 | 固定为 `item_ids` |
| `item_ids` | string[] | 是 | 用作查询种子的已入库资源 ID 列表 |
| `candidate_item_ids` | string[] | 否 | 候选资源 ID 列表；传入时只在这些资源内排序并返回其子集，不传时在 AI 服务已索引资源内搜索 |
| `top_k` | integer | 否 | 返回结果数量；不传时由服务端使用默认值；传入时必须大于 0，具体最大值由双方上线前确认 |

不允许传 `text` 或 `asset`。

### Response

响应结构同“搜索响应字段”。

## 混合搜索接口

### 接口能力

组合两个或多个搜索信号后检索相似素材。该接口固定 `search_mode=hybrid`，可组合 `text`、`asset`、`item_ids`，并支持可选 `candidate_item_ids` 和可选 `top_k`。

### Request

```http
POST /api/v1/ai-jobs/vector-search
```

```json
{
  "search_mode": "hybrid",
  "text": {
    "query": "红色礼盒"
  },
  "asset": {
    "public_url": "https://bucket.example.com/query/query_001.png",
    "content_type": "image/png"
  },
  "item_ids": ["asset_001"],
  "candidate_item_ids": ["asset_010", "asset_011", "asset_012"]
}
```

### Request Fields

| 字段 | 类型 | 必填 | 说明 |
|---|---:|---:|---|
| `search_mode` | string | 是 | 固定为 `hybrid` |
| `text` | object | 条件必填 | 文本搜索条件，结构见 `Text Query Fields` |
| `asset` | object | 条件必填 | 查询图片 OSS 或公网资源信息，结构见 `Asset Fields` |
| `item_ids` | string[] | 条件必填 | 用作查询种子的已入库资源 ID 列表 |
| `candidate_item_ids` | string[] | 否 | 候选资源 ID 列表；传入时只在这些资源内排序并返回其子集，不传时在 AI 服务已索引资源内搜索 |
| `top_k` | integer | 否 | 返回结果数量；不传时由服务端使用默认值；传入时必须大于 0，具体最大值由双方上线前确认 |

`text`、`asset`、`item_ids` 至少提供两种。

### Response

响应结构同“搜索响应字段”。

## 搜索响应字段

搜索接口统一返回资源 ID 列表。传入 `candidate_item_ids` 时，返回结果只会来自该候选池；不传时，返回结果来自 AI 服务已索引资源集合。业务后端拿 `item_ids` 回自己的素材库拼装详情。

```json
{
  "code": "0",
  "msg": "success",
  "data": {
    "item_ids": ["asset_001", "asset_002", "asset_003"]
  },
  "request_id": "req_06",
  "server_time": "2026-08-31T10:02:00+00:00"
}
```

| 字段 | 类型 | 说明 |
|---|---:|---|
| `item_ids` | string[] | 命中的业务资源 ID，按相似度从高到低排序 |

## 正向对账接口

### 接口能力

业务后端传入一批 `item_id`，AI 服务返回这些资源是否已经存在向量投影。该接口用于导入后验收、补建判断和线上一致性检查。

### Request

```http
POST /api/v1/ai-jobs/vector-assets:exists
```

```json
{
  "item_ids": ["asset_001", "asset_002"]
}
```

### Request Fields

| 字段 | 类型 | 必填 | 说明 |
|---|---:|---:|---|
| `item_ids` | string[] | 是 | 需要检查是否已建向量的业务资源 ID 列表 |

### Response

```json
{
  "code": "0",
  "msg": "success",
  "data": {
    "items": [
      {
        "item_id": "asset_001",
        "exists": true
      },
      {
        "item_id": "asset_002",
        "exists": false
      }
    ]
  },
  "request_id": "req_07",
  "server_time": "2026-08-31T10:03:00+00:00"
}
```

### Response Fields

| 字段 | 类型 | 说明 |
|---|---:|---|
| `items[]` | object[] | 按请求 `item_ids[]` 顺序返回 |
| `items[].item_id` | string | 业务资源 ID |
| `items[].exists` | boolean | 是否存在向量投影 |

## 反向对账接口

### 接口能力

业务后端分页拉取 AI 服务已经索引的 `item_id`，再回自己的素材库判断哪些资源已经删除、下架或不应存在，然后显式提交批量删除资源 Job。

### Request

```http
GET /api/v1/ai-jobs/vector-assets/ids?limit=500&cursor=asset_001
```

### Query Fields

| 字段 | 类型 | 必填 | 说明 |
|---|---:|---:|---|
| `limit` | integer | 否 | 返回数量，必须大于 0，最大 500；不传时服务端默认返回 100 条 |
| `cursor` | string 或 null | 否 | 上一页响应中的 `next_cursor`；调用方只应原样传回，不要自行构造 |

### Response

```json
{
  "code": "0",
  "msg": "success",
  "data": {
    "item_ids": ["asset_001", "asset_002"],
    "next_cursor": null
  },
  "request_id": "req_08",
  "server_time": "2026-08-31T10:04:00+00:00"
}
```

### Response Fields

| 字段 | 类型 | 说明 |
|---|---:|---|
| `item_ids` | string[] | AI 服务已索引的业务资源 ID 列表 |
| `next_cursor` | string 或 null | 下一页游标；没有下一页时为 `null` |

## Curl 示例

以下示例用于说明当前调用方式。示例中的域名、密钥、Job ID 和资源 URL 需要替换为双方联调环境提供的值。

### 批量新增/更新资源

```bash
curl -sS -X POST "https://test-ai.example.com/api/v1/ai-jobs/jobs" \
  -H "Authorization: Bearer <TEST_SERVICE_API_KEY>" \
  -H "X-AI-Service-Caller-ID: cms-test" \
  -H "X-Request-ID: test-asset-vector-upsert-001" \
  -H "Content-Type: application/json" \
  -d '{
    "client_request_id": "asset-vector-upsert-20260831-001",
    "job_type": "asset_vector_batch_upsert",
    "job_params": {
      "items": [
        {
          "item_id": "asset_001",
          "item_name": "棕色中长卷发",
          "asset": {
            "public_url": "https://bucket.example.com/assets/hair_001.png",
            "content_type": "image/png"
          },
          "labels": [
            {
              "label_id": "label_hair_color_brown",
              "language": "zh",
              "label_name": "棕色",
              "definition": "头发主体颜色为棕色或棕褐色"
            }
          ]
        }
      ]
    },
    "options": {
      "priority": "normal",
      "idempotency_mode": "return_existing"
    }
  }'
```

### 查询 Job

```bash
curl -sS -X GET "https://test-ai.example.com/api/v1/ai-jobs/jobs/018f9a7f-2b7d-7a0a-9f24-6ff0b87c8b91" \
  -H "Authorization: Bearer <TEST_SERVICE_API_KEY>" \
  -H "X-AI-Service-Caller-ID: cms-test" \
  -H "X-Request-ID: test-asset-vector-poll-001"
```

### 批量删除资源

```bash
curl -sS -X POST "https://test-ai.example.com/api/v1/ai-jobs/jobs" \
  -H "Authorization: Bearer <TEST_SERVICE_API_KEY>" \
  -H "X-AI-Service-Caller-ID: cms-test" \
  -H "X-Request-ID: test-asset-vector-delete-001" \
  -H "Content-Type: application/json" \
  -d '{
    "client_request_id": "asset-vector-delete-20260831-001",
    "job_type": "asset_vector_batch_delete",
    "job_params": {
      "item_ids": ["asset_001", "asset_002"]
    },
    "options": {
      "priority": "normal",
      "idempotency_mode": "return_existing"
    }
  }'
```

### 图片搜索

```bash
curl -sS -X POST "https://test-ai.example.com/api/v1/ai-jobs/vector-search" \
  -H "Authorization: Bearer <TEST_SERVICE_API_KEY>" \
  -H "X-AI-Service-Caller-ID: cms-test" \
  -H "X-Request-ID: test-asset-vector-image-search-001" \
  -H "Content-Type: application/json" \
  -d '{
    "search_mode": "image",
    "asset": {
      "public_url": "https://bucket.example.com/query/query_001.png",
      "content_type": "image/png"
    },
    "top_k": 20
  }'
```

### 限定候选池文本搜索

```bash
curl -sS -X POST "https://test-ai.example.com/api/v1/ai-jobs/vector-search" \
  -H "Authorization: Bearer <TEST_SERVICE_API_KEY>" \
  -H "X-AI-Service-Caller-ID: cms-test" \
  -H "X-Request-ID: test-asset-vector-text-search-001" \
  -H "Content-Type: application/json" \
  -d '{
    "search_mode": "text",
    "text": {
      "query": "红色礼盒，节日风格"
    },
    "candidate_item_ids": ["asset_001", "asset_002", "asset_003"],
    "top_k": 20
  }'
```

### 资源 ID 搜索

```bash
curl -sS -X POST "https://test-ai.example.com/api/v1/ai-jobs/vector-search" \
  -H "Authorization: Bearer <TEST_SERVICE_API_KEY>" \
  -H "X-AI-Service-Caller-ID: cms-test" \
  -H "X-Request-ID: test-asset-vector-item-search-001" \
  -H "Content-Type: application/json" \
  -d '{
    "search_mode": "item_ids",
    "item_ids": ["asset_001"],
    "top_k": 20
  }'
```

### 混合搜索

```bash
curl -sS -X POST "https://test-ai.example.com/api/v1/ai-jobs/vector-search" \
  -H "Authorization: Bearer <TEST_SERVICE_API_KEY>" \
  -H "X-AI-Service-Caller-ID: cms-test" \
  -H "X-Request-ID: test-asset-vector-hybrid-search-001" \
  -H "Content-Type: application/json" \
  -d '{
    "search_mode": "hybrid",
    "text": {
      "query": "红色礼盒"
    },
    "asset": {
      "public_url": "https://bucket.example.com/query/query_001.png",
      "content_type": "image/png"
    },
    "top_k": 20
  }'
```

### 正向对账

```bash
curl -sS -X POST "https://test-ai.example.com/api/v1/ai-jobs/vector-assets:exists" \
  -H "Authorization: Bearer <TEST_SERVICE_API_KEY>" \
  -H "X-AI-Service-Caller-ID: cms-test" \
  -H "X-Request-ID: test-asset-vector-exists-001" \
  -H "Content-Type: application/json" \
  -d '{
    "item_ids": ["asset_001", "asset_002"]
  }'
```

### 反向对账

```bash
curl -sS -X GET "https://test-ai.example.com/api/v1/ai-jobs/vector-assets/ids?limit=500" \
  -H "Authorization: Bearer <TEST_SERVICE_API_KEY>" \
  -H "X-AI-Service-Caller-ID: cms-test" \
  -H "X-Request-ID: test-asset-vector-ids-001"
```

## 处理规则

- `item_id` 是业务资源唯一 ID，也是本服务和业务后端之间的结果对齐键。
- `client_request_id` 是调用方幂等键，唯一范围是同一 `X-AI-Service-Caller-ID`。同一调用方重复提交同一请求时返回已有 Job；同一 `client_request_id` 对应不同请求内容时返回 `CLIENT_REQUEST_ID_CONFLICT`。
- 新增/更新资源时，业务方传入当前资源完整快照；AI 服务不从业务后端反查素材或标签。
- 新增/更新资源时，AI 服务根据 `item_name`、`asset`、`labels[]` 生成或更新向量。
- `labels[]` 是资源已有标签，是增强建库输入。没有标签时可以省略或传空数组，不影响仅基于资源名称和素材内容更新向量。
- 多语种标签由业务方直接展开到 `labels[]`。同一个 `label_id` 可以出现多条不同 `language` 的标签文本。
- 标签是否可信、是否已审核、是否参与检索，由业务后端决定。本服务不调用标签库，也不保存完整标签库。
- 资源名称、标签名、标签描述发生变化且业务方认为会影响检索时，业务方应重新提交批量新增/更新资源接口。
- 搜索接口支持可选 `candidate_item_ids`。业务后端可以先按权限、项目、分类、标签、状态、收藏等业务规则过滤候选池，再把候选池传给 AI 服务做向量排序，避免先全局取 Top K 再过滤导致结果不足。
- `candidate_item_ids` 只表达搜索候选池，不表达查询种子；资源 ID 搜索的查询种子仍使用 `item_ids`。
- `candidate_item_ids=[]` 是合法请求，表示候选池为空，服务端直接返回 `item_ids=[]`。
- `candidate_item_ids` 中尚未建向量的资源不参与排序，也不会出现在返回结果中；业务方需要严格检查缺失向量时使用正向对账接口。
- 搜索接口的 `top_k` 是可选字段；不传时由服务端控制默认返回数量。
- 搜索结果只返回排序后的 `item_ids`，不返回业务素材详情、分数、模型、维度或向量元信息。
- 未传 `candidate_item_ids` 时，AI 服务按已索引资源集合检索；业务后端仍可在拿到 `item_ids` 后做最终过滤和详情拼装，但可能出现过滤后结果不足。
- 正向和反向对账接口只返回对账必需字段，不返回模型、维度、版本或其他向量元信息。
- 本服务不暴露完整向量、完整图片二进制、base64 大 payload、完整模型响应或 provider raw payload。

## 错误码

| Reason | HTTP | 场景 | Retryable |
|---|---:|---|---:|
| `INVALID_INPUT` | 400 | 同步搜索或对账接口请求结构非法，例如 `search_mode` 与参数不匹配、`top_k` 超限、`limit` 超限 | no |
| `UNAUTHORIZED` | 401 | 缺少或错误的 Bearer token | no |
| `FORBIDDEN` | 403 | caller 被拒绝访问 | no |
| `REQUEST_ID_INVALID` | 400 | `X-Request-ID` 格式非法 | no |
| `INVALID_JOB_TYPE` | 400 | `job_type` 未注册或当前环境不允许外部提交 | no |
| `INVALID_JOB_PARAMS` | 400 | Job 请求结构非法，例如 `items[]` 为空、`item_id` 重复、`item_ids[]` 为空 | no |
| `CLIENT_REQUEST_ID_CONFLICT` | 409 | 同一 caller 下重复 `client_request_id` 但请求内容不一致 | no |
| `JOB_NOT_FOUND` | 404 | 查询的 Job 不存在或不属于当前 caller | no |
| `QUEUE_FULL` | 503 | 服务当前接单容量已满 | yes |
| `QUERY_ITEM_NOT_INDEXED` | 404 | `item_ids[]` 中存在未建向量的查询种子资源 | no |
| `MODEL_CALL_FAILED` | 502 | 调用模型失败 | yes |
| `MODEL_CALL_TIMEOUT` | 504 | 调用模型超时 | yes |
| `MODEL_OUTPUT_INVALID` | 502 | 模型输出不符合合同，例如向量维度不符、字段缺失或响应不可解析 | no |
| `JOB_TIMEOUT` | 504 | Job 执行超时 | yes |
| `JOB_EXECUTION_FAILED` | 500 | 未归类的 Job 执行失败 | no |
| `INTERNAL_ERROR` | 500 | 同步搜索或对账接口未预期异常 | no |

错误响应中的 `code` 是数字错误码，`msg` 是错误消息；表中的 Reason 是服务内部和 `job_error.reason` 中使用的稳定错误原因。调用方做业务分支时优先根据 HTTP status、`job_status` 和 `job_error.reason` 处理。
