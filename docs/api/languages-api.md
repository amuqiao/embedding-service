# Languages API

本文面向调用方，定义 AI 服务语种目录接口的对接合同。翻译 Job 的 `source_language` 和 `target_language` 都应使用本接口返回的 `language` 值。

## 联调参数配置

以下配置区用于双方联调时填写环境地址和密钥。不要把真实生产密钥提交到仓库；交付文档中的密钥应使用占位符，由安全渠道另行下发。

### 测试环境

| 项 | 示例值 | 说明 |
|---|---|---|
| Base URL | `https://test-ai.example.com` | 测试环境 AI 服务地址 |
| API Prefix | `/api/v1/ai-jobs` | 本文接口前缀 |
| `SERVICE_API_KEY` | `<TEST_SERVICE_API_KEY>` | 用于 `Authorization: Bearer <SERVICE_API_KEY>` |
| `X-AI-Service-Caller-ID` | `cms-test` | 可选；不传时服务使用 `default` |
| `X-Request-ID` | `test-languages-001` | 可选；单次请求追踪 ID |

### 生产环境

| 项 | 示例值 | 说明 |
|---|---|---|
| Base URL | `https://ai.example.com` | 生产环境 AI 服务地址 |
| API Prefix | `/api/v1/ai-jobs` | 本文接口前缀 |
| `SERVICE_API_KEY` | `<PROD_SERVICE_API_KEY>` | 用于 `Authorization: Bearer <SERVICE_API_KEY>` |
| `X-AI-Service-Caller-ID` | `cms` | 可选；不传时服务使用 `default` |
| `X-Request-ID` | `prod-languages-001` | 可选；单次请求追踪 ID |

请求头：

```http
Authorization: Bearer <SERVICE_API_KEY>
X-AI-Service-Caller-ID: <caller-id>
X-Request-ID: <request-id>
Content-Type: application/json
```

## 接口说明

`GET /languages` 返回 AI 服务当前可用的服务级基础语种目录。当前接口不接收 `job_type`，也不按具体 Job 过滤语种。

```text
调用方
  -> GET /api/v1/ai-jobs/languages
  -> 读取 data.languages[].language
  -> 创建翻译 Job 时填入 source_language / target_language
```

`language` 是唯一程序化键；`display_name` 和 `native_name` 只用于界面展示，调用方不能基于展示名称做业务判断。

## Method / Path

```http
GET /api/v1/ai-jobs/languages
```

Query 参数：无。

## Success Response

```json
{
  "code": "0",
  "msg": "success",
  "data": {
    "languages": [
      {
        "language": "zh",
        "display_name": "Chinese (Simplified)",
        "native_name": "中文（简体）"
      },
      {
        "language": "en",
        "display_name": "English",
        "native_name": "English"
      },
      {
        "language": "in",
        "display_name": "Indonesian",
        "native_name": "Bahasa Indonesia"
      }
    ]
  },
  "request_id": "test-languages-001",
  "server_time": "2026-08-12T10:00:00+00:00"
}
```

## Response Fields

| 字段 | 类型 | 说明 |
|---|---:|---|
| `code` | string | `"0"` 表示请求成功 |
| `msg` | string | 响应消息 |
| `data.languages` | array | 可用语种列表 |
| `languages[].language` | string | 提交任务时使用的语种代码 |
| `languages[].display_name` | string | 英文展示名称 |
| `languages[].native_name` | string | 本地语言展示名称 |
| `request_id` | string | 请求追踪 ID |
| `server_time` | string | 服务端响应时间，ISO 8601 |

## 当前语种列表

| `language` | `display_name` | `native_name` | 说明 |
|---|---|---|---|
| `zh` | `Chinese (Simplified)` | `中文（简体）` | 简体中文；业务基础语言 |
| `zh-TW` | `Chinese (Traditional)` | `繁體中文` | 繁体中文 |
| `en` | `English` | `English` | 英语 |
| `es` | `Spanish` | `Español` | 西班牙语 |
| `pt` | `Portuguese` | `Português` | 葡萄牙语 |
| `in` | `Indonesian` | `Bahasa Indonesia` | 印尼语；三方合同使用 `in`，不使用 `id` |
| `th` | `Thai` | `ไทย` | 泰语 |
| `de` | `German` | `Deutsch` | 德语 |
| `fr` | `French` | `Français` | 法语 |
| `hi` | `Hindi` | `हिन्दी` | 印地语 |
| `fil` | `Filipino` | `Filipino` | 菲律宾语 |
| `tr` | `Turkish` | `Türkçe` | 土耳其语 |
| `ko` | `Korean` | `한국어` | 韩语 |
| `ja` | `Japanese` | `日本語` | 日语 |
| `ru` | `Russian` | `Русский` | 俄语 |
| `ar` | `Arabic` | `العربية` | 阿拉伯语 |
| `it` | `Italian` | `Italiano` | 意大利语 |
| `pl` | `Polish` | `Polski` | 波兰语 |
| `ro` | `Romanian` | `Română` | 罗马尼亚语 |
| `cs` | `Czech` | `Čeština` | 捷克语 |
| `bg` | `Bulgarian` | `Български` | 保加利亚语 |
| `vi` | `Vietnamese` | `Tiếng Việt` | 越南语 |

## Error Response

鉴权失败示例：

```json
{
  "code": "200001",
  "msg": "missing or invalid service token",
  "data": null,
  "request_id": "test-languages-001",
  "server_time": "2026-08-12T10:00:00+00:00"
}
```

非法 `X-Request-ID` 示例：

```json
{
  "code": "100002",
  "msg": "invalid request id",
  "data": {
    "header": "X-Request-ID",
    "allowed": "ASCII letters, digits, dot, underscore, colon, and hyphen; length 1-128"
  },
  "request_id": "e1f5b0a7c30c4d5d9d6912b66c5ac001",
  "server_time": "2026-08-12T10:00:00+00:00"
}
```

## 错误码

| Reason | HTTP | 场景 | Retryable |
|---|---:|---|---:|
| `UNAUTHORIZED` | 401 | 缺少或错误的 Bearer token | no |
| `FORBIDDEN` | 403 | caller 被拒绝访问 | no |
| `REQUEST_ID_INVALID` | 400 | `X-Request-ID` 格式非法 | no |
| `INTERNAL_ERROR` | 500 | 服务端未预期错误 | no |

## Curl 示例

```bash
curl -sS -X GET "https://test-ai.example.com/api/v1/ai-jobs/languages" \
  -H "Authorization: Bearer <TEST_SERVICE_API_KEY>" \
  -H "X-AI-Service-Caller-ID: cms-test" \
  -H "X-Request-ID: test-languages-001"
```

## 对接规则

- 调用方应直接保存并传递 `language` 字符串，不维护额外映射表。
- 翻译 Job 的 `target_language` 必须来自本接口返回的 `language`。
- 翻译 Job 的 `source_language` 如果传入，也必须来自本接口返回的 `language`。
- `display_name` 和 `native_name` 只用于展示。
- `in` 是三方合同中的印尼语代码；不要在调用 AI 服务时转换为 `id`。
- 当前接口不接收 `job_type`。如果未来某个 Job 需要按任务过滤语种，应通过新合同版本或兼容扩展明确说明。

