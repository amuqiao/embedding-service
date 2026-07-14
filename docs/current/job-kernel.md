# Job Kernel 当前模型

本文用心智模型解释当前 Job 可靠执行内核：Job 如何创建、执行、重试、恢复、callback 和软删除。它是当前实现事实文档，不是对外 HTTP 合同；对外字段以 [`../api/service-contract.md`](../api/service-contract.md) 和 `app/schemas/` 为准。

读这篇文档时，先把 Job kernel 看成几组互相配合的运行事实，而不是一张表或一条消息队列。

```text
Job 聚合事实
+ Attempt 执行权
+ Dispatch Outbox 发布意图
+ Callback Outbox 投递账本
+ Recovery 补偿扫描
+ Root / Internal Child lineage
```

这几个事实源共同解决的是异步 Job 在多 API、多 worker、多 publisher 环境里的可靠推进问题：重复提交不能创建重复资源，重复消息不能被多个 worker 同时推进，worker 崩溃后要能恢复，发布和 callback 失败要能重试，workflow root 和 child 要能保持同一组 lineage。

## 先建立整体模型

### Job 是资源，Attempt 是执行权

调用方看到的是 Job。worker 真正消费和领取的是 attempt。

```text
Job
  对外资源
  查询、callback、billing、最终结果都以 public root Job 为入口

Attempt
  单次执行权
  worker 收到 attempt_id 后必须 claim lease
  只有持有当前 active attempt lease 的 worker 才能写进度和终态
```

因此 `job_execution_attempts` 不是普通审计历史。它保存 worker 执行互斥、heartbeat、lease、timeout、retry policy snapshot、retry decision 和 retry chain。没有这张表，多 worker 重复消费、超时恢复和按 attempt 重试都缺少事实源。

### Outbox 是副作用意图

Job kernel 有两类 outbox，它们都遵守“先落库、再投递”的原则，但目标和语义不同。

```text
dispatch_outbox
  目标: Taskiq broker
  payload: run_attempt(attempt_id)
  失败后: 重发同一个 attempt_id

callback_outbox
  目标: 调用方 callback URL
  payload: root Job 终态 callback envelope
  失败后: 重投 callback，不改变 Job 终态
```

两者不能合并成一个含混事件表，因为副作用目标、重试策略、payload 合同、恢复责任都不同。

### Retry 不是一套机制

当前有三套重试机制，名字都叫 retry，但对象和触发条件不同。

```text
execution attempt retry
  对象: job_execution_attempts
  目标: 为同一 Job 创建下一次 execution attempt
  判断: error.code 命中 retryable_error_codes，且 attempt 次数未耗尽

dispatch publish retry
  对象: dispatch_outbox
  目标: 把同一个 attempt_id 发布到 Taskiq
  判断: publish 失败、dispatch orphan 或 lease 过期

callback delivery retry
  对象: callback_outbox
  目标: 重投 root Job 终态 callback
  判断: HTTP delivery failed 或 callback lease 过期，且 delivery 次数未耗尽
```

这三套机制互不消耗对方的 retry budget。dispatch publish retry 不会增加 execution attempt 次数；callback delivery retry 不会让 Job 回退，也不会把业务 Job 改成 failed。

### Recovery 是补偿，不是重新猜结果

Recovery 只根据数据库里已经落下的事实补偿缺口。

```text
可以做:
  重发 due / orphan dispatch
  接管 stale running attempt
  补建 missing callback outbox
  重投 due callback
  推进 workflow root reconciliation
  收敛 stale AI ledger pending

不会做:
  根据 provider 外部状态猜测 Job 成功
  重放已经成功的业务结果
  绕过 attempt lease 直接改业务终态
```

如果 provider 实际已经完成，但本服务没有可信 terminal 事实，recovery 不会凭空补出成功结果。

## Job 生命周期

普通 non-workflow Job 当前由 public root Job 自己执行；workflow root Job 的 root attempt 只负责编排，真实业务执行落到 internal child Job 的 attempt。

```text
提交阶段
  POST /jobs
    -> client_request_id 幂等检查
    -> MAX_ACTIVE_JOBS 容量门禁
    -> 创建 public root Job
    -> 创建 active attempt
    -> 创建 dispatch_outbox

发布阶段
  dispatch_outbox
    -> publisher 发布 jobs.run_attempt(attempt_id)
    -> publish 失败或长期未被 claim 时，recovery 重发同一 attempt_id

执行阶段
  worker 收到 attempt_id
    -> claim 当前 active attempt lease
    -> non-workflow: 执行 root Job executor / model call
    -> workflow root: 编排 ready child Jobs
    -> workflow child: 执行 leaf / root_or_leaf executor

终态阶段
  Job succeeded / failed
    -> 写 result 或 error
    -> 清空 active_attempt_id
    -> 如配置 callback，写 callback_outbox
    -> callback 投递失败只影响 callback 状态，不改变 Job 终态
```

### 容量门禁

`MAX_ACTIVE_JOBS` 是提交阶段的接单上限。检查对象是当前 active Job 数：`queued` Job 加上仍持有 active attempt 的 `running` Job。这个计数不按 caller、`job_type` 或 root/child 分组；active internal child 也会计入。

容量检查与 public root Job 的 `job_aggregates`、`job_execution_attempts`、`dispatch_outbox` 创建处于同一个事务级 advisory lock 窗口内；事务提交前不会释放容量闸门。重复提交命中已有 `client_request_id` 且使用 `return_existing` 时直接返回已有 Job，不消耗新的容量名额。

