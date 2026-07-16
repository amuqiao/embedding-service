# cms-novel-localize Job 体系接入计划

本文记录旧项目 `cms-novel-localize` 接入当前 Taskiq + Job 体系的计划。本文不是当前实现事实；当前 Job、workflow、registry 和 API 合同事实以 `docs/current/` 与 `docs/api/` 为准。

## Current Baseline

- 当前项目使用 FastAPI、Taskiq、PostgreSQL、Redis 和对象存储承载异步 Job；公开 Job 创建、状态查询、callback、产物和 billing 由当前 Job 体系负责。
- 当前项目已有 `job_type` registry、AI capability registry、tools registry、payload adapter、AI gateway facade 和 workflow kernel；`@register_job_type` 只作为源码准入标记，不在 import executor 时写入全局 registry。
- 当前 workflow kernel 的稳定边界是 root Job 作为公开查询、callback 和 billing 入口，internal child Job 作为内部执行资源；child Job 不应默认暴露给旧业务调用方。
- 当前项目已有 Job 级总开销投影：AI 调用写入 root Job billing scope 的 `ai_call_ledger_entries`，billing service 聚合后可投影为 `job.cost`，完整费用状态与诊断仍由当前 `/api/v1/ai-jobs/jobs/{job_id}/billing` 承载。
- 当前项目已新增 `app/jobs/payload_adapters/legacy_oss_object_ref.py`，用于旧 `source.oss` 与当前 `CanonicalObjectRef` 的纯投影，以及当前写出对象结果到旧 `storage=oss_object` artifact manifest 的纯投影；它不读写 OSS。
- 旧项目对外前缀是 `/api/v1/novel-localization-ai`，主要合同包括模型与模板查询、`POST /jobs` 创建 Job、`GET /jobs/{job_id}` 查询 Job。
- 旧项目创建 Job 的请求合同包括 `client_request_id`、`job_type`、`model_id`、`source.oss`、`callback` 和 `prompt.blocks`，并使用严格 schema 拒绝未知字段。
- 旧项目公开的 `job_type` 至少包括 `novel_localization.step1_localize`、`novel_localization.step2_review`、`novel_localization.step3_translate`。
- 旧项目生产执行路径是 Celery task `jobs.process` 中的单次直接执行：读取输入与 prompt，调用模型，解析模型输出，必要时持久化大产物，更新 Job 状态并投递 callback。
- 旧项目存在 chunk planner 和 Celery chain/group/chord 风格的 workflow 草稿，但旧代码中生产入口明确未启用该 workflow；迁移时不能把这部分直接当成当前生产能力照搬。
- 旧项目结果语义需要保留：`localized_text` 和 `translated_text` 是大文本 artifact，`work_note`、`review_summary` 和 `signals` 是结构化结果字段；`step2_review` 的“不通过”应表现为业务成功且 `signals.passed=false`，不是 Job 执行失败。

## Remaining Gaps

