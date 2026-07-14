# 当前可观测与日志规范

本文是服务日志和业务事件的当前事实源，也是新增日志代码时的准入规则。外部 HTTP envelope、错误码和 header 合同以 [`../api/service-contract.md`](../api/service-contract.md) 为准。

## 当前日志出口

应用代码只把服务日志写到 stdout/stderr。生产、compose-full 和已部署 Pod 环境应依赖容器或平台日志采集，不在应用代码中默认写本地日志文件。

本地 `./scripts/dev.sh` 的 local 模式会把 API / worker stdout/stderr 重定向到：

| 文件 | 来源 | 边界 |
|---|---|---|
| `logs/api.log` | API 进程 stdout/stderr | 只属于本地开发排障便利 |
| `logs/worker.log` | worker 进程 stdout/stderr | 只属于本地开发排障便利 |

不要在应用代码中新增默认 `logging.FileHandler`，也不要让服务日志只写本地文件。

## 日志格式和 request_id

`RequestIDMiddleware` 负责确定本次 HTTP 请求的 `request_id`，并写入：

- `request.state.request_id`
- 日志上下文
- `X-Request-ID` 响应头
- 成功或错误 envelope 的 `request_id`

创建 Job 时，route 会把同一个 request id 传入 Job service，并写入 `runtime_fields._system.trigger_request_id`。后续 Callback payload 的 `trigger_request_id` 也来自这个值。

新增 API route 或 service 代码时，不要重新生成另一套 request id。后台任务没有 HTTP 上游请求时，可以使用任务上下文中的 request id；没有上下文时使用 `-` 或明确的后台任务标识。

## 业务事件白名单

结构化业务事件使用 `app.core.logging.log_event()` 和 `LogEvent` 白名单。新增事件必须同步 registry 引用和测试。

当前日志事件只记录可排障索引和稳定分类，不记录完整 payload。典型字段包括：

```text
job_id
root_job_id
attempt_id
caller_id
job_type
event
status
error_code
failure_phase
duration_ms
provider
model_id
```

`job_audit_events` 是 Job 时间线和排障证据，不驱动恢复；日志也不应替代数据库、对象存储、AI ledger 或 callback outbox 的事实源。

## 敏感内容边界

默认不得记录：

- 密钥、token、签名、完整连接串密码
- 完整请求体、完整模型响应、完整 callback ack body
- 图片、音频、视频二进制或 base64 payload
- provider raw response
- signed URL、内部对象存储地址或包含 query secret 的 callback URL

可以记录输入规模、条数、文件大小、content type、provider、model、operation、耗时、失败分类和可查询标识符。对象存储 URL 或 key 只有在现有 API / result 本来会暴露时才可记录；内部地址和签名 URL 不作为默认日志字段。

## 新增日志准入

新增日志前先判断它是否跨越排障边界。排障边界指“出了问题后，维护人员需要知道系统走到了哪里、卡在哪个对象、失败属于哪一类”的位置。

优先记录这些位置：

| 层 | 推荐记录 |
|---|---|
| API route | 请求准入、鉴权失败、参数 envelope 失败、Job 创建结果摘要 |
| Job service / repo | 状态迁移、幂等冲突、容量拒绝、恢复收敛结果 |
| Worker / executor | attempt claim、执行开始/结束、可恢复失败、不可恢复失败 |
| Provider adapter | provider 调用边界、耗时、标准化失败分类 |
| Callback | 投递开始/结果、重试、dead-letter |
| Recovery | 每轮收敛摘要和异常路径 |

不建议为每个普通 DB 查询、普通字段转换、循环内部小步骤或完整 payload 打日志。需要完整数据时，应把完整数据放在数据库、对象存储、AI call ledger、audit event 或 API response 中，日志只记录可查索引。

## 日志级别

| 级别 | 当前语义 |
|---|---|
| `DEBUG` | 本地临时调试，不作为生产排障依赖 |
| `INFO` | 正常生命周期关键点，例如 Job created、attempt claimed、callback delivered |
| `WARNING` | 可恢复但需要关注的异常路径，例如外部依赖临时失败后会重试 |
| `ERROR` | 当前操作失败且需要排障，例如 Job failed、callback dead-letter、不可恢复 provider error |

不要用 `ERROR` 表达已按设计恢复的瞬时问题，也不要用 `INFO` 打大 payload。

## 验证

修改日志事件、日志格式、request id 或敏感字段边界后，至少运行：

```bash
uv run pytest tests/test_logging.py
```

涉及 Job、registry 或业务 executor 日志时，还应运行相关测试和：

```bash
./scripts/verify.sh check
```
