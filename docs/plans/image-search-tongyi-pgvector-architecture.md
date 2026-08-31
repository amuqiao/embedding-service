# 以图搜图 Asset Vector Capability 落地方案

本文把“以图搜图 / 文搜图 / 描述搜索”定义为 `asset_vector` capability：后端资源库是资源事实源，本服务是 AI 能力层和向量 read model 投影层，通过异步 Job 同步资源向量，通过同步 HTTP route 提供搜索和对账能力。

本文是计划文档，不记录当前已实现事实，也不是正式 API 合同。实现完成后，应把当前事实沉淀到 `docs/current/`，把对外稳定接口拆到 `docs/api/asset-vector-api.md`，并把本文标记完成或归档。

## 先建立两个心智模型

以图搜图不是一个单独 `job_type`，而是一个可拆卸 capability。

### 心智模型一：资源事实源与向量投影

第一层心智模型是“源表和投影”。

```text
后端资源服务 = source of truth
  resource_id / group_id
  OSS public_url
  标签 / 分类 / 权限 / 上下架 / 收藏
  资源详情 / 组装规则 / 搜索历史

embedding-service = vector projection
  asset_vector_entries
  asset 主检索向量
  embedding_input_kind = image / text / image_text
  source_revision
  model_id / route_config_hash
```

这意味着：

```text
后端资源发生变化
  -> 后端决定这次变化是否影响检索
  -> 后端调用 batch_upsert / batch_delete
  -> embedding-service 更新自己的向量投影
```

所以“本服务无状态”需要精确定义：

```text
API / worker 进程不依赖本地文件或内存状态，可以水平扩容。
本服务不持有业务资源状态。
本服务会持久化自己的向量 read model，也就是 pgvector 数据库。
```

### 心智模型二：全生命周期闭环

第二层心智模型是“同步、搜索、删除、对账、重建”的闭环。

```text
             [后端资源库]
                  |
                  | batch_upsert / batch_delete
                  v
        [embedding-service Job]
                  |
                  | read OSS public_url / embed text_payload
                  v
          [pgvector 向量投影]
                  |
                  | vector-search / exists / ids
                  v
             [后端资源库]
                  |
                  | 拼详情 / 聚合组 / 展示
                  v
              [前端资源库]
```

这个闭环拆成三类调用：

```text
资源向量同步：慢写入，可重试

后端资源新增 / 更新 / 重传 / 检索文本变化
  -> 提交 asset_vector_batch_upsert Job
  -> 单资源场景也作为 items 中的一条记录
  -> worker 读取 OSS 公网 URL 或 text_payload
  -> 调 DashScope embedding
  -> 写入 pgvector asset_vector_entries
  -> Job succeeded / failed + callback
```

```text
资源向量删除：资源事实删除后的投影清理

后端资源删除 / 下架 / 批量清理
  -> 提交 asset_vector_batch_delete Job
  -> 单资源场景也作为 resource_ids 中的一条记录
  -> 删除 asset_vector_entries 中对应向量
  -> 后端可通过对账接口发现漏删或漏建
```

```text
搜索与对账：快查询，同步返回

用户搜索
  -> 后端先做权限、项目、分类、标签、状态、收藏等业务过滤
  -> 调用 vector-search，传 candidate_resource_ids
  -> 本服务生成 query embedding 或读取已有 resource embedding
  -> pgvector 在候选集内排序
  -> 返回 resource_id + group_id + score
  -> 后端回业务库拼详情、聚合组、排序展示

后端定期对账
  -> exists 找本服务缺失的向量
  -> ids 找本服务多出来的幽灵向量
  -> 补提 batch_upsert 或 batch_delete
```

索引和删除是 batch-first 异步 Job；搜索和对账是同步 HTTP。它们共享同一张向量表，但不是同一种接口形态。

## 已确认事实与前提

已确认：

- 本地 PostgreSQL 镜像已替换为 `pgvector/pgvector:0.8.6-pg16`。
- 向量模型采用 `tongyi-embedding-vision-flash-2026-03-06`。
- 每个资源一定有 `resource_id`。
- 如果资源是组资源，额外有 `group_id`。
- OSS 公网可读，本服务可以直接读取后端传入的 OSS 公网 URL。
- 本服务是 AI 能力层，被后端服务调用。
- 本服务不管理 OSS 资源，不管理后端资源表。
- 本服务维护自己的 pgvector 向量数据库。
- 以图搜图是 `asset_vector` capability，不是一个单独 `job_type`。
- capability 需要垂直收拢，方便模板项目复用时整体拆卸。

没有确认，不能当成事实：

- 不能假设每个资源一定有标签。
- 不能假设每个资源一定已有 AI 描述。
- 不能假设后端当前已经稳定持有中英双语描述字段。
- 不能假设 `score >= 0.90` 就等价于产品语义上的“相似度大于 90”。

因此本方案使用 `text_payload` 作为对外合同字段：

```text
text_payload = 后端本次请求提供的最终检索文本
```

`text_payload` 可以来自 AI 描述、人工描述、资源名称、标签、分类名或其他业务字段，但这些组成规则属于后端资源服务和产品规则。本服务不解析这些业务字段，也不假设它们必然存在。

相关资料：

- 阿里云多模态向量文档：[`../aliyun/阿里云文档_文本与多模态向量化.md`](../aliyun/阿里云文档_文本与多模态向量化.md)
- 阿里云重排序文档：[`../aliyun/重排序.md`](../aliyun/重排序.md)
- 原始草稿：[`../以图搜图/向量检索服务技术方案-v2-通义方案.md`](../以图搜图/向量检索服务技术方案-v2-通义方案.md)
- 新 CC 库 4.6：[`../以图搜图/新cc库-基础搭建/新cc库-基础搭建.md`](../以图搜图/新cc库-基础搭建/新cc库-基础搭建.md)
- `asset_vector` 图片打标 Job 计划：[`asset-image-tagging-job-architecture.md`](asset-image-tagging-job-architecture.md)

## 需求翻译

表面诉求：

```text
使用阿里云通义多模态向量 API，实现素材库以图搜图，并梳理需要提供给业务后端的接口。
```

真实工程需求：

```text
后端资源服务需要一个可调用的 AI 向量能力层。
它把资源图片和检索文本同步到 embedding-service。
embedding-service 生成并维护向量 read model。
搜索时后端传候选资源 ID，本服务只返回向量相似排序。
```

这不是“让 embedding-service 接管资源管理”。它更接近一个可重建的 read model projection：

```text
后端资源表是 write model / source of truth
asset_vector_entries 是 read model / projection
```

资源更新后的同步责任也因此明确：

- 后端知道资源何时新增、替换、删除、下架。
- 后端知道哪些字段应该影响检索。
- 后端负责在这些变更发生后调用本服务同步向量。
- 本服务负责幂等写入、版本防旧覆盖、批量部分失败、对账和明确错误。

## 范围边界

本 capability 负责：

- 图片 URL 读取和输入校验。
- 文本 `text_payload` 输入校验。
- 调用 DashScope 多模态 embedding。
- 将资源主检索向量写入 pgvector。
- 资源向量增量同步。
- 资源向量批量同步和全量重建。
- 单个或批量删除向量。
- 搜索时在后端给定候选集内排序。
- 向量库正向和反向对账。
- Job、billing、callback、日志和错误语义接入本项目现有框架。

本 capability 不负责：

