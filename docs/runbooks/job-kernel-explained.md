# Job 机制讲解：跟一个请求走完整条链路

本文是 [`job-kernel.md`](../current/job-kernel.md) 的配套讲解文档，目标是建立心智模型，不是补充新事实。

- 事实源仍然只有一个：字段名、默认值、约束、状态枚举以 [`job-kernel.md`](../current/job-kernel.md) 和代码为准；本文和它冲突时，以 [`job-kernel.md`](../current/job-kernel.md) 为准。
- [`job-kernel.md`](../current/job-kernel.md) 按"概念"组织（幂等 / lineage / 重试 / 恢复 / 表），适合查字典；本文按"一个请求实际怎么走"组织，适合建立地图。
- 本文不重复列举字段清单和 schema 约束，只在必要处引用。

读完本文，你应该能回答："一个 Job 提交进来之后，中间任何一步卡住，系统怎么知道、怎么救、救不回来会怎样"。

## 先看全局：四本账 + 一个巡检员

这套系统看起来复杂，是因为它不是一张表、一条队列，而是 **四本独立的账**，每本账回答一个不同的问题，外加一个周期性巡检的角色，负责把账本之间的缺口补上。

```text
job_aggregates            门面账：外部唯一看到的真相（状态/进度/结果/错误）
job_execution_attempts    干活账：这次执行谁在干、干到哪、能不能重干
dispatch_outbox           派工账：worker 有没有真的收到"去执行"的通知
callback_outbox           通知账：调用方有没有真的收到"完成了"的通知

Recovery（周期巡检）
  不单独持有业务事实
  只对比这四本账，把卡住的地方按各自账本自己的规则往前推一步
  绝不发明新的业务结果，绝不跳过 attempt lease 直接改终态
```

为什么不能合并成一本账？因为这四件事的失败方式、重试对象、重试预算完全独立：

- 派工失败（Redis/Taskiq 发不出去）不代表业务失败，不该消耗业务重试次数。
- 通知调用方失败（对方服务器 5xx）不代表 Job 失败，更不该让 Job 状态回退。
- 干活失败（模型超时）该不该重干，取决于 `job_type` 自己的策略，跟前两者无关。

记住这四本账的名字和各自负责的问题，后面所有内容都是在讲这四本账怎么互相配合。

## Part 1：跟着一次提交走一遍完整链路

以一次普通（非 workflow）提交为例，比如 `poster_title_image`。

### 1.1 提交：先问"这是不是同一件事"

调用方带着 `client_request_id` 发 `POST /jobs`。服务端要先回答一个问题：**这个请求，是不是已经处理过了？**

```text
caller_id + client_request_id
  -> 事务级 advisory lock（同一个 key 不会被并发处理两次）
  -> 查 job_submission_keys 有没有未删除的同 key 记录
       有，且内容指纹一致  -> 按 idempotency_mode 返回已有 Job 或报冲突
       有，但内容指纹不同  -> 报冲突（同一个 key 不能偷偷换内容）
       没有                -> 继续往下走，视为新请求
```

这一步解决的是网络重试、客户端超时重发、按钮连点，不是"失败了要不要重跑"——重跑是另一回事，见 Part 2。

### 1.2 接单：容量门禁

新请求要先过 `MAX_ACTIVE_JOBS` 这一关，防止无限接单压垮 worker/Redis：

```text
持 advisory lock 统计 active_jobs（queued + 仍持有 active attempt 的 running）
  active_jobs < MAX_ACTIVE_JOBS  -> 放行
  active_jobs >= MAX_ACTIVE_JOBS -> QUEUE_FULL，不创建任何记录
```

放行之后，`job_aggregates`（Job 本身）、`job_execution_attempts`（第一次 attempt）、`dispatch_outbox`（第一条派工意图）在**同一个数据库事务**里一起写入，然后提交事务，最后才尝试发布 Taskiq 消息。这个顺序很重要：数据库先落地，再触发外部效果，是整套可靠性设计的地基。

### 1.3 派工：Dispatch outbox 三级跳

“Job 已经创建”和“worker 已经知道要执行”是两件事，中间隔着一次 Redis/Taskiq 发布，这个发布可能失败。`dispatch_outbox` 就是为了不丢失这次通知：

```text
pending   刚创建，还没试过发布
  -> 发布成功                 -> published（附带一个 orphan 检查时间窗口）
  -> 发布失败，还有额度        -> retrying（等 publish_retry_delay_seconds 后重试）
  -> 发布失败，额度耗尽        -> dead_letter（不会再自动重试，见 Part 3 的坑）

published 发布成功后，如果长时间没人来 claim（worker 没收到/丢消息）
  -> 视为 orphan，巡检重新发布同一个 attempt_id
```

