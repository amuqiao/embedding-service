# Job / AI Gateway / Billing 心智模型

本文为本项目定调：`Job` 是当前主要异步执行外壳，AI gateway 是模型调用能力层，Billing 是对 AI call ledger 的读模型。计费事实应先围绕“真实模型调用”建立，Job billing 只是其中一种 scope 投影。

```text
AI gateway
  统一模型调用入口

AI call ledger
  每次真实 LiteLLM / provider call 一行账本

Billing read model
  从 AI call ledger 聚合，不反控 Job 或 provider 调用

Job runtime
  当前主要异步执行外壳，是 AI gateway 的一种消费者
```

## 文档职责

本文负责回答：

- `Job`、AI gateway 和 Billing 分别是什么。
- `model_id`、runtime snapshot、`ai_call_logs` 和 Billing read model 分别保存什么事实。
- 为什么 `ai_call_logs` 不应强绑定 `job_id`。
- Job 如何通过 `scope_type="job"` 获得 Job 级 billing。
- 哪些依赖方向是允许的，哪些依赖方向应避免。

本文不负责：

- Taskiq Job 状态机、Attempt lease、Callback outbox 的字段级设计。
- 具体 `job_type` 的参数、Prompt、模型输出 schema。
- 价格配置、成本计算公式和 billing envelope 字段合同。
- 用户余额、扣款、发票、退款、税务或最终财务对账。

AI gateway 层目标设计见 [`../设计文档/ai-gateway-layer-design.md`](../设计文档/ai-gateway-layer-design.md)。阅读路径和相关文档统一在 [`../README.md`](../README.md) 维护。

## 1. 总体心智模型

当前本服务的主要对外工作方式仍是 AI Job：

```text
调用方 / 业务后端
  ├─ 创建 Job
  ├─ 查询 Job
  └─ 接收 Callback
```

当前代码已经落地首个 Job scope billing 路径：

- `ai_call_logs` 记录每次真实 AI provider 调用的 ledger、usage、cost estimate 和 scope 归属。
- `app/services/ai_gateway_facade.py` 统一编排 model gate、ledger、LiteLLM adapter、usage 提取和成本估算。
- `GET /api/v1/ai-jobs/jobs/{job_id}/billing` 返回 `JobBillingResponseData(billing=BillingEnvelope)`。
- `scripts/real-flow.sh` 可在显式 `--confirm-cost` 后手动触发真实 LLM Job 并查询 billing 证据。

通用 scope billing 查询、caller 时间窗口聚合、批量导出和非 Job scope 的公开 HTTP 合同仍是目标设计，不是当前已开放 API。

AI cost 的底层事实不应由 Job 表拥有，而应由 AI gateway 统一创建调用账本。

```text
Job runtime
  ├─ 创建 Job / Attempt
  ├─ 管理状态、租约、恢复、结果投影和 Callback
  └─ 调用 JobExecutor
        ↓
AI gateway
  ├─ 校验 model_id
  ├─ 调用 LiteLLM / provider adapter
  ├─ 写入 ai_call_logs
  └─ 返回模型输出、usage 或稳定错误
        ↓
Billing read model
  └─ 按 scope_type / scope_id 聚合 ai_call_logs
```

核心判断：

- `Job` 是平台执行外壳，不是 AI provider call。
- AI gateway 是模型调用能力层，不直接控制 Job 状态机。
- `ai_call_logs` 是 AI provider call 明细账本，不是 `jobs` 表的扩展字段集合。
- Billing 依赖可计量调用事实，不依赖 Job 状态机。
- Job billing 绑定 `scope_type="job"` 和 `scope_id=job_id`，不表示所有 AI 计费都必须来自 Job。

## 2. 三个对象分别回答什么

| 对象 | 回答的问题 | 事实源 | 对外形态 |
|---|---|---|---|
| `Job` | 这次异步执行现在是什么状态，公开结果或公开错误是什么 | `jobs`、`job_attempts`、`callback_outbox`、`job_events` | `JobEnvelope` |
| AI gateway | 这次模型调用应该使用哪个服务模型、provider model，并返回什么模型输出和 usage | `model catalog`、LiteLLM adapter、provider response | 上层调用方的执行结果 |
| Billing | 哪些模型调用发生了，usage 是多少，按哪个价格版本估算了多少成本 | `ai_call_logs`、pricing snapshot | scope billing、Job billing、内部明细查询或导出 |