```text
active_jobs < MAX_ACTIVE_JOBS
  -> 允许创建 Job

active_jobs >= MAX_ACTIVE_JOBS
  -> 拒绝本次创建
  -> 返回 QUEUE_FULL / HTTP 503
  -> 不创建 job_aggregates、job_execution_attempts 或 dispatch_outbox
```

workflow root 已完成 orchestration、正在等待 child 且 `active_attempt_id=null` 时，不计入这个门禁。`MAX_ACTIVE_JOBS=0` 表示禁用检查；它只控制接单上限，不杀掉已存在的 Job，也不改变 worker 并发。

workflow child 创建也会检查同一个全局容量门禁。root orchestration 首轮 fan-out 时，容量计数会排除当前 root orchestration 自身，避免 root active attempt 平白占掉一个 child 槽位。容量不足时，本轮 orchestration / reconciliation 不创建新的 child；root Job 保持 running + `active_attempt_id=null`，等待后续 recovery / reconciliation 在容量释放后继续补齐 ready child，并写入内部 `workflow.capacity_deferred` audit event 作为排障事实。

### 普通 Job 与 Workflow Job

公开调用方提交得到的是 public root Job。调用方查询、callback、billing 和最终结果都以 root Job 为入口。

| 形态 | 当前行为 |
|---|---|
| 普通 non-workflow Job | public root Job 直接持有 active attempt，由 worker 执行业务 executor |
| workflow Job | public root Job 持有 frozen `workflow_plan`；root attempt 做 orchestration，业务执行由 internal child Jobs 完成 |

注意：普通 non-workflow Job 当前还不是 `root + one child`；它由 public root Job 自己执行。

## 幂等模型

Job kernel 的幂等不是一处逻辑，而是按边界分层：提交幂等保护 public root Job，workflow child 幂等保护 root 内部节点，outbox 幂等保护副作用意图，attempt lease 保护 worker 执行权。

```text
提交幂等
  caller_id + client_request_id
    -> job_submission_keys
    -> request_fingerprint 校验同 key 是否同内容

Workflow child 幂等
  root_job_id + workflow_node_key
    -> 同一 root node 只创建一个 child Job

Dispatch 幂等
  attempt_id + task_name
    -> 同一 attempt 只保留一个 dispatch 意图

Callback 幂等
  job_id + event_type
    -> 同一 root Job 终态事件只保留一个 callback 意图

Worker 执行幂等
  active_attempt_id + lease_token
    -> 同一 attempt 只有一个有效 worker 可以推进
```

### 提交幂等

提交幂等由调用方提供的 `client_request_id` 表达“这是不是同一次业务提交”。服务端不会只用 `job_params` 自动判断幂等，因为相同参数既可能是网络重试，也可能是用户明确想再生成一次。

```text
第一次提交
  caller_id = cpp
  client_request_id = req-001
  job_params = A

服务端
  -> 对 caller_id + client_request_id 加事务级 advisory lock
  -> 查 job_submission_keys
  -> 没有 active key
  -> 创建 public root Job J1
  -> 写入 job_submission_keys:
       caller_id = cpp
       key_kind = client_request_id
       key_value = req-001
       job_id = J1
       request_fingerprint = hash(caller_id + client_request_id + job_type + job_params + callback + options)
```

重复提交时，服务端先查 active `job_submission_keys`，再比较本次请求重新计算出的 `request_fingerprint`。

```text
同 caller_id + 同 client_request_id + 同 request_fingerprint
  idempotency_mode = return_existing
    -> 返回已有 Job 当前状态

同 caller_id + 同 client_request_id + 同 request_fingerprint
  idempotency_mode = reject_duplicate 或未传 options
    -> CLIENT_REQUEST_ID_CONFLICT

同 caller_id + 同 client_request_id + 不同 request_fingerprint
  -> CLIENT_REQUEST_ID_CONFLICT
```

`client_request_id` 决定“是不是同一次提交”；`request_fingerprint` 只负责防止同一个 key 被拿去提交另一套内容。`idempotency_mode` 只决定重复提交时返回已有 Job 还是拒绝重复，不会改变已有 Job 的状态。

```text
J1 = failed

再次 POST 同 client_request_id=req-001 + return_existing
  -> 返回 J1 failed
  -> 不会把 J1 重新变成 queued/running

再次 POST 新 client_request_id=req-002
  -> 创建新 Job J2
  -> J2 独立执行
```

因此提交幂等不是失败重跑机制。它只处理网络重试、客户端超时后重试、按钮重复点击这类“同一次提交”的收敛。

### Workflow Child 幂等

workflow root orchestration、recovery 或并发进程可能重复尝试创建同一个 child node。child 幂等由 `root_job_id + workflow_node_key` 表达。

```text
root Job R
  node_key = style_probe:abc
    -> child Job C1

重复 orchestration / recovery
  node_key = style_probe:abc
    -> 命中已有 child
    -> 不创建 C2
```

数据库层面的 `unique(root_job_id, workflow_node_key) where workflow_node_key is not null` 是幂等约束，不只是查询索引。它保证同一个 root 下同一个 workflow node 只有一个 child Job。

### Dispatch / Callback 幂等

outbox 幂等保护的是副作用意图，不是业务执行结果。

```text
dispatch_outbox
  event_id = job_attempt:{attempt_id}:dispatch
  unique(attempt_id, task_name)
  -> 同一 attempt 只保留一个 Taskiq publish 意图
  -> publish retry 重发同一个 attempt_id

callback_outbox
  unique(job_id, event_type)
  unique(event_id)
  -> 同一 root Job 的同一 terminal event 只保留一个 callback 投递意图
  -> delivery retry 重投同一个 callback payload snapshot
```