- 不做素材详情拼装。
- 不管理 OSS 对象上传、删除、迁移或签名。
- 不维护后端资源表。
- 不判断用户权限、项目权限、团队权限。
- 不维护标签、分类、审核状态、上下架状态、收藏状态。
- 不做普通 ID / 名称搜索。
- 不维护前端搜索状态、搜索历史、输入框清空行为、拖拽 UI 状态或展示排序。
- 向量索引链路不负责 AI 打标。打标产出的描述或标签只有在后端确认为 `text_payload` 后，才进入向量索引。
- 不暴露完整向量、完整图片二进制、base64 大 payload 或 provider 原始响应。

## 核心设计决策

| 决策 | 方案 | 原因 |
|---|---|---|
| 能力边界 | `asset_vector` capability | 以图搜图、文搜图、描述搜索共享同一套向量投影 |
| 资源事实源 | 后端资源服务 | 本服务不管理资源表、OSS 生命周期和业务字段 |
| 向量事实源 | `asset_vector_entries` | 本服务维护自己的 pgvector read model |
| 外部资源 ID | `resource_id` | 每个资源必有，搜索结果以它为主 |
| 组资源 ID | `group_id` nullable | 组资源才有，用于后端聚合和展示 |
| 写入形态 | 异步 Job | 模型调用、图片下载、批量处理会受限流和失败影响 |
| 资源写删接口风格 | batch-first | 单资源只是数组里 1 个 item，减少接口数量和对接分支 |
| 搜索形态 | 同步 HTTP route | 用户搜索需要低延迟，异步轮询不适合主交互 |
| 同步语义 | 后端主动提交 `batch_upsert` / `batch_delete` | 后端知道资源变化，本服务不轮询后端资源库 |
| 搜索候选集 | 用户态搜索要求 `candidate_resource_ids` | 权限、分类、标签、收藏和状态过滤必须在后端完成 |
| 向量存储 | pgvector 单表 + `vector_kind='asset'` | 当前落地合同中每个资源维护一条主检索向量，避免把图像、文本、融合三路召回复杂度暴露给后端 |
| 版本防护 | `source_revision` CAS | 防止旧 Job 晚完成后覆盖新素材向量 |
| 模型接入 | DashScope 原生多模态 embedding adapter | OpenAI-compatible adapter 不支持通义多模态输入 |
| 文本输入 | `text_payload` caller-owned | 不假设标签或 AI 描述是资源必有字段 |

## 一次对接的最小生产闭环

本方案不按“能跑的 MVP”和“后续增强”拆合同，而是按“后端只对接一次”的目标收口。接口数量不继续扩展，但下列 5 个能力面都属于完整生命周期闭环：

| 能力面 | 承载接口或 Job | 闭环作用 |
|---|---|---|
| 建库 / 更新 / 重建 | `asset_vector_batch_upsert` | 后端把资源事实投影成本服务的 `asset` 向量 |
| 删除 / 下架 / 不可检索清理 | `asset_vector_batch_delete` | 后端明确删除不应继续检索的向量投影 |
| 用户搜索 | `POST /api/v1/ai-jobs/vector-search` | 本服务只在后端候选集内做相似度排序 |
| 正向对账 | `POST /api/v1/ai-jobs/vector-assets:exists` | 后端发现应存在但缺失的向量 |
| 反向对账 | `GET /api/v1/ai-jobs/vector-assets/ids` | 后端发现本服务多出的幽灵向量 |

这 5 个能力面已经覆盖“同步、搜索、删除、对账、重建”的服务侧闭环。继续收口的重点不是砍接口，而是把合同边界写硬：

- `asset_vector_batch_upsert` 同时覆盖新增、更新、重传、批量导入、批量修复和模型重建，不再拆 `create`、`update`、`rebuild`。
- `asset_vector_batch_delete` 同时覆盖删除、永久下架、批量清理和资源不可检索，不再拆 `offline`、`disable`、`remove`。
- `vector-search` 必须由后端传入 `candidate_resource_ids`，本服务不做权限、分类、标签、收藏、上下架和项目空间过滤。
- `exists` 和 `ids` 不属于用户搜索链路，但属于生产运维闭环；没有它们，漏建和幽灵向量只能靠人工 SQL 排查。

不再增加下列接口：

```text
单资源 upsert
单资源 delete
单资源 get
资源详情查询
标签查询
搜索历史接口
收藏接口
上下架接口
普通关键词搜索接口
图片打标接口
```

单资源场景统一使用 batch 接口的单元素数组表达。

## 当前 Baseline

本项目当前已经具备：

- FastAPI 统一 HTTP envelope、Bearer auth、`X-Request-ID` 和 caller id 边界。
- 标准异步 Job 入口：`POST /api/v1/ai-jobs/jobs`。
- Job 聚合、Attempt、Dispatch outbox、Callback outbox、recovery 和 billing ledger。
- AI provider / adapter / model catalog / pricing registry 分层。
- `embedding` 模型类型和 `openai_compatible_embeddings` 文本向量 adapter。
- `dashscope` provider 注册。
- 本地 compose PostgreSQL 可使用 pgvector 镜像。

仍缺少：

- `asset_vector` capability package。
- pgvector Alembic migration：`CREATE EXTENSION IF NOT EXISTS vector`、向量表和索引。
- DashScope 原生多模态 embedding adapter。
- 多模态 embedding gateway + ledger + usage/cost 归一。
- `asset_vector_batch_upsert`、`asset_vector_batch_delete` 等 batch-first Job。
- `vector-search`、`vector-assets:exists`、`vector-assets/ids` 等同步 HTTP route。
- 对外正式合同文档和 contract tests。

## Capability 可拆卸边界

因为本仓库是模板项目，`asset_vector` 应按 capability 垂直收拢。运行时合同仍分 Job 和 HTTP route，但代码组织尽量放在同一个子目录。

推荐目录：

```text
app/features/asset_vector/
  __init__.py
  constants.py
  errors.py
  models.py
  repository.py
  schemas.py
  service.py
  routes.py
  ai.py
  register.py
  jobs/
    __init__.py
    batch_upsert.py
    batch_delete.py
    register.py
```

职责说明：

| 文件 | 职责 |
|---|---|
| `schemas.py` | 同步 route 和 Job params/result 的 Pydantic schema |
| `models.py` | SQLAlchemy model；只表达 `asset_vector` 自己的表 |
| `repository.py` | pgvector 写入、删除、状态查询和相似度查询 |
| `service.py` | 同步搜索、状态查询、对账、删除的应用服务 |
| `routes.py` | FastAPI route；不直接调用 provider，不直接拼 SQL |
| `ai.py` | 调用通用 AI gateway 的薄封装，组织 embedding input item |
| `errors.py` | capability 自己的错误码注册 |
| `register.py` | 注册 router、operation、error、schema 的能力入口 |
| `jobs/` | 异步 Job executor 和 Job package 注册 |

全局只保留薄胶水：

```text
app/jobs/types/register.py
  -> 注册 app.features.asset_vector.jobs.register.PACKAGE

app/api/operations.py
  -> 注册 vector-search / vector-assets operation spec

app/main.py 或 app/api/routes 聚合入口
  -> include app.features.asset_vector.routes.router

alembic/versions/
  -> asset_vector_xxx_create_pgvector_tables.py
```

拆卸时应能主要删除：

```text
app/features/asset_vector/
alembic/versions/*asset_vector*
docs/api/asset-vector-api.md
docs/current/asset-vector.md
docs/plans/image-search-tongyi-pgvector-architecture.md
```

