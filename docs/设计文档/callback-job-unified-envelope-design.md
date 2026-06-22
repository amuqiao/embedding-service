# Callback 与 Job 统一响应信封架构设计文档

```text
Version: 1.0.4
Status: Proposed / Development Baseline Candidate
Date: 2026-06-21
Base Standard: FastAPI 统一响应信封架构设计文档 v1.0.1
Change Policy: 进入开发后，任何 HTTP 外层字段、JobEnvelope 字段、CallbackEnvelope 字段、Job 状态语义、Callback 投递语义、错误码映射、OpenAPI 展示结构或轮询/Callback 一致性规则变更都必须升版本并记录变更原因。
Change Reason: 简化 Callback 第一阶段请求头，只保留 Content-Type、时间戳和签名头；event、event_id 以 payload 字段为准，secret 版本和签名协议版本不进入 MVP HTTP 合同。
```

本文定义本项目 Job 与 Callback 在统一 HTTP 响应信封体系下的外部合同。核心目标是把三层结构分清：HTTP 请求结果使用 `HttpEnvelope` 或 `ErrorEnvelope`，异步任务状态使用 `JobEnvelope`，终态通知使用 `CallbackEnvelope`。

## 1. 文档职责

本文负责定义：

- `POST /jobs` 与 `GET /jobs/{job_id}` 在统一 HTTP 响应信封下的返回结构。
- Job 创建、排队、运行、成功、失败各状态的外部表达。
- Job 执行失败与 HTTP 请求失败的边界。
- Callback 事件信封的结构、触发条件、幂等语义和安全边界。
- 轮询结果与 Callback payload 的一致性要求。
- `job_type` 扩展时不得破坏的公共外壳。

本文不负责：

- 具体 `job_type` 的业务参数和业务结果字段设计。
- Taskiq、数据库、Attempt、Lease、Reconciler、Callback outbox 的完整内部状态机实现。
- Callback 接收方的业务处理逻辑。
- 生产部署、监控平台、签名密钥分发或第三方调用方 SDK 实现。

本文以 [`FastAPI 统一响应信封架构设计文档.md`](FastAPI%20统一响应信封架构设计文档.md) 为 HTTP 外层协议标准。若本文与该标准在 HTTP 顶层字段、`request_id`、`server_time`、HTTP 状态码或业务 `code` 语义上冲突，以该标准为准。

本文当前状态仍为 Candidate，不直接作为 Accepted 基线使用。升为 Accepted 前必须至少完成本文第 15 节的验证标准，并确认调用方能够接受 HTTP 成功码、业务码、时间格式、Callback 安全头和 ACK 合同的外部语义。

## 2. 核心心智模型

Job 与 Callback 不应各自发明一套 HTTP 返回格式。本项目对外只有三类稳定外壳：

```text
HTTP API 成功/失败外壳:
  HttpEnvelope[T] / ErrorEnvelope

Job 状态与结果外壳:
  JobEnvelope[JobResult]

Callback 终态事件外壳:
  CallbackEnvelope[JobEnvelope[JobResult]]
```

三者关系：

```text
POST /jobs
  -> HttpEnvelope[JobResponseData[null]]
       -> data.job = JobEnvelope[job_result=null]

GET /jobs/{job_id}
  -> HttpEnvelope[JobResponseData[JobResult]]
       -> data.job = JobEnvelope[job_result 或 job_error]

Callback delivery
  -> CallbackEnvelope[JobEnvelope[JobResult]]
       -> job = 与 GET 终态同源的 JobEnvelope
```

关键分层：

- HTTP 信封回答“这次 HTTP 请求是否成功被服务处理”。
- JobEnvelope 回答“异步 Job 当前处于什么状态，是否产生业务结果或业务失败”。
- CallbackEnvelope 回答“某个 Job 终态事件被投递给调用方”。

因此，**Job 执行失败不等于 HTTP 查询失败**。`GET /jobs/{job_id}` 成功查到一个失败 Job 时，HTTP 仍返回 `200`，顶层 `code="0"`，失败原因放在 `data.job.job_error`。

## 3. 总体原则

统一 Job 与 Callback 合同遵循以下原则：

```text
HTTP 层只表达请求处理结果
Job 层只表达任务状态和公开业务结果
Callback 层只表达终态事件通知
Attempt / Lease / Outbox 属于内部可靠性机制
job_type 只扩展 job_params 与 job_result
```

约束：

- 标准 JSON HTTP API 成功响应必须使用 `HttpEnvelope[T]`。
- 标准 JSON HTTP API 错误响应必须使用 `ErrorEnvelope`。
- `POST /jobs` 成功只表示服务已接单并持久化 Job，不表示 Job 已执行完成。
- `GET /jobs/{job_id}` 返回同一套 `JobEnvelope`，只随状态推进改变 `job_status`、`job_progress`、`job_result`、`job_error` 和 `callback`。
- Callback 不套 `HttpEnvelope`，因为它是服务主动发出的事件 payload，不是本服务响应调用方查询的 HTTP API 外层。
- Callback 接收方的 ACK 响应是另一条入站协议，不得与本服务对外 `HttpEnvelope` 混用。
- 任何 `job_type` 不得新增 Job 公共顶层字段；能力差异只能进入 `job_params`、runtime snapshot、`job_result` 或内部执行逻辑。

## 4. HTTP 外层响应标准

Job 相关 HTTP API 必须遵守统一响应信封：

| 字段 | 类型 | 规则 |
|---|---|---|
| `code` | `str` | 成功固定为 `"0"`；失败为已登记业务码。 |
| `msg` | `str` | 面向调用方的人读说明，不作为客户端分支依据。 |
| `data` | `T \| null` | 成功时承载业务数据；失败时承载安全错误详情或 `null`。 |
| `request_id` | `str` | 来自合法 `X-Request-ID` 或服务生成。 |
| `server_time` | `str` | 生成信封时的 ISO 8601 时间，必须包含时区。 |

Job HTTP API 的 `data` 固定只放一个顶层字段：

```json
{
  "job": {}
}
```

这样可以避免 Job 字段污染通用 `data` 层级，并为未来在不破坏 JobEnvelope 的情况下扩展 HTTP data 外壳保留治理空间。

## 5. Job HTTP 合同

### 5.1 创建 Job

```text
POST /jobs -> HttpEnvelope[JobResponseData[null]]
```

创建 Job 成功使用 HTTP `200`，顶层业务 `code="0"`。这里的“成功”表示请求已通过认证、参数校验、幂等校验、容量校验和持久化，Job 已进入异步执行生命周期。

`POST /jobs` 不使用 HTTP `202`，除非未来将其明确登记为统一响应信封例外并同步更新 OpenAPI 与客户端合同。第一阶段按照统一 FastAPI 响应信封标准，标准 JSON API 成功响应统一使用 HTTP `200`。

请求顶层字段固定：

