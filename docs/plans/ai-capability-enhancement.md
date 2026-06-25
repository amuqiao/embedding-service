# AI Capability Kernel 设计

本文定义本项目 AI 能力层的目标骨架。它是计划文档，不覆盖当前已经实现的事实；当前事实仍以 `docs/current/architecture.md` 和代码为准。

AI Capability Kernel 负责把业务 Job 的 AI 请求稳定转成 provider 调用、usage 标准化和 ledger 写入意图。成本估算的规则、ledger 事实源和非扣费边界由 [`ai-capability-cost-boundary-design.md`](ai-capability-cost-boundary-design.md) 负责。

## 设计目标

AI 能力层需要稳定回答三个问题：

```text
这个 job_type / step 是否允许调用这个模型。
应该如何构造 provider 请求并调用 provider。
provider 返回的 usage 如何标准化并可靠交给成本边界。
```

AI 能力层不负责：

- 多节点 workflow 编排。
- root Job 状态机和 callback outbox。
- 用户扣费、余额、钱包、发票、退款或财务总账。
- 直接向调用方暴露 provider raw usage、价格矩阵或内部成本明细。
- 为每个业务 job_type 复制一套 provider 调用、usage 解析和成本计算逻辑。

## Current Baseline

- 当前稳定 AI 调用入口是 `app/services/ai_gateway_facade.py` 的 `generate_text_with_ledger()`。
- `app/services/ai_capability_kernel.py` 承载 `ModelGate`、`ProviderGateway`、`UsageNormalizer`、`TypedPricingResolver` 和 `UsageLedgerWriter`；`app/services/ai_gateway_facade.py` 保留文本调用 facade。
- 当前稳定执行链仍是 `generate_text_with_ledger()` + `TextModel`。多模态 `UsageRecord` / `PricingRule` 是扩展抽象基础，不代表图片、音频或视频 provider path 已经交付。
- `app/integrations/ai_gateway.py` 当前是文本生成 adapter，返回 `TextGenerationResult` 和 token usage，不写 Job、Callback、Billing envelope 或数据库。
- `app/core/model_registry.py` 从 `app/core/models.yaml` 加载模型目录，并校验 enabled、能力标签、输入/输出媒体类型、required env 声明、provider model 派生、generation 参数和 `pricing_ref`。缺少 required env 的模型不会出现在可用模型列表中。
- `app/core/pricing_registry.py` 从 `app/core/pricing.yaml` 加载价格规则，当前配置只启用文本 `per_token`，代码已有 `per_image`、`per_second`、`per_call` 的基础类型。
- `app/core/usage_records.py` 已经提供 `TextUsageRecord`、`ImageUsageRecord`、`AudioUsageRecord` 和 `VideoUsageRecord` 的 typed usage 入口。
- `ai_call_ledger_entries` 是 AI provider call 的 usage / cost estimate 事实源；`app/services/billing.py` 从 ledger 聚合 Job scope billing read model。
- `app/core/prompt_templates.py` 和 `app/core/prompts.yaml` 提供 Prompt 模板入口；Prompt-driven `job_type`、step prompt 和 output schema 还没有统一 fail-fast 合同。

## Remaining Gaps

- 多模态 path 仍未交付；当前 kernel 组件和 provider adapter 仍主要服务文本生成路径。
- Model catalog 已有能力标签、输入/输出媒体类型和 generation 参数校验，但多模态模型条目、provider adapter 归属和真实多模态 pricing fixtures 还未接入。
- Prompt Registry 已纳入 registry consistency 校验；后续仍需要在正式业务 `job_type` 接入时补足对应 prompt refs、step prompt refs 和 output schema refs。
- Usage normalizer 已有 typed record 基础，但 provider raw usage 到 `usage_units` 的转换规则仍主要覆盖文本。
- 成本估算边界已经单独收敛到 `ai-capability-cost-boundary-design.md`，本文件需要避免重复定义 pricing 深节和 billing 状态矩阵。

## 成熟模式

AI Capability Kernel 采用以下成熟模式组合：

| 模式 | 用途 |
|---|---|
| Facade | 业务 Job 只调用稳定 AI facade，不直接触碰 provider adapter、pricing 或 ledger repo |
| Ports and Adapters | Provider adapter 只负责外部 provider API 适配，不承载 Job、billing 或 workflow 规则 |
| Anti-Corruption Layer | Provider raw response / raw usage 先转成内部 `UsageRecord`，再进入成本边界 |
| Catalog / Registry | `models.yaml`、`prompts.yaml` 和 `pricing.yaml` 作为 reference config，并在启动或 verify 期 fail-fast |
| Typed Rule / Strategy | 不同媒体类型、operation 和 usage kind 使用明确类型分派，不在业务代码里散落 provider-specific 分支 |
| Per-call Fact Ledger | provider call 发生前先写 pending ledger，terminal update 后由成本边界聚合 |
| Read Model Projection | 对外成本展示只读取派生 projection，不让 Job result 或 callback 成为成本事实源 |

