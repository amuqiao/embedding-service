# AI 提供 RS 标签体系翻译接口

本文面向 RS 调用方，定义 RS 如何通过通用 AI Job 接口提交默认标签结构体翻译任务，并通过轮询获取翻译结果。

## 接口边界

RS 是标签体系的拥有方。RS 调用本接口时提交一次固定的默认 `TagSchemaSnapshot` 和 `MutualExclusionRule[]` 快照，以及本次任务的 `source_language` 和 `target_languages`。

RS 负责：

- 维护默认标签结构体。
- 提供待翻译的 `source_schema` 和 `source_mutual_exclusion_rules` 快照。
- 提供 `source_language` 和 `target_languages`。
- 创建翻译 Job，保存 `job_id`。
- 通过轮询获取翻译结果。
- 保存和分发翻译后的标签结构体。

AI 负责：

- 异步翻译分类展示名、标签展示名和标签定义。
- 保持标签 id、辅助 slug、数量约束和互斥规则不变。
- 返回与输入结构一一对应的 `translated_schemas`。

AI 不负责保存、分发或切换 RS 标签库。本接口只通过轮询获取结果。

## 基础接口

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `POST` | `/api/v1/ai-jobs/jobs` | 创建标签体系翻译 Job。 |
| `GET` | `/api/v1/ai-jobs/jobs/{job_id}` | 查询标签体系翻译 Job 状态和结果。 |

请求应携带服务鉴权：

```http
Authorization: Bearer <SERVICE_API_KEY>
X-AI-Service-Caller-ID: rs
```

## 创建翻译 Job

```http
POST /api/v1/ai-jobs/jobs
Content-Type: application/json
```

成功返回 `202 Accepted`。该响应只表示 AI 已接单，不表示翻译完成。

### 请求体

```json
{
  "client_request_id": "rs:tag-schema-default:en,es,pt",
  "job_type": "short_drama.tag_schema.translation",
  "job_params": {
    "source_language": "zh",
    "target_languages": ["en", "es", "pt"],
    "source_schema": {
      "categories": [
        {
          "display_name": "受众",
          "min_items": 1,
          "max_items": 1,
          "labels": [
            {
              "label_id": "lbl_audience_male",
              "label_key": "male_oriented",
              "display_name": "男频",
              "definition": "核心受众为男性群体，叙事视角、人物塑造、价值观以男性主角为核心..."
            }
          ]
        }
      ]
    },
    "source_mutual_exclusion_rules": [
      {
        "label_id": "lbl_genre_family_ethics",
        "exclude_label_ids": ["lbl_genre_thriller_supernatural", "lbl_genre_adventure"]
      }
    ]
  },
  "metadata": {
    "source_service": "rs",
    "business_scene": "tag_schema_translation"
  }
}
```

### 请求字段

| 字段 | 类型 | 必需性 | 说明 |
| --- | --- | --- | --- |
| `client_request_id` | `string` | 建议 | RS 幂等键。推荐包含默认标签数据标识和 `target_languages` 的规范化值。 |
| `job_type` | `string` | 必需 | 固定为 `short_drama.tag_schema.translation`。 |
| `job_params` | `object` | 必需 | 标签体系翻译任务参数。 |
| `job_params.source_language` | `string` | 必需 | 源语种。使用 [language-codes.md](language-codes.md) 中的代码，基础中文使用 `zh`。 |
| `job_params.target_languages` | `string[]` | 必需 | 目标语种列表。必须非空、去重，所有值都必须在 [language-codes.md](language-codes.md) 中，并按该文档的业务列表顺序排列。 |
| `job_params.source_schema` | `object` | 必需 | 默认标签结构体快照，类型为 `TagSchemaSnapshot`。 |
| `job_params.source_mutual_exclusion_rules` | `object[]` | 必需 | 默认互斥标签结构体快照，类型为 `MutualExclusionRule[]`。 |
| `metadata` | `object` | 可选 | 调用方透传元数据。 |

`source_schema` 和 `source_mutual_exclusion_rules` 只提交结构体本体，不包含外层 envelope。

### 创建响应

创建成功后，AI 返回已创建的 job 基本信息。首次创建的新 job 初始 `status` 必须为 `queued`。

如果 `client_request_id` 命中已有 job，AI 不创建新 job，仍返回该 job 的基本信息；响应中的 `status` 必须是已有 job 的当前真实状态。