| 字段 | 类型 | 必填 | 规则 |
|---|---|---:|---|
| `client_request_id` | `string` | 是 | 调用方幂等键；同一 `caller_id + client_request_id` 不得创建语义不同的 Job。 |
| `job_type` | `string` | 是 | 来自 `job_type` registry。 |
| `job_params` | `object` | 是 | 由对应 `job_type` 的 `ParamsSchema` 校验，默认拒绝未知字段。 |
| `callback` | `CallbackConfig \| null` | 否 | 终态通知配置；不传表示只轮询。 |
| `metadata` | `object` | 否 | 调用方排查元数据，不参与执行语义。 |
| `options` | `JobOptions \| null` | 否 | 通用执行选项，只能表达跨 `job_type` 的稳定控制意图。 |

`CallbackConfig` 字段：

| 字段 | 类型 | 必填 | 规则 |
|---|---|---:|---|
| `url` | `string` | 是 | Callback 投递地址，必须通过安全校验。 |
| `events` | `array enum \| null` | 否 | 允许值首版为 `job.succeeded`、`job.failed`；省略时订阅两个终态事件。 |

`JobOptions` 字段：

| 字段 | 类型 | 允许值 | 规则 |
|---|---|---|---|
| `priority` | `enum` | `normal`、`low` | 首版默认 `normal`；不承诺强实时调度。 |
| `idempotency_mode` | `enum` | `reject_duplicate`、`return_existing` | 默认 `reject_duplicate`。 |

### 5.2 幂等语义

`client_request_id` 是调用方提供的幂等键，真实判定必须同时结合服务端生成的 canonical request hash。服务不得只用 `client_request_id` 判断两个请求是否语义相同。

canonical request hash 输入必须稳定、可重算、与 JSON 字段顺序无关。第一阶段 hash 输入包含：

| 字段 | 是否参与 hash | 规则 |
|---|---:|---|
| `caller_id` | 是 | 防止不同调用方的同名 `client_request_id` 互相影响。 |
| `client_request_id` | 是 | 幂等键本身进入 hash，便于审计。 |
| `job_type` | 是 | 不同能力一定是不同语义。 |
| normalized `job_params` | 是 | 使用 `ParamsSchema` 校验和规范化后的结果。 |
| normalized `callback` | 是 | 包含 `url` 和规范化后的 `events`。 |
| normalized `options` | 是 | 至少包含 `priority` 和 `idempotency_mode` 的有效值。 |
| `metadata` | 否 | 只用于排查，不参与执行语义。 |

canonicalization 规则：

| 对象 | 规范化规则 |
|---|---|
| JSON 对象 | 按 key 字典序序列化；移除无语义空白；数字、布尔、字符串按 JSON 标准类型保留。 |
| 缺省字段 | 在计算 hash 前填入服务端默认值，例如 `priority="normal"`、`idempotency_mode="reject_duplicate"`。 |
| `null` 与省略 | 对有默认值字段，`null`、省略和空对象必须先规范化为同一默认有效值；对无默认值字段，`null` 与省略按 schema 规则处理。 |
| `callback.events` | 省略或 `null` 规范化为 `["job.failed","job.succeeded"]`；空数组非法；最终数组按字典序排序并去重。 |
| `callback.url` scheme / host | scheme 和 host 小写；host 使用 IDNA ASCII 形式；路径、查询字符串保持语义原样但必须使用标准 URL parser 归一化。 |
| `callback.url` 端口 | HTTPS 默认端口 `443` 不进入 canonical form；显式非默认端口必须保留。 |
| `callback.url` fragment | 禁止携带 fragment；出现 fragment 时请求非法，不进入 hash。 |
| `job_params` | 使用对应 `ParamsSchema` 校验、填充默认值和规范化后的对象。 |

canonical form 必须写入日志或可审计摘要时只记录 hash 和字段摘要，不记录完整大载荷、密钥或隐私文本。

幂等处理规则：

| 场景 | `idempotency_mode` | HTTP | 结果 |
|---|---|---:|---|
| 未找到同 key Job | 任意 | `200` | 创建新 Job，返回新 JobEnvelope。 |
| 找到同 key 且 hash 相同 | `reject_duplicate` | `409` | 返回 `ErrorEnvelope`，不创建新 Job。 |
| 找到同 key 且 hash 相同 | `return_existing` | `200` | 返回已有 Job 的当前 JobEnvelope，不创建新 Job。 |
| 找到同 key 但 hash 不同 | 任意 | `409` | 返回 `ErrorEnvelope`，错误 reason 为 `CLIENT_REQUEST_ID_CONFLICT` 或等价登记项。 |

`return_existing` 返回旧 Job 时，响应仍是成功 HTTP 查询语义：顶层 `code="0"`，`data.job` 是已有 Job 的当前投影。服务不得为了兼容调用方而重新创建、覆盖或合并 Job。

幂等冲突错误示例：

```json
{
  "code": "100409",
  "msg": "client_request_id conflict",
  "data": {
    "client_request_id": "cpp-20260621-book-2042-tagging",
    "existing_job_id": "01JZ8Q7Y4W7X2Z6M8N9P0Q1R2S"
  },
  "request_id": "01JZ8QA0C1D2E3F4G5H6J7K8M9",
  "server_time": "2026-06-21T22:30:01+08:00"
}
```

### 5.3 查询 Job

```text
GET /jobs/{job_id} -> HttpEnvelope[JobResponseData[JobResult]]
```

查询成功使用 HTTP `200`，顶层业务 `code="0"`。只要 Job 存在且调用方有权查看，哪怕 Job 自身已经执行失败，HTTP 查询仍是成功。

查询失败场景才返回 `ErrorEnvelope`：

| 场景 | HTTP 状态码 | 说明 |
|---|---:|---|
| `job_id` 格式非法 | `400` | 路径参数校验失败。 |
| 未认证 | `401` | 缺少或无效服务凭证。 |
| 无权限 | `403` | 调用方无权查看目标 Job。 |
| Job 不存在 | `404` | 当前调用方边界内找不到该 Job。 |
| 服务内部异常 | `500` | 查询、投影或响应校验失败。 |

## 6. JobEnvelope 结构

`JobEnvelope[JobResult]` 是 Job 对外状态的唯一公共壳，由 HTTP `data.job` 和 Callback `job` 共同复用。

| 字段 | 类型 | 必填 | 规则 |
|---|---|---:|---|
| `job_id` | `str` | 是 | 服务生成的 Job 主键。 |
| `client_request_id` | `str \| null` | 是 | 调用方幂等键。 |
| `job_type` | `str` | 是 | 来自 `job_type` registry。 |
| `job_status` | `enum` | 是 | 首版为 `queued`、`running`、`succeeded`、`failed`。 |
| `job_progress` | `JobProgress` | 是 | 进度摘要，不作为终态判断依据。 |
| `job_result` | `JobResult \| null` | 是 | 公开业务结果；创建、运行中或失败时为 `null`。 |
| `job_error` | `JobErrorDetail \| null` | 是 | Job 执行失败原因；仅失败终态必填。 |
| `callback` | `CallbackState` | 是 | Callback 聚合状态摘要。 |
| `status_url` | `str` | 是 | 轮询地址，第一阶段固定为 API base URL 相对路径。 |
| `created_at` | `str` | 是 | Job 创建时间，ISO 8601。 |
| `updated_at` | `str` | 是 | Job 最近状态更新时间，ISO 8601。 |
| `finished_at` | `str \| null` | 是 | Job 进入终态的时间。 |

