# Job Type 示例与压测合同标准化计划

本文记录把 Job type 示例、workflow 编排原语和 `scripts/load.sh` 压测目标解耦为一套代码可验证合同的计划。当前实现事实仍以 [`../current/job-kernel.md`](../current/job-kernel.md)、[`../current/workflow-kernel.md`](../current/workflow-kernel.md) 和 [`../current/job-load-testing.md`](../current/job-load-testing.md) 为准；本文只写尚未实施的目标、分层和验收。

## Current Baseline

- `job_type` 当前是字符串注册表 key；`JobExecutor` 通过 `visibility`、`role`、`execution_mode`、`retry_policy`、`allow_callback` 和 `result_snapshot_statuses` 暴露元信息。
- 当前 public 业务入口是 `poster_title_image`；它是 workflow root，并编排 internal child：`poster_title_image_style_probe`、`poster_title_image_generate_item` 和 `poster_title_image_join`。
- 当前 demo / 示例类型包括 `arithmetic`、`job_test_echo`、`job_test_add`、`job_test_collect`、`job_test_workflow`、`job_real_llm_echo` 和 `job_real_llm_double_echo`。
- 当前 workflow kernel 支持 `task`、`chain`、`group`、`chord`、`map`、`starmap` 和 `chunks`。`single` 只是现有 `job_test_workflow` 的示例模式，用一个 `task` 表达 one-child workflow，不是独立 kernel primitive。
- `scripts/load.sh` 已有 `case` / `profile` 分层：case 决定压哪条链路，profile 决定用哪个 `job_type` 和默认 `job_params`。
- 当前 `job-flow` / `job-submit` 默认压 `job_test_echo`，`workflow-flow` 默认压 `job_test_workflow`；`locustfile.py` 的 payload builder 只特殊支持这两个 job type。
- `load.sh` 通过 `--allow-real-job` 防止误压真实业务；但当前 demo 判断是 `job_type.startswith("job_test_")`，没有直接使用 registry 的 `visibility="demo"`。

## Problem

当前问题不是缺少几个 mock job，而是缺少一套由代码事实源、合同、示例实现和压测入口共同约束的标准化体系：

```text
代码事实源
  -> 系统真正注册了哪些 job_type、workflow primitive、load case/profile

合同
  -> 新业务 job 必须遵守哪些元数据、schema、workflow、callback 和压测规则

示例实现
  -> 用低副作用 demo job 展示合同如何落地

压测入口
  -> 用 case/profile 选择结构等价的低成本压测目标

业务接入
  -> 新业务按合同新增 job_type、workflow definition、profile 和测试
```

如果只新增 `example_*` job，而不固定这些分层，后续业务仍会从示例代码里倒推规范，继续形成“一次一个设计”的漂移。

## Target Model

目标是让代码成为事实源，文档只解释事实源和扩展规则：

| 层 | 责任 | 不负责 |
|---|---|---|
| 代码事实源 | 注册 job type、workflow primitive、load case/profile，并提供机器可读投影 | 靠文档手写列表维护能力 |
| 合同层 | 定义 job type 元数据、workflow primitive 语义、profile schema 和业务接入规则 | 绑定某个 demo job 名称 |
| 示例 job family | 作为合同的低副作用参考实现和默认压测目标 | 定义 workflow 原语本身 |
| load case/profile | 表达压测链路和压测对象，可替换 job_type + job_params 而不改 runner | 写入真实业务 payload 分支 |
| 业务接入参考 | 说明新业务如何选 role/visibility、复用 primitive、补测试和 profile | 要求业务继承 demo job 结构 |

核心约束：

