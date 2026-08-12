# Tagged Text Translation API

本文面向调用方，定义批量带标签文案翻译 Job 的对接合同。该 Job 通过统一异步 Job 接口提交，调用方通过轮询查询结果，也可以接收终态 Callback。

## 联调参数配置

以下配置区用于双方联调时填写环境地址和密钥。不要把真实生产密钥提交到仓库；交付文档中的密钥应使用占位符，由安全渠道另行下发。

### 测试环境

| 项 | 示例值 | 说明 |
|---|---|---|
| Base URL | `https://test-ai.example.com` | 测试环境 AI 服务地址 |
| API Prefix | `/api/v1/ai-jobs` | 本文所有 AI Job 接口前缀 |
| `SERVICE_API_KEY` | `<TEST_SERVICE_API_KEY>` | 用于 `Authorization: Bearer <SERVICE_API_KEY>` |
| `CALLBACK_SIGNING_SECRET` | `<TEST_CALLBACK_SIGNING_SECRET>` | 用于调用方校验 AI 服务投递的 Callback 签名 |
| `X-AI-Service-Caller-ID` | `cms-test` | 可选；不传时服务使用 `default` |
| `X-Request-ID` | `test-translate-001` | 可选；单次请求追踪 ID |

### 生产环境

| 项 | 示例值 | 说明 |
|---|---|---|
| Base URL | `https://ai.example.com` | 生产环境 AI 服务地址 |
| API Prefix | `/api/v1/ai-jobs` | 本文所有 AI Job 接口前缀 |
| `SERVICE_API_KEY` | `<PROD_SERVICE_API_KEY>` | 用于 `Authorization: Bearer <SERVICE_API_KEY>` |
| `CALLBACK_SIGNING_SECRET` | `<PROD_CALLBACK_SIGNING_SECRET>` | 用于调用方校验 AI 服务投递的 Callback 签名 |
| `X-AI-Service-Caller-ID` | `cms` | 可选；不传时服务使用 `default` |
| `X-Request-ID` | `prod-translate-001` | 可选；单次请求追踪 ID |

请求头：

```http
Authorization: Bearer <SERVICE_API_KEY>
X-AI-Service-Caller-ID: <caller-id>
X-Request-ID: <request-id>
Content-Type: application/json
```

`X-AI-Service-Caller-ID` 是可选调用方标识，不是多租户安全边界。`X-Request-ID` 允许 1 到 128 个 ASCII 字母、数字、点号、下划线、冒号或连字符；不传时服务端生成。

## 合同状态

| 项 | 内容 |
|---|---|
| `job_type` | `tagged_text_translation` |
| 文档状态 | 待实现对接合同 |
| 接口形态 | 复用统一 Job 创建、查询和 Callback 合同 |
| Prompt 暴露 | 不暴露 Prompt 查询接口；Prompt 是服务内部实现细节 |

本文定义的是拟新增翻译 Job 的调用方合同。当前代码尚未注册 `tagged_text_translation`；上线前必须以实现、schema、registry 和合同测试为准完成校验。未上线环境直接按本文创建任务会返回 `INVALID_JOB_TYPE`。

## 整体模型

调用方一次提交一个批量翻译 Job。单条翻译也是批量的特例，只需要在 `items` 中放 1 条。

```text
调用方
  -> POST /jobs 创建 tagged_text_translation Job
  -> GET /jobs/{job_id} 轮询状态
  -> succeeded 后读取 job_result.items[]

可选：
  -> 创建 Job 时传 callback.url
  -> 服务在终态后投递 job.succeeded 或 job.failed Callback
```

输入文案可以包含 HTML 标签或固定占位符。服务只翻译自然语言文本，必须保留标签、占位符和 item 顺序。`max_target_chars_hint` 是目标语种可见文本字符数建议，不包含保留的 HTML 标签和固定占位符；它不是硬失败条件。

## 创建翻译 Job

本节是 `tagged_text_translation` 上线后的请求合同。当前未注册该 `job_type` 的环境不能直接用于联调创建任务。

### Method / Path

```http
POST /api/v1/ai-jobs/jobs
```

### Request Body

```json
{
  "client_request_id": "cms-translate-20260812-001",
  "job_type": "tagged_text_translation",
  "job_params": {
    "source_language": "en",
    "target_language": "zh",
    "items": [
      {
        "id": "homepage.title",
        "text": "<span>Hello {user_name}, welcome back!</span>",
        "max_target_chars_hint": 30
      },
      {
        "id": "homepage.description",
        "text": "<p>Your order {{order_id}} is ready.</p>",
        "max_target_chars_hint": 50
      }
    ]
  },
  "callback": {
    "url": "https://cms.example.com/ai-callbacks/translation",
    "events": ["job.succeeded", "job.failed"]
  },
  "metadata": {
    "scene": "cms",
    "batch_id": "homepage-i18n-001"
  },
  "options": {
    "priority": "normal",
    "idempotency_mode": "return_existing"
  }
}
```

