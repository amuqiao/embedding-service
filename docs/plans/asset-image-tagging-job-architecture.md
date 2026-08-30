# 图片打标 Job 架构计划

本文把“图片打标签”需求单独落到本项目 FastAPI AI Job 架构中。它不是以图搜图方案，也不记录当前已实现事实；真正落地后，应把稳定实现沉淀到 `docs/current/`，把对外字段合同拆到 `docs/api/`。

## 先区分两类需求

图片打标和以图搜图都面向素材图片，但解决的是两件不同的事。

```text
图片打标
  -> 生成 description、结构化 tags、aspect 分类
  -> 服务业务筛选、运营审核、可解释展示和搜索增强

以图搜图
  -> 生成 image/text/fused embedding
  -> 用 pgvector 做相似度召回和排序
  -> 返回 entity_id + score
```

两者可以组合，但不应在第一版耦合成一个不可拆的流程。推荐做法是先让图片打标产出候选标签和描述，由业务后端决定是否审核、落库和触发向量索引。

## 需求翻译

用户表面诉求：

```text
给图片自动打标签，并判断是否需要借鉴 chapter_image_tagger。
```

我理解的真实需求：

```text
业务后端维护素材、标签体系、审核状态、权限和展示数据。
本服务负责基于图片和候选标签集调用多模态模型，返回可校验的候选标签、图片描述和模型诊断元信息。
```

不在本服务解决的问题：

- 不作为业务标签主库。
- 不决定标签是否最终生效。
- 不做用户权限、项目权限、素材状态筛选。
- 不拼装素材详情页展示数据。
- 不用裸 URL、完整 base64 或 provider 原始响应作为对外稳定合同。
- 不把图片打标强绑定到 pgvector 向量写入。

## 当前 Baseline

本项目当前已经具备：

- 标准异步 Job 入口：`POST /api/v1/ai-jobs/jobs`。
- Job 聚合、Attempt、Dispatch outbox、Callback outbox、recovery 和 billing ledger。
- `generate_text_with_images_with_ledger()` 多模态文本生成通路。
- model catalog、provider、adapter、pricing registry 和模型门禁。
- `OssUrlRef` 风格的图片输入引用、sha256、content-type 和图片大小校验能力。
- `poster_title_image` 中可参考的图片读取、校验、模型调用、Job 结果快照和 workflow 组织方式。

仍缺少：

- 图片打标专用 `job_type`。
- 图片打标 params/result schema。
- 图片打标 prompt 模板和模型选择配置。
- 针对模型 JSON 输出的严格解析与 schema 校验。
- 图片打标结果与后端标签体系的版本、审核和向量索引衔接约定。

## 对 `chapter_image_tagger` 的借鉴边界

`chapter_image_tagger` 值得借鉴的是产品模型，不是运行时架构。

| 可借鉴部分 | 原因 |
|---|---|
| `candidate_tags` | 由后端传入候选标签，模型只在受控集合内选择，减少自由造词 |
| `image_type` | 按 `hair`、`face`、`body`、`cloth`、`background` 等场景切换判断重点 |
| `gender` | 对人物素材的发型、脸部、服装标签有辅助意义 |
| `description` | 可作为人工审核摘要，也可作为后续文本向量输入 |
| 按 aspect 输出 | 便于业务后端把标签映射回标签体系 |

不建议直接照搬的部分：

| 不照搬部分 | 原因 |
|---|---|
| 独立 `/tag` 同步接口 | 模型调用有耗时、限流、失败和费用，适合当前 Job 异步链路 |
| 直接下载远程图片 URL | 应复用本项目 OSS ref、sha256 和 content-type 校验 |
| 直接使用 `AsyncOpenAI` | 会绕过 model catalog、usage ledger、billing 和统一错误语义 |
| 正则提取 JSON | 当前项目应 fail-fast，用严格 JSON 解析和 Pydantic schema 校验 |
| 宽泛异常返回 500 | 当前项目应投影为可治理的 `AppError` 和 Job error code |

## 推荐架构

第一版推荐新增一个普通 public root Job：

```text
asset_image_tagging
```

它是单图打标任务，不需要一开始做 root/child workflow。

```text
业务后端
  -> 上传或确认素材图片
  -> 提交 asset_image_tagging Job，带 entity_id、source_revision、image、candidate_tags

embedding-service API
  -> 校验 job_type / job_params / 图片引用
  -> 写 Job / attempt / dispatch outbox

worker
  -> 读取图片并校验 hash、content-type、大小和像素
  -> 构造图片打标 prompt
  -> generate_text_with_images_with_ledger()
  -> 严格解析 JSON
  -> 校验输出标签必须来自 candidate_tags
  -> 写 Job succeeded result 或 failed error

业务后端
  -> 轮询或接收 callback
  -> 审核、落库、展示或触发 asset_vector_upsert
```

推荐先做单图 Job 的原因：

- 图片打标通常是一次模型调用，单 root Job 已经能表达异步执行、重试、billing 和 callback。
- 批量导入是否需要部分成功、失败重跑和进度合并，取决于后端真实调用方式，不必第一版提前复杂化。
- 与以图搜图解耦后，标签错误不会自动污染向量索引。