这些 outbox retry 不重新定义业务状态。dispatch retry 只负责让 worker 最终收到同一个 `attempt_id`；callback retry 只负责投递 root Job 终态通知，不会让 Job 回退或重新执行。

### Worker 执行幂等

Taskiq 可能重复投递同一个 `attempt_id`，多个 worker 也可能同时看到同一条消息。执行幂等由 attempt lease 和 active attempt 指针保证。

```text
worker 收到 attempt_id
  -> claim pending attempt
  -> 写入 lease_token
  -> Job.active_attempt_id 必须仍指向该 attempt

重复消息 / 其它 worker
  -> claim 不到当前 lease
  -> 不能推进进度或终态

终态写入
  -> 必须匹配 active_attempt_id + lease_token
  -> 成功后清空 active_attempt_id
```

这层幂等保证“同一 attempt 只有一个有效执行者”。如果有效 worker 崩溃，recovery 要等 lease / stale running 窗口过期后才能接管并按 attempt policy 收敛。

## Root / Child 运行时身份

`role` 描述 `job_type` 的设计用途；某条 Job 实例运行时到底是 root 还是 child，仍看 `job_aggregates` 的 lineage 字段。

```text
job_type role = 目录意图
  root          对外入口 / 聚合根
  leaf          workflow 内部执行节点
  root_or_leaf  可直提，也可被 workflow 复用

Job instance = 运行时身份
  root_job_id=null + workflow_node_key=null      public root Job
  root_job_id=R    + workflow_node_key=node_key  internal child Job

job_execution_attempts = 某条 Job 的一次执行尝试
  job_id 可能指向 public root Job
  job_id 也可能指向 internal child Job
```

因此直接提交 `root_or_leaf` job_type 时，它是 public root Job；被 workflow 创建时，它是 internal child Job。`root_or_leaf` 不表示“只有一个 Job”，也不表示自动创建 root + leaf。

当前内置 `job_type` 标记如下；完整准入、schema、capability 和错误码事实以 registry 运行时输出为准。

| job_type | visibility | role |
|---|---|---|
| `poster_title_image` | `public` | `root` |
| `audio_stem_separation` | `demo` | `root` |
| `audio_stem_separation_triton` | `demo` | `root` |
| `arithmetic` | `demo` | `root` |
| `example_workflow` | `demo` | `root` |
| `example_sleep` | `demo` | `root_or_leaf` |
| `example_pair` | `demo` | `root_or_leaf` |
| `example_collect` | `demo` | `leaf` |
| `job_real_llm_echo` | `demo` | `root_or_leaf` |
| `job_real_llm_double_echo` | `demo` | `root_or_leaf` |
| `poster_title_image_style_probe` | `internal` | `leaf` |
| `poster_title_image_generate_item` | `internal` | `leaf` |
| `poster_title_image_join` | `internal` | `leaf` |

`visibility` 决定外部提交准入：`APP_ENV=local/dev` 允许外部提交 `public` 和 `demo`；`APP_ENV=test/prd` 只允许外部提交 `public`；`internal` 只供服务内部 workflow child 使用，任何环境都不能被外部直接提交。

`example_*` 是模板内置示例 family，作为低副作用 Job 合同参考和默认压测目标。它们统一标记为 `visibility="demo"`，`allow_callback=False`，不调用 LLM、不访问对象存储、不发起外部 HTTP，也不写真实业务副作用。正式业务可以参考它们的 schema、executor、registry 和 workflow definition 组织方式，但不继承它们的 `job_type`、结果 schema 或压测参数。

`audio_stem_separation` 和 `audio_stem_separation_triton` 当前都标记为 `visibility="demo"`，用于本地和开发环境验证音乐源分离真实模型链路；它们不是模板 smoke 示例。前者加载本地 ONNX 权重，后者调用 Triton HTTP endpoint，二者都会读取 OSS WAV 输入并写出四条音频 stem，因此使用前必须配置输入来源白名单和对应模型运行环境。

### Workflow Lineage

当前 workflow 不新增 `workflow_instances`、`workflow_nodes`、`workflow_node_dependencies` 或 `workflow_wakeup_outbox`。它用 `job_aggregates` 自索引表达 root/child lineage。

```text
job_aggregates
  id
  root_job_id
  workflow_node_key
  status
  result
  error
```

字段含义：

| 字段 | 当前含义 |
|---|---|
| `root_job_id` | `NULL` 表示 public root；非 `NULL` 表示 child 归属的 public root Job |
| `workflow_node_key` | child 在 root 内的 leaf node 幂等身份；public root 必须为 `NULL` |

`root_job_id` 不表达 DAG 执行依赖。当前 workflow child 都直接挂在 root 下；`chain`、`chord` 等顺序关系只存在 frozen `workflow_plan.nodes[].depends_on` 中。workflow 任务模型和依赖语义以 [`workflow-kernel.md`](workflow-kernel.md) 为准。

关键约束：

```text
index(root_job_id)
unique(root_job_id, workflow_node_key) where workflow_node_key is not null
index(root_job_id, status)
```

其中 `unique(root_job_id, workflow_node_key)` 是 child node 幂等约束，不只是查询优化。它保证重复 root orchestration、重复 reconciler 或并发进程不会为同一个 root node 创建多个 child Job。

常见排查查询不在本文维护，统一使用 [`../runbooks/job/jobs使用与排障手册.md`](../runbooks/job/jobs使用与排障手册.md) 和 `./scripts/jobs.sh`。

## 重试模型

先分清重试对象，再看配置入口。当前重试不是“全局失败就重跑 N 次”。

### 三套重试机制