### Job Params

| 字段 | 类型 | 必填 | 说明 |
|---|---:|---:|---|
| `source_language` | string | 否 | 源语种；不传时由模型识别。若传入，必须使用语种接口返回的 `language` 值 |
| `target_language` | string | 是 | 目标语种；必须使用语种接口返回的 `language` 值 |
| `items` | array | 是 | 待翻译条目列表；单条翻译也传 1 个 item |

### Translation Item

| 字段 | 类型 | 必填 | 说明 |
|---|---:|---:|---|
| `id` | string | 是 | 调用方传入的批量条目标识；响应原样返回，用于回填 CMS 字段 |
| `text` | string | 是 | 待翻译文案；可包含 HTML 标签、`{name}`、`{{name}}` 等固定占位符 |
| `max_target_chars_hint` | integer | 否 | 目标译文可见文本字符数建议；服务尽量遵守，但不因超过该值直接失败 |

规则：

- `items` 必须至少包含 1 条。
- 同一个请求内 `items[].id` 必须唯一；重复时请求应以 `INVALID_JOB_PARAMS` 失败。
- `id` 可以是 CMS 字段路径、数据库 ID、数组序号字符串或调用方自定义字符串。
- `id` 是调用方自带、仅用于本次批量内 item 对齐的 opaque identifier，不是服务生成的资源 ID，也不要求跨 Job 全局唯一。
- 服务必须保持 `items` 语义一一对应；调用方以 `id` 归并结果，不依赖数组下标。
- HTML 标签、属性名、占位符、变量名和模板标记不能被翻译、删除或重命名。
- 翻译质量、标签保留和占位符保留优先级高于字符数建议。
- 第一版不承诺部分成功结果；`job_status=succeeded` 表示全部 item 翻译成功，`job_status=failed` 表示本次批量任务失败。

### Success Response

创建成功表示服务已经接单，不表示翻译已经完成。

```json
{
  "code": "0",
  "msg": "success",
  "data": {
    "job": {
      "job_id": "018f9a7f-2b7d-7a0a-9f24-6ff0b87c8b91",
      "client_request_id": "cms-translate-20260812-001",
      "job_type": "tagged_text_translation",
      "job_status": "queued",
      "job_progress": {
        "percent": 0,
        "stage": "accepted",
        "message": "accepted"
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
      "created_at": "2026-08-12T10:00:00+00:00",
      "updated_at": "2026-08-12T10:00:00+00:00",
      "finished_at": null
    }
  },
  "request_id": "test-translate-001",
  "server_time": "2026-08-12T10:00:00+00:00"
}
```

## 轮询查询 Job

### Method / Path

```http
GET /api/v1/ai-jobs/jobs/{job_id}
```

### Polling Example

```bash
curl -sS -X GET "https://test-ai.example.com/api/v1/ai-jobs/jobs/018f9a7f-2b7d-7a0a-9f24-6ff0b87c8b91" \
  -H "Authorization: Bearer <TEST_SERVICE_API_KEY>" \
  -H "X-AI-Service-Caller-ID: cms-test" \
  -H "X-Request-ID: test-translate-poll-001"
```

### Job Status

| 状态 | 说明 | `job_result` |
|---|---|---|
| `queued` | 已接单，尚未执行 | `null` |
| `running` | 执行中 | 默认 `null`；是否返回增量快照以上线实现为准 |
| `succeeded` | 翻译成功 | 返回完整翻译结果 |
| `failed` | 翻译失败 | 默认 `null`；失败详情见 `job_error` |

调用方只能用 `job_status` 判断 Job 状态。`job_progress.percent` 只用于展示，不能作为成功或失败依据。

第一版对接合同不承诺 `running` 状态返回部分 `job_result`。调用方应在 `succeeded` 终态读取完整结果。

### Succeeded Response Example

