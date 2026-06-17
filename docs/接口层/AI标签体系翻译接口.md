# AI 提供 RS 标签翻译接口

本文面向 RS 调用方，定义 RS 如何通过通用 AI Job 接口提交标签名和标签定义翻译任务，并通过轮询获取翻译结果。

## 接口边界

RS 是标签体系的拥有方。RS 调用本接口时只提交需要翻译的标签列表，不提交分类结构体、互斥规则或完整标签体系快照。

RS 负责：

- 提供待翻译标签的 `label_id`、源语种、目标语种、展示名和定义。
- 创建翻译 Job，保存 `job_id`。
- 通过轮询读取成功终态的 `result.artifacts[]`。
- 保存和分发翻译结果。

AI 负责：

- 异步翻译每个标签的 `display_name` 和 `definition`。
- 保持 `label_id` 不变。
- 按每个标签自己的 `target_languages` 返回 `langs`。

AI 不负责保存、分发或切换 RS 标签库。本接口只通过轮询获取结果，不使用 callback。

## Schema 合同

本接口接入统一 AI Job 壳。公共 Job 壳只负责 `client_request_id`、`job_type`、`metadata`、`options`、状态、进度、错误和时间字段；标签翻译专属结构只允许放在：

| 数据类型 | 位置 | Schema |
| --- | --- | --- |
| 创建任务参数 | `job_params` | `TagSchemaTranslationParams` |
| 成功结果 | `JobView.result` | `TagSchemaTranslationResult` |
| 失败信息 | `JobView.error` | 通用 `JobError` |

创建请求先校验通用 `CreateJobRequest`，再根据 `job_type=short_drama.tag_schema.translation` 校验 `job_params`。查询响应先校验通用 `JobView` 状态组合，再在 `succeeded` 状态下校验 `result` 是否满足 `TagSchemaTranslationResult`。

本任务不支持 callback。即使请求外壳支持 `callback` 字段，标签体系翻译 job 也必须拒绝 callback 配置。

