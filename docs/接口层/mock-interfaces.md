# Mock 接口说明

本文定义本服务提供的联调用假接口。Mock 接口只返回稳定假数据，用于 CPP、RS 接口联调、前端调试和对接验收，不代表真实 Job 执行、真实 RS 存储或真实模型结果。

## 接口边界

Mock 接口固定使用 `v1`，不开放 `api_version` 路径参数。

Mock 接口按调用方拆成两组 Job 接口：

```http
POST /api/v1/mock/cpp/ai-jobs/jobs
GET  /api/v1/mock/cpp/ai-jobs/jobs/{job_id}

POST /api/v1/mock/rs/ai-jobs/jobs
GET  /api/v1/mock/rs/ai-jobs/jobs/{job_id}
```

两组接口的请求和响应都复用真实 AI Job 壳；区别只在路径前缀和允许的 `job_type`。

除健康检查外，Mock 接口仍使用服务间 Bearer 鉴权：

```http
Authorization: Bearer <SERVICE_API_KEY>
X-AI-Service-Caller-ID: <caller>
```

## CPP Mock Job

CPP mock 对应 [CPP服务接口.md](CPP服务接口.md)，用于模拟短剧打标 Job。

```http
POST /api/v1/mock/cpp/ai-jobs/jobs
GET  /api/v1/mock/cpp/ai-jobs/jobs/{job_id}
```

创建接口只接受：

| job_type | 用途 |
| --- | --- |
| `short_drama.tagging.initial` | 首次短剧打标 mock。 |
| `short_drama.tagging.incremental` | 增量短剧打标 mock。 |

创建响应中的 `status_url` 会返回同组 CPP mock 查询路径：

```json
{
  "job_id": "7b5c2c62-9a3a-41b7-bd41-f24a5d34a099",
  "client_request_id": "cpp:204200150000004872:initial:20260615",
  "job_type": "short_drama.tagging.initial",
  "status": "queued",
  "status_url": "/api/v1/mock/cpp/ai-jobs/jobs/7b5c2c62-9a3a-41b7-bd41-f24a5d34a099",
  "created_at": "2026-06-15T10:00:00Z"
}
```