```json
{
  "job_id": "0a9be3fb-f01b-4f5d-90b5-4148c4a61df1",
  "client_request_id": "rs:tag-schema-default:en,es,pt",
  "job_type": "short_drama.tag_schema.translation",
  "status": "queued",
  "status_url": "/api/v1/ai-jobs/jobs/0a9be3fb-f01b-4f5d-90b5-4148c4a61df1",
  "created_at": "2026-06-15T10:00:00Z"
}
```

| 字段 | 类型 | 必需性 | 说明 |
| --- | --- | --- | --- |
| `job_id` | `string` | 必需 | AI Job 唯一 id。 |
| `client_request_id` | `string` | 可选 | 创建请求中传入的幂等键。 |
| `job_type` | `string` | 必需 | 固定为 `short_drama.tag_schema.translation`。 |
| `status` | `string` | 必需 | 当前 job 状态。 |
| `status_url` | `string` | 必需 | 查询该 job 的相对路径。 |
| `created_at` | `string` | 必需 | Job 创建时间，RFC 3339 / ISO 8601。 |

创建请求和幂等命中请求均返回 `202 Accepted`；RS 必须以响应体中的 `job_id` 和 `status` 为准。

## 查询翻译结果

```http
GET /api/v1/ai-jobs/jobs/{job_id}
```

### 状态语义

| status | 是否终态 | RS 处理方式 | 语义 |
| --- | --- | --- | --- |
| `queued` | 否 | 继续轮询。 | AI 已接单并创建 job，但尚未开始执行。 |
| `running` | 否 | 继续轮询，可展示 `progress`。 | AI 已开始执行，可能处于输入校验、翻译、结构校验或产物生成阶段。 |
| `succeeded` | 是 | 停止轮询，读取 `result`。 | AI 已完成翻译，并生成结构自洽的翻译产物。 |
| `failed` | 是 | 停止轮询，读取 `error`。 | 任务失败。失败原因由 `error.code` 和 `error.message` 表示。 |

状态字段规则：

- 只有 `succeeded` 和 `failed` 是终态。
- `queued` 和 `running` 时 `result` 和 `error` 必须为 `null`。
- `succeeded` 时 `result` 必须存在，`error` 必须为 `null`。
- `failed` 时 `error` 必须存在，`result` 必须为 `null`。
- 内部执行阶段应通过 `progress.stage` 表示，不扩展外部 `status` 枚举。

### 成功响应

```json
{
  "job_id": "0a9be3fb-f01b-4f5d-90b5-4148c4a61df1",
  "client_request_id": "rs:tag-schema-default:en,es,pt",
  "job_type": "short_drama.tag_schema.translation",
  "status": "succeeded",
  "progress": {
    "percent": 100,
    "message": "finished",
    "stage": "finished"
  },
  "result": {
    "artifacts": [
      {
        "key": "translated_schemas",
        "type": "json",
        "label": "翻译后的标签结构体",
        "content": [
          {
            "categories": [
              {
                "display_name": "Audience",
                "min_items": 1,
                "max_items": 1,
                "labels": [
                  {
                    "label_id": "lbl_audience_male",
                    "label_key": "male_oriented",
                    "display_name": "Male-oriented",
                    "definition": "The core audience is male..."
                  }
                ]
              }
            ]
          },
          {
            "categories": [
              {
                "display_name": "Audiencia",
                "min_items": 1,
                "max_items": 1,
                "labels": [
                  {
                    "label_id": "lbl_audience_male",
                    "label_key": "male_oriented",
                    "display_name": "Orientado a hombres",
                    "definition": "La audiencia principal es masculina..."
                  }
                ]
              }
            ]
          },
          {
            "categories": [
              {
                "display_name": "Público",
                "min_items": 1,
                "max_items": 1,
                "labels": [
                  {
                    "label_id": "lbl_audience_male",
                    "label_key": "male_oriented",
                    "display_name": "Voltado ao público masculino",
                    "definition": "O público principal é masculino..."
                  }
                ]
              }
            ]
          }
        ]
      },
      {
        "key": "mutual_exclusion_rules",
        "type": "json",
        "label": "互斥标签结构体",
        "content": [
          {
            "label_id": "lbl_genre_family_ethics",
            "exclude_label_ids": ["lbl_genre_thriller_supernatural", "lbl_genre_adventure"]
          }
        ]
      }
    ],
    "signals": {
      "source_schema_hash": "sha256:source-schema",
      "translated_schemas_hash": "sha256:translated-schemas"
    }
  },
  "error": null,
  "metadata": {
    "source_service": "rs",
    "business_scene": "tag_schema_translation"
  },
  "created_at": "2026-06-15T10:00:00Z",
  "started_at": "2026-06-15T10:00:05Z",
  "finished_at": "2026-06-15T10:01:30Z"
}
```