这里的关键词是"同一个 `attempt_id`"：不管重发几次，都是在通知同一次执行尝试，不会因为发布重试而多算一次业务执行。

### 1.4 干活：Attempt 执行权

worker 收到 `attempt_id` 后，第一件事不是执行，是**抢执行权**：

```text
claim_attempt_for_execution
  条件：Job.active_attempt_id == 这个 attempt_id
       且 Job.status == queued
       且 Attempt.status == pending
  抢到 -> 发一个 lease_token，Attempt 转 running，Job 转 running
  抢不到（已经被别的 worker 抢走，或状态已经变了）-> 静默跳过
```

之后所有推进进度、写终态的操作，都必须带着这个 `lease_token` 去匹配数据库里当前的 lease，对不上就直接失败——这就是"同一个 attempt 任何时刻只有一个 worker 能写"的实现方式，不需要分布式锁，纯靠数据库条件更新。

执行期间 worker 会在关键节点续约心跳（`heartbeat_attempt`），把 `lease_expires_at` 往后推。**心跳只在写进度的时候顺带发生，不是后台独立线程持续续约**——如果模型调用中间那一段时间特别长而没有中间进度点，lease 有可能在这段时间内不被续约到，这也是当前实现里一个已知的硬化点（见 Part 7）。

### 1.5 交卷：终态与结果

执行成功或失败，都要求"当前 lease 仍然有效"这个条件成立才能写终态：

```text
成功
  mark_succeeded：Job -> succeeded，active_attempt_id 清空，写 result
  同一事务里顺手创建 callback_outbox（如果配置了 callback）

失败
  mark_attempt_failed：先判断能不能重试（见 Part 2）
    能重试   -> 创建下一个 attempt，Job 回到 queued，重新走一遍 1.3-1.5
    不能重试 -> Job -> failed，active_attempt_id 清空，写 error
              同一事务里顺手创建 callback_outbox
```

`active_attempt_id` 清空这一步有数据库约束兜底：`status` 是终态时 `active_attempt_id` 必须是 `NULL`，反过来也一样——这个不变量不是靠代码自觉，是靠 `CheckConstraint` 锁死的。

### 1.6 通知：Callback outbox

Job 终态之后，如果配置了 `callback_url`，`callback_outbox` 负责把这个终态告诉调用方，逻辑和 dispatch outbox 长得很像，但目标和后果完全不同：

```text
没配 callback_url                        -> 不创建 outbox
配了，但没订阅当前这个终态事件            -> 创建为 skipped
配了，订阅了                              -> 创建为 pending，尝试投递

投递成功（2xx + 合同要求的 JSON ack）      -> delivered
投递失败/超时，还有额度                    -> retrying
投递失败，额度耗尽                         -> dead_letter
```

无论 callback 最终是 `delivered` 还是 `dead_letter`，**Job 自己的 `status`/`result`/`error` 都不会因此改变**——callback 只是"通知"，不是"确认"，Job 的业务终态在 1.5 就已经写死了。

## Part 2：出问题时——三套"重试"分别保护什么

这是最容易搞混的地方，因为三套机制都叫 retry，但对象、触发条件、重试预算完全独立，互不消耗对方的额度。

```text
出错发生在哪一步                  谁来决定要不要重试            重试的是什么
--------------------------------------------------------------------------------
派工：Taskiq publish 失败         dispatch_outbox 自己的策略     重新发布同一个 attempt_id
干活：worker 执行失败/超时        job_type 声明的 retry_policy   为同一个 Job 创建下一个 attempt
通知：callback HTTP 投递失败      callback_outbox 自己的策略     重新投递同一份 callback 内容
```

判断"干活失败要不要重试"（`mark_attempt_failed` 里的核心逻辑）：

```text
can_retry =
  这次失败的 error.code 命中该 attempt 的 policy_retryable_error_codes
  且 Job 还没被判定为最终 failed
  且 purpose_attempt_no < policy_max_attempts

命中 -> 创建 next attempt（同一个 retry_chain_id，previous_attempt_id 指回上一个）
        Job 重新回到 queued，走一遍新的 dispatch_outbox
没命中 -> Job -> failed，不再有下一次
```

默认策略非常保守：

```text
workflow_orchestration（root 负责编排的那次 attempt）
  max_attempts=3，只在 JOB_STATE_TRANSITION_CONFLICT / TASKIQ_PUBLISH_FAILED 时重试

business_execution（真正跑业务逻辑的那次 attempt）
  max_attempts=1，retryable_error_codes 为空
  也就是说：默认情况下，业务失败一次就是最终失败，不会自动重跑
```