- `task`、`chain`、`group`、`chord`、`map`、`starmap`、`chunks` 是 workflow primitive contract，独立于示例 job。
- `example_*` 只是这些合同的参考实现和低成本压测目标。
- `scripts/load.sh` 默认压测 `example_*`，但真实业务通过 profile 接入同一 runner，不新增业务专属 Locust 分支。
- 不兼容旧 `job_test_*` 默认结构；这是新项目，可以清理旧 demo 路径和旧 CLI 参数。
- `APP_ENV=local/dev` 允许外部提交 `visibility="demo"`；`APP_ENV=test/prd` 只允许外部提交 `visibility="public"`。
- 错误合同以稳定错误码 / reason 为准，不以错误消息字符串为准。

## Canonical Sources

实施后，各层唯一事实源应如下：

| 对象 | 代码事实源 | 测试事实源 | 文档位置 |
|---|---|---|---|
| Job type catalog | `app/jobs/types/register.py`、`app/jobs/registry.py`、executor metadata | registry contract tests、`jobs.sh types --json` projection tests | `docs/current/job-kernel.md`、`docs/api/extension-guide.md` |
| Job params/result/runtime schema | Pydantic schema 与 executor `params_schema` / result schema 声明 | schema / contract tests | `docs/api/extension-guide.md` 只写接入规则，不复制字段大表 |
| Workflow primitive catalog | `app/workflows/base.py`、compiler、workflow registry | primitive compiler tests、workflow orchestrator tests | `docs/current/workflow-kernel.md`、`docs/api/extension-guide.md` |
| Example job family | `app/jobs/types/example/` | example executor / workflow / registry tests | `docs/current/template-readiness.md`、`docs/current/job-load-testing.md` |
| Built-in load behavior | `scripts/load/cases.py`、`scripts/load/profiles.py` | `load.sh cases --json`、`profiles --json` tests | `docs/current/job-load-testing.md` |
| Load profile / manifest contract | `scripts/load/profiles.py`、dry-run manifest payload | profile schema tests、dry-run manifest tests | `docs/api/extension-guide.md`，如果内容膨胀再拆 `docs/api/load-profile-contract.md` |
| External submission gate | `app/services/jobs.py`、`JobTypeSpec.visibility`、`APP_ENV` rules | create contract tests | `docs/api/service-contract.md` |
| Business onboarding | business executor、workflow definition、profile JSON 和 tests | minimal business onboarding fixture 或 contract test | `docs/api/extension-guide.md` |

计划完成后，文档中的列表必须能从代码投影或测试断言中重建；不能只靠 Markdown 表格维持事实。

## Contracts To Standardize

### Job Type Contract

每个 `job_type` 必须声明并被测试锁定：

- `name`
- `visibility`
- `role`
- `execution_mode`
- `allow_callback`
- `params_schema`
- `runtime_fields_schema`
- `canonical_result_schema`
- `public_result_schema`
- `retry_policy`
- `timeout_seconds`
- `result_snapshot_statuses`
- `error_codes`

`role` 只表达目录意图；运行时 root / child 身份仍由 `root_job_id` 和 `workflow_node_key` 决定。

### Workflow Primitive Contract

workflow primitive 必须独立于 demo job 被测试：

| primitive | 合同语义 |
|---|---|
| `task` | 单个 child node |
| `chain` | 后一个 root node 依赖前一个 leaf node |
| `group` | 多个成员并行 ready |
| `chord` | header leaves 完成后再运行 body roots |
| `map` | items 展开为同一 job_type 的多个节点 |
| `starmap` | items 解包为多参数 job_params |
| `chunks` | items 按 chunk_size 分块为多个节点 |

`single` 如果继续保留，只能作为 `example_workflow.mode` 的别名，编译为一个 `task`，不写入 primitive catalog。

### Load Profile Contract

`load.sh` 应保证：

- 内置 profile 服务模板默认压测。
- JSON profile 可替换 `job_type`、`job_params` 和默认压测参数。
- 真实业务 `job_type` 不需要修改 `scripts/load/locustfile.py`。
- 非 demo job type 仍必须显式 `--allow-real-job`。
- manifest 记录 machine-readable 的 case、profile、job_type、params source、billable risk 和输出路径。

## Example Job Family

示例 job family 是合同参考实现，不是 workflow 标准本身。