这三类事实不能混成一张表或一个公共外壳。

```text
JobEnvelope 不保存 usage/cost 明细。
ai_call_logs 不保存 Job 当前状态。
Billing read model 不决定 Job succeeded / failed。
AI gateway 不直接投递 Callback。
```

## 3. 数据分层

### 3.1 Job 相关数据

Job 表只保存平台执行状态和对外投影：

```text
jobs
  Job 聚合根，保存状态、进度、公开结果、公开错误、Callback 聚合状态和 runtime 引用。

job_attempts
  保存一次执行尝试的发布、领取、租约、超时和错误。

callback_outbox
  保存终态事件的可靠投递账本。

job_events
  保存生命周期事件，用于排障和审计。
```

Job 表不应保存：

- provider 原始响应。
- 完整 Prompt 或完整模型输出。
- token usage 明细。
- 成本计算明细。
- provider 私有参数。

### 3.2 AI gateway 相关数据

AI gateway 读取或冻结模型调用所需事实：

```text
model catalog
  模型目录事实源。首版可以是 app/core/models.yaml。

runtime snapshot
  Job 创建时冻结本次 Job 执行需要的 model_id、Prompt、输出目标等运行时字段。

AI gateway facade
  应用服务层门面，编排 model gate、ledger、LiteLLM adapter、usage extraction 和 cost estimate。

LiteLLM / provider adapter
  把服务内 model_id 映射到 provider / LiteLLM model，并执行真实调用。
```

`model_id` 在 Job scope 中的流向应是：

```text
model catalog
  ↓ 创建期校验
runtime snapshot.runtime_fields.model_id
  ↓ Worker 执行期读取和再次 gate
AI gateway facade
  ↓ 调用时审计复制
ai_call_logs.model_id / provider / provider_model / pricing_ref
```

因此，`jobs` 表不需要顶层 `model_id`。如果某个 Job 不调用模型，`runtime_fields.model_id` 可以不存在；如果某个 Job 调用多个模型，每次调用也应分别进入 `ai_call_logs`。

### 3.3 AI cost / billing 相关数据

AI cost / billing 保存可计量调用事实和聚合投影：

```text
ai_call_logs
  每次真实 provider / LiteLLM 调用一行，保存 scope、调用状态、usage、pricing snapshot 和成本估算。

scope billing
  按 scope_type / scope_id 聚合 ai_call_logs 得到的成本估算返回对象。

Job billing
  scope_type="job" 且 scope_id=job_id 的 scope billing 投影。

pricing config
  把 provider usage 转换成内部 cost estimate 的配置事实源。
```

当前已新增 `ai_call_logs` 作为 AI call ledger。当前不需要新增 `job_billing_summaries` 事实表；如果后续读压或导出要求需要投影表，它也只能是 `ai_call_logs` 的派生读模型。

## 4. 允许的依赖方向

推荐依赖方向：

```text
Job runner 或 HTTP route
  -> capability service / JobExecutor
  -> AI gateway facade
  -> AI gateway ledger + LiteLLM adapter

AI gateway facade
  -> model catalog
  -> pricing config
  -> ai_call_logs repository
  -> LiteLLM / provider adapter

Billing service
  -> ai_call_logs repository
  -> billing schema
```

允许：

- Job runner 把 `job_id`、`attempt_id`、`caller_id`、`job_type`、`model_id` 等上下文作为 `scope_type="job"` 传给 AI gateway。
- 同步接口把 `request_id` 或业务请求 id 作为 `scope_type="sync_api"` 传给 AI gateway。
- AI gateway 返回模型输出、usage 和稳定错误。
- Billing service 按 scope 聚合 `ai_call_logs`。

不允许：

- AI gateway 直接修改 `jobs.status`。
- AI gateway 直接创建或投递 Callback。
- Billing service 直接改变 Job succeeded / failed。
- Job 状态机依赖 `cost_amount` 是否存在来判断执行成功。
- `jobs` 表直接保存 provider usage / cost 明细。
- 缺 usage、缺价格或成本计算失败时静默写 `0` 后继续成功。

一句话：Job 或其它入口可以调用 AI gateway，AI gateway 可以产生 usage，Billing 可以聚合 usage；反过来 AI gateway 和 Billing 都不应控制 Job 状态机。

## 5. 对外 API 心智模型

当前调用方以 Job 为中心工作：

