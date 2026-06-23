# Job / AI / Billing 心智模型

本文为本项目定调：本服务是 **Headless AI Job Platform Service**，`Job` 是平台执行外壳，AI 是底层能力层，Billing 是对可计量调用的账本和读模型。

```text
Headless AI Job Platform Service
  Job 是平台执行外壳
  AI 是底层能力层
  Billing 是对可计量调用的账本 / 读模型
```

这一定调决定了后续所有实现边界：

- 对外主入口围绕 Job，而不是同步 AI gateway。
- Job 状态机不保存 AI provider 细节，也不保存成本明细。
- AI capability layer 可以被 Job 调用，但不直接控制 Job 状态机。
- Billing 绑定 `job_id` 做查询和归因，但不属于 Job 状态机。
- `ai_call_logs` 是 AI provider call 明细账本，不是 `jobs` 表的扩展字段集合。

本文说明 `Job`、AI 能力层和 AI cost / billing 之间的分层关系，避免实现时把执行状态、模型调用和计费账本写成互相强绑定的单体。

## 文档职责

本文负责回答：

- `Job` 在本项目中是什么。
- AI 能力层在 Job 平台中的位置是什么。
- AI cost / billing 与 Job、AI provider call 的关系是什么。
- `model_id`、runtime snapshot、`ai_call_logs` 和 `BillingEnvelope` 分别保存什么事实。
- 哪些依赖方向是允许的，哪些依赖方向应避免。

本文不负责：

- Taskiq Job 状态机、Attempt lease、Callback outbox 的字段级设计。
- 具体 `job_type` 的参数、Prompt、模型输出 schema。
- 价格配置、成本计算公式和 `BillingEnvelope` 字段合同。
- 用户余额、扣款、发票、退款、税务或最终财务对账。

相关细节文档：

- [架构总览](架构总览.md)
- [Taskiq Job 数据模型设计](../设计文档/taskiq-job-model-design.md)
- [Headless AI Job Platform Service 设计文档](../设计文档/headless-ai-job-platform-service-design.md)
- [新增 job_type 标准接入规范](../接口层/job-type-extension-standard.md)

## 1. 总体心智模型

本项目应被理解为 **Headless AI Job Platform Service**：

```text
调用方 / 业务后端
  ├─ 管理用户、项目、业务状态和业务重试
  └─ 创建 Job，查询 Job，接收 Callback，查询 BillingEnvelope

Headless AI Job Platform Service
  ├─ Job runtime
  │   ├─ 创建 Job / Attempt
  │   ├─ 管理状态、租约、恢复、结果投影和 Callback
  │   └─ 调用具体 job_type 的执行器
  ├─ AI capability layer
  │   ├─ 解析 model_id
  │   ├─ 调用内部 AI gateway / provider adapter
  │   └─ 返回模型输出和 provider usage
  └─ AI cost / billing layer
      ├─ 写入 ai_call_logs
      ├─ 按 pricing snapshot 估算成本
      └─ 按 job_id 聚合 BillingEnvelope
```

核心判断：

- `Job` 是平台执行外壳，不是 AI provider call。
- AI 是底层能力，可以被某些 `job_type` 使用，但不是所有 Job 的必需组成。
- Billing 是对可计量调用的账本和读模型，不是 Job 状态机的一部分。
- `BillingEnvelope` 绑定 `job_id` 是查询维度，不表示计费明细属于 `jobs` 表。

## 2. 三个对象分别回答什么

| 对象 | 回答的问题 | 事实源 | 对外形态 |
|---|---|---|---|
| `Job` | 这次异步执行现在是什么状态，公开结果或公开错误是什么 | `jobs`、`job_attempts`、`callback_outbox`、`job_events` | `JobEnvelope` |
| AI capability | 这次能力是否需要模型，应该调用哪个 provider model，模型返回了什么 | `model catalog`、runtime snapshot、provider response | 具体 `job_type` 的执行结果 |
| AI cost / billing | 哪些模型调用发生了，usage 是多少，按哪个价格版本估算了多少成本 | `ai_call_logs`、pricing snapshot | `BillingEnvelope`、内部明细查询或导出 |

这三类事实不能混成一张表或一个公共外壳。

```text
JobEnvelope 不保存 usage/cost 明细。
ai_call_logs 不保存 Job 当前状态。
BillingEnvelope 不决定 Job succeeded / failed。
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

reconciler_leases
  保存恢复任务租约。
```

Job 表不应保存：

- provider 原始响应。
- 完整 Prompt 或完整模型输出。
- token usage 明细。
- 成本计算明细。
- provider 私有参数。

### 3.2 AI 相关数据

AI 能力层保存或读取模型调用所需事实：

```text
model catalog
  模型目录事实源。首版可以是 app/core/models.yaml。

runtime snapshot
  Job 创建时冻结本次执行需要的 model_id、Prompt、输出目标等运行时字段。

AI gateway / provider adapter
  把服务内 model_id 映射到 provider / LiteLLM model，并执行真实调用。
```

`model_id` 的流向应是：

```text
model catalog
  ↓ 创建期校验
runtime snapshot.runtime_fields.model_id
  ↓ Worker 执行期读取和再次 gate
AI gateway / provider adapter
  ↓ 调用时审计复制
ai_call_logs.model_id / provider / provider_model / pricing_ref
```

因此，`jobs` 表不需要顶层 `model_id`。如果某个 Job 不调用模型，`runtime_fields.model_id` 可以不存在；如果某个 Job 调用多个模型，每次调用也应分别进入 `ai_call_logs`，而不是把多个模型硬塞进 Job 顶层字段。