`status_url` 语义：

- 第一阶段 `status_url` 固定返回相对路径，例如 `/api/v1/ai-jobs/jobs/{job_id}`。
- 调用方必须使用本服务 API base URL 解析该相对路径。
- Callback payload 中的 `status_url` 也使用同一相对路径，不承诺可直接由接收方作为绝对 URL 请求。
- 如果未来需要返回绝对公开 URL，必须升版本并定义 API base URL 来源、反向代理头信任边界和 OpenAPI 展示规则。

### 6.1 Job 状态约束

| `job_status` | `job_result` | `job_error` | 说明 |
|---|---|---|---|
| `queued` | `null` | `null` | 已接单，等待执行。 |
| `running` | `null` | `null` | 正在执行。 |
| `succeeded` | `JobResult \| null` | `null` | 已成功；若该 `job_type` 声明无公开结果，可为 `null`。 |
| `failed` | `null` | `JobErrorDetail` | 已失败；失败原因必须可治理、可排查。 |

`job_progress` 不作为终态判断依据。客户端必须使用 `job_status` 判断 Job 是否终态。

Job 状态迁移：

| 起始状态 | 目标状态 | 触发方 | 规则 |
|---|---|---|---|
| none | `queued` | API | 创建请求校验、幂等和容量检查通过后创建 Job。 |
| `queued` | `running` | Worker | Worker 成功领取执行权后进入运行。 |
| `queued` | `failed` | 系统 | 发布失败、前置资源失效或恢复收敛确认无法执行。 |
| `running` | `succeeded` | Worker | 执行成功并持久化公开结果。 |
| `running` | `failed` | Worker / Reconciler | 执行异常、超时、重试耗尽或恢复收敛失败。 |
| `succeeded` | any | none | 终态不可逆。 |
| `failed` | any | none | 终态不可逆。 |

第一阶段不对外暴露 `cancelled`。如果未来支持取消，必须升版本并同时定义取消 API、取消状态、Callback 是否触发、OpenAPI 和客户端处理规则。

时间字段更新规则：

- 创建 Job 时写入 `created_at` 和 `updated_at`，`finished_at=null`。
- 每次 `job_status` 或对外可见 `job_progress` 变化时更新 `updated_at`。
- 首次进入 `succeeded` 或 `failed` 时写入 `finished_at`，之后不得改写终态和终态时间。
- Callback 投递状态变化可以更新 JobEnvelope 中的 `callback` 和 `updated_at`，但不得改变 `finished_at`。

### 6.2 JobProgress

| 字段 | 类型 | 规则 |
|---|---|---|
| `stage` | `enum` | 首版建议为 `accepted`、`fetching_input`、`planning`、`calling_model`、`merging`、`writing_result`、`completed`、`failed`。 |
| `percent` | `int` | `0` 到 `100`，只表示展示进度，不承诺精确执行比例。 |
| `message` | `str \| null` | 面向调用方的人读摘要，不作为机器判断依据。 |

Callback 投递不是 Job 执行进度的一部分，不进入 `job_progress.stage`。Callback 相关状态只通过 `callback.status`、`callback.attempt`、`callback.last_error` 和 `callback.next_retry_at` 表达。

### 6.3 JobErrorDetail

Job 执行失败使用 `JobErrorDetail` 表达，结构与 HTTP 错误详情保持治理一致，但不等同于顶层 `ErrorEnvelope`。

| 字段 | 类型 | 规则 |
|---|---|---|
| `reason` | `str` | 稳定英文枚举，必须来自错误注册表。 |
| `details` | `object` | 只允许安全暴露的结构化细节。 |
| `retryable` | `bool` | 调用方可据此判断是否适合重新创建 Job。 |

约束：

- `job_error` 不得包含堆栈、密钥、Prompt 全文、模型原始响应、SQL、请求头或大载荷。
- 上游模型失败、对象存储失败、超时、输出校验失败等执行期错误写入 `job_error`。
- 创建前参数错误、鉴权错误、幂等冲突、容量拒绝等请求期错误不创建 Job，返回 HTTP `ErrorEnvelope`。

### 6.4 CallbackState

`CallbackState` 是对调用方可见的 Callback 聚合状态，不暴露 outbox 内部账本。

| 字段 | 类型 | 规则 |
|---|---|---|
| `status` | `enum` | 首版为 `not_configured`、`pending`、`delivering`、`delivered`、`retrying`、`failed`。 |
| `attempt` | `int` | 已尝试投递次数，未投递时为 `0`。 |
| `last_error` | `CallbackErrorDetail \| null` | 最近一次投递失败摘要。 |
| `next_retry_at` | `str \| null` | 下一次可重试投递时间，ISO 8601。 |

约束：

- Callback 投递失败只改变 `callback` 聚合状态，不改变 `job_status`、`job_result` 或 `job_error`。
- `delivering` 只表示当前正在执行 HTTP 投递。
- 仍可重试的投递失败应回到 `pending` 或 `retrying`；最终耗尽重试才进入 `failed`。
- 内部 `event_id`、`lease_token`、`last_http_status`、`next_attempt_at`、死信原因等账本字段默认不进入 `CallbackState`。
- `attempt` 表示已经发起过的投递次数，不表示剩余次数。
- `next_retry_at` 只在 `pending` 或 `retrying` 且确实存在下一次计划投递时有值；`not_configured`、`delivering`、`delivered`、`failed` 时必须为 `null`。

### 6.5 CallbackErrorDetail

Callback 投递失败使用 `CallbackErrorDetail` 表达。它的字段结构可以与 `JobErrorDetail` 保持一致，但语义独立：Callback 错误描述通知投递失败，不描述 Job 执行失败。

| 字段 | 类型 | 规则 |
|---|---|---|
| `reason` | `str` | 稳定英文枚举，必须来自 Callback 或集成错误注册表。 |
| `details` | `object` | 只允许安全暴露的结构化投递细节。 |
| `retryable` | `bool` | 表示该投递失败是否适合自动重试。 |

约束：

- `CallbackErrorDetail.reason` 不得复用会让调用方误判为 Job 执行失败的 reason。
- `callback.last_error` 不得写入 `job_error`。
- `job_error` 不得写入 `callback.last_error`。
- Callback 投递失败的 HTTP 状态、ACK 摘要、超时类型可以进入 `details`，但必须裁剪。