再从全局注册入口移除少量胶水即可。不要把同步 route、repository 和 service 都塞进 `app/jobs/types/asset_vector/`，因为同步搜索不是 Job type。

## 数据模型

### pgvector extension

Alembic migration 第一阶段：

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

如果生产数据库不允许业务用户创建 extension，应在部署前由 DBA 或平台初始化完成；应用 migration 应 fail-fast，不要静默跳过。

### 主表：`asset_vector_entries`

当前落地合同建议每个资源维护一条 `asset` 主检索向量。`tongyi-embedding-vision-flash-2026-03-06` 是多模态向量模型，输入可以是图片、文本，或图片加文本的融合输入；但对外合同不让后端选择 `image/text/fused` 三套向量，而是统一投影成资源主向量。

`vector_kind` 仍保留在表结构中，但当前落地合同固定为 `asset`。它的作用是给未来模型评测后确有必要的多路向量留扩展位，不作为当前调用方必须理解的分支。

```sql
CREATE TABLE asset_vector_entries (
  resource_id text NOT NULL,
  group_id text,
  vector_kind text NOT NULL DEFAULT 'asset',
  embedding vector(768) NOT NULL,
  embedding_input_kind text NOT NULL,
  model_id text NOT NULL,
  provider text NOT NULL,
  provider_model text NOT NULL,
  dimension integer NOT NULL,
  source_revision bigint NOT NULL,
  source_version text,
  source_hash text NOT NULL,
  input_fingerprint text NOT NULL,
  image_url_hash text,
  text_payload_hash text,
  route_config_hash text NOT NULL,
  indexed_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (resource_id, vector_kind)
);
```

字段语义：

| 字段 | 说明 |
|---|---|
| `resource_id` | 后端资源 ID，所有资源必有；搜索结果原样返回 |
| `group_id` | 组资源 ID；非组资源为空 |
| `vector_kind` | 当前固定为 `asset`，表示资源主检索向量 |
| `embedding` | pgvector 向量，当前固定 `vector(768)` |
| `embedding_input_kind` | `image`、`text`、`image_text`；记录本次主向量由什么输入生成 |
| `model_id` | 本服务模型 ID |
| `provider` | `dashscope` |
| `provider_model` | provider 真实模型名 |
| `dimension` | 向量维度，当前必须等于 768 |
| `source_revision` | 后端提供的可比较版本；用于防旧覆盖，建议正式合同必填 |
| `source_version` | 后端可读版本，用于审计和排查 |
| `source_hash` | 本次输入内容 hash，覆盖 image URL/hash、text_payload、`asset` 向量语义和模型 |
| `input_fingerprint` | 本次 embedding 请求指纹，含输入类型、模型和输入摘要 |
| `image_url_hash` | OSS 公网 URL 的 hash；不把 OSS URL 当成本服务资源事实源 |
| `text_payload_hash` | `text_payload` 的 hash；不要求保存完整检索文本 |
| `route_config_hash` | 模型路由配置 hash，用于模型切换和对账 |
| `indexed_at` | 本向量首次或最近成功索引时间 |
| `updated_at` | 行更新时间 |

约束建议：

```sql
ALTER TABLE asset_vector_entries
  ADD CONSTRAINT asset_vector_entries_vector_kind_check
  CHECK (vector_kind = 'asset');

ALTER TABLE asset_vector_entries
  ADD CONSTRAINT asset_vector_entries_input_kind_check
  CHECK (embedding_input_kind IN ('image', 'text', 'image_text'));

ALTER TABLE asset_vector_entries
  ADD CONSTRAINT asset_vector_entries_dimension_check
  CHECK (dimension = 768);

ALTER TABLE asset_vector_entries
  ADD CONSTRAINT asset_vector_entries_source_hash_check
  CHECK (source_hash ~ '^sha256:[0-9a-f]{64}$');
```

索引建议：

```sql
CREATE INDEX asset_vector_entries_resource_id_idx
  ON asset_vector_entries (resource_id);

CREATE INDEX asset_vector_entries_group_id_idx
  ON asset_vector_entries (group_id)
  WHERE group_id IS NOT NULL;

CREATE INDEX asset_vector_entries_updated_at_idx
  ON asset_vector_entries (updated_at);
```

候选集检索是当前主路径。如果业务后端每次传入几百到几千个 `candidate_resource_ids`，可以先用精确计算：

```sql
SELECT resource_id, group_id, 1 - (embedding <=> :query_embedding) AS score
FROM asset_vector_entries
WHERE vector_kind = 'asset'
  AND resource_id = ANY(:candidate_resource_ids)
ORDER BY embedding <=> :query_embedding
LIMIT :top_k;
```

同时为大候选集或内部全库检索准备 partial HNSW index：

```sql
CREATE INDEX asset_vector_entries_asset_hnsw
  ON asset_vector_entries
  USING hnsw (embedding vector_cosine_ops)
  WHERE vector_kind = 'asset';
```

### 不建议保存资源副本

不建议在 `asset_vector_entries` 中保存完整 OSS URL、完整 `text_payload`、标签列表、分类、审核状态或收藏状态。

原因：

- 后端资源表才是这些字段的事实源。
- 本服务保存副本会制造一致性债务。
- 全量重建时应由后端重新导出 manifest，而不是让本服务依赖旧 URL 自行重建。
- 日志和账单也不应记录完整图片、完整文本、完整向量或 provider raw payload。

如果排查确实需要定位输入，应存 hash、长度、content type、source_revision、model_id 和 request_id。

### 组资源建模

对外合同以 `resource_id` 为主，因为每个资源都有 `resource_id`。`group_id` 只作为可选辅助字段。

当前推荐：

```text
向量行粒度 = resource_id + vector_kind，其中 vector_kind 当前固定为 asset
搜索结果粒度 = resource_id
group_id = 返回给后端做聚合展示的辅助字段
```

如果某类组资源使用“一组一个描述”，后端有两种选择：

| 方案 | 做法 | 适用 |
|---|---|---|
| 推荐：组描述复制到组内资源 | 对组内每个 `resource_id` 提交同一份 `text_payload` 和同一 `group_id` | 搜索仍返回资源，后端可按组聚合 |
| 延后：组级向量 | 单独引入 `group_vector_entries` 或 `entity_scope=group` | 产品明确要求描述搜索直接返回组 |

当前不建议同时支持资源级和组级两套主键，否则候选集、删除、对账和搜索结果粒度都会复杂化。

## 资源同步设计

资源同步是本方案必须显式覆盖的主流程。后端资源库发生变化后，向量库不会自动知道变化；必须由后端调用同步接口。

### 触发时机

后端应在这些事件发生后同步：

| 后端事件 | 推荐动作 | 向量影响 |
|---|---|---|
| 新增图片资源 | `asset_vector_batch_upsert` | `items` 可只放 1 条；写入 `asset` 主检索向量 |
| 替换资源图片 | `asset_vector_batch_upsert` | `items` 可只放 1 条；重新生成并覆盖 `asset` 主检索向量 |
| 后端认为检索文本变化 | `asset_vector_batch_upsert` | `items` 可只放 1 条；如果该资源使用文本参与检索，则重新生成 `asset` 主检索向量 |
| 新增只有检索文本的资源 | `asset_vector_batch_upsert` | `items` 可只放 1 条；写入 text-only 的 `asset` 主检索向量 |
| 删除、永久下架或不可检索资源 | `asset_vector_batch_delete` | `resource_ids` 可只放 1 个；删除对应 `asset` 向量 |
| 批量导入或迁移 | `asset_vector_batch_upsert` | 分批写入 `asset` 主检索向量 |
| 模型切换或维度切换 | 新表或新模型版本 + batch rebuild | 不原地改旧向量维度 |

