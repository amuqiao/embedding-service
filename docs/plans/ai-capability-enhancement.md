# AI 能力增强计划

## Current Baseline

- 当前服务边界保持为单个 FastAPI AI Job 服务模板，Job 可靠执行内核已经由 `job_submission_keys`、`dispatch_outbox`、`job_execution_attempts`、`callback_outbox` 和 recovery 共同承担。
- `app/services/ai_gateway_facade.py` 是当前 AI 调用业务入口，已经负责模型可用校验、pricing ref 匹配、调用前写入 `ai_call_ledger_entries` pending 行、调用 provider、校验 usage、计算 cost，并写入 succeeded / failed 终态。
- `app/integrations/ai_gateway.py` 当前只是文本生成 adapter，返回 `TextGenerationResult` 和 token usage，不写 Job、Callback、Billing envelope 或数据库。
- `ai_call_ledger_entries` 是 AI provider call 的 usage / cost 事实源；`app/services/billing.py` 从 ledger 聚合 Job scope `BillingEnvelope`。它不是用户余额、扣费账户或财务总账。
- `app/core/model_registry.py` 和 `app/core/pricing_registry.py` 已经有模型目录、pricing ref 和 model/provider 匹配校验；当前 pricing 只支持文本 `per_token`。
- `app/core/prompt_templates.py` 和 `app/core/prompts.yaml` 提供 Prompt 模板入口；当前内置模板为空，Prompt 引用、step prompt 和 output schema 还没有统一 fail-fast 合同。

## Remaining Gaps

- Provider Gateway、Adapter、Model Gate 的内部边界还不清晰；`generate_text_with_ledger()` 当前同时承担模型解析、provider 调用、usage 标准化、成本计算和 ledger 终态写入编排。
- Usage 和 pricing 缺少 typed contract。当前只支持文本 token 计费，不能表达 image / audio / video 的用量单位、pricing kind、usage schema version 或 provider-specific raw usage。
- Prompt Registry 缺少启动期和 verify 期 fail-fast。Prompt-driven `job_type` 还不能声明稳定 `prompt_ref`、step prompt refs 或 `output_schema_ref` 并由 registry 统一校验。
- 多步 LLM Job 可能通过自定义 runtime fields 携带多个 prompt payload，绕过通用 prompt/template 校验面。
- Billing read model 已能表达 `estimated`、`incomplete`、`failed` 和 `not_billable`，但 ledger unknown / incomplete 的排障查询和 runbook 仍不足。
- 当前不具备真实图片、音频或视频 provider adapter；多模态 pricing 只能作为待接入能力的内部合同目标，不能写成当前外部能力。

## Planned Work

### Phase 1: Fail-fast internal contracts

- 定义内部 AI 能力合同词汇，但不新增公开 API：`ResolvedModel`、`ModelGateDecision`、`UsageRecord`、`PricingRule`、`PromptSpec`。
- 将 `UsageRecord` 设计为可表达 `text`、`image`、`audio`、`video` 的 typed usage；当前实现可以只落地 text normalizer，但类型和测试要能拒绝错误 shape。
- 将 `PricingRule` 设计为 discriminated pricing rule：`per_token`、`per_image`、`per_second`、`per_call`。当前生产配置仍可只启用 `per_token`。
- 将 Prompt Registry 纳入启动期和 `./scripts/verify.sh check` 校验。Prompt-driven `job_type` 必须能声明 prompt refs 和 output schema refs；缺失、拼错或 step 不匹配必须 fail-fast。

### Phase 2: Preserve facade, split internal responsibilities

- 保留 `generate_text_with_ledger()` 作为当前稳定 facade，避免影响现有 `job_type` 和测试。
- AI 能力增强必须遵循当前 FastAPI 分层骨架，不做目录迁移或提前升级到 `domain/`：
  - `app/api/` 只处理 HTTP route、认证依赖和 response data schema，不直接调用 provider，不直接写 ledger。
  - `app/services/` 编排 `ModelGate`、`ProviderGateway`、`UsageNormalizer`、`TypedPricingResolver` 和 `UsageLedgerWriter`。
  - `app/core/` 承载 model / pricing / prompt registry、配置解析和 fail-fast 检查。
  - `app/integrations/` 只做外部 provider adapter，不写数据库、不拼 Job / Callback / Billing envelope。
  - `app/repositories/` 只封装 ledger、Job 和 billing 查询，不承载 provider 调用或计费规则。
  - `app/schemas/` 只放公开请求/响应合同；provider raw usage 和内部 typed usage 不进入公开 schema，除非公开 API 合同已经升级。
- 在 facade 内部拆出清晰职责链：

```text
ModelGate
  -> ProviderGateway
  -> UsageNormalizer
  -> TypedPricingResolver
  -> UsageLedgerWriter
```

- 保持 `ai_call_ledger_entries` 为唯一 AI call usage / cost 事实源；如需 schema 变更，只补充最小判别字段，例如 `usage_kind`、`usage_schema_version`，不新增第二套 AI call ledger。
- 继续保持公开 billing 合同为 Job scope `GET /jobs/{job_id}/billing`；不把 usage / cost 塞进 Job result 或 callback 当前合同。

### Phase 3: Triggered multimodal catalog and pricing expansion