- 当前项目还没有兼容旧前缀和旧响应形状的 route facade。
- 当前项目还没有旧 `CreateJobRequest`、`CreateJobResponse`、`JobStatusResponse`、meta response、callback body 和 callback headers 的兼容 schema。
- 当前项目还没有面向 `cms-novel-localize` 的 input/output 装饰器化 adapter 边界，也没有对应的纯投影 adapter 实现。
- 当前项目还没有旧 callback delivery adapter；旧项目 callback header、签名和成功判定与当前通用 callback 合同不同，不能只靠 output adapter 解决。
- 当前项目还没有注册旧 `job_type` 值，也没有对应 executor、prompt 资源、parser、错误码、artifact 映射和 contract tests；迁移落点应是 1 个 `app/jobs/types/novel_localization/` 业务包承载 3 个 public `job_type`，而不是 1 个泛化 `novel_localization` job_type 再由 adapter 或 `job_params.step` 分发。
- 当前公开 Job 合同与旧项目合同不同；需要明确旧前缀是否绕过当前统一响应 envelope，以及旧状态字段、错误字段、`status_url`、时间字段和 artifact 字段如何投影。
- 当前项目的 billing 查询是独立 root-scope 合同；旧 `GET /jobs/{job_id}` 需要通过 output adapter 返回兼容的 `cost` 总开销字段，但不应泄露当前 billing envelope、`usage`、ledger、provider、pricing ref 或诊断明细。
- 旧 `source.oss` 与旧 `storage=oss_object` artifact manifest 的底层 payload 片段投影已由 `legacy_oss_object_ref.py` 承接；剩余工作是让业务 input/output adapter 调用它，而不是重新实现 OSS payload 转换。
- 旧项目使用 Celery，当前项目使用 Taskiq；迁移目标是重写业务执行链，不保留 Celery task、Celery canvas、Celery result backend 或旧 Job 状态机实现。
- 复杂任务的 root / child 拆分边界需要重新设计。旧 chunk workflow 不能直接复制，只有在当前 workflow kernel 能冻结静态计划、明确 root 聚合和 child 幂等语义后才进入实现。
- 需要判断是否新增 tool 或 capability。初始判断是：业务 prompt、解析、小说文本切分、merge 和审核语义属于该 `job_type` 的业务逻辑，不应先抽成公共 tool；只有稳定、可复用、无 Job 状态副作用的底层能力才考虑注册 tool 或 capability。

## 装饰器化 Adapter 接入标准

本次迁移的 adapter 标准以“调用方无感知、内部符合当前 Job 体系”为边界。旧兼容能力允许用 input/output 装饰器承接 route facade 的输入输出适配，但转换规则必须保留为显式、可测试的纯 adapter 函数或类方法。

装饰器是 adapter 的入口壳，不是新的业务执行框架。它只负责在旧前缀 route facade 的关键位置调用 adapter、统一错误投影和绑定 legacy contract 元数据；它不拥有 Job 状态机、不执行模型调用、不计算费用、不直接读写大文本内容、不投递 Taskiq、不调度 workflow。

本文中的 legacy `cost` 是本次迁移明确要求的兼容输出字段；它不改变旧业务核心输入合同，也不改变当前项目 billing 事实源。

推荐调用关系：

```text
legacy route facade
  |
  | @legacy_input_adapter(...)
  v
当前 Job 创建服务
  |
  v
Taskiq / worker / novel_localization executors
  |
  v
当前 Job 状态、结果、artifact、billing
  |
  | @legacy_output_adapter(...)
  v
旧 CreateJobResponse / JobStatusResponse / callback body
```

装饰器边界必须满足：

- 装饰器可以包裹旧 route handler 或旧 facade 方法，但 handler 内部仍调用当前 Job service，不直接处理旧字段转换。
- input 装饰器只调用 `LegacyCreateJobInputAdapter`，把旧 `CreateJobRequest` 投影为当前 Job 创建命令。
- output 装饰器只调用 `LegacyOutputAdapter`，把当前 Job 记录、结果、错误和总开销投影为旧响应。
- 装饰器不在 import 时连接 DB、读取 OSS、投递任务、注册全局可变运行时状态或读取外部服务；如需 registry 元数据，应由当前 composition root 显式接入。
- 装饰器不吞异常。adapter 失败必须产生可投影回旧合同的明确错误，不能 silent fallback。
- executor、worker、Taskiq 和 billing service 不感知旧合同字段。

### Adapter 清单