## 方案比较

| 方案 | 适用场景 | 判断 |
|---|---|---|
| 独立 `asset_image_tagging` Job | 单图或后端自编排批量；需要审核标签 | 推荐第一版 |
| `asset_image_ingest` workflow：打标后自动建向量 | 后端明确要求全自动入库，且接受错标签进入检索文本 | 后续可选 |
| 同步 `/tag` API | 低延迟、无重试、低成本的内部试验 | 不推荐作为正式能力 |
| 直接复用 `chapter_image_tagger` 服务 | 临时 PoC | 不推荐进入本项目生产路径 |

## Job 输入草案

第一版建议由后端传入已知标签体系和图片引用。

```json
{
  "client_request_id": "asset-image-tagging:a1:v12",
  "job_type": "asset_image_tagging",
  "job_params": {
    "entity_id": "a1",
    "source_version": "12",
    "source_revision": 12,
    "image": {
      "public_url": "https://oss.example.com/preview/a1.jpg",
      "internal_url": "https://bucket.oss-cn-shanghai-internal.aliyuncs.com/preview/a1.jpg",
      "content_type": "image/jpeg",
      "sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    },
    "image_type": "hair",
    "gender": "female",
    "taxonomy_version": "asset-tags-v1",
    "candidate_tags": {
      "hair_color": ["black", "brown", "blonde", "red"],
      "hair_length": ["short", "medium", "long"],
      "hair_shape": ["straight", "wavy", "curly"]
    },
    "max_tags_per_aspect": 3
  },
  "metadata": {
    "source": "cms"
  },
  "options": {
    "idempotency_mode": "return_existing"
  }
}
```

字段语义：

| 字段 | 建议 | 说明 |
|---|---|---|
| `entity_id` | 必需 | 业务素材 ID；本服务只回传和审计，不负责查详情 |
| `source_version` | 可选 | 后端可读版本；用于审计和幂等说明 |
| `source_revision` | 推荐必传 | 可比较的新旧版本；后端用它防止旧结果覆盖新标签 |
| `image` | 必需 | 复用 OSS URL Ref 风格，包含 `public_url`、`internal_url`、`content_type`、`sha256` |
| `image_type` | 必需 | 第一版可取 `hair`、`face`、`body`、`cloth`、`background`、`general` |
| `gender` | 可选 | 第一版可取 `male`、`female`、`unknown` |
| `taxonomy_version` | 必需 | 后端标签体系版本 |
| `candidate_tags` | 必需 | key 是 aspect，value 是该 aspect 下允许选择的标签 |
| `max_tags_per_aspect` | 可选 | 限制每个 aspect 最多返回几个标签 |

`candidate_tags` 应保持为后端传入的受控集合。第一版不建议让本服务自己维护业务标签目录，避免标签事实源分裂。

## Job 输出草案

成功结果：

```json
{
  "entity_id": "a1",
  "source_version": "12",
  "source_revision": 12,
  "taxonomy_version": "asset-tags-v1",
  "description": "A woman with medium-length wavy brown hair.",
  "tags": [
    {
      "aspect": "hair_color",
      "values": ["brown"],
      "confidence": 0.86
    },
    {
      "aspect": "hair_length",
      "values": ["medium"],
      "confidence": 0.78
    }
  ],
  "model_id": "gpt-5.5",
  "provider_model": "gpt-5.5",
  "needs_review": true
}
```

输出约束：

- `tags[].aspect` 必须来自 `candidate_tags` 的 key。
- `tags[].values[]` 必须来自对应 aspect 的候选标签列表。
- `confidence` 是模型自评参考值，不作为自动通过依据。
- `description` 用于审核摘要和可选向量文本，不作为标签事实源。
- `needs_review` 第一版建议固定为 `true`，由后端决定是否自动采信。

失败语义：

| 错误 | 含义 | 是否可重试 |
|---|---|---|
| `INVALID_INPUT` | 标签集合为空、字段非法、图片引用非法 | 否 |
| `INPUT_HASH_MISMATCH` | 图片内容与提交 hash 不一致 | 否 |
| `INPUT_TOO_LARGE` | 图片大小、宽高或像素超过限制 | 否 |
| `MODEL_NOT_AVAILABLE` | 配置的多模态模型不可用 | 否 |
| `MODEL_CALL_TIMEOUT` | 模型调用超时 | 是 |
| `AI_PROVIDER_FAILED` | provider 暂态失败或限流 | 是 |
| `MODEL_OUTPUT_INVALID` | 模型输出不是合法 JSON，或标签不在候选集合内 | 否 |

## Prompt 与输出校验

Prompt 应继承 `chapter_image_tagger` 的核心约束，但按本项目方式沉淀到 `app/jobs/types/asset_image_tagging/prompts.yaml`。

Prompt 目标：

- 明确图片类型、性别、标签体系版本和 candidate tags。
- 要求模型只从候选标签中选择，不允许创造新标签。
- 要求输出 JSON object。
- 要求每个 aspect 最多返回 `max_tags_per_aspect` 个标签。
- 要求给出短描述。