| job_type | visibility | role | 边界 |
|---|---|---|---|
| `example_sleep` | `demo` | `root_or_leaf` | 普通执行节点；模拟耗时、结果大小和失败 |
| `example_pair` | `demo` | `root_or_leaf` | `starmap` 参考节点；验证多参数展开 |
| `example_collect` | `demo` | `leaf` | join / chunks 参考节点；验证汇总和分块输入 |
| `example_workflow` | `demo` | `root` | workflow root 参考实现；用 mode 编译 primitive 组合 |

所有 `example_*` executor 必须：

- `allow_callback=False`
- 不调用 LLM
- 不访问 OSS
- 不发起外部 HTTP
- 不写真实业务副作用
- 只使用本地 sleep、参数校验、JSON 结果构造和显式失败模拟

`example_workflow.mode` 可覆盖：

- `single`：示例别名，编译为一个 `task`
- `chain`
- `group`
- `chord`
- `map`
- `starmap`
- `chunks`

参数字段以 schema 为事实源。计划层只要求必须覆盖这些控制意图：

- 节点数量
- 执行耗时
- 结果大小
- 失败节点
- chunk size
- join 耗时
- failure policy

非法参数必须 fail-fast；不要静默修正、降级或填兜底默认。

`example_workflow` 的 public result schema 是 example-only。它可以返回 node_count、succeeded、failed 和节点摘要，方便压测和示例观察；但正式业务 workflow root 不继承这套结果面。业务 root 的 public result 仍由自己的 `public_result_schema` 和 API 合同定义。

## Planned Work

1. 建立代码事实源投影
   - 为 job type registry、workflow primitive catalog、load case/profile 增加或补强机器可读投影。
   - 测试这些投影，确保文档主表能从代码事实源重建。

2. 标准化 job type 合同测试
   - registry contract tests 锁定所有 `job_type` 的 metadata、schema、callback、retry、timeout、error code。
   - 外部提交准入测试覆盖 `APP_ENV=local/dev` 和 `APP_ENV=test/prd`。

3. 标准化 workflow primitive 合同测试
   - 独立测试 `task`、`chain`、`group`、`chord`、`map`、`starmap`、`chunks` 的编译语义。
   - 保留或新增 orchestrator tests，覆盖 child 创建、root finalize、child failure 和 repeated advance。
   - 如果 `single` 保留，只测试它是 `example_workflow` 的 mode alias。

4. 实现 `example_*` family
   - 新建或重组 `app/jobs/types/example/`。
   - 实现 `example_sleep`、`example_pair`、`example_collect`、`example_workflow`。
   - 删除旧 `job_test_*` 默认路径；新项目不为旧名称、旧 payload 或旧 CLI 参数提供兼容层。
   - `job_real_llm_*` 如果保留，只作为真实 LLM 计费链路样例，不进入默认 load profile。

5. 重构 `scripts/load.sh` 接入方式
   - `job-flow` / `job-submit` 默认使用 `example_sleep`。
   - `workflow-flow` 默认使用 `example_workflow`。
   - 内置 profile 改为 `example-sleep`、`example-workflow-chain`、`example-workflow-group`、`example-workflow-chord`、`example-workflow-map`、`example-workflow-starmap`、`example-workflow-chunks`。
   - `locustfile.py` 只知道 profile/job_params 合同；除标准 example payload helper 外，不增加业务 job 分支。
   - dry-run manifest 成为 profile/case 合同的测试面。

6. 补强业务接入参考
   - 更新 `docs/api/extension-guide.md`，说明新增业务 job 的最小步骤：schema、executor、registry、workflow definition、profile、contract tests、load dry-run。
   - 把稳定 load profile / manifest 合同写入 `docs/api/extension-guide.md` 或独立 `docs/api/load-profile-contract.md`；`docs/current/job-load-testing.md` 只写当前内置 case/profile 行为和使用方法。
   - 保持 `APP_ENV` 与 `visibility` 的外部提交准入真源在 `docs/api/service-contract.md`。
   - 明确业务 job 对接的是 job type / workflow / profile 合同，不是继承 `example_*`。
   - 增加一个最小业务 onboarding 验收 fixture 或测试，证明新增业务 job 不需要改 workflow kernel 或 load runner。

