# FastAPI AI Job 模板全局心智模型

本文是一份轻量入口文档：帮助后来维护者快速理解本模板怎么开发、怎么扩展、哪些边界不能绕过。它不替代当前事实、API 合同或排障手册。

## 这是什么

本仓库是 FastAPI AI Job 服务模板，核心目标不是提供通用后端大而全能力，而是固定一套异步 AI Job 服务范式：

- HTTP 接口只负责提交、查询、Callback 和元信息读取。
- Job 是调用方可见的异步资源。
- Attempt、outbox、worker 和 recovery 负责执行可靠性。
- executor、workflow 和 tool 负责业务执行分层。
- registry 和 verify 脚本负责让代码、OpenAPI、文档和扩展点不漂移。

当前模板适合复制成一个具体 AI Job 微服务，不适合直接当作用户系统、项目管理系统、跨服务工作流平台或生产部署平台。模板就绪边界看 [`../current/template-readiness.md`](../current/template-readiness.md)。

## 一句话心智模型

对调用方来说，本服务只有一类核心资源：

```text
POST /jobs
  -> 返回 job_id
  -> GET /jobs/{job_id}
  -> 拿 status、job_result、billing 或 error
```

对服务内部来说，一个 Job 会经过固定骨架：

```text
OperationSpec
  -> HTTP route / OpenAPI / service contract
  -> JobTypeSpec
  -> Job / Attempt
  -> Taskiq worker
  -> executor 或 WorkflowDefinition
  -> Tool
  -> provider adapter / AI provider / object storage
  -> result / callback / billing
```

新增业务时优先把自己放进这条链路，而不是从 route、worker 或 provider 旁边另起一套流程。

## 核心地基

这里先帮你识别地基和下一步入口，不在本文维护完整字段、状态或接入清单。

| 地基 | 负责什么 | 继续阅读 |
|---|---|---|
| `OperationSpec` | HTTP operation 的代码级登记点 | [`../current/registry-governance.md`](../current/registry-governance.md)、[`../api/service-contract.md`](../api/service-contract.md) |
| `JobTypeSpec` | `job_type` 的代码级登记点 | [`../api/extension-guide.md`](../api/extension-guide.md#新增-job_type) |
| `WorkflowDefinition` | root/child workflow 的入口、版本、失败策略和节点上限 | [`../current/workflow-kernel.md`](../current/workflow-kernel.md) |
| `Job kernel` | Job、Attempt、dispatch outbox、callback outbox、recovery 和 lineage | [`../current/job-kernel.md`](../current/job-kernel.md) |
| `Tool` | 底层执行动作边界 | [`../api/extension-guide.md`](../api/extension-guide.md#新增-tool) |
| `AI gateway` | 模型调用入口、model / prompt / pricing registry 和 usage 记录 | [`../current/ai-capability.md`](../current/ai-capability.md) |
| `Billing` | 从 AI call ledger 聚合 Job billing read model，不是资金账本 | [`../current/ai-billing.md`](../current/ai-billing.md) |
| `scripts/verify.sh` | 模板一致性和最小验收入口 | [`../../scripts/README.md`](../../scripts/README.md) |

## 新业务怎么接

先判断业务形态，再选入口。

| 需求 | 优先入口 | 不要做什么 |
|---|---|---|
| 一个异步任务能在单个执行器内完成 | 新增 `job_type` + executor | 不要为了“显得可编排”拆 child Job |
| 一个 root Job 需要拆多个内部步骤 | 新增 root `job_type` + internal child job + `WorkflowDefinition` | 不开放任意 DAG 给外部调用方 |
| 新增公开 HTTP 能力 | 新增 `OperationSpec`，route decorator 消费 operation helper | 不在 route 上手写另一份 path / response / error metadata |
| 复用一段业务处理能力 | 业务包内 helper / adapter | 不把跨业务复合能力抽成全局模块 |
| 封装底层 I/O、解码、SDK 或本地函数 | 新增 tool | 不让 tool 依赖 `app/jobs` |
| 新增模型或 Prompt | 改 registry 配置或业务包内 prompt | 不在 route 或 executor 里临时拼 provider 参数 |
| 新增大文件结果 | 写对象存储，result 返回 artifact metadata 或 ref | 不把大 payload 塞进 `job_result` |

具体字段、注册方式和测试要求只看 [`../api/extension-guide.md`](../api/extension-guide.md)。

## 单任务和 workflow 的选择

本项目支持异步单任务，也支持受控 root/child workflow。这里不重复维护完整选择清单，只记住判断方向：

```text
一个业务步骤能自然收敛成一个结果
  -> 优先单 executor

多个步骤需要独立观测、重试、汇总或复用
  -> 考虑 root/child workflow
```

具体示例、伪代码和新增 Job 落点看 [`job/job-orchestration-examples.md`](job/job-orchestration-examples.md)。正式扩展步骤看 [`../api/extension-guide.md`](../api/extension-guide.md)。

## 开发前先问

新增正式业务前，先回答这几个问题，再落代码：

- 这是公开 root `job_type`，还是内部 child 能力？
- 调用方需要看到一个最终结果，还是需要 root 汇总多个 child？
- 大输入、大结果和文件产物是否需要对象存储引用？
- 是否需要 AI gateway、model / prompt / pricing registry 或 billing ledger？
- 改动会触碰 current 事实、api 合同、runbook 操作还是 plans 计划？

这些问题只用于选路径；具体接入清单以扩展指南和对应 current 文档为准。

## 维护底线

- 新能力先进入对应代码事实源，再进入 route、worker 或业务实现。
- route、executor、tool 保持单向依赖，不反向编排。
- 大输入、大结果和文件产物走对象存储引用，不走 response 大 payload。
- 配置和 provider 异常应快速暴露，不新增 silent fallback。
- 长期事实放 `docs/current/`，对外合同和扩展清单放 `docs/api/`，操作和心智模型放 `docs/runbooks/`，未来计划放 `docs/plans/`。
- 本文只做入口和判断，不作为字段、状态、错误码、schema 或 API 的独立事实源。

## 改完怎么验

通用代码和合同改动优先运行：

```bash
./scripts/verify.sh check
```

修改 Job 执行、Taskiq workflow、分块或 merge 后，优先运行：

```bash
./scripts/run.sh up dev
./scripts/verify.sh workflow-smoke
./scripts/run.sh down dev
```

只改说明性文档时，至少人工检查相对链接、章节职责和是否重复维护事实源。文档改变了稳定合同或当前事实时，应同步运行对应 registry、contract 或完整 check。

## 记住一条原则

```text
新增业务不是新增一条路；
新增业务是把自己的输入、执行、产物和副作用放进模板已有骨架。
```