### 失败响应

```json
{
  "job_id": "0a9be3fb-f01b-4f5d-90b5-4148c4a61df1",
  "client_request_id": "rs:tag-schema-default:en,es,pt",
  "job_type": "short_drama.tag_schema.translation",
  "status": "failed",
  "progress": {
    "percent": 100,
    "message": "invalid source schema",
    "stage": "failed"
  },
  "result": null,
  "error": {
    "code": "INVALID_SOURCE_SCHEMA",
    "message": "source_schema contains duplicate label_id",
    "details": {
      "label_id": "lbl_audience_male"
    }
  },
  "metadata": {
    "source_service": "rs",
    "business_scene": "tag_schema_translation"
  },
  "created_at": "2026-06-15T10:00:00Z",
  "started_at": "2026-06-15T10:00:05Z",
  "finished_at": "2026-06-15T10:00:06Z"
}
```

### 查询响应字段

| 字段 | 类型 | 必需性 | 说明 |
| --- | --- | --- | --- |
| `job_id` | `string` | 必需 | AI Job 唯一 id。 |
| `client_request_id` | `string` | 可选 | 创建请求中传入的幂等键。 |
| `job_type` | `string` | 必需 | 固定为 `short_drama.tag_schema.translation`。 |
| `status` | `string` | 必需 | `queued`、`running`、`succeeded`、`failed`。 |
| `progress` | `object` | 必需 | 任务进度信息。 |
| `result` | `object \| null` | 终态成功时必需 | 翻译结果。 |
| `error` | `object \| null` | 终态失败时必需 | 失败信息。 |
| `metadata` | `object` | 可选 | 创建请求透传元数据。 |
| `created_at` | `string` | 必需 | Job 创建时间，RFC 3339 / ISO 8601。 |
| `started_at` | `string \| null` | 可选 | Job 开始执行时间。 |
| `finished_at` | `string \| null` | 终态时必需 | Job 完成时间。 |

### result 字段

| 字段 | 类型 | 必需性 | 说明 |
| --- | --- | --- | --- |
| `result.artifacts` | `object[]` | 必需 | Job 产物列表。成功终态必须包含 `translated_schemas` 和 `mutual_exclusion_rules` 两个 artifact。 |
| `result.signals` | `object` | 必需 | 翻译任务信号字段。 |
| `artifacts[].key` | `string` | 必需 | 产物 key。允许值：`translated_schemas`、`mutual_exclusion_rules`。 |
| `artifacts[].type` | `string` | 必需 | 产物类型，当前固定为 `json`。 |
| `artifacts[].label` | `string` | 必需 | 产物展示名。 |
| `artifacts[].content` | `object[]` | 必需 | 产物内容，具体结构由 `key` 决定。 |
| `translated_schemas.content` | `object[]` | 必需 | 翻译后的 `TagSchemaSnapshot[]`，数组顺序必须与请求 `job_params.target_languages` 一致。 |
| `mutual_exclusion_rules.content` | `object[]` | 必需 | 互斥规则列表，类型为 `MutualExclusionRule[]`。AI 不翻译该内容，只原样返回。 |
| `result.signals.source_schema_hash` | `string` | 必需 | 源标签结构体快照 hash。 |
| `result.signals.translated_schemas_hash` | `string` | 必需 | 翻译结果 hash。 |

### error 字段

| 字段 | 类型 | 必需性 | 说明 |
| --- | --- | --- | --- |
| `error.code` | `string` | 失败时必需 | 稳定错误码，见“错误码”。 |
| `error.message` | `string` | 失败时必需 | 可读错误描述。 |
| `error.details` | `object` | 失败时可选 | 错误详情。 |

## 结构体字段

### TagSchemaSnapshot

| 字段 | 类型 | 必需性 | 说明 |
| --- | --- | --- | --- |
| `categories` | `object[]` | 必需 | 标签分类列表，元素类型为 `TagCategory`。 |

### TagCategory