| 类型 | 数量 | 建议落点 | 职责 |
|---|---:|---|---|
| OSS payload adapter | 1 | `app/jobs/payload_adapters/legacy_oss_object_ref.py` | 已新增。处理旧 `source.oss` 与旧 `storage=oss_object` artifact manifest 的 payload 片段转换；不替代顶层 input/output adapter |
| input decorator | 1 | `app/jobs/types/novel_localization/adapter_decorators.py` | 作为旧创建入口的薄壳，调用 input adapter 后把当前 Job 创建命令交给 route facade / Job service |
| output decorator | 1 | `app/jobs/types/novel_localization/adapter_decorators.py` | 作为旧查询、创建响应、meta 和 callback body 的薄壳，调用 output adapter 后返回旧响应形状 |
| input adapter | 1 | `app/jobs/types/novel_localization/input_adapter.py` | 纯投影。接收旧 `CreateJobRequest`，输出当前 Job 创建所需的规范化 `job_type`、`job_params`、runtime metadata、callback 配置和 legacy 投影标记 |
| output adapter | 1 | `app/jobs/types/novel_localization/output_adapter.py` | 纯投影。集中投影旧创建响应、旧状态响应、旧 callback body、旧 meta response、旧错误响应和 legacy `cost` 字段 |
| callback delivery adapter | 1 | `app/jobs/types/novel_localization/callback_delivery_adapter.py` | 处理旧 callback 传输协议、签名 header、成功判定和重试记录映射 |

不新增独立 billing adapter。legacy `cost` 是 output adapter 对当前 Job 级总开销投影的包装，不是新的计费事实源。

### Input Adapter 标准

`@legacy_input_adapter(...)` 是旧创建入口的唯一输入装饰器。它必须调用 `LegacyCreateJobInputAdapter`，不能在 wrapper 内部重复实现字段映射。

`LegacyCreateJobInputAdapter` 是唯一顶层 input adapter 投影实现。它必须锁定以下输入标准：

- 严格接收旧 `CreateJobRequest` 字段：`client_request_id`、`job_type`、`model_id`、`source.oss`、`callback`、`prompt.blocks`。
- 拒绝未知字段，不兼容旧测试中明确拒绝的 `input`、`output`、`execution_mode` 或 artifact `target`。
- 保留 `client_request_id` 为同一 caller 下的幂等键；重复提交返回已有 Job，不创建新 Job。
- 保留三个旧公开 `job_type` 值：`novel_localization.step1_localize`、`novel_localization.step2_review`、`novel_localization.step3_translate`，并映射到当前同名 public root job_type；不得把它们折叠成一个泛化 `novel_localization` job_type。
- 将 `model_id` 映射为当前执行所需的模型选择；adapter 只做准入和归一化，不直接构造 provider 请求。
- 调用 `canonical_ref_from_legacy_source_oss()` 将 `source.oss` 归一化为当前 executor 可消费的 `CanonicalObjectRef`：`oss_key` 按旧合同只做去首尾空白和斜杠，`oss_url` 保留为旧合同必填字段但不用于对象身份推导，`content_hash` 必须是 `sha256:<64 lowercase hex>` 或空，`content_type` 必须规范化为 `text/plain; charset=utf-8`。
- adapter 不在路由层读取 OSS 正文；正文读取、hash 校验、大小限制和失败错误仍属于 Job 执行链。
- 将 `prompt.blocks` 归一化为当前 prompt payload；`key` 必须来自对应 `job_type` 的模板，`role` 必须为旧合同允许的 `user`，`content` 允许空字符串但不能为 `null`。
- 不自动补齐缺失 prompt block，不接受 `system` block，不吞掉未知、重复或角色不匹配的 prompt block。
- 将 `callback.url` 和 `callback.events` 归一化为当前 Job callback 配置，同时标记该 Job 使用 legacy callback delivery 协议。
- input 装饰器返回给 route facade / Job service 的只能是当前项目风格的创建命令或等价结构，不能让旧 `CreateJobRequest` 继续向 executor、worker 或 Job service 深层传播。

### Output Adapter 标准

`@legacy_output_adapter(...)` 是旧响应出口的统一装饰器。它必须调用 `LegacyOutputAdapter`，不能在 wrapper 内部重复实现响应字段映射。