“检索文本变化”由后端判断。本服务不猜标签、描述、标题是否存在，也不猜这些字段是否应该进入搜索。

### 增量同步

增量同步仍走 batch-first Job，单资源新增或更新只是 `items` 数组长度为 1：

```text
后端写资源表成功
  -> 生成或递增 source_revision
  -> 准备 OSS public_url 和可选 text_payload
  -> 提交 asset_vector_batch_upsert Job，items = [当前资源]
  -> Job 成功后后端可标记向量已同步
```

增量同步应使用 `client_request_id` 做请求幂等，使用 `source_revision` 做并发防旧覆盖。

### 批量同步

批量同步用于存量导入、迁移、批量修复和模型重建。

小批量可以直接传 `items`。大批量应由后端导出 manifest 到 OSS，再传 `manifest_ref`，避免把大数组塞进 API 请求。

manifest 行建议：

```json
{
  "resource_id": "res_001",
  "group_id": "grp_001",
  "source_revision": 12,
  "source_version": "12",
  "image": {
    "public_url": "https://assets.example.com/res_001.png",
    "content_type": "image/png",
    "sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
  },
  "text_payload": "后端确认后的最终检索文本"
}
```

批量同步必须支持部分成功，但不能吞错：

```text
一条失败
  -> 记录该 item 的失败原因
  -> 继续或停止取决于 continue_on_item_error
  -> Job result 或 result_ref 返回 succeeded / skipped / failed 明细
```

### 全量重建

全量重建推荐由后端重新导出当前资源全集 manifest。

```text
后端导出当前有效资源全集
  -> 提交 asset_vector_batch_upsert
  -> 本服务按 manifest 重建向量
  -> 后端用 exists / ids 做正反向对账
  -> 后端明确调用删除接口清理不应存在的向量
```

当前不建议 `batch_upsert` 自动删除 manifest 中不存在的历史向量。自动 prune 是破坏性操作，应独立设计成显式任务，必须带 `sync_run_id`、dry-run 结果和确认机制。

### 对账同步

正向对账：

```text
后端拿自己资源表中的 resource_id 列表
  -> 调 POST /vector-assets:exists
  -> 找出本服务缺失的向量
  -> 对缺失资源补提 asset_vector_batch_upsert
```

反向对账：

```text
后端分页拉本服务已索引 resource_id
  -> 调 GET /vector-assets/ids
  -> 回后端资源表判断哪些已经删除、下架或不应存在
  -> 显式调用删除接口清理
```

本服务不主动查询后端资源表，也不主动判定某个向量是否应该删除。

## 模型与 AI Gateway

当前模型：

```text
model_id: tongyi-embedding-vision-flash-2026-03-06
provider: dashscope
dimension: 768
capability: embeddings
```

已知约束：

| 项 | 方案取值 | 影响 |
|---|---|---|
| 向量维度 | 768 | 表字段固定为 `vector(768)`，维度变化不能原地切换 |
| 文本长度 | 1,024 Token | `text_payload` 必须由后端控制长度或由本服务明确拒绝 |
| 图片大小 | 建议不超过 5 MB，最大 10 MB | 后端应传预览图 OSS URL，不推荐原图 |
| 图片输入 | OSS 公网 URL | 已确认公网可读，当前不需要签名 URL 管理 |
| 单次内容元素 | 总数不超过 20 | 批量 Job 应分批调用 provider |
| 资源主向量 | 图片、文本或同一个 content 对象内的 `text` + `image` | 对外统一写入 `asset` 主检索向量 |
| OpenAI compatible | 不支持多模态独立/融合向量 | 需要 DashScope 原生 adapter |

新增 adapter：

```text
app/ai/adapters/dashscope_multimodal_embeddings_adapter.py
```

新增 gateway：

```text
generate_multimodal_embeddings_with_ledger()
```

gateway 必须复用：

- `ModelGate`
- `ProviderGateway`
- `UsageLedgerWriter`
- `TypedPricingResolver`
- provider failure 分类
- request/response hash

索引 Job 调用时：

```text
scope_type = "job"
scope_id = root_job_id
operation = "asset_vector.embed"
```

同步搜索实时生成 query embedding 时：

```text
scope_type = "sync_api"
scope_id = request_id
operation = "asset_vector.search_embed_query"
```

ledger 只能记录输入摘要、hash、content_type、大小、模型和路由信息，不记录完整图片、完整 base64、完整 `text_payload` 或完整向量。

## 模型目录和价格

计划在 `app/ai/catalog/models.yaml` 增加：

```yaml
- id: tongyi-embedding-vision-flash-2026-03-06
  enabled: true
  public:
    name: Tongyi Embedding Vision Flash 2026-03-06
    provider: dashscope
    model_type: embedding
    capabilities:
      - embeddings
    input_media_types:
      - text/plain
      - image/jpeg
      - image/png
      - image/webp
    output_media_types:
      - application/vnd.embedding-vector
    limits:
      embedding_dimension: 768
      text_tokens: 1024
      image_size_mb_recommended: 5
      image_size_mb_max: 10
      input_items_max: 20
    features:
      supports_multimodal_embedding: true
      supports_multimodal_fusion_input: true
    parameters:
      - dimension
  execution:
    routes:
      embeddings:
        adapter: dashscope_multimodal_embeddings
        provider: dashscope
        provider_model: tongyi-embedding-vision-flash-2026-03-06
        adapter_model: tongyi-embedding-vision-flash-2026-03-06
        pricing_ref: dashscope:tongyi-embedding-vision-flash-2026-03-06@2026-03-06
        requires_env:
          - DASHSCOPE_API_KEY
          - DASHSCOPE_NATIVE_BASE_URL
        embedding:
          dimension: 768
```

同时在 `app/ai/pricing/pricing.yaml` 增加对应规则。

如果 provider 无法返回可归一化用量，有两种可接受方案：

| 方案 | 说明 |
|---|---|
| 定义明确的不可计费状态 | ledger 标记 `not_billable` 或 `usage_unavailable`，不伪造 0 成本 |
| 定义按请求/按 item 计费 | pricing registry 明确计量单位，不依赖 token |

不允许静默写入 0 成本并标记为最终可信费用。

## 异步 Job 合同草案

正式实现后，这部分应迁移到 `docs/api/asset-vector-api.md`。在计划阶段，它用于评估后端是否能对接。

所有 Job 仍使用统一入口：

```http
POST /api/v1/ai-jobs/jobs
GET /api/v1/ai-jobs/jobs/{job_id}
GET /api/v1/ai-jobs/jobs/{job_id}/billing
```

### `asset_vector_batch_upsert`

用于资源新增、资源更新、存量导入、批量修复、批量增量同步和模型重建。它是唯一写入类 Job，单资源同步也使用这个 Job，`items` 数组长度为 1 即可。

小批量可以直接传 items：

```json
{
  "client_request_id": "asset-vector-upsert:res_001:v12",
  "job_type": "asset_vector_batch_upsert",
  "job_params": {
    "items": [
      {
        "resource_id": "res_001",
        "group_id": "grp_001",
        "source_revision": 12,
        "source_version": "12",
        "image": {
          "public_url": "https://assets.example.com/preview/res_001.png",
          "content_type": "image/png",
          "sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
        },
        "text_payload": "后端确认后的最终检索文本"
      }
    ],
    "continue_on_item_error": true
  }
}
```

