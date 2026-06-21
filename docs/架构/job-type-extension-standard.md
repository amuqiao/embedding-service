# 新增 job_type 标准接入规范

本文定义新增 `job_type` 的固定接入路径。当前实现采用：

```text
Registry 装饰器 + JobExecutor ABC 模板方法 + get_job_executor() 简易工厂函数
```

一个能力必须同时声明 schema、runtime fields、执行入口、结果投影、错误码、日志事件和验证。

## 文档职责

本文负责说明：

- 新增 `job_type` 应放在哪些模块。
- 通用 Job 壳和能力独有 schema 如何分工。
- 每次新增 `job_type` 必须补充哪些信息。
- 如何通过 `JobExecutor` 和 registry checks 支持横向扩展。

本文不负责具体模型提示词、业务标签体系或调用方业务流程。

## 接入心智模型

```text
job_params
  ↓ ParamsSchema
normalized job_params
  ↓ runtime_job_fields()
RuntimeFieldsSchema
  ↓ JobExecutor.execute()
CanonicalResultSchema
  ↓ public_result()
PublicResultSchema
  ↓
JobEnvelope.job_result
  ↓
CallbackEnvelope.job.job_result
```

`job_type` 只定义能力自己的输入、运行时派生字段和输出；通用 Job 外壳只能复用公共定义。第一版 Taskiq Job MVP 不引入 `JobWorkItem`、`job_steps`、DAG、chain、group 或 chord。

`job_type` 不定义 HTTP 响应外壳，也不定义 Callback 公共外壳。HTTP 请求成功/失败使用统一 `HttpEnvelope` / `ErrorEnvelope`；Job 状态使用统一 `JobEnvelope`；Callback 终态事件使用统一 `CallbackEnvelope`。

## 模块边界

公共模块：

```text
app/schemas/jobs.py          CreateJobRequest、JobEnvelope、JobResult、通用 artifact
app/schemas/callbacks.py     CallbackEnvelope、CallbackResponseEnvelope
app/schemas/errors.py        JobErrorDetail、CallbackErrorDetail
app/jobs/base.py             JobExecutor、JobTypeSpec
app/jobs/registry.py         register_job_type()、registry 查询和 Job 视图校验
app/jobs/factory.py          get_job_executor()
app/jobs/runner.py           Taskiq attempt 内调用的共享 Job 执行器
app/jobs/types/register.py   显式导入所有内置 job_type，触发装饰器注册
app/core/error_registry.py   ErrorSpec
app/core/logging.py          LogEvent
app/schemas/registry.py      schema 名称反查表
```

能力独有模块：

```text
app/schemas/jobs.py 或 app/schemas/jobs/<job_type>.py
  ParamsSchema
  RuntimeFieldsSchema
  CanonicalResultSchema
  PublicResultSchema

app/jobs/types/<job_type>.py
  @register_job_type
  class XxxJob(JobExecutor)

tests/test_<job_type>_workflow.py
  schema、执行、结果投影和 registry metadata 测试
```

当前仓库仍使用单文件 `app/schemas/jobs.py`。当 `job_type` 继续增加时，应优先把能力独有 schema 拆到 `app/schemas/jobs/<job_type>.py`，公共 Job 壳保持不动。

## 标准接入步骤

1. 定义 `ParamsSchema`。

   `ParamsSchema` 是 `CreateJobRequest.job_params` 的唯一事实源，必须拒绝未知字段。业务必需字段不得放入 `metadata` 或 `options`。

2. 定义 `RuntimeFieldsSchema`。

   `runtime_job_fields()` 返回的字段必须能被该 schema 表达。运行时派生值进入 runtime snapshot，不进入 HTTP 顶层。

3. 定义 `CanonicalResultSchema`。

   canonical result 是内部事实源，用于恢复、副作用和最终投影。模型原始输出、中间结果和公开结果不能互相替代。

4. 定义 `PublicResultSchema`。

   `JobEnvelope.job_result` 和 `CallbackEnvelope.job.job_result` 必须来自同一份 public result 投影。允许显式声明 public result 为 `null`，但轮询和 callback 必须一致。

   `PublicResultSchema` 只能进入 `job_result`，不得扩展 `JobEnvelope` 公共顶层字段，也不得为 Callback 单独增加轮询接口不可见的业务字段。

5. 实现 `JobExecutor` 子类。

   executor 必须声明：

   ```text
   name
   params_schema
   runtime_fields_schema_name
   canonical_result_schema
   public_result_schema
   allow_callback
   max_attempts
   timeout_seconds
   large_artifact_keys
   allowed_error_codes
   log_events
   ```

   `params_schema`、`runtime_fields_schema_name`、`canonical_result_schema` 和 `public_result_schema` 必须能在 `app/schemas/registry.py` 反查。当前未实际发射的日志事件不要提前写入 `log_events`，避免 registry 对可观测性做过度承诺。

   如果 `allow_callback=True`，该能力只声明是否允许终态通知，不得自定义 Callback 公共字段、HTTP 头、签名算法或 ACK 语义。Callback URL 安全、终态事件、稳定 `event_id`、签名头和 ACK 合同由公共 Callback 层统一负责。

