# 模板阶段剩余验收计划

> Archived: 本文是模板阶段历史验收计划，不再作为活动计划维护。当前模板就绪边界以 [`../../current/template-readiness.md`](../../current/template-readiness.md) 为准。

本文只记录进入正式业务模板阶段前仍需要人工确认或后续业务接入补齐的验收项。当前实现事实见 `docs/current/`，公开合同见 `docs/api/`。

## Current Baseline

- Job kernel、DAG-lite workflow、AI gateway、AI ledger 和 Job billing 已有当前事实文档。
- 模板 smoke 覆盖单 Job workflow 和六种 workflow mode。
- root workflow billing 已覆盖 descendant child AI 调用。
- 本仓库仍是 AI Job 微服务模板，不是生产部署方案或跨服务 workflow 平台。

## Remaining Gaps

- 正式业务 `job_type` 尚未接入；当前内置 `example_*` 和 `job_real_llm_*` 只作为模板验证、压测目标或真实 LLM 链路样例。
- 真实模型业务 e2e 需要等正式业务 schema、prompt、model、pricing 和对象存储产物确定后补齐。
- node / child 级 cost attribution、running result snapshot、child node 查询、取消语义和运维 UI 都不是模板阶段前置项。
- 生产部署、K8s、云平台 Secrets、CI/CD 发布流水线不属于本仓库当前边界。

## Planned Work

1. 模板复制前，按 [`../current/template-readiness.md`](../current/template-readiness.md) 完成身份替换、安全配置和 smoke。
2. 接入正式业务时，新增业务自己的 `job_type`、schema、executor、workflow definition、prompt refs、model/pricing 配置和业务 e2e。
3. 只有真实业务或调用方合同要求时，才升级 running result、node 查询、取消语义或细粒度 cost attribution。

## Acceptance

- `./scripts/verify.sh check` 通过。
- 修改 Job、Taskiq、Workflow、Recovery、Callback 或对象存储后，`workflow-smoke` 和 `workflow-modes-smoke` 通过。
- 正式业务接入后，业务 e2e 能证明真实输入、真实 child Job、对象存储产物、callback mock 和 root billing。
- current 文档只描述已实现事实；plans 只保留尚未实现或需业务触发的缺口。