`LegacyOutputAdapter` 是唯一 output adapter 投影模块，但内部必须至少拆出以下确定性 projection，便于单元测试锁定：

- `project_create_response(job)`: 当前 Job 记录 -> 旧 `CreateJobResponse`，返回 `job_id`、`status`、`status_url`、`created_at`，HTTP 状态保持 `202`。
- `project_status_response(job, current_cost)`: 当前 Job 记录、公开结果、公开错误和当前 Job 级总开销 -> 旧 `JobStatusResponse`。字段包括 `job_id`、`job_type`、`status`、`progress_percent`、`progress_text`、`result`、`error`、`cost`、`created_at`、`started_at`、`finished_at`。
- `project_result_artifacts(job_result)`: 当前业务结果 -> 旧 `result.artifacts[]` 与 `result.signals`。大文本写出结果调用 `legacy_oss_artifact_from_output_object()` 投影为旧 OSS object manifest；短文本才允许 `content`。
- `project_cost(current_job_cost)`: 当前 `job.cost` -> legacy `cost`。未终态、费用未聚合完成或当前 `job.cost` 不可用时返回 `{"currency":"USD","amount":null,"final":false}`；当前 `job.cost` 可用时返回同一 `currency`、`amount` 并置 `final=true`。
- `project_callback_body(job, current_cost)`: 当前终态 Job -> 旧 callback body，沿用旧 `event`、`job_id`、`job_type`、`status`、`result`、`error`、`cost`、`finished_at` 语义。
- `project_meta_response(registry)`: 当前模型和 prompt 模板来源 -> 旧 `ModelsResponse` 与 `PromptTemplatesResponse`。
- `project_error(error)`: 当前 `AppError` 或 validation error -> 旧 `{ "error": { "code", "message", "details" } }` 响应形状。

output 装饰器和 output adapter 不暴露当前 `JobEnvelope`、billing envelope、`usage`、ledger、provider、pricing ref、attempt、workflow node、internal child job id 或 provider 原始错误。

`LegacyCreateJobInputAdapter`、`LegacyOutputAdapter` 和 input/output 装饰器都不重新实现旧 OSS payload 片段转换；它们必须复用 `legacy_oss_object_ref.py` 中的 `canonical_ref_from_legacy_source_oss()` 和 `legacy_oss_artifact_from_output_object()`。实际 OSS 读写仍由 executor 和当前对象存储 integration/tool 负责。

### Callback Delivery Adapter 标准

`LegacyCallbackDeliveryAdapter` 不生成业务结果，只负责投递 output adapter 生成的旧 callback body。它必须锁定以下传输标准：

- header 使用 `X-AI-Service-Job-Id`、`X-AI-Service-Event`、`X-AI-Service-Timestamp`、`X-AI-Service-Signature`。
- 签名原文为 `timestamp + "." + request_body`，算法为 HMAC-SHA256，格式为 `sha256=<hmac>`。
- 接收方返回任意 `2xx` 即视为成功；不要求当前通用 callback 合同的 `accepted=true` JSON body。
- callback 投递失败不改变 Job 终态，只映射到当前 callback 记录、重试和运维可见状态。

## Planned Work

1. 捕获并锁定旧合同
   - 从旧项目 schema、route tests 和接口文档提取兼容合同，形成当前项目内的 contract tests。
   - 锁定旧请求的 strict schema 行为，包括未知字段拒绝、必填字段、`source.oss` 格式、`prompt.blocks` 结构和 callback 校验。
   - 锁定旧响应形状，包括 `POST /jobs` 的 202 body、`GET /jobs/{job_id}` 的状态 body、错误 body、artifact body 和 callback body。
   - 锁定旧 meta 接口响应形状，包括 `GET /models` 和 `GET /prompt-templates`。
   - 明确兼容边界：旧前缀服务旧合同，当前 `/api/v1/ai-jobs` 继续服务当前通用 Job 合同；旧合同不通过“记录差异”来放宽。