只有某个 `job_type` 自己在 executor 里声明了更宽松的 `retry_policy`，`business_execution` 才会有额外重试。比如 `poster_title_image_style_probe` 和 `poster_title_image_generate_item` 声明了 `max_attempts=2`，只对 `MODEL_CALL_TIMEOUT` / `OSS_FETCH_FAILED` / `OSS_WRITE_FAILED` 这三类瞬时错误放宽一次。这是**按 job_type 主动选择的例外**，不是全局规则。

这套设计背后的取舍是：业务重试涉及"这次模型调用/OSS 写入到底有没有副作用、重跑会不会产生重复计费或重复产物"，这个判断只有 `job_type` 自己知道，所以不能有一个全局的"失败重跑 N 次"开关——这也是为什么 `.env` 里明确拒绝 `JOB_MAX_EXECUTION_ATTEMPTS` 这种全局配置。

## Part 3：Recovery 巡检员——能修什么，不能修什么

Recovery 是周期性任务（默认 60 秒一轮），每轮做的事情都是"看数据库里已经落地的事实，补一个本该发生但没发生的动作"。

```text
卡在哪一步                           Recovery 怎么发现               怎么救
------------------------------------------------------------------------------------------
worker 抢到 attempt 后失联           lease_expires_at 已过期          判定 attempt 失败
（进程崩溃/机器重启/网络分区）        （stale running）                 -> 走 Part 2 的重试判断

Taskiq publish 失败或消息丢失        dispatch 到期未发布/发布后        重新发布同一个 attempt_id
                                      长期无人 claim（orphan）

Job 已终态但 callback_outbox 缺失    终态 root Job 找不到对应 outbox   补建 callback_outbox

callback 投递失败，到了重试时间      next_attempt_at 已到              重新投递

workflow root 该往前推进但没推进     root 在等 child，但没人调用过     调用 reconcile_workflow_root
（编排逻辑异常退出、进程崩溃）        reconciler                        补建 ready child 或收敛终态

AI 调用账本长期停在 pending          超过 stale 阈值仍未终态           标记为失败，避免 billing 卡死
```

Recovery 明确不做的事，理解这条边界比理解它做什么更重要：

```text
不会：根据 provider 侧的外部状态去猜"其实已经成功了"
不会：重放一个已经 succeeded 的业务结果
不会：绕开 attempt lease 直接改 Job 的业务终态
不会：把一个已经判定 do_not_retry 的 Job 重新拉回 queued
```

也就是说，Recovery 只能修"账本之间没对齐"的问题，修不了"业务本身该不该重跑"的问题——后者的决定权始终在 `job_type` 声明的 retry policy 里，创建 attempt 时就已经固化成 snapshot，事后改代码也不会回头影响已经存在的 attempt。

**当前一个需要留意的缺口**：dispatch publish 重试耗尽进入 `dead_letter` 后，missing-dispatch 巡检会看到 outbox 已经存在而不会补建，due-dispatch 巡检又不会选中已经 `dead_letter` 的行——这条路径目前没有让 Job 自动收敛到一个明确终态，Job 可能长时间停在 `queued`。这是 [`../plans/job-kernel-reliability-review.md`](../plans/job-kernel-reliability-review.md) 里记录的 P1 项，属于"知道、还没修"，不是"没意识到"。

## Part 4：一个 Job 可能其实是一串 Job

`workflow` 类型的 `job_type`（比如内部会拆成 style probe / 生图 / 合并三步的场景）提交后，看起来是一个 Job，实际内部是 **一个 root + 若干 internal child**，root 自己不干业务活，只负责编排：

```text
public root Job（对外唯一入口，查询/callback/billing 都对它）
  active attempt 的 purpose = workflow_orchestration
    职责：读冻结好的 workflow_plan，创建"依赖已满足"的 child Job
    自己不跑模型、不写 OSS

internal child Job（root_job_id 指向 root，带 workflow_node_key）
  active attempt 的 purpose = business_execution
    职责：真正执行 leaf 业务逻辑
    外部无法直接提交、查询这类 job_type（visibility=internal）
```

child 之间的依赖关系（先做 A 再做 B）不是靠轮询查表，是 root 编排 attempt 冻结在 `workflow_plan.nodes[].depends_on` 里的一份 DAG。每次有 child 终态变化（成功/失败），都会触发 `reconcile_workflow_root`：