这套组合的目的不是建设通用 AI 平台，而是在单个 FastAPI AI Job 服务模板内稳定 AI 调用骨架，让新 `job_type` 按同一条路径接入。

## 核心骨架

目标调用链：

```text
Job executor / business service
  -> AI Facade
    -> ModelGate
    -> PromptBuilder / RequestBuilder
    -> ProviderGateway
    -> UsageNormalizer
    -> TypedPricingResolver
    -> UsageLedgerWriter
  -> provider result
```

职责说明：

| 组件 | 职责 | 不应承担 |
|---|---|---|
| AI Facade | 对业务 Job 暴露稳定调用入口，编排下游组件和失败处理 | 不写业务 result projection，不决定 workflow 状态 |
| ModelGate | 校验 `model_id`、enabled、环境变量、provider model、capability 和 `pricing_ref` | 不调用 provider，不计算 cost |
| PromptBuilder / RequestBuilder | 根据 prompt refs、业务输入和模型能力构造 provider 请求 | 不读取 provider raw usage，不写 ledger |
| ProviderGateway | 调用 provider adapter，返回 provider result | 不写数据库，不拼 Job / Callback / Billing envelope |
| UsageNormalizer | 将 provider raw usage 标准化为 `UsageRecord` / `usage_units` | 不做价格计算，不从自然语言 prompt 推断计费单位 |
| TypedPricingResolver | 根据 `PricingRule + UsageRecord + PricingContext` 形成 cost estimate | 不管理用户余额，不修改 Job 状态 |
| UsageLedgerWriter | 写入和收敛 `ai_call_ledger_entries` | 不作为资金账本，不生成业务产物 |

AI 能力层和成本边界是解耦的，但 provider 调用事实必须通过标准交接点衔接：

```text
UsageRecord
  -> TypedPricingResolver
  -> UsageLedgerWriter
  -> ai_call_ledger_entries
```

## 分层边界

| 层 | AI Capability Kernel 职责 |
|---|---|
| `app/api/` | 只处理 HTTP route、认证依赖和 response data schema |
| `app/services/` | 编排 AI facade、model gate、request builder、provider gateway、usage normalizer、pricing resolver 和 ledger writer |
| `app/core/` | 管理 model / prompt / pricing registry、配置解析、typed records 和 fail-fast 校验 |
| `app/integrations/` | 调用外部 provider，返回 raw response / raw usage |
| `app/repositories/` | 封装 ledger、Job 和 billing 查询 |
| `app/schemas/` | 公开请求/响应合同，不放 provider raw usage 或内部 typed usage |

禁止的依赖方向：

- API route 直接调用 provider adapter。
- Provider adapter 直接写数据库。
- Business job 直接调用 `pricing_registry` 计算 cost。
- Workflow node 直接解析 provider raw usage。
- Repository 承载 provider 调用、prompt 渲染或 pricing 规则。
- Public schema 暴露 provider raw usage、内部 `pricing_ref` 或价格矩阵。

## 配置和数据所有权

当前阶段继续使用 YAML 管 reference config，使用 DB 管运行时事实。简化理解是：YAML 定义“允许什么、如何解释”，DB 记录“这次实际发生了什么”。

Phase 1 的数据库/表边界如下：

- 当前阶段不新增 AI 能力核心表；provider call、usage、cost estimate 和 failure diagnostic 继续只落在 `ai_call_ledger_entries`。
- `ai_call_ledger_entries` 是唯一 AI call usage / cost estimate 事实源；Job result、callback payload、workflow summary 和 future summary table 都只能作为派生投影。
- 当前阶段不新增 `job_cost_summary`。只有查询性能或 root projection 稳定性需要时，才评估把它作为可由 `ai_call_ledger_entries` 重建的 read model。
- workflow / child Job 归因落地前，才评估给 `ai_call_ledger_entries` 增加 `root_job_id`、`workflow_id`、`workflow_node_id`、`child_job_id` 等列，或新增等价 attribution 辅助表。
- `usage_kind`、`usage_schema_version` 当前不是已存在持久化列；如果后续需要按它们查询、过滤或建立索引，必须通过 Alembic migration 明确增加。

