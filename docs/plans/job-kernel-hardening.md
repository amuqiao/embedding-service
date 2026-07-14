# Job Kernel Hardening Plan

本文是当前唯一活动 Job kernel 硬化计划，负责把可靠性、一致性和公开信息边界中仍值得短期处理的风险收口成可执行事项。当前实现事实见 [`../current/job-kernel.md`](../current/job-kernel.md)，外部调用合同见 [`../api/service-contract.md`](../api/service-contract.md)。

## Current Baseline

- Job / Attempt / Dispatch outbox / Callback outbox 已分离。
- Attempt claim、执行期周期 heartbeat、终态写入已使用 active attempt 和 lease token 保护。
- `MAX_ACTIVE_JOBS` 容量判断和 public root Job / Attempt / Dispatch outbox 创建处于同一事务级 advisory lock 窗口；workflow child 创建也受同一全局容量门禁约束，root orchestration fan-out 时会从计数中排除当前 root 自身，容量延后会写入 `workflow.capacity_deferred` audit event。
- Callback duplicate `event_id` 的成功 ACK 语义已有合同测试锁住；`accepted=false` 仍表示拒收并触发 retry / dead-letter 路径。
- root / child lineage 已由同一 Job 表表达，root 是公开查询、callback 和 billing 入口。
- workflow root 公开错误已与 child 内部诊断分离，不向调用方暴露 child id、node key 或 provider 原始错误。
- 当前 API 合同明确只假设单可信上游，`X-AI-Service-Caller-ID` 不是多租户安全边界。
- 本仓库仍是 AI Job 服务模板，不承担用户系统、通用工作流平台或生产部署平台。

## Remaining Gaps

| 优先级 | Gap | 收口边界 |
|---|---|---|
| P0 | 多 caller 场景下，`caller_id` 不能只依赖共享服务密钥后的请求头 | 单可信上游已写入合同；如果进入多 caller，必须由凭证派生 caller |
| P2 | 关键跨表不变量仍有一部分依赖 repository 写路径 | 只补会影响 billing、attempt、root/child 一致性的约束或测试 |

## Planned Work

1. 多 caller 产品化前，把认证结果改为服务端凭证派生 `caller_id`，并废弃生产环境对调用方自报 caller header 的信任。
2. 下沉关键不变量：优先覆盖 `ai_call_ledger_entries(job_id, attempt_id)`、active submission key、root / child 形状和 retry chain 的一致性。
3. 继续补边界测试：高并发容量闸门、heartbeat 失效取消执行、callback lease 回收和 workflow public error callback payload。

## Acceptance

- 多 caller 产品化前，`caller_id` 不再由共享服务密钥后的请求头单独决定。
- 新增 API 字段、错误语义或 callback 语义时，同步 `docs/api/service-contract.md` 和 contract tests。
- 新增数据库约束时，配套 Alembic migration 和最小回归测试。
- 公开响应、callback payload 和普通日志不泄露 child 内部拓扑、provider 原始错误、完整 callback URL query 或敏感 ack details。

## Non-goals

- 不把本服务改造成用户系统、项目管理系统或通用业务编排平台。
- 不新增通用配额/租户计费系统；容量闸门只服务 Job kernel 稳定性。
- 不在本计划中设计生产部署、K8s Secret 管理、CI/CD 或跨仓库运维。
- 不重开已经明确为 non-goal 的公开 generic billing scope route。
- 不修改 `scripts/k8s.sh check` 的当前排障合同；如需变更，必须先修改 AGENTS.md 和 runbook 边界。