```text
先看有没有必须依赖已完成、状态失败的 required child
  有 -> 按 failure_policy 决定：fail_fast 直接判root失败；allow_partial 看有没有至少一个成功
再看依赖已满足但还没创建的 child
  有 -> 创建它们的 Job + attempt + dispatch outbox（同样受 workflow_node_key 幂等约束保护，
        重复调用 reconcile 不会为同一个节点建两个 child）
所有 required child 都成功了
  -> 汇总结果，把 root 标记为 succeeded
都不是上面几种
  -> 只更新 root 的进度百分比（按各节点权重加权），root 继续等
```

这意味着：**child 失败不会自动重跑整个 workflow，也不会重跑已经成功的 child**——root 的失败是"编排层看到子结果后做的一次终态收敛判断"，不消耗、也不触发 root 自己的 execution attempt retry。

## Part 5：生命周期终点——软删除是"一整个家庭"一起消失

`root Job + 它所有的 internal child` 被当作一个不可拆分的单位处理软删除，前提是这个家庭已经完全"定型"：

```text
只有同时满足才允许软删除：
  root 处于 succeeded/failed，且没有 active attempt
  callback 没配置，或者终态 callback 已经 delivered/skipped/dead_letter
  所有 child 都没有未终态或仍持有 active attempt 的记录

软删除同一事务做两件事：
  job_aggregates：root + 所有 child 的 deleted_at/deleted_reason 一起写
  job_submission_keys：root 的幂等键也标记删除（释放 client_request_id，允许调用方重新用它提交新 Job）
```

`job_execution_attempts` / `dispatch_outbox` / `callback_outbox` / `job_audit_events` 都不单独维护删除状态，它们是否"可见"，完全follow 关联 `job_aggregates` 的删除状态——这样审计链路永远完整，删除只影响查询视图，不影响历史事实。

恢复（restore）是反向操作，同样按整个家庭恢复，并且会检查 `caller_id + client_request_id` 有没有被新提交的 Job 占用——占用了就拒绝恢复，不会静默覆盖调用方新提交的东西。

## Part 6：配置到底分几层？

回到最初的困惑：配置项看起来乱，是因为它们分布在三个完全不同的层次，混在一起读就会觉得杂乱。分开看，每一层只回答一个问题。

```text
第一层：.env 里真正暴露给你调的主控项（只有 3 个）
  MODEL_CALL_TIMEOUT_SECONDS   一次 AI 调用最多等多久
  MAX_ACTIVE_JOBS              系统同时能接多少活跃 Job
  CALLBACK_TIMEOUT_SECONDS     一次 callback HTTP 请求最多等多久
  （另外 CALLBACK_SIGNING_SECRET 是必填密钥，不是策略参数）

第二层：job_type 自己在代码里声明的策略（不进 .env，因为每个业务不一样）
  retry_policy       这个 job_type 的业务失败要不要重试、重试几次
  timeout_seconds    这个 job_type 一次执行的超时上限

第三层：内部派生值和固定常量（既不进 .env，也不由某个 job_type 决定）
  worker_soft_time_limit / worker_hard_time_limit / job_stale_running_seconds
    —— 都是从第一层的 MODEL_CALL_TIMEOUT_SECONDS 加固定 buffer 算出来的
  dispatch outbox 的发布重试次数/退避策略
  callback outbox 的投递重试次数/退避策略（默认值来自 CallbackSettings，不是 ORM 列默认值）
```

第三层为什么不直接暴露成配置，而是要派生？因为这几个值之间有硬性的先后顺序要求，如果各自独立配置，很容易配出"worker 还没跑完，recovery 就已经把它当成 stale 抢救"这种自相矛盾的组合。看一眼默认值怎么串起来的就明白了（`MODEL_CALL_TIMEOUT_SECONDS` 默认 300 秒）：

```text
MODEL_CALL_TIMEOUT_SECONDS   300s   一次模型调用最多等这么久
  + 300s buffer
worker_soft_time_limit       600s   worker 软超时，必须晚于单次调用超时
  + 60s buffer
worker_hard_time_limit       660s   worker 硬超时，必须晚于软超时
  + 600s buffer
job_stale_running_seconds   1260s   recovery 才把 attempt 当 stale，必须晚于硬超时

如果这四个数字都能各自独立配置，运维一旦手滑把某个改小，
就可能出现"worker 还在合法执行"却被 recovery 提前抢救的连锁故障。
派生 = 把这条不等式关系用代码锁死，不给手滑的机会。
```

`CALLBACK_TIMEOUT_SECONDS` 同理派生出 `delivery_timeout_seconds`（callback 领取窗口，默认 5+175=180 秒），`orphan_timeout_seconds`（300 秒）同时兼作 dispatch 的发布租约窗口。

一张"我想改 xxx，该去哪改"的速查表：