item 字段规则：

| 字段 | 必需 | 说明 |
|---|---|---|
| `resource_id` | 是 | 后端资源 ID，所有资源必有 |
| `group_id` | 否 | 组资源 ID，非组资源不传 |
| `source_revision` | 是 | 后端资源版本，用于防旧覆盖 |
| `source_version` | 推荐 | 人类可读版本或内容版本 |
| `image.public_url` | 条件必需 | OSS 公网可读 URL；`image.public_url` 和 `text_payload` 至少提供一个 |
| `image.sha256` | 推荐 | 用于校验下载内容 |
| `text_payload` | 条件必需 | 后端拼好的最终检索文本；`image.public_url` 和 `text_payload` 至少提供一个 |

主检索向量生成规则：

| 输入 | 写入结果 |
|---|---|
| 只有 `image.public_url` | 生成 image-only 的 `asset` 向量 |
| 只有 `text_payload` | 生成 text-only 的 `asset` 向量 |
| 同时有 `image.public_url` 和 `text_payload` | 生成 image+text 融合输入的 `asset` 向量 |

大批量应传 manifest ref，不把超大列表塞进 Job 请求：

```json
{
  "client_request_id": "asset-vector-rebuild:2026-08-29",
  "job_type": "asset_vector_batch_upsert",
  "job_params": {
    "manifest_ref": {
      "storage": "oss_object",
      "bucket": "cms-assets",
      "region": "cn-shanghai",
      "key": "manifests/asset-vector-rebuild-2026-08-29.jsonl",
      "content_type": "application/x-ndjson",
      "sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    },
    "continue_on_item_error": true
  }
}
```

结果：

```json
{
  "total": 1,
  "succeeded": 1,
  "skipped": 0,
  "failed": 0,
  "items": [
    {
      "resource_id": "res_001",
      "group_id": "grp_001",
      "source_revision": 12,
      "source_version": "12",
      "indexed": {
        "vector_kind": "asset",
        "embedding_input_kind": "image_text",
        "source_hash": "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
        "indexed_at": "2026-08-29T10:00:00Z"
      },
      "skipped": null,
      "failed": null
    }
  ],
  "result_ref": null
}
```

大批量结果可把 `items` 明细写入 `result_ref`，避免 Job result 过大：

```json
{
  "total": 10000,
  "succeeded": 9980,
  "skipped": 10,
  "failed": 10,
  "items": [],
  "result_ref": {
    "storage": "oss_object",
    "bucket": "cms-assets",
    "region": "cn-shanghai",
    "key": "results/asset-vector-batch-2026-08-29.json",
    "content_type": "application/json",
    "sha256": "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
  }
}
```

旧版本写入以 item 级 skipped 表达：

```json
{
  "resource_id": "res_001",
  "indexed": null,
  "skipped": {
    "vector_kind": "asset",
    "reason": "stale_source_revision",
    "existing_source_revision": 13,
    "incoming_source_revision": 12
  },
  "failed": null
}
```

执行流程：

```text
asset_vector_batch_upsert executor
  -> validate params
  -> load inline items or manifest items
  -> for each item validate resource_id / source_revision / image-or-text input
  -> validate OSS public_url / content hash / image bytes when image is provided
  -> validate text_payload when text is provided
  -> build one asset embedding input from image, text, or image+text
  -> generate_multimodal_embeddings_with_ledger()
  -> repository.upsert_vector_entries()
  -> return item-level indexed/skipped/failed result
```

当前可先用单 executor 内部分批处理。只有当批量规模、部分失败恢复和进度展示要求证明有必要时，再升级为 root/child workflow：

```text
asset_vector_batch_upsert root
  -> chunk child: asset_vector_batch_upsert_chunk
  -> join child: asset_vector_batch_join
```

### `asset_vector_batch_delete`

用于批量删除向量，适合资源批量下架、项目删除、导入回滚或业务库清理。

请求：

```json
{
  "client_request_id": "asset-vector-delete:project:p1:2026-08-29",
  "job_type": "asset_vector_batch_delete",
  "job_params": {
    "resource_ids": ["res_001", "res_002"]
  }
}
```

规则：

- 删除不存在的 `resource_id` 视为成功，保证删除事件可重试。
- 当前删除该 `resource_id` 的 `asset` 主检索向量。
- 大批量删除同样可用 manifest ref。
- 批量删除不查询后端资源状态，调用方必须保证删除意图来自后端事实源。

## 同步 HTTP 合同草案

同步 route 不经过 Job kernel，但仍使用统一 HTTP envelope、认证、`OperationSpec`、request_id 和错误 envelope。

推荐 route 放在：

```text
app/features/asset_vector/routes.py
```

并在 `app/api/operations.py` 注册：

```text
search_vector_assets
check_vector_assets_exist
list_vector_asset_ids
```

### 搜索：`POST /api/v1/ai-jobs/vector-search`

用途：在调用方给定候选资源内做向量相似排序。

请求：

```json
{
  "query": {
    "text": "蓬松的棕色卷发"
  },
  "candidate_resource_ids": ["res_001", "res_002", "res_003"],
  "exclude_resource_ids": [],
  "top_k": 50,
  "min_score": 0.0
}
```

`query` 当前建议支持三类：

| query | 场景 | 行为 |
|---|---|---|
| `text` | 文搜图、描述搜索 | 同步生成 query embedding |
| `image.public_url` | 上传图搜图库相似图 | 校验图片后同步生成 query embedding |
| `resource_id` | 库内素材找相似素材 | 直接读取已有 resource 的 `asset` 向量，不额外调用模型 |

请求约束：

- `query` 必须三选一，不能同时传多个。
- 用户态搜索必须传 `candidate_resource_ids`，且数量应有上限。
- `exclude_resource_ids` 用于库内搜图排除自身。
- `top_k` 必须小于等于服务配置上限。
- `min_score` 只做过滤，不应作为业务相似判断真理。
- 普通 ID / 名称搜索不调用本接口。

响应：

```json
{
  "results": [
    {
      "resource_id": "res_002",
      "group_id": "grp_001",
      "score": 0.8321,
      "vector_kind": "asset",
      "embedding_input_kind": "image_text",
      "source_revision": 13,
      "source_version": "13"
    }
  ],
  "query_kind": "text",
  "model_id": "tongyi-embedding-vision-flash-2026-03-06",
  "dimension": 768,
  "score_semantics": "1 - cosine_distance"
}
```

分数语义：

```text
score = 1 - cosine_distance
越大越相似
```

调用方不能把 `score` 当成百分比。上线阈值必须由真实素材评测集反推。

不传 `candidate_resource_ids` 的全库检索当前不开放给用户态搜索。如果要给内部工具开放，应使用显式字段和更严格权限：

```json
{
  "allow_global_search": true
}
```

### 批量向量状态检查：`POST /api/v1/ai-jobs/vector-assets:exists`

用途：业务后端正向对账，发现缺失向量并补建。单资源状态检查也使用这个接口，`resource_ids` 数组长度为 1 即可。

请求：

```json
{
  "resource_ids": ["res_001", "res_002"]
}
```

响应：