| 字段 | 类型 | 必需性 | 说明 |
| --- | --- | --- | --- |
| `display_name` | `string` | 必需 | 分类展示名，需要翻译。 |
| `min_items` | `integer` | 必需 | 最少输出数量，不翻译。 |
| `max_items` | `integer` | 必需 | 最多输出数量，不翻译。 |
| `labels` | `object[]` | 必需 | 标签列表，元素类型为 `TagLabel`。 |

### TagLabel

| 字段 | 类型 | 必需性 | 说明 |
| --- | --- | --- | --- |
| `label_id` | `string` | 必需 | 标签全局唯一 id，不翻译、不改变。 |
| `label_key` | `string` | 可选 | 辅助 slug，不翻译、不作为引用键。 |
| `display_name` | `string` | 必需 | 标签展示名，需要翻译。 |
| `definition` | `string` | 必需 | 标签定义，需要翻译。 |

### MutualExclusionRule

| 字段 | 类型 | 必需性 | 说明 |
| --- | --- | --- | --- |
| `label_id` | `string` | 必需 | 互斥规则主标签 id，不翻译。 |
| `exclude_label_ids` | `string[]` | 必需 | 与主标签互斥的标签 id 列表，不翻译。 |

## 翻译规则

- `display_name` 翻译。
- `definition` 翻译。
- `label_id` 不翻译、不改变。
- `label_key` 如存在，不翻译、不作为引用键。
- `min_items`、`max_items` 不翻译。
- `source_mutual_exclusion_rules` 不翻译，只原样带入结果。
- `translated_schemas.content` 必须与请求 `job_params.target_languages` 按数组顺序一一对应。

## 自洽规则

- `source_language` 和 `target_languages` 是本接口唯一表达翻译方向的字段。
- `target_languages` 必须非空、去重，且每个值都必须来自 [language-codes.md](language-codes.md)。
- `target_languages` 必须按 [language-codes.md](language-codes.md) 的业务列表顺序排列；顺序不符合时返回 `400 INVALID_REQUEST`。
- `translated_schemas.content` 的长度必须等于 `target_languages` 的长度。
- 每个翻译后的 `TagSchemaSnapshot` 必须保留源 schema 的分类数量和标签数量。
- 每个 `label_id` 必须与源 schema 一致。
- `label_id` 全局唯一，翻译过程不得新增、删除、替换或重写。
- 互斥规则引用必须仍然指向存在的 `label_id`。

## 幂等

推荐幂等键：

```text
rs:tag-schema-default:{target_languages_csv}
```

`target_languages_csv` 应使用已经去重并按 [language-codes.md](language-codes.md) 业务列表顺序排列后的 `target_languages` 拼接，例如：

```text
rs:tag-schema-default:en,es,pt
```

请求指纹必须覆盖完整 `job_params`，至少包括 `source_language`、规范化后的 `target_languages`、`source_schema` 和 `source_mutual_exclusion_rules`。同一幂等键如果传入不同请求指纹，应返回 `409 CLIENT_REQUEST_ID_CONFLICT`。

## 错误码

| 错误码 | 说明 |
| --- | --- |
| `INVALID_REQUEST` | 请求结构不合法。 |
| `INVALID_JOB_TYPE` | `job_type` 不合法或未注册。 |
| `CLIENT_REQUEST_ID_CONFLICT` | 同一幂等键请求内容不一致。 |
| `JOB_NOT_FOUND` | 查询的 job 不存在或无权访问。 |
| `UNSUPPORTED_LANGUAGE` | `source_language` 或 `target_languages[]` 中存在不支持的代码。 |
| `INVALID_SOURCE_SCHEMA` | 源标签结构体不合法，例如重复 `label_id` 或互斥规则引用不存在。 |
| `TRANSLATION_FAILED` | 翻译过程失败。 |
| `JOB_TIMEOUT` | 翻译任务超时。 |
| `INTERNAL_ERROR` | AI 服务内部错误。 |

## HTTP 状态码

| 状态码 | 场景 |
| --- | --- |
| `202 Accepted` | 创建请求已接收，或 `client_request_id` 幂等命中已有 job。 |
| `200 OK` | 查询 job 成功。 |
| `400 Bad Request` | 请求结构不合法、代码不支持或 `target_languages` 顺序不符合要求。 |
| `401 Unauthorized` | 缺少或未通过服务鉴权。 |
| `403 Forbidden` | 调用方无权访问该 job。 |
| `404 Not Found` | 查询的 job 不存在。 |
| `409 Conflict` | 同一 `client_request_id` 对应的请求指纹不一致。 |
| `500 Internal Server Error` | AI 服务内部错误。 |