| 机制 | 对象 | 触发条件 | 重试动作 | 是否看 Job error code |
|---|---|---|---|---|
| execution attempt retry | `job_execution_attempts` | worker 执行失败或 stale running 被 recovery 接管 | 为同一 Job 创建下一次 attempt + 新 dispatch_outbox | 是 |
| dispatch publish retry | `dispatch_outbox` | Taskiq publish 失败、dispatch orphan、dispatch lease 过期 | 重发同一个 `attempt_id` | 否 |
| callback delivery retry | `callback_outbox` | callback HTTP delivery failed 或 callback lease 过期 | 重投同一个 callback envelope | 否 |

### Execution Attempt Retry

execution retry 以 `job_execution_attempts` 为单位，不是简单重跑同一条 Taskiq 消息。失败 attempt 属于哪条 Job，就只判断并重试那条 Job。

```text
attempt running for Job X
  -> 执行失败或 lease 超时
  -> mark current attempt failed
  -> 如果 retryable 且 attempt policy snapshot 未耗尽
       为 Job X 创建 next attempt
       Job X 回到 queued
       写新的 dispatch_outbox
     否则
       Job X failed
       如配置 callback，写 callback_outbox
```

execution retry 生效的前置条件：

```text
1. 当前 attempt 已进入 running
2. worker 或 recovery 把失败收敛为 error.code
3. error.code 命中 attempt.policy_retryable_error_codes
4. attempt.purpose_attempt_no < attempt.policy_max_attempts
5. Job 本身还没有进入 failed 终态
```

缺任意一个条件，都不会创建下一次 execution attempt。

当前 attempt policy 在创建 attempt 时固化为 snapshot：

| 字段 | 当前含义 |
|---|---|
| `purpose` | 区分 `workflow_orchestration` 与 `business_execution` |
| `purpose_attempt_no` | 当前 purpose chain 内第几次 attempt |
| `policy_max_attempts` | 该 purpose chain 最多允许几次 attempt |
| `policy_retryable_error_codes` | 哪些错误码可自动进入下一次 attempt |
| `policy_retry_delay_seconds` / `policy_backoff_kind` | 下一次 attempt 的调度延迟 |
| `retry_chain_id` / `previous_attempt_id` | 同一 purpose retry chain 的链路 |
| `retry_policy_snapshot` | attempt 创建时的 policy 快照；后续改代码不回写已有 attempt |

### 两类 Execution Purpose

execution retry 再按 `purpose` 分两类。

```text
普通 non-workflow Job
  public root Job
    -> business_execution attempt

workflow Job
  public root Job
    -> workflow_orchestration attempt
       只负责编排 child
  internal child Job
    -> business_execution attempt
       执行 leaf / root_or_leaf 业务逻辑
```

当前默认策略：

| purpose | 当前默认 policy | 当前实际含义 |
|---|---|---|
| `workflow_orchestration` | `max_attempts=3`，`retry_delay_seconds=5`，`backoff_kind=fixed`，`retryable_error_codes={JOB_STATE_TRANSITION_CONFLICT, TASKIQ_PUBLISH_FAILED}` | 只在编排阶段遇到这些错误码时重试 root orchestration attempt |
| `business_execution` | `max_attempts=1`，`retry_delay_seconds=None`，`backoff_kind=none`，`retryable_error_codes={}` | 默认只有 1 次业务执行 attempt，不自动业务重跑 |

`business_execution.max_attempts` 是 attempt 总数，不是额外 retry 次数。只有 job_type 明确覆盖 `retry_policy` 并把某个错误码加入 `retryable_error_codes` 后，execution retry 才会被使用。

当前 job_type 专属 business execution retry：

| job_type | business_execution policy | 当前实际含义 |
|---|---|---|
| `poster_title_image_style_probe` | `max_attempts=3`，`retry_delay_seconds=15`，`backoff_kind=fixed`，`retryable_error_codes={AI_PROVIDER_FAILED, MODEL_CALL_TIMEOUT, OSS_FETCH_FAILED, OSS_WRITE_FAILED, JOB_TIMEOUT}` | 对风格探测中的 provider、模型超时、Job 超时和引用图读写类瞬时失败允许最多 2 次重试 |
| `poster_title_image_generate_item` | `max_attempts=3`，`retry_delay_seconds=15`，`backoff_kind=fixed`，`retryable_error_codes={AI_PROVIDER_FAILED, MODEL_CALL_TIMEOUT, OSS_FETCH_FAILED, OSS_WRITE_FAILED, JOB_TIMEOUT}` | 对单个标题图生成中的 provider、模型超时、Job 超时、引用图读取和结果写入类瞬时失败允许最多 2 次重试 |

按运行形态展开：

| 失败位置 | 失败对象 | 是否进入 execution retry 判断 | 可重试时重试谁 | 是否自动重跑整个 workflow |
|---|---|---|---|---|
| 普通 non-workflow Job 执行失败 | public root Job 的 business attempt | 是 | 这条 public root Job | 不适用 |
| workflow root 编排失败 | public root Job 的 orchestration attempt | 是 | root orchestration attempt | 否 |
| workflow child 执行失败 | internal child Job 的 business attempt | 是 | 失败的 child Job | 否 |
| child 最终 failed 后 root 被标记 failed | workflow root terminal projection | 否 | 不自动重试 | 否 |

workflow root 的 `failed` 需要区分两种来源：

```text
Root orchestration attempt failed
  root 的 active attempt 仍在编排阶段失败
  -> 如果该 root orchestration attempt 的 policy snapshot 未耗尽，只重试 root 编排 attempt
  -> 编排重试依赖 child lineage / workflow_node_key 幂等补齐缺口
  -> 不表示重建所有 child，也不表示重跑已成功 child

Workflow terminal failed
  root orchestration attempt 已成功，root 正在等待 child
  某个 required child 最终 failed
  -> workflow reconciler 把 root 投影为 failed
  -> 这是聚合终态收敛，不触发 root execution attempt retry
  -> 不自动重跑 root、全部 child 或已成功 child
```