7. 更新当前事实文档
   - 实施完成后再更新 `docs/current/job-kernel.md`、`docs/current/workflow-kernel.md`、`docs/current/job-load-testing.md`、`docs/current/template-readiness.md`。
   - `docs/current/` 只写已落地事实；字段细节链接到 schema/contract，不复制大表。

## Implementation Order

1. 先做 catalog / projection 和测试，让代码事实源可查询。
2. 再锁 job type contract 与 workflow primitive contract。
3. 再实现 `example_*` family。
4. 再切换 `load.sh` case/profile/manifest。
5. 再新增业务 onboarding 测试。
6. 最后迁移 current/api 文档，并清理旧 `job_test_*` 默认引用。

## Acceptance

- `jobs.sh types --json` 或等价 registry projection 能机器可读地输出 `job_type` metadata，并被测试锁定。
- workflow primitive projection 或 compiler tests 独立覆盖 `task`、`chain`、`group`、`chord`、`map`、`starmap`、`chunks`。
- `load.sh cases --json`、`load.sh profiles --json` 和 dry-run manifest 被测试锁定，不依赖人读 CLI 文案。
- 所有 `example_*` 都是 `visibility="demo"`、`allow_callback=False`，并且 `APP_ENV=test/prd` 外部提交会被拒绝。
- `example_*` 测试证明不会调用 LLM、OSS、外部 HTTP、callback 或真实计费路径。
- compiler / orchestrator tests 证明 primitive、root finalize 和 failure policy 语义；`example_workflow` 只作为组合示例覆盖 `single`、`chain`、`group`、`chord`、`map`、`starmap`、`chunks`，其中 `single` 明确只是示例 mode alias。
- `starmap` 验收断言 `example_pair` 的多参数展开结果；`chunks` 验收断言 `example_collect` 收到的 chunk 内容和 chunk size。
- `example_workflow` 成功路径能完成 root finalize；它的 example-only result 可返回 node_count、succeeded、failed 和节点摘要，但该结果面不作为业务 workflow root 合同。
- `example_workflow` 失败路径能按 `failure_policy` 进入稳定终态，并返回稳定错误 reason；错误合同断言 reason，不断言消息字符串。
- 非法 `child_count`、`chunk_size` 或超出 `max_nodes` 时，`POST /jobs` fail-fast，且不创建 root Job。
- 真实业务 `poster_title_image` 或任意 `public` job 可通过 JSON profile 接入 `job-submit` / `job-flow`，不修改 `locustfile.py`。
- 最小业务 onboarding 测试证明：新增业务 job 只需注册代码、补 schema/contract tests、写 profile/文档，不需要改 workflow kernel 或 load runner。
- 文档同步清单明确完成：`docs/current/job-kernel.md`、`docs/current/workflow-kernel.md`、`docs/current/job-load-testing.md`、`docs/current/template-readiness.md`、`docs/api/service-contract.md`、`docs/api/extension-guide.md` 只写各自分层内的内容。
- `./scripts/verify.sh check` 通过；如果修改 workflow 或 load runner，另跑 `./scripts/verify.sh workflow-smoke` 或新增的 workflow modes smoke 命令并在结果中记录。

## Non-goals

- 不开放任意 DAG 提交。
- 不把 `poster_title_image` 改成 mock，也不绕过真实业务执行。
- 不把真实业务 payload 写进 `scripts/load/locustfile.py`。
- 不引入新的压测平台、结果数据库、多机压测调度或 Grafana 看板。
- 不在本计划中统一所有普通业务 Job 为 `root + one child`。
- 不兼容旧 `job_test_*` 默认结构、旧 load 参数或旧 profile 名称。