## 7. CallbackEnvelope 结构与安全合同

Callback 是本服务主动向调用方发送的终态事件 payload，不套本服务的 HTTP `code/msg/data/request_id/server_time` 信封。

```text
CallbackEnvelope[JobEnvelope[JobResult]]
```

### 7.1 事件结构

顶层字段：

| 字段 | 类型 | 必填 | 规则 |
|---|---|---:|---|
| `event` | `enum` | 是 | 首版为 `job.succeeded`、`job.failed`。 |
| `event_id` | `str` | 是 | 单个终态事件的稳定 ID；接收方用于幂等。 |
| `attempt` | `int` | 是 | 本次投递尝试次数，从 `1` 开始。 |
| `sent_at` | `str` | 是 | 投递时间，ISO 8601。 |
| `trigger_request_id` | `str \| null` | 是 | 创建或触发本 Job 的入口 `request_id`。 |
| `caller_id` | `str` | 是 | 调用方身份摘要。 |
| `job` | `JobEnvelope[JobResult]` | 是 | 与轮询终态同源的 JobEnvelope。 |

事件与 Job 状态必须匹配：

| `event` | `job.job_status` | 规则 |
|---|---|---|
| `job.succeeded` | `succeeded` | `job.job_result` 按 `job_type` 公开结果 schema 投影。 |
| `job.failed` | `failed` | `job.job_error` 必填，`job.job_result=null`。 |

Callback payload 约束：

- Callback 只在 Job 终态事件触发。
- 同一个 Job 的同一种终态事件必须有稳定 `event_id`，便于接收方幂等处理。
- Callback 重试不得重新生成语义不同的 payload。
- Callback payload 中的 `job` 必须来自已持久化终态 Job 投影，不得在投递适配器中重新计算业务结果。
- Callback 发送时必须使用 `Content-Type`、时间戳头和签名头；事件类型和事件 ID 以 payload 顶层字段为准，不再通过重复请求头表达。
- Callback payload 中的 `job.callback` 表示事件创建或本次投递开始前的聚合状态快照，不承诺包含本次投递结果。
- 接收方收到 Callback 时，不得根据 payload 内的 `job.callback.status` 判断本次投递是否已经成功；本次投递结果只能由接收方返回 ACK 后，再通过后续 `GET /jobs/{job_id}` 查看。
- 第一次投递的 payload 中，`job.callback.status` 通常为 `delivering` 或 `pending`，不得写成 `delivered`。

### 7.2 Callback 传输头

Callback HTTP 请求第一阶段只要求以下头：

| Header | 必填 | 规则 |
|---|---:|---|
| `Content-Type` | 是 | 固定为 `application/json`。 |
| `X-Callback-Timestamp` | 是 | 发送时间，ISO 8601，必须包含时区。 |
| `X-Callback-Signature` | 是 | `HMAC-SHA256` 签名，格式为 `sha256=<hex_digest>`。 |

签名输入固定为：

```text
X-Callback-Timestamp + "." + raw_body_bytes
```

签名计算规则：

```text
hex_digest = HMAC-SHA256(callback_secret, timestamp + "." + raw_body_bytes)
X-Callback-Signature = "sha256=" + hex_digest
```

约束：

- 接收方必须使用原始请求体字节计算签名，不得用重新序列化后的 JSON。
- 接收方必须按 `event_id` 做幂等处理；同一个 `event_id` 的重复投递不得重复执行业务副作用。
- 接收方必须校验时间戳重放窗口。第一阶段默认窗口为 5 分钟，超出窗口的请求应拒绝。
- 第一阶段每个调用方只使用一个当前有效的 `callback_secret`。Secret 轮换通过双方配置发布完成，不进入 Callback HTTP 头合同。
- 签名 secret 不得进入日志、HTTP 响应、Callback payload 或错误详情。

### 7.3 Callback URL 安全

Callback URL 属于服务端主动访问外部地址，必须按 SSRF 风险处理。

创建 Job 前必须校验：

- `callback.url` 必须使用 HTTPS。
- 禁止 localhost、loopback、private network、link-local、unspecified、multicast 地址。
- 禁止云厂商 metadata endpoint，例如 `169.254.169.254` 和等价链路本地地址。
- 禁止仅依赖字符串判断域名安全；必须校验解析后的 IP。
- 禁止用户信息形式 URL，例如 `https://user:pass@example.com/callback`。
- 禁止非标准端口，除非该端口被显式加入安全 allowlist。

投递前必须重新校验：

- 重新解析域名并校验 DNS 结果。
- HTTP 重定向默认不跟随；如果未来允许跟随重定向，必须对每一次跳转后的目标重新执行完整 SSRF 校验。
- DNS 解析失败、解析到内网或投递期安全校验失败，只影响 Callback 状态，不改变 Job 终态。

本地开发如需允许 `http://127.0.0.1` 或 `http://localhost`，必须通过显式本地开关启用，且该开关只能在本地 loopback 依赖环境中生效。生产环境不得启用该例外。

### 7.4 Callback 重试状态机

Callback 投递语义是 at-least-once。接收方必须用稳定 `event_id` 幂等，服务端不得承诺 exactly-once。

状态迁移：

| 起始状态 | 目标状态 | 触发 | 规则 |
|---|---|---|---|
| `not_configured` | none | 未配置 callback | 不创建外部投递事件。 |
| `pending` | `delivering` | 投递器领取事件 | `attempt` 递增，开始一次 HTTP 投递。 |
| `delivering` | `delivered` | HTTP `2xx` 且 ACK 合法接受 | 投递完成，`next_retry_at=null`。 |
| `delivering` | `retrying` | 可重试失败且未达最大次数 | 写入 `last_error` 和 `next_retry_at`。 |
| `retrying` | `delivering` | 到达 `next_retry_at` | `attempt` 递增，重新投递同一事件。 |
| `delivering` | `failed` | 不可重试失败或达到最大次数 | 终止投递，`next_retry_at=null`。 |
| `delivered` | none | 终态 | 不再投递。 |
| `failed` | none | 终态 | 不再自动投递。 |

重试策略：

- 第一阶段默认最大尝试次数为 3 次。
- 退避策略使用指数退避加上限，例如 `30s`、`2m`、`10m`；具体数值必须集中配置。
- 最大投递窗口必须有上限，例如 24 小时；超过窗口后进入 `failed`。
- `attempt` 是已发起投递次数；第一次投递时为 `1`。
- 重试时 `event_id`、`event`、`trigger_request_id`、`caller_id` 和 `job` 语义必须保持稳定。
- 重试时 `attempt` 必须递增，`sent_at` 可以更新为本次发送时间，签名和时间戳必须重新生成。
- 重试不得重新计算 `job_result`、`job_error` 或其它业务结果字段。

## 8. Callback ACK 合同

Callback 接收方可以通过 HTTP 响应表达是否接受事件。该 ACK 是调用方返回给本服务的入站协议，不使用本服务对外 `HttpEnvelope`。