2. 增加 legacy route facade
   - 新增旧前缀 route，保留 `/api/v1/novel-localization-ai/jobs`、`/api/v1/novel-localization-ai/jobs/{job_id}`、`/api/v1/novel-localization-ai/models` 和 `/api/v1/novel-localization-ai/prompt-templates` 的调用方式。
   - facade 只负责旧前缀路由、幂等请求处理、当前 Job 创建调用和状态查询调用；旧合同输入输出适配通过 input/output 装饰器承接。
   - facade 不承载模型调用、prompt 拼接、OSS 大文本处理或业务解析逻辑。
   - 若旧合同要求无 envelope 响应，旧前缀应明确避开当前通用 envelope；不能让调用方看到当前项目的内部响应包装。
   - `models` 和 `prompt-templates` 的数据来源可以来自当前 registry 或业务静态配置，但输出必须经过 `@legacy_output_adapter(...)` 调用旧 meta response projection。

3. 增加 input/output 装饰器
   - 在 `app/jobs/types/novel_localization/adapter_decorators.py` 下新增 `@legacy_input_adapter(...)` 和 `@legacy_output_adapter(...)`。
   - 装饰器只负责调用对应 adapter、统一 legacy contract 元数据、错误投影入口和 route facade 的薄包装。
   - 装饰器不得执行模型调用、OSS 读写、费用计算、Taskiq 投递、workflow 编排或 Job 状态写入。
   - 装饰器不得在 import 时产生运行时副作用；如需注册元数据，应通过当前 composition root 显式接入。
   - 装饰器 wrapper 内不得重复实现旧字段映射、OSS manifest 映射或 `cost` 映射。

4. 增加 input adapter
   - 在 `app/jobs/types/novel_localization/` 下新增业务专属 input adapter。
   - 将旧 `CreateJobRequest` 投影为当前 Job 创建所需的 `job_type`、`job_params`、runtime metadata 和 callback 配置。
   - 对旧 `source.oss` 调用已新增的 `canonical_ref_from_legacy_source_oss()`，不要在业务 input adapter 中重复实现 OSS key、hash 或 content type 规则。
   - 保留旧 `job_type` 字符串作为外部兼容值，内部映射到当前同名 public root `job_type`，映射必须确定、可测试。
   - 不允许把旧 `job_type` 降级成 `job_params.step`，也不允许由 input adapter 在一个泛化 `novel_localization` job_type 内部分发 step。
   - 只在 adapter 层处理旧字段名、旧 OSS 引用、旧 prompt blocks 和旧 model id；executor 接收当前项目风格的结构化输入。
   - adapter 失败应产生可投影回旧合同的明确错误，不做 silent fallback 或默认值吞错。

5. 增加 output adapter
   - 在 `app/jobs/types/novel_localization/` 下新增业务专属 output adapter。
   - 将当前 Job 状态、结果、错误、artifact 和时间字段投影为旧 `JobStatusResponse`。
   - 隐藏当前内部 attempt、provider、ledger、workflow child job id 和内部错误细节。
   - 保留旧结果语义：大文本 artifact 暴露为旧 artifact 字段，审核不通过暴露为 `signals.passed=false` 的成功结果。
   - 旧 `GET /jobs/{job_id}` 和旧 callback 可以返回 legacy `cost` 字段；该字段由 output adapter 基于本项目已有 Job 级总开销投影生成，不在 executor 或 adapter 内重新计算费用。
   - legacy `cost` 只表达总开销三元组：`currency`、`amount`、`final`。当 Job 未终态、费用未聚合完成或当前 `job.cost` 不可用时，返回 `{"currency":"USD","amount":null,"final":false}`；当本项目 `job.cost` 可用时，返回同一总金额并置 `final=true`。
   - output adapter 不暴露当前 billing envelope、`usage`、ledger、provider、pricing ref、计费单位或诊断原因；如需完整计费状态，只能走当前 `/api/v1/ai-jobs/jobs/{job_id}/billing` 合同。
   - artifact 字段按旧合同稳定投影：`localized_text` 和 `translated_text` 必须返回 `storage=oss_object`、`oss_bucket`、`oss_key`、`oss_region`、`content_hash`、`content_size_bytes`，不得返回正文 `content` 或额外下载 URL；`work_note`、`review_summary` 等短文本 artifact 才允许返回 `content`。
   - 对大文本写出结果调用已新增的 `legacy_oss_artifact_from_output_object()`，不要在业务 output adapter 中重复实现 OSS object manifest 规则。
   - `localized_text` 的对象 key 语义保持为 `<output_prefix>/<job_id>/localized.txt`，`translated_text` 的对象 key 语义保持为 `<output_prefix>/<job_id>/translated.txt`；对象生命周期仍由当前对象存储与 Job artifact 规则承担。