| 对象 | 当前推荐存储 | 说明 |
|---|---|---|
| Model catalog | `app/core/models.yaml` | 可调用模型列表、provider 映射、能力标签、环境变量要求和生成参数约束 |
| Pricing catalog | `app/core/pricing.yaml` | 价格事实配置和 `pricing_ref`，详细规则见成本边界文档 |
| Prompt catalog | `app/core/prompts.yaml` | prompt refs、step prompts 和 output schema refs |
| Provider adapter capability | 代码 + catalog 字段 | adapter 是实现能力，catalog 负责声明可选择能力 |
| AI call facts | DB `ai_call_ledger_entries` | provider call、usage、cost estimate 和 failure diagnostic |
| Job / attempt / outbox | DB | Job kernel 的可靠执行事实 |
| Cost summary | 当前不建表 | 未来只有查询性能或 root projection 需要时才新增可重建派生 read model |

暂不把 model / pricing / prompt catalog 搬进数据库。只有出现以下条件时再评估 catalog DB 化：

- 需要运营后台运行时上下架模型。
- 需要租户级模型可见性、租户级 provider key 或租户级价格。
- 价格变更需要审批流、审计日志和回滚。
- 多服务共享同一个模型/价格中心。
- 不允许重新部署也必须修改模型、prompt 或价格配置。

在这些条件出现前，YAML 更适合作为模板仓库的 reference config：可 review、可版本化、可在启动期 fail-fast，不引入额外运维面。

## 新能力接入流程

新增一个 AI 能力或 provider adapter 时，必须按同一条路径接入：

1. 在 model catalog 中声明 `model_id`、`provider`、`provider_model`、capabilities、input / output media types、`pricing_ref` 和 required env。
2. 如能力依赖 prompt，声明 `prompt_ref`、step prompt refs 和 output schema refs，并纳入 fail-fast 校验。
3. 在 provider adapter 中实现外部调用，只返回 provider result 和 raw usage。
4. 在 usage normalizer 中把 raw usage 转成 typed `UsageRecord` 和 `usage_units`。
5. 在 pricing catalog 中声明对应 `pricing_ref`，具体 pricing 规则遵循成本边界文档。
6. 通过 AI facade 在 provider 调用前创建 pending ledger。
7. provider 成功后把标准化 usage 交给 `TypedPricingResolver` 和 `UsageLedgerWriter`。
8. provider 失败、usage 缺失、pricing 失败或 ledger terminal update 失败时，显式暴露失败或 incomplete，不重放 provider 调用修账。
9. 增加 model registry、prompt registry、usage normalizer、AI facade failure path 和成本边界交接测试。

业务 `job_type` 只能选择能力、传入稳定参数并消费 provider result；不能绕过上述流程。

## Failure Modes

| 失败模式 | 期望行为 |
|---|---|
| `model_id` 不存在或未启用 | fail-fast，返回模型不可用 |
| enabled model 缺少 required env | 模型不出现在可用列表，verify / registry 校验应暴露问题 |
| `pricing_ref` 不存在或与模型不匹配 | 启动、worker 启动或 verify fail-fast |
| prompt ref 或 output schema ref 缺失 | 启动或 verify fail-fast |
| provider 调用超时或失败 | ledger terminal 标记 provider failure，向业务层抛出明确错误 |
| provider 已调用但 usage 缺失 | ledger terminal 标记 usage failure，billing 表达 failed / incomplete |
| usage shape 与 pricing type 不匹配 | pricing failure，不允许回退为 0 成本成功 |
| ledger terminal update 失败 | 不重放 provider 调用；依赖 recovery / reconciler 暴露 incomplete |
| workflow child Job 调用 AI | 仍走同一 AI facade，并带 root / child attribution |

## Planned Work

### Phase 1: Kernel contract 收口

- 将 AI Capability Kernel 的长期边界稳定在本文。
- 保留 `generate_text_with_ledger()` 作为现有文本路径 facade，避免影响当前 `job_type`。
- 明确业务 Job 只能通过 AI facade 调用 provider。
- 明确成本估算细节归属 `ai-capability-cost-boundary-design.md`，本文只保留交接点。
- 明确 Phase 1 不新增 AI 能力核心表，不新增 `job_cost_summary`，不要求修改当前 `ai_call_ledger_entries` 表结构。
- 明确 `ai_call_ledger_entries` 是唯一 AI call usage / cost estimate 事实源。
- 明确 workflow / child Job 归因落地前，才评估 `root_job_id`、`workflow_id`、`workflow_node_id`、`child_job_id` 加列或等价 attribution 辅助表。
- 明确 `usage_kind`、`usage_schema_version` 如需持久化查询/索引，必须通过 migration 增加，不能写成当前已存在事实。

### Phase 2: Registry fail-fast

- 扩展 model catalog 校验，支持能力标签、输入/输出媒体类型、required env 声明和生成参数约束。
- 保持 Prompt Registry 在启动期、worker 启动和 `./scripts/verify.sh check` 中校验。
- Prompt-driven `job_type` 必须声明 prompt refs、step prompt refs 和 output schema refs。
- 缺失、拼错或 step 不匹配必须 fail-fast。

### Phase 3: Typed usage path

