# 新增 HTTP 接口标准接入规范

本文定义新增业务 HTTP 接口的固定接入路径：接口不是只加一个 route，而是同时登记 operation、schema、错误、日志和测试。

## 文档职责

本文负责说明：

- 新增 HTTP 接口应放在哪些模块。
- 公共模块和接口独有模块如何分工。
- 每次新增接口必须补充哪些信息。
- 如何通过 registry 和测试防止接口漂移。

本文不负责具体业务字段设计，也不替代 `project-standards.md` 中的全局 envelope、异常、日志和安全边界规范。

## 接入心智模型

```text
业务需求
  ↓
Request / Response data schema
  ↓
OperationSpec
  ↓
FastAPI route
  ↓
AppError / ErrorSpec
  ↓
LogEvent
  ↓
contract tests + registry check
```

接口必须同时进入三层事实源：

| 层级 | 文件 | 职责 |
|---|---|---|
| 公共外壳 | `app/schemas/envelope.py`、`app/schemas/errors.py` | `ResponseEnvelope`、`ErrorDetail` 统一定义。 |
| 接口登记 | `app/api/operations.py` | `operation_id`、schema、错误码、日志事件和副作用声明。 |
| 路由实现 | `app/api/routes/*.py` | 只做 HTTP 入站、鉴权依赖、调用 application/service、返回标准 envelope。 |

## 模块边界

公共模块只表达跨接口稳定语义：

```text
app/schemas/envelope.py      HTTP 成功/错误外壳
app/schemas/errors.py        ErrorDetail
app/core/error_registry.py   全局错误码注册表
app/core/logging.py          稳定日志事件和日志 helper
app/api/operations.py        HTTP operation registry
app/schemas/registry.py      schema 名称反查表
```

接口独有模块只表达该接口自己的输入、输出和业务调用：

```text
app/schemas/<domain>.py      该领域 request / response data schema
app/api/routes/<domain>.py   该领域 route
app/services/<domain>.py     该领域业务编排
tests/test_<domain>.py       该领域合同和行为测试
```

禁止在 route 中复制 `code/msg/data/request_id/server_time`，也禁止 route 临时拼错误响应。

## 标准接入步骤

1. 定义 request schema。

   所有 request schema 必须继承 `StrictBaseModel` 或等价地拒绝未知字段。字段要明确类型、必填、长度、范围、枚举和 `null` 语义。

2. 定义 response data schema。

   route 的 `response_model` 必须是 `ResponseEnvelope[DataSchema]`。`DataSchema` 只表达业务数据，不包含 HTTP 外壳字段。

3. 登记 `OperationSpec`。

   在 `app/api/operations.py` 补充：

   ```text
   operation_id
   channel
   method / path
   auth_boundary
   request_schema
   response_data_schema
   error_codes
   idempotency_key
   side_effects
   log_events
   metrics
   change_policy
   ```

   `request_schema` 和 `response_data_schema` 必须能在 `app/schemas/registry.py` 反查到当前 schema 类型。

4. 实现 route。

   route decorator 必须显式使用 registry 中的 `operation_id`：

   ```python
   @router.post(
       "/example",
       response_model=ResponseEnvelope[ExampleData],
       operation_id=OperationID.CREATE_EXAMPLE,
   )
   ```

5. 统一错误。

   业务错误必须抛 `AppError` 或其子类，`reason` 必须已登记在 `app/core/error_registry.py`。接口独有错误可以新增，但仍进入同一张错误注册表，并声明 `scope="http"` 或具体 owner。

6. 统一日志。

   新接口的 `OperationSpec.log_events` 只能引用 `app/core/logging.py` 中登记的稳定事件。接口实现不得默认记录请求体、响应体、密钥、token、Prompt、模型输出或大载荷。

7. 补测试。

   至少覆盖：

   ```text
   成功响应 envelope
   失败响应 envelope
   OpenAPI operationId
   operation registry consistency
   error reason 已注册
   ```

## 错误分层

公共错误用于所有接口：

```text
UNAUTHORIZED
FORBIDDEN
NOT_FOUND
INVALID_INPUT
INTERNAL_ERROR
METHOD_NOT_ALLOWED
```

接口独有错误用于某个接口或领域：

```text
CLIENT_REQUEST_ID_CONFLICT
INVALID_JOB_TYPE
MODEL_NOT_AVAILABLE
QUEUE_FULL
```

规则：

- 公共错误和接口独有错误共用 `ErrorSpec`。
- `OperationSpec.error_codes` 声明该接口可能对外返回或记录的 reason。
- 未登记 reason 不能进入 HTTP error envelope。

## 新增接口检查清单

```text
[ ] request schema 拒绝未知字段
[ ] response data schema 不包含 HTTP 外壳字段
[ ] route response_model 使用 ResponseEnvelope[DataSchema]
[ ] route 显式 operation_id
[ ] app/api/operations.py 已登记 OperationSpec
[ ] OperationSpec 的 schema 名称已登记到 app/schemas/registry.py
[ ] OperationSpec.error_codes 全部存在于 error registry
[ ] OperationSpec.log_events 全部存在于 log event registry
[ ] route 不直接拼错误响应
[ ] contract test 覆盖成功和失败 envelope
[ ] registry consistency 测试通过
[ ] 文档或示例已同步
```
