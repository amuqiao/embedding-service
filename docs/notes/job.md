我确认过 `git status --short`，当前工作区是干净的。

**结论**
目前项目的 **Job 架构、注册机制、工具合同已经基本稳定**，可以作为项目地基继续接正式业务。
但这里的“稳定”指的是：**边界清楚、扩展路径清楚、失败模型清楚、不会靠文档约束后续开发**。
它还不等于“所有生产硬化项都已完成”。

**整体模型**
```text
外部调用方
  |
  v
POST /jobs
  |
  v
Job Type Registry
  校验 job_type / schema / visibility / role / allowed_capability_refs
  |
  v
Job Kernel
  job_aggregates
  job_submission_keys
  job_execution_attempts
  dispatch_outbox
  callback_outbox
  recovery
  |
  v
Worker / Executor
  |
  v
Capability Registry
  |
  v
Tool Registry
  |
  v
Integration / Adapter
  OSS / AI Provider / Triton / 其他外部系统
```

**Job 架构稳定性**
```text
Job
  = 对外资源
  = 查询、callback、billing、result 的入口

Attempt
  = 单次执行权
  = worker 必须 claim lease 后才能推进

Dispatch Outbox
  = DB -> Taskiq 的可靠发布意图
  = publish 失败可重试 / dead-letter / recovery 收敛

Callback Outbox
  = Job terminal -> 调用方 callback 的投递账本
  = callback 失败不改变 Job 终态

Recovery
  = 补偿扫描
  = 修复 due dispatch / stale attempt / missing callback / workflow reconcile
```

这套结构是成熟的后端异步可靠性组合：`idempotency key + transactional outbox + lease/heartbeat + recovery/reconciler + dead-letter`。方向是对的，骨架也是稳定的。

仍在 hardening 里的点主要是生产强度问题，不是架构方向问题：

```text
P1: MAX_ACTIVE_JOBS 计数与创建还未完全原子化
P1: 长任务执行期间还需要更强的周期 heartbeat
P1: 多 caller 场景下 caller_id 应由凭证派生
P1: callback 幂等 ACK、公开错误脱敏需要继续锁测试和合同
```

**注册机制稳定性**
```text
Job Type
  -> declares allowed_capability_refs

Capability
  -> declares allowed_tool_refs
  -> owns capability schema / result schema / errors / log events

Tool
  -> declares entrypoint / request schema / required settings / errors

Registry Check
  -> startup / verify 阶段 fail-fast
```

这已经不是“靠文档提醒大家要注册”，而是代码层面约束：

```text
新增 job_type 没注册       -> fail-fast
引用不存在 capability     -> fail-fast
capability 引用不存在 tool -> fail-fast
schema / error / log 缺失  -> fail-fast
internal error 暴露到 public contract -> fail-fast
依赖方向反了              -> fail-fast
```

所以注册治理体系是稳定的，而且方向正确：**统一治理，轻量实现，不让每层各搞一套注册机制。**

**工具合同稳定性**
当前工具合同边界也比较清楚：

```text
Job 层
  拥有状态、attempt、retry、dispatch、callback、billing 入口

Capability 层
  拥有业务能力抽象
  消费 frozen plan
  不拥有 Job 状态

Tool 层
  拥有具体副作用边界
  例如 object_storage_read:1
  不写 Job 状态
  不决定 retry
  不投影 public result

Integration 层
  封装底层 SDK / 协议
  例如 OSS、Triton、AI adapter
```

关键稳定点是：

```text
Job 不是 tool
Capability 不是 child job
Tool 不是 workflow node
Integration 不反向依赖 Job
```

这避免了你之前担心的“中间态数据越存越多”“项目变成万能后端”“工具层偷偷拥有业务状态”。

**我对当前成熟度的判断**
```text
架构地基          稳定
Job lifecycle     稳定
root/child 模型    稳定
注册治理          稳定
工具合同          稳定
扩展入口          稳定
生产硬化          仍有 P0/P1/P2 队列
通用平台化        明确不做
```

所以我的判断是：**可以进入正式业务接入阶段**。后续新增能力应走这条路径：

```text
新增 job_type
  -> 注册 JobExecutor
  -> 声明 schema / visibility / role
  -> 声明 allowed_capability_refs
  -> 如需能力，走 capability
  -> 如需外部副作用，走 tool
  -> 如需底层协议，走 integration
  -> registry_check / verify fail-fast
```

不应该再绕回去重构大骨架。接下来更应该做的是：在这个稳定骨架上接正式业务，同时按 `job-kernel-hardening` 逐项补生产硬化。