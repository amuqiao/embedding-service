# Mock 接口说明

本文定义本服务提供的联调用假接口。Mock 接口只返回稳定假数据，用于 CPP、RS 接口联调、前端调试和对接验收，不代表真实 Job 执行、真实 RS 存储或真实模型结果。

开发环境地址、鉴权 header 和 Swagger UI 使用方式见 [联调配置与开发环境鉴权](联调配置与开发环境鉴权.md)。

## 接口边界

Mock 接口固定使用 `v1`，不开放 `api_version` 路径参数。

Mock 接口由 `ENABLE_MOCK_INTERFACES` 控制。本地联调默认开启；上线后应设置为 `false`，避免暴露 mock tool。

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

RS mock 用于模拟 RS 调 AI 的多标签翻译 Job。

```http
POST /api/v1/mock/rs/ai-jobs/jobs
GET  /api/v1/mock/rs/ai-jobs/jobs/{job_id}
```

创建接口只接受：

| job_type | 用途 |
| --- | --- |
| `short_drama.tag_labels.translation` | 多标签翻译 mock。 |

创建请求的 `job_params` 必须是对象，不再接受历史列表型 `job_params`。多标签列表放在 `job_params.labels`，每个标签项保持独立完整结构，包含 `label_id`、`source_language`、`target_languages`、`display_name` 和 `definition`。

```json
{
  "client_request_id": "rs:tag-labels:batch:20260617",
  "job_type": "short_drama.tag_labels.translation",
  "job_params": {
    "labels": [
      {
        "label_id": "65f0a1b2c3d4e5f6a7b8c901",
        "source_language": "zh",
        "target_languages": ["en", "es", "pt"],
        "display_name": "男频",
        "definition": "核心受众为男性群体，叙事视角、人物塑造、价值观以男性主角为核心。"
      },
      {
        "label_id": "65f0a1b2c3d4e5f6a7b8c902",
        "source_language": "zh",
        "target_languages": ["en", "es", "ko"],
        "display_name": "女频",
        "definition": "核心受众为女性群体，叙事视角、人物塑造、情感逻辑以女性主角为核心。"
      }
    ]
  },
  "metadata": {
    "source_service": "rs",
    "business_scene": "tag_labels_translation"
  }
}
```

创建响应中的 `status_url` 会返回同组 RS mock 查询路径：

```json
{
  "job_id": "0a9be3fb-f01b-4f5d-90b5-4148c4a61df1",
  "client_request_id": "rs:tag-labels:batch:20260617",
  "job_type": "short_drama.tag_labels.translation",
  "status": "queued",
  "status_url": "/api/v1/mock/rs/ai-jobs/jobs/0a9be3fb-f01b-4f5d-90b5-4148c4a61df1",
  "created_at": "2026-06-15T10:00:00Z"
}
```

RS mock 在 `status=succeeded` 时返回翻译结果。结果兼容通用 `JobResult`：业务数组放在 `result.artifacts[0].content`。

```json
{
  "result": {
    "artifacts": [
      {
        "key": "translated_labels",
        "type": "json",
        "label": "翻译后的标签",
        "content": [
          {
            "label_id": "65f0a1b2c3d4e5f6a7b8c901",
            "langs": {
              "en": {
                "name": "Male-oriented",
                "definition": "The story is primarily written for male audiences, with the narrative viewpoint and character arcs centered on a male lead."
              },
              "es": {
                "name": "Orientado a hombres",
                "definition": "La historia se dirige principalmente a una audiencia masculina, con el punto de vista narrativo y los arcos de personajes centrados en un protagonista masculino."
              },
              "pt": {
                "name": "Voltado ao publico masculino",
                "definition": "A historia e voltada principalmente ao publico masculino, com o ponto de vista narrativo e os arcos dos personagens centrados em um protagonista homem."
              }
            }
          },
          {
            "label_id": "65f0a1b2c3d4e5f6a7b8c902",
            "langs": {
              "en": {
                "name": "Female-oriented",
                "definition": "The story is primarily written for female audiences, with the narrative viewpoint, characterization, and emotional logic centered on a female lead."
              },
              "es": {
                "name": "Orientado a mujeres",
                "definition": "La historia se dirige principalmente a una audiencia femenina, con el punto de vista, la caracterización y la lógica emocional centrados en una protagonista femenina."
              },
              "ko": {
                "name": "여성향",
                "definition": "핵심 독자는 여성이며, 서사 시점과 인물 설정, 감정선이 여성 주인공을 중심으로 전개됩니다."
              }
            }
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

CPP mock `failed` 时返回 `MODEL_OUTPUT_INVALID`，RS mock `failed` 时返回 `INVALID_LABEL_TRANSLATION_INPUT`；不支持的 `job_type` 返回 `INVALID_JOB_TYPE`。

## 不再暴露的内部 Fixture

以下路径不作为对外 mock 接口暴露：

```http
GET  /api/v1/mock/tag-schemas/default
POST /api/v1/mock/ai-tag-results
POST /api/v1/mock/ai-jobs/jobs
```

这些能力如果后续仍需要，应作为内部验证夹具单独设计，不混入 CPP/RS 调 AI 的 mock 接口面。