### Dispatch Publish Retry

dispatch publish retry 的对象是 `dispatch_outbox`，目标是把同一个 `attempt_id` 发布到 Taskiq。

```text
create attempt
  -> create dispatch_outbox(attempt_id)
  -> publish jobs.run_attempt(attempt_id)

publish failed
  -> publish_attempts + 1
  -> 如果 publish_attempts < max_publish_attempts
       status = retrying
       next_attempt_at = now + publish_retry_delay_seconds
     否则
       status = dead_letter
       recovery 将对应 pending attempt 和 Job 收敛为 failed
       error.code = DISPATCH_PUBLISH_EXHAUSTED
       如配置 callback，写 callback_outbox

publish succeeded
  -> publish_attempts + 1
  -> status = published
  -> next_attempt_at 保留为 orphan 检查窗口

published but worker long time not claim
  -> recovery 视为 orphan dispatch
  -> 重新发布同一个 attempt_id
```

当前默认值在 `Settings.job` 与 outbox snapshot 中：

| 参数 | 当前默认 | 当前位置和含义 |
|---|---:|---|
| `dispatch_max_publish_attempts` | `12` | `app/core/config.py` 的 `JobSettings`；创建 `dispatch_outbox` 时固化到 `max_publish_attempts` |
| `orphan_timeout_seconds` | `300` | 判断 publish 后长期无人 claim 的窗口，也用于 dispatch lease |
| `publish_retry_delay_seconds` | `5` | 创建 `dispatch_outbox` 时写入；当前是代码内部固定值 |
| `publish_backoff_kind` | `fixed` | 当前 dispatch publish retry 固定退避语义 |

`publish_attempts` 是总发布尝试次数，成功发布和失败发布都会增加。它不等同于“失败次数”。

这些 retry 不消耗 execution attempt retry budget。也就是说，同一个 attempt 可能经历多次 dispatch publish retry，但仍然只是一条 execution attempt。

dispatch `dead_letter` 是 publish retry budget 耗尽，不是 worker 执行业务失败。当前 recovery 会扫描 `queued` + active pending attempt + `dispatch_outbox.status=dead_letter` 的记录，并把 Job 终态收敛为 `failed`，避免永久 `queued` 占用 active capacity。人工 replay 属于确认式写操作，入口和使用边界由 `./scripts/job-ops.sh replay-dispatch -h` 维护。

### Callback Delivery Retry

callback delivery retry 的对象是 `callback_outbox`，目标是投递 root Job 终态 callback。它不看 Job 的 `error.code`，只看 HTTP delivery 结果和 delivery 次数。

```text
Job terminal
  -> 如果没有 callback_url 或没有 terminal event_type
       不创建 callback_outbox
  -> 如果 callback_url 存在但未订阅当前终态事件
       callback_outbox created as skipped
  -> 如果订阅当前终态事件
       callback_outbox created as pending
  -> delivery succeeds
       status = delivered
     delivery fails and attempts remain
       status = retrying
       next_attempt_at = now + retry_delay_seconds
     delivery attempts exhausted
       status = dead_letter

Job status 不回退
Job result/error 不因为 callback 失败而改变
```

调用方按 `event_id` 去重。同一个 `event_id` 已经处理过时，调用方仍应返回 `accepted=true`；服务端会把这类 duplicate ACK 视为 delivered。`accepted=false`、非 2xx、超时、非 JSON、空 body 或 ack schema 不合法都会进入 retry / dead-letter 路径。

当前默认值在 `Settings.callback` 与 outbox snapshot 中：

| 参数 | 当前默认 | 当前含义 |
|---|---:|---|
| `timeout_seconds` | `5` | callback 单次 HTTP 请求超时；flat env 键是 `CALLBACK_TIMEOUT_SECONDS` |
| `max_delivery_attempts` | `12` | 最多 delivery 次数；创建 `callback_outbox` 时固化到 `max_delivery_attempts` |
| `retry_delay_seconds` | `300` | delivery failed 后下一次投递延迟 |

`CALLBACK_MAX_DELIVERY_ATTEMPTS` 和 `CALLBACK_RETRY_DELAY_SECONDS` 不是当前 flat env 配置合同；放进 `.env` / `ENV_FILE` 会被视为不支持或废弃键，而不是静默生效。

### AI Provider Internal Retry

AI provider internal retry 不是 Job execution retry。

```text
provider internal retry
  发生在一次 AI 调用内部
  不创建新的 job_execution_attempts
  不创建新的 dispatch_outbox
  可能影响成本、账本和幂等边界

Job execution retry
  发生在 attempt 失败之后
  创建下一条 job_execution_attempts
  可能重跑整个 executor
```

当前通用文本模型经 LiteLLM 时会把模型目录中的 `generation.num_retries` 传给 provider adapter；当前模型配置为 `0`。OpenAI Responses adapter 创建 client 时 `max_retries=0`。因此不要把 `business_execution.max_attempts` 理解为 provider 单次请求的内部 retry。

## Recovery 模型

Recovery 是周期性补偿扫描。它不替代 worker，也不重放已成功的业务结果。