6. 增加 legacy callback delivery adapter
   - 新增旧 callback delivery adapter，单独处理旧 callback 传输协议，而不是复用当前通用 callback sender 的响应判定。
   - callback body 使用旧项目公开合同，由 `@legacy_output_adapter(...)` 调用 output adapter 生成稳定 payload 后交给 delivery adapter 投递。
   - callback headers、签名串、时间戳和重放防护规则必须保持旧项目 `X-AI-Service-*` 合同；不能替换成当前通用 `X-Callback-*` 合同。
   - callback 成功判定保持旧项目语义：接收方返回任意 `2xx` 即视为成功；不能要求接收方返回当前通用合同的 `accepted=true` JSON body。
   - callback 重试、超时、错误记录和最终失败状态需要映射到当前 Job callback 记录能力，但不得改变旧调用方可见的 callback 协议。

7. 重写初始 `job_type` 执行链
   - 新增 `app/jobs/types/novel_localization/` 业务包，集中承载小说本地化共享代码、adapter、parser、prompt、错误码和 executor。
   - 在该业务包内注册 `novel_localization.step1_localize`、`novel_localization.step2_review`、`novel_localization.step3_translate` 三个公开兼容 `job_type`。
   - 不注册单一泛化 `novel_localization` job_type；`step1`、`step2`、`step3` 是当前 registry、schema、prompt、executor、错误码、artifact 和运维查询的可发现治理单元。
   - 这三个 legacy `job_type` 固定作为 public root 入口；它们是旧调用方可提交、可查询、可接收 callback 的兼容面。
   - 三个 public `job_type` 可以复用同一个包内的 shared helper，但各自的 executor/spec/result projection 边界必须能被单独测试和注册校验。
   - 使用当前项目 executor 风格重写执行链：输入加载、prompt 组装、AI 调用、模型输出解析、artifact 持久化、结果构建和错误抛出。
   - AI 调用应通过当前 `app/services/ai_gateway_facade.py` 和 ledger 机制，不直接复制旧模型客户端调用。
   - 对象存储读写应复用当前对象存储工具和产物规范，不复制旧 OSS service 的状态机逻辑。
   - parser 需要保留旧业务输出合同：step1 解析工作注释与本地化正文，step2 解析校验结论与问题说明，step3 输出译文。
   - 大文本结果不得默认内联到 Job result；需要按当前 artifact 规则持久化并由 output adapter 投影回旧字段。