ACK 响应体必须是 JSON object，`Content-Type` 必须为 `application/json`。第一阶段不把空响应体或 HTTP `204` 视为成功 ACK。

ACK 结构：

| 字段 | 类型 | 必填 | 规则 |
|---|---|---:|---|
| `accepted` | `bool` | 是 | `true` 表示接收方接受事件；`false` 表示接收方拒绝事件。 |
| `msg` | `str \| null` | 否 | 接收方人读说明。 |
| `details` | `object` | 否 | 接收方安全暴露的结构化说明。 |

处理规则：

- HTTP `2xx` 且 ACK 合法、`accepted=true`，视为投递成功。
- HTTP `2xx` 但 ACK 结构非法，视为 Callback 响应合同错误。
- HTTP `2xx` 且 `accepted=false`，视为接收方拒绝事件。
- HTTP `204`、空响应体、非 JSON 响应体或 `Content-Type` 非 JSON，视为 ACK 合同错误。
- HTTP 非 `2xx`、网络错误、超时或签名/协议错误，视为投递失败。
- `accepted=false` 第一阶段统一视为可重试失败，错误 reason 为 `CALLBACK_ACK_REJECTED`。
- `accepted=false` 不支持接收方声明永久拒绝语义；服务端持续重试直到达到最大尝试次数或最大投递窗口，之后进入 `failed`。
- `details.retry_after_seconds` 第一阶段只作为接收方诊断建议，不影响服务端退避策略；服务端必须使用自身集中配置计算 `next_retry_at`。
- ACK 超时阈值必须集中配置，且不得超过 Callback 单次投递总超时。
- Callback 投递失败只影响 Callback 状态和重试，不改变 Job 终态。

ACK 成功示例：

```json
{
  "accepted": true,
  "msg": "received",
  "details": {}
}
```

ACK 拒绝示例：

```json
{
  "accepted": false,
  "msg": "temporary downstream backlog",
  "details": {
    "reason": "CALLER_BACKLOG",
    "retry_after_seconds": 300
  }
}
```

第一阶段即使 ACK 返回 `accepted=false`，本服务也统一按可重试失败处理，不从 `details.reason` 推导永久失败。

## 9. 错误分层

### 9.1 HTTP ErrorEnvelope

以下场景返回 HTTP `ErrorEnvelope`，不创建或不返回 Job 成功信封：

| 场景 | HTTP 状态码 | 说明 |
|---|---:|---|
| 请求体格式错误或参数校验失败 | `400` | `CreateJobRequest`、`job_params`、`callback` 或路径参数非法。 |
| `X-Request-ID` 非法 | `400` | 按统一 FastAPI 响应信封标准返回请求追踪 ID 错误。 |
| 未认证 | `401` | 缺少或无效服务凭证。 |
| 无权限 | `403` | 调用方无权访问目标 Job。 |
| Job 不存在 | `404` | 查询目标不存在。 |
| 幂等冲突 | `409` | 同一 `caller_id + client_request_id` 已绑定不同请求语义。 |
| 服务内部异常 | `500` | 数据库、投影、响应校验或未捕获异常。 |
| 上游服务异常 | `502` | 创建阶段依赖外部服务且外部异常。 |
| 上游服务超时 | `504` | 创建阶段依赖外部服务且超时。 |

业务 `code` 必须来自集中注册表。本文不强制具体码段，但建议至少登记以下语义：

| 建议 reason | HTTP 状态码 | 说明 |
|---|---:|---|
| `INVALID_INPUT` | `400` | 通用参数校验失败。 |
| `REQUEST_ID_INVALID` | `400` | `X-Request-ID` 非法。 |
| `UNAUTHORIZED` | `401` | 未认证。 |
| `FORBIDDEN` | `403` | 无权限。 |
| `JOB_NOT_FOUND` | `404` | Job 不存在。 |
| `CLIENT_REQUEST_ID_CONFLICT` | `409` | 幂等键冲突。 |
| `INTERNAL_ERROR` | `500` | 未处理系统异常。 |
| `DEPENDENCY_FAILED` | `502` | 外部依赖异常。 |
| `DEPENDENCY_TIMEOUT` | `504` | 外部依赖超时。 |

### 9.2 JobErrorDetail

以下场景通常表现为 Job 失败终态，而不是 HTTP 请求失败：

| 场景 | `job_status` | `job_error.reason` 示例 |
|---|---|---|
| Worker 执行失败 | `failed` | `JOB_EXECUTION_FAILED` |
| 模型调用失败 | `failed` | `MODEL_CALL_FAILED` |
| 模型输出不符合 schema | `failed` | `MODEL_OUTPUT_INVALID` |
| 对象存储读取或写入失败 | `failed` | `OSS_FETCH_FAILED`、`OSS_WRITE_FAILED` |
| Job 执行超时 | `failed` | `JOB_TIMEOUT` |
| 运行时配置缺失 | `failed` | `RUNTIME_CONFIG_MISSING` |

如果失败发生在 Job 创建前，例如 `job_type` 不存在、`job_params` 不合法或 `callback.url` 不安全，应返回 HTTP `ErrorEnvelope`，不得创建一个失败 Job 作为兜底。

### 9.3 Callback Last Error

以下场景只影响 Callback 状态：

| 场景 | `callback.status` | `callback.last_error.reason` 示例 |
|---|---|---|
| Callback URL 投递期安全校验失败 | `failed` 或 `retrying` | `CALLBACK_URL_INVALID` |
| Callback payload 构造失败 | `failed` | `CALLBACK_BODY_INVALID` |
| 接收方返回非 `2xx` | `retrying` 或 `failed` | `CALLBACK_HTTP_ERROR` |
| 网络错误或超时 | `retrying` 或 `failed` | `CALLBACK_REQUEST_ERROR` |
| ACK 结构非法 | `retrying` 或 `failed` | `CALLBACK_RESPONSE_CONTRACT_INVALID` |
| ACK 显式拒绝 | `retrying` 或 `failed` | `CALLBACK_ACK_REJECTED` |
| Callback payload 超限 | `failed` | `CALLBACK_BODY_TOO_LARGE` |

Callback 错误不得反写为 `job_error`。如果终态 Job 公开投影无法构造，应在 Job 结果生成阶段收敛为 Job 执行失败或系统错误，而不是进入 Callback Last Error。

## 10. 轮询与 Callback 一致性

`GET /jobs/{job_id}` 终态响应和 Callback payload 必须在 Job 业务状态与公开结果上同源。

一致性规则：

