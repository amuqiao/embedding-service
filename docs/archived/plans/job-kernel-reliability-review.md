# Job Kernel 可靠性审查

> Archived: 本文是 Job kernel 历史可靠性审查，不再作为活动计划维护。当前硬化计划见 [`../../plans/job-kernel-hardening.md`](../../plans/job-kernel-hardening.md)。

本文从生产事故入口审查当前 Job 机制。当前实现事实仍以
[`../current/job-kernel.md`](../current/job-kernel.md) 和代码为准；本文只把当前事实作为风险证据，记录
可靠性、一致性、安全性和测试覆盖的硬化计划与验收清单，不替代 current 或 api 合同文档。

## 心智模型

把 Job kernel 看成六道连续守门面，而不是单个“异步任务表”：

1. **身份边界**：谁在投递、查询、接收回调，是否能伪装成别的 caller。
2. **接单账本**：同一个业务请求是否只产生一个 root Job，容量闸门是否真正生效。
3. **执行所有权**：某个 Attempt 在某个时间点是否只有一个 worker 有写入资格。
4. **外部副作用账本**：模型调用、OSS 写入、计费和 Callback 是否可重复、可追溯、可收敛。
5. **恢复收敛**：Redis、worker、API 或 callback 目标短暂失败后，系统是否最终进入终态。
6. **生命周期清理**：root/child family、幂等键、callback、billing 和删除恢复是否保持同一视图。

这套实现已经具备正确的骨架：Job / Attempt / Dispatch outbox /
Callback outbox 分离，Attempt lease 保护状态写入，恢复循环扫描缺口，root/child
lineage 有持久化约束，软删除不直接物理删除业务视图。

但按生产可靠性标准，当前仍不应视为“多 caller、不可信调用方、长模型调用、高并发接单”场景下完全闭环。最高风险集中在身份绑定、接单容量原子性、长执行副作用幂等、Callback ACK
收敛和公开信息脱敏；Dispatch dead-letter 已具备自动失败收敛，剩余是人工 replay 入口与更多边界测试。

## 风险分级