- 稳定 `UsageRecord`、`usage_units`、`usage_detail` 和 `usage_schema_version` 的内部合同。
- `usage_schema_version` 和未来 `usage_kind` 如需持久化，必须通过明确 migration 增加；当前不能把它们写成已经存在的 DB 事实。
- 保持文本 normalizer 的当前行为。
- 为 image / audio / video normalizer 先补单元测试，再接真实 provider。
- 禁止 billing service 临时解析 provider raw usage。

### Phase 4: 首个多模态消费者接入

- 首个图片生成类业务只能作为 AI Capability Kernel 的消费者接入，不在业务代码里复制 provider、usage 或 pricing 流程。
- 只新增该能力需要的 model catalog、provider adapter、usage normalizer 和 pricing rule。
- workflow node / child Job 只传递 root、node 和 child attribution，不计算 cost。

### Phase 5: 多模态扩展

- 只有在真实接入 image / audio / video `job_type` 或 provider adapter 时，才补对应 adapter、usage normalizer、pricing fixtures、model catalog entries 和 workflow tests。
- 多模态 model catalog 按能力分组接入，而不是按 provider 文件照搬。
- 参考旧项目的模型覆盖面和计费维度，但不复制旧文件结构、analytics 语义或 fallback 行为。
- 只有当统一 `/jobs + job_type` 无法承载能力差异时，才评估 capability-specific route。

## Acceptance

Phase 1 验收：

- `ai-capability-enhancement.md` 只定义 AI Capability Kernel，成本估算和非扣费边界由 `ai-capability-cost-boundary-design.md` 承担。
- 数据库/表边界已经说明清楚：不新增 AI 能力核心表，不新增 `job_cost_summary`，不要求修改当前 `ai_call_ledger_entries` 表结构。
- `ai_call_ledger_entries` 被明确为唯一 AI call usage / cost estimate 事实源；Job result、callback payload、workflow summary 和 future summary table 都不是成本事实源。
- `usage_kind`、`usage_schema_version` 没有被写成当前已存在 DB 事实；如需持久化查询/索引，验收条件必须包含 migration。
- 现有文本 AI 调用路径保持不变：provider 调用前写 pending ledger，成功后写入 usage、cost、currency、pricing ref 和 terminal status。
- 失败路径保持不变：provider 失败、usage 缺失、pricing 失败或 terminal ledger update 失败时，不能重放 provider 调用修账，billing 只能显式表现为 `incomplete` 或 `failed`，不能伪造零成本成功。
- 成本边界测试继续由 `ai-capability-cost-boundary-design.md` 约束；本文只要求 AI facade 交接点不被绕过。
- 分层 drift check 通过：API 不直接调用 provider 或写 ledger，integrations 不写数据库，repositories 不承载业务规则，schemas 不暴露内部 provider raw usage。

后续阶段验收：

- Phase 2 后，非法 `model_id`、`pricing_ref`、prompt ref、step prompt ref、output schema ref 在应用启动、worker 启动或 `./scripts/verify.sh check` 中直接失败。
- Phase 2 后，`GET /models` 公开 `capabilities`、`input_media_types` 和 `output_media_types`，但不公开 `pricing_ref`、价格矩阵或 provider raw usage schema。
- Phase 2 后，`tests/test_model_registry.py` 覆盖 model gate、capability、media types、required env 和 pricing ref 匹配。
- Phase 3 后，`tests/test_ai_gateway_facade.py` 覆盖 facade 正常路径、provider 失败、usage 缺失、pricing 失败和 terminal ledger update 失败。
- Phase 4 前，workflow / child Job 归因必须完成设计选择：给 `ai_call_ledger_entries` 加 attribution 字段，或使用等价 attribution 辅助表 / scope 规则。
- Phase 4 后，业务 Job、workflow node 和 provider adapter 都不能绕过 AI facade 直接写 cost 或解析 provider raw usage。
- `./scripts/verify.sh check` 成为关闭本计划阶段性工作的最小验证入口；涉及真实 Job workflow 时，再运行 `./scripts/verify.sh workflow-smoke`。

## Non-goals

- 不重构当前 Job kernel。
- 不为概念纯化拆掉 `JobAttempt`、dispatch outbox、callback outbox 或 recovery。
- 不提前新增 `app/domain/` 或进行目录大迁移。
- 不新增 wallet / balance / credit ledger。
- 不把 `ai_call_ledger_entries` 复用为资金账本。
- 不引入独立工作流引擎、事件总线、CDC 或 provider marketplace。
- 不把 model / pricing / prompt catalog 过早搬进数据库。
- 不新增公开 `GET /billing/scopes/{scope_type}/{scope_id}`。
- 不新增 capability-specific route，除非外部合同已经证明统一 `/jobs + job_type` 无法表达能力差异。