| 扫描对象 | 修复的问题 | 收敛动作 |
|---|---|---|
| due / orphan dispatch | Taskiq 发布失败、发布后长期无人 claim、dispatch lease 过期 | 重新发布同一个 `attempt_id` |
| stale running attempt | worker 崩溃、heartbeat 停止、attempt lease 过期 | 标记 attempt failed；可重试时创建下一 attempt，否则 Job failed |
| workflow root | child 终态后 root 没有继续推进、ready child 缺失、root terminal projection 漏执行 | 调用 workflow reconciler 创建 child 或终结 root |
| missing callback outbox | root Job 已终态但 callback 投递意图缺失 | 补建 callback outbox |
| due callback | callback 失败后到达下次重试时间，或 callback lease 过期 | 重新投递 callback |
| stale AI ledger pending | AI 调用账本长期 pending | 标记为 failed / unknown，避免 billing read model 长期不收敛 |

多进程下的恢复边界：

```text
多 API Pod
  job_submission_keys 唯一约束保证提交幂等
  dispatch_outbox event_id / (attempt_id, task_name) 唯一约束避免重复 dispatch 意图

多 worker Pod
  job_execution_attempts lease_token 与行锁保证同一 attempt 只有一个有效执行者
  worker 写终态时必须仍持有当前 lease
  recovery 只处理 lease 过期或 stale 的 attempt

多 publisher / callback worker
  outbox 行领取依赖 status、lease token 和 lease 过期时间
  失败后按 next_attempt_at 重试，超过上限进入失败或 dead letter 状态
```

## Timeout / Lease 边界

当前执行等待边界分成两条：AI 调用等待由 `MODEL_CALL_TIMEOUT_SECONDS` 控制；attempt 执行权由 lease / heartbeat 和派生的 stale running threshold 控制。

```text
AI 调用
  MODEL_CALL_TIMEOUT_SECONDS
    -> 截断单次 provider 调用等待

Attempt 执行权
  worker claim / heartbeat
    -> lease_expires_at
    -> recovery 扫描 stale running attempt
    -> mark attempt failed / retry / Job failed
```

`worker_soft_time_limit`、`worker_hard_time_limit` 和 `job_stale_running_seconds` 由 `MODEL_CALL_TIMEOUT_SECONDS` 派生，用于保持配置不变量和 stale running 接管窗口单调递增。当前运行路径用 `job_stale_running_seconds` 作为全局 attempt lease floor；claim 和 heartbeat 写入 `lease_expires_at` 时会取 `max(job_stale_running_seconds, job_execution_attempts.timeout_seconds)`。因此 job_type 声明的较长 `timeout_seconds` 可以拉长 attempt lease，但不能缩短全局保护窗口。runner 在业务 executor / model call 执行期间会用独立 DB session 周期续约 attempt lease；如果周期 heartbeat 失效，执行路径会暴露状态冲突，后续终态写入仍由 `lease_token` 拦截。`timeout_seconds` 当前不是 runner 层统一强制终止期限；soft / hard time limit 也不是独立 flat env。

| 边界 | 当前作用 |
|---|---|
| `MODEL_CALL_TIMEOUT_SECONDS` | 截断单次 AI provider 调用等待 |
| attempt lease / heartbeat | 让多 worker 和 recovery 判断谁仍有执行权 |
| `job_stale_running_seconds` | 全局 attempt lease floor；lease 过期后 recovery 才接管 stale running attempt |
| `job_execution_attempts.timeout_seconds` | attempt 创建时固化的声明超时；大于全局 floor 时拉长 claim / heartbeat 后的 lease 窗口 |
| `worker_soft_time_limit` / `worker_hard_time_limit` | 派生保护窗口和配置不变量；当前不作为独立 env 配置 |

## 删除模型

Job soft delete 是 root-family 级可见性机制，不是执行重试、状态回滚或物理删除。

```text
普通 Job
  root job = public Job 本身

Workflow Job
  root job = public root Job
  child jobs = root_job_id 指向该 root 的 internal Jobs

soft delete(root_job_id)
  -> root + child 的 job_aggregates.deleted_at / deleted_reason 同事务写入
  -> root 的 job_submission_keys.deleted_at / deleted_reason 同事务写入
  -> attempts / dispatch / callback / audit 不单独写 deleted_at
```

当前删除状态事实源分两层：

| 表 | 软删除职责 |
|---|---|
| `job_aggregates` | Job family 是否退出正常运行和查询视图的事实源；root 与 child 必须按同一个 root 操作同向变化 |
| `job_submission_keys` | 幂等键生命周期事实源；只对 `deleted_at IS NULL` 的 key 保持 `caller_id + key_kind + key_value` 唯一 |

`job_execution_attempts`、`dispatch_outbox`、`callback_outbox` 和 `job_audit_events` 不维护独立 deleted 状态。它们是否出现在普通查询中，由关联的 `job_aggregates.deleted_at` 决定。

自动过期清理只软删除已收敛的 public root family：

```text
root expires_at <= now()
root status in succeeded / failed
root active_attempt_id is null
callback 未配置，或终态 callback 已 delivered / skipped / dead_letter
child jobs 没有 active 或非终态记录
```

不满足这些条件时，cleanup 不会把 Job family 软删除。被软删除的 Job 不参与 API 普通查询、worker/recovery 推进、`jobs.sh list/show/summary/stuck/gate` 等正常排障视图。内部只读审计和确认式 delete/restore 操作由 `./scripts/jobs.sh deleted-*` 与 `./scripts/job-ops.sh` help 维护。

Restore 也是 root-family 级内部机制：只能从 root job id 恢复整组 root + child，并同时恢复 root submission key。恢复前会检查 family 是否处于完整软删除状态、deleted submission key 是否存在，以及同一 `caller_id + client_request_id` 是否已被新的 active key 占用；冲突时 fail-fast，不静默覆盖。

## 核心表职责

一图流中的持久化事实按职责分层：