| 级别 | 风险 | 影响 | 建议 |
|---|---|---|---|
| P0 | `caller_id` 由共享 `SERVICE_API_KEY` 后的 `X-AI-Service-Caller-ID` 决定，未与凭证绑定 | 任一持有服务密钥的调用方可伪装其他 caller，造成跨 caller 查询、幂等键碰撞或回调混淆 | 上线多 caller 前必须改成凭证映射 caller，或每个 caller 独立 token 且服务端派生 caller |
| P1 | `MAX_ACTIVE_JOBS` 容量锁在创建 Job / Attempt / outbox 前释放 | 并发投递可突破活跃 Job 上限，压垮 worker、Redis 或 callback 积压 | 将容量判断与创建放在同一事务锁窗口，或使用数据库 semaphore / quota 表 |
| P1 | 长模型调用期间没有周期性 lease 续约，`update_progress` 不延长 `lease_expires_at` | 已部分缓解：claim / heartbeat 已使用 `max(job_stale_running_seconds, attempt.timeout_seconds)`，较长 job_type timeout 不会被全局 floor 提前判 stale；但 `_execute()` 期间仍无周期性续约，旧 worker 的数据库写会被 CAS 拦住，外部模型、OSS、计费副作用仍可能重复发生 | 长执行仍需要周期性 heartbeat；外部副作用必须有幂等键和账本约束 |
| P1 | Callback 接收方把“重复事件已处理”返回为 `accepted=false` 时，服务会按拒收持续重试直到失败 | 去重实现稍有偏差就会把成功业务事件拖入 callback dead-letter | API 合同明确重复事件应返回 `accepted=true`，测试覆盖重复投递 ACK 语义 |
| P1 | root workflow 失败详情会向公开查询和 callback 暴露 child id、node key、child 原始错误 | 内部拓扑、节点命名和 provider 错误可能泄漏给外部系统 | 对公开 `job_error` 做脱敏映射，内部详情只保留在审计日志或管理查询 |
| P1 | `scripts/k8s.sh check` 设计上打印完整数据库和 Redis 密码 | 排障输出容易进入终端记录或日志系统 | 默认脱敏，只有显式 `--show-secrets --confirm` 才输出明文 |
| P2 | `ai_call_ledger_entries.job_id` 与 `attempt_id` 缺少复合外键约束 | 正常写路径之外的数据损坏可能让计费/审计 ledger 指向错误 Attempt | 增加 `(job_id, attempt_id)` 到 `job_execution_attempts(job_id, id)` 的复合约束 |
| P2 | job_type `timeout_seconds` 缺少上界和运行形态校验 | 过大的声明超时会把 stale recovery / retry 发现时间静默拉长到同量级 | 为 registry 增加按运行形态可解释的 timeout 上界，或把长任务 timeout 纳入单独的容量/运维评审 |
| P2 | `root_job_id`、active `submission_key`、retry chain 主要依赖 repository 写路径维持形状 | 旁路写入或未来改造可能破坏 family、删除恢复或 retry 语义 | 补充 DB trigger / partial unique / 复合外键，把当前写路径不变量下沉到数据库 |
| P2 | Callback URL 和 ack details 日志过宽 | URL query secret、目标响应体或错误细节可能污染日志 | 日志只保留 host/path/status，ack details 限长并过滤敏感字段 |
| P2 | `CallbackOutbox` ORM 默认值与配置/迁移默认值不一致 | 绕过 service 创建 outbox 时重试策略不一致 | 移除 ORM 业务默认或对齐到配置语义，并用测试锁住 |
| P2 | `replay-dispatch` 只处理尚未被 recovery 终态收敛的 dead-letter dispatch | 已经 failed 且可能已经发出 failed callback 的 Job 不会被重开，运维若要二次业务执行需要 retry-as-new-job 设计 | 后续另行设计 clone/retry-as-new-job，明确二次终态 callback 合同 |
| P2 | Dispatch / callback lease 回收、容量冲刺、workflow child 竞争等缺少直接测试 | 回归风险主要留在生产流量中暴露 | 增加定向单元测试和小型并发测试 |

## 分面审查

### 1. 身份、授权与隔离

当前 API route 使用 `require_service_auth` 保护，服务认证成功后信任
`X-AI-Service-Caller-ID`。这适合“单上游服务、caller 只是内部租户标签”的模板阶段，不适合作为多租户安全边界。

生产判断：

- 如果只有一个可信上游服务，且 caller_id 由该上游服务内部生成，当前风险可接受但需要文档明确。
- 如果多个业务方共享 `SERVICE_API_KEY`，当前设计不可接受。
- 如果 Job id 可被猜测、日志泄漏或跨系统传播，伪造 caller_id 会扩大为跨 caller 查询风险。

硬化目标：

- token/API key 与 caller_id 绑定，由服务端从凭证派生 caller。
- 查询、幂等键、callback owner 都使用派生 caller，不接受外部自由声明。
- 管理员或内部排障查询使用独立 admin 权限，不复用业务 caller 认证。

### 2. 投递、幂等与容量

`create_job` 已经有 client request advisory lock、submission key 查询、payload
fingerprint 校验、Job/Attempt/Dispatch outbox 同事务创建和提交后 publish。这是正确方向。

主要问题是容量闸门不是严格闸门：`MAX_ACTIVE_JOBS` 的 advisory lock 在计数后释放，然后才继续创建 Job。高并发下多个请求可能都看到容量未满，然后一起创建，超过上限。

硬化目标：

- 容量计数和 Job 创建在同一锁窗口内完成。
- 如果需要按 caller 或 job_type 限流，应单独建 quota / semaphore，不把全局容量逻辑扩展成复杂条件查询。
- `QUEUE_FULL` 需要定向测试，覆盖并发冲刺而不是只测串行满载。

### 3. Attempt 所有权、重试与超时

