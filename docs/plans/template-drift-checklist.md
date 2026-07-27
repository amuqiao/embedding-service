# Template Drift Checklist

本文记录模板骨架仍需要持续收口的漂移检查项。当前已经落地的 registry governance 事实以 [`../current/registry-governance.md`](../current/registry-governance.md) 为准；外部调用合同以 [`../api/service-contract.md`](../api/service-contract.md) 为准；新增业务接入合同以 [`../api/extension-guide.md`](../api/extension-guide.md) 为准。

## Current Baseline

- `OperationSpec` 是 HTTP operation 的代码事实源，route decorator 从它派生 path、`operation_id`、`response_model`、成功状态和错误响应描述。
- `JobTypeSpec` 是 `job_type` 的代码事实源，覆盖 visibility、role、schema、retry、callback、snapshot、capability refs、error codes 和 log events。
- `WorkflowDefinition` 声明 `workflow_type`、`root_job_type`、版本、失败策略和节点上限，并纳入 registry consistency 校验。
- `./scripts/tools.sh registry --json` 输出 operation、job type、workflow、capability 和 tool 的机器可读投影。
- `./scripts/verify.sh check` 经由 registry check 校验注册图、route/OpenAPI 对齐和关键合同文档存在。

## Remaining Gaps

- `docs/api/service-contract.md` 当前只做 route method/path 漂移检查；尚未解析完整错误码表、Job 字段表或每个 `job_type` 的公开 contract matrix。
- workflow registry 不在启动期伪造参数编译动态 DAG；child `job_type` 穷尽校验仍依赖 workflow compiler 测试、业务 e2e 和运行期 `_create_child_job()` 校验。
- 正式业务 `job_type` 的 real-flow、load profile 和业务级 e2e 仍按业务接入时补充，没有统一生成器。

## Planned Work

- 将 service contract 中可结构化的 route matrix、错误码 matrix 和 public job type matrix 逐步纳入 drift gate。
- 为正式 `job_type` 增加最小 contract snapshot 测试：params schema、public result schema、callback 允许性、result snapshot 状态和公开错误码。
- 为正式 workflow job family 增加固定验收模板：root create、child execution、root terminal projection、callback mock、billing scope 和 recovery/idempotency 重放。
- 扩展 registry JSON 的消费测试，使新增正式业务必须显式确认 public/demo/internal 分类和验证入口。

## Acceptance

- 新增或修改 HTTP route 时，`OperationSpec`、route decorator、OpenAPI 和 `service-contract.md` 任一处漂移都会被 `./scripts/verify.sh check` 捕获。
- 新增正式 `job_type` 时，缺少 schema、error/log event、capability/tool 引用、contract snapshot 或必要业务验证入口会被测试捕获。
- 新增 workflow job family 时，root/child role、workflow definition 元数据、compiler 测试和业务 smoke/e2e 均有明确失败信号。