```json
{
  "items": [
    {
      "resource_id": "res_001",
      "group_id": "grp_001",
      "exists": true,
      "vector_kind": "asset",
      "embedding_input_kind": "image_text",
      "model_id": "tongyi-embedding-vision-flash-2026-03-06",
      "dimension": 768,
      "source_revision": 12,
      "indexed_at": "2026-08-29T10:00:00Z"
    },
    {
      "resource_id": "res_002",
      "group_id": null,
      "exists": false,
      "vector_kind": "asset",
      "embedding_input_kind": null,
      "model_id": null,
      "dimension": null,
      "source_revision": null,
      "indexed_at": null
    }
  ]
}
```

不返回 `embedding` 原始向量。

### 分页拉取已索引 ID：`GET /api/v1/ai-jobs/vector-assets/ids`

用途：业务后端反向对账，发现本服务存在但业务库已删除或不应存在的向量。

请求参数：

```text
cursor=
limit=1000
```

响应：

```json
{
  "items": [
    {
      "resource_id": "res_001",
      "group_id": "grp_001",
      "vector_kind": "asset",
      "embedding_input_kind": "image_text",
      "source_revision": 12,
      "updated_at": "2026-08-29T10:00:00Z"
    }
  ],
  "next_cursor": "..."
}
```

## 后端对接职责

资源新增或更新时，业务后端提供：

| 字段 | 必需 | 说明 |
|---|---|---|
| `resource_id` | 是 | 资源业务 ID |
| `group_id` | 否 | 组资源 ID |
| `source_revision` | 是 | 可比较版本；用于防旧覆盖 |
| `source_version` | 推荐 | 人类可读版本或内容版本 |
| `image.public_url` | 条件必需 | OSS 公网可读 URL；与 `text_payload` 至少提供一个 |
| `text_payload` | 条件必需 | 后端确认后的最终检索文本；与 `image.public_url` 至少提供一个 |
| `callback` | 可选 | 接收 Job 终态 |

搜索时，业务后端负责：

- 根据用户、项目、团队、素材状态、分类、标签、收藏等条件先过滤候选资源。
- 把候选资源 ID 作为 `candidate_resource_ids` 传给本服务。
- 接收 `resource_id + group_id + score` 后回业务库拼详情。
- 自己决定组资源聚合、展示排序、阈值策略、空结果文案和搜索历史。

对账时，业务后端负责：

- 用 `vector-assets:exists` 找缺失向量。
- 用 `vector-assets/ids` 找幽灵向量。
- 资源删除时提交 `asset_vector_batch_delete` Job；单资源删除也传 `resource_ids` 单元素数组。
- 当后端认为某次资源变更影响检索时，提交 `asset_vector_batch_upsert` Job。

## 模拟对接过程

下面用一次后端接入过程模拟完整生命周期。这里的“同步”指后端把资源事实投影到本服务的向量库，不是让本服务接管资源管理。

### 第 0 步：双方确认合同

```text
后端确认：
  resource_id 必有
  group_id 仅组资源有
  OSS public_url 公网可读
  source_revision 可比较
  text_payload 由后端按业务规则决定是否提供
  image.public_url 和 text_payload 至少提供一个

embedding-service 确认：
  只读取 public_url
  只保存向量和必要元信息
  不保存资源详情
  不判断权限 / 分类 / 标签 / 收藏
  不生成资源描述
```

### 第 1 步：存量资源首次建库

```text
后端资源表
  -> 导出当前有效资源 manifest
  -> 每行包含 resource_id / group_id / source_revision / public_url / text_payload
  -> 提交 asset_vector_batch_upsert

embedding-service worker
  -> 分批读取 manifest
  -> 每个 item 生成一条 asset 主检索向量
  -> 按 resource_id + vector_kind='asset' upsert 到 pgvector
  -> 输出 succeeded / skipped / failed / result_ref

后端
  -> 读取 result_ref
  -> 对 failed item 修复后重提 batch_upsert
```

这一阶段完成后，向量库具备被搜索的基础数据。

### 第 2 步：增量资源持续同步

```text
后端发生资源新增或更新
  -> 写后端资源表
  -> 递增 source_revision
  -> 提交 asset_vector_batch_upsert，items 可以只放 1 条

embedding-service
  -> 读取 OSS public_url 或 text_payload
  -> 生成新向量
  -> 如果 incoming.source_revision >= existing.source_revision，则覆盖
  -> 如果 incoming.source_revision < existing.source_revision，则 skipped
```

这一步解决资源更新后的向量同步问题。后端资源表仍是事实源，向量库只是最终一致的投影。

### 第 3 步：用户发起以图搜图

```text
前端
  -> 用户上传图片或选择已有资源

后端
  -> 如果是上传图片，生成可控 public_url
  -> 如果是已有资源，直接使用 resource_id
  -> 先按权限 / 分类 / 标签 / 状态 / 收藏筛出 candidate_resource_ids
  -> 调 POST /vector-search

embedding-service
  -> query.image.public_url：实时生成查询向量
  -> query.resource_id：读取已索引 asset 向量
  -> 在 candidate_resource_ids 内按 pgvector 相似度排序
  -> 返回 resource_id + group_id + score

后端
  -> 回资源表查详情
  -> 按 group_id 做必要聚合
  -> 返回前端展示
```

这一阶段就是 4.6 图片搜索的服务侧实现。拖拽、按钮状态、清空搜索条件属于前端；候选集和详情拼装属于后端。

### 第 4 步：用户发起描述搜索或文搜图

```text
前端
  -> 用户输入文本

后端
  -> 先筛 candidate_resource_ids
  -> 调 POST /vector-search，query.text

embedding-service
  -> 实时把 query.text 编码成向量
  -> 查 asset 主检索向量
  -> 返回 resource_id + group_id + score

后端
  -> 拼详情 / 聚合组 / 应用展示阈值
```

文搜图和描述搜索在服务侧都调用同一个 `vector-search`，差异由后端传入的 `candidate_resource_ids` 和后端展示规则决定。本服务只把 `query.text` 编码成向量，并和候选资源的 `asset` 主向量比较。

### 第 5 步：资源删除或下架

```text
后端资源删除 / 永久下架 / 批量清理
  -> 提交 asset_vector_batch_delete
  -> 单资源删除也用 resource_ids = [当前资源]

embedding-service
  -> 按 resource_id 删除 asset 主检索向量
  -> 不查询后端资源表
  -> 删除不存在的 resource_id 仍返回成功明细
```

删除同样由后端主动通知。本服务不主动判断某个资源是否已经下架。

### 第 6 步：定期对账和修复

```text
正向对账：后端认为应该存在
  后端 resource_id 列表
    -> POST /vector-assets:exists
    -> 找 exists=false 的资源
    -> 补提 asset_vector_batch_upsert

反向对账：本服务认为已经存在
  GET /vector-assets/ids
    -> 后端回查资源表
    -> 找已删除 / 已下架 / 不应存在的 resource_id
    -> 提交 asset_vector_batch_delete
```

对账让向量库不依赖“每次事件都百分百成功送达”。即使某次同步失败或漏发，也可以通过周期任务修复。

## 全生命周期覆盖判断

按上述设计，可以实现以图搜图全生命周期，但前提是后端按合同承担资源事实源职责。