Attempt claim 使用 active attempt、状态、purpose、lease token 和行锁共同保护，成功/失败/progress 写入也带 active attempt 与 lease token 条件。这能阻止旧 worker 在 lease 失效后覆盖新 attempt 的数据库终态。

风险不在数据库终态覆盖，而在长执行期间的外部副作用：

- `run_job_attempt` 在执行前 heartbeat；claim / heartbeat 的 lease 窗口已取 `max(job_stale_running_seconds, attempt.timeout_seconds)`，但长模型调用期间没有周期性续约。
- `update_progress` 更新 `heartbeat_at`，但不延长 `lease_expires_at`。
- recovery 仍可能在较长 attempt timeout 过期后把长时间无 heartbeat 的 Attempt 判定为 stale，并创建 retry。
- 旧 worker 的终态写入会失败，但它已经发出的模型请求、OSS 写入、provider 计费可能无法撤回。

硬化目标：

- worker 执行长任务时定期 heartbeat 并延长 lease；当前仅已完成 per-attempt timeout 拉长 claim / heartbeat lease 窗口。
- 模型调用、OSS artifact、AI ledger、callback 都要有稳定幂等键，允许重复执行但只记一次业务结果。
- worker soft/hard timeout、stale running timeout、provider timeout 必须有集中校验，确保 stale 判断晚于合法执行窗口。
- job_type `timeout_seconds` 需要有可解释上界，避免单个 job_type 静默把 stale recovery 窗口拉到不可运维的量级。

### 4. Dispatch outbox 与恢复收敛

Dispatch outbox 让数据库提交和 Redis publish 解耦，恢复循环会扫描 missing dispatch、due dispatch 和 stale attempts。这个模型可以承受 API 在提交后崩溃、Redis 短暂不可用和 worker 没收到任务。

当前已补齐自动收敛：publish 重试耗尽后，`dispatch_outbox` 进入 `dead_letter`，recovery 会把对应 `queued` + active pending attempt 的 Job 收敛为 `failed`，错误码为 `DISPATCH_PUBLISH_EXHAUSTED`，并走终态 callback outbox。

当前也已新增确认式写操作入口 `./scripts/job-ops.sh replay-dispatch <job_id> --confirm`。该入口只重放尚未被 recovery 终态收敛的 `queued` + active pending attempt + dead-letter dispatch，不重开已经 `failed` 的 Job，避免 failed callback 已经对外送达后再产生第二个终态。

剩余硬化目标：

- 对 dead-letter 收敛后的 Job 查询要显示可操作原因，而不是只显示普通 failed。
- 继续增加测试覆盖 leased publish 过期、publish 重试耗尽和 retry-as-new-job 边界。

### 5. Callback outbox、安全与幂等

Callback outbox 已经有 event id、签名 header、HTTPS URL 校验、私网地址拦截、delivery attempt 和 retry delay。对模板来说，这是比较完整的基础。

剩余硬化：

- 签名 header、重复 `event_id` 应返回 `accepted=true`、非 2xx 重试和 `accepted=false` 拒收语义需要自动化测试锁住。
- 日志不应输出完整 callback URL，尤其是 query string。
- ack details 不应无界保存或写日志，避免目标服务把敏感信息或大 payload 回传。
- callback lease 回收和 delivery dead-letter 需要测试锁住。
- 需要给接收方补一份可执行 runbook 或示例 verifier，覆盖签名校验、timestamp 窗口和 `event_id` 去重。

### 6. root/child workflow 生命周期

当前 root/child 由同一 Job 表表达，child 使用 `root_job_id`、`workflow_node_key` 和 root/child 形状约束；依赖关系来自 root 冻结的 `workflow_plan.nodes[].depends_on`。orchestrator 在 child 完成后推进 root 汇总。

需要关注的数据不变量：

- DB 只保证 `root_job_id` 引用一个 Job，未保证被引用者是 public root；正常 repository 写路径会以 root Job 创建 child，但数据库层还没有把这个不变量锁死。
- child 创建依赖“先查再创建 + unique(root,node)”抵抗重复，但第二次 lookup 竞争缺少直接测试。
- root failure error 会携带 child 细节到公开响应，需要分离 public error 与 internal diagnostic。