| 我想要的效果 | 去改什么 | 属于第几层 |
|---|---|---|
| 让单次模型调用等更久 | `.env` 的 `MODEL_CALL_TIMEOUT_SECONDS` | 第一层，会连带推高 worker 超时和 stale 阈值 |
| 让系统能同时接更多活 | `.env` 的 `MAX_ACTIVE_JOBS` | 第一层，`0` 表示不限制 |
| callback 请求等更久 | `.env` 的 `CALLBACK_TIMEOUT_SECONDS` | 第一层 |
| 某个 job_type 失败后自动重跑 | 该 executor 类里声明 `retry_policy` | 第二层，只影响这一个 job_type |
| 某个 job_type 单次执行给更长时间 | 该 executor 类里的 `timeout_seconds` | 第二层 |
| dispatch 发布重试次数/窗口 | `JobSettings.dispatch_max_publish_attempts` / `orphan_timeout_seconds` | 第三层，代码常量，不进 `.env` |
| callback 投递重试次数/间隔 | `CallbackSettings.max_delivery_attempts` / `retry_delay_seconds` | 第三层，代码常量，不进 `.env` |

第一层之外的旧键（`JOB_MAX_EXECUTION_ATTEMPTS`、`JOB_ORPHAN_TIMEOUT_SECONDS`、`CALLBACK_MAX_DELIVERY_ATTEMPTS` 等）写进 `.env` 会直接 fail-fast，不是"写了没用"，是"写了直接报错启动不了"——这是有意设计成这样，逼着改配置的人去改对层次，而不是在错的地方留一个不生效的旋钮。

## Part 7：现在还没做到的事

这套机制的骨架是对的：四本账分离、attempt lease 保护写入、恢复循环补缺口、root/child lineage 有数据库约束、软删除不动物理数据。但按生产级"多租户、高并发、长任务、真金白银计费"的标准，还有几个明确知道、还没关闭的缺口，主要是：

- caller 身份目前靠请求头自报，不是从凭证派生的，多租户场景下能被伪造。
- `MAX_ACTIVE_JOBS` 的并发闸门锁窗口偏窄，理论上能被并发投递击穿。
- 上面提到的 dispatch dead-letter 收敛缺口。
- 长任务执行期间没有独立的心跳续约线程，只靠进度点顺带续约。

完整清单、风险分级和验收标准见 [`../plans/job-kernel-reliability-review.md`](../plans/job-kernel-reliability-review.md)，这份文档只负责让你知道"骨架讲的通"，硬不硬化是另一个独立的决策，不在本文讨论范围。

## 附：高频问题

**execution attempt retry、dispatch publish retry、callback delivery retry，到底怎么分？**
看失败发生在谁的动作上：worker 执行本身失败 -> execution retry；worker 根本还没收到通知 -> dispatch retry；调用方没收到终态通知 -> callback retry。三者互不消耗对方的次数，一次 execution attempt 里可能发生好几次 dispatch retry。

**worker 进程直接崩溃了会怎样？**
它持有的 attempt lease 会在 `job_stale_running_seconds` 之后过期，Recovery 下一轮扫描会把这个 attempt 判定失败，再走一遍 Part 2 的重试判断——不会一直卡在 `running`，但要等到 lease 过期那一刻，不是立刻发现。

**同一个 attempt 会不会被两个 worker 同时执行？**
不会。`claim_attempt_for_execution` 要求 `Job.active_attempt_id` 精确匹配这个 attempt 且状态为 `pending`，抢到之后其他 worker 的 claim 一律落空。就算 Taskiq 重复投递同一条消息，第二次 claim 也会因为状态已经是 `running` 而拿不到。

**为什么 `business_execution` 默认 `max_attempts=1`，失败了不会自动重跑？**
因为要不要重跑取决于这次失败有没有产生外部副作用（模型已经计费、OSS 已经写入一半），这个判断只有具体 `job_type` 知道，所以默认最保守：失败一次就是失败，除非这个 `job_type` 自己声明了对哪些错误码放宽。

**软删除之后数据还在吗？**
在。软删除只写 `deleted_at`/`deleted_reason`，不删行；`job_execution_attempts`/`dispatch_outbox`/`callback_outbox`/`job_audit_events` 完全不受影响，只是普通查询和排障命令默认看不到，需要走 `./scripts/jobs.sh deleted-*` 专门查看。

**workflow 的 internal child 能被外部直接提交或查询到吗？**
不能。`visibility=internal` 的 `job_type` 在任何环境下都拒绝外部直接提交；child Job 也没有独立的对外查询入口，调用方只能查 root Job。