| 生命周期阶段 | 是否覆盖 | 承载接口 |
|---|---|---|
| 存量资源建库 | 覆盖 | `asset_vector_batch_upsert` + `manifest_ref` |
| 新资源入库 | 覆盖 | `asset_vector_batch_upsert`，`items` 单元素 |
| 资源图片更新 | 覆盖 | `asset_vector_batch_upsert`，重新生成 `asset` 主向量 |
| 检索文本更新 | 覆盖 | `asset_vector_batch_upsert`，重新生成 `asset` 主向量 |
| 以图搜图 | 覆盖 | `POST /vector-search`，`query.image.public_url` 或 `query.resource_id` |
| 文搜图 | 覆盖 | `POST /vector-search`，`query.text` |
| 描述搜索 | 覆盖 | `POST /vector-search`，`query.text` |
| 资源删除 / 下架 | 覆盖 | `asset_vector_batch_delete` |
| 漏建修复 | 覆盖 | `POST /vector-assets:exists` + `asset_vector_batch_upsert` |
| 幽灵向量清理 | 覆盖 | `GET /vector-assets/ids` + `asset_vector_batch_delete` |
| 模型切换重建 | 覆盖计划 | 新模型版本 + `asset_vector_batch_upsert` 全量重建 |
| 前端交互和收藏 | 不覆盖 | 前端 / 后端资源服务 |
| AI 描述生成和打标 | 不覆盖 | 向量索引链路不直接执行；后端确认后的描述和标签可作为 `text_payload` 输入 |

因此，本方案能覆盖“后端资源进入向量库、被搜索、被更新、被删除、被对账修复、被全量重建”的服务侧闭环。它不覆盖资源管理产品本身，也不覆盖前端交互、AI 描述生成和 AI 打标。

## 关键流程细节

本节补充模拟对接过程中的字段级细节，重点说明单资源场景如何落到 batch-first 合同。

### 新资源入库

```text
1. 后端写业务资源库，生成 resource_id 和 source_revision。
2. 如果是组资源，后端同时提供 group_id。
3. 后端准备 OSS public_url。
4. 后端按业务规则决定是否提供 text_payload。
5. 后端提交 asset_vector_batch_upsert Job，items 中放当前资源。
6. 本服务创建 Job、attempt、dispatch outbox。
7. worker 领取 attempt。
8. worker 校验输入、读取 OSS public_url、调用 DashScope embedding。
9. repository 用 source_revision 防旧覆盖后写入 asset_vector_entries。
10. Job succeeded，按需 callback 后端。
11. 后端标记资源向量索引完成。
```

### 资源更新

```text
1. 后端更新资源图片或后端定义的检索文本。
2. 后端递增 source_revision。
3. 后端提交新的 asset_vector_batch_upsert Job，items 中放当前资源。
4. 如果旧 Job 晚完成，repository 根据 source_revision 跳过旧写入。
5. 新 Job 成功后搜索结果自然使用新向量。
```

图片未变但检索文本变化时，后端重新提交当前资源，至少包含新的 `text_payload`。如果希望新主向量继续融合图片和文本，也应同时提供 `image.public_url`：

```json
{
  "resource_id": "res_001",
  "source_revision": 13,
  "image": {
    "public_url": "https://assets.example.com/preview/res_001.png"
  },
  "text_payload": "新的最终检索文本"
}
```

图片变化但没有文本检索需求时，后端可以只提交图片输入：

```json
{
  "resource_id": "res_001",
  "source_revision": 14,
  "image": {
    "public_url": "https://assets.example.com/preview/res_001.png"
  }
}
```

### 用户以文字搜图

```text
1. 用户输入文本。
2. 后端按权限、项目、分类、标签、状态、收藏等条件筛出 candidate_resource_ids。
3. 后端调用 vector-search，query.text。
4. 本服务同步生成文本 query embedding。
5. pgvector 在 candidate_resource_ids 内按 asset 主向量排序。
6. 本服务返回 resource_id + group_id + score。
7. 后端查业务库拼素材详情并展示。
```

### 用户以图片搜图

```text
1. 用户上传图片或选择已有资源。
2. 如果是上传图片，后端先生成可控 public_url，或使用受限 base64 方案。
3. 后端筛出 candidate_resource_ids。
4. 后端调用 vector-search，query.image.public_url 或 query.resource_id。
5. 本服务生成或读取 query embedding。
6. pgvector 排序后返回 resource_id + group_id + score。
7. 后端拼详情展示。
```

### 资源删除

```text
1. 后端删除、永久下架或批量清理资源。
2. 后端提交 asset_vector_batch_delete Job。
3. 单资源删除时，resource_ids 中只放当前资源。
4. 本服务删除对应 vector entries。
5. 后端定期用 ids 反向对账，修复漏删。
```

### 全量重建

```text
1. 后端导出当前有效资源 manifest 到 OSS。
2. 后端提交 asset_vector_batch_upsert Job。
3. worker 分批读取 manifest item。
4. 每批调用 embedding provider 并写入 pgvector。
5. 部分失败写入 result_ref。
6. 后端读取结果文件，补跑失败项。
7. 后端通过 exists / ids 做对账。
```

## 与新 CC 库 4.6 的覆盖关系

| 4.6 需求 | 本方案覆盖情况 | 说明 |
|---|---|---|
| 普通 ID 搜索 | 不覆盖 | 后端资源库直接查 ID；不需要 embedding-service |
| 普通名称搜索 | 不覆盖 | 后端资源库直接查名称或模糊搜索 |
| 搜索历史 | 不覆盖 | 前端或业务后端用户态能力 |
| 搜索输入清空行为 | 不覆盖 | 前端交互状态 |
| 图片搜索 | 覆盖服务侧能力 | `query.image.public_url` 或 `query.resource_id` |
| 音频管理不支持图片搜索 | 覆盖边界 | 后端不把音频资源放入图搜图候选集 |
| 当前分类和标签筛选后再图搜图 | 覆盖接缝 | 后端先筛出 `candidate_resource_ids`，本服务只排序 |
| 只展示相似度大于 90 | 部分覆盖 | 本服务提供 `score` 和 `min_score`，但阈值必须评测后确定，不能直接当百分比 |
| 拖拽图片触发图搜图 | 覆盖后端服务接口，不覆盖 UI | 前端拖拽后由后端生成 public_url 再调用搜索 |
| 描述搜索核心流程 | 覆盖 | `text_payload` 可参与生成资源 `asset` 主向量，用户查询生成 query vector |
| 全部资源组默认生成 AI 描述 | 不属于向量索引链路 | 向量索引只消费后端确认后的 `text_payload` |
| AI 描述中英双版本 | 不属于向量索引链路 | 后端确认后可把中英文本拼入 `text_payload` |
| 身体不需要生成描述 | 不属于向量索引链路 | 后端决定是否提交 `text_payload`；向量索引链路只生成 `asset` 主向量 |
| 发型-脸一个组一个描述 | 部分覆盖 | 本方案支持 `group_id`，推荐组描述复制到组内资源；是否直接返回组需产品确认 |
| 音频文件生成音频描述 | 覆盖检索承载，不覆盖描述生成 | 后端提供音频 `text_payload` 后，本服务可写 text-only 的 `asset` 主向量 |
| 资源 AI 描述 + 资源标签参与匹配 | 覆盖承载，不覆盖拼接规则 | 后端拼好最终 `text_payload`，本服务不假设标签或描述必有 |
| 脸/头发/物件/背景/BGM/音效描述模板 | 不覆盖模板生成 | 不属于向量检索执行链路 |
| 收藏筛选和收藏计数 | 不覆盖 | 后端资源服务维护，搜索时可通过候选集体现 |

结论：

```text
本文覆盖 4.6 的向量检索服务侧能力。
本文作为向量索引方案，不覆盖 4.6 的前端交互、资源管理、收藏、普通搜索、描述生成模板和 AI 打标。
```

## 错误与失败模式