8. 处理复杂任务和 child Job
   - 初始接入不迁移旧 Celery chunk workflow；先用单 root Job 覆盖旧项目已启用的生产路径。
   - 如果后续确认长文本必须拆分，优先按当前 workflow kernel 设计静态 root + internal child Job：root 负责公开状态、callback、billing 和最终结果，child 只负责内部节点执行。
   - 未来 internal child job_type 必须使用独立的内部 leaf 类型，不复用 `novel_localization.step1_localize`、`novel_localization.step2_review`、`novel_localization.step3_translate` 这些 legacy public root 名称。
   - child Job 的数量、依赖和执行参数必须在 root 创建或 root 执行早期形成冻结计划；不能把 Celery 的动态 canvas 直接搬进 Taskiq worker。
   - 旧调用方仍只看到一个旧合同下的 Job，不看到 child Job、节点重试、内部状态或中间错误。
   - 若 chunk 数量依赖读取 OSS 大文本才能确定，应优先评估在单 root executor 内完成分块编排，或设计独立 prepare 节点；不能绕开当前 workflow kernel 的冻结计划约束。

9. tool / capability 决策
   - 初始阶段不新增业务 tool 或 AI capability；复用当前对象存储、AI gateway、registry 和 Job kernel。
   - 不新增独立计费 adapter 或业务计费计算器；legacy `cost` 是 output adapter 对当前 Job 级总开销投影的响应包装。
   - 不把小说本地化 prompt、业务 parser、审核规则、merge 规则注册为公共 tool。
   - 只有当某个能力满足“稳定、可复用、无 Job 状态副作用、输入输出合同独立”的条件时，才评估新增 tool。
   - 只有当能力需要被多个 `job_type` 以统一 AI capability 合同复用时，才评估新增 capability。

10. 测试与验证
   - 增加旧接口 contract tests，覆盖 meta 查询、创建、查询、错误响应、严格 schema、idempotency、callback 投影、artifact 投影和 legacy `cost` 投影。
   - 增加 input adapter 与 output adapter 单元测试，覆盖字段映射、错误映射、旧 `job_type` 映射和 `job.cost -> legacy cost` 映射。
   - 增加 input/output 装饰器单元测试，覆盖装饰器确实调用对应 adapter、不会重复实现字段映射、不会让旧 payload 继续向内部 service/executor 传播，以及 adapter error 会被投影成旧错误响应。
   - 已新增 legacy OSS payload adapter 单元测试；后续业务 input/output adapter 测试应覆盖它们是否调用 `canonical_ref_from_legacy_source_oss()` 和 `legacy_oss_artifact_from_output_object()`，避免重复实现规则。
   - 增加 legacy callback delivery adapter 测试，覆盖旧 header、签名、任意 `2xx` 成功判定、非 `2xx` 失败、超时和重试记录。
   - 增加 executor parser 单元测试，覆盖 step1、step2、step3 的合法输出与非法输出。
   - 增加 registry 测试，确保 3 个旧 `job_type` 分别被注册，且 prompt、模型、错误码和 artifact 规则可发现；测试应拒绝只注册单一 `novel_localization` job_type 的实现。
   - 增加 worker 层测试，覆盖成功、模型输出不合法、对象存储失败、callback 失败重试和 step2 审核不通过但 Job 成功。
   - 如果后续实现 workflow/chunking，再增加 root/child workflow e2e、重复推进幂等、root billing 聚合和 callback mock 测试。

## Acceptance