硬化目标：

- DB 或 repository 层禁止 child-of-child。
- workflow root 只暴露业务可理解的失败摘要；child job id、node key、provider 原始错误只给内部排障。
- child 创建竞争、root 汇总重复调用、child terminal 后 root recovery 补扫都应有测试。

### 7. 删除、恢复与保留

当前没有公开 HTTP DELETE route。软删除通过 repository 控制 root family，要求 root 已 settled、callback 已 settled、没有未终态 child，并同步软删除 submission key。这符合“删除视图，不破坏审计账本”的方向。

需要补齐的约束：

- 一个 Job 的 active submission key 数量应由 DB 保证为 1。
- restore 时的 key 冲突检测已经存在，但依赖前置数据形状健康。
- 清理策略要明确 billing、AI ledger、callback outbox、artifact 元数据是否保留，以及保留多久。

### 8. 数据库不变量

当前迁移已经补了很多关键约束，例如 active attempt 同 Job、root/child 形状、Dispatch outbox
状态和 attempt 绑定、Callback event 唯一性。这是可靠性基础。

以下不是已确认正常写路径缺陷，而是防御性硬化目标：把当前 service/repository 维持的不变量下沉到 DB，让旁路写入、未来重构或数据损坏更早失败。

- `ai_call_ledger_entries(job_id, attempt_id)` 必须指向同一个 attempt。
- `jobs.root_job_id` 必须指向 public root。
- `job_submission_keys(job_id, key_kind)` active 唯一。
- `job_execution_attempts.previous_attempt_id` 必须属于同一个 job，purpose 也应一致。
- ORM 默认值、迁移默认值和 `Settings` 默认值保持同一语义，避免旁路写入产生不同生命周期。

## 测试验收清单

上线前建议至少补齐这些测试：

- 身份隔离：同 token 不得伪造其他 caller；不同 caller 不能查询、复用或覆盖彼此 idempotency key。
- 投递幂等：同 key 同 fingerprint 返回同 Job；同 key 不同 fingerprint 返回冲突；并发投递不突破容量上限。
- Dispatch：pending 到 leased 到 published；leased 过期可重试；publish 重试耗尽后 Job 自动失败收敛；人工 replay 入口另行设计。
- Attempt：lease token 失效后旧 worker 不能写终态；长执行 heartbeat 会延长 lease；stale recovery 不会误杀合法长任务。
- 外部副作用：模型调用账本、OSS artifact 和 billing 在 retry 后保持幂等。
- Callback：签名 header 合同、接收方非 2xx 重试、lease 过期回收、重试耗尽终态、日志脱敏。
- Workflow：child 创建竞争、child 失败后 root 公开错误脱敏、root recovery 补扫 terminal children。
- 删除恢复：未终态 family 不能删；删除后幂等键释放；restore 冲突失败；restore 成功后 family 查询一致。

## 生产准入判断

当前 Job kernel 可作为模板和受控内部链路继续迭代，但生产准入应分层判断：

- **单可信上游、低并发、短任务、内部回调**：可在明确风险的前提下试运行，并加强监控。
- **多 caller 共享服务、长模型调用、真实计费、高并发投递**：必须先关闭 P0/P1。
- **对外产品化平台**：除 P0/P1 外，还需要补齐 API callback 合同、日志脱敏、配额隔离、人工恢复 runbook 和混沌/故障注入验证。

最低生产硬化门槛：

1. caller 身份由凭证派生，不信任外部自由传入。
2. 容量闸门与 Job 创建原子化。
3. 长执行有周期 heartbeat，外部副作用有幂等账本。
4. 关键跨表不变量进入数据库约束或强制 repository 校验。
5. 公开错误、callback URL、ack details 和 k8s secret 输出完成脱敏。
6. 上述测试验收清单中的 P0/P1 路径全部自动化。