```text
job_submission_keys      提交幂等
job_aggregates           Job 聚合事实和 root/child lineage
job_execution_attempts   单次 worker 执行尝试、lease、heartbeat、retry state
dispatch_outbox          Taskiq worker task 发布意图和 publish retry
callback_outbox          root Job 终态 callback 投递意图和 delivery retry
job_audit_events         排障时间线，不参与状态推进
```

| 表 | 角色 | 是否核心 | 不承担 |
|---|---|---|---|
| `job_aggregates` | Job 聚合事实源，保存状态、进度、结果、错误和 root/child lineage | 是 | 不保存每次执行尝试、dispatch publish 或 callback delivery 的 retry 状态 |
| `job_submission_keys` | 提交幂等键，保证同一 caller 的 `client_request_id` 可拒重或返回已有 Job | 是 | 不表示执行状态，不发布消息 |
| `job_execution_attempts` | 单次执行尝试，持有 lease、worker、heartbeat、attempt 状态和失败原因 | 是 | 不是审计历史表，不能从核心流程移除 |
| `dispatch_outbox` | 从数据库事务可靠发布 Taskiq 任务的 outbox | 是 | 不发布 callback，不表达 Job 业务终态 |
| `callback_outbox` | Job 终态 Callback 的投递账本和重试队列 | 是 | 不改变 Job 终态，不发布 worker 任务 |
| `job_audit_events` | 内部审计事件和排障时间线 | 辅助 | 不作为恢复、幂等或状态推进依据 |

当前 schema 已把 retry、执行权、publish 和 callback delivery 从 `job_aggregates` 中拆出：

| 表 | 当前负责的事实 | 明确不再保存的事实 |
|---|---|---|
| `job_aggregates` | Job 聚合状态、root/child lineage、提交参数 ref/hash、runtime snapshot、result/error、callback 配置、`active_attempt_id` | attempt 次数、retry policy、worker lease/CAS token、heartbeat、dispatch retry、callback delivery 摘要 |
| `job_execution_attempts` | `purpose`、`purpose_attempt_no`、lease、heartbeat、timeout、policy snapshot、retry chain、retry decision | Taskiq publish 次数、callback HTTP 投递次数 |
| `dispatch_outbox` | 发布同一个 `attempt_id` 给 Taskiq 的 publish retry、lease、dead-letter 和 policy snapshot | Job 业务终态、business execution retry、callback delivery |
| `callback_outbox` | root Job 终态 callback payload 快照、delivery retry、HTTP 响应、dead-letter 和 policy snapshot | Job 业务终态、worker task publish、execution attempt |

关键 schema 约束：

- public root 固定为 `root_job_id IS NULL + workflow_node_key IS NULL + client_request_id IS NOT NULL`；workflow child 固定为 `root_job_id IS NOT NULL + workflow_node_key IS NOT NULL + client_request_id IS NULL`。
- `job_params_ref` 和 `job_params_hash` 对每条 Job 必填；公开 result 和 canonical result 保存在 JSONB，外部大文件只通过 result 内部 artifact ref 表达。
- terminal Job 必须清空 `active_attempt_id`；`active_attempt_id` 通过复合 FK 约束为同一 `job_id` 的 execution attempt，避免跨 Job 执行权指针污染。
- `dispatch_outbox` 不保存 `job_id`，只通过 `attempt_id` 关联 execution attempt；它保留 `unique(event_id)`、`unique(attempt_id, task_name)`、`pending/leased/published/retrying/dead_letter` status 枚举、publish attempt 计数和 `publish_retry_policy_snapshot`。数据库约束要求 lease 字段与 `leased` 状态一致，`pending/retrying/published` 有 `next_attempt_at`，`dead_letter` 与 `dead_lettered_at` 双向一致。
- `callback_outbox` 保留 `unique(job_id, event_type)`、`unique(event_id)`、`pending/leased/delivered/retrying/skipped/dead_letter` status 枚举、delivery attempt 计数和 `delivery_retry_policy_snapshot`；callback 投递失败不会回写或改变 Job 终态。数据库约束要求 lease 字段与 `leased` 状态一致，`pending/retrying` 有 `next_attempt_at`，terminal callback 状态清空 `next_attempt_at`，`delivered_at` / `dead_lettered_at` 与对应终态双向一致。

已经移出 `job_aggregates` 的旧字段包括：`max_attempts`、`attempt_count`、`execution_attempts`、`execution_token`、`execution_generation`、`last_execution_at`、`last_heartbeat_at`、`timeout_seconds`、`callback_*` delivery 摘要、`parent_job_id`、`is_internal`、`job_params`、`result_ref` 和 `canonical_result_ref`。

## 配置边界

### 可通过 flat env 配置的主控项

当前 `.env.example` 暴露的是少量稳定控制意图：

| 配置 | 当前含义 |
|---|---|
| `MODEL_CALL_TIMEOUT_SECONDS` | AI 调用主 timeout；代码由它派生 worker timeout 链和 stale running 阈值 |
| `MAX_ACTIVE_JOBS` | active Job 接单上限；超出时创建请求返回繁忙 |
| `CALLBACK_TIMEOUT_SECONDS` | Callback 单次 HTTP 请求超时 |

当前也存在少量业务 `job_type` 配置，语义只绑定对应业务能力：