```text
POST /api/v1/ai-jobs/jobs
  创建一次异步执行，返回 JobEnvelope。

GET /api/v1/ai-jobs/jobs/{job_id}
  查询执行状态、公开结果或公开错误。

Callback
  接收终态通知。
```

当前设计和实现中，Job billing 是 Job scope 的独立查询投影，而不是 `JobEnvelope` 顶层字段：

```text
GET /api/v1/ai-jobs/jobs/{job_id}/billing
  -> scope_type = "job"
  -> scope_id = job_id
  -> Job 终态后查询该 Job scope 的成本估算摘要
```

该接口已经作为首个公开 billing 投影落地；当前合同是 `JobBillingResponseData(billing=BillingEnvelope)`，并已进入 operation registry、schema registry、错误码和 route contract tests。

后续如果新增同步 AI 能力接口，应让该接口复用 AI gateway 和 `ai_call_logs`：

```text
POST /some-sync-api
  -> AI gateway(scope_type="sync_api", scope_id=request_id)
  -> ai_call_logs
```

但同步接口的 HTTP 路径、查询权限、billing 查询方式和保留期必须单独设计，不能因为有 ledger 就隐式开放。

## 6. 常见误区

### 6.1 “AI Job Service 是否就是 AI Gateway？”

不是。

```text
当前对外主入口：AI Job Service
内部模型调用层：AI gateway
执行适配器：LiteLLM / provider adapter
```

AI gateway 是内部能力层，用于连接 model catalog、LiteLLM adapter、usage ledger 和 cost estimate。它可以被 Job 使用，也可以被未来同步 API 或内部任务使用。

### 6.2 “计费是否必须绑定 Job？”

不应该。

Job billing 是重要的首个公开投影，但 AI cost 的底层事实应是：

```text
scope_type + scope_id + ai_call_logs
```

Job 只是：

```text
scope_type = "job"
scope_id = job_id
```

这样未来非 Job 调用也能计费，而不需要创建假 Job。

### 6.3 “model_id 是否应该放在 jobs 表顶层？”

不建议。

原因：

- 不是所有 Job 都调用模型。
- 一个 Job 未来可能调用多个模型。
- 模型选择属于具体 `job_type` 的运行时事实，应冻结在 runtime snapshot。
- 每次真实调用的模型审计事实应进入 `ai_call_logs`。

### 6.4 “BillingEnvelope 是否应该放进 JobEnvelope？”

首版不建议。

原因：

- 失败 Job 也可能已经产生模型费用，但失败时 `job_result=null`。
- 多 attempt、多步骤、多模型调用都需要明细账本聚合。
- Billing 的修复、导出、保留期和 Job 状态机不同。
- Callback 与轮询必须同源，贸然内联会扩大公共合同变化面。

### 6.5 “计费是否只服务 AI？”

MVP 中是，因为当前可计量对象是 AI provider call。

抽象上，计费服务依赖的是“可计量调用事实”，不是 AI 这个业务名词。未来如果出现非 AI 的可计量能力，应单独设计通用 metered usage，而不是提前把首版 `ai_call_logs` 泛化成过宽的表。

## 7. 设计检查清单

新增或修改相关能力时，应检查：

```text
Job:
  [ ] 是否只修改执行状态、结果投影、Callback 或 runtime snapshot。
  [ ] 是否避免把 provider usage / cost 明细塞进 jobs。

AI gateway:
  [ ] model_id 是否来自 model catalog。
  [ ] Worker 或同步接口调用前是否再次 gate。
  [ ] AI gateway 是否没有直接推进 Job 状态机。
  [ ] LiteLLM adapter 是否只做 provider 调用适配。

Usage ledger:
  [ ] 真实 provider 调用前是否先写 pending row。
  [ ] 每次真实 provider 调用是否都有独立 ai_call_logs。
  [ ] ai_call_logs 是否使用 scope_type / scope_id 表达归属。
  [ ] 缺 usage / 缺价格 / 成本计算失败是否 fail-fast。

Billing:
  [ ] Billing read model 是否只从 ai_call_logs 聚合。
  [ ] Job billing 是否只是 scope_type="job" 的投影。
  [ ] failed-but-billed 是否能表达。
  [ ] Callback 重试是否不改变 billing。
  [ ] 是否没有把 Billing read model 当成扣款、发票或最终对账事实。
```

如果某个实现需要打破这些规则，应先更新本文和对应设计文档，再进入开发。