| 失败模式 | 处理方式 |
|---|---|
| DashScope 429 / 5xx / timeout | Job 路径按 retry policy 重试；搜索路径返回明确错误 |
| OSS public_url 不可读 | 返回输入读取错误；不尝试猜测其他地址 |
| 图片超过限制 | 返回 `INPUT_TOO_LARGE` 或 capability 专用错误，后端换预览图 |
| 图片 hash 不一致 | 返回 `INPUT_HASH_MISMATCH`，不继续调用模型 |
| `text_payload` 超过模型限制 | 返回输入过长错误，由后端缩短 |
| 模型输出维度不等于 768 | 返回 `MODEL_OUTPUT_INVALID`，不写入向量表 |
| 同一资源并发 upsert | 通过 `source_revision` 防旧覆盖 |
| 批量部分失败 | Job result 或 result_ref 记录失败项，不吞错 |
| 后端删除资源但向量未删 | `vector-assets/ids` 反向对账后删除 |
| 本服务缺少向量 | `vector-assets:exists` 正向对账后补建 |
| 模型切换或维度变化 | 新建模型版本或新表后重建，不原地改 `vector(768)` |
| 相似度阈值不准 | 用真实评测集确定，不硬编码默认真理 |
| 同步搜索被大候选集压垮 | 限制 `candidate_resource_ids` 数量、`top_k` 和请求频率 |

## 不建议的设计

不建议把所有动作塞进一个泛化 Job：

```json
{
  "job_type": "asset_vector",
  "job_params": {
    "operation": "search"
  }
}
```

原因：

- 搜索是同步交互，不应变成异步 Job。
- `upsert`、`batch_upsert`、`delete`、`search` 的 schema 和错误语义不同。
- 一个大 Job 会削弱 registry、测试、文档和 OpenAPI 的可发现性。

不建议让本服务做业务过滤：

```text
本服务不应该理解用户权限、项目权限、标签筛选、素材上下架、收藏和业务详情。
```

这些都是业务后端事实源。本服务只对 `candidate_resource_ids` 做向量排序。

不建议当前直接做全自动“打标 + 索引 + 搜索”闭环。图片打标是独立 Job 能力；描述和标签进入搜索文本前应由后端确认。

不建议本服务主动轮询后端资源库。它会引入跨服务分页、鉴权、增量游标和删除发现问题，且与本项目“AI Job 服务模板”的边界不一致。当前使用后端主动同步 + 对账接口更清晰。

batch-first 不等于把所有动作做成一个 `operation` 大接口。推荐做法是保留少量语义清晰的 Job type 和 route，但每个写删接口天然接收数组：

```text
asset_vector_batch_upsert  -> items 长度可以是 1
asset_vector_batch_delete  -> resource_ids 长度可以是 1
vector-assets:exists       -> resource_ids 长度可以是 1
```

## 实施工作包

以下工作包共同构成一次完整对接的落地范围，不表达分期延期。实际排期可以并行或串行，但对外联调前应整体达到验收标准。

### 工作包 1: 数据库和 repository

- 新增 pgvector extension migration。
- 新增 `asset_vector_entries` 表。
- 新增 partial HNSW index 和基础 B-tree index。
- 实现 repository：upsert、delete、get、exists、list ids、search。
- 实现 `source_revision` 防旧覆盖。
- 增加 repository 单元测试或集成测试。

### 工作包 2: DashScope embedding gateway

- 新增多模态 embedding request/result 类型。
- 新增 `dashscope_multimodal_embeddings_adapter.py`。
- 新增 `generate_multimodal_embeddings_with_ledger()`。
- 在 model catalog 注册 `tongyi-embedding-vision-flash-2026-03-06`。
- 在 pricing registry 注册计费规则或明确不可计费状态。
- 覆盖 timeout、provider failure、usage missing、dimension mismatch 测试。

### 工作包 3: 批量资源同步 Job

- 新增 `asset_vector_batch_upsert` Job executor，支持 `items` 单元素和多元素。
- 复用 Job kernel 的 idempotency、attempt、dispatch outbox、retry、callback 和 billing。
- 校验 `resource_id`、`group_id`、`source_revision`，以及 `image.public_url` / `text_payload` 至少一项输入。
- 写入 item 级 `indexed`、`skipped`、`failed` 结果。
- 增加 schema、executor、registry 和 Job lifecycle 测试。

### 工作包 4: 同步搜索和对账接口

- 新增 `app/features/asset_vector/routes.py`。
- 注册 `OperationSpec`。
- 实现 `POST /vector-search`，优先支持 `query.text` 和 `query.resource_id`。
- 实现 `POST /vector-assets:exists`。
- 实现 `GET /vector-assets/ids`。
- 增加 route contract tests。

### 工作包 5: 批量 manifest 和批量删除

- 扩展 `asset_vector_batch_upsert` 支持 manifest ref。
- 支持大批量部分成功和 result_ref。
- 新增 `asset_vector_batch_delete`。
- 根据真实批量规模决定是否升级为 root/child workflow。

### 工作包 6: 4.6 真实素材评测

- 准备真实素材评测集。
- 验证 `text -> image`、`image -> image`、`resource_id -> image` 三条路径。
- 验证 `asset` 主向量是否能同时承载文搜图、图搜图和描述搜索。
- 统计分数分布，确定 `min_score` 建议值。
- 验证“组描述复制到组内资源”的搜索展示效果。
- 评估是否需要在未来引入多路向量；没有真实评测证据前不把它纳入当前一次对接合同。
- 如果 Top 50-100 初排质量不稳定，再评估 `qwen3-vl-rerank`。

## 验收标准

实现完成后，至少需要证明：

- `./scripts/deploy.sh check` 能通过，compose 使用 pgvector 镜像。
- Alembic 能创建 `vector` extension、`asset_vector_entries` 表和索引。
- `asset_vector_batch_upsert` 能用单元素 `items` 成功写入 `asset` 主检索向量。
- 重复提交同一 `client_request_id` 不创建不一致索引。
- 旧 `source_revision` Job 晚完成时不会覆盖新向量。
- `asset_vector_batch_upsert` 能通过 manifest 批量同步资源，并输出部分失败明细。
- `asset_vector_batch_delete` 能幂等删除资源向量。
- `POST /vector-search` 能支持 `text -> image`、`image -> image` 和 `resource_id -> image`。
- 搜索结果只返回 `resource_id + group_id + score` 等必要元信息，不返回业务详情、权限信息、完整向量或 provider raw payload。
- `vector-assets:exists` 和 `vector-assets/ids` 能支撑正反向对账。
- DashScope 429 / timeout / 5xx 在 Job 路径和同步搜索路径都有明确错误语义。
- 模型维度、usage/cost、route_config_hash 有测试覆盖。
- 正式 API 合同已拆到 `docs/api/asset-vector-api.md`。
- 当前实现事实已沉淀到 `docs/current/asset-vector.md`。
- 本计划文档标记完成或移入归档。

## 仍需确认

- `source_revision` 的后端生成规则，是单调递增整数、更新时间毫秒，还是业务版本号。
- `candidate_resource_ids` 的最大数量上限。
- 描述搜索结果是否必须按 `group_id` 聚合展示。
- BGM / 音效是否进入本服务的 text-only `asset` 主向量检索范围。
- provider 用量无法返回时，billing 采用不可计费状态还是按请求/按 item 定价。
- 全库检索是否只给内部工具开放。
- 批量导入的规模上限，以及是否必须支持 root/child workflow 级别的部分失败恢复。
