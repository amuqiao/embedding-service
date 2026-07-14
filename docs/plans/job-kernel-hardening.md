# Job Kernel Hardening Plan

本文是当前唯一活动 Job kernel 硬化计划，负责把可靠性、一致性和公开信息边界中仍值得短期处理的风险收口成可执行事项。当前实现事实见 [`../current/job-kernel.md`](../current/job-kernel.md)，外部调用合同见 [`../api/service-contract.md`](../api/service-contract.md)。

## Current Baseline

- Job / Attempt / Dispatch outbox / Callback outbox 已分离。
- Attempt claim、heartbeat、终态写入已使用 active attempt 和 lease token 保护。
- root / child lineage 已由同一 Job 表表达，root 是公开查询、callback 和 billing 入口。
- 本仓库仍是 AI Job 服务模板，不承担用户系统、通用工作流平台或生产部署平台。

## Remaining Gaps

| 优先级 | Gap | 收口边界 |
|---|---|---|
| P0 | 多 caller 场景下，`caller_id` 不能只依赖共享服务密钥后的请求头 | 如果仍是单可信上游，先在合同中写清边界；如果进入多 caller，必须由凭证派生 caller |
| P1 | `MAX_ACTIVE_JOBS` 容量判断需要和 Job 创建保持原子性 | 不扩展成复杂配额系统，先保证全局闸门真实生效 |
| P1 | 长模型执行期间缺少周期性 lease 续约 | 只处理长 Job 不被误判 stale 和旧 worker 副作用重复的核心路径 |
| P1 | Callback 重复事件 ACK 语义需要测试锁住 | 重复 `event_id` 已处理时应视为 accepted，不把成功业务事件拖入 dead-letter |
| P1 | root workflow 公开错误不应暴露 child id、node key 或 provider 原始错误 | public error projection 与 internal diagnostic 分离 |
| P2 | 关键跨表不变量仍有一部分依赖 repository 写路径 | 只补会影响 billing、attempt、root/child 一致性的约束或测试 |

## Planned Work

1. 明确 caller 安全边界：单可信上游写入 API contract；多 caller 进入产品化前改为凭证派生 caller。
2. 修正容量闸门：让活跃 Job 计数和 Job / Attempt / Dispatch outbox 创建处于同一锁窗口或等价事务保护。
3. 增加长执行 heartbeat：执行期间周期性延长 lease，并集中校验 job timeout、worker timeout 和 stale running 窗口关系。
4. 锁住 callback 幂等：补重复事件、非 2xx、`accepted=false`、lease 回收和 dead-letter 的定向测试。
5. 收口公开错误：公开查询和 callback 只暴露业务可理解错误摘要，内部 child details 保留给管理排障。
6. 下沉关键不变量：优先覆盖 `ai_call_ledger_entries(job_id, attempt_id)`、active submission key、root / child 形状和 retry chain 的一致性。

## Acceptance

- P0/P1 项完成后，`./scripts/verify.sh check` 通过。
- 涉及 Job 执行、Taskiq workflow、callback 或 recovery 时，额外运行 `./scripts/verify.sh workflow-smoke`。
- 新增 API 字段、错误语义或 callback 语义时，同步 `docs/api/service-contract.md` 和 contract tests。
- 新增数据库约束时，配套 Alembic migration 和最小回归测试。
- 公开响应、callback payload 和普通日志不泄露 child 内部拓扑、provider 原始错误、完整 callback URL query 或敏感 ack details。

## Non-goals

- 不把本服务改造成用户系统、项目管理系统或通用业务编排平台。
- 不新增通用配额/租户计费系统；容量闸门只服务 Job kernel 稳定性。
- 不在本计划中设计生产部署、K8s Secret 管理、CI/CD 或跨仓库运维。
- 不重开已经明确为 non-goal 的公开 generic billing scope route。
- 不修改 `scripts/k8s.sh check` 的当前排障合同；如需变更，必须先修改 AGENTS.md 和 runbook 边界。
