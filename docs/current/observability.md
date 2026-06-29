# 当前可观测与日志规范

本文回答一个问题：面对一个新业务点，应该如何思考、设计和验证日志。它同时记录当前已经落地的日志出口、格式和运行形态；外部 HTTP envelope、错误码和 header 合同以 [`../api/service-contract.md`](../api/service-contract.md) 为准。

## 先理解日志的职责

日志不是业务数据副本，也不是把每一步执行都打印出来的流水账。日志是生产问题发生后，用来还原因果链的索引。

本项目的日志思考顺序是：

```text
新业务点
  -> 它是否跨越了一个排障边界?
  -> 生产出问题时这条日志是否能定位?
  -> 应该记录在哪一层?
  -> 应该使用什么级别?
  -> 需要哪些稳定关联字段?
  -> 哪些内容必须排除?
  -> 最后由 stdout/stderr 进入本地或平台日志系统
```

先判断日志是否有排障价值，再决定字段、级别和出口。不要从“我要多打一条日志”开始。

## 第一步：识别业务点是否跨越排障边界

只有跨越排障边界的业务点才优先考虑日志。排障边界指“出了问题后，维护人员需要知道系统走到了哪里、卡在哪个对象、失败属于哪一类”的位置。

| 边界 | 应记录的事件 | 不应记录的细节 |
|---|---|---|
| HTTP API | 请求完成、请求失败、鉴权或参数异常摘要 | 完整请求体、完整响应体、token |
| Job lifecycle | created、published、started、succeeded、failed | 每个普通字段更新 |
| Worker execution | attempt claim、skip、start、success、failure、retry decision、lease/recovery | 执行循环里的每个内部步骤 |
| AI provider | provider/model/operation/duration/usage 摘要、失败分类 | prompt 全文、模型响应全文 |
| Object storage | object key、content type、bytes、sha256 | 文件内容、图片二进制、base64 |
| Callback | scheduled、delivered、failed、dead-letter | 完整 callback body |
| Recovery | recovered count、skipped count、失败分类 | 每轮扫描的完整候选列表 |
| 脚本 | 人读结果、错误原因、JSON 机器输出 | 人读文本混入 `--json` stdout |

普通 DB CRUD、纯函数内部计算、可从数据库精确查询的完整对象，不默认打日志。日志只放能把人带到事实源的索引字段。

## 第二步：用三个问题决定是否值得打

新增日志前必须回答三个问题：

1. 生产出问题时，这条日志能不能帮助定位？
2. 这条日志有没有稳定的关联字段，比如 `request_id`、`job_id`、`attempt_id`、`callback_id`？
3. 这条日志会不会泄露敏感内容或输出大 payload？

三个答案分别是“能 / 有 / 不会”，才值得打。

如果答案是“可能有用”，但没有稳定关联字段，通常不应新增日志。应先补上能关联到请求、Job、attempt、callback、provider call 或对象存储产物的标识符。

如果答案是“能定位”，但需要打印完整 payload 才能定位，通常说明事实源设计不对。应把完整数据放在数据库、对象存储、AI call ledger、audit event 或 API response 中，日志只记录可查索引。

## 第三步：选择日志所在层

日志应打在拥有语义的层，不要打在所有调用链上。

| 层 | 本项目规则 |
|---|---|
| API 层 | 记录请求完成、请求失败、重要鉴权/参数异常摘要 |
| Job service / repo | 不为每个普通 DB 操作打日志；状态迁移、幂等冲突、容量拒绝可以打 |
| Worker | 记录 attempt claim、start、success、failure、retry decision、lease/recovery |
| AI provider | 记录 provider、model、operation、duration、usage 摘要；不记录 prompt 全文和模型全文 |
| Object storage | 记录 key、content type、bytes、sha256；不记录文件内容 |
| Callback | 记录 scheduled、delivered、failed、dead-letter；不记录完整 callback body |
| 脚本 | 人读输出走 stdout，错误走 stderr；`--json` 时 stdout 只允许 JSON |

同一个事件不要在多层重复记录。上层记录“请求/业务意图”，下层记录“边界调用/失败分类”。如果两条日志没有不同的排障作用，应保留更靠近语义边界的一条。

## 第四步：选择日志级别

日志级别表达维护优先级，不表达开发者情绪。