6. 实现执行入口。

   - 自定义运行时：覆盖 `_execute(job, db)`，返回 canonical result dict。
   - 内置 LLM 文本运行时：让 `_execute()` 返回 `None`，并实现 `parse_output(text)`。
   - 成功前副作用：按需覆盖 `run_success_side_effect(job, canonical_result, db)`。

7. 注册 job_type。

   在 `app/jobs/types/<job_type>.py` 使用 `@register_job_type` 装饰器，并在 `app/jobs/types/register.py` 中显式 import 模块。注册入口应幂等，registry 不允许静默覆盖不同类的重复 `job_type`。

8. 补错误码。

   `allowed_error_codes` 中的 reason 必须存在于 `app/core/error_registry.py`。能力独有错误可以新增，但仍使用统一 `ErrorSpec`。

9. 补日志事件。

   `log_events` 只能引用 `app/core/logging.py` 中登记的稳定事件。日志记录应包含 `job_id`、`job_type`、`job_status`、`execution_generation`、`stage`、`error_reason` 等字段。

10. 补测试和文档。

   新增 `job_type` 至少要覆盖参数校验、执行结果、Job envelope、Callback envelope、失败错误、registry consistency。

   如果允许 callback，还应覆盖该 `job_type` 的 public result 能够同时通过轮询和 Callback payload 投影；不需要为每个 `job_type` 重测公共 Callback 传输头和 ACK 状态机。

## 错误分层

Job 公共错误：

```text
INVALID_JOB_TYPE
INVALID_JOB_PARAMS
INVALID_INPUT
JOB_STATE_TRANSITION_CONFLICT
JOB_EXECUTION_FAILED
JOB_TIMEOUT
MODEL_CALL_FAILED
MODEL_OUTPUT_INVALID
```

能力独有错误：

```text
<JOB_TYPE>_INPUT_INVALID
<JOB_TYPE>_OUTPUT_INVALID
<JOB_TYPE>_SIDE_EFFECT_FAILED
```

规则：

- 能复用公共错误时优先复用公共错误。
- 能力独有错误必须登记 `scope="job"`，`owner` 写具体能力或模块。
- `job_error.reason` 和 `callback.last_error.reason` 都必须可反查同一张 `ErrorSpec` 表。
- HTTP 请求期错误通过 `ErrorEnvelope.code/msg/data` 对外表达；内部 reason 来自 `AppError` / `ErrorSpec`，不得要求调用方依赖旧式 `data.error.reason`。
- Job 执行期错误写入 `JobErrorDetail`，Callback 投递错误写入 `CallbackErrorDetail`，两者语义不能混用。
- 不允许把异常吞掉后写空结果或默认成功。

## 进度与 Callback 边界

`job_progress.stage` 只描述 Job 执行进度，不描述 Callback 投递进度。Callback 投递状态只能通过 `callback.status`、`callback.attempt`、`callback.last_error` 和 `callback.next_retry_at` 表达。

规则：

- `job_type` 不得新增 Callback 专属 `job_progress.stage`。
- Callback 投递失败不得修改 `job_status`、`job_result` 或 `job_error`。
- 终态 Job 的轮询结果和 Callback payload 必须复用同一份公开 Job 投影。
- 历史内部状态如 `delivering_callback` 若存在，只能在公共投影层映射到合法 Job 执行阶段，不得外露给调用方。

## 新增 job_type 检查清单

```text
[ ] ParamsSchema 已定义并拒绝未知字段
[ ] RuntimeFieldsSchema 已定义或用稳定 schema 名称声明
[ ] CanonicalResultSchema 已定义，内部结果会被校验
[ ] PublicResultSchema 已定义；如为 null 已显式声明
[ ] 以上 schema 名称已登记到 app/schemas/registry.py
[ ] JobExecutor 声明完整 JobTypeSpec metadata
[ ] runtime_job_fields() 返回值符合 RuntimeFieldsSchema
[ ] _execute() / parse_output() / run_success_side_effect() 路径明确
[ ] allow_callback 策略明确
[ ] allow_callback=True 时不自定义 Callback 公共外壳、传输头、签名或 ACK 合同
[ ] max_attempts 和 timeout_seconds 策略明确
[ ] large_artifact_keys 策略明确
[ ] allowed_error_codes 全部已注册
[ ] log_events 全部已注册
[ ] @register_job_type 已使用，register_all_job_types() 显式导入且保持幂等
[ ] 参数校验测试通过
[ ] 执行结果和 public_result 投影测试通过
[ ] Job envelope / Callback envelope 合同测试通过
[ ] job_progress.stage 不包含 Callback 投递阶段
[ ] registry consistency 检查通过
[ ] 对接文档只描述该能力字段，不重写通用 Job 壳
```

## 维护原则

新增能力时优先横向增加 `JobExecutor`、schema 和测试，不修改通用 Job 外壳。只有当多个 `job_type` 出现重复字段、重复执行路径或重复结果投影规则时，才考虑抽公共模块。