### 3.3 AI cost / billing 相关数据

AI cost / billing 保存可计量调用事实和聚合投影：

```text
ai_call_logs
  每次真实 provider / LiteLLM 调用一行，保存调用状态、usage、pricing snapshot 和成本估算。

BillingEnvelope
  按 job_id 聚合 ai_call_logs 得到的 Job 级成本估算返回对象。

pricing config
  把 provider usage 转换成内部 cost estimate 的配置事实源。
```

首版需要新增 `ai_call_logs`。首版不需要新增 `job_billing_summaries` 事实表；如果后续读压或导出要求需要投影表，它也只能是 `ai_call_logs` 的派生读模型。

## 4. 允许的依赖方向

推荐依赖方向：

```text
Job runner
  -> JobExecutor
  -> AI capability layer
  -> AI gateway / provider adapter

AI capability layer
  -> usage ledger
  -> pricing / cost estimator

Billing service
  -> ai_call_logs
  -> BillingEnvelope
```

允许：

- Job runner 把 `job_id`、`attempt_id`、`caller_id`、`job_type`、`model_id` 等上下文传给 AI capability layer。
- AI capability layer 返回模型输出、usage 和稳定错误。
- usage ledger 根据 Job 上下文写入 `ai_call_logs`。
- Billing service 按 `job_id` 聚合 `ai_call_logs`。

不允许：

- AI gateway 直接修改 `jobs.status`。
- AI gateway 直接创建或投递 Callback。
- Billing service 直接改变 Job succeeded / failed。
- Job 状态机依赖 `cost_amount` 是否存在来判断执行成功。
- `jobs` 表直接保存 provider usage / cost 明细。
- 缺 usage、缺价格或成本计算失败时静默写 `0` 后继续成功。

一句话：Job 可以调用 AI，AI 可以产生 usage，Billing 可以聚合 usage；反过来 AI 和 Billing 都不应控制 Job 状态机。

## 5. 对外 API 心智模型

调用方以 Job 为中心工作：

```text
POST /jobs
  创建一次异步执行，返回 JobEnvelope。

GET /jobs/{job_id}
  查询执行状态、公开结果或公开错误。

GET /jobs/{job_id}/billing
  Job 终态后查询该 Job 的成本估算摘要。

Callback
  接收终态通知；首版不默认携带 BillingEnvelope。
```

这意味着：

- `POST /jobs` 成功只表示服务接单，不表示模型调用已经发生。
- `GET /jobs/{job_id}` 只回答执行状态和公开结果，不回答完整成本明细。
- `GET /jobs/{job_id}/billing` 只回答成本估算，不改变 Job 状态。
- Callback 投递失败只影响 Callback 状态，不影响 Job 终态，也不新增模型费用。

## 6. 常见误区

### 6.1 “AI Job Service 是否就是 AI Gateway？”

不是。更准确的说法是：

```text
本项目对外是 Headless AI Job Platform Service。
AI gateway 是内部能力层，用于连接 model catalog、provider adapter、usage ledger 和 cost estimate。
```

首版不应对外提供绕过 Job runtime 的同步 AI 网关接口，例如直接 `POST /ai/chat`。这类接口会绕过 Attempt、恢复、Callback 和 BillingEnvelope 主流程，必须另行设计。

### 6.2 “model_id 是否应该放在 jobs 表顶层？”

不建议。

原因：

- 不是所有 Job 都调用模型。
- 一个 Job 未来可能调用多个模型。
- 模型选择属于具体 `job_type` 的运行时事实，应冻结在 runtime snapshot。
- 每次真实调用的模型审计事实应进入 `ai_call_logs`。

### 6.3 “BillingEnvelope 是否应该放进 JobEnvelope？”

首版不建议。

原因：

- 失败 Job 也可能已经产生模型费用，但失败时 `job_result=null`。
- 多 attempt、多步骤、多模型调用都需要明细账本聚合。
- Billing 的修复、导出、保留期和 Job 状态机不同。
- Callback 与轮询必须同源，贸然内联会扩大公共合同变化面。

### 6.4 “计费是否只服务 AI？”

MVP 中是，因为当前可计量对象是 AI provider call。

但抽象上，计费服务依赖的是“可计量调用事实”，不是 AI 这个业务名词。未来如果出现非 AI 的可计量能力，应单独设计通用 metered usage，而不是提前把首版 `ai_call_logs` 泛化成过宽的表。

## 7. 设计检查清单

新增或修改相关能力时，应检查：

```text
Job:
  [ ] 是否只修改执行状态、结果投影、Callback 或 runtime snapshot。
  [ ] 是否避免把 provider usage / cost 明细塞进 jobs。

AI:
  [ ] model_id 是否来自 model catalog。
  [ ] model_id 是否在创建期冻结到 runtime snapshot。
  [ ] Worker 调用前是否再次 gate。
  [ ] AI gateway 是否没有直接推进 Job 状态机。

Usage ledger:
  [ ] 真实 provider 调用前是否先写 pending row。
  [ ] 每次真实 provider 调用是否都有独立 ai_call_logs。
  [ ] 缺 usage / 缺价格 / 成本计算失败是否 fail-fast。

Billing:
  [ ] BillingEnvelope 是否只从 ai_call_logs 聚合。
  [ ] failed-but-billed 是否能表达。
  [ ] Callback 重试是否不改变 BillingEnvelope。
  [ ] 是否没有把 BillingEnvelope 当成扣款、发票或最终对账事实。
```

如果某个实现需要打破这些规则，应先更新本文和对应设计文档，再进入开发。