- 同一个 Job 的成功轮询结果和 `job.succeeded` Callback 中，`job.job_result` 必须来自同一份 `PublicResultSchema` 投影。
- 同一个 Job 的失败轮询结果和 `job.failed` Callback 中，`job.job_error` 必须表达同一失败原因。
- `job_status`、`job_result`、`job_error`、`status_url`、业务公开字段和业务时间字段必须同源同义。
- `job.callback` 是投递状态快照，允许与后续 `GET /jobs/{job_id}` 返回的 Callback 聚合状态不同。
- Callback 不得包含轮询接口看不到的业务结果字段。
- 轮询接口不得在 `job_result` 外另行暴露 Callback 专属业务结果。
- 大文件、大 JSON、模型输出、对象存储引用等需要对外可见时，必须作为具体 `job_type` 的 `job_result` 字段表达。
- 若某个 `job_type` 声明成功终态无公开结果，轮询和 Callback 都必须返回 `job_result=null`。

## 11. 安全与可观测性

### 11.1 Request ID

HTTP API 必须遵守统一 FastAPI 响应信封的 `request_id` 规则：

- 合法 `X-Request-ID` 沿用。
- 缺失时服务生成。
- 非法时返回 HTTP `400`，不得静默替换。
- 响应体、响应头和日志使用同一个 `request_id`。

Job 创建时应把入口 `request_id` 写入 Job 运行时系统字段或等价上下文，用作 Callback 的 `trigger_request_id`。

### 11.2 时间字段

HTTP `server_time`、Job 时间字段、Callback `sent_at` 都使用 ISO 8601 字符串并包含时区。

字段语义：

- `server_time`：HTTP 响应信封生成时间。
- `created_at`：Job 创建时间。
- `updated_at`：Job 最近状态更新时间。
- `finished_at`：Job 进入终态时间。
- `sent_at`：Callback payload 生成或发送时间。

### 11.3 敏感信息

以下内容不得进入 `HttpEnvelope.data`、`ErrorEnvelope.data`、`JobEnvelope` 或 `CallbackEnvelope`：

- 密钥、token、数据库 URL、签名 secret。
- 完整 Prompt、模型输入输出全文、供应商原始响应体。
- 堆栈、SQL、内部请求头、内部租约令牌。
- 大文件内容、超长错误页、未裁剪 traceback。
- Callback outbox 内部 lease、死信、重试账本的完整细节。

### 11.4 Payload 尺寸与裁剪

信封字段必须有明确尺寸边界，避免大结果、大错误页或模型原文拖垮数据库、HTTP 响应和 Callback 投递。

第一阶段默认合同上限：

| 对象 | 上限 | 超限处理 |
|---|---:|---|
| `metadata` JSON 序列化后大小 | `16 KiB` | 创建请求返回 HTTP `400`，不创建 Job。 |
| `job_params` JSON 序列化后大小 | 由 `job_type` 声明，默认不超过 `256 KiB` | 创建请求返回 HTTP `400`，不创建 Job。 |
| `job_result` 内联 JSON 序列化后大小 | `256 KiB` | 必须改用 `output_ref`、artifact 引用或对象存储引用；不得内联返回。 |
| `job_error.details` JSON 序列化后大小 | `8 KiB` | 显式裁剪，并写入 `truncated=true`、`original_size_bytes`。 |
| `callback.last_error.details` JSON 序列化后大小 | `8 KiB` | 显式裁剪，并写入 `truncated=true`、`original_size_bytes`。 |
| Callback raw body | `512 KiB` | 不投递超限 body；Job 终态不变，Callback 进入 `failed`，`last_error.reason="CALLBACK_BODY_TOO_LARGE"`。 |
| 人读 `msg`、`message` 字段 | `512` 字符 | 显式裁剪，并保留稳定 `reason` 供机器判断。 |

约束：

- 输入超限属于请求合同错误，应快速失败，不创建 Job。
- 输出超限属于 `job_type` 结果投影设计问题，必须通过引用字段解决，不得把大内容直接塞进 `job_result` 或 Callback payload。
- 错误详情裁剪不是 silent fallback。裁剪后的错误仍必须保留稳定 `reason`、关键排查字段和 `truncated=true`。
- Callback payload 超限不得通过删除业务字段来凑大小；应让 `job_result` 改为引用结构后再投递。
- `CALLBACK_BODY_TOO_LARGE` 必须进入错误注册表，`details` 至少包含 `size_bytes` 和 `limit_bytes`。
- 未构造 Callback body 或未发起外部 HTTP 请求的失败不递增 `attempt`；例如 payload 超限时 `attempt` 保持为已实际发起的投递次数，首轮构造即失败时为 `0`。

## 12. OpenAPI 与 Schema 展示

OpenAPI 必须展示最终对外结构，而不是业务接口内部裸返回值。

要求：

- `POST /jobs` 成功响应展示 `HttpEnvelope[JobResponseData]`。
- `GET /jobs/{job_id}` 成功响应展示 `HttpEnvelope[JobResponseData]`。
- 常见 HTTP `4xx`、`5xx` 展示 `ErrorEnvelope`。
- `JobResponseData.job` 展示统一 `JobEnvelope`。
- `CallbackEnvelope` 作为调用方接收事件的独立 schema 展示，不嵌套 `HttpEnvelope`。
- `CallbackResponseEnvelope` 或 ACK schema 作为调用方响应本服务投递的独立 schema 展示。
- `X-Request-ID` 的格式、长度和非法处理规则应进入 OpenAPI 说明。
- Callback 传输头 `Content-Type`、`X-Callback-Timestamp`、`X-Callback-Signature` 必须进入对接文档。
- Callback ACK 的 `Content-Type`、必填 `accepted` 字段、空响应体和 HTTP `204` 处理规则必须进入对接文档。
- Payload 尺寸限制必须进入对接文档或 OpenAPI 扩展说明。

## 13. `job_type` 扩展边界

新增 `job_type` 时只能扩展以下内容：

- `ParamsSchema`：`CreateJobRequest.job_params` 的业务输入。
- `RuntimeFieldsSchema`：运行时派生字段，进入 runtime snapshot。
- `CanonicalResultSchema`：内部执行事实源。
- `PublicResultSchema`：进入 `JobEnvelope.job_result` 与 Callback `job.job_result`。
- 该能力允许的错误 reason、日志事件和副作用策略。

不得扩展：

- `HttpEnvelope` 顶层字段。
- `JobResponseData` 顶层字段。
- `JobEnvelope` 公共顶层字段。
- `CallbackEnvelope` 公共顶层字段。
- Callback ACK 的基础语义。

如果多个 `job_type` 出现重复结果字段，应优先抽公共 `JobResult` 子结构，而不是修改 Job 公共壳。

## 14. 示例

### 14.1 创建 Job 成功