| 级别 | 使用场景 |
|---|---|
| `INFO` | 正常关键状态变化，例如请求完成、Job created、attempt started、Job succeeded |
| `WARNING` | 可恢复但需要关注的异常路径，例如领取重试、幂等冲突、外部依赖临时失败后会重试 |
| `ERROR` | 当前操作失败，需要排障，例如 Job failed、callback dead-letter、不可恢复的 provider error |
| `DEBUG` | 默认不依赖；不能作为生产排障必需证据 |

不要用 `ERROR` 表示可预期的业务拒绝，例如参数非法、鉴权失败或容量门禁拒绝。这类场景应返回稳定错误，并用可检索字段记录摘要。

## 第五步：选择字段

当前应用日志格式：

```text
%(asctime)s level=%(levelname)s logger=%(name)s request_id=%(request_id)s %(message)s
```

日志消息正文使用 `key=value` 形式。新增业务关键日志优先使用 `app.core.logging.log_event()`，不要手写一套不一致的事件字段。

字段选择顺序：

1. 先放关联字段：`request_id`、`job_id`、`attempt_id`、`callback_id`、`caller_id`。
2. 再放状态字段：`status`、`from_status`、`to_status`、`retry_decision`、`reason`。
3. 再放定位字段：`job_type`、`provider`、`model`、`operation`、`object_key`。
4. 再放度量字段：`duration_ms`、`bytes`、`image_count`、`token_count`。
5. 失败时放分类字段：`error_kind`、`failure_phase`、`code`、`http_status`。

| 字段 | 规则 |
|---|---|
| `request_id` | HTTP 请求内由 `RequestIDMiddleware` 设置；非 HTTP 后台任务没有上游请求时可以是 `-` 或任务上下文传入值 |
| `event` | 必须来自 `app.core.logging.LogEvent` 白名单 |
| `job_id` / `attempt_id` / `callback_id` | 有对应对象时记录，用于串联 Job 排障 |
| `caller_id` | 有调用方身份时记录 |
| `duration_ms` | 记录耗时时使用毫秒整数或可直接比较的数值 |
| `error_kind` / `failure_phase` | 失败日志优先记录分类，不记录完整敏感输入 |

## 第六步：排除敏感内容和大 payload

默认不记录以下内容：

| 场景 | 是否打印 | 原因 |
|---|---:|---|
| 完整请求体 | 否 | 可能含敏感数据，体积不可控 |
| 完整响应体 | 否 | 会复制 API 合同输出，污染日志 |
| 完整 prompt 或模型响应 | 否 | 可能含隐私、版权内容或大文本 |
| 图片二进制、base64、大文件内容 | 否 | 体积过大，不适合日志系统 |
| token、secret、签名密钥、OSS access key | 否 | 安全风险 |
| 每个普通 DB CRUD 操作 | 通常否 | 噪声大，DB 本身是事实源 |
| 正常循环里的每一步细节 | 通常否 | 会淹没关键状态变化 |
| 捕获异常后只打印日志并继续 | 否 | 应分类失败或抛出，不能吞错 |

可以记录输入规模、条数、文件大小、content type、provider、model、operation、耗时、失败分类、Job/attempt/callback/ledger 标识符。对象存储 URL 或 key 只有在现有 API/结果本来会暴露时才记录；内部地址和签名 URL 不应作为默认日志字段。

需要调试某个复杂问题时，可以临时增加本地调试日志，但不能把调试噪声作为生产排障依赖。

## 第七步：确认日志出口

服务日志必须输出到 stdout/stderr。业务代码、FastAPI 启动路径和 worker 启动路径不得只写本地文件，也不得在应用代码里默认新增 `logging.FileHandler`。

```text
应用代码
  -> stdout/stderr
  -> 容器运行时、K8s 或平台日志采集

local dev.sh
  -> 启动 API/worker
  -> 将 stdout/stderr 重定向到 logs/api.log 和 logs/worker.log
```

应用本身只负责把服务日志写到 stdout/stderr。本地 `logs/` 目录只是 `./scripts/dev.sh` 的开发便利能力，不是生产日志合同。

当前实现：

- `app.core.logging.configure_logging()` 清空 root logger 旧 handler，并安装一个 `logging.StreamHandler(sys.stdout)`。
- API startup、Taskiq worker startup 和 recovery loop 都调用 `configure_logging()`。
- `start-api.sh` 直接执行 Uvicorn，API 进程日志继承进程 stdout/stderr。
- `start-worker.sh` 直接执行 Taskiq worker，worker 和 recovery loop 日志继承进程 stdout/stderr。
- `./scripts/dev.sh start` 在 local 模式下用 shell 重定向把 API/worker 的 stdout/stderr 分别写到 `logs/api.log` 和 `logs/worker.log`。
- `compose-full` 模式下 API/worker 日志来自容器 stdout/stderr，不写宿主机 `logs/api.log` 或 `logs/worker.log`。