CPP mock 在 `status=succeeded` 时返回 `result=null`，表示 AI 已完成打标并模拟完成 RS 写入。为了便于联调，`metadata.mock_tagging` 会返回模拟作品和 RS 写入摘要；完整打标 payload 不放入 `result`，保持与 [CPP服务接口.md](CPP服务接口.md) 一致。

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
  "result": null,
  "error": null,
  "callback": {
    "status": "delivered",
    "attempts": 1,
    "next_retry_at": null,
    "last_error": null
  },
  "metadata": {
    "source_service": "cpp",
    "business_scene": "short_drama_tagging",
    "api_version": "v1",
    "mock_tagging": {
      "t_book_id": "204200150000004872",
      "title": "Acting for Real-He Fell First",
      "rs_write": {
        "saved": true,
        "source": "ai_auto",
        "category_count": 3,
        "label_count": 4
      }
    }
  },
  "created_at": "2026-06-15T10:00:00Z",
  "started_at": "2026-06-15T10:00:05Z",
  "finished_at": "2026-06-15T10:01:30Z"
}
```

## RS Mock Job

RS mock 对应 [AI标签体系翻译接口.md](AI标签体系翻译接口.md)，用于模拟标签体系翻译 Job。

```http
POST /api/v1/mock/rs/ai-jobs/jobs
GET  /api/v1/mock/rs/ai-jobs/jobs/{job_id}
```

创建接口只接受：

| job_type | 用途 |
| --- | --- |
| `short_drama.tag_schema.translation` | 标签体系翻译 mock。 |

创建响应中的 `status_url` 会返回同组 RS mock 查询路径：

```json
{
  "job_id": "0a9be3fb-f01b-4f5d-90b5-4148c4a61df1",
  "client_request_id": "rs:tag-schema-default:en",
  "job_type": "short_drama.tag_schema.translation",
  "status": "queued",
  "status_url": "/api/v1/mock/rs/ai-jobs/jobs/0a9be3fb-f01b-4f5d-90b5-4148c4a61df1",
  "created_at": "2026-06-15T10:00:00Z"
}
```

RS mock 在 `status=succeeded` 时返回翻译结果。下面示例为节选；实际响应中 `en`、`es`、`pt` 每种语言都会返回 `000001`、`000003`、`000006` 三个分类。

```json
{
  "result": {
    "artifacts": [
      {
        "key": "translated_schemas",
        "type": "json",
        "label": "翻译后的标签结构体",
        "content": [
          {
            "language": "en",
            "categories": [
              {
                "category_id": "000001",
                "name": "Audience",
                "required": true,
                "min_items": 1,
                "max_items": 1,
                "labels": [
                  {
                    "label_id": "65f0a1b2c3d4e5f6a7b8c901",
                    "name": "Male-oriented",
                    "definition": "The story is primarily written for male audiences, with the narrative viewpoint and character arcs centered on a male lead."
                  },
                  {
                    "label_id": "65f0a1b2c3d4e5f6a7b8c902",
                    "name": "Female-oriented",
                    "definition": "The story is primarily written for female audiences, with the narrative viewpoint, characterization, and emotional logic centered on a female lead."
                  }
                ]
              }
            ]
          },
          {
            "language": "es",
            "categories": [
              {
                "category_id": "000003",
                "name": "Genero",
                "required": true,
                "min_items": 1,
                "max_items": 3,
                "labels": [
                  {
                    "label_id": "65f0a1b2c3d4e5f6a7b8c9f1",
                    "name": "Etica familiar",
                    "definition": "Se centra en relaciones, responsabilidades, conflictos, reconciliacion o traicion dentro de una familia comun."
                  },
                  {
                    "label_id": "65f0a1b2c3d4e5f6a7b8c9f2",
                    "name": "Suspenso sobrenatural",
                    "definition": "Crea miedo y tension mediante sucesos extranos, historias de fantasmas, maldiciones o senales sobrenaturales."
                  }
                ]
              }
            ]
          },
          {
            "language": "pt",
            "categories": [
              {
                "category_id": "000006",
                "name": "Emocao",
                "required": true,
                "min_items": 1,
                "max_items": 3,
                "labels": [
                  {
                    "label_id": "65f0a1b2c3d4e5f6a7b8ca01",
                    "name": "Sofrimento",
                    "definition": "Cria deliberadamente tristeza, repressao, injustica ou ferida emocional."
                  },
                  {
                    "label_id": "65f0a1b2c3d4e5f6a7b8ca02",
                    "name": "Vinganca satisfatoria",
                    "definition": "Cria prazer por meio de contra-ataque, virada, punicao dos ofensores ou compensacao."
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
            "label_id": "65f0a1b2c3d4e5f6a7b8c9f1",
            "mutex_label_ids": ["65f0a1b2c3d4e5f6a7b8c9f2"]
          },
          {
            "label_id": "65f0a1b2c3d4e5f6a7b8c9f2",
            "mutex_label_ids": ["65f0a1b2c3d4e5f6a7b8c9f1"]
          }
        ]
      }
    ],
    "signals": {
      "source_schema_hash": "sha256:387d2f3bb1b89bccf00bb9939d81c3b3a41054c03af607a2a09da683f8dc576d",
      "translated_schemas_hash": "sha256:12f54f8e0d5055cf0b3bbe0780667c3b952aa9c175d14fdcac8c21f91821f9ac"
    }
  }
}
```

## 状态模拟

查询接口可通过 `status` query 参数模拟不同状态：

| 参数 | 允许值 | 默认值 |
| --- | --- | --- |
| `status` | `queued`、`running`、`succeeded`、`failed` | `succeeded` |

示例：

```http
GET /api/v1/mock/cpp/ai-jobs/jobs/{job_id}?status=running
GET /api/v1/mock/rs/ai-jobs/jobs/{job_id}?status=failed
```

CPP mock `failed` 时返回 `RS_RESULT_WRITE_FAILED`，RS mock `failed` 时返回 `INVALID_SOURCE_SCHEMA`；不支持的 `job_type` 返回 `INVALID_JOB_TYPE`。

## 不再暴露的内部 Fixture

以下路径不作为对外 mock 接口暴露：

```http
GET  /api/v1/mock/tag-schemas/default
POST /api/v1/mock/ai-tag-results
POST /api/v1/mock/ai-jobs/jobs
```

这些能力如果后续仍需要，应作为内部验证夹具单独设计，不混入 CPP/RS 调 AI 的 mock 接口面。