```json
{
  "code": "0",
  "msg": "success",
  "data": {
    "job": {
      "job_id": "01JZ8Q7Y4W7X2Z6M8N9P0Q1R2S",
      "client_request_id": "cpp-20260621-book-2042-tagging",
      "job_type": "story.tagging",
      "job_status": "queued",
      "job_progress": {
        "stage": "accepted",
        "percent": 0,
        "message": "accepted"
      },
      "job_result": null,
      "job_error": null,
      "callback": {
        "status": "pending",
        "attempt": 0,
        "last_error": null,
        "next_retry_at": null
      },
      "status_url": "/api/v1/ai-jobs/jobs/01JZ8Q7Y4W7X2Z6M8N9P0Q1R2S",
      "created_at": "2026-06-21T22:30:00+08:00",
      "updated_at": "2026-06-21T22:30:00+08:00",
      "finished_at": null
    }
  },
  "request_id": "01JZ8Q7XZAY6R6Q0W9S8T7V6U5",
  "server_time": "2026-06-21T22:30:00+08:00"
}
```

### 14.2 `return_existing` 返回已有 Job

```json
{
  "code": "0",
  "msg": "success",
  "data": {
    "job": {
      "job_id": "01JZ8Q7Y4W7X2Z6M8N9P0Q1R2S",
      "client_request_id": "cpp-20260621-book-2042-tagging",
      "job_type": "story.tagging",
      "job_status": "running",
      "job_progress": {
        "stage": "calling_model",
        "percent": 60,
        "message": "calling model"
      },
      "job_result": null,
      "job_error": null,
      "callback": {
        "status": "pending",
        "attempt": 0,
        "last_error": null,
        "next_retry_at": null
      },
      "status_url": "/api/v1/ai-jobs/jobs/01JZ8Q7Y4W7X2Z6M8N9P0Q1R2S",
      "created_at": "2026-06-21T22:30:00+08:00",
      "updated_at": "2026-06-21T22:31:00+08:00",
      "finished_at": null
    }
  },
  "request_id": "01JZ8QA0C1D2E3F4G5H6J7K8N0",
  "server_time": "2026-06-21T22:31:05+08:00"
}
```

### 14.3 查询 Job 成功终态

```json
{
  "code": "0",
  "msg": "success",
  "data": {
    "job": {
      "job_id": "01JZ8Q7Y4W7X2Z6M8N9P0Q1R2S",
      "client_request_id": "cpp-20260621-book-2042-tagging",
      "job_type": "story.tagging",
      "job_status": "succeeded",
      "job_progress": {
        "stage": "completed",
        "percent": 100,
        "message": "completed"
      },
      "job_result": {
        "book_id": "2042",
        "accepted": true,
        "tags": [
          {
            "tag_id": "theme.love",
            "name": "Love",
            "confidence": 0.94
          }
        ],
        "output_ref": {
          "kind": "oss_object",
          "uri": "oss://bucket/output/01JZ8Q7Y4W7X2Z6M8N9P0Q1R2S/result.json",
          "sha256": "6c4f9d1b8e22"
        }
      },
      "job_error": null,
      "callback": {
        "status": "delivered",
        "attempt": 1,
        "last_error": null,
        "next_retry_at": null
      },
      "status_url": "/api/v1/ai-jobs/jobs/01JZ8Q7Y4W7X2Z6M8N9P0Q1R2S",
      "created_at": "2026-06-21T22:30:00+08:00",
      "updated_at": "2026-06-21T22:33:01+08:00",
      "finished_at": "2026-06-21T22:33:00+08:00"
    }
  },
  "request_id": "01JZ8QB5M6DA7A8B9C0D1E2F3G",
  "server_time": "2026-06-21T22:33:02+08:00"
}
```

### 14.4 查询 Job 失败终态

```json
{
  "code": "0",
  "msg": "success",
  "data": {
    "job": {
      "job_id": "01JZ8Q7Y4W7X2Z6M8N9P0Q1R2S",
      "client_request_id": "cpp-20260621-book-2042-tagging",
      "job_type": "story.tagging",
      "job_status": "failed",
      "job_progress": {
        "stage": "failed",
        "percent": 100,
        "message": "failed"
      },
      "job_result": null,
      "job_error": {
        "reason": "MODEL_CALL_FAILED",
        "details": {
          "provider": "openai",
          "operation": "structured_output"
        },
        "retryable": true
      },
      "callback": {
        "status": "delivered",
        "attempt": 1,
        "last_error": null,
        "next_retry_at": null
      },
      "status_url": "/api/v1/ai-jobs/jobs/01JZ8Q7Y4W7X2Z6M8N9P0Q1R2S",
      "created_at": "2026-06-21T22:30:00+08:00",
      "updated_at": "2026-06-21T22:31:31+08:00",
      "finished_at": "2026-06-21T22:31:31+08:00"
    }
  },
  "request_id": "01JZ8QB5M6DA7A8B9C0D1E2F3H",
  "server_time": "2026-06-21T22:31:32+08:00"
}
```

### 14.5 创建 Job 参数错误

```json
{
  "code": "100001",
  "msg": "invalid input",
  "data": {
    "errors": [
      {
        "loc": ["body", "job_params", "book_id"],
        "type": "missing",
        "msg": "field required"
      }
    ]
  },
  "request_id": "01JZ8QCB7Q9K1M2N3P4Q5R6S7T",
  "server_time": "2026-06-21T22:34:00+08:00"
}
```

### 14.6 Callback 成功事件

请求头示例：

```http
POST /caller/ai-job-callback HTTP/1.1
Content-Type: application/json
X-Callback-Timestamp: 2026-06-21T22:33:01+08:00
X-Callback-Signature: sha256=7f83b1657ff1fc53b92dc18148a1d65dfa135ea0d1f2b3c4d5e6f7a8b9c0d1e2
```

```json
{
  "event": "job.succeeded",
  "event_id": "01JZ8QD9Z1H2J3K4L5M6N7P8Q9",
  "attempt": 1,
  "sent_at": "2026-06-21T22:33:01+08:00",
  "trigger_request_id": "01JZ8Q7XZAY6R6Q0W9S8T7V6U5",
  "caller_id": "cpp-service",
  "job": {
    "job_id": "01JZ8Q7Y4W7X2Z6M8N9P0Q1R2S",
    "client_request_id": "cpp-20260621-book-2042-tagging",
    "job_type": "story.tagging",
    "job_status": "succeeded",
    "job_progress": {
      "stage": "completed",
      "percent": 100,
      "message": "completed"
    },
    "job_result": {
      "book_id": "2042",
      "accepted": true,
      "tags": [
        {
          "tag_id": "theme.love",
          "name": "Love",
          "confidence": 0.94
        }
      ],
      "output_ref": {
        "kind": "oss_object",
        "uri": "oss://bucket/output/01JZ8Q7Y4W7X2Z6M8N9P0Q1R2S/result.json",
        "sha256": "6c4f9d1b8e22"
      }
    },
    "job_error": null,
    "callback": {
      "status": "delivering",
      "attempt": 1,
      "last_error": null,
      "next_retry_at": null
    },
    "status_url": "/api/v1/ai-jobs/jobs/01JZ8Q7Y4W7X2Z6M8N9P0Q1R2S",
    "created_at": "2026-06-21T22:30:00+08:00",
    "updated_at": "2026-06-21T22:33:01+08:00",
    "finished_at": "2026-06-21T22:33:00+08:00"
  }
}
```