- 只有在真实接入 image / audio / video `job_type` 或 provider adapter 时，才补对应 adapter、usage normalizer、pricing fixtures、model catalog entries 和 workflow tests。
- 模型列表和计费只参考 `cms-ai-manga-backend-master/backend/cost_config.yaml` 的覆盖面，不复制其文件结构或 analytics 语义。目标是把模型目录和价格目录分别落到本项目现有边界：
  - `app/core/models.yaml` 继续作为可调用模型列表来源，新增模型必须声明稳定 `model_id`、`provider`、`provider_model`、`pricing_ref`、能力标签、输入/输出媒体类型、环境变量要求和生成参数约束。
  - `app/core/pricing.yaml` 继续作为价格事实配置来源，新增价格必须使用稳定 `pricing_ref`，并与 model catalog 做 fail-fast 匹配。
  - `GET /models` 仍只暴露调用方需要选择模型的公开元信息；默认不暴露 provider raw model、价格矩阵、内部 usage schema 或成本明细。
- Typed pricing resolver 需要覆盖目标项目已经证明会出现的计费形态：文本 token、文本/图片拆分 token、图片张数、音频/视频秒数、分辨率阶梯、是否有音频、是否有参考图/参考视频、按区域或 provider 前缀区分的价格。
- 多模态 model catalog 应优先按能力分组接入，而不是按 provider 文件照搬：text / multimodal text、image generation、audio / TTS、video generation。可参考的模型族包括 OpenAI image、Gemini image/TTS、Veo、Tongyi Wan、Kling、Vidu、Doubao Seedream / Seedance 和 BytePlus Dreamina。
- 多模态 pricing resolver 必须先有纯单元测试，再接入 provider workflow；价格缺失、条件价格为 `null`、usage 缺少必要维度或 provider raw usage 无法标准化时必须 fail-fast 或写入明确 incomplete / failed ledger，不允许回退为零成本成功。
- 只有当通用 Job 合同无法承载能力差异时，才考虑 capability-specific facade；内部仍复用同一 Job kernel、model catalog、pricing catalog、AI call ledger 和 billing read model。

## Acceptance

- 非法 `model_id`、`pricing_ref`、prompt ref、step prompt ref、output schema ref 在应用启动、worker 启动或 `./scripts/verify.sh check` 中直接失败。
- 现有文本 AI 调用路径保持不变：调用 provider 前必须先写 `ai_call_ledger_entries.status=pending`；成功后写入 usage、cost、currency、pricing ref 和 terminal status。
- 失败路径保持不变：provider 失败、usage 缺失、pricing 计算失败或 terminal ledger update 失败时，不能重放 provider 调用修账，billing 只能显式表现为 `incomplete` 或 `failed`，不能伪造零成本成功。
- `tests/test_ai_gateway_facade.py` 覆盖 facade 正常路径、provider 失败、usage 缺失、pricing 失败和 terminal ledger update 失败。
- `tests/test_pricing_registry.py` 覆盖 typed pricing rule，包括错误 usage shape、错误 pricing kind 和缺失必要单位的 fail-fast。
- `tests/test_model_registry.py` 覆盖 model gate 与 pricing ref 匹配。
- 多模态 model catalog 测试覆盖新增模型的能力标签、媒体类型、provider model、`pricing_ref` 和必需环境变量；缺失或不匹配必须 fail-fast。
- 多模态 pricing 测试覆盖 `per_token`、`per_image`、`per_second`、`per_call`，以及按文本/图片 token、分辨率、音频、参考素材和区域/provider 前缀区分价格的规则。
- Prompt registry 增加测试，覆盖缺失 prompt ref、缺失 output schema ref、多 step prompt 配置不一致和正常渲染路径。
- `tests/test_billing_service.py` 继续证明 billing read model 只从 ledger 聚合，并正确处理 `estimated`、`incomplete`、`failed` 和 `not_billable`。
- 分层 drift check 通过：API 不直接调用 provider 或写 ledger，integrations 不写数据库，repositories 不承载业务规则，schemas 不暴露内部 provider raw usage。
- `./scripts/verify.sh check` 成为关闭本计划阶段性工作的最小验证入口；涉及真实 Job workflow 时，再运行 `./scripts/verify.sh workflow-smoke`。

## Non-goals

- 不重构当前 Job kernel，不为概念纯化拆掉 `JobAttempt`、dispatch outbox、callback outbox 或 recovery。
- 不为对齐分层骨架而做目录迁移、模块大搬家或提前新增 `app/domain/`；只有当 service 层规则明显复用、状态机/计费/准入规则开始复杂化时再评估 5 层。
- 不新增 wallet / balance / credit ledger；若未来接入真实资金扣费，必须另建资金账本边界，不能复用 AI call cost estimate。
- 不引入 Saga / process manager；当前问题仍是单服务 AI 调用、usage 和计费边界，不是跨系统补偿编排。
- 不提前建设 bulkhead、provider quota 平台、复杂 rate limit 分层或 adapter marketplace；这些只在真实饱和、滥用、多 provider 配额冲突或多团队平台复用出现后再评估。
- 不新增公开 `GET /billing/scopes/{scope_type}/{scope_id}`；内部 `get_scope_billing()` 先作为复用边界保留。
- 不新增 capability-specific route；除非外部合同已经证明统一 `/jobs + job_type` 无法表达能力差异。