`logs/` 是 local 模式排障入口：

| 文件 | 来源 | 用途 |
|---|---|---|
| `logs/api.log` | `./scripts/dev.sh` 重定向 API stdout/stderr | 本地 API 启动、请求、异常和压测排障 |
| `logs/worker.log` | `./scripts/dev.sh` 重定向 worker stdout/stderr | 本地 Taskiq worker、recovery loop、Job 执行排障 |

生产和 compose-full 环境应通过容器或平台日志命令导出 stdout/stderr，再按 runbook 传给排障脚本。

## 请求追踪

HTTP 请求的追踪边界：

- `RequestIDMiddleware` 接收合法 `X-Request-ID` 或生成新的 request id。
- request id 写入 `request.state.request_id`、日志上下文、响应头 `X-Request-ID` 和 HTTP envelope。
- 创建 Job 时，route 将 request id 传入 Job service，并写入 runtime system 字段；后续 Callback payload 使用同一个 trigger request id。

新增 API route 或 service 代码时，不要重新生成另一套 request id。需要记录请求相关日志时，直接使用当前日志上下文或显式传入已经存在的 request id。

## 业务事件白名单

`LogEvent` 是业务日志事件名白名单。新增事件必须同时满足：

- 事件名加入 `app.core.logging.LogEvent` 和 `_LOG_EVENTS`。
- 对应 operation 或 job type 引用事件时，registry check 能通过。
- 测试覆盖新增事件的注册或引用路径。
- 文档只说明事件语义和排障价值，不把完整 payload 当成日志合同。

当前禁止直接使用临时字符串绕过 `log_event()`。未知事件会快速失败，避免生产日志事件名发散。

当前 `poster_title_image` 业务日志只记录跨边界摘要：

| 事件 | 语义 | 关键字段 | 禁止内容 |
|---|---|---|---|
| `poster_title_image_style_probe_completed` | 风格探测 leaf 完成 | `job_id`、`root_job_id`、`attempt_id`、`trigger_request_id`、`caller_id`、`job_type`、`workflow_node_key`、`operation`、`model_id`、`duration_ms` | 风格描述全文、prompt、参考图内容 |
| `poster_title_image_object_stored` | 单张标题层图片已写入对象存储 | `job_id`、`root_job_id`、`attempt_id`、`trigger_request_id`、`caller_id`、`job_type`、`item_id`、`language`、`image_index`、`oss_key`、`content_type`、`content_hash`、`bytes` | 图片 bytes、base64、public/internal URL、签名 URL |
| `poster_title_image_item_completed` | 单个 item/language 生成完成 | `job_id`、`root_job_id`、`attempt_id`、`trigger_request_id`、`caller_id`、`job_type`、`item_id`、`language`、`operation`、`model_id`、`image_count`、`duration_ms` | 标题文本、prompt、模型输出图内容 |
| `poster_title_image_join_completed` | root workflow 的结果汇总完成 | `job_id`、`root_job_id`、`attempt_id`、`trigger_request_id`、`caller_id`、`job_type`、`workflow_node_key`、`total`、`succeeded`、`failed`、`ai_model_ms`、`total_ms` | 完整结果 payload、对象 URL 列表 |

## 新增代码检查清单

新增或修改日志相关代码时，按顺序检查：

1. 这个业务点是否跨越排障边界？
2. 这条日志是否满足“能定位 / 有关联字段 / 不泄密不输出大 payload”？
3. 日志是否放在拥有语义的层，而不是多层重复打印？
4. 级别是否符合 `INFO` / `WARNING` / `ERROR` 规则？
5. 字段是否包含稳定关联字段和失败分类？
6. 是否没有新增默认 `FileHandler` 或只写文件的路径？
7. 是否使用 `log_event()` 记录业务关键事件？
8. 是否同步了相关测试和文档地图？

## 验证

日志规范的最小验证：

```bash
uv run pytest tests/test_logging.py
```

涉及已登记业务事件时，还应运行对应业务测试和 registry contract 测试。例如 `poster_title_image` 日志事件：

```bash
uv run pytest tests/test_poster_title_image.py tests/test_registry_contract.py tests/test_logging.py
```

涉及脚本日志读取、`logs/api.log` 或 compose-full 日志路径时，还应运行相关脚本测试或对应 runbook 中的只读命令。