## 基础接口

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `POST` | `/api/v1/ai-jobs/jobs` | 创建标签翻译 Job。 |
| `GET` | `/api/v1/ai-jobs/jobs/{job_id}` | 查询标签翻译 Job 状态和结果。 |

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
  "client_request_id": "rs:tag-labels:en,es,pt,ko",
  "job_type": "short_drama.tag_schema.translation",
  "job_params": {
    "labels": [
      {
        "label_id": "bihuihuigu76576585",
        "source_language": "zh",
        "target_languages": ["en", "es", "pt"],
        "display_name": "男频",
        "definition": "核心受众为男性群体，叙事视角、人物塑造、价值观以男性主角为核心..."
      },
      {
        "label_id": "bihuihuigu76576585211212",
        "source_language": "zh",
        "target_languages": ["en", "es", "ko"],
        "display_name": "女频",
        "definition": "核心受众为女性群体，叙事视角、人物塑造、情感逻辑以女性主角为核心..."
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
| `client_request_id` | `string` | 建议 | RS 幂等键。推荐包含标签批次标识和目标语种集合。 |
| `job_type` | `string` | 必需 | 固定为 `short_drama.tag_schema.translation`。 |
| `job_params` | `object` | 必需 | 标签翻译任务参数。 |
| `job_params.labels` | `object[]` | 必需 | 待翻译标签列表，必须非空。 |
| `labels[].label_id` | `string` | 必需 | 标签全局唯一 id，不翻译、不改变。 |
| `labels[].source_language` | `string` | 必需 | 当前标签源语种，使用 [language-codes.md](language-codes.md) 中的业务语种代码。 |
| `labels[].target_languages` | `string[]` | 必需 | 当前标签目标语种列表，必须非空、去重，并按业务语种顺序排列。 |
| `labels[].display_name` | `string` | 必需 | 待翻译标签名。 |
| `labels[].definition` | `string` | 必需 | 待翻译标签定义。 |
| `metadata` | `object` | 可选 | 调用方透传元数据。 |

## 创建响应

```json
{
  "job_id": "0a9be3fb-f01b-4f5d-90b5-4148c4a61df1",
  "client_request_id": "rs:tag-labels:en,es,pt,ko",
  "job_type": "short_drama.tag_schema.translation",
  "status": "queued",
  "status_url": "/api/v1/ai-jobs/jobs/0a9be3fb-f01b-4f5d-90b5-4148c4a61df1",
  "created_at": "2026-06-15T10:00:00Z"
}
```

创建请求和幂等命中请求均返回 `202 Accepted`；RS 必须以响应体中的 `job_id` 和 `status` 为准。

## 查询翻译结果

```http
GET /api/v1/ai-jobs/jobs/{job_id}
```

| status | 是否终态 | RS 处理方式 | 语义 |
| --- | --- | --- | --- |
| `queued` | 否 | 继续轮询。 | AI 已接单但尚未执行。 |
| `running` | 否 | 继续轮询，可展示 `progress`。 | AI 正在翻译或校验结果。 |
| `succeeded` | 是 | 停止轮询，读取 `result`。 | AI 已完成翻译并生成结构自洽的翻译产物。 |
| `failed` | 是 | 停止轮询，读取 `error`。 | 任务失败。 |

状态字段规则：

- 本接口不触发 callback，终态结果只通过查询接口获取。
- `queued` 和 `running` 时 `result` 和 `error` 必须为 `null`。
- `succeeded` 时 `result` 必须存在，`error` 必须为 `null`。
- `failed` 时 `error` 必须存在，`result` 必须为 `null`。

### 成功响应

```json
{
  "job_id": "0a9be3fb-f01b-4f5d-90b5-4148c4a61df1",
  "client_request_id": "rs:tag-labels:en,es,pt,ko",
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
        "label_id": "bihuihuigu76576585",
        "langs": {
          "en": {
            "name": "Male-oriented",
            "definition": "The core audience is male..."
          },
          "es": {
            "name": "Orientado a hombres",
            "definition": "La audiencia principal es masculina..."
          },
          "pt": {
            "name": "Voltado ao público masculino",
            "definition": "O público principal é masculino..."
          }
        }
      },
      {
        "label_id": "bihuihuigu76576585211212",
        "langs": {
          "en": {
            "name": "Female-oriented",
            "definition": "The core audience is female..."
          },
          "es": {
            "name": "Orientado a mujeres",
            "definition": "La audiencia principal es femenina..."
          },
          "ko": {
            "name": "여성향",
            "definition": "핵심 독자는 여성입니다..."
          }
        }
      }
    ],
    "signals": {
      "source_schema_hash": "sha256:1111111111111111111111111111111111111111111111111111111111111111",
      "translated_schemas_hash": "sha256:2222222222222222222222222222222222222222222222222222222222222222"
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

### result 字段

| 字段 | 类型 | 必需性 | 说明 |
| --- | --- | --- | --- |
| `result.artifacts` | `object[]` | 必需 | 翻译结果列表，顺序与请求 `job_params.labels[]` 一致。 |
| `artifacts[].label_id` | `string` | 必需 | 对应请求中的 `label_id`。 |
| `artifacts[].langs` | `object` | 必需 | key 为目标语种，value 为该语种翻译结果。 |
| `langs.{lang}.name` | `string` | 必需 | 翻译后的标签名。 |
| `langs.{lang}.definition` | `string` | 必需 | 翻译后的标签定义。 |
| `result.signals.source_schema_hash` | `string` | 必需 | 输入 `labels[]` 的 hash。保留历史字段名。 |
| `result.signals.translated_schemas_hash` | `string` | 必需 | 翻译结果 hash。保留历史字段名。 |

## 自洽规则

- `job_params` 必须是对象，不能是历史列表型结构。
- `labels[]` 必须非空。
- `label_id` 在同一请求中必须唯一。
- 每个标签独立表达 `source_language` 和 `target_languages`。
- `target_languages` 必须非空、去重，且每个值都必须来自 [language-codes.md](language-codes.md)。
- `target_languages` 必须按 [language-codes.md](language-codes.md) 的业务列表顺序排列。
- 成功结果的 `result.artifacts[]` 数量必须等于请求 `labels[]` 数量。
- 每个 artifact 的 `label_id` 必须与同位置请求标签一致。
- 每个 artifact 的 `langs` 必须刚好包含该标签请求的 `target_languages`。
- AI 不得新增、删除、替换或重写 `label_id`。

Mock 接口中的请求示例和查询响应示例只作为发送前数据和回复数据样例，也必须通过同一套 `CreateJobRequest + TagSchemaTranslationParams`、`JobView + TagSchemaTranslationResult` 校验；mock 接口不维护独立的标签翻译校验规则。

## 幂等

推荐幂等键：

```text
rs:tag-labels:{target_languages_csv}
```

请求指纹必须覆盖完整 `job_params.labels[]`、`metadata` 和 `options`。同一幂等键如果传入不同请求指纹，应返回 `409 CLIENT_REQUEST_ID_CONFLICT`。

## 错误码

| 错误码 | 说明 |
| --- | --- |
| `INVALID_INPUT` | 请求结构不合法。 |
| `INVALID_JOB_TYPE` | `job_type` 不合法或未注册。 |
| `CLIENT_REQUEST_ID_CONFLICT` | 同一幂等键请求内容不一致。 |
| `JOB_NOT_FOUND` | 查询的 job 不存在或无权访问。 |
| `TRANSLATION_FAILED` | 翻译过程失败或模型输出不符合合同。 |
| `JOB_TIMEOUT` | 翻译任务超时。 |
| `MODEL_CALL_FAILED` | 模型调用失败或内部处理失败。 |