| 配置 | 当前含义 |
|---|---|
| `POSTER_TITLE_IMAGE_MAX_ITEMS` / `POSTER_TITLE_IMAGE_MAX_DRAW_COUNT` | `poster_title_image` 的批量数量和单 item 出图数量上限 |
| `POSTER_TITLE_IMAGE_ALLOWED_OSS_BUCKETS` / `POSTER_TITLE_IMAGE_ALLOWED_OSS_REGIONS` | `poster_title_image` 参考图输入 OSS 来源白名单 |
| `AUDIO_STEM_SEPARATION_ALLOWED_OSS_BUCKETS` / `AUDIO_STEM_SEPARATION_ALLOWED_OSS_REGIONS` | `audio_stem_separation` 输入 WAV OSS 来源白名单 |
| `AUDIO_STEM_SEPARATION_EXECUTION_PROVIDER` | `audio_stem_separation` 的 ONNX Runtime provider 模式：`auto` 有 CUDA 用 CUDA 否则 CPU，`cpu` 强制 CPU，`cuda` 强制 CUDA 且不可用时失败 |
| `HTDEMUCS_MODEL_DIR` | `audio_stem_separation` 使用的 htdemucs-ft ONNX required 模型目录 |
| `AUDIO_STEM_TRITON_URL` | `audio_stem_separation_triton` 调用的 Triton HTTP endpoint；按 `tritonclient` 约定不包含 `http://` 或 `https://` |
| `AUDIO_STEM_TRITON_TOKEN` | `audio_stem_separation_triton` 调用 EAS/Triton 服务时使用的 Authorization Token |
| `AUDIO_STEM_TRITON_MODEL_VERSION` | `audio_stem_separation_triton` 请求的 Triton 模型版本目录，默认 `1` |
| `AUDIO_STEM_TRITON_REQUEST_TIMEOUT_SECONDS` | `audio_stem_separation_triton` 单次 Triton infer HTTP 请求超时秒数 |

`AUDIO_STEM_SEPARATION_EXECUTION_PROVIDER=cuda` 只表示运行期必须选择 `CUDAExecutionProvider`；部署镜像或虚拟环境仍需安装 GPU 版 ONNX Runtime，并确保 Pod/容器能看到 NVIDIA GPU。当前项目默认依赖只包含 CPU 版 `onnxruntime`，避免本地 CPU 开发和通用验证被 GPU wheel 拉取、CUDA 运行时或镜像源问题阻塞。

`audio_stem_separation_triton` 是独立 job_type，保留 `audio_stem_separation` 的输入/输出业务合同，但模型推理通过 Triton HTTP 服务完成；音频下载、WAV 校验、分段、overlap-add、结果上传和 callback 仍由本服务负责。Triton worker 镜像需额外安装 `tritonclient[http]`，且 `AUDIO_STEM_TRITON_URL` 为空时该 job_type 首次执行会快速失败，不会回退到本地 ONNX Runtime。

新增或调整 Job 配置时，应优先暴露业务可理解的主控变量；worker timeout、stale running、callback claim window 等联动值由 `Settings` 统一派生并做 fail-fast 校验。

### 不属于 flat env 合同的内部策略

这些派生项和内部 retry policy 不进入 `.env.example` / flat env 配置合同；代码在 `Settings` 或 job registry 内部持有默认值，并在创建 attempt / outbox 时固化到 policy snapshot。

| 配置 | 当前边界 |
|---|---|
| `WORKER_SOFT_TIME_LIMIT` | 由 `MODEL_CALL_TIMEOUT_SECONDS` 加内部 buffer 派生，避免调用 timeout 和 worker timeout 倒挂 |
| `WORKER_HARD_TIME_LIMIT` | 由 soft timeout 加内部 buffer 派生 |
| `JOB_STALE_RUNNING_SECONDS` | 由 hard timeout 加内部 buffer 派生，保证 recovery 晚于 worker 硬超时 |
| dispatch publish retry 参数 | `orphan_timeout_seconds`、`dispatch_max_publish_attempts`、固定 publish delay 等落到 `dispatch_outbox` policy snapshot，不作为通用 flat env 旋钮 |
| callback delivery retry 参数 | `max_delivery_attempts`、`retry_delay_seconds` 等落到 `callback_outbox` policy snapshot；flat env 只暴露 `CALLBACK_TIMEOUT_SECONDS` |
| execution retry policy | 由 `JobRetryPolicy` / job_type executor 声明，并在创建 attempt 时固化到 `job_execution_attempts` |

这些旧键或不支持的通用旋钮不属于当前应用配置合同；出现在 `.env` 或 `ENV_FILE` 这类应用配置文件时会 fail-fast，而不是静默降级：

| 配置 | 拒绝原因 |
|---|---|
| `JOB_MAX_EXECUTION_ATTEMPTS` | 全局执行重试会绕过 job_type 幂等性、成本和副作用差异；当前按 job_type 声明 |
| `MODEL_CALL_MAX_RETRIES` | provider 调用重试会影响成本、账本和幂等边界；当前不是通用配置合同 |
| `JOB_ORPHAN_TIMEOUT_SECONDS` / `JOB_DISPATCH_MAX_PUBLISH_ATTEMPTS` | dispatch publish retry 是可靠性内部策略，落到 outbox policy snapshot，不进入通用 env 模板 |
| `CALLBACK_MAX_DELIVERY_ATTEMPTS` / `CALLBACK_RETRY_DELAY_SECONDS` | callback delivery retry 是可靠性内部策略，落到 outbox policy snapshot，不进入通用 env 模板 |
| `JOB_RECOVERY_INTERVAL_SECONDS` / `JOB_RECOVERY_BATCH_SIZE` / `JOB_RECOVERY_CALLBACK_BATCH_SIZE` | recovery 扫描节奏和批大小是代码默认内部参数，不进入通用 env 模板 |

修改入口细节以代码和脚本 help 为准。Job 内核机制变化必须同步本文；新增 `job_type`、schema、模型、Prompt 和对象存储产物的接入入口见 [`../api/extension-guide.md`](../api/extension-guide.md)。