```json
{
  "code": "0",
  "msg": "success",
  "data": {
    "job": {
      "job_id": "018f9a7f-2b7d-7a0a-9f24-6ff0b87c8b91",
      "client_request_id": "cms-translate-20260812-001",
      "job_type": "tagged_text_translation",
      "job_status": "succeeded",
      "job_progress": {
        "percent": 100,
        "stage": "completed",
        "message": "completed"
      },
      "job_result": {
        "source_language": "en",
        "target_language": "zh",
        "items": [
          {
            "id": "homepage.title",
            "source_text": "<span>Hello {user_name}, welcome back!</span>",
            "translated_text": "<span>你好 {user_name}，欢迎回来！</span>",
            "char_count": {
              "source": 46,
              "target": 31,
              "target_limit_hint": 30,
              "within_hint": false
            }
          },
          {
            "id": "homepage.description",
            "source_text": "<p>Your order {{order_id}} is ready.</p>",
            "translated_text": "<p>你的订单 {{order_id}} 已准备好。</p>",
            "char_count": {
              "source": 40,
              "target": 32,
              "target_limit_hint": 50,
              "within_hint": true
            }
          }
        ]
      },
      "job_error": null,
      "cost": {
        "currency": "USD",
        "amount": "0.001200",
        "final": true
      },
      "usage": {
        "ai_call_count": 1,
        "total_tokens": 820,
        "final": true
      },
      "callback": {
        "status": "delivered",
        "attempt": 1,
        "last_error": null,
        "next_retry_at": null
      },
      "status_url": "/api/v1/ai-jobs/jobs/018f9a7f-2b7d-7a0a-9f24-6ff0b87c8b91",
      "created_at": "2026-08-12T10:00:00+00:00",
      "updated_at": "2026-08-12T10:00:12+00:00",
      "finished_at": "2026-08-12T10:00:12+00:00"
    }
  },
  "request_id": "test-translate-poll-001",
  "server_time": "2026-08-12T10:00:13+00:00"
}
```

### Job Result

| 字段 | 类型 | 说明 |
|---|---:|---|
| `source_language` | string 或 null | 实际使用的源语种；未指定且无法可靠识别时可为 `null` |
| `target_language` | string | 目标语种 |
| `items` | array | 翻译结果列表 |

### Result Item

| 字段 | 类型 | 说明 |
|---|---:|---|
| `id` | string | 原请求 item 的批量条目标识 |
| `source_text` | string | 原请求文本 |
| `translated_text` | string | 翻译后的文本；应保留原标签和占位符 |
| `char_count.source` | integer | 源文本字符数 |
| `char_count.target` | integer | 译文可见文本字符数，不包含保留的 HTML 标签和固定占位符 |
| `char_count.target_limit_hint` | integer 或 null | 原请求字符数建议 |
| `char_count.within_hint` | boolean 或 null | 是否满足字符数建议；未传建议时为 `null` |

## Callback

创建 Job 时传入 `callback.url` 后，服务会在 Job 终态投递 Callback。Callback payload 不套 HTTP envelope。

### Callback Headers

```http
Content-Type: application/json
X-Callback-Timestamp: 2026-08-12T10:00:12+00:00
X-Callback-Signature: sha256=<hex>
```

签名内容是：

```text
timestamp + "." + raw_body
```

签名算法是 HMAC-SHA256，密钥是双方按环境约定的 `CALLBACK_SIGNING_SECRET`。调用方应校验签名、时间戳和 `event_id`，防止伪造与重放。

### Callback Payload Example

```json
{
  "event": "job.succeeded",
  "event_id": "018f9a7f-2b7d-5a0a-9f24-6ff0b87c8b92",
  "attempt": 1,
  "sent_at": "2026-08-12T10:00:12+00:00",
  "trigger_request_id": "test-translate-001",
  "caller_id": "cms-test",
  "job": {
    "job_id": "018f9a7f-2b7d-7a0a-9f24-6ff0b87c8b91",
    "client_request_id": "cms-translate-20260812-001",
    "job_type": "tagged_text_translation",
    "job_status": "succeeded",
    "job_progress": {
      "percent": 100,
      "stage": "completed",
      "message": "completed"
    },
    "job_result": {
      "source_language": "en",
      "target_language": "zh",
      "items": [
        {
          "id": "homepage.title",
          "source_text": "<span>Hello {user_name}, welcome back!</span>",
          "translated_text": "<span>你好 {user_name}，欢迎回来！</span>",
          "char_count": {
            "source": 46,
            "target": 31,
            "target_limit_hint": 30,
            "within_hint": false
          }
        }
      ]
    },
    "job_error": null,
    "cost": {
      "currency": "USD",
      "amount": "0.001200",
      "final": true
    },
    "usage": {
      "ai_call_count": 1,
      "total_tokens": 820,
      "final": true
    },
    "callback": {
      "status": "pending",
      "attempt": 0,
      "last_error": null,
      "next_retry_at": null
    },
    "status_url": "/api/v1/ai-jobs/jobs/018f9a7f-2b7d-7a0a-9f24-6ff0b87c8b91",
    "created_at": "2026-08-12T10:00:00+00:00",
    "updated_at": "2026-08-12T10:00:12+00:00",
    "finished_at": "2026-08-12T10:00:12+00:00"
  }
}
```

### Callback Ack

调用方接受 Callback 时必须返回 `2xx`、`Content-Type: application/json`，body 必须是：

```json
{
  "accepted": true,
  "msg": "ok",
  "details": {}
}
```

`204`、空 body、非 JSON body、缺少 `accepted`、`accepted` 不是 boolean 或 `accepted=false` 都视为未接受，会触发重试直到成功或达到最大尝试次数。