模型输出处理：

```text
provider text
  -> json.loads()
  -> AssetImageTaggingModelOutput schema
  -> 校验 aspect 和 tag value 均属于 candidate_tags
  -> 转为 AssetImageTaggingResult
```

不建议用正则从自然语言里抽 JSON。模型不返回合法 JSON 时，应直接让 Job 失败为 `MODEL_OUTPUT_INVALID`，方便 prompt、模型或调用方输入被明确修复。

## 与以图搜图的衔接

图片打标计划与 [`image-search-tongyi-pgvector-architecture.md`](image-search-tongyi-pgvector-architecture.md) 是两份独立计划。

可选衔接方式：

```text
asset_image_tagging succeeded
  -> 后端审核 tags
  -> 后端把 description + tags 拼成 text
  -> 后端提交 asset_vector_upsert
  -> 本服务写 text/fused embedding
```

不建议第一版由 `asset_image_tagging` executor 直接写 pgvector，因为这会把模型候选标签、业务审核状态和向量索引状态耦合到同一个 Job 结果里。

后续如果业务明确要求全自动素材入库，可新增 root workflow：

```text
asset_image_ingest root
  -> asset_image_tagging child
  -> asset_vector_upsert child
  -> join
```

这个 workflow 应作为新计划或第二阶段实现，不覆盖第一版单图打标 Job。

## 后端需要提供的接口输入

业务后端创建打标 Job 时需要提供：

- `entity_id`
- `source_revision` 或可比较的更新时间
- `image` OSS URL Ref
- `image_type`
- `taxonomy_version`
- `candidate_tags`
- 可选 `gender`
- 可选 `max_tags_per_aspect`
- 可选 callback URL

业务后端接收结果后需要负责：

- 按 `entity_id + source_revision` 判断结果是否仍然新鲜。
- 把模型候选标签进入审核流或自动采信策略。
- 把最终标签写入业务标签表。
- 在需要搜索增强时，提交独立向量索引 Job。
- 使用 `client_request_id` 保证同一次打标提交幂等。

## 实现落点

建议新增文件：

```text
app/jobs/types/asset_image_tagging/
  __init__.py
  executor.py
  register.py
  prompts.yaml
  models.yaml
```

建议修改现有文件：

```text
app/jobs/types/register.py
app/schemas/jobs.py
app/core/logging.py
app/ai/catalog/models.yaml
app/ai/pricing/pricing.yaml
docs/api/asset-image-tagging-api.md
```

如果后续把图片输入 schema 从 `poster_title_image` 抽成通用对象，应避免只为打标复制一份长期分叉的图片校验逻辑。

## Planned Work

### Phase 1: 单图打标 Job

- 新增 `asset_image_tagging` job type。
- 新增 params/result schema。
- 新增 prompt 模板和模型 slot。
- 复用 `generate_text_with_images_with_ledger()`。
- 复用 OSS URL Ref 和图片校验。
- 严格校验模型输出。
- 增加 executor 单元测试、schema 测试和 registry 合同测试。

### Phase 2: 对接后端审核与索引

- 补充 `docs/api/asset-image-tagging-api.md`。
- 明确后端如何处理 `source_revision`、审核状态和重复 callback。
- 明确 `description/tags` 拼接为向量文本的规则。
- 与 `asset_vector_upsert` 计划对齐字段命名和幂等键。

### Phase 3: 批量和 workflow

仅当后端需要服务端批量编排时再做：

- 新增 `asset_image_tagging_batch` root workflow。
- 每张图一个 internal child Job。
- join 汇总成功、失败和待重试项。
- 保持部分失败结果可查询，不把批量失败隐藏成一个总错误。

## Acceptance

计划可以关闭或沉淀为 current/API 文档的条件：

- `asset_image_tagging` 可通过 `POST /api/v1/ai-jobs/jobs` 创建。
- Job 成功结果通过 Pydantic schema 校验，且所有标签都来自 `candidate_tags`。
- 图片输入会校验 content-type、sha256、大小、宽高和像素限制。
- 模型调用进入 AI ledger，Job billing 可查询。
- provider 暂态失败可按 Job retry policy 重试。
- `MODEL_OUTPUT_INVALID`、`INPUT_HASH_MISMATCH`、`INPUT_TOO_LARGE` 等错误有测试覆盖。
- API 对接文档明确业务后端负责标签事实源、审核和向量索引触发。
- 与以图搜图文档保持边界清晰，不把打标实现写入向量检索 current 事实。

## 需要确认

- `image_type` 是否固定为 `hair / face / body / cloth / background / general`，还是由后端按业务标签体系传入。
- `gender` 是否只用于人物素材，非人物素材是否统一传 `unknown`。
- `candidate_tags` 的标签值使用英文 code、中文 label，还是同时包含 code 和 label。
- 是否需要返回每个标签的解释理由。如果需要，理由只能作为审核辅助，不应进入稳定筛选事实。
- 第一版是否允许后端自动采信高置信标签，还是所有模型标签都必须人工审核。