### 14.7 Callback body 超限后的状态摘要

```json
{
  "code": "0",
  "msg": "success",
  "data": {
    "job": {
      "job_id": "01JZ8Q7Y4W7X2Z6M8N9P0Q1R2S",
      "client_request_id": "cpp-20260621-book-2042-tagging",
      "job_type": "story.tagging",
      "job_status": "succeeded",
      "job_progress": {
        "stage": "completed",
        "percent": 100,
        "message": "completed"
      },
      "job_result": {
        "book_id": "2042",
        "accepted": true,
        "output_ref": {
          "kind": "oss_object",
          "uri": "oss://bucket/output/01JZ8Q7Y4W7X2Z6M8N9P0Q1R2S/result.json",
          "sha256": "6c4f9d1b8e22"
        }
      },
      "job_error": null,
      "callback": {
        "status": "failed",
        "attempt": 0,
        "last_error": {
          "reason": "CALLBACK_BODY_TOO_LARGE",
          "details": {
            "size_bytes": 735232,
            "limit_bytes": 524288
          },
          "retryable": false
        },
        "next_retry_at": null
      },
      "status_url": "/api/v1/ai-jobs/jobs/01JZ8Q7Y4W7X2Z6M8N9P0Q1R2S",
      "created_at": "2026-06-21T22:30:00+08:00",
      "updated_at": "2026-06-21T22:33:01+08:00",
      "finished_at": "2026-06-21T22:33:00+08:00"
    }
  },
  "request_id": "01JZ8QE0A1B2C3D4E5F6G7H8J9",
  "server_time": "2026-06-21T22:33:10+08:00"
}
```

## 15. 验证标准

实现本文后至少验证：

| 场景 | 预期 |
|---|---|
| 创建 Job 成功 | HTTP `200`，`code="0"`，`data.job.job_status="queued"`，`job_result=null`。 |
| 查询 queued/running Job | HTTP `200`，`code="0"`，`job_result=null`，`job_error=null`。 |
| 查询 succeeded Job | HTTP `200`，`code="0"`，`job_result` 符合对应 `PublicResultSchema`。 |
| 查询 failed Job | HTTP `200`，`code="0"`，`job_error` 必填，顶层不返回错误信封。 |
| 创建请求参数非法 | HTTP `400`，返回 `ErrorEnvelope`，不创建 Job。 |
| 幂等键冲突 | HTTP `409`，返回 `ErrorEnvelope`，不创建新 Job。 |
| Job 不存在 | HTTP `404`，返回 `ErrorEnvelope`。 |
| 非法 `X-Request-ID` | HTTP `400`，业务码为请求 ID 非法语义，不静默替换。 |
| Callback URL 创建前非法 | HTTP `400`，返回 `ErrorEnvelope`，不创建 Job；覆盖 HTTPS、内网、localhost、link-local、metadata endpoint、重定向规则。 |
| Job 状态迁移 | 只允许 `queued -> running -> succeeded/failed` 和受控 `queued -> failed`，终态不可逆。 |
| `job_progress.stage` | 不包含 Callback 投递阶段；Callback 状态只在 `callback` 对象表达。 |
| 幂等相同请求 `reject_duplicate` | HTTP `409`，返回 `ErrorEnvelope`，不创建新 Job。 |
| 幂等相同请求 `return_existing` | HTTP `200`，返回已有 Job 的当前 JobEnvelope。 |
| 幂等同 key 不同语义 | HTTP `409`，返回 `ErrorEnvelope`，错误 reason 为幂等冲突。 |
| Callback 成功投递 | Callback payload 为 `CallbackEnvelope[JobEnvelope]`，事件与 Job 终态匹配，时间戳和签名头完整。 |
| Callback payload 快照 | payload 内 `job.callback.status` 不承诺本次投递结果，第一次投递不得写成 `delivered`。 |
| Callback 签名校验 | 接收方可用 `timestamp + "." + raw_body` 和当前有效 `callback_secret` 校验签名。 |
| Callback 重放窗口 | 超过 5 分钟窗口的请求被接收方拒绝。 |
| Callback ACK 成功 | HTTP `2xx`、`Content-Type=application/json`、合法 JSON object、`accepted=true`。 |
| Callback ACK 非法 | 空 body、HTTP `204`、非 JSON、非法 schema 都按 ACK 合同错误处理。 |
| Callback ACK 拒绝 | `accepted=false` 统一按可重试失败处理，直到达到最大尝试次数或最大投递窗口。 |
| Callback ACK retry hint | `details.retry_after_seconds` 不影响服务端 `next_retry_at` 计算。 |
| Callback 投递失败 | 只更新 `callback` 聚合状态，不改变 Job 终态；可重试时 `attempt` 递增、`event_id` 不变。 |
| Payload 尺寸限制 | `metadata`、`job_result`、`job_error.details`、Callback body 超限行为符合第 11.4 节，Callback body 超限 reason 为 `CALLBACK_BODY_TOO_LARGE`。 |
| 未发起投递失败 | Callback body 未构造或未发起外部 HTTP 请求时不递增 `attempt`。 |
| 轮询与 Callback 一致 | 同一 Job 的终态轮询与 Callback 在业务状态和公开结果上同源；`job.callback` 可因投递状态变化不同。 |
| OpenAPI | HTTP API 展示 `HttpEnvelope` / `ErrorEnvelope`，Callback 展示独立 `CallbackEnvelope`。 |

## 16. 最小交付清单

第一阶段实现本文应包含：

- `HttpEnvelope[T]` 与 `ErrorEnvelope` 已按统一 FastAPI 响应信封标准落地。
- `JobResponseData` 只包含 `job`。
- `JobEnvelope[JobResult]` 统一定义，并被 `POST /jobs`、`GET /jobs/{job_id}` 和 Callback 复用。
- `CallbackEnvelope[JobEnvelope[JobResult]]` 统一定义。
- Callback ACK schema 独立定义，不复用本服务 HTTP 响应信封。
- Callback 传输安全头、签名算法、重放窗口和 SSRF 防护已定义。
- canonical request hash、canonicalization 规则和幂等模式行为已实现并有合同测试。
- Job 状态迁移和 Callback 重试状态机已实现并有合同测试。
- `CallbackErrorDetail` 已与 `JobErrorDetail` 语义拆分。
- Job 创建前错误、Job 执行期错误、Callback 投递错误三类错误分层明确。
- Payload 尺寸限制、显式裁剪和对象存储引用策略已实现。
- 业务码注册表覆盖 Job HTTP 错误、Job 执行错误和 Callback 投递错误。
- OpenAPI 展示最终外部结构。
- 合同测试覆盖创建、查询、失败 Job、Callback、错误信封、ACK、签名、幂等、尺寸限制和轮询/Callback 一致性。