## Failed Job Example

```json
{
  "code": "0",
  "msg": "success",
  "data": {
    "job": {
      "job_id": "018f9a7f-2b7d-7a0a-9f24-6ff0b87c8b91",
      "client_request_id": "cms-translate-20260812-001",
      "job_type": "tagged_text_translation",
      "job_status": "failed",
      "job_progress": {
        "percent": 100,
        "stage": "failed",
        "message": "failed"
      },
      "job_result": null,
      "job_error": {
        "reason": "MODEL_OUTPUT_INVALID",
        "details": {
          "message": "model output did not preserve required placeholders"
        },
        "retryable": false
      },
      "cost": null,
      "usage": null,
      "callback": {
        "status": "pending",
        "attempt": 0,
        "last_error": null,
        "next_retry_at": null
      },
      "status_url": "/api/v1/ai-jobs/jobs/018f9a7f-2b7d-7a0a-9f24-6ff0b87c8b91",
      "created_at": "2026-08-12T10:00:00+00:00",
      "updated_at": "2026-08-12T10:00:12+00:00",
      "finished_at": "2026-08-12T10:00:12+00:00"
    }
  },
  "request_id": "test-translate-poll-002",
  "server_time": "2026-08-12T10:00:13+00:00"
}
```

## HTTP Error Example

```json
{
  "code": "100012",
  "msg": "invalid job_params",
  "data": {
    "field": "job_params.items",
    "reason": "items must contain at least one translation item"
  },
  "request_id": "test-translate-001",
  "server_time": "2026-08-12T10:00:00+00:00"
}
```

## 错误码

| Reason | HTTP | 场景 | Retryable |
|---|---:|---|---:|
| `UNAUTHORIZED` | 401 | 缺少或错误的 Bearer token | no |
| `FORBIDDEN` | 403 | caller 无权访问该 Job | no |
| `REQUEST_ID_INVALID` | 400 | `X-Request-ID` 格式非法 | no |
| `INVALID_JOB_TYPE` | 400 | `job_type` 未注册或当前环境不允许外部提交 | no |
| `INVALID_JOB_PARAMS` | 400 | `job_params` 缺少必填字段、字段类型错误或语种不支持 | no |
| `CLIENT_REQUEST_ID_CONFLICT` | 409 | 同一 caller 下重复 `client_request_id` 但请求内容不一致 | no |
| `QUEUE_FULL` | 503 | 服务当前接单容量已满 | yes |
| `MODEL_NOT_AVAILABLE` | 400 | 翻译 Job 配置的模型不可用 | no |
| `MODEL_CALL_FAILED` | 502 | 调用模型失败 | yes |
| `MODEL_CALL_TIMEOUT` | 504 | 调用模型超时 | yes |
| `MODEL_OUTPUT_INVALID` | 502 | 模型输出不符合翻译结果合同，例如破坏占位符或 JSON 结构 | no |
| `JOB_TIMEOUT` | 504 | Job 执行超时 | yes |
| `JOB_EXECUTION_FAILED` | 500 | 未归类的 Job 执行失败 | no |
| `JOB_NOT_FOUND` | 404 | 查询的 Job 不存在或不属于当前 caller | no |

错误响应中的 `code` 是数字错误码，`msg` 是错误消息；表中的 Reason 是服务内部和 Job error 中使用的稳定错误原因。调用方做业务分支时优先根据 HTTP status、`job_status` 和 `job_error.reason` 处理。

## Curl 示例

以下示例用于说明上线后的调用方式。当前未注册 `tagged_text_translation` 的环境不能直接用于联调创建任务。

### 创建任务

```bash
curl -sS -X POST "https://test-ai.example.com/api/v1/ai-jobs/jobs" \
  -H "Authorization: Bearer <TEST_SERVICE_API_KEY>" \
  -H "X-AI-Service-Caller-ID: cms-test" \
  -H "X-Request-ID: test-translate-create-001" \
  -H "Content-Type: application/json" \
  -d '{
    "client_request_id": "cms-translate-20260812-001",
    "job_type": "tagged_text_translation",
    "job_params": {
      "source_language": "en",
      "target_language": "zh",
      "items": [
        {
          "id": "homepage.title",
          "text": "<span>Hello {user_name}, welcome back!</span>",
          "max_target_chars_hint": 30
        }
      ]
    },
    "options": {
      "priority": "normal",
      "idempotency_mode": "return_existing"
    }
  }'
```

### 查询任务

```bash
curl -sS -X GET "https://test-ai.example.com/api/v1/ai-jobs/jobs/<job_id>" \
  -H "Authorization: Bearer <TEST_SERVICE_API_KEY>" \
  -H "X-AI-Service-Caller-ID: cms-test" \
  -H "X-Request-ID: test-translate-poll-001"
```