- 旧调用方可以继续调用 `/api/v1/novel-localization-ai/jobs`、`/api/v1/novel-localization-ai/jobs/{job_id}`、`/api/v1/novel-localization-ai/models` 和 `/api/v1/novel-localization-ai/prompt-templates`，请求和响应字段保持旧合同兼容。
- 接入实现必须按本文 adapter 清单落地：已新增的 `legacy_oss_object_ref.py` 作为 OSS payload 片段转换边界，另需 `@legacy_input_adapter(...)`、`@legacy_output_adapter(...)`、1 个顶层 `LegacyCreateJobInputAdapter`、1 个集中 `LegacyOutputAdapter`、1 个 `LegacyCallbackDeliveryAdapter`；不得新增绕过这些边界的业务请求/响应转换路径。
- 旧前缀 route facade 的输入输出适配必须经由 input/output 装饰器触发；装饰器 wrapper 内不得重复实现旧字段映射、OSS manifest 映射、`cost` 映射或业务解析。
- input/output 装饰器不得执行模型调用、OSS 读写、费用计算、Taskiq 投递、workflow 编排、Job 状态写入或 import-time 运行时注册副作用。
- 当前 Job service、Taskiq worker、executor、AI gateway 和 billing service 不接收旧 `CreateJobRequest` 或旧响应 shape，只处理当前项目内部结构。
- 旧 `CreateJobRequest` 的 strict schema 行为有 contract tests 锁定，未知字段不会被静默忽略。
- `client_request_id` 幂等语义按旧合同保留：相同 `client_request_id` 返回已有 Job，不重复创建新 Job。
- 新增代码目录按业务聚合为 1 个 `app/jobs/types/novel_localization/` 包；该包内可以共享 helper、parser、prompt 和 adapter，但不能把 3 个 step 隐藏到包外的通用分发器。
- 旧 `job_type` 值 `novel_localization.step1_localize`、`novel_localization.step2_review`、`novel_localization.step3_translate` 必须作为 3 个 public root `job_type` 被当前 Job registry 接受，并分别确定映射到对应 executor/spec。
- 不注册单一泛化 `novel_localization` job_type，也不通过 `job_params.step`、adapter 分发或 executor 内大分支替代 3 个 public `job_type`。
- 旧 callback body、`X-AI-Service-*` header、签名和任意 `2xx` 成功判定保持兼容，不要求旧调用方支持当前通用 `X-Callback-*` header 或 `accepted=true` 响应 body。
- 当前实现不引入 Celery 依赖，不运行 Celery worker，不复制 Celery canvas；所有执行都通过当前 Taskiq + Job 体系。
- 模型调用通过当前 AI gateway facade 和 ledger 记录，成本与调用记录归属当前 root Job；legacy `cost` 只由 output adapter 包装当前 Job 级总开销投影，不新增业务计费计算路径。
- 大文本结果通过当前对象存储 artifact 机制保存，旧输出字段由 output adapter 投影生成；`localized_text` 和 `translated_text` 对旧调用方只暴露旧 OSS object manifest，不暴露正文 `content` 或额外下载 URL；旧 `GET /jobs/{job_id}` 可返回 legacy `cost`，但不暴露当前 billing envelope、`usage`、ledger、provider、pricing ref 或诊断明细。
- Job 未终态或费用不可用时，legacy `cost` 返回 `{"currency":"USD","amount":null,"final":false}`；本项目 Job 级总开销可用时，legacy `cost.amount` 返回同一总金额且 `final=true`。
- `step2_review` 的审核不通过结果表现为 Job 成功且 `signals.passed=false`，不会被错误地映射为执行失败。
- 初始版本不暴露 child Job；如果未来新增 workflow，旧调用方仍只看到 legacy public root Job 的旧合同状态，internal leaf job_type 不复用 legacy public `job_type` 名称。
- 新增实现通过 `./scripts/verify.sh check`；涉及 workflow 或真实业务 e2e 时，额外通过 workflow smoke 与业务级 e2e 验证。

## Non-goals

- 不修改旧调用方的请求合同和输出合同。
- 不把旧 Celery 实现原样迁入当前项目。
- 不把旧项目的 chunk workflow 草稿视为已生产化能力。
- 不把小说本地化业务规则抽象成全局公共工具。
- 不把 `novel_localization.step1_localize`、`novel_localization.step2_review`、`novel_localization.step3_translate` 折叠成一个 `novel_localization` job_type。
- 不让 adapter 承担内部 step 分发；adapter 只做旧合同输入输出投影。
- 不让旧调用方感知当前 Job attempt、internal child Job、AI provider ledger 或内部 workflow 节点。
- 不在本计划中承诺生产部署、用户系统、项目管理或前端状态编排。
